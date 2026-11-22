"""Index membership lists (which tickers are in the S&P 500, Nasdaq-100, ...).

Each index has several public sources that are tried in order. Results are
cached on disk for a week so scans start fast and keep working if a source
is temporarily down. The Dow 30 also has a built-in list as a last resort.
"""
from __future__ import annotations

import io
import json
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable

import pandas as pd
import requests

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36 MATouchScanner/1.0"
)
CACHE_MAX_AGE = 7 * 24 * 3600


@dataclass
class Member:
    ticker: str
    name: str = ""
    sector: str = ""


def yahoo_symbol(raw: str) -> str:
    """Convert exchange-style symbols (BRK.B, BF/B) to Yahoo's format (BRK-B)."""
    s = str(raw).strip().upper()
    s = re.sub(r"\s+", "", s)
    return s.replace(".", "-").replace("/", "-")


def _valid_symbol(s: str) -> bool:
    return bool(re.fullmatch(r"[A-Z][A-Z0-9\-]{0,9}", s))


def _get(url: str, timeout: int = 20) -> requests.Response:
    r = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"}, timeout=timeout)
    r.raise_for_status()
    return r


# ---------------------------------------------------------------------------
# Parsers (kept separate from fetching so they can be unit tested)
# ---------------------------------------------------------------------------

_SYMBOL_COLS = ("symbol", "ticker", "ticker symbol")
_NAME_COLS = ("security", "company", "company name", "name")
_SECTOR_COLS = ("gics sector", "sector", "industry", "gics sub-industry")


def _flat_cols(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [str(c[-1]) for c in df.columns]
    df.columns = [re.sub(r"\[.*?\]", "", str(c)).strip() for c in df.columns]
    return df


def _pick(cols: list[str], wanted: tuple[str, ...]) -> str | None:
    lower = {c.lower(): c for c in cols}
    for w in wanted:
        if w in lower:
            return lower[w]
    return None


def members_from_tables(tables: list[pd.DataFrame], min_rows: int, max_rows: int) -> list[Member]:
    """Pick the first table that looks like a constituents list."""
    for t in tables:
        t = _flat_cols(t.copy())
        sym = _pick(list(t.columns), _SYMBOL_COLS)
        if not sym or not (min_rows <= len(t) <= max_rows):
            continue
        name = _pick(list(t.columns), _NAME_COLS)
        sector = _pick(list(t.columns), _SECTOR_COLS)
        out: list[Member] = []
        seen = set()
        for _, row in t.iterrows():
            tk = yahoo_symbol(row[sym])
            if not _valid_symbol(tk) or tk in seen:
                continue
            seen.add(tk)
            out.append(Member(tk, str(row[name]) if name else "", str(row[sector]) if sector else ""))
        if len(out) >= min_rows:
            return out
    return []


def parse_html_tables(html: str, min_rows: int, max_rows: int) -> list[Member]:
    tables = pd.read_html(io.StringIO(html))
    return members_from_tables(tables, min_rows, max_rows)


def parse_csv_table(text: str, min_rows: int, max_rows: int) -> list[Member]:
    return members_from_tables([pd.read_csv(io.StringIO(text))], min_rows, max_rows)


def parse_ishares_csv(text: str, min_rows: int) -> list[Member]:
    """iShares holdings CSVs have a few lines of fund info before the header row."""
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.lstrip('﻿"').startswith("Ticker")), None)
    if start is None:
        return []
    df = pd.read_csv(io.StringIO("\n".join(lines[start:])), on_bad_lines="skip")
    if "Asset Class" in df:
        df = df[df["Asset Class"].astype(str).str.strip().eq("Equity")]
    out, seen = [], set()
    for _, row in df.iterrows():
        tk = yahoo_symbol(row.get("Ticker", ""))
        if not _valid_symbol(tk) or tk in seen:
            continue
        seen.add(tk)
        out.append(Member(tk, str(row.get("Name", "")).title(), str(row.get("Sector", ""))))
    return out if len(out) >= min_rows else []


def parse_nasdaq_json(payload: dict, min_rows: int) -> list[Member]:
    """Find the list of {symbol, companyName} rows anywhere in Nasdaq's JSON."""
    def walk(node):
        if isinstance(node, list) and node and isinstance(node[0], dict) and "symbol" in node[0]:
            return node
        if isinstance(node, dict):
            for v in node.values():
                found = walk(v)
                if found:
                    return found
        return None

    rows = walk(payload) or []
    out, seen = [], set()
    for r in rows:
        tk = yahoo_symbol(r.get("symbol", ""))
        if _valid_symbol(tk) and tk not in seen:
            seen.add(tk)
            out.append(Member(tk, str(r.get("companyName", "")), str(r.get("sector", ""))))
    return out if len(out) >= min_rows else []


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

Source = Callable[[], list[Member]]


def wiki(url: str, min_rows: int, max_rows: int) -> Source:
    return lambda: parse_html_tables(_get(url).text, min_rows, max_rows)


def csv_url(url: str, min_rows: int, max_rows: int) -> Source:
    return lambda: parse_csv_table(_get(url).text, min_rows, max_rows)


def ishares(product_path: str, fund: str, min_rows: int) -> Source:
    url = (f"https://www.ishares.com/us/products/{product_path}/1467271812596.ajax"
           f"?fileType=csv&fileName={fund}_holdings&dataType=fund")
    return lambda: parse_ishares_csv(_get(url).text, min_rows)


def nasdaq_list(list_type: str, min_rows: int) -> Source:
    url = f"https://api.nasdaq.com/api/quote/list-type/{list_type}"
    return lambda: parse_nasdaq_json(_get(url).json(), min_rows)


# Built-in last resort for the Dow (composition after the June 29, 2026 change:
# Alphabet replaced Verizon). Larger indexes are always fetched live.
DOW30_FALLBACK = [
    "AAPL", "AMGN", "AMZN", "AXP", "BA", "CAT", "CRM", "CSCO", "CVX", "DIS",
    "GOOGL", "GS", "HD", "HON", "IBM", "JNJ", "JPM", "KO", "MCD", "MMM",
    "MRK", "MSFT", "NKE", "NVDA", "PG", "SHW", "TRV", "UNH", "V", "WMT",
]


def _static(tickers: list[str]) -> Source:
    return lambda: [Member(t) for t in tickers]


INDEXES: dict[str, list[Source]] = {
    "S&P 500": [
        wiki("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", 450, 600),
        csv_url("https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv", 450, 600),
        ishares("239726/ishares-core-sp-500-etf", "IVV", 450),
    ],
    "Nasdaq-100": [
        wiki("https://en.wikipedia.org/wiki/Nasdaq-100", 90, 110),
        nasdaq_list("nasdaq100", 90),
    ],
    "Dow Jones 30": [
        wiki("https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average", 28, 32),
        _static(DOW30_FALLBACK),
    ],
    "S&P MidCap 400": [
        wiki("https://en.wikipedia.org/wiki/List_of_S%26P_400_companies", 350, 450),
        ishares("239763/ishares-core-sp-midcap-etf", "IJH", 350),
    ],
    "S&P SmallCap 600": [
        wiki("https://en.wikipedia.org/wiki/List_of_S%26P_600_companies", 520, 650),
        ishares("239774/ishares-core-sp-smallcap-etf", "IJR", 520),
    ],
    "Russell 1000": [
        wiki("https://en.wikipedia.org/wiki/Russell_1000_Index", 900, 1100),
        ishares("239707/ishares-russell-1000-etf", "IWB", 900),
    ],
    "Russell 2000": [
        ishares("239710/ishares-russell-2000-etf", "IWM", 1700),
    ],
}

INDEX_NAMES = list(INDEXES.keys())


# ---------------------------------------------------------------------------
# Cache + public API
# ---------------------------------------------------------------------------

class UniverseCache:
    def __init__(self, cache_dir: Path):
        self.path = Path(cache_dir) / "index_members.json"

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def get(self, index: str) -> tuple[list[Member], float] | None:
        entry = self._load().get(index)
        if not entry:
            return None
        return [Member(**m) for m in entry["members"]], float(entry["fetched"])

    def put(self, index: str, members: list[Member]) -> None:
        data = self._load()
        data[index] = {"fetched": time.time(), "members": [asdict(m) for m in members]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data), encoding="utf-8")


def load_index(index: str, cache: UniverseCache | None, force_refresh: bool = False) -> tuple[list[Member], str]:
    """Return (members, note). Raises RuntimeError if nothing is available."""
    cached = cache.get(index) if cache else None
    if cached and not force_refresh and time.time() - cached[1] < CACHE_MAX_AGE:
        return cached[0], "cached list"

    errors = []
    for source in INDEXES[index]:
        try:
            members = source()
            if members:
                if cache:
                    cache.put(index, members)
                return members, "updated list"
        except Exception as exc:  # try the next source
            errors.append(str(exc)[:120])

    if cached:
        age_days = (time.time() - cached[1]) / 86400
        return cached[0], f"offline - using list from {age_days:.0f} days ago"
    raise RuntimeError(f"Could not load {index}: " + "; ".join(errors))


def parse_custom_tickers(text: str) -> list[Member]:
    out, seen = [], set()
    for tok in re.split(r"[\s,;]+", text or ""):
        tk = yahoo_symbol(tok.lstrip("$"))
        if tk and _valid_symbol(tk) and tk not in seen:
            seen.add(tk)
            out.append(Member(tk))
    return out
