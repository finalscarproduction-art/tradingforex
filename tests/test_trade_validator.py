"""Trade validator — direct functional tests."""
import pytest
from datetime import timedelta

from trade_validator import (
    SetupCandidate, HTFBias, OpenPosition, RecentSignal,
    ValidatedTrade, RejectionReason, RejectionCode,
    validate, is_approved,
)


def test_all_gates_pass_returns_validated_trade(setup_candidate, bullish_bias):
    result = validate(setup_candidate, bullish_bias, [], [])
    assert isinstance(result, ValidatedTrade)
    assert is_approved(result)
    assert result.confidence == "HIGH"
    assert result.rr1 == 1.5  # (0.79200-0.78900)/(0.78900-0.78700) = 1.5
    assert "htf_bias_aligned" in result.reasons_passed


def test_medium_confidence_rejected(setup_candidate, bullish_bias):
    s = SetupCandidate(**{**setup_candidate.__dict__, "confidence": "MEDIUM"})
    result = validate(s, bullish_bias, [], [])
    assert not is_approved(result)
    assert result.code == RejectionCode.CONFIDENCE_TOO_LOW


def test_rr_below_1_5_rejected(setup_candidate, bullish_bias):
    # tp1 only 0.5R away from entry
    bad_tp1 = setup_candidate.entry + (setup_candidate.entry - setup_candidate.sl) * 0.5
    s = SetupCandidate(**{**setup_candidate.__dict__, "tp1": bad_tp1})
    result = validate(s, bullish_bias, [], [])
    assert not is_approved(result)
    assert result.code == RejectionCode.RR_TOO_LOW


def test_sl_below_0_3_atr_rejected(setup_candidate, bullish_bias):
    # SL only 5 pips, ATR is 30 pips → ratio 0.17 < 0.3
    s = SetupCandidate(**{**setup_candidate.__dict__,
                          "sl": setup_candidate.entry - 0.00005,
                          "tp1": setup_candidate.entry + 0.00010})
    result = validate(s, bullish_bias, [], [])
    assert not is_approved(result)
    assert result.code == RejectionCode.SL_TOO_TIGHT


def test_duplicate_signal_within_4h_rejected(setup_candidate, bullish_bias):
    prior = RecentSignal(
        setup_id=setup_candidate.setup_id,
        symbol=setup_candidate.symbol,
        direction=setup_candidate.direction,
        timestamp=setup_candidate.timestamp - timedelta(hours=2),
    )
    result = validate(setup_candidate, bullish_bias, [], [prior])
    assert not is_approved(result)
    assert result.code == RejectionCode.DUPLICATE_SIGNAL


def test_open_position_same_direction_rejected(setup_candidate, bullish_bias):
    pos = OpenPosition(symbol=setup_candidate.symbol,
                       direction=setup_candidate.direction, ticket=99999)
    result = validate(setup_candidate, bullish_bias, [pos], [])
    assert not is_approved(result)
    assert result.code == RejectionCode.POSITION_ALREADY_OPEN


def test_htf_bias_conflict_rejected(setup_candidate, bearish_bias):
    result = validate(setup_candidate, bearish_bias, [], [])
    assert not is_approved(result)
    assert result.code == RejectionCode.HTF_BIAS_CONFLICT


def test_neutral_bias_rejects_all_directions(setup_candidate):
    neutral = HTFBias(symbol="USD/CHF", direction="NEUTRAL",
                     blocks_long=True, blocks_short=True)
    result = validate(setup_candidate, neutral, [], [])
    assert not is_approved(result)
    assert result.code == RejectionCode.HTF_BIAS_CONFLICT


def test_invalid_input_negative_entry_rejected(setup_candidate, bullish_bias):
    s = SetupCandidate(**{**setup_candidate.__dict__, "entry": -1})
    result = validate(s, bullish_bias, [], [])
    assert not is_approved(result)
    assert result.code == RejectionCode.INVALID_INPUT


def test_invalid_input_sl_above_entry_for_long(setup_candidate, bullish_bias):
    s = SetupCandidate(**{**setup_candidate.__dict__,
                          "sl": setup_candidate.entry + 0.001})
    result = validate(s, bullish_bias, [], [])
    assert not is_approved(result)
    assert result.code == RejectionCode.INVALID_INPUT


# ── TP distinctness (Tier-1 gap fix) ────────────────────────────────────────
def test_clustered_tps_all_equal_rejected(setup_candidate, bullish_bias):
    """TP1 == TP2 == TP3 must be rejected with TP_CLUSTERED."""
    same = setup_candidate.entry + 0.0030
    s = SetupCandidate(**{**setup_candidate.__dict__,
                          "tp1": same, "tp2": same, "tp3": same})
    result = validate(s, bullish_bias, [], [])
    assert not is_approved(result)
    assert result.code == RejectionCode.TP_CLUSTERED
    assert "TP1 == TP2" in result.message


def test_clustered_tp1_equals_tp2_rejected(setup_candidate, bullish_bias):
    """Only TP1 == TP2 — must still be rejected."""
    s = SetupCandidate(**{**setup_candidate.__dict__,
                          "tp1": 0.79200, "tp2": 0.79200, "tp3": 0.79600})
    result = validate(s, bullish_bias, [], [])
    assert not is_approved(result)
    assert result.code == RejectionCode.TP_CLUSTERED


def test_tp_gap_below_min_spacing_rejected(setup_candidate, bullish_bias):
    """
    TPs distinct but TP1↔TP2 gap < 0.5 × first reward unit.
    First reward = 0.79200 - 0.78900 = 0.00300; min gap = 0.00150.
    Set TP2 only 0.00100 above TP1 → reject.
    """
    s = SetupCandidate(**{**setup_candidate.__dict__,
                          "tp1": 0.79200, "tp2": 0.79300, "tp3": 0.79800})
    result = validate(s, bullish_bias, [], [])
    assert not is_approved(result)
    assert result.code == RejectionCode.TP_CLUSTERED
    assert "gap" in result.message.lower()


def test_distinct_tps_with_proper_spacing_pass(setup_candidate, bullish_bias):
    """Default fixture (TP1=0.792, TP2=0.794, TP3=0.796) — spacing 0.002 ≥ 0.0015 — pass."""
    result = validate(setup_candidate, bullish_bias, [], [])
    assert is_approved(result)
    assert "tp_distinct" in result.reasons_passed


# ── TP direction-geometry gate (Issue A defensive fix) ──────────────────────
def test_short_with_tp1_above_entry_rejected(setup_candidate, bullish_bias):
    """Reproduces today's USD/CHF SHORT bug: TP1 above entry must be rejected."""
    bear_bias = HTFBias(symbol="USD/CHF", direction="BEAR")
    s = SetupCandidate(
        setup_id="USDCHF_TEST", symbol="USD/CHF", direction="SHORT",
        entry=0.77759, sl=0.77879,
        tp1=0.777857,                # ← TP above entry — BUG
        tp2=0.77600, tp3=0.77500,
        confidence="HIGH", atr_4h=0.0030,
        timestamp=setup_candidate.timestamp,
    )
    result = validate(s, bear_bias, [], [])
    assert not is_approved(result)
    assert result.code == RejectionCode.INVALID_INPUT
    # Message says "TP1 (X) must be BELOW entry" for SHORT
    assert "below entry" in result.message.lower()
    assert "tp1" in result.message.lower()


def test_short_with_tp2_above_entry_rejected(setup_candidate, bullish_bias):
    bear_bias = HTFBias(symbol="USD/CHF", direction="BEAR")
    s = SetupCandidate(
        setup_id="USDCHF_TEST", symbol="USD/CHF", direction="SHORT",
        entry=0.77759, sl=0.77879,
        tp1=0.77600, tp2=0.78000, tp3=0.77400,   # TP2 above entry
        confidence="HIGH", atr_4h=0.0030,
        timestamp=setup_candidate.timestamp,
    )
    result = validate(s, bear_bias, [], [])
    assert not is_approved(result)
    assert result.code == RejectionCode.INVALID_INPUT


def test_long_with_tp1_below_entry_rejected(setup_candidate, bullish_bias):
    """Mirror: LONG with TP below entry must be rejected."""
    s = SetupCandidate(
        setup_id="EURUSD_TEST", symbol="USD/CHF", direction="LONG",
        entry=1.0900, sl=1.0880,
        tp1=1.0850,                  # ← TP below entry — bug
        tp2=1.0930, tp3=1.0950,
        confidence="HIGH", atr_4h=0.0030,
        timestamp=setup_candidate.timestamp,
    )
    result = validate(s, bullish_bias, [], [])
    assert not is_approved(result)
    assert result.code == RejectionCode.INVALID_INPUT
    # LONG message: "TP1 (X) must be ABOVE entry"
    assert "above entry" in result.message.lower()
    assert "tp1" in result.message.lower()


def test_long_with_tp3_below_entry_rejected(setup_candidate, bullish_bias):
    s = SetupCandidate(
        setup_id="EURUSD_TEST", symbol="USD/CHF", direction="LONG",
        entry=1.0900, sl=1.0880,
        tp1=1.0930, tp2=1.0940, tp3=1.0890,   # TP3 below entry
        confidence="HIGH", atr_4h=0.0030,
        timestamp=setup_candidate.timestamp,
    )
    result = validate(s, bullish_bias, [], [])
    assert not is_approved(result)
    assert result.code == RejectionCode.INVALID_INPUT


# ── Regression: real-world production bug 2026-05-07 ───────────────────────
def test_real_world_short_with_tp_above_entry_blocked(setup_candidate):
    """Regression test for 2026-05-07 USD/CHF SHORT bug.

    On 2026-05-07 09:22 UTC the engine generated a SHORT signal with
    tp1 ABOVE entry due to S/R buffer overshoot in _adjust_tp.
    The validator's input-sanity gate must catch this.
    """
    bear = HTFBias(symbol="USD/CHF", direction="BEAR")
    s = SetupCandidate(
        setup_id="USDCHF_REGRESSION_20260507",
        symbol="USD/CHF", direction="SHORT",
        entry=0.77759, sl=0.77879,
        tp1=0.777857,        # bug: above entry on a SHORT
        tp2=0.77, tp3=0.769,
        confidence="HIGH", atr_4h=0.0030,
        timestamp=setup_candidate.timestamp,
    )
    result = validate(s, bear, [], [])
    assert not is_approved(result)                      # rejected
    assert result.code == RejectionCode.INVALID_INPUT
    assert "TP1" in result.message
    assert "BELOW entry" in result.message


# ── RR threshold equality (Issue B fix) ─────────────────────────────────────
def test_rr_exactly_equal_to_min_passes(setup_candidate, bullish_bias):
    """A signal whose RR1 lands exactly at the 1.5 floor must PASS, not be
    rejected by float-noise from a strict-less-than comparison."""
    # entry=0.789, sl=0.787 → risk=0.002. To get rr=1.5 exactly: tp1 = entry+0.003
    s = SetupCandidate(
        setup_id="EURUSD_BOUNDARY", symbol="USD/CHF", direction="LONG",
        entry=0.78900, sl=0.78700,
        tp1=0.79200,                          # exactly 1.5R
        tp2=0.79400, tp3=0.79600,
        confidence="HIGH", atr_4h=0.0030,
        timestamp=setup_candidate.timestamp,
    )
    result = validate(s, bullish_bias, [], [])
    assert is_approved(result), \
        f"Expected approval at RR=1.5, got {result.code if not is_approved(result) else 'OK'}"
    assert result.rr1 == 1.5
