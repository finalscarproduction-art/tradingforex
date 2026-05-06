"""
MT5 Executor — connects to FxPro MT5 terminal and places SMC engine trades.
Uses the official MetaTrader5 Python package.

Behavior per signal:
  1. Resolve broker symbol
  2. Verify spread, daily DD, max positions
  3. Compute lot size to risk exactly RISK_PCT_PER_TRADE of GBP account
  4. Open ONE position with full lot, attach SL
  5. Place 3 partial-TP limit orders (35% / 40% / 25%)
"""

import logging
from datetime import datetime, timezone

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

from mt5_config import (
    MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, LIVE_TRADING, MT5_MODE, DRY_RUN,
    RISK_PCT_PER_TRADE, MAX_OPEN_POSITIONS, MAX_DAILY_DRAWDOWN_PCT,
    MAX_SLIPPAGE_PIPS, MIN_LOT, MAX_LOT,
    TP1_CLOSE_PCT, TP2_CLOSE_PCT, TP3_CLOSE_PCT,
    SYMBOL_MAP, ALLOWED_SYMBOLS,
    MAX_SPREAD_PIPS_FX, MAX_SPREAD_PIPS_JPY, MAX_SPREAD_POINTS_GOLD,
    MAGIC_NUMBER, EXPERT_NAME,
)
from mt5_safety import (
    OrderIntent, precheck_signal, validate_order_request,
    _order_type_for_intent,
)

logger = logging.getLogger(__name__)
_connected = False
_session_start_balance = None


def connect() -> bool:
    """Initialise MT5 terminal and log in. Idempotent."""
    global _connected, _session_start_balance
    if _connected:
        return True
    if not MT5_AVAILABLE:
        logger.warning("MetaTrader5 package not installed — `pip install MetaTrader5`")
        return False

    if not mt5.initialize():
        logger.error("MT5 initialize failed: %s", mt5.last_error())
        return False

    if MT5_LOGIN and MT5_PASSWORD:
        ok = mt5.login(MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)
        if not ok:
            logger.error("MT5 login failed: %s", mt5.last_error())
            mt5.shutdown()
            return False

    info = mt5.account_info()
    if not info:
        logger.error("MT5 account_info empty — check login")
        return False

    _session_start_balance = info.balance
    _connected = True
    logger.info("MT5 connected ✓ %s | acct=%d | bal=%.2f %s | mode=%s",
                info.server, info.login, info.balance, info.currency, MT5_MODE)
    return True


def shutdown():
    global _connected
    if _connected and MT5_AVAILABLE:
        mt5.shutdown()
        _connected = False


def execute_signal(signal: dict) -> dict:
    """
    Place a trade from an engine signal. Returns:
      {"success": bool, "ticket": int|None, "reason": str, "lot": float}
    """
    if not connect():
        return _fail("MT5 not connected")

    # ── 🛡 SAFETY GATE 1: signal geometry ──────────────────────────────────
    geo = precheck_signal(signal)
    if not geo.ok:
        logger.error("🛑 SIGNAL GEOMETRY UNSAFE — refusing to send: %s [%s]",
                     geo.reason, geo.code)
        return _fail(f"safety_geometry: {geo.reason}")

    asset      = signal.get("asset")
    direction  = signal.get("direction")
    entry      = signal.get("entry")
    sl         = signal.get("sl_price")
    tp1        = signal.get("tp1")
    tp2        = signal.get("tp2")
    tp3        = signal.get("tp3")

    # ── Resolve broker symbol ────────────────────────────────────────────────
    symbol = SYMBOL_MAP.get(asset)
    if not symbol or symbol not in ALLOWED_SYMBOLS:
        return _fail(f"{asset} not in symbol whitelist")

    if not mt5.symbol_select(symbol, True):
        return _fail(f"Symbol {symbol} unavailable on broker")

    info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if not info or not tick:
        return _fail(f"No tick data for {symbol}")

    # ── Spread filter ────────────────────────────────────────────────────────
    spread_pts = (tick.ask - tick.bid) / info.point
    spread_pips = spread_pts * 10 if info.digits in (3, 5) else spread_pts
    cap = (MAX_SPREAD_POINTS_GOLD if "XAU" in symbol else
           MAX_SPREAD_PIPS_JPY if "JPY" in symbol else MAX_SPREAD_PIPS_FX)
    cap_pts = cap * 10 if info.digits in (3, 5) else cap
    if spread_pts > cap_pts:
        return _fail(f"Spread {spread_pts:.0f}pt > cap {cap_pts:.0f}pt")

    # ── Position cap ─────────────────────────────────────────────────────────
    open_positions = mt5.positions_get() or []
    bot_positions = [p for p in open_positions if p.magic == MAGIC_NUMBER]
    if len(bot_positions) >= MAX_OPEN_POSITIONS:
        return _fail(f"Max positions reached ({len(bot_positions)}/{MAX_OPEN_POSITIONS})")

    # ── Daily drawdown kill switch ───────────────────────────────────────────
    acct = mt5.account_info()
    if _session_start_balance and acct:
        dd_pct = (_session_start_balance - acct.equity) / _session_start_balance * 100
        if dd_pct >= MAX_DAILY_DRAWDOWN_PCT:
            return _fail(f"Daily DD {dd_pct:.2f}% >= cap {MAX_DAILY_DRAWDOWN_PCT}% — bot paused")

    # ── Position size from SL distance ───────────────────────────────────────
    risk_money = acct.balance * (RISK_PCT_PER_TRADE / 100)
    sl_dist    = abs(entry - sl)
    if sl_dist <= 0:
        return _fail("Invalid SL distance")

    # tick_value = profit/loss in account currency for 1 lot per 1 point move
    one_point_value = info.trade_tick_value / info.trade_tick_size  # value per 1.0 price unit per 1 lot
    raw_lot = risk_money / (sl_dist * one_point_value)

    # Round to broker's volume step
    step = info.volume_step or 0.01
    lot = max(MIN_LOT, min(MAX_LOT, round(raw_lot / step) * step))
    lot = round(lot, 2)

    logger.info("Sizing: risk=£%.2f sl_dist=%.5f → raw=%.4f → lot=%.2f",
                risk_money, sl_dist, raw_lot, lot)

    # ── Place market order with SL only (TPs as separate partial orders) ─────
    order_type = _order_type_for_intent(direction, OrderIntent.ENTRY_ORDER, mt5)
    price      = tick.ask if direction == "LONG" else tick.bid
    deviation  = MAX_SLIPPAGE_PIPS * 10 if info.digits in (3, 5) else MAX_SLIPPAGE_PIPS

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       symbol,
        "volume":       lot,
        "type":         order_type,
        "price":        price,
        "sl":           round(sl, info.digits),
        "tp":           0.0,                       # no native TP — we manage TP1/2/3 ourselves
        "deviation":    deviation,
        "magic":        MAGIC_NUMBER,
        "comment":      f"SMC ENTRY {direction[0]}"[:31],
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    # ── 🛡 SAFETY GATE 2: pre-send audit (logs request + verifies type↔direction)
    audit = validate_order_request(request, direction, OrderIntent.ENTRY_ORDER, mt5)
    if not audit.ok:
        logger.error("🛑 PRE-SEND AUDIT BLOCKED ENTRY ORDER: %s [%s]",
                     audit.reason, audit.code)
        return _fail(f"audit_blocked: {audit.reason}")

    # ── 🧪 DRY_RUN — simulate the fill, never call broker ─────────────────
    if DRY_RUN:
        logger.info("🧪 DRY_RUN: would have sent %s order on %s %.2f lots @ %.5f "
                    "(SL %.5f) — NO real fill",
                    "BUY" if direction == "LONG" else "SELL",
                    symbol, lot, price, sl)
        return {
            "success": True, "ticket": 0, "lot": lot,
            "fill_price": price, "reason": "DRY_RUN",
            "tp_orders": [], "mode": "DRY_RUN",
        }

    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        rc = result.retcode if result else "no result"
        return _fail(f"order_send failed: {rc} — {result.comment if result else ''}")

    ticket = result.order
    logger.info("✅ MT5 ORDER FILLED — %s %s %.2f lots @ %.5f (ticket %d)",
                symbol, direction, lot, result.price, ticket)

    # ── Place 3 partial-TP take-profit limit orders ──────────────────────────
    tp_orders = _place_partial_tps(symbol, direction, lot, tp1, tp2, tp3, info.digits, ticket)

    return {
        "success":  True,
        "ticket":   ticket,
        "lot":      lot,
        "fill_price": result.price,
        "reason":   "OK",
        "tp_orders": tp_orders,
        "mode":     MT5_MODE,
    }


def _place_partial_tps(symbol, direction, total_lot, tp1, tp2, tp3, digits, parent_ticket):
    """Place limit orders that close 35/40/25% of the position at TP1/2/3.
       Dedupes prices, respects broker's stops level, auto-detects filling mode."""
    info = mt5.symbol_info(symbol)
    step = info.volume_step or 0.01
    stops_pts = info.trade_stops_level or 0
    point     = info.point
    min_dist  = stops_pts * point + point   # 1 extra point safety margin

    tick = mt5.symbol_info_tick(symbol)
    market_close_price = tick.bid if direction == "LONG" else tick.ask

    # ── Build planned splits, merging any TPs at the same price ──────────────
    raw = [(TP1_CLOSE_PCT, tp1, "TP1"),
           (TP2_CLOSE_PCT, tp2, "TP2"),
           (TP3_CLOSE_PCT, tp3, "TP3")]

    merged = {}
    for pct, price, label in raw:
        # Push TP outside the broker's stops-level if too close
        if direction == "LONG" and price <= market_close_price + min_dist:
            price = market_close_price + min_dist
        elif direction == "SHORT" and price >= market_close_price - min_dist:
            price = market_close_price - min_dist
        key = round(price, digits)
        if key in merged:
            merged[key]["pct"]    += pct
            merged[key]["labels"].append(label)
        else:
            merged[key] = {"pct": pct, "price": key, "labels": [label]}

    # Centralised type lookup with intent tagging — partial TP exits only
    ot = _order_type_for_intent(direction, OrderIntent.TAKE_PROFIT_EXIT, mt5)

    # ── Auto-detect filling mode for pending orders (RETURN if supported) ────
    fmode = mt5.ORDER_FILLING_RETURN
    if hasattr(info, "filling_mode"):
        fm = info.filling_mode
        # If RETURN bit (4) not set, fall back to IOC (2), then FOK (1)
        if not (fm & 4):
            fmode = mt5.ORDER_FILLING_IOC if (fm & 2) else mt5.ORDER_FILLING_FOK

    orders = []
    for entry in merged.values():
        vol = round((total_lot * entry["pct"]) / step) * step
        vol = round(max(MIN_LOT, vol), 2)
        if vol < MIN_LOT:
            continue
        label = "+".join(entry["labels"])
        # ── 🛡 Geometric sanity per-TP — direction must match ────────────
        if direction == "LONG"  and entry["price"] <= market_close_price:
            logger.error("🛑 SHORT-shaped TP for LONG · price %.5f ≤ market %.5f — blocked",
                         entry["price"], market_close_price)
            continue
        if direction == "SHORT" and entry["price"] >= market_close_price:
            logger.error("🛑 LONG-shaped TP for SHORT · price %.5f ≥ market %.5f — blocked",
                         entry["price"], market_close_price)
            continue

        req = {
            "action":       mt5.TRADE_ACTION_PENDING,
            "symbol":       symbol,
            "volume":       vol,
            "type":         ot,
            "price":        entry["price"],
            "magic":        MAGIC_NUMBER,
            "comment":      f"SMC TP_EXIT_{direction[0]} #{parent_ticket}"[:31],
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": fmode,
        }

        # ── 🛡 SAFETY GATE 3: pre-send audit on each TP-exit ─────────────
        audit = validate_order_request(req, direction, OrderIntent.TAKE_PROFIT_EXIT, mt5)
        if not audit.ok:
            logger.error("🛑 PRE-SEND AUDIT BLOCKED %s: %s [%s]", label, audit.reason, audit.code)
            continue

        r = mt5.order_send(req)
        if r and r.retcode == mt5.TRADE_RETCODE_DONE:
            orders.append({"label": label, "ticket": r.order, "lot": vol, "price": entry["price"]})
            logger.info("  %s placed: %.2f lots @ %.5f (ticket %d)",
                        label, vol, entry["price"], r.order)
        else:
            err = (r.retcode, r.comment) if r else ("none", "no result")
            last_err = mt5.last_error()
            logger.warning("  %s failed: %s | last_error=%s | req={vol=%.2f price=%.5f filling=%d}",
                           label, err, last_err, vol, entry["price"], fmode)

    # ── Fallback: if all 3 partial limits failed, set ONE native TP on the position ──
    if not orders and merged:
        # Pick TP2 (the middle target) as the position's native TP
        tp_prices = sorted(merged.keys()) if direction == "SHORT" else sorted(merged.keys(), reverse=True)
        # For LONG: take the lowest of the merged prices (closest to market = TP1)
        # For SHORT: take the highest = TP1
        target_tp = max(merged.keys()) if direction == "SHORT" else min(merged.keys())

        positions = mt5.positions_get(ticket=parent_ticket) or []
        if positions:
            p = positions[0]
            mod_req = {
                "action":   mt5.TRADE_ACTION_SLTP,
                "position": parent_ticket,
                "symbol":   symbol,
                "sl":       p.sl,
                "tp":       round(target_tp, digits),
            }
            mr = mt5.order_send(mod_req)
            if mr and mr.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info("  ✓ Fallback: native TP set on position #%d at %.5f",
                            parent_ticket, target_tp)
                orders.append({"label": "TP_native", "ticket": parent_ticket,
                              "lot": total_lot, "price": target_tp})
            else:
                logger.warning("  Fallback TP also failed: %s | last_error=%s",
                               (mr.retcode if mr else "none"), mt5.last_error())
    return orders


def trail_to_breakeven_on_tp1(check_interval: int = 30):
    """Background helper — call periodically to move SL to BE after TP1 hits."""
    if not connect():
        return
    positions = mt5.positions_get() or []
    for p in positions:
        if p.magic != MAGIC_NUMBER:
            continue
        # If SL already past entry (BE or better), skip
        if p.type == mt5.POSITION_TYPE_BUY and p.sl >= p.price_open:
            continue
        if p.type == mt5.POSITION_TYPE_SELL and p.sl <= p.price_open and p.sl != 0:
            continue
        # Check if TP1 limit was filled (i.e., volume reduced)
        # Simpler heuristic: if profit already at +1.5×SL distance, BE the stop
        info = mt5.symbol_info(p.symbol)
        sl_dist = abs(p.price_open - p.sl)
        target  = p.price_open + 1.5*sl_dist if p.type == mt5.POSITION_TYPE_BUY else p.price_open - 1.5*sl_dist
        cur = mt5.symbol_info_tick(p.symbol)
        reached = (cur.bid >= target) if p.type == mt5.POSITION_TYPE_BUY else (cur.ask <= target)
        if reached:
            mt5.order_send({
                "action":   mt5.TRADE_ACTION_SLTP,
                "position": p.ticket,
                "sl":       round(p.price_open, info.digits),
                "tp":       p.tp,
            })
            logger.info("Moved SL to BE for #%d", p.ticket)


def _fail(reason: str) -> dict:
    logger.warning("Execution skipped: %s", reason)
    return {"success": False, "ticket": None, "reason": reason, "lot": 0.0, "mode": MT5_MODE}
