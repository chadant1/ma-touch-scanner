"""Moving-average math and the "touched / came close" scan.

This module has no GUI or network code so it can be tested on its own.
Every price frame is a pandas DataFrame indexed by date with the columns
Open, High, Low, Close, Volume (oldest row first).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Moving averages
# ---------------------------------------------------------------------------

MA_TYPES = ["SMA", "EMA", "WMA", "HMA", "VWMA", "DEMA", "TEMA"]

MA_DESCRIPTIONS = {
    "SMA": "Simple moving average - plain average of the last N closes",
    "EMA": "Exponential moving average - weights recent closes more",
    "WMA": "Weighted moving average - linear weights, newest close heaviest",
    "HMA": "Hull moving average - fast, low-lag (built from WMAs)",
    "VWMA": "Volume-weighted moving average - closes weighted by volume",
    "DEMA": "Double exponential moving average - less lag than EMA",
    "TEMA": "Triple exponential moving average - even less lag",
}


def sma(close: pd.Series, n: int) -> pd.Series:
    return close.rolling(n, min_periods=n).mean()


def ema(close: pd.Series, n: int) -> pd.Series:
    return close.ewm(span=n, adjust=False, min_periods=n).mean()


def wma(close: pd.Series, n: int) -> pd.Series:
    values = close.to_numpy(dtype=float)
    out = np.full(len(values), np.nan)
    if n >= 1 and len(values) >= n:
        weights = np.arange(1, n + 1, dtype=float)
        # np.convolve flips the kernel, so reversing it puts weight n on the newest bar.
        out[n - 1:] = np.convolve(values, weights[::-1], mode="valid") / weights.sum()
    return pd.Series(out, index=close.index)


def hma(close: pd.Series, n: int) -> pd.Series:
    half = max(1, n // 2)
    root = max(1, int(round(np.sqrt(n))))
    raw = 2 * wma(close, half) - wma(close, n)
    return wma(raw, root)


def vwma(close: pd.Series, volume: pd.Series, n: int) -> pd.Series:
    vol = volume.astype(float)
    pv = (close * vol).rolling(n, min_periods=n).sum()
    vs = vol.rolling(n, min_periods=n).sum()
    out = pv / vs.replace(0, np.nan)
    # Fall back to a simple average where volume is missing/zero.
    return out.fillna(sma(close, n)).where(sma(close, n).notna())


def dema(close: pd.Series, n: int) -> pd.Series:
    e1 = ema(close, n)
    e2 = ema(e1, n)
    return 2 * e1 - e2


def tema(close: pd.Series, n: int) -> pd.Series:
    e1 = ema(close, n)
    e2 = ema(e1, n)
    e3 = ema(e2, n)
    return 3 * e1 - 3 * e2 + e3


def compute_ma(df: pd.DataFrame, ma_type: str, period: int) -> pd.Series:
    """Return the moving average of df['Close'] (same index as df)."""
    close = df["Close"].astype(float)
    t = ma_type.upper()
    if t == "SMA":
        return sma(close, period)
    if t == "EMA":
        return ema(close, period)
    if t == "WMA":
        return wma(close, period)
    if t == "HMA":
        return hma(close, period)
    if t == "VWMA":
        vol = df["Volume"] if "Volume" in df else pd.Series(1.0, index=df.index)
        return vwma(close, vol, period)
    if t == "DEMA":
        return dema(close, period)
    if t == "TEMA":
        return tema(close, period)
    raise ValueError(f"Unknown moving-average type: {ma_type}")


# ---------------------------------------------------------------------------
# Scan configuration and results
# ---------------------------------------------------------------------------

DEFAULT_MAS = [
    (10, "EMA"),
    (21, "EMA"),
    (50, "SMA"),
    (100, "SMA"),
    (200, "SMA"),
]


@dataclass
class MASpec:
    period: int
    ma_type: str = "SMA"
    enabled: bool = True

    @property
    def label(self) -> str:
        return f"{self.ma_type} {self.period}"


PRICE_BASIS = {
    "range": "High / Low (intraday wicks)",
    "close": "Closing price only",
}

TIMEFRAMES = {
    "1d": "Daily",
    "1wk": "Weekly",
    "1mo": "Monthly",
}
# Trading days per bar, used to show average volume per day on any timeframe.
DAYS_PER_BAR = {"1d": 1, "1wk": 5, "1mo": 21}
BAR_UNITS = {"1d": ("day", "days", "d"), "1wk": ("week", "weeks", "w"), "1mo": ("month", "months", "mo")}


def bars_ago_text(n: int, timeframe: str) -> str:
    if n == 0:
        return {"1d": "Today", "1wk": "This week", "1mo": "This month"}.get(timeframe, "Latest")
    return f"{n}{BAR_UNITS.get(timeframe, BAR_UNITS['1d'])[2]} ago"


SIDES = {
    "any": "Any side",
    "above": "Price above MA (support test)",
    "below": "Price below MA (resistance test)",
}


@dataclass
class ScanConfig:
    mas: list[MASpec] = field(default_factory=lambda: [MASpec(p, t) for p, t in DEFAULT_MAS])
    timeframe: str = "1d"           # "1d" daily, "1wk" weekly, "1mo" monthly bars
    lookback_days: int = 3          # 1..10 most recent bars (days/weeks/months), latest included
    tolerance_pct: float = 1.0      # how close counts as "near", as % of the MA value
    price_basis: str = "range"      # "range" or "close"
    side: str = "any"               # "any", "above", "below"
    min_price: float = 5.0
    min_avg_volume: float = 300_000

    def enabled_mas(self) -> list[MASpec]:
        return [m for m in self.mas if m.enabled]

    def max_period(self) -> int:
        periods = [m.period for m in self.enabled_mas()]
        return max(periods) if periods else 0


@dataclass
class Hit:
    ticker: str
    ma_label: str
    ma_type: str
    period: int
    status: str            # "Touched", "Crossed", or "Near"
    days_ago: int          # bars ago; 0 = most recent bar
    bar_date: pd.Timestamp
    ma_value: float        # MA on the most recent bar
    last_close: float
    change_pct: float      # last close vs previous close
    distance_pct: float    # (last close - MA) / MA, signed
    closest_pct: float     # smallest gap between price and MA inside the lookback window
    side: str              # "Above" or "Below"
    hits_in_window: int    # how many bars in the window qualified
    avg_volume: float      # average shares per trading day
    timeframe: str = "1d"
    name: str = ""
    sector: str = ""
    indexes: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return (self.ticker, self.ma_label)


def clamp_lookback(days: int) -> int:
    return int(min(10, max(1, days)))


def clean_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Keep rows with a close and make sure High/Low/Volume exist."""
    if df is None or df.empty or "Close" not in df:
        return pd.DataFrame()
    out = df.dropna(subset=["Close"]).copy()
    for col in ("High", "Low"):
        if col not in out:
            out[col] = out["Close"]
        out[col] = out[col].fillna(out["Close"])
    if "Volume" not in out:
        out["Volume"] = np.nan
    return out


def _bar_gap(lo: float, hi: float, m: float) -> tuple[bool, float]:
    """(touched, gap%) of a bar's price range [lo, hi] against MA value m."""
    if lo <= m <= hi:
        return True, 0.0
    if lo > m:
        return False, (lo - m) / m * 100.0
    return False, (m - hi) / m * 100.0


def scan_symbol(ticker: str, df: pd.DataFrame, cfg: ScanConfig) -> list[Hit]:
    """Return one Hit per enabled MA the stock touched or came close to."""
    df = clean_frame(df)
    if len(df) < 2:
        return []

    closes = df["Close"].to_numpy(dtype=float)
    last_close = float(closes[-1])
    prev_close = float(closes[-2])
    change_pct = (last_close / prev_close - 1.0) * 100.0 if prev_close else 0.0
    # Average volume per trading day over roughly the last 50 days, whatever the bar size.
    per_bar = DAYS_PER_BAR.get(cfg.timeframe, 1)
    lookback_bars = max(2, int(round(50 / per_bar)))
    avg_volume = (float(df["Volume"].tail(lookback_bars).mean()) / per_bar
                  if df["Volume"].notna().any() else float("nan"))

    if last_close < cfg.min_price:
        return []
    if cfg.min_avg_volume > 0 and not (avg_volume >= cfg.min_avg_volume):
        return []

    n = clamp_lookback(cfg.lookback_days)
    tol = max(0.0, float(cfg.tolerance_pct))
    highs = df["High"].to_numpy(dtype=float)
    lows = df["Low"].to_numpy(dtype=float)
    dates = df.index

    hits: list[Hit] = []
    for spec in cfg.enabled_mas():
        if spec.period < 1 or len(df) < spec.period:
            continue
        ma = compute_ma(df, spec.ma_type, spec.period).to_numpy(dtype=float)
        ma_last = ma[-1]
        if not np.isfinite(ma_last) or ma_last <= 0:
            continue

        side = "Above" if last_close >= ma_last else "Below"
        if cfg.side == "above" and side != "Above":
            continue
        if cfg.side == "below" and side != "Below":
            continue

        best_gap = float("inf")
        # Newest bar that actually touched/crossed, and newest bar that was only near.
        recent_touch: tuple[int, str] | None = None
        recent_near: tuple[int, str] | None = None
        count = 0
        for days_ago in range(min(n, len(df))):
            i = len(df) - 1 - days_ago
            m = ma[i]
            if not np.isfinite(m) or m <= 0:
                continue
            if cfg.price_basis == "close":
                c = closes[i]
                gap = abs(c - m) / m * 100.0
                crossed = False
                if i > 0 and np.isfinite(ma[i - 1]):
                    prev_diff, cur_diff = closes[i - 1] - ma[i - 1], c - m
                    crossed = (prev_diff <= 0 < cur_diff) or (prev_diff >= 0 > cur_diff)
                touched = crossed or gap == 0.0
                if crossed:
                    gap = 0.0
                status = "Crossed" if crossed else ("Touched" if touched else "Near")
            else:
                touched, gap = _bar_gap(lows[i], highs[i], m)
                status = "Touched" if touched else "Near"
            best_gap = min(best_gap, gap)
            if touched:
                count += 1
                if recent_touch is None:
                    recent_touch = (days_ago, status)
            elif gap <= tol:
                count += 1
                if recent_near is None:
                    recent_near = (days_ago, status)

        # A real touch inside the window outranks a later near-miss.
        recent = recent_touch or recent_near
        if recent is None:
            continue
        days_ago, status = recent
        hits.append(Hit(
            ticker=ticker,
            ma_label=spec.label,
            ma_type=spec.ma_type,
            period=spec.period,
            status=status,
            days_ago=days_ago,
            bar_date=pd.Timestamp(dates[len(df) - 1 - days_ago]),
            ma_value=float(ma_last),
            last_close=last_close,
            change_pct=change_pct,
            distance_pct=(last_close - ma_last) / ma_last * 100.0,
            closest_pct=best_gap,
            side=side,
            hits_in_window=count,
            avg_volume=avg_volume,
            timeframe=cfg.timeframe,
        ))
    return hits


def scan_universe(
    data: dict[str, pd.DataFrame],
    cfg: ScanConfig,
    progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> list[Hit]:
    results: list[Hit] = []
    items = list(data.items())
    for k, (ticker, df) in enumerate(items, 1):
        if cancelled and cancelled():
            break
        try:
            results.extend(scan_symbol(ticker, df, cfg))
        except Exception:  # one bad symbol should never stop the scan
            pass
        if progress and (k % 25 == 0 or k == len(items)):
            progress(k, len(items))
    results.sort(key=lambda h: (h.days_ago, h.closest_pct, h.ticker))
    return results


def hit_window_rows(df: pd.DataFrame, cfg: ScanConfig) -> Iterable[int]:
    """Row positions covered by the lookback window (for charting)."""
    n = clamp_lookback(cfg.lookback_days)
    return range(max(0, len(df) - n), len(df))
