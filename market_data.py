"""Daily price history: Yahoo Finance via yfinance, plus a synthetic demo feed."""
from __future__ import annotations

import time
from typing import Callable

import numpy as np
import pandas as pd

FIELDS = ["Open", "High", "Low", "Close", "Volume"]


def history_period(max_ma_period: int) -> str:
    """How much daily history to request so the longest MA is well warmed up."""
    if max_ma_period <= 120:
        return "1y"
    if max_ma_period <= 250:
        return "2y"
    return "5y"


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


def download_history(
    tickers: list[str],
    period: str,
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
                    chunk, period=period, interval="1d", group_by="ticker",
                    auto_adjust=True, threads=True, progress=False,
                )
                frames.update(split_download(raw, chunk))
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


def demo_history(tickers: list[str], bars: int = 400, seed: int = 7) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    end = pd.Timestamp.today().normalize()
    dates = pd.bdate_range(end=end, periods=bars)
    out = {}
    for t in tickers:
        drift = rng.normal(0.0004, 0.0006)
        vol = rng.uniform(0.012, 0.028)
        rets = rng.normal(drift, vol, bars)
        close = rng.uniform(40, 400) * np.exp(np.cumsum(rets))
        opn = close * (1 + rng.normal(0, vol / 3, bars))
        high = np.maximum(opn, close) * (1 + np.abs(rng.normal(0, vol / 2, bars)))
        low = np.minimum(opn, close) * (1 - np.abs(rng.normal(0, vol / 2, bars)))
        volume = rng.uniform(1e6, 3e7) * rng.lognormal(0, 0.3, bars)
        out[t] = pd.DataFrame(
            {"Open": opn, "High": high, "Low": low, "Close": close, "Volume": volume},
            index=dates,
        )
    return out
