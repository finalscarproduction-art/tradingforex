"""
Module 7 — Stop Loss + 3 Take Profits
Anchored on the Order Block (when present) with hard minimum SL.
"""

import logging
from config import (
    RISK_PER_TRADE_PCT, ATR_BUFFER_MULTIPLIER, MAX_SL_ATR_MULTIPLIER,
    MIN_SL_PIPS_FX, MIN_SL_PIPS_JPY, MIN_SL_PIPS_CRYPTO,
)

logger = logging.getLogger(__name__)


def _pip(pair: str) -> float:
    if "JPY" in pair.upper(): return 0.01
    if "USDT" in pair.upper(): return 1.0
    return 0.0001


def _min_sl_pips(pair: str) -> float:
    if "USDT" in pair.upper(): return MIN_SL_PIPS_CRYPTO
    if "JPY"  in pair.upper(): return MIN_SL_PIPS_JPY
    return MIN_SL_PIPS_FX


def calculate(entry: float, direction: str, atr_15m: float, atr_4h: float,
              df_15m, sr_levels: list[dict], pair: str, matched_ob: dict | None,
              size_factor: float = 1.0) -> dict:

    pip = _pip(pair)
    min_sl_pips = _min_sl_pips(pair)

    # ── Stop Loss anchor ──────────────────────────────────────────────────────
    # Priority 1: beyond the matched Order Block (most logical)
    # Priority 2: beyond entry candle wick + 0.5×ATR(15M) buffer
    # Then enforce min_sl_pips and 2×ATR(4H) cap.
    buffer = max(ATR_BUFFER_MULTIPLIER * atr_15m, 3 * pip)

    if matched_ob:
        if direction == "LONG":
            sl_anchor = matched_ob["low"] - buffer
        else:
            sl_anchor = matched_ob["high"] + buffer
    else:
        c = df_15m.iloc[-1]
        sl_anchor = (c["low"] - buffer) if direction == "LONG" else (c["high"] + buffer)

    risk_pts = abs(entry - sl_anchor)
    risk_pips = risk_pts / pip

    # Enforce minimum
    if risk_pips < min_sl_pips:
        risk_pts = min_sl_pips * pip
        sl_anchor = entry - risk_pts if direction == "LONG" else entry + risk_pts
        risk_pips = min_sl_pips

    sl_price = round(sl_anchor, 6)

    # Hard cap
    if atr_4h and risk_pts > MAX_SL_ATR_MULTIPLIER * atr_4h:
        return {"valid": False,
                "reason": f"SL {risk_pips:.1f} pips > 2×ATR_4H — setup too volatile"}

    # ── Take Profit zones ─────────────────────────────────────────────────────
    if direction == "LONG":
        tp1 = entry + 1.5 * risk_pts
        tp2 = entry + 2.5 * risk_pts
        tp3 = entry + 4.0 * risk_pts
    else:
        tp1 = entry - 1.5 * risk_pts
        tp2 = entry - 2.5 * risk_pts
        tp3 = entry - 4.0 * risk_pts

    # Adjust TPs to land 5 pips before strong S/R, with minimum spacing
    # so MT5 can place 3 distinct limit orders.
    base_tp1, base_tp2, base_tp3 = tp1, tp2, tp3
    tp1 = _adjust_tp(tp1, entry, direction, sr_levels, pip, min_touches=2)
    tp2 = _adjust_tp(tp2, entry, direction, sr_levels, pip, min_touches=3)
    tp3 = _adjust_tp(tp3, entry, direction, sr_levels, pip, min_touches=2,
                     prefer_extreme=True)

    # Enforce min 0.5R spacing — fall back to base targets if TPs cluster
    half_R = 0.5 * risk_pts
    if abs(tp2 - tp1) < half_R:
        tp2 = base_tp2 if abs(base_tp2 - tp1) >= half_R else (
              tp1 + half_R if direction == "LONG" else tp1 - half_R)
    if abs(tp3 - tp2) < half_R:
        tp3 = base_tp3 if abs(base_tp3 - tp2) >= half_R else (
              tp2 + half_R if direction == "LONG" else tp2 - half_R)

    rr1 = round(abs(tp1 - entry) / risk_pts, 2)
    rr2 = round(abs(tp2 - entry) / risk_pts, 2)
    rr3 = round(abs(tp3 - entry) / risk_pts, 2)

    return {
        "valid":        True,
        "entry":        round(entry, 6),
        "direction":    direction,
        "sl_price":     sl_price,
        "sl_pips":      round(risk_pips, 1),
        "sl_anchor":    "OrderBlock" if matched_ob else "ATR+wick",
        "tp1":          round(tp1, 6),
        "tp2":          round(tp2, 6),
        "tp3":          round(tp3, 6),
        "rr1":          f"1:{rr1}",
        "rr2":          f"1:{rr2}",
        "rr3":          f"1:{rr3}",
        "risk_pct":     round(RISK_PER_TRADE_PCT * size_factor, 2),
        "tp1_action":   "Close 35% — move SL to BE",
        "tp2_action":   "Close 40%",
        "tp3_action":   "Trail remaining 25%",
        "invalidation": f"Trade invalid if price closes {'below' if direction=='LONG' else 'above'} {sl_price}",
    }


def _adjust_tp(tp, entry, direction, sr_levels, pip, min_touches=2, prefer_extreme=False):
    if not sr_levels: return tp
    if direction == "LONG":
        between = [l for l in sr_levels
                   if l.get("type") in ("resistance", "round")
                   and entry < l["price"] <= tp
                   and l.get("touches", 0) >= min_touches]
        if between:
            target = max(between, key=lambda x: x["price"]) if prefer_extreme else \
                     min(between, key=lambda x: x["price"])
            return target["price"] - 5 * pip
    else:
        between = [l for l in sr_levels
                   if l.get("type") in ("support", "round")
                   and tp <= l["price"] < entry
                   and l.get("touches", 0) >= min_touches]
        if between:
            target = min(between, key=lambda x: x["price"]) if prefer_extreme else \
                     max(between, key=lambda x: x["price"])
            return target["price"] + 5 * pip
    return tp
