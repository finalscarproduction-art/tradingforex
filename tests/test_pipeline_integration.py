"""Pipeline integration tests covering the 7 user-specified scenarios.

Uses pipeline_helper.run_pipeline (mirrors main.py's gate flow exactly)
with all side effects mocked so each test asserts WHICH callbacks fired.
"""
from datetime import datetime, timezone, timedelta

from pipeline_helper import run_pipeline
from cooldown_guard   import record_signal_fired, record_structure_event
from trade_validator  import OpenPosition


# ─────────────────────────────────────────────────────────────────────────────
# 1 · record_structure_event() ALWAYS runs after SMC detection
# ─────────────────────────────────────────────────────────────────────────────
def test_record_structure_event_fires_when_smc_event_present(
    usdchf_long, bullish_bias, fake_side_effects,
):
    run_pipeline(usdchf_long, bullish_bias, [], [], fake_side_effects)

    fake_side_effects["record_structure_event"].assert_called_once_with(
        "USD/CHF", "BOS_BULL", 295,
    )


def test_record_structure_event_runs_even_when_blocked(
    usdchf_long, bullish_bias, fake_side_effects,
):
    """Cache must update even on a blocked signal — structure tracking is
    always-on regardless of whether the trade fires."""
    record_signal_fired("USD/CHF", "LONG", "USDCHF_191_0.7887_LONG")

    run_pipeline(usdchf_long, bullish_bias, [], [], fake_side_effects)

    fake_side_effects["record_structure_event"].assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# 2 · BLOCKED_SIGNAL → write_blocked + log_signal called, NOTHING else
# ─────────────────────────────────────────────────────────────────────────────
def test_cooldown_block_triggers_write_blocked_and_log_only(
    usdchf_long, bullish_bias, fake_side_effects,
):
    # Pre-fire so the next call is a duplicate
    record_signal_fired("USD/CHF", "LONG", "USDCHF_191_0.7887_LONG")

    result = run_pipeline(usdchf_long, bullish_bias, [], [], fake_side_effects)

    assert result == "BLOCKED_COOLDOWN"

    # Required calls
    fake_side_effects["write_blocked"].assert_called_once()
    fake_side_effects["log_signal"].assert_called_once()

    args, _ = fake_side_effects["log_signal"].call_args
    assert args[1] == "BLOCKED_SIGNAL"
    assert args[2] is False

    # NONE of these may have fired
    fake_side_effects["send_telegram"].assert_not_called()
    fake_side_effects["send_whatsapp"].assert_not_called()
    fake_side_effects["push_chart"].assert_not_called()
    fake_side_effects["execute_mt5"].assert_not_called()
    fake_side_effects["record_signal_fired"].assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 3 · Cooldown passes but validation fails → no alerts, no MT5, rejection logged
# ─────────────────────────────────────────────────────────────────────────────
def test_validator_rejection_logs_but_no_side_effects(
    usdchf_long, bullish_bias, fake_side_effects,
):
    # Make the signal MEDIUM so the validator rejects it
    bad = {**usdchf_long, "confidence": "MEDIUM"}
    result = run_pipeline(bad, bullish_bias, [], [], fake_side_effects)

    assert result == "BLOCKED_VALIDATION"

    # Rejection must be logged
    fake_side_effects["log_signal"].assert_called_once()
    args, _ = fake_side_effects["log_signal"].call_args
    assert args[1] == "BLOCKED_SIGNAL"
    assert "rejection_code" in args[0]
    assert args[0]["rejection_code"] == "CONFIDENCE_TOO_LOW"

    # NO alerts, NO MT5, NO blocked-audit (that's only for cooldown blocks)
    fake_side_effects["send_telegram"].assert_not_called()
    fake_side_effects["send_whatsapp"].assert_not_called()
    fake_side_effects["push_chart"].assert_not_called()
    fake_side_effects["execute_mt5"].assert_not_called()
    fake_side_effects["record_signal_fired"].assert_not_called()
    fake_side_effects["write_blocked"].assert_not_called()


def test_htf_bias_conflict_blocks_with_no_side_effects(
    usdchf_long, bearish_bias, fake_side_effects,
):
    """LONG against bearish daily bias must fail validation."""
    result = run_pipeline(usdchf_long, bearish_bias, [], [], fake_side_effects)

    assert result == "BLOCKED_VALIDATION"
    fake_side_effects["send_telegram"].assert_not_called()
    fake_side_effects["execute_mt5"].assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 4 · Both gates pass → all four channels fire, record_signal_fired is called
# ─────────────────────────────────────────────────────────────────────────────
def test_full_approval_fires_all_side_effects(
    usdchf_long, bullish_bias, fake_side_effects,
):
    result = run_pipeline(usdchf_long, bullish_bias, [], [], fake_side_effects)

    assert result == "EXECUTED"

    # All four delivery channels fire
    fake_side_effects["send_telegram"].assert_called_once()
    fake_side_effects["send_whatsapp"].assert_called_once()
    fake_side_effects["push_chart"].assert_called_once()
    fake_side_effects["execute_mt5"].assert_called_once()

    # Signal recorded for future dedup
    fake_side_effects["record_signal_fired"].assert_called_once()
    args, _ = fake_side_effects["record_signal_fired"].call_args
    assert args[0] == "USD/CHF"
    assert args[1] == "LONG"

    # Last log_signal call should be SENT
    final_log = fake_side_effects["log_signal"].call_args
    assert final_log.args[1] == "SENT"
    assert final_log.args[2] is True

    # Cooldown audit file is NOT touched on a passing signal
    fake_side_effects["write_blocked"].assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 5 · Duplicate USD/CHF LONG within 4h is blocked
# ─────────────────────────────────────────────────────────────────────────────
def test_duplicate_usdchf_long_blocks_full_pipeline(
    usdchf_long, bullish_bias, fake_side_effects, mocker,
):
    # First signal goes through cleanly
    run_pipeline(usdchf_long, bullish_bias, [], [], fake_side_effects)
    assert fake_side_effects["execute_mt5"].call_count == 1

    # The mock didn't actually persist the signal — call the real recorder
    # to simulate what main.py does after a SENT signal.
    record_signal_fired("USD/CHF", "LONG", "USDCHF_191_0.7887_LONG")

    # Fresh mock counters for the second cycle
    fake2 = {k: mocker.MagicMock(return_value=True) for k in fake_side_effects}

    result = run_pipeline(usdchf_long, bullish_bias, [], [], fake2)

    assert result == "BLOCKED_COOLDOWN"
    fake2["execute_mt5"].assert_not_called()
    fake2["send_telegram"].assert_not_called()
    fake2["send_whatsapp"].assert_not_called()
    fake2["push_chart"].assert_not_called()
    fake2["record_signal_fired"].assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 6 · Same-direction open position blocks new trade
# ─────────────────────────────────────────────────────────────────────────────
def test_same_direction_open_position_blocks(
    usdchf_long, bullish_bias, fake_side_effects,
):
    open_pos = [OpenPosition(symbol="USD/CHF", direction="LONG", ticket=12345)]

    result = run_pipeline(usdchf_long, bullish_bias, open_pos, [], fake_side_effects)

    assert result == "BLOCKED_COOLDOWN"

    log_call = fake_side_effects["log_signal"].call_args
    payload  = log_call.args[0]
    assert payload["cooldown_code"] == "POSITION_ALREADY_OPEN"
    assert payload["last_match"]["direction"] == "LONG"

    fake_side_effects["send_telegram"].assert_not_called()
    fake_side_effects["execute_mt5"].assert_not_called()
    fake_side_effects["record_signal_fired"].assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 7 · Opposite direction blocked unless CHoCH exists
# ─────────────────────────────────────────────────────────────────────────────
def test_opposite_direction_blocked_without_choch(
    bullish_bias, fake_side_effects,
):
    pos_open = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    open_pos = [OpenPosition(symbol="USD/CHF", direction="LONG", ticket=1234)]

    short_signal = {
        "asset": "USD/CHF", "direction": "SHORT",
        "entry": 0.78900, "sl_price": 0.79100,
        "tp1": 0.78600, "tp2": 0.78400, "tp3": 0.78200,
        "confidence": "HIGH", "atr_4h": 0.0030, "session": "London",
        "matched_ob": {"idx_end": 250, "low": 0.78920, "high": 0.78940,
                       "direction": "bearish"},
        "smc_4h_event": {"type": "BOS_BEAR", "idx": 300, "price": 0.78900},
    }
    bearish_bias_for_test = type(bullish_bias)(
        symbol="USD/CHF", direction="BEAR", blocks_long=True,
    )

    # NO CHoCH recorded → opposite SHORT must block
    result = run_pipeline(short_signal, bearish_bias_for_test, open_pos, [], fake_side_effects)
    assert result == "BLOCKED_COOLDOWN"

    log_call = fake_side_effects["log_signal"].call_args
    payload  = log_call.args[0]
    assert payload["cooldown_code"] == "OPPOSITE_NEEDS_CHOCH"

    fake_side_effects["execute_mt5"].assert_not_called()


def test_opposite_direction_allowed_after_choch(
    bullish_bias, fake_side_effects,
):
    """Once a CHoCH prints, the opposite signal can fire."""
    pos_open = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    open_pos = [OpenPosition(symbol="USD/CHF", direction="LONG", ticket=1234)]

    record_structure_event("USD/CHF", "CHOCH_BEAR", 310)

    short_signal = {
        "asset": "USD/CHF", "direction": "SHORT",
        "entry": 0.78900, "sl_price": 0.79100,
        "tp1": 0.78600, "tp2": 0.78400, "tp3": 0.78200,
        "confidence": "HIGH", "atr_4h": 0.0030, "session": "London",
        "matched_ob": {"idx_end": 310, "low": 0.78920, "high": 0.78940,
                       "direction": "bearish"},
        "smc_4h_event": {"type": "CHOCH_BEAR", "idx": 310, "price": 0.78900},
    }
    bear_bias = type(bullish_bias)(
        symbol="USD/CHF", direction="BEAR", blocks_long=True,
    )

    result = run_pipeline(short_signal, bear_bias, open_pos, [], fake_side_effects)

    assert result == "EXECUTED"
    fake_side_effects["send_telegram"].assert_called_once()
    fake_side_effects["execute_mt5"].assert_called_once()
