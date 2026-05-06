"""
Test-only pipeline harness.

Replicates the EXACT gate sequence from main.py — cooldown → validator →
side-effects — but with every side effect injected as a callable so tests
can mock them. Behaviour must mirror main.py:

  1. record_structure_event() ALWAYS runs after SMC detection
  2. cooldown gate fires FIRST
  3. validator gate fires only on PASSED_COOLDOWN
  4. alerts + MT5 fire only on validator approval
  5. record_signal_fired() runs only after a SENT signal
"""
from datetime import datetime, timezone

from cooldown_guard import (
    evaluate_cooldown, decision_to_payload, OpenPositionRef,
)
from trade_validator import (
    SetupCandidate, validate, is_approved,
)


def _build_structure_id(signal):
    sym = signal["asset"].replace("/", "")
    direction = signal["direction"]
    ob = signal.get("matched_ob") or {}
    anchor = (
        f"{ob.get('idx_end', 'x')}_{round(ob.get('low', 0), 5)}"
        if ob else signal.get("entry", "noob")
    )
    return f"{sym}_{anchor}_{direction}"


def _setup_from_signal(signal, ts=None):
    return SetupCandidate(
        setup_id   = _build_structure_id(signal),
        symbol     = signal["asset"],
        direction  = signal["direction"],
        entry      = signal["entry"],
        sl         = signal["sl_price"],
        tp1        = signal["tp1"],
        tp2        = signal["tp2"],
        tp3        = signal["tp3"],
        confidence = signal.get("confidence", "MEDIUM"),
        atr_4h     = signal.get("atr_4h", 0.001),
        timestamp  = ts or datetime.now(timezone.utc),
    )


def run_pipeline(signal, htf_bias, open_positions, recent_signals, side_effects):
    """
    Mirror of main.py's gate flow with injected side effects.

    Returns one of:
      "BLOCKED_COOLDOWN" / "BLOCKED_VALIDATION" / "EXECUTED"
    """
    # 1. ALWAYS update structure cache first
    if signal.get("smc_4h_event"):
        ev = signal["smc_4h_event"]
        side_effects["record_structure_event"](
            signal["asset"], ev.get("type", ""), int(ev.get("idx", 0)),
        )

    structure_id = _build_structure_id(signal)
    cooldown_pos = [
        OpenPositionRef(symbol=p.symbol, direction=p.direction,
                        opened_at=getattr(p, "opened_at", None))
        for p in open_positions
    ]

    # 2. Cooldown gate (BEFORE validation)
    decision = evaluate_cooldown(
        signal["asset"], signal["direction"], structure_id, cooldown_pos,
    )
    response = decision_to_payload(decision)

    if response["status"] == "BLOCKED_SIGNAL":
        side_effects["write_blocked"](signal, decision)
        side_effects["log_signal"]({**signal, **response}, "BLOCKED_SIGNAL", False)
        return "BLOCKED_COOLDOWN"

    # 3. Validation gate (only if cooldown passed)
    setup = _setup_from_signal(signal)
    val_result = validate(setup, htf_bias, open_positions, recent_signals)

    if not is_approved(val_result):
        payload = {
            **signal,
            "rejection_code":    val_result.code.value,
            "rejection_message": val_result.message,
        }
        side_effects["log_signal"](payload, "BLOCKED_SIGNAL", False)
        return "BLOCKED_VALIDATION"

    # 4. APPROVED — every side effect fires below this line
    side_effects["push_chart"](signal)
    side_effects["send_whatsapp"](signal)
    side_effects["send_telegram"](signal)
    side_effects["execute_mt5"](signal)
    side_effects["record_signal_fired"](
        signal["asset"], signal["direction"], structure_id,
    )
    side_effects["log_signal"](signal, "SENT", True)
    return "EXECUTED"
