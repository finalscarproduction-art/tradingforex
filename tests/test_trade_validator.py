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
