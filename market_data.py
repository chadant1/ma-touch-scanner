"""Daily price history: Yahoo Finance via yfinance, plus a synthetic demo feed."""
from __future__ import annotations

import time
from typing import Callable

import numpy as np
import pandas as pd

FIELDS = ["Open", "High", "Low", "Close", "Volume"]


BARS_PER_YEAR = {"1d": 252, "1wk": 52, "1mo": 12}


def history_period(max_ma_period: int, timeframe: str = "1d") -> str:
    """How much history to request so the longest MA is well warmed up."""
    bars_needed = int(max_ma_period * 1.5) + 15
    years = bars_needed / BARS_PER_YEAR.get(timeframe, 252)
    for y in (1, 2, 5, 10):
        if years <= y:
            return f"{y}y"
    return "max"


def split_download(raw: pd.DataFrame, tickers: list[str]) -> dict[str, pd.DataFrame]:
    """Turn yfinance's (possibly multi-level) download frame into one frame per ticker."""
    out: dict[str, pd.DataFrame] = {}
    if raw is None or raw.empty:
        return out
    if isinstance(raw.columns, pd.MultiIndex):
        lvl0 = set(map(str, raw.columns.get_level_values(0)))
        ticker_level = 0 if any(t in lvl0 for t in tickers) else 1
        available = set(map(str, raw.columns.get_level_values(ticker_level)))
        for t in tickers:
            if t not in available:
                continue
            sub = raw.xs(t, axis=1, level=ticker_level)
            sub = sub[[c for c in FIELDS if c in sub.columns]].dropna(how="all")
            if not sub.empty and "Close" in sub:
                out[t] = sub
    else:  # single ticker, flat columns
        sub = raw[[c for c in FIELDS if c in raw.columns]].dropna(how="all")
        if not sub.empty and tickers:
            out[tickers[0]] = sub
    for t, df in out.items():
        idx = pd.to_datetime(df.index)
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_localize(None)
        df.index = idx
    return out


def merge_split_last_bar(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    """Yahoo sometimes returns the in-progress week/month as two rows; merge them."""
    if interval == "1d" or len(df) < 2:
        return df
    freq = "W-FRI" if interval == "1wk" else "M"
    a, b = df.index[-2], df.index[-1]
    if pd.Timestamp(a).to_period(freq) != pd.Timestamp(b).to_period(freq):
        return df
    prev, last = df.iloc[-2], df.iloc[-1]
    merged = prev.copy()
    if "High" in df:
        merged["High"] = max(prev["High"], last["High"])
    if "Low" in df:
        merged["Low"] = min(prev["Low"], last["Low"])
    if "Close" in df:
        merged["Close"] = last["Close"]
    if "Volume" in df:
        merged["Volume"] = prev["Volume"] + last["Volume"]
    out = df.iloc[:-1].copy()
    out.iloc[-1] = merged
    return out


def download_history(
    tickers: list[str],
    period: str,
    interval: str = "1d",
    chunk_size: int = 100,
    progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Download daily bars in chunks. Returns (frames, failed_tickers)."""
    import yfinance as yf  # imported lazily: it is slow to import

    frames: dict[str, pd.DataFrame] = {}
    total = len(tickers)
    for start in range(0, total, chunk_size):
        if cancelled and cancelled():
            break
        chunk = tickers[start:start + chunk_size]
        for attempt in range(3):
            try:
                raw = yf.download(
                    chunk, period=period, interval=interval, group_by="ticker",
                    auto_adjust=True, threads=True, progress=False,
                )
                for t, f in split_download(raw, chunk).items():
                    frames[t] = merge_split_last_bar(f, interval)
                break
            except Exception:
                time.sleep(2 * (attempt + 1))
        if progress:
            progress(min(start + chunk_size, total), total)
    failed = [t for t in tickers if t not in frames]
    return frames, failed


# ---------------------------------------------------------------------------
# Demo data (no internet needed) - launch with:  python main.py --demo
# ---------------------------------------------------------------------------

DEMO_TICKERS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "JPM", "V",
    "UNH", "XOM", "LLY", "MA", "HD", "PG", "COST", "JNJ", "ABBV", "WMT",
    "BAC", "KO", "PEP", "CRM", "ORCL", "AMD", "NFLX", "ADBE", "TMO", "CSCO",
]


DEMO_FREQ = {"1d": "B", "1wk": "W-FRI", "1mo": "MS"}
DEMO_SCALE = {"1d": 1.0, "1wk": 2.2, "1mo": 4.5}   # bigger moves on bigger bars


def demo_history(tickers: list[str], bars: int = 400, seed: int = 7,
                 timeframe: str = "1d") -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    end = pd.Timestamp.today().normalize()
    dates = pd.date_range(end=end, periods=bars, freq=DEMO_FREQ.get(timeframe, "B"))
    k = DEMO_SCALE.get(timeframe, 1.0)
    out = {}
    for t in tickers:
        drift = rng.normal(0.0004, 0.0006) * k * k
        vol = rng.uniform(0.012, 0.028) * k
        rets = rng.normal(drift, vol, bars)
        close = rng.uniform(40, 400) * np.exp(np.cumsum(rets))
        opn = close * (1 + rng.normal(0, vol / 3, bars))
        high = np.maximum(opn, close) * (1 + np.abs(rng.normal(0, vol / 2, bars)))
        low = np.minimum(opn, close) * (1 - np.abs(rng.normal(0, vol / 2, bars)))
        volume = rng.uniform(1e6, 3e7) * rng.lognormal(0, 0.3, bars) * {"1d": 1, "1wk": 5, "1mo": 21}.get(timeframe, 1)
        out[t] = pd.DataFrame(
            {"Open": opn, "High": high, "Low": low, "Close": close, "Volume": volume},
            index=dates,
        )
    return out
