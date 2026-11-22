"""Candlestick chart with the moving averages and the look-back window highlighted."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPicture
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from scanner import ScanConfig, clamp_lookback, compute_ma
from themes import Theme

BARS_SHOWN = 160


class CandleItem(pg.GraphicsObject):
    def __init__(self, o, h, l, c, up: str, down: str):
        super().__init__()
        self._pic = QPicture()
        p = QPainter(self._pic)
        p.setRenderHint(QPainter.Antialiasing, False)
        w = 0.32
        for i in range(len(c)):
            color = QColor(up if c[i] >= o[i] else down)
            pen = pg.mkPen(color, width=1)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.drawLine(QPointF(i, l[i]), QPointF(i, h[i]))
            p.setBrush(pg.mkBrush(color))
            top, bot = max(o[i], c[i]), min(o[i], c[i])
            p.drawRect(QRectF(i - w, bot, 2 * w, max(top - bot, 1e-9)))
        p.end()
        self._rect = QRectF(self._pic.boundingRect())

    def paint(self, painter, *args):
        painter.drawPicture(0, 0, self._pic)

    def boundingRect(self):
        return self._rect


class DateAxis(pg.AxisItem):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.dates: list[pd.Timestamp] = []

    def tickStrings(self, values, scale, spacing):
        out = []
        for v in values:
            i = int(round(v))
            out.append(self.dates[i].strftime("%b %d") if 0 <= i < len(self.dates) else "")
        return out


class ChartWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.title = QLabel("Select a result to see its chart")
        self.title.setObjectName("chartTitle")
        self.axis = DateAxis(orientation="bottom")
        self.plot = pg.PlotWidget(axisItems={"bottom": self.axis})
        self.plot.setMenuEnabled(False)
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.legend = self.plot.addLegend(offset=(8, 8))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.title)
        lay.addWidget(self.plot, 1)
        self.theme: Theme | None = None
        self._last = None

    def apply_theme(self, theme: Theme):
        self.theme = theme
        self.plot.setBackground(theme.chart_bg)
        for name in ("left", "bottom"):
            ax = self.plot.getAxis(name)
            ax.setPen(pg.mkPen(theme.border))
            ax.setTextPen(pg.mkPen(theme.muted))
        self.legend.setLabelTextColor(theme.text)
        self.legend.setBrush(pg.mkBrush(QColor(theme.panel)))
        if self._last:
            self.show_symbol(*self._last)

    def clear(self, message: str = "Select a result to see its chart"):
        self._last = None
        self.plot.clear()
        self.legend.clear()
        self.title.setText(message)

    def show_symbol(self, ticker: str, df: pd.DataFrame, cfg: ScanConfig, highlight_ma: str = "", name: str = ""):
        if self.theme is None or df is None or df.empty:
            return
        self._last = (ticker, df, cfg, highlight_ma, name)
        t = self.theme
        self.plot.clear()
        self.legend.clear()

        view = df.tail(BARS_SHOWN)
        offset = len(df) - len(view)
        self.axis.dates = list(view.index)
        x = np.arange(len(view))
        o, h, l, c = (view[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
        o = np.where(np.isfinite(o), o, c)

        # Shade the look-back window.
        n = clamp_lookback(cfg.lookback_days)
        region = pg.LinearRegionItem(values=(len(view) - n - 0.5, len(view) - 0.5), movable=False,
                                     brush=pg.mkBrush(QColor(t.accent).red(), QColor(t.accent).green(),
                                                      QColor(t.accent).blue(), 38))
        for line in region.lines:
            line.setPen(pg.mkPen(None))
        self.plot.addItem(region)
        self.plot.addItem(CandleItem(o, h, l, c, t.up, t.down))

        lo, hi = np.nanmin(l), np.nanmax(h)
        for k, spec in enumerate(cfg.enabled_mas()):
            ma = compute_ma(df, spec.ma_type, spec.period).to_numpy(float)[offset:]
            if spec.label == highlight_ma and np.isfinite(ma).any():
                lo, hi = min(lo, np.nanmin(ma)), max(hi, np.nanmax(ma))
            color = t.ma_colors[k % len(t.ma_colors)]
            width = 3 if spec.label == highlight_ma else 1.6
            style = Qt.SolidLine if spec.label == highlight_ma or not highlight_ma else Qt.DashLine
            self.plot.plot(x, ma, pen=pg.mkPen(color, width=width, style=style), name=spec.label,
                           connect="finite")

        self.plot.setXRange(-1, len(view) + 1, padding=0)
        pad = (hi - lo) * 0.06
        self.plot.setYRange(lo - pad, hi + pad, padding=0)
        last = c[-1]
        label = f"{ticker}" + (f"  ·  {name}" if name else "") + f"   {last:,.2f}"
        if highlight_ma:
            label += f"   —  highlighted: {highlight_ma}"
        self.title.setText(label)
