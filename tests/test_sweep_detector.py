"""Unit tests for sweep_detector — liquidity sweep detection."""
import pandas as pd
import pytest

from sweep_detector import (
    Sweep, find_recent_swings, detect_sweeps, latest_unmitigated_sweep,
    to_dict,
)


def _df(rows):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"])


# ── find_recent_swings ──────────────────────────────────────────────────────
def test_find_swings_picks_high_pivot():
    rows = [
        [1.10, 1.10, 1.099, 1.099],
        [1.099, 1.101, 1.098, 1.100],
        [1.100, 1.105, 1.099, 1.103],   # idx 2 — swing high (lookback=2)
        [1.103, 1.102, 1.099, 1.101],
        [1.101, 1.100, 1.098, 1.099],
    ]
    df = _df(rows)
    highs, lows = find_recent_swings(df, lookback=2)
    assert 2 in highs


def test_find_swings_picks_low_pivot():
    rows = [
        [1.10, 1.101, 1.099, 1.100],
        [1.100, 1.101, 1.098, 1.099],
        [1.099, 1.100, 1.090, 1.092],   # idx 2 — swing low
        [1.092, 1.095, 1.091, 1.094],
        [1.094, 1.097, 1.093, 1.096],
    ]
    df = _df(rows)
    highs, lows = find_recent_swings(df, lookback=2)
    assert 2 in lows


def test_find_swings_empty_when_dataframe_too_short():
    df = _df([[1, 1.01, 0.99, 1]] * 3)
    assert find_recent_swings(df, lookback=5) == ([], [])


# ── detect_sweeps ───────────────────────────────────────────────────────────
def test_bearish_sweep_detected_when_wick_takes_high_then_closes_below():
    """High wick takes out a prior swing high, body closes below = bearish sweep.

    detect_sweeps() early-returns [] unless BOTH a swing high AND a swing low
    exist in the data, so the fixture below contains:
      • a swing low at idx 3
      • a swing high at idx 8 (the level we'll sweep)
      • the sweep candle at idx 13
    """
    rows = [
        # decline into a low
        [1.108, 1.110, 1.104, 1.105],
        [1.105, 1.106, 1.100, 1.101],
        [1.101, 1.102, 1.097, 1.098],
        # idx 3 — swing low 1.094
        [1.098, 1.099, 1.094, 1.096],
        # rally
        [1.096, 1.100, 1.095, 1.099],
        [1.099, 1.105, 1.098, 1.103],
        [1.103, 1.111, 1.102, 1.110],
        [1.110, 1.115, 1.109, 1.113],
        # idx 8 — swing high 1.120 (the level the sweep takes out)
        [1.113, 1.120, 1.112, 1.116],
        # decline confirms the swing high
        [1.116, 1.117, 1.111, 1.112],
        [1.112, 1.114, 1.108, 1.109],
        [1.109, 1.111, 1.105, 1.106],
        [1.106, 1.108, 1.103, 1.104],
        # idx 13 — SWEEP: wicks above 1.120, closes back below
        [1.104, 1.124, 1.103, 1.111],
        # continuation down
        [1.111, 1.112, 1.107, 1.108],
    ]
    df = _df(rows)
    sweeps = detect_sweeps(df, lookback_swings=5, swing_lookback=2)
    bearish = [s for s in sweeps if s.direction == "bearish"]
    assert any(s.idx == 13 and s.wick_extreme == 1.124 for s in bearish), \
        f"Expected bearish sweep at idx 13, got: {[(s.idx, s.wick_extreme) for s in sweeps]}"


def test_bullish_sweep_detected_when_wick_takes_low_then_closes_above():
    """Mirror of the bearish test — fixture has both a swing high (idx 3)
    and a swing low (idx 8). The sweep candle at idx 13 wicks below the low."""
    rows = [
        # rally into a high
        [1.090, 1.094, 1.089, 1.093],
        [1.093, 1.097, 1.092, 1.096],
        [1.096, 1.100, 1.095, 1.099],
        # idx 3 — swing high 1.105 (just to satisfy the both-exist guard)
        [1.099, 1.105, 1.098, 1.102],
        # decline
        [1.102, 1.103, 1.097, 1.098],
        [1.098, 1.099, 1.093, 1.094],
        [1.094, 1.095, 1.089, 1.090],
        [1.090, 1.091, 1.086, 1.087],
        # idx 8 — swing low 1.082 (the level the sweep takes out)
        [1.087, 1.088, 1.082, 1.085],
        # rally confirms the swing low
        [1.085, 1.090, 1.084, 1.089],
        [1.089, 1.094, 1.088, 1.093],
        [1.093, 1.097, 1.092, 1.096],
        [1.096, 1.100, 1.095, 1.099],
        # idx 13 — SWEEP: wicks below 1.082, closes back above
        [1.099, 1.100, 1.078, 1.092],
        # continuation up
        [1.092, 1.096, 1.091, 1.095],
    ]
    df = _df(rows)
    sweeps = detect_sweeps(df, lookback_swings=5, swing_lookback=2)
    bullish = [s for s in sweeps if s.direction == "bullish"]
    assert any(s.idx == 13 and s.wick_extreme == 1.078 for s in bullish), \
        f"Expected bullish sweep at idx 13, got: {[(s.idx, s.wick_extreme) for s in sweeps]}"


def test_no_sweep_when_close_holds_outside_swing():
    """If a candle CLOSES through the swing level, that's a break, not a sweep."""
    rows = [
        [1.100, 1.110, 1.099, 1.108],
        [1.108, 1.115, 1.107, 1.110],   # swing high 1.115
        [1.110, 1.111, 1.106, 1.109],
        [1.109, 1.112, 1.107, 1.110],
        [1.110, 1.115, 1.108, 1.111],
        [1.111, 1.118, 1.110, 1.117],   # closes ABOVE 1.115 — no sweep
    ]
    df = _df(rows)
    sweeps = detect_sweeps(df, lookback_swings=5, swing_lookback=2)
    bearish_at_5 = [s for s in sweeps if s.direction == "bearish" and s.idx == 5]
    assert bearish_at_5 == []


# ── latest_unmitigated_sweep ────────────────────────────────────────────────
def test_latest_unmitigated_returns_most_recent():
    df = _df([[1, 1.01, 0.99, 1]] * 30)
    s1 = Sweep(idx=10, direction="bearish", swept_level=1.10,
               wick_extreme=1.115, close=1.105)
    s2 = Sweep(idx=20, direction="bearish", swept_level=1.10,
               wick_extreme=1.118, close=1.108)
    pick = latest_unmitigated_sweep([s1, s2], df, max_age_bars=20)
    assert pick.idx == 20


def test_latest_unmitigated_excludes_when_wick_exceeded():
    """If post-sweep bars print HIGHER than the wick extreme, sweep is invalidated."""
    rows = [[1.10, 1.11, 1.099, 1.105]] * 25
    rows[24] = [1.105, 1.130, 1.105, 1.128]   # post-sweep candle blew through
    df = _df(rows)
    s = Sweep(idx=20, direction="bearish", swept_level=1.10,
              wick_extreme=1.115, close=1.108)
    pick = latest_unmitigated_sweep([s], df, max_age_bars=10)
    assert pick is None


def test_latest_unmitigated_returns_none_for_empty_list():
    df = _df([[1, 1.01, 0.99, 1]] * 5)
    assert latest_unmitigated_sweep([], df) is None


# ── to_dict serialisation ───────────────────────────────────────────────────
def test_sweep_to_dict_round_trip():
    s = Sweep(idx=5, direction="bullish", swept_level=1.0,
              wick_extreme=0.99, close=1.005)
    d = to_dict(s)
    assert d["idx"] == 5 and d["direction"] == "bullish"
    assert d["swept_level"] == 1.0
