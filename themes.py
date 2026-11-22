"""Color themes. Each theme drives the Qt palette, a small stylesheet and the chart."""
from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtGui import QColor, QPalette


@dataclass(frozen=True)
class Theme:
    name: str
    dark: bool
    window: str
    panel: str
    base: str
    alt_base: str
    text: str
    muted: str
    border: str
    accent: str
    accent_text: str
    up: str
    down: str
    touched: str
    near: str
    chart_bg: str
    grid: str
    ma_colors: tuple = field(default_factory=tuple)


THEMES: dict[str, Theme] = {t.name: t for t in [
    Theme("Dark", True,
          window="#16181d", panel="#1d2027", base="#121418", alt_base="#181b21",
          text="#d7dae0", muted="#8a909c", border="#2c313a", accent="#4f8cff", accent_text="#ffffff",
          up="#3fb950", down="#f85149", touched="#e3b341", near="#58a6ff",
          chart_bg="#121418", grid="#262a33",
          ma_colors=("#f0b429", "#4f8cff", "#c678dd", "#56b6c2", "#ff7b72", "#98c379", "#e5c07b", "#d19a66")),
    Theme("Midnight Blue", True,
          window="#0d1526", panel="#121c31", base="#0a1120", alt_base="#0f182b",
          text="#cfd8ea", muted="#7d8ba6", border="#1f2c47", accent="#3d7eff", accent_text="#ffffff",
          up="#2ec27e", down="#ff6b6b", touched="#ffcc4d", near="#6cb6ff",
          chart_bg="#0a1120", grid="#1a2540",
          ma_colors=("#ffcc4d", "#6cb6ff", "#b48eff", "#4dd8c4", "#ff8f70", "#9fe07a", "#ffd88a", "#ff9ed2")),
    Theme("Night Shift (warm)", True,
          window="#1b1612", panel="#231d17", base="#16120e", alt_base="#1c1712",
          text="#e8d8c3", muted="#a08c74", border="#3a3026", accent="#d98a3d", accent_text="#1b1612",
          up="#9cc46b", down="#e0705a", touched="#f2c14e", near="#d9a066",
          chart_bg="#16120e", grid="#2d251d",
          ma_colors=("#f2c14e", "#d98a3d", "#c9a27e", "#9cc46b", "#e0705a", "#b8a07a", "#e6b98a", "#a3876a")),
    Theme("OLED Black", True,
          window="#000000", panel="#0a0a0a", base="#000000", alt_base="#080808",
          text="#e6e6e6", muted="#8c8c8c", border="#222222", accent="#5b9dff", accent_text="#000000",
          up="#35d07f", down="#ff5c5c", touched="#ffd33d", near="#5b9dff",
          chart_bg="#000000", grid="#1a1a1a",
          ma_colors=("#ffd33d", "#5b9dff", "#d38cff", "#3ee0d0", "#ff8a65", "#a5e86b", "#ffb86b", "#ff79c6")),
    Theme("High Contrast", True,
          window="#000000", panel="#000000", base="#000000", alt_base="#101010",
          text="#ffffff", muted="#d0d0d0", border="#ffffff", accent="#ffff00", accent_text="#000000",
          up="#00ff7f", down="#ff4040", touched="#ffff00", near="#00e5ff",
          chart_bg="#000000", grid="#444444",
          ma_colors=("#ffff00", "#00e5ff", "#ff66ff", "#00ff7f", "#ff8800", "#ffffff", "#ff4040", "#88aaff")),
    Theme("Light", False,
          window="#f3f4f6", panel="#ffffff", base="#ffffff", alt_base="#f7f8fa",
          text="#1f2328", muted="#656d76", border="#d0d7de", accent="#0969da", accent_text="#ffffff",
          up="#1a7f37", down="#cf222e", touched="#9a6700", near="#0969da",
          chart_bg="#ffffff", grid="#eaeef2",
          ma_colors=("#bf8700", "#0969da", "#8250df", "#1b7c83", "#cf222e", "#1a7f37", "#bc4c00", "#bf3989")),
    Theme("Solarized Light", False,
          window="#eee8d5", panel="#fdf6e3", base="#fdf6e3", alt_base="#f5efdc",
          text="#586e75", muted="#839496", border="#d6cfb8", accent="#268bd2", accent_text="#fdf6e3",
          up="#859900", down="#dc322f", touched="#b58900", near="#268bd2",
          chart_bg="#fdf6e3", grid="#eee8d5",
          ma_colors=("#b58900", "#268bd2", "#6c71c4", "#2aa198", "#cb4b16", "#859900", "#d33682", "#657b83")),
]}

DEFAULT_THEME = "Dark"


def get_theme(name: str) -> Theme:
    return THEMES.get(name, THEMES[DEFAULT_THEME])


def build_palette(t: Theme) -> QPalette:
    p = QPalette()
    c = QColor
    p.setColor(QPalette.Window, c(t.window))
    p.setColor(QPalette.WindowText, c(t.text))
    p.setColor(QPalette.Base, c(t.base))
    p.setColor(QPalette.AlternateBase, c(t.alt_base))
    p.setColor(QPalette.ToolTipBase, c(t.panel))
    p.setColor(QPalette.ToolTipText, c(t.text))
    p.setColor(QPalette.PlaceholderText, c(t.muted))
    p.setColor(QPalette.Text, c(t.text))
    p.setColor(QPalette.Button, c(t.panel))
    p.setColor(QPalette.ButtonText, c(t.text))
    p.setColor(QPalette.BrightText, c(t.down))
    p.setColor(QPalette.Highlight, c(t.accent))
    p.setColor(QPalette.HighlightedText, c(t.accent_text))
    p.setColor(QPalette.Link, c(t.accent))
    p.setColor(QPalette.Mid, c(t.border))
    p.setColor(QPalette.Dark, c(t.border))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, c(t.muted))
    return p


def build_stylesheet(t: Theme, font_pt: int) -> str:
    return f"""
    QWidget {{ font-size: {font_pt}pt; }}
    QMainWindow, QDialog {{ background: {t.window}; }}
    QGroupBox {{
        background: {t.panel}; border: 1px solid {t.border}; border-radius: 8px;
        margin-top: 14px; padding: 10px 8px 8px 8px; font-weight: 600;
    }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {t.muted}; }}
    QScrollArea, QScrollArea > QWidget > QWidget {{ background: {t.window}; border: none; }}
    QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        background: {t.base}; color: {t.text}; border: 1px solid {t.border};
        border-radius: 5px; padding: 3px 6px; selection-background-color: {t.accent};
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
        border: 1px solid {t.accent};
    }}
    QComboBox QAbstractItemView {{ background: {t.panel}; color: {t.text}; selection-background-color: {t.accent}; }}
    QPushButton {{
        background: {t.panel}; color: {t.text}; border: 1px solid {t.border};
        border-radius: 6px; padding: 5px 12px;
    }}
    QPushButton:hover {{ border-color: {t.accent}; }}
    QPushButton:disabled {{ color: {t.muted}; }}
    QPushButton#primary {{ background: {t.accent}; color: {t.accent_text}; border: none; font-weight: 700; }}
    QPushButton#primary:disabled {{ background: {t.border}; color: {t.muted}; }}
    QTableView {{
        background: {t.base}; alternate-background-color: {t.alt_base}; color: {t.text};
        gridline-color: {t.border}; border: 1px solid {t.border}; border-radius: 6px;
        selection-background-color: {t.accent}; selection-color: {t.accent_text};
    }}
    QHeaderView::section {{
        background: {t.panel}; color: {t.muted}; border: none;
        border-bottom: 1px solid {t.border}; border-right: 1px solid {t.border}; padding: 4px 6px; font-weight: 600;
    }}
    QProgressBar {{
        background: {t.base}; border: 1px solid {t.border}; border-radius: 5px;
        text-align: center; color: {t.text}; min-height: 16px;
    }}
    QProgressBar::chunk {{ background: {t.accent}; border-radius: 4px; }}
    QStatusBar {{ color: {t.muted}; }}
    QToolBar {{ background: {t.panel}; border-bottom: 1px solid {t.border}; spacing: 6px; padding: 4px; }}
    QSplitter::handle {{ background: {t.border}; }}
    QLabel#muted {{ color: {t.muted}; }}
    QLabel#chartTitle {{ font-weight: 700; }}
    QCheckBox {{ spacing: 7px; }}
    QCheckBox::indicator {{
        width: 14px; height: 14px; border: 1px solid {t.muted}; border-radius: 4px; background: {t.base};
    }}
    QCheckBox::indicator:hover {{ border-color: {t.accent}; }}
    QCheckBox::indicator:checked {{ background: {t.accent}; border-color: {t.accent}; }}
    QCheckBox::indicator:disabled {{ border-color: {t.border}; }}
    QCheckBox::indicator:checked:disabled {{ background: {t.border}; }}
    QMenu {{ background: {t.panel}; color: {t.text}; border: 1px solid {t.border}; }}
    QMenu::item:selected {{ background: {t.accent}; color: {t.accent_text}; }}
    QToolTip {{ background: {t.panel}; color: {t.text}; border: 1px solid {t.border}; }}
    """
