"""
Validation Bridge
=================
Single integration point between the engine and trade_validator.
Builds the validator's typed inputs from engine state, then gates every
downstream side effect (TradingView, WhatsApp, Telegram, MT5).

Invariant enforced here:
  A signal is NEVER alerted or executed unless validate() returns
  ValidatedTrade. Period. There is no other path to alerting or MT5.
"""

import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

from trade_validator import (
    SetupCandidate, OpenPosition, RecentSignal,
    ValidatedTrade, RejectionReason, ValidationResult,
    RejectionCode, validate, is_approved,
    DUPLICATE_WINDOW_HOURS,
)
from htf_bias_provider import compute_bias

logger = logging.getLogger(__name__)
SIGNALS_DB = Path(__file__).parent / "signals.json"

try:
    import MetaTrader5 as mt5
    from mt5_config import MAGIC_NUMBER, SYMBOL_MAP
    MT5_AVAILABLE = True
except Exception:
    MT5_AVAILABLE = False


# ── Build inputs the validator needs ────────────────────────────────────────
def _build_setup_id(signal: dict) -> str:
    """Stable ID — same OB on same pair in same direction = same id."""
    sym = signal["asset"].replace("/", "")
    direction = signal["direction"]
    ob = signal.get("matched_ob") or {}
    if ob:
        anchor = f"{ob.get('idx_end','x')}_{round(ob.get('low', 0), 5)}"
    else:
        anchor = signal.get("entry", "noob")
    return f"{sym}_{anchor}_{direction}"


def _setup_from_signal(signal: dict) -> SetupCandidate:
    return SetupCandidate(
        setup_id   = _build_setup_id(signal),
        symbol     = signal["asset"],
        direction  = signal["direction"],
        entry      = float(signal["entry"]),
        sl         = float(signal["sl_price"]),
        tp1        = float(signal["tp1"]),
        tp2        = float(signal["tp2"]),
        tp3        = float(signal["tp3"]),
        confidence = signal.get("confidence", "MEDIUM").split(" ")[0],
        atr_4h     = float(signal.get("atr_4h", 0) or 0),
        timestamp  = datetime.now(timezone.utc),
    )


def _open_positions_from_mt5(symbol_map_reverse: dict) -> list[OpenPosition]:
    """Pull live open positions tagged with our magic number."""
    if not MT5_AVAILABLE:
        return []
    try:
        if not mt5.initialize():
            return []
        positions = mt5.positions_get() or []
        out = []
        for p in positions:
            if p.magic != MAGIC_NUMBER:
                continue
            engine_symbol = symbol_map_reverse.get(p.symbol, p.symbol)
            direction = "LONG" if p.type == mt5.POSITION_TYPE_BUY else "SHORT"
            out.append(OpenPosition(symbol=engine_symbol, direction=direction,
                                    ticket=int(p.ticket)))
        return out
    except Exception as e:
        logger.warning("MT5 open-positions fetch failed: %s — using empty list", e)
        return []


def _recent_signals_from_log() -> list[RecentSignal]:
    """Load only SENT signals within the cooldown window."""
    if not SIGNALS_DB.exists():
        return []
    try:
        records = json.loads(SIGNALS_DB.read_text(encoding="utf-8"))
    except Exception:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=DUPLICATE_WINDOW_HOURS)
    out = []
    for r in records:
        if r.get("status") != "SENT":
            continue
        ts_raw = r.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_raw)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if ts < cutoff:
            continue
        sid = r.get("setup_id") or _build_setup_id(r)
        out.append(RecentSignal(
            setup_id=sid, symbol=r.get("asset", "?"),
            direction=r.get("direction", "?"), timestamp=ts,
        ))
    return out


# ── Public API ───────────────────────────────────────────────────────────────
def validate_signal(signal: dict, df_daily) -> ValidationResult:
    """
    Build all inputs and run the validator.
    Returns ValidatedTrade on success, RejectionReason on any failure.
    """
    setup    = _setup_from_signal(signal)
    bias     = compute_bias(setup.symbol, df_daily)
    sym_map_reverse = {v: k for k, v in SYMBOL_MAP.items()} if MT5_AVAILABLE else {}
    open_pos = _open_positions_from_mt5(sym_map_reverse)
    recent   = _recent_signals_from_log()

    logger.info(
        "Validating %s %s · setup_id=%s · bias=%s · open=%d · recent=%d",
        setup.symbol, setup.direction, setup.setup_id,
        bias.direction, len(open_pos), len(recent),
    )
    return validate(setup, bias, open_pos, recent)


def rejection_to_payload(rej: RejectionReason, original_signal: dict) -> dict:
    """Build the payload that gets stored as BLOCKED_SIGNAL for audit."""
    return {
        **original_signal,
        "rejection_code":    rej.code.value,
        "rejection_message": rej.message,
        "rejection_details": rej.details,
        "setup_id":          rej.setup_id,
    }


def approval_to_payload(trade: ValidatedTrade, original_signal: dict) -> dict:
    """Carry the validated trade fields back into the engine signal dict."""
    return {
        **original_signal,
        "setup_id":      trade.setup_id,
        "rr1_validated": trade.rr1,
        "gates_passed":  trade.reasons_passed,
    }
