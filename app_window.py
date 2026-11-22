"""Main window: scan settings on the left, results table and chart on the right."""
from __future__ import annotations

import csv
import json
import time
import webbrowser
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (QAbstractTableModel, QByteArray, QModelIndex, QObject, QPointF,
                            QSortFilterProxyModel, QStandardPaths, Qt, QThread, QTimer, Signal)
from PySide6.QtGui import (QAction, QBrush, QColor, QFont, QGuiApplication, QIcon, QPainter,
                           QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMenu,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
                               QSizePolicy, QSpinBox, QSplitter, QStyleFactory, QSystemTrayIcon,
                               QTableView, QToolBar, QVBoxLayout, QWidget)

import market_data
import universe
from chart_widget import ChartWidget
from scanner import MA_DESCRIPTIONS, MA_TYPES, PRICE_BASIS, SIDES, Hit, MASpec, scan_universe
from settings_store import AppSettings, load_settings, save_settings
from themes import THEMES, Theme, build_palette, build_stylesheet, get_theme

APP_NAME = "MA Touch Scanner"


def app_data_dir() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    path = Path(base or Path.home() / ".ma_touch_scanner")
    path.mkdir(parents=True, exist_ok=True)
    return path


def us_market_open(now: datetime | None = None) -> bool:
    """Regular US session (9:30-16:15 New York time, Mon-Fri; holidays not checked)."""
    try:
        from zoneinfo import ZoneInfo
        now = now or datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return True
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 9 * 60 + 30 <= minutes <= 16 * 60 + 15


def make_app_icon(accent: str = "#4f8cff") -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#151820"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(2, 2, 60, 60, 14, 14)
    p.setPen(QPen(QColor("#3fb950"), 3))
    for x, lo, hi in ((16, 30, 48), (28, 22, 40), (40, 26, 44), (50, 14, 32)):
        p.drawLine(x, lo, x, hi)
    path = QPainterPath(QPointF(10, 44))
    path.cubicTo(QPointF(24, 40), QPointF(36, 34), QPointF(56, 22))
    p.setPen(QPen(QColor(accent), 4, Qt.SolidLine, Qt.RoundCap))
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)
    p.end()
    return QIcon(pm)


# ---------------------------------------------------------------------------
# Background scan
# ---------------------------------------------------------------------------

class ScanWorker(QObject):
    progress = Signal(int, str)
    finished = Signal(object)
    failed = Signal(str)
    ended = Signal()          # always emitted last, even after Stop

    def __init__(self, settings: AppSettings, cache_dir: Path, demo: bool, refresh_lists: bool = False):
        super().__init__()
        self.s = settings
        self.cache = universe.UniverseCache(cache_dir)
        self.demo = demo
        self.refresh_lists = refresh_lists
        self._stop = False

    def stop(self):
        self._stop = True

    def cancelled(self) -> bool:
        return self._stop

    def run(self):
        try:
            self._run()
        except Exception as exc:  # report instead of crashing the UI
            self.failed.emit(str(exc))
        finally:
            self.ended.emit()

    def _run(self):
        t0 = time.time()
        cfg = self.s.scan
        if not cfg.enabled_mas():
            self.failed.emit("Turn on at least one moving average.")
            return

        # 1. Build the list of stocks to scan.
        members: dict[str, tuple[universe.Member, list[str]]] = {}
        notes: list[str] = []

        def add(mlist, label):
            for m in mlist:
                if m.ticker in members:
                    members[m.ticker][1].append(label)
                else:
                    members[m.ticker] = (m, [label])

        if self.demo:
            add([universe.Member(t, f"{t} (demo data)", "Demo") for t in market_data.DEMO_TICKERS], "Demo")
        else:
            for k, idx in enumerate(self.s.indexes):
                if self._stop:
                    return
                self.progress.emit(2 + k, f"Loading {idx} members…")
                try:
                    mlist, note = universe.load_index(idx, self.cache, self.refresh_lists)
                    add(mlist, idx)
                    notes.append(f"{idx}: {len(mlist)} stocks ({note})")
                except Exception as exc:
                    notes.append(str(exc))
            if self.s.use_custom:
                add(universe.parse_custom_tickers(self.s.custom_tickers), "Custom")
        if not members:
            self.failed.emit("No stocks to scan. " + " ".join(notes))
            return

        tickers = sorted(members)

        # 2. Download daily prices.
        if self.demo:
            data, failed = market_data.demo_history(tickers), []
        else:
            period = market_data.history_period(cfg.max_period())
            self.progress.emit(10, f"Downloading {len(tickers)} stocks…")
            data, failed = market_data.download_history(
                tickers, period,
                progress=lambda d, n: self.progress.emit(10 + int(80 * d / max(n, 1)),
                                                         f"Downloading prices {d}/{n}…"),
                cancelled=self.cancelled,
            )
        if self._stop:
            return
        if not data:
            self.failed.emit("No price data came back. Check your internet connection and try again.")
            return

        # 3. Scan.
        self.progress.emit(92, "Scanning…")
        hits = scan_universe(data, cfg, cancelled=self.cancelled)
        for h in hits:
            m, idx = members.get(h.ticker, (universe.Member(h.ticker), []))
            h.name, h.sector, h.indexes = m.name, m.sector, ", ".join(idx)
        self.finished.emit({
            "hits": hits, "data": data, "notes": notes, "failed": failed,
            "scanned": len(data), "elapsed": time.time() - t0,
        })


# ---------------------------------------------------------------------------
# Results table
# ---------------------------------------------------------------------------

COLUMNS = [
    ("Ticker", "ticker"), ("Company", "name"), ("MA", "ma_label"), ("Status", "status"),
    ("When", "days_ago"), ("Last", "last_close"), ("Chg %", "change_pct"),
    ("MA value", "ma_value"), ("vs MA %", "distance_pct"), ("Closest %", "closest_pct"),
    ("Side", "side"), ("Bars hit", "hits_in_window"), ("Avg volume", "avg_volume"),
    ("Sector", "sector"), ("Index", "indexes"),
]
TOOLTIPS = {
    "When": "Most recent bar in the look-back window that touched or came close (Today = latest bar)",
    "vs MA %": "Where the last price sits relative to the MA right now",
    "Closest %": "Smallest gap between the price bar and the MA during the look-back window (0 = touched)",
    "Bars hit": "How many bars in the look-back window touched or came close",
    "Side": "Price above the MA (testing support) or below it (testing resistance)",
}


def _fmt_volume(v: float) -> str:
    if v != v:
        return "–"
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if v >= div:
            return f"{v / div:.1f}{unit}"
    return f"{v:.0f}"


class ResultsModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.hits: list[Hit] = []
        self.new_keys: set = set()
        self.theme: Theme = get_theme("Dark")

    def set_hits(self, hits: list[Hit], new_keys: set | None = None):
        self.beginResetModel()
        self.hits = hits
        self.new_keys = new_keys or set()
        self.endResetModel()

    def set_theme(self, theme: Theme):
        self.theme = theme
        if self.hits:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.hits) - 1, len(COLUMNS) - 1))

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.hits)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal:
            if role == Qt.DisplayRole:
                return COLUMNS[section][0]
            if role == Qt.ToolTipRole:
                return TOOLTIPS.get(COLUMNS[section][0])
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        h = self.hits[index.row()]
        title, attr = COLUMNS[index.column()]
        v = getattr(h, attr)
        t = self.theme
        if role == Qt.DisplayRole:
            if attr == "ticker":
                return f"★ {v}" if h.key in self.new_keys else v
            if attr == "days_ago":
                return "Today" if v == 0 else f"{v}d ago"
            if attr in ("last_close", "ma_value"):
                return f"{v:,.2f}"
            if attr in ("change_pct", "distance_pct"):
                return f"{v:+.2f}%"
            if attr == "closest_pct":
                return f"{v:.2f}%"
            if attr == "avg_volume":
                return _fmt_volume(v)
            return str(v)
        if role == Qt.UserRole:  # sort key
            return v if isinstance(v, (int, float)) else str(v).lower()
        if role == Qt.ForegroundRole:
            if attr == "status":
                return QBrush(QColor(t.touched if v in ("Touched", "Crossed") else t.near))
            if attr in ("change_pct", "distance_pct"):
                return QBrush(QColor(t.up if v >= 0 else t.down))
            if attr == "side":
                return QBrush(QColor(t.up if v == "Above" else t.down))
        if role == Qt.FontRole and attr in ("ticker", "status"):
            f = QFont()
            f.setBold(True)
            return f
        if role == Qt.TextAlignmentRole and isinstance(v, (int, float)):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ToolTipRole:
            if attr == "days_ago":
                return f"{h.status} on {h.bar_date:%a %b %d, %Y}"
            if attr == "ticker" and h.key in self.new_keys:
                return "New since the previous scan"
        return None


class ResultsFilter(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.text = ""
        self.ma = ""
        self.status = ""
        self.setSortRole(Qt.UserRole)

    def set_filters(self, text: str, ma: str, status: str):
        self.text, self.ma, self.status = text.strip().lower(), ma, status
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        h = self.sourceModel().hits[row]
        if self.ma and h.ma_label != self.ma:
            return False
        if self.status == "Touched / crossed" and h.status not in ("Touched", "Crossed"):
            return False
        if self.status == "Near only" and h.status != "Near":
            return False
        if self.text:
            hay = f"{h.ticker} {h.name} {h.sector} {h.indexes}".lower()
            return self.text in hay
        return True


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, demo: bool = False):
        super().__init__()
        self.demo = demo
        self.data_dir = app_data_dir()
        self.settings_path = self.data_dir / "settings.json"
        self.settings = load_settings(self.settings_path)
        self.data: dict = {}
        self.prev_keys: set | None = None
        self.thread: QThread | None = None
        self.worker: ScanWorker | None = None
        self.last_scan_auto = False
        self._scan_cfg = self.settings.scan

        self.setWindowTitle(APP_NAME + ("  —  DEMO DATA" if demo else ""))
        self.setWindowIcon(make_app_icon())
        self.resize(1440, 900)

        self._build_toolbar()
        self._build_body()
        self.status = self.statusBar()
        self.status.showMessage("Ready. Choose your indexes and moving averages, then press Scan.")

        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(make_app_icon(), self)
            self.tray.setToolTip(APP_NAME)
            menu = QMenu()
            menu.addAction("Show window", self._bring_to_front)
            menu.addAction("Scan now", self.start_scan)
            menu.addSeparator()
            menu.addAction("Quit", QApplication.instance().quit)
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(lambda reason: self._bring_to_front()
                                        if reason == QSystemTrayIcon.Trigger else None)
            self.tray.messageClicked.connect(self._bring_to_front)
            self.tray.show()

        self.auto_timer = QTimer(self)
        self.auto_timer.timeout.connect(self._auto_scan_tick)

        self._load_into_widgets(self.settings)
        self.apply_theme()
        self._restore_geometry()
        self._update_auto_timer()

    # -- layout -------------------------------------------------------------
    def _build_toolbar(self):
        tb = QToolBar("Main")
        tb.setMovable(False)
        self.addToolBar(tb)

        self.scan_btn = QPushButton("▶  Scan")
        self.scan_btn.setObjectName("primary")
        self.scan_btn.setShortcut("Ctrl+R")
        self.scan_btn.setToolTip("Scan now (Ctrl+R)")
        self.scan_btn.clicked.connect(self.start_scan)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_scan)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(260)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("Idle")
        self.export_btn = QPushButton("Export CSV")
        self.export_btn.clicked.connect(self.export_csv)
        self.export_btn.setEnabled(False)

        for w in (self.scan_btn, self.stop_btn, self.progress, self.export_btn):
            tb.addWidget(w)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)

        tb.addWidget(QLabel("Theme "))
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(list(THEMES))
        self.theme_combo.currentTextChanged.connect(lambda _: self.apply_theme())
        tb.addWidget(self.theme_combo)
        tb.addWidget(QLabel("  Text size "))
        self.font_spin = QSpinBox()
        self.font_spin.setRange(8, 18)
        self.font_spin.setSuffix(" pt")
        self.font_spin.valueChanged.connect(lambda _: self.apply_theme())
        tb.addWidget(self.font_spin)

    def _build_body(self):
        # ---- left: settings ----
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(8, 4, 8, 8)

        g_idx = QGroupBox("Indexes to scan")
        vi = QVBoxLayout(g_idx)
        self.index_checks: dict[str, QCheckBox] = {}
        for name in universe.INDEX_NAMES:
            cb = QCheckBox(name.replace("&", "&&"))   # "&&" shows a literal "&"
            self.index_checks[name] = cb
            vi.addWidget(cb)
        self.custom_check = QCheckBox("My watchlist")
        vi.addWidget(self.custom_check)
        self.custom_edit = QPlainTextEdit()
        self.custom_edit.setPlaceholderText("Tickers separated by spaces or commas, e.g. AAPL MSFT SPY QQQ")
        self.custom_edit.setFixedHeight(64)
        vi.addWidget(self.custom_edit)
        self.refresh_btn = QPushButton("Refresh index lists")
        self.refresh_btn.setToolTip("Index member lists are cached for a week. Click to re-download them on the next scan.")
        self.refresh_btn.setCheckable(True)
        vi.addWidget(self.refresh_btn)
        if self.demo:
            g_idx.setEnabled(False)
            g_idx.setTitle("Indexes to scan (demo mode uses sample data)")
        lv.addWidget(g_idx)

        g_ma = QGroupBox("Moving averages")
        gm = QGridLayout(g_ma)
        gm.addWidget(self._muted("On"), 0, 0)
        gm.addWidget(self._muted("Length"), 0, 1)
        gm.addWidget(self._muted("Type"), 0, 2)
        self.ma_rows: list[tuple[QCheckBox, QSpinBox, QComboBox]] = []
        for r in range(5):
            cb = QCheckBox()
            sp = QSpinBox()
            sp.setRange(2, 500)
            combo = self._ma_type_combo()
            gm.addWidget(cb, r + 1, 0)
            gm.addWidget(sp, r + 1, 1)
            gm.addWidget(combo, r + 1, 2)
            self.ma_rows.append((cb, sp, combo))
        gm.addWidget(self._muted("Set all to"), 6, 0, 1, 2)
        self.all_types = QComboBox()
        self.all_types.addItem("—")
        self.all_types.addItems(MA_TYPES)
        self.all_types.currentTextChanged.connect(self._set_all_types)
        gm.addWidget(self.all_types, 6, 2)
        lv.addWidget(g_ma)

        g_rule = QGroupBox("Touch rules")
        fr = QFormLayout(g_rule)
        self.lookback = QSpinBox()
        self.lookback.setRange(1, 10)
        self.lookback.setSuffix(" trading days")
        self.lookback.setToolTip("Look at the most recent N daily bars (1 = today only)")
        fr.addRow("Look-back", self.lookback)
        self.tolerance = QDoubleSpinBox()
        self.tolerance.setRange(0.0, 10.0)
        self.tolerance.setSingleStep(0.1)
        self.tolerance.setDecimals(2)
        self.tolerance.setSuffix(" %")
        self.tolerance.setToolTip("A bar within this % of the MA counts as 'near'. Bars that cross the MA always count as touched.")
        fr.addRow("Close within", self.tolerance)
        self.basis = QComboBox()
        for k, v in PRICE_BASIS.items():
            self.basis.addItem(v, k)
        fr.addRow("Compare using", self.basis)
        self.side = QComboBox()
        for k, v in SIDES.items():
            self.side.addItem(v, k)
        fr.addRow("Price now", self.side)
        self.min_price = QDoubleSpinBox()
        self.min_price.setRange(0, 10000)
        self.min_price.setPrefix("$ ")
        fr.addRow("Minimum price", self.min_price)
        self.min_vol = QSpinBox()
        self.min_vol.setRange(0, 100_000_000)
        self.min_vol.setSingleStep(100_000)
        self.min_vol.setGroupSeparatorShown(True)
        self.min_vol.setToolTip("50-day average daily volume")
        fr.addRow("Min avg volume", self.min_vol)
        lv.addWidget(g_rule)

        g_alert = QGroupBox("Alerts")
        fa = QFormLayout(g_alert)
        self.auto_combo = QComboBox()
        for label, mins in (("Off", 0), ("Every 15 minutes", 15), ("Every 30 minutes", 30),
                            ("Every hour", 60), ("Every 2 hours", 120), ("Every 4 hours", 240)):
            self.auto_combo.addItem(label, mins)
        self.auto_combo.currentIndexChanged.connect(lambda _: self._update_auto_timer())
        fa.addRow("Auto-scan", self.auto_combo)
        self.hours_check = QCheckBox("Only while the US market is open")
        fa.addRow(self.hours_check)
        self.notify_check = QCheckBox("Desktop notification when stocks are found")
        fa.addRow(self.notify_check)
        self.next_scan_label = self._muted("")
        fa.addRow(self.next_scan_label)
        lv.addWidget(g_alert)
        lv.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(left)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(400)
        scroll.setMaximumWidth(520)
        for combo in left.findChildren(QComboBox):
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(8)

        # ---- right: results + chart ----
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(4, 8, 8, 4)
        fbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by ticker, company, sector…")
        self.search.setClearButtonEnabled(True)
        self.ma_filter = QComboBox()
        self.status_filter = QComboBox()
        self.status_filter.addItems(["All", "Touched / crossed", "Near only"])
        self.summary = self._muted("")
        fbar.addWidget(self.search, 2)
        fbar.addWidget(QLabel("MA"))
        fbar.addWidget(self.ma_filter)
        fbar.addWidget(QLabel("Status"))
        fbar.addWidget(self.status_filter)
        fbar.addWidget(self.summary, 1, Qt.AlignRight)
        rv.addLayout(fbar)

        self.model = ResultsModel()
        self.proxy = ResultsFilter()
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(4, Qt.AscendingOrder)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        self.table.doubleClicked.connect(lambda _: self._open_link("tradingview"))
        self.table.selectionModel().currentRowChanged.connect(self._row_selected)
        for w in (self.search,):
            w.textChanged.connect(self._apply_filters)
        self.ma_filter.currentTextChanged.connect(self._apply_filters)
        self.status_filter.currentTextChanged.connect(self._apply_filters)

        self.chart = ChartWidget()
        self.right_split = QSplitter(Qt.Vertical)
        self.right_split.addWidget(self.table)
        self.right_split.addWidget(self.chart)
        self.right_split.setSizes([480, 360])
        rv.addWidget(self.right_split, 1)

        self.main_split = QSplitter(Qt.Horizontal)
        self.main_split.addWidget(scroll)
        self.main_split.addWidget(right)
        self.main_split.setStretchFactor(1, 1)
        self.main_split.setSizes([420, 1020])
        self.setCentralWidget(self.main_split)

    def _muted(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("muted")
        return lbl

    def _ma_type_combo(self) -> QComboBox:
        c = QComboBox()
        for i, t in enumerate(MA_TYPES):
            c.addItem(t)
            c.setItemData(i, MA_DESCRIPTIONS[t], Qt.ToolTipRole)
        return c

    def _set_all_types(self, t: str):
        if t in MA_TYPES:
            for _, _, combo in self.ma_rows:
                combo.setCurrentText(t)
            self.all_types.blockSignals(True)
            self.all_types.setCurrentIndex(0)
            self.all_types.blockSignals(False)

    # -- settings <-> widgets ------------------------------------------------
    def _load_into_widgets(self, s: AppSettings):
        for name, cb in self.index_checks.items():
            cb.setChecked(name in s.indexes)
        self.custom_check.setChecked(s.use_custom)
        self.custom_edit.setPlainText(s.custom_tickers)
        for (cb, sp, combo), spec in zip(self.ma_rows, s.scan.mas):
            cb.setChecked(spec.enabled)
            sp.setValue(spec.period)
            combo.setCurrentText(spec.ma_type)
        self.lookback.setValue(s.scan.lookback_days)
        self.tolerance.setValue(s.scan.tolerance_pct)
        self.basis.setCurrentIndex(max(0, self.basis.findData(s.scan.price_basis)))
        self.side.setCurrentIndex(max(0, self.side.findData(s.scan.side)))
        self.min_price.setValue(s.scan.min_price)
        self.min_vol.setValue(int(s.scan.min_avg_volume))
        self.auto_combo.setCurrentIndex(max(0, self.auto_combo.findData(s.auto_scan_minutes)))
        self.hours_check.setChecked(s.market_hours_only)
        self.notify_check.setChecked(s.notify)
        self.theme_combo.setCurrentText(s.theme if s.theme in THEMES else "Dark")
        self.font_spin.setValue(s.font_pt)

    def collect_settings(self) -> AppSettings:
        s = self.settings
        s.indexes = [n for n, cb in self.index_checks.items() if cb.isChecked()]
        s.use_custom = self.custom_check.isChecked()
        s.custom_tickers = self.custom_edit.toPlainText()
        s.scan.mas = [MASpec(sp.value(), combo.currentText(), cb.isChecked()) for cb, sp, combo in self.ma_rows]
        s.scan.lookback_days = self.lookback.value()
        s.scan.tolerance_pct = self.tolerance.value()
        s.scan.price_basis = self.basis.currentData()
        s.scan.side = self.side.currentData()
        s.scan.min_price = self.min_price.value()
        s.scan.min_avg_volume = self.min_vol.value()
        s.auto_scan_minutes = self.auto_combo.currentData()
        s.market_hours_only = self.hours_check.isChecked()
        s.notify = self.notify_check.isChecked()
        s.theme = self.theme_combo.currentText()
        s.font_pt = self.font_spin.value()
        return s

    def _restore_geometry(self):
        try:
            if self.settings.geometry:
                self.restoreGeometry(QByteArray.fromHex(self.settings.geometry.encode()))
            if self.settings.splitter:
                self.right_split.restoreState(QByteArray.fromHex(self.settings.splitter.encode()))
        except Exception:
            pass

    def save(self):
        s = self.collect_settings()
        s.geometry = bytes(self.saveGeometry().toHex()).decode()
        s.splitter = bytes(self.right_split.saveState().toHex()).decode()
        try:
            save_settings(self.settings_path, s)
        except Exception:
            pass

    # -- theme --------------------------------------------------------------
    def apply_theme(self):
        theme = get_theme(self.theme_combo.currentText())
        app = QApplication.instance()
        app.setStyle(QStyleFactory.create("Fusion"))
        app.setPalette(build_palette(theme))
        app.setStyleSheet(build_stylesheet(theme, self.font_spin.value()))
        f = app.font()
        f.setPointSize(self.font_spin.value())
        app.setFont(f)
        self.model.set_theme(theme)
        self.chart.apply_theme(theme)
        self.table.verticalHeader().setDefaultSectionSize(int(self.font_spin.value() * 2.4))

    # -- scanning -----------------------------------------------------------
    def start_scan(self, auto: bool = False):
        if self.thread is not None:
            return
        s = self.collect_settings()
        if not self.demo and not s.indexes and not (s.use_custom and s.custom_tickers.strip()):
            QMessageBox.information(self, APP_NAME, "Pick at least one index (or turn on your watchlist).")
            return
        self.save()
        self.last_scan_auto = bool(auto)
        self.scan_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress.setValue(1)
        self.progress.setFormat("Starting…")

        # Work on a snapshot so changing settings mid-scan doesn't matter.
        snapshot = AppSettings.from_dict(json.loads(s.to_json()))
        self.thread = QThread(self)
        self.worker = ScanWorker(snapshot, self.data_dir, self.demo, self.refresh_btn.isChecked())
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.failed.connect(self._on_failed)
        self.worker.ended.connect(self._cleanup_thread)
        self.thread.start()
        self._scan_cfg = snapshot.scan

    def stop_scan(self):
        if self.worker:
            self.worker.stop()
            self.stop_btn.setEnabled(False)
            self.progress.setFormat("Stopping…")

    def _cleanup_thread(self, *_):
        if self.thread is None:
            return
        self.thread.quit()
        self.thread.wait(5000)
        self.thread = None
        self.worker = None
        self.scan_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        if self.progress.value() < 100:
            self.progress.setValue(0)
            self.progress.setFormat("Idle")

    def _on_progress(self, pct: int, msg: str):
        self.progress.setValue(pct)
        self.progress.setFormat(msg)

    def _on_failed(self, msg: str):
        self.progress.setValue(0)
        self.progress.setFormat("Idle")
        self.status.showMessage("Scan failed: " + msg)
        if not self.last_scan_auto:
            QMessageBox.warning(self, APP_NAME, msg)

    def _on_finished(self, result: dict):
        self.refresh_btn.setChecked(False)
        hits: list[Hit] = result["hits"]
        self.data = result["data"]
        keys = {h.key for h in hits}
        new_keys = keys - self.prev_keys if self.prev_keys is not None else set()
        self.prev_keys = keys
        self.model.set_hits(hits, new_keys)
        self.export_btn.setEnabled(bool(hits))

        current_ma = self.ma_filter.currentText()
        self.ma_filter.blockSignals(True)
        self.ma_filter.clear()
        self.ma_filter.addItem("All")
        self.ma_filter.addItems([m.label for m in self._scan_cfg.enabled_mas()])
        self.ma_filter.setCurrentText(current_ma if self.ma_filter.findText(current_ma) >= 0 else "All")
        self.ma_filter.blockSignals(False)
        self._apply_filters()
        self.table.resizeColumnsToContents()
        if self.table.columnWidth(1) > 220:
            self.table.setColumnWidth(1, 220)

        stocks = len({h.ticker for h in hits})
        self.progress.setValue(100)
        self.progress.setFormat(f"Done · {stocks} stocks")
        stamp = datetime.now().strftime("%I:%M %p").lstrip("0")
        msg = (f"{stamp} — scanned {result['scanned']} stocks in {result['elapsed']:.0f}s: "
               f"{stocks} stocks / {len(hits)} MA hits.")
        if result["failed"]:
            msg += f"  {len(result['failed'])} tickers had no data."
        if result["notes"]:
            msg += "   " + " · ".join(result["notes"])
        self.status.showMessage(msg)

        if hits and self.proxy.rowCount():
            self.table.selectRow(0)
        else:
            self.chart.clear("No stocks matched. Try a longer look-back or a wider 'close within' %.")
        self._notify(hits, new_keys)
        self._update_auto_timer()

    def _notify(self, hits: list[Hit], new_keys: set):
        if not self.notify_check.isChecked() or not hits:
            return
        if self.last_scan_auto:
            if not new_keys:
                return
            fresh = [h for h in hits if h.key in new_keys]
            title = f"{len({h.ticker for h in fresh})} new stocks near their moving averages"
            body = ", ".join(f"{h.ticker} ({h.ma_label})" for h in fresh[:8])
        else:
            if self.isActiveWindow():
                return
            title = f"Scan finished: {len({h.ticker for h in hits})} stocks near their moving averages"
            body = ", ".join(f"{h.ticker} ({h.ma_label})" for h in hits[:8])
        if self.tray:
            self.tray.showMessage(title, body, make_app_icon(), 10000)
        QApplication.alert(self, 0)

    def _auto_scan_tick(self):
        if self.hours_check.isChecked() and not us_market_open():
            self.next_scan_label.setText("Auto-scan waiting for the market to open")
            return
        self.start_scan(auto=True)

    def _update_auto_timer(self):
        mins = self.auto_combo.currentData() or 0
        if mins:
            self.auto_timer.start(mins * 60 * 1000)
            nxt = datetime.now().timestamp() + mins * 60
            self.next_scan_label.setText("Next auto-scan around " +
                                         datetime.fromtimestamp(nxt).strftime("%I:%M %p").lstrip("0"))
        else:
            self.auto_timer.stop()
            self.next_scan_label.setText("")

    # -- table interactions -------------------------------------------------
    def _apply_filters(self):
        ma = self.ma_filter.currentText()
        self.proxy.set_filters(self.search.text(), "" if ma in ("", "All") else ma,
                               self.status_filter.currentText())
        shown = self.proxy.rowCount()
        total = self.model.rowCount()
        self.summary.setText(f"{shown} of {total} rows" if total else "")

    def _current_hit(self) -> Hit | None:
        idx = self.table.currentIndex()
        if not idx.isValid():
            return None
        return self.model.hits[self.proxy.mapToSource(idx).row()]

    def _row_selected(self, *_):
        h = self._current_hit()
        if h and h.ticker in self.data:
            self.chart.show_symbol(h.ticker, self.data[h.ticker], self._scan_cfg, h.ma_label, h.name)

    def _table_menu(self, pos):
        h = self._current_hit()
        if not h:
            return
        m = QMenu(self)
        m.addAction(f"Open {h.ticker} on TradingView", lambda: self._open_link("tradingview"))
        m.addAction(f"Open {h.ticker} on Yahoo Finance", lambda: self._open_link("yahoo"))
        m.addAction(f"Open {h.ticker} on Finviz", lambda: self._open_link("finviz"))
        m.addSeparator()
        m.addAction("Copy ticker", lambda: QGuiApplication.clipboard().setText(h.ticker))
        m.addAction("Copy all visible tickers", self._copy_visible)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _open_link(self, site: str):
        h = self._current_hit()
        if not h:
            return
        t = h.ticker
        url = {
            "tradingview": f"https://www.tradingview.com/chart/?symbol={t.replace('-', '.')}",
            "yahoo": f"https://finance.yahoo.com/quote/{t}/chart",
            "finviz": f"https://finviz.com/quote.ashx?t={t}",
        }[site]
        webbrowser.open(url)

    def _copy_visible(self):
        seen, out = set(), []
        for r in range(self.proxy.rowCount()):
            h = self.model.hits[self.proxy.mapToSource(self.proxy.index(r, 0)).row()]
            if h.ticker not in seen:
                seen.add(h.ticker)
                out.append(h.ticker)
        QGuiApplication.clipboard().setText(", ".join(out))
        self.status.showMessage(f"Copied {len(out)} tickers to the clipboard.")

    def export_csv(self):
        default = str(Path.home() / f"ma_touches_{datetime.now():%Y%m%d_%H%M}.csv")
        path, _ = QFileDialog.getSaveFileName(self, "Export results", default, "CSV files (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([c[0] for c in COLUMNS] + ["Bar date"])
            for r in range(self.proxy.rowCount()):
                h = self.model.hits[self.proxy.mapToSource(self.proxy.index(r, 0)).row()]
                row = []
                for _, attr in COLUMNS:
                    v = getattr(h, attr)
                    row.append(round(v, 4) if isinstance(v, float) else v)
                w.writerow(row + [h.bar_date.strftime("%Y-%m-%d")])
        self.status.showMessage(f"Saved {self.proxy.rowCount()} rows to {path}")

    # -- window -------------------------------------------------------------
    def _bring_to_front(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        self.save()
        if self.worker:
            self.worker.stop()
        if self.thread:
            self.thread.quit()
            self.thread.wait(15000)
        if self.tray:
            self.tray.hide()
        super().closeEvent(event)
