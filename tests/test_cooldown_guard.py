"""Cooldown guard — direct functional tests."""
from datetime import datetime, timezone, timedelta

from cooldown_guard import (
    evaluate_cooldown, decision_to_payload, write_blocked_signal,
    record_signal_fired, record_structure_event,
    OpenPositionRef,
)


# ── Response shape contracts ────────────────────────────────────────────────
def test_passed_cooldown_response_shape():
    d = evaluate_cooldown("EUR/USD", "LONG", "EURUSD_OB_1_LONG", [])
    payload = decision_to_payload(d)
    assert payload == {
        "status":          "PASSED_COOLDOWN",
        "cooldown_code":   None,
        "cooldown_reason": None,
        "age_minutes":     None,
        "last_match":      None,
    }


def test_blocked_signal_response_shape_has_all_required_keys():
    record_signal_fired("EUR/USD", "LONG", "EURUSD_OB_1_LONG")
    d = evaluate_cooldown("EUR/USD", "LONG", "EURUSD_OB_1_LONG", [])
    payload = decision_to_payload(d)
    assert set(payload.keys()) == {
        "status", "cooldown_code", "cooldown_reason", "age_minutes", "last_match"
    }
    assert payload["status"] == "BLOCKED_SIGNAL"
    assert payload["cooldown_code"] == "DUPLICATE_SIGNAL"
    assert isinstance(payload["age_minutes"], float)


# ── Test #5 · Duplicate USD/CHF LONG ────────────────────────────────────────
def test_duplicate_usdchf_long_is_blocked():
    sid = "USDCHF_OB_191_LONG"
    record_signal_fired("USD/CHF", "LONG", sid)

    d = evaluate_cooldown("USD/CHF", "LONG", sid, [])
    assert not d.allowed
    assert d.code == "DUPLICATE_SIGNAL"
    assert d.last_match["structure_id"] == sid
    assert d.age_minutes < 1   # just fired


def test_duplicate_outside_4h_window_is_allowed(monkeypatch):
    sid = "USDCHF_OB_191_LONG"
    # Fire a signal "5h ago"
    old = datetime.now(timezone.utc) - timedelta(hours=5)
    record_signal_fired("USD/CHF", "LONG", sid, timestamp=old)

    d = evaluate_cooldown("USD/CHF", "LONG", sid, [])
    assert d.allowed
    assert d.code == "ALLOWED"


def test_duplicate_reset_by_new_bos():
    sid = "USDCHF_OB_191_LONG"
    record_signal_fired("USD/CHF", "LONG", sid)
    record_structure_event("USD/CHF", "BOS_BULL", 300)

    d = evaluate_cooldown("USD/CHF", "LONG", sid, [])
    assert d.allowed
    assert "structure" in d.reason.lower()


# ── Test #6 · Same-direction position blocks ────────────────────────────────
def test_position_already_open_same_direction_blocks():
    open_pos = [OpenPositionRef(symbol="USD/CHF", direction="LONG")]
    d = evaluate_cooldown("USD/CHF", "LONG", "USDCHF_NEW_OB", open_pos)

    assert not d.allowed
    assert d.code == "POSITION_ALREADY_OPEN"
    assert d.last_match["symbol"] == "USD/CHF"
    assert d.last_match["direction"] == "LONG"


def test_position_open_does_not_affect_other_symbols():
    open_pos = [OpenPositionRef(symbol="USD/CHF", direction="LONG")]
    d = evaluate_cooldown("EUR/USD", "LONG", "EURUSD_OB_1_LONG", open_pos)
    assert d.allowed


# ── Test #7 · Opposite direction needs CHoCH ────────────────────────────────
def test_opposite_direction_blocked_without_choch():
    pos_open = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    open_pos = [OpenPositionRef(symbol="USD/CHF", direction="LONG", opened_at=pos_open)]
    d = evaluate_cooldown("USD/CHF", "SHORT", "USDCHF_OB_201_SHORT", open_pos)

    assert not d.allowed
    assert d.code == "OPPOSITE_NEEDS_CHOCH"
    assert d.last_match["direction"] == "LONG"


def test_opposite_direction_allowed_after_choch():
    pos_open = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    open_pos = [OpenPositionRef(symbol="USD/CHF", direction="LONG", opened_at=pos_open)]

    record_structure_event("USD/CHF", "CHOCH_BEAR", 250)

    d = evaluate_cooldown("USD/CHF", "SHORT", "USDCHF_OB_201_SHORT", open_pos)
    assert d.allowed


def test_opposite_direction_blocked_when_event_is_bos_not_choch():
    """Only CHoCH (reversal) unblocks opposite — BOS (continuation) does not."""
    pos_open = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    open_pos = [OpenPositionRef(symbol="USD/CHF", direction="LONG", opened_at=pos_open)]

    record_structure_event("USD/CHF", "BOS_BULL", 250)

    d = evaluate_cooldown("USD/CHF", "SHORT", "USDCHF_OB_201_SHORT", open_pos)
    assert not d.allowed
    assert d.code == "OPPOSITE_NEEDS_CHOCH"


# ── write_blocked_signal persistence ────────────────────────────────────────
def test_write_blocked_signal_creates_audit_file(isolated_state):
    record_signal_fired("USD/CHF", "LONG", "USDCHF_OB_191_LONG")
    d = evaluate_cooldown("USD/CHF", "LONG", "USDCHF_OB_191_LONG", [])
    assert not d.allowed

    signal = {"asset": "USD/CHF", "direction": "LONG", "entry": 0.789,
              "sl_price": 0.787, "tp1": 0.792, "tp2": 0.794, "tp3": 0.796,
              "confidence": "HIGH", "session": "London"}
    write_blocked_signal(signal, d)

    import json
    records = json.loads(isolated_state["blocked"].read_text(encoding="utf-8"))
    assert len(records) == 1
    assert records[0]["status"]        == "BLOCKED_SIGNAL"
    assert records[0]["cooldown_code"] == "DUPLICATE_SIGNAL"
    assert "signal_snapshot" in records[0]


def test_write_blocked_signal_skips_when_allowed(isolated_state):
    """Should NOT write the audit file when decision is allowed."""
    d = evaluate_cooldown("EUR/USD", "LONG", "EURUSD_OB_1_LONG", [])
    write_blocked_signal({"asset": "EUR/USD"}, d)
    assert not isolated_state["blocked"].exists()
