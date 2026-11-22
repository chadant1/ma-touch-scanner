"""Unit tests (no internet needed):  python -m pytest -q"""
import numpy as np
import pandas as pd
import pytest

import market_data
import universe
from scanner import (MA_TYPES, MASpec, ScanConfig, compute_ma, ema, hma, scan_symbol,
                     scan_universe, sma, wma)


def frame(closes, highs=None, lows=None, volume=1_000_000):
    closes = np.asarray(closes, float)
    idx = pd.bdate_range("2025-01-01", periods=len(closes))
    return pd.DataFrame({
        "Open": closes,
        "High": closes if highs is None else np.asarray(highs, float),
        "Low": closes if lows is None else np.asarray(lows, float),
        "Close": closes,
        "Volume": np.full(len(closes), float(volume)),
    }, index=idx)


def cfg(period=5, ma_type="SMA", **kw):
    base = dict(mas=[MASpec(period, ma_type)], lookback_days=3, tolerance_pct=1.0,
                min_price=0, min_avg_volume=0)
    base.update(kw)
    return ScanConfig(**base)


# --- moving averages --------------------------------------------------------

def test_sma_matches_manual():
    s = pd.Series(np.arange(1, 11, dtype=float))
    assert sma(s, 3).iloc[-1] == pytest.approx((8 + 9 + 10) / 3)
    assert np.isnan(sma(s, 3).iloc[1])


def test_wma_matches_manual():
    s = pd.Series([1.0, 2.0, 3.0, 4.0])
    # newest gets weight 3: (2*1 + 3*2 + 4*3) / 6
    assert wma(s, 3).iloc[-1] == pytest.approx((2 * 1 + 3 * 2 + 4 * 3) / 6)
    assert np.isnan(wma(s, 3).iloc[1])


def test_ema_matches_recursive_definition():
    s = pd.Series(np.random.default_rng(1).normal(100, 2, 50))
    n = 10
    a = 2 / (n + 1)
    e = s.iloc[0]
    for v in s.iloc[1:]:
        e = a * v + (1 - a) * e
    assert ema(s, n).iloc[-1] == pytest.approx(e)


def test_constant_series_all_types_equal_price():
    df = frame([50.0] * 300)
    for t in MA_TYPES:
        assert compute_ma(df, t, 20).iloc[-1] == pytest.approx(50.0), t


def test_hma_tracks_trend_with_less_lag():
    df = frame(np.linspace(10, 110, 200))
    assert hma(df["Close"], 21).iloc[-1] > sma(df["Close"], 21).iloc[-1]


# --- touch detection --------------------------------------------------------

def test_intraday_wick_touch_today():
    closes = [100.0] * 30 + [110.0] * 5
    lows = list(closes)
    lows[-1] = 99.0          # today's low dips to/through the 30-bar SMA area
    df = frame(closes, lows=lows, highs=closes)
    hits = scan_symbol("TEST", df, cfg(period=30, tolerance_pct=0.0))
    assert len(hits) == 1
    h = hits[0]
    assert h.status == "Touched" and h.days_ago == 0 and h.side == "Above"


def test_near_within_tolerance():
    closes = [100.0] * 40 + [101.5]
    df = frame(closes)
    ma_last = sma(df["Close"], 20).iloc[-1]
    hits = scan_symbol("NEAR", df, cfg(period=20, tolerance_pct=2.0, lookback_days=1))
    assert hits and hits[0].status == "Near"
    assert hits[0].closest_pct == pytest.approx((101.5 - ma_last) / ma_last * 100)
    assert scan_symbol("NEAR", df, cfg(period=20, tolerance_pct=0.5, lookback_days=1)) == []


def test_touch_outranks_later_near_miss():
    closes = [100.0] * 40 + [101.5]   # touched yesterday (flat at the MA), near today
    hits = scan_symbol("T", frame(closes), cfg(period=20, tolerance_pct=2.0, lookback_days=3))
    assert hits[0].status == "Touched" and hits[0].days_ago == 1 and hits[0].hits_in_window == 3


def test_lookback_window_limits():
    # Touch happened 4 bars ago, then price ran away.
    closes = [100.0] * 40 + [100.0, 120, 125, 130, 135]
    df = frame(closes)
    assert scan_symbol("X", df, cfg(period=40, lookback_days=3, tolerance_pct=0.5)) == []
    hits = scan_symbol("X", df, cfg(period=40, lookback_days=5, tolerance_pct=0.5))
    assert hits and hits[0].days_ago == 4


def test_lookback_is_clamped_to_1_10():
    closes = [100.0] * 40 + [100.0] + [150.0] * 12
    df = frame(closes)
    assert scan_symbol("X", df, cfg(period=40, lookback_days=50, tolerance_pct=0.5)) == []


def test_side_filter():
    closes = [100.0] * 40 + [99.5]
    df = frame(closes)
    assert scan_symbol("B", df, cfg(period=20, side="above")) == []
    hits = scan_symbol("B", df, cfg(period=20, side="below"))
    assert hits and hits[0].side == "Below"


def test_close_basis_detects_cross():
    closes = [100.0] * 30 + [90.0] * 10 + [112.0]
    df = frame(closes, highs=np.array(closes) + 1, lows=np.array(closes) - 1)
    hits = scan_symbol("C", df, cfg(period=10, price_basis="close", tolerance_pct=0.1, lookback_days=1))
    assert hits and hits[0].status == "Crossed"


def test_min_price_and_volume_filters():
    df = frame([3.0] * 60)
    assert scan_symbol("P", df, cfg(period=10, min_price=5)) == []
    df = frame([50.0] * 60, volume=1000)
    assert scan_symbol("V", df, cfg(period=10, min_avg_volume=10_000)) == []


def test_not_enough_history_is_skipped():
    df = frame([100.0] * 50)
    assert scan_symbol("S", df, cfg(period=200)) == []


def test_multiple_mas_and_universe_sorting():
    closes = [100.0] * 250
    df = frame(closes)
    c = ScanConfig(mas=[MASpec(10, "EMA"), MASpec(21, "SMA"), MASpec(200, "SMA"), MASpec(50, "WMA", False)],
                   lookback_days=2, tolerance_pct=0.5, min_price=0, min_avg_volume=0)
    hits = scan_universe({"A": df, "B": frame([10.0] * 5)}, c)
    assert sorted(h.ma_label for h in hits) == ["EMA 10", "SMA 200", "SMA 21"]


def test_nan_rows_are_ignored():
    closes = [100.0] * 60
    df = frame(closes)
    df.iloc[10:15] = np.nan
    assert scan_symbol("N", df, cfg(period=20))


# --- data plumbing ----------------------------------------------------------

def test_split_download_multiindex_ticker_first():
    idx = pd.bdate_range("2025-01-01", periods=3)
    cols = pd.MultiIndex.from_product([["AAA", "BBB"], market_data.FIELDS])
    raw = pd.DataFrame(np.arange(30, dtype=float).reshape(3, 10), index=idx, columns=cols)
    raw.loc[:, ("BBB", slice(None))] = np.nan
    out = market_data.split_download(raw, ["AAA", "BBB"])
    assert list(out) == ["AAA"] and list(out["AAA"].columns) == market_data.FIELDS


def test_split_download_multiindex_field_first():
    idx = pd.bdate_range("2025-01-01", periods=3)
    cols = pd.MultiIndex.from_product([market_data.FIELDS, ["AAA", "BBB"]])
    raw = pd.DataFrame(np.ones((3, 10)), index=idx, columns=cols)
    out = market_data.split_download(raw, ["AAA", "BBB"])
    assert set(out) == {"AAA", "BBB"}


def test_history_period():
    assert market_data.history_period(50) == "1y"
    assert market_data.history_period(200) == "2y"
    assert market_data.history_period(400) == "5y"
    assert market_data.history_period(50, "1wk") == "2y"
    assert market_data.history_period(200, "1wk") == "10y"
    assert market_data.history_period(21, "1mo") == "5y"
    assert market_data.history_period(200, "1mo") == "max"


def test_bars_ago_text():
    from scanner import bars_ago_text
    assert bars_ago_text(0, "1d") == "Today"
    assert bars_ago_text(0, "1wk") == "This week"
    assert bars_ago_text(3, "1wk") == "3w ago"
    assert bars_ago_text(2, "1mo") == "2mo ago"


def test_weekly_volume_is_reported_per_day():
    df = frame([50.0] * 60, volume=5_000_000)          # 5M shares per weekly bar
    hits = scan_symbol("W", df, cfg(period=10, timeframe="1wk", min_avg_volume=900_000))
    assert hits and hits[0].avg_volume == pytest.approx(1_000_000)
    assert hits[0].timeframe == "1wk"
    assert scan_symbol("W", df, cfg(period=10, timeframe="1wk", min_avg_volume=1_100_000)) == []


def test_merge_split_last_bar():
    idx = pd.to_datetime(["2026-10-01", "2026-11-01", "2026-11-20"])
    df = pd.DataFrame({"Open": [1, 2, 9], "High": [3, 4, 8], "Low": [0.5, 1.5, 1.0],
                       "Close": [2, 3, 7], "Volume": [10, 20, 5]}, index=idx, dtype=float)
    out = market_data.merge_split_last_bar(df, "1mo")
    assert len(out) == 2
    last = out.iloc[-1]
    assert (last["Open"], last["High"], last["Low"], last["Close"], last["Volume"]) == (2, 8, 1.0, 7, 25)
    assert len(market_data.merge_split_last_bar(df.iloc[:2], "1mo")) == 2
    assert len(market_data.merge_split_last_bar(df, "1d")) == 3


@pytest.mark.parametrize("tf", ["1d", "1wk", "1mo"])
def test_demo_data_every_timeframe(tf):
    data = market_data.demo_history(market_data.DEMO_TICKERS, timeframe=tf)
    hits = scan_universe(data, ScanConfig(timeframe=tf, lookback_days=5, tolerance_pct=2.0,
                                          min_price=0, min_avg_volume=0))
    assert hits and all(h.timeframe == tf for h in hits)


def test_symbols_and_custom_list():
    assert universe.yahoo_symbol("brk.b") == "BRK-B"
    got = [m.ticker for m in universe.parse_custom_tickers("aapl, $msft;  BRK.B\nAAPL bad!!")]
    assert got == ["AAPL", "MSFT", "BRK-B"]


def test_parse_wikipedia_style_table():
    rows = "".join(f"<tr><td>T{i:03d}</td><td>Co {i}</td><td>Tech</td></tr>" for i in range(500))
    html = ("<table><tr><th>Year</th><th>Close</th></tr><tr><td>1</td><td>2</td></tr></table>"
            f"<table><tr><th>Symbol</th><th>Security</th><th>GICS Sector</th></tr>{rows}</table>")
    members = universe.parse_html_tables(html, 450, 600)
    assert len(members) == 500 and members[0].name == "Co 0" and members[0].sector == "Tech"


def test_parse_ishares_csv():
    text = ('Fund Holdings as of,"Nov 20, 2026"\n\xa0\n'
            "Ticker,Name,Sector,Asset Class,Market Value,Weight (%)\n"
            + "".join(f"T{i},NAME {i},Tech,Equity,1,0.1\n" for i in range(30))
            + "USD,USD CASH,Cash,Cash,1,0.1\n")
    members = universe.parse_ishares_csv(text, 20)
    assert len(members) == 30 and all(m.ticker != "USD" for m in members)


def test_parse_nasdaq_json():
    payload = {"data": {"data": {"rows": [{"symbol": f"S{i}", "companyName": "X"} for i in range(100)]}}}
    assert len(universe.parse_nasdaq_json(payload, 90)) == 100


def test_cache_roundtrip(tmp_path):
    cache = universe.UniverseCache(tmp_path)
    cache.put("Dow Jones 30", [universe.Member("AAPL", "Apple", "Tech")])
    members, _ = universe.load_index("Dow Jones 30", cache)
    assert members[0].ticker == "AAPL"


def test_demo_data_scans():
    data = market_data.demo_history(market_data.DEMO_TICKERS)
    hits = scan_universe(data, ScanConfig(lookback_days=5, tolerance_pct=1.5, min_price=0, min_avg_volume=0))
    assert hits  # random walks always brush some MA within 5 days


def test_settings_roundtrip(tmp_path):
    from settings_store import AppSettings, load_settings, save_settings
    s = AppSettings()
    s.scan.lookback_days = 7
    s.scan.mas[1] = MASpec(21, "HMA", False)
    s.theme = "Midnight Blue"
    s.scan.timeframe = "1wk"
    save_settings(tmp_path / "s.json", s)
    t = load_settings(tmp_path / "s.json")
    assert t.scan.lookback_days == 7 and t.scan.mas[1].ma_type == "HMA" and not t.scan.mas[1].enabled
    assert t.theme == "Midnight Blue" and t.scan.timeframe == "1wk"
    assert load_settings(tmp_path / "missing.json").scan.lookback_days == 3
