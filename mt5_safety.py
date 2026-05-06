"""
MT5 Order Safety Layer
======================
Hard guarantees before any order is submitted to the broker:

  1. Geometric sanity for the signal
       LONG  →  SL < entry < TPs
       SHORT →  TPs < entry < SL
  2. Order-type / direction agreement
       LONG  →  BUY | BUY_LIMIT | BUY_STOP only
       SHORT →  SELL | SELL_LIMIT | SELL_STOP only
  3. Every order carries an explicit OrderIntent so the broker history
     clearly distinguishes ENTRY_ORDER from TAKE_PROFIT_EXIT.
  4. Final pre-send audit logs the full request and refuses to send if
     the request's type contradicts the signal direction.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# ── Order intent taxonomy ───────────────────────────────────────────────────
class OrderIntent(str, Enum):
    ENTRY_ORDER      = "ENTRY_ORDER"
    STOP_LOSS        = "STOP_LOSS"
    TAKE_PROFIT_EXIT = "TAKE_PROFIT_EXIT"
    PARTIAL_CLOSE    = "PARTIAL_CLOSE"


# ── Geometric validation of the signal itself ───────────────────────────────
@dataclass(frozen=True)
class SafetyResult:
    ok:     bool
    reason: str = ""
    code:   str = ""


def validate_signal_geometry(direction: str, entry: float, sl: float,
                              tps: list[float]) -> SafetyResult:
    """
    Confirm SL is on the protective side of entry and ALL TPs are on the
    profit side. Failing this returns an OK=False result with a typed code.
    """
    direction = direction.upper()
    if direction not in ("LONG", "SHORT"):
        return SafetyResult(False, f"Unknown direction '{direction}'",
                            "BAD_DIRECTION")

    # Entry / SL relationship
    if direction == "LONG" and sl >= entry:
        return SafetyResult(
            False,
            f"LONG SL {sl} must be BELOW entry {entry}",
            "SL_WRONG_SIDE",
        )
    if direction == "SHORT" and sl <= entry:
        return SafetyResult(
            False,
            f"SHORT SL {sl} must be ABOVE entry {entry}",
            "SL_WRONG_SIDE",
        )

    # Entry / TP relationship — every TP must be on the profit side
    for i, tp in enumerate(tps, start=1):
        if direction == "LONG" and tp <= entry:
            return SafetyResult(
                False,
                f"LONG TP{i} {tp} must be ABOVE entry {entry}",
                f"TP{i}_WRONG_SIDE",
            )
        if direction == "SHORT" and tp >= entry:
            return SafetyResult(
                False,
                f"SHORT TP{i} {tp} must be BELOW entry {entry}",
                f"TP{i}_WRONG_SIDE",
            )

    return SafetyResult(True, "Geometry OK", "OK")


# ── Order-type ↔ direction agreement ────────────────────────────────────────
def _order_type_for_intent(direction: str, intent: OrderIntent, mt5_module):
    """
    Pick the broker order_type CONST that's compatible with both the trade
    direction and the order intent. This is the single place where order
    types are produced in the codebase.
    """
    direction = direction.upper()
    if intent == OrderIntent.ENTRY_ORDER:
        return mt5_module.ORDER_TYPE_BUY if direction == "LONG" else mt5_module.ORDER_TYPE_SELL

    if intent in (OrderIntent.TAKE_PROFIT_EXIT, OrderIntent.PARTIAL_CLOSE):
        # Close a LONG → SELL_LIMIT above market.
        # Close a SHORT → BUY_LIMIT below market. (Yes, BUY_LIMIT is correct
        # for closing a SHORT — but we tag the intent so it's never confused
        # with a new entry.)
        return mt5_module.ORDER_TYPE_SELL_LIMIT if direction == "LONG" \
               else mt5_module.ORDER_TYPE_BUY_LIMIT

    if intent == OrderIntent.STOP_LOSS:
        return mt5_module.ORDER_TYPE_SELL_STOP if direction == "LONG" \
               else mt5_module.ORDER_TYPE_BUY_STOP

    raise ValueError(f"Unknown intent {intent}")


def _allowed_order_types(direction: str, intent: OrderIntent, mt5_module):
    """The whitelist of order_type constants legal for this (direction, intent)."""
    direction = direction.upper()
    if intent == OrderIntent.ENTRY_ORDER:
        return ({mt5_module.ORDER_TYPE_BUY, mt5_module.ORDER_TYPE_BUY_LIMIT,
                 mt5_module.ORDER_TYPE_BUY_STOP} if direction == "LONG"
                else
                {mt5_module.ORDER_TYPE_SELL, mt5_module.ORDER_TYPE_SELL_LIMIT,
                 mt5_module.ORDER_TYPE_SELL_STOP})

    if intent in (OrderIntent.TAKE_PROFIT_EXIT, OrderIntent.PARTIAL_CLOSE):
        # Opposite-side limit/stop is the ONLY legal exit pattern
        return ({mt5_module.ORDER_TYPE_SELL_LIMIT, mt5_module.ORDER_TYPE_SELL_STOP}
                if direction == "LONG"
                else
                {mt5_module.ORDER_TYPE_BUY_LIMIT, mt5_module.ORDER_TYPE_BUY_STOP})

    return set()


def validate_order_request(request: dict, signal_direction: str,
                            intent: OrderIntent, mt5_module) -> SafetyResult:
    """
    Final pre-send audit. Logs the full request and refuses if order_type
    contradicts the signal direction or intent.
    """
    order_type = request.get("type")
    allowed    = _allowed_order_types(signal_direction, intent, mt5_module)

    logger.info(
        "[MT5 PRE-SEND AUDIT] direction=%s intent=%s · type=%s · "
        "symbol=%s · vol=%s · price=%s · sl=%s · tp=%s · allowed_types=%s",
        signal_direction, intent.value, order_type,
        request.get("symbol"), request.get("volume"),
        request.get("price"), request.get("sl"), request.get("tp"),
        sorted(allowed),
    )

    if order_type not in allowed:
        return SafetyResult(
            False,
            f"order_type {order_type} not allowed for {signal_direction} "
            f"with intent={intent.value} (allowed: {sorted(allowed)})",
            "ORDER_TYPE_DIRECTION_MISMATCH",
        )

    # Geometric sanity on the request itself
    sl, tp, price = request.get("sl"), request.get("tp"), request.get("price")
    if intent == OrderIntent.ENTRY_ORDER and sl and price:
        if signal_direction == "LONG"  and sl >= price:
            return SafetyResult(False,
                f"ENTRY LONG: SL {sl} not below price {price}",
                "SL_WRONG_SIDE")
        if signal_direction == "SHORT" and sl <= price:
            return SafetyResult(False,
                f"ENTRY SHORT: SL {sl} not above price {price}",
                "SL_WRONG_SIDE")
    return SafetyResult(True, "OK", "OK")


# ── Convenience: single-call gate used by mt5_executor ─────────────────────
def precheck_signal(signal: dict) -> SafetyResult:
    """
    Run the geometric check on an engine signal dict before any broker work.
    """
    tps = [
        float(signal.get("tp1") or 0),
        float(signal.get("tp2") or 0),
        float(signal.get("tp3") or 0),
    ]
    tps = [t for t in tps if t > 0]
    if not tps:
        return SafetyResult(False, "No TP levels on signal", "NO_TPS")
    return validate_signal_geometry(
        direction = signal["direction"],
        entry     = float(signal["entry"]),
        sl        = float(signal["sl_price"]),
        tps       = tps,
    )
