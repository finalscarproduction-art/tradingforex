"""FVG detector + scorer tests."""
import pandas as pd
import pytest

from fvg_detector import (
    detect_fvg, score_fvg, best_actionable, ACTIONABLE_THRESHOLD, FVG,
)


def _df(rows):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"])


# ── Detection ────────────────────────────────────────────────────────────
def test_bullish_fvg_detected():
    """candle[i-1].high < candle[i+1].low → bullish FVG."""
    df = _df([
        [1.0, 1.05, 0.95, 1.02],
        [1.02, 1.10, 1.01, 1.09],   # middle (displacement up)
        [1.09, 1.15, 1.08, 1.14],   # candle[i+1].low = 1.08 > candle[i-1].high 1.05 → gap
        [1.14, 1.16, 1.10, 1.12],
    ])
    fvgs = detect_fvg(df)
    assert any(f.direction == "bullish" for f in fvgs)
    bull = next(f for f in fvgs if f.direction == "bullish")
    assert bull.top    == 1.08
    assert bull.bottom == 1.05
    assert bull.mid    == pytest.approx(1.065)


def test_bearish_fvg_detected():
    """candle[i-1].low > candle[i+1].high → bearish FVG."""
    df = _df([
        [1.20, 1.22, 1.15, 1.16],
        [1.16, 1.17, 1.05, 1.06],   # displacement down
        [1.06, 1.09, 1.03, 1.04],   # candle[i+1].high 1.09 < candle[i-1].low 1.15
        [1.04, 1.07, 1.00, 1.05],
    ])
    fvgs = detect_fvg(df)
    bear = next(f for f in fvgs if f.direction == "bearish")
    assert bear.top    == 1.15
    assert bear.bottom == 1.09


def test_no_fvg_in_orderly_market():
    df = _df([[1.00, 1.01, 0.99, 1.005]] * 5)
    assert detect_fvg(df) == []


# ── Lifecycle: filled / mitigated ───────────────────────────────────────
def test_bullish_fvg_marked_mitigated_when_wick_re_enters():
    df = _df([
        [1.0, 1.05, 0.95, 1.02],
        [1.02, 1.10, 1.01, 1.09],
        [1.09, 1.15, 1.08, 1.14],
        [1.14, 1.16, 1.07, 1.12],   # wick at 1.07 dips into FVG (1.05–1.08)
    ])
    fvgs = detect_fvg(df)
    bull = next(f for f in fvgs if f.direction == "bullish")
    assert bull.mitigated is True
    assert bull.filled is False


def test_bullish_fvg_marked_filled_when_close_breaks_through():
    df = _df([
        [1.0, 1.05, 0.95, 1.02],
        [1.02, 1.10, 1.01, 1.09],
        [1.09, 1.15, 1.08, 1.14],
        [1.14, 1.16, 1.00, 1.02],   # close 1.02 < bottom 1.05 → filled
    ])
    fvgs = detect_fvg(df)
    bull = next(f for f in fvgs if f.direction == "bullish")
    assert bull.filled is True


# ── Quality scoring ─────────────────────────────────────────────────────
def test_fvg_score_full_stack_reaches_threshold():
    """All 5 components present → score should reach ACTIONABLE."""
    fvg = FVG(idx=10, direction="bullish", top=1.08, bottom=1.05, mid=1.065,
              created_at=11)
    score_fvg(
        fvg,
        htf_direction = "BULLISH",
        poi_zones     = [{"low": 1.05, "high": 1.08, "price": 1.065}],
        recent_sweep  = True,
        recent_bos    = True,
        ema_200       = 1.064,
        pip_size      = 0.0001,
    )
    assert fvg.score == 10
    assert fvg.score_breakdown["htf_aligned"]   is True
    assert fvg.score_breakdown["inside_poi"]    is True
    assert fvg.score_breakdown["after_sweep"]   is True
    assert fvg.score_breakdown["after_bos"]     is True
    assert fvg.score_breakdown["near_200ema"]   is True


def test_fvg_score_below_threshold_not_actionable():
    fvg = FVG(idx=10, direction="bullish", top=1.08, bottom=1.05, mid=1.065,
              created_at=11)
    score_fvg(fvg, htf_direction="BEARISH",   # misaligned
              poi_zones=[], recent_sweep=False, recent_bos=False)
    assert fvg.score == 0
    assert best_actionable([fvg]) is None


def test_best_actionable_picks_highest_score():
    f1 = FVG(idx=10, direction="bullish", top=1.08, bottom=1.05, mid=1.065,
             created_at=11)
    f2 = FVG(idx=20, direction="bullish", top=1.08, bottom=1.05, mid=1.065,
             created_at=21)
    f1.score, f2.score = 7, 9
    pick = best_actionable([f1, f2])
    assert pick.score == 9
