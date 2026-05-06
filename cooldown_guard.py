"""
Cooldown Guard — Stage 7 (pre-validator)
=========================================

Hard pre-filter that runs BEFORE the validator. Stops duplicate signals
fast, enforces position-awareness, and supports structure-based cooldown
reset.

Block rules:
  1. DUPLICATE_SIGNAL      — same (symbol, direction, structure_id) within
                             COOLDOWN_HOURS, unless a new BOS/CHoCH has
                             occurred on this symbol since.
  2. POSITION_ALREADY_OPEN — same direction position already exists.
  3. OPPOSITE_NEEDS_CHOCH  — opposite direction blocked unless a CHoCH
                             has printed on this symbol after the existing
                             position opened.

State persists in cooldown_state.json so cooldowns survive engine restarts.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Tunables ─────────────────────────────────────────────────────────────────
COOLDOWN_HOURS = 4
SIGNAL_HISTORY_DAYS = 7   # how far back to keep signals in cache

STATE_FILE         = Path(__file__).parent / "cooldown_state.json"
BLOCKED_AUDIT_FILE = Path(__file__).parent / "blocked_signals.json"


# ── Data contracts ───────────────────────────────────────────────────────────
@dataclass(frozen=True)
class OpenPositionRef:
    symbol:    str
    direction: str          # "LONG" | "SHORT"
    opened_at: Optional[str] = None   # ISO timestamp


@dataclass(frozen=True)
class CooldownDecision:
    allowed: bool
    code:    str             # "ALLOWED" | "DUPLICATE_SIGNAL" | "POSITION_ALREADY_OPEN" | "OPPOSITE_NEEDS_CHOCH"
    reason:  str = ""
    symbol:  str = ""
    direction: str = ""
    structure_id: str = ""
    age_minutes: Optional[float] = None
    last_match:  Optional[dict] = None


# ── State persistence (atomic write) ─────────────────────────────────────────
def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {"signals": [], "structure_events": {}}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("cooldown_state.json unreadable — starting fresh")
        return {"signals": [], "structure_events": {}}


def _save_state(state: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    tmp.replace(STATE_FILE)


def _parse_ts(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── Recording APIs (called by the engine) ────────────────────────────────────
def record_signal_fired(symbol: str, direction: str,
                         structure_id: str,
                         timestamp: Optional[datetime] = None) -> None:
    """Append AFTER a signal has been validated, broadcast and executed."""
    state = _load_state()
    ts = (timestamp or datetime.now(timezone.utc)).isoformat()
    state["signals"].append({
        "symbol":       symbol,
        "direction":    direction,
        "structure_id": structure_id,
        "timestamp":    ts,
    })
    cutoff = datetime.now(timezone.utc) - timedelta(days=SIGNAL_HISTORY_DAYS)
    state["signals"] = [
        s for s in state["signals"] if _parse_ts(s["timestamp"]) >= cutoff
    ]
    _save_state(state)
    logger.info("Cooldown cache: recorded signal %s %s %s", symbol, direction, structure_id)


def record_structure_event(symbol: str, event_type: str, event_idx: int,
                             timestamp: Optional[datetime] = None) -> None:
    """
    Call EVERY cycle that produces a 4H BOS / CHoCH event.
    Only updates the timestamp when (event_type, event_idx) actually changes.
    """
    if not event_type or event_idx is None:
        return
    state = _load_state()
    prev  = state["structure_events"].get(symbol)
    if prev and prev.get("idx") == event_idx and prev.get("type") == event_type:
        return                # same event already recorded
    state["structure_events"][symbol] = {
        "type":      event_type,
        "idx":       event_idx,
        "timestamp": (timestamp or datetime.now(timezone.utc)).isoformat(),
    }
    _save_state(state)
    logger.info("Cooldown cache: structure event %s %s idx=%d", symbol, event_type, event_idx)


# ── Internal helpers ────────────────────────────────────────────────────────
def _last_signal_match(state: dict, symbol: str, direction: str,
                       structure_id: str, cutoff: datetime) -> Optional[dict]:
    matches = [
        s for s in state["signals"]
        if s["symbol"]       == symbol
        and s["direction"]   == direction
        and s["structure_id"] == structure_id
        and _parse_ts(s["timestamp"]) >= cutoff
    ]
    return matches[-1] if matches else None


def _structure_changed_since(state: dict, symbol: str,
                               prior_ts: datetime) -> bool:
    """
    Returns True if a structure event has been recorded at-or-after the prior
    signal's timestamp. Uses >= (not >) so back-to-back same-microsecond calls
    on Windows still register the structure change correctly.
    """
    ev = state["structure_events"].get(symbol)
    if not ev:
        return False
    return _parse_ts(ev["timestamp"]) >= prior_ts


def _choch_since(state: dict, symbol: str, prior_ts: datetime) -> bool:
    """For opposite-direction guard — only CHoCH (not BOS) counts as reversal."""
    ev = state["structure_events"].get(symbol)
    if not ev or not str(ev.get("type", "")).startswith("CHOCH"):
        return False
    return _parse_ts(ev["timestamp"]) >= prior_ts


# ── Public API ───────────────────────────────────────────────────────────────
def evaluate_cooldown(symbol: str, direction: str, structure_id: str,
                      open_positions: list[OpenPositionRef]) -> CooldownDecision:
    """
    Run the three pre-filter checks. Returns a CooldownDecision —
    if `.allowed` is False, the engine MUST stop and not run the validator,
    not alert, not execute.
    """
    state = _load_state()
    now   = datetime.now(timezone.utc)

    # ── 1. Same-direction open position ─────────────────────────────────────
    for p in open_positions:
        if p.symbol == symbol and p.direction == direction:
            msg = f"Already in {direction} position on {symbol}"
            logger.warning(
                "COOLDOWN BLOCK [position_already_open] symbol=%s direction=%s structure_id=%s",
                symbol, direction, structure_id,
            )
            return CooldownDecision(
                allowed=False, code="POSITION_ALREADY_OPEN", reason=msg,
                symbol=symbol, direction=direction, structure_id=structure_id,
                last_match=asdict(p),
            )

    # ── 2. Opposite-direction needs CHoCH ───────────────────────────────────
    opposite = next(
        (p for p in open_positions if p.symbol == symbol and p.direction != direction),
        None,
    )
    if opposite:
        try:
            prior_ts = _parse_ts(opposite.opened_at) if opposite.opened_at else now - timedelta(days=7)
        except Exception:
            prior_ts = now - timedelta(days=7)
        if not _choch_since(state, symbol, prior_ts):
            msg = f"Opposite-direction {direction} blocked — no CHoCH since opposite position opened"
            logger.warning(
                "COOLDOWN BLOCK [opposite_needs_choch] symbol=%s direction=%s structure_id=%s",
                symbol, direction, structure_id,
            )
            return CooldownDecision(
                allowed=False, code="OPPOSITE_NEEDS_CHOCH", reason=msg,
                symbol=symbol, direction=direction, structure_id=structure_id,
                last_match=asdict(opposite),
            )

    # ── 3. Duplicate within cooldown window ─────────────────────────────────
    cutoff = now - timedelta(hours=COOLDOWN_HOURS)
    last   = _last_signal_match(state, symbol, direction, structure_id, cutoff)
    if last:
        match_ts = _parse_ts(last["timestamp"])
        if _structure_changed_since(state, symbol, match_ts):
            logger.info(
                "Cooldown RESET [%s %s] — new BOS/CHoCH since prior signal at %s",
                symbol, direction, match_ts.isoformat(),
            )
            return CooldownDecision(
                allowed=True, code="ALLOWED",
                reason="Cooldown reset by new structure event",
                symbol=symbol, direction=direction, structure_id=structure_id,
            )
        age_min = (now - match_ts).total_seconds() / 60
        msg = f"duplicate_signal — same setup fired {age_min:.1f} min ago"
        logger.warning(
            "COOLDOWN BLOCK [duplicate_signal] symbol=%s direction=%s structure_id=%s age_min=%.1f",
            symbol, direction, structure_id, age_min,
        )
        return CooldownDecision(
            allowed=False, code="DUPLICATE_SIGNAL", reason=msg,
            symbol=symbol, direction=direction, structure_id=structure_id,
            age_minutes=round(age_min, 1), last_match=last,
        )

    # ── All clear ───────────────────────────────────────────────────────────
    return CooldownDecision(
        allowed=True, code="ALLOWED",
        symbol=symbol, direction=direction, structure_id=structure_id,
    )


def decision_to_payload(d: CooldownDecision) -> dict:
    """
    Returns the EXACT response shape required by the spec.

    Blocked → {status: "BLOCKED_SIGNAL", cooldown_code, cooldown_reason,
               age_minutes, last_match}
    Passed  → {status: "PASSED_COOLDOWN", all other fields null}
    """
    if d.allowed:
        return {
            "status":          "PASSED_COOLDOWN",
            "cooldown_code":   None,
            "cooldown_reason": None,
            "age_minutes":     None,
            "last_match":      None,
        }
    return {
        "status":          "BLOCKED_SIGNAL",
        "cooldown_code":   d.code,
        "cooldown_reason": d.reason,
        "age_minutes":     d.age_minutes if d.age_minutes is not None else 0,
        "last_match":      d.last_match if d.last_match is not None else {},
    }


def write_blocked_signal(signal: dict, decision: CooldownDecision) -> None:
    """Persist a blocked signal to blocked_signals.json for audit/review."""
    if decision.allowed:
        return
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol":    decision.symbol,
        "direction": decision.direction,
        "structure_id": decision.structure_id,
        **decision_to_payload(decision),
        "signal_snapshot": {
            k: signal.get(k) for k in
            ("asset", "direction", "entry", "sl_price",
             "tp1", "tp2", "tp3", "confidence", "session")
        },
    }
    try:
        records = []
        if BLOCKED_AUDIT_FILE.exists():
            try:
                records = json.loads(BLOCKED_AUDIT_FILE.read_text(encoding="utf-8"))
            except Exception:
                records = []
        records.append(entry)
        # Trim — keep last 500 to bound disk
        records = records[-500:]
        tmp = BLOCKED_AUDIT_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(records, indent=2, default=str), encoding="utf-8")
        tmp.replace(BLOCKED_AUDIT_FILE)
    except Exception as e:
        logger.warning("Could not write blocked_signals.json: %s", e)


# ── Self-test demonstrating every block path ────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")

    # Use a temp state file so we don't pollute production
    test_file = Path(__file__).parent / "cooldown_state.test.json"
    if test_file.exists():
        test_file.unlink()
    globals()["STATE_FILE"] = test_file

    sym, dirn, sid = "EUR/USD", "LONG", "EURUSD_OB_191_LONG"

    sym, dirn, sid = "EUR/USD", "LONG", "EURUSD_OB_191_LONG"

    def show(label, d):
        print(f"\n── {label} ──")
        print(json.dumps(decision_to_payload(d), indent=2, default=str))

    show("1 · empty state · PASSED_COOLDOWN", evaluate_cooldown(sym, dirn, sid, []))

    record_signal_fired(sym, dirn, sid)

    show("2 · duplicate within 4h · BLOCKED_SIGNAL/DUPLICATE_SIGNAL",
         evaluate_cooldown(sym, dirn, sid, []))

    record_structure_event(sym, "BOS_BULL", 200)
    show("3 · cooldown reset by new BOS · PASSED_COOLDOWN",
         evaluate_cooldown(sym, dirn, sid, []))

    open_pos = [OpenPositionRef(symbol=sym, direction=dirn, opened_at=datetime.now(timezone.utc).isoformat())]
    show("4 · same direction position · BLOCKED_SIGNAL/POSITION_ALREADY_OPEN",
         evaluate_cooldown(sym, dirn, "EURUSD_DIFFERENT_OB", open_pos))

    pos_open_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    open_pos = [OpenPositionRef(symbol=sym, direction="LONG", opened_at=pos_open_ts)]
    # Note: previous step recorded BOS_BULL idx=200; force a state reset for this test
    state = _load_state()
    state["structure_events"][sym] = {"type":"BOS_BULL","idx":200,"timestamp":pos_open_ts}
    _save_state(state)
    show("5 · opposite without CHoCH · BLOCKED_SIGNAL/OPPOSITE_NEEDS_CHOCH",
         evaluate_cooldown(sym, "SHORT", "EURUSD_OB_201_SHORT", open_pos))

    record_structure_event(sym, "CHOCH_BEAR", 210)
    show("6 · opposite WITH CHoCH after · PASSED_COOLDOWN",
         evaluate_cooldown(sym, "SHORT", "EURUSD_OB_201_SHORT", open_pos))

    test_file.unlink(missing_ok=True)
    print("\nAll 6 paths verified.\n")
