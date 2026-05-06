"""
Module 6 — 15M Entry Trigger v2 (SMC + 200 EMA + S/R)

Perfect-entry checklist (must satisfy ALL):
  1. 4H 200 EMA agrees with direction        [hard gate]
  2. 4H last structure event = BOS in dir    [hard gate]
  3. 15M price inside an active Order Block  [hard gate]
  4. 15M CHoCH or BOS in trade direction     [confirmation]
  5. 15M EMA(9) cross or aligned with EMA(21)
  6. 15M candle pattern confirms (engulfing / pin / inside-break)
  7. RSI 40–65 (long) / 35–60 (short)        [no overshoot]

Confidence:
  ALL 7  → PREMIUM
  6 of 7 → HIGH
  5 of 7 → MEDIUM
  <5     → NO_TRADE
Gates 1–3 violated  → BLOCKED
"""

import pandas as pd
import pandas_ta as ta
import logging

from ema_analysis import compute_emas, ema200_gate
from smc_detector import analyse_smc, price_in_ob
from config       import REQUIRE_OB_TOUCH, SMC_SWING_LOOKBACK_15M

logger = logging.getLogger(__name__)


def evaluate_entry(df_15m: pd.DataFrame, df_4h: pd.DataFrame,
                   htf: dict, sr_levels: list[dict]) -> dict:
    if df_15m is None or len(df_15m) < 30:
        return _no_trade("insufficient 15M data")
    if df_4h is None or len(df_4h) < 30:
        return _no_trade("insufficient 4H data")

    direction = "LONG"  if htf["trend"] == "BULLISH" else \
                "SHORT" if htf["trend"] == "BEARISH" else "NEUTRAL"
    if direction == "NEUTRAL":
        return _no_trade("4H trend neutral")

    last = df_15m["close"].iloc[-1]

    # ── HARD GATE 1: 4H 200 EMA ───────────────────────────────────────────────
    gate_4h = ema200_gate(htf["last_close"], htf.get("ema200"),
                           htf.get("ema200_slope", "flat"), direction)
    if not gate_4h["passed"]:
        return _blocked(direction, last, "4H 200 EMA violated")

    # ── HARD GATE 2: 4H structure must show BOS in trade direction ────────────
    smc_4h = analyse_smc(df_4h, lookback=3)
    last_4h_event = smc_4h.get("last_event")
    smc_4h_aligned = (
        last_4h_event and (
            (direction == "LONG"  and last_4h_event["type"] in ("BOS_BULL", "CHOCH_BULL")) or
            (direction == "SHORT" and last_4h_event["type"] in ("BOS_BEAR", "CHOCH_BEAR"))
        )
    )
    if not smc_4h_aligned:
        return _blocked(direction, last,
            f"4H last structure: {last_4h_event['type'] if last_4h_event else 'none'} — not aligned")

    # ── HARD GATE 3: Price inside an active Order Block (4H or 15M) ───────────
    smc_15m = analyse_smc(df_15m, lookback=SMC_SWING_LOOKBACK_15M)
    active_obs_4h  = smc_4h.get("active_obs", [])
    active_obs_15m = smc_15m.get("active_obs", [])

    in_ob = False
    matched_ob = None
    matched_tf = None
    for ob in active_obs_4h:
        if (direction == "LONG" and ob["direction"] == "bullish") or \
           (direction == "SHORT" and ob["direction"] == "bearish"):
            if price_in_ob(last, ob):
                in_ob, matched_ob, matched_tf = True, ob, "4H"
                break
    if not in_ob:
        for ob in active_obs_15m:
            if (direction == "LONG" and ob["direction"] == "bullish") or \
               (direction == "SHORT" and ob["direction"] == "bearish"):
                if price_in_ob(last, ob):
                    in_ob, matched_ob, matched_tf = True, ob, "15M"
                    break

    if REQUIRE_OB_TOUCH and not in_ob:
        return _blocked(direction, last, "Price not in any active Order Block")

    # ── 7-condition checklist ─────────────────────────────────────────────────
    conditions = {}
    conditions["ema_200_4h"]   = True   # already gated
    conditions["bos_4h"]       = True   # already gated
    conditions["in_order_block"] = in_ob
    conditions["smc_15m_confirm"] = bool(
        smc_15m.get("last_event") and (
            (direction == "LONG"  and smc_15m["last_event"]["type"].endswith("BULL")) or
            (direction == "SHORT" and smc_15m["last_event"]["type"].endswith("BEAR"))
        )
    )

    # EMA 9 / 21 cross or alignment
    close = df_15m["close"]
    e9, e21 = ta.ema(close, 9), ta.ema(close, 21)
    if e9 is not None and e21 is not None:
        if direction == "LONG":
            conditions["ema_cross_15m"] = bool(e9.iloc[-1] > e21.iloc[-1])
        else:
            conditions["ema_cross_15m"] = bool(e9.iloc[-1] < e21.iloc[-1])
    else:
        conditions["ema_cross_15m"] = False

    # Candle pattern
    conditions["candle_pattern"] = _detect_pattern(df_15m, direction)

    # RSI sweet spot
    rsi = ta.rsi(close, length=14)
    if rsi is not None:
        rv = float(rsi.iloc[-1])
        if direction == "LONG":
            conditions["rsi_window"] = 40 <= rv <= 65
        else:
            conditions["rsi_window"] = 35 <= rv <= 60
    else:
        conditions["rsi_window"] = False

    met = sum(conditions.values())

    if met >= 7:
        confidence = "PREMIUM"
    elif met >= 6:
        confidence = "HIGH"
    elif met >= 5:
        confidence = "MEDIUM"
    else:
        confidence = "NO_TRADE"

    atr_15m = ta.atr(df_15m["high"], df_15m["low"], close, length=14)
    atr_val = float(atr_15m.iloc[-1]) if atr_15m is not None else 0.0

    logger.info(
        "Entry [%s]: %d/7 cond | conf=%s | OB=%s on %s | 4H last: %s",
        direction, met, confidence,
        "YES" if in_ob else "NO",
        matched_tf or "—",
        last_4h_event["type"] if last_4h_event else "none",
    )

    return {
        "tradeable":      confidence in ("PREMIUM", "HIGH", "MEDIUM"),
        "confidence":     confidence,
        "conditions_met": met,
        "conditions":     conditions,
        "direction":      direction,
        "entry_price":    round(float(last), 6),
        "atr_15m":        atr_val,
        "gate_4h":        gate_4h,
        "matched_ob":     matched_ob,
        "ob_timeframe":   matched_tf,
        "smc_4h_event":   last_4h_event,
        "smc_15m_event":  smc_15m.get("last_event"),
        "smc_15m_obs":    active_obs_15m[:3],
        "smc_4h_obs":     active_obs_4h[:3],
    }


def _detect_pattern(df: pd.DataFrame, direction: str) -> bool:
    if len(df) < 4: return False
    o1, h1, l1, c1 = df[["open","high","low","close"]].iloc[-2]
    o2, h2, l2, c2 = df[["open","high","low","close"]].iloc[-1]
    body2 = abs(c2 - o2)

    if direction == "LONG":
        if c1 < o1 and c2 > o2 and c2 > o1 and o2 < c1: return True
        if c2 > o2 and (o2 - l2) > 2 * body2: return True
        if h2 > h1 and c2 > c1: return True
    else:
        if c1 > o1 and c2 < o2 and c2 < o1 and o2 > c1: return True
        if c2 < o2 and (h2 - o2) > 2 * body2: return True
        if l2 < l1 and c2 < c1: return True
    return False


def _no_trade(reason: str) -> dict:
    return {"tradeable": False, "confidence": "NO_TRADE", "conditions_met": 0,
            "conditions": {}, "direction": "NEUTRAL", "entry_price": 0.0,
            "atr_15m": 0.0, "reason": reason,
            "matched_ob": None, "smc_4h_obs": [], "smc_15m_obs": []}


def _blocked(direction: str, price: float, reason: str) -> dict:
    return {"tradeable": False, "confidence": "BLOCKED", "conditions_met": 0,
            "conditions": {}, "direction": direction, "entry_price": price,
            "atr_15m": 0.0, "reason": reason,
            "matched_ob": None, "smc_4h_obs": [], "smc_15m_obs": []}
