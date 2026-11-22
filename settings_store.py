"""Saves the user's choices (MAs, look-back, theme, ...) between sessions."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from scanner import DEFAULT_MAS, MA_TYPES, MASpec, ScanConfig, clamp_lookback


@dataclass
class AppSettings:
    scan: ScanConfig = field(default_factory=ScanConfig)
    indexes: list[str] = field(default_factory=lambda: ["S&P 500"])
    use_custom: bool = False
    custom_tickers: str = ""
    theme: str = "Dark"
    font_pt: int = 10
    auto_scan_minutes: int = 0          # 0 = off
    market_hours_only: bool = True
    notify: bool = True
    geometry: str = ""                  # hex-encoded Qt window geometry
    splitter: str = ""

    # -- (de)serialisation -------------------------------------------------
    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_dict(cls, d: dict) -> "AppSettings":
        s = cls()
        scan = d.get("scan") or {}
        mas = []
        for i, m in enumerate(scan.get("mas") or []):
            try:
                t = str(m.get("ma_type", "SMA")).upper()
                mas.append(MASpec(int(m["period"]), t if t in MA_TYPES else "SMA", bool(m.get("enabled", True))))
            except Exception:
                continue
        if len(mas) != len(DEFAULT_MAS):
            mas = [MASpec(p, t) for p, t in DEFAULT_MAS]
        s.scan = ScanConfig(
            mas=mas,
            lookback_days=clamp_lookback(int(scan.get("lookback_days", 3))),
            tolerance_pct=float(scan.get("tolerance_pct", 1.0)),
            price_basis=scan.get("price_basis", "range") if scan.get("price_basis") in ("range", "close") else "range",
            side=scan.get("side", "any") if scan.get("side") in ("any", "above", "below") else "any",
            min_price=float(scan.get("min_price", 5.0)),
            min_avg_volume=float(scan.get("min_avg_volume", 300_000)),
        )
        for key in ("indexes", "use_custom", "custom_tickers", "theme", "font_pt",
                    "auto_scan_minutes", "market_hours_only", "notify", "geometry", "splitter"):
            if key in d:
                setattr(s, key, d[key])
        s.font_pt = int(min(18, max(8, int(s.font_pt))))
        return s


def load_settings(path: Path) -> AppSettings:
    try:
        return AppSettings.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
    except Exception:
        return AppSettings()


def save_settings(path: Path, settings: AppSettings) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(settings.to_json(), encoding="utf-8")
