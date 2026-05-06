"""
Unit tests for sr_detector — POI / S/R discovery.
(The user's "poi_detector" maps to this module: S/R levels are the engine's POI zones.)
"""
import pandas as pd
import pytest

from sr_detector import (
    detect_sr, nearest_levels, _cluster, _pip_size,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────
def _df(rows):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"])


@pytest.fixture
def trending_up_df():
    """Synthetic uptrend with two clean swing highs and one swing low."""
    rows = []
    for p in [1.0900, 1.0905, 1.0902]:
        rows.append([p, p + 0.0010, p - 0.0005, p + 0.0005])
    # Swing high #1 at idx 5
    rows.append([1.0908, 1.0950, 1.0905, 1.0945])
    rows.append([1.0945, 1.0948, 1.0930, 1.0935])
    rows.append([1.0935, 1.0938, 1.0925, 1.0930])
    # Swing low at idx 8
    rows.append([1.0930, 1.0935, 1.0900, 1.0905])
    rows.append([1.0905, 1.0910, 1.0902, 1.0908])
    rows.append([1.0908, 1.0912, 1.0905, 1.0910])
    rows.append([1.0910, 1.0915, 1.0908, 1.0913])
    rows.append([1.0913, 1.0918, 1.0911, 1.0916])
    # Swing high #2 at idx 14
    rows.append([1.0916, 1.0980, 1.0915, 1.0975])
    rows.append([1.0975, 1.0978, 1.0960, 1.0965])
    rows.append([1.0965, 1.0968, 1.0955, 1.0960])
    rows.append([1.0960, 1.0962, 1.0950, 1.0958])
    rows.append([1.0958, 1.0960, 1.0950, 1.0955])
    rows.append([1.0955, 1.0958, 1.0950, 1.0952])
    return _df(rows)


# ── _pip_size ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("pair,expected", [
    ("EUR/USD", 0.0001),
    ("GBP/USD", 0.0001),
    ("USD/JPY", 0.01),
    ("EUR/JPY", 0.01),
    ("BTC/USDT", 1.0),
    ("ETH/USDT", 1.0),
    ("XAU/USD", 0.0001),     # in this module XAU/USD uses default forex pip
])
def test_pip_size_known_pairs(pair, expected):
    assert _pip_size(pair) == expected


# ── detect_sr ────────────────────────────────────────────────────────────────
def test_detect_sr_returns_levels_with_required_fields(trending_up_df):
    levels = detect_sr(trending_up_df, current_price=1.0955,
                       pair="EUR/USD", lookback=2, filter_pips=300)
    assert isinstance(levels, list) and len(levels) > 0
    for lvl in levels:
        assert {"price", "type", "touches", "distance_pips", "side"} <= set(lvl.keys())
        assert lvl["type"] in {"support", "resistance", "round"}
        assert lvl["side"] in {"above", "below"}


def test_detect_sr_filters_by_proximity(trending_up_df):
    """Levels further than `filter_pips` from price must be excluded."""
    levels = detect_sr(trending_up_df, current_price=1.0955,
                       pair="EUR/USD", lookback=2, filter_pips=10)
    # Only levels within 10 pips of 1.0955 should appear
    for lvl in levels:
        assert lvl["distance_pips"] <= 10


def test_detect_sr_returns_empty_for_short_dataframe():
    df = _df([[1.0, 1.01, 0.99, 1.005]] * 3)
    assert detect_sr(df, 1.0, "EUR/USD", lookback=5) == []


def test_detect_sr_includes_round_numbers():
    df = _df([[1.0850, 1.0855, 1.0848, 1.0852]] * 30)
    levels = detect_sr(df, current_price=1.0850, pair="EUR/USD",
                       lookback=2, filter_pips=300)
    types_found = {lvl["type"] for lvl in levels}
    assert "round" in types_found


# ── nearest_levels ───────────────────────────────────────────────────────────
def test_nearest_levels_returns_closest_above_and_below():
    sr = [
        {"price": 1.10, "type": "resistance", "touches": 3,
         "distance_pips": 50, "side": "above"},
        {"price": 1.11, "type": "resistance", "touches": 2,
         "distance_pips": 100, "side": "above"},
        {"price": 1.08, "type": "support", "touches": 4,
         "distance_pips": 100, "side": "below"},
    ]
    nl = nearest_levels(sr, current_price=1.09)
    assert nl["nearest_resistance"]["price"] == 1.10
    assert nl["nearest_support"]["price"] == 1.08


def test_nearest_levels_returns_none_when_empty():
    nl = nearest_levels([], current_price=1.0)
    assert nl["nearest_resistance"] is None
    assert nl["nearest_support"] is None


def test_immediate_block_only_when_close_and_strong():
    sr = [
        {"price": 1.0905, "type": "resistance", "touches": 3,
         "distance_pips": 5, "side": "above"},   # close + strong
        {"price": 1.0950, "type": "resistance", "touches": 1,
         "distance_pips": 50, "side": "above"},
    ]
    nl = nearest_levels(sr, current_price=1.0900)
    assert nl["immediate_block"] is not None
    assert nl["immediate_block"]["touches"] >= 2


# ── Clustering ───────────────────────────────────────────────────────────────
def test_cluster_merges_close_levels():
    levels = [
        {"price": 1.10000, "type": "resistance", "touches": 1},
        {"price": 1.10004, "type": "resistance", "touches": 1},   # within 0.1%
        {"price": 1.10500, "type": "resistance", "touches": 1},   # not close
    ]
    out = _cluster(levels, price_ref=1.10, threshold_pct=0.001)
    assert len(out) == 2
    merged = next(l for l in out if abs(l["price"] - 1.10002) < 0.001)
    assert merged["touches"] == 2
