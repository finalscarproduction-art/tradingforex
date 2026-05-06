"""
Module 6B v2 — LTF Confluence Entry Trigger
============================================
FVG promoted from standalone trigger to a confluence component.

Required components (all three must be present):
  • HTF bias       (4H direction)
  • POI            (Order Block / S/R)
  • Liquidity sweep (5M sweep of recent 5M/15M swing)

Plus ≥ 2 of:
  • BOS  (continuation break of structure)
  • CHoCH (reversal break of structure)
  • FVG  (mitigated, scoring ≥ 7)
  • Rejection candle (1M pin / engulfing inside the FVG)

Confidence tiers:
  PREMIUM  — bias + POI + sweep + FVG + BOS    (5 confluences)
  HIGH     — bias + POI + sweep + (FVG OR BOS) (4 confluences)
  MEDIUM   — bias + POI + sweep + rejection    (3 confluences)
  Below 3 confluences → no signal

Entry:
  PASSIVE_LIMIT — entry at FVG midpoint (best fill)
  AGGRESSIVE    — entry at FVG far edge if price already pulled in
SL:
  Beyond FVG far edge + 0.3 × ATR(1M)
"""

import logging
import pandas as pd
import pandas_ta as ta

from sweep_detector import detect_sweeps, latest_unmitigated_sweep, to_dict as sweep_to_dict
from smc_detector   import analyse_smc
from fvg_detector   import (
    detect_fvg, score_fvg, best_actionable, to_dict as fvg_to_dict,
    ACTIONABLE_THRESHOLD,
)

logger = logging.getLogger(__name__)


# ── Public API ───────────────────────────────────────────────────────────────
def evaluate_ltf_entry(df_1m: pd.DataFrame, df_5m: pd.DataFrame,
                       df_15m: pd.DataFrame, htf_direction: str,
                       pair: str,
                       poi_zones: list[dict] | None = None,
                       ema_200_5m: float | None = None) -> dict:
    """
    Returns a dict with:
      tradeable, confidence, entry_price, sl_price, sl_pips, mode,
      direction, fvg, sweep, bos_event, confluences, score, reasons
    """
    if df_1m is None or len(df_1m) < 30:
        return _no_entry("insufficient 1M data")
    if df_5m is None or len(df_5m) < 30:
        return _no_entry("insufficient 5M data")
    if htf_direction not in ("LONG", "SHORT"):
        return _no_entry("HTF bias not set")

    pip = _pip(pair)
    target_dir = "bullish" if htf_direction == "LONG" else "bearish"
    confluences: list[str] = ["HTF_BIAS"]    # required #1

    # ── Required #2 — POI presence (caller supplies; if missing → block) ────
    if not poi_zones:
        return _no_entry("No POI zones supplied — required confluence missing")
    confluences.append("POI")

    # ── Required #3 — Liquidity sweep on 5M ─────────────────────────────────
    sweeps = detect_sweeps(df_5m, reference_df=df_15m,
                           lookback_swings=5, swing_lookback=2)
    aligned_sweeps = [s for s in sweeps if s.direction == target_dir]
    sweep = latest_unmitigated_sweep(aligned_sweeps, df_5m, max_age_bars=12)
    if sweep is None:
        return _no_entry(f"No recent {target_dir} 5M sweep — required confluence missing")
    confluences.append("SWEEP")

    # ── Optional confluences ────────────────────────────────────────────────
    sweep_ts = df_5m.index[sweep.idx]
    df_1m_post = df_1m[df_1m.index >= sweep_ts]
    if len(df_1m_post) < 5:
        return _no_entry("Sweep too recent — need ≥5 1M bars after sweep")

    smc_1m  = analyse_smc(df_1m_post.reset_index(drop=True), lookback=2)
    last_ev = smc_1m.get("last_event")

    bos_aligned = (last_ev and (
        (target_dir == "bullish" and last_ev["type"] in ("BOS_BULL", "CHOCH_BULL")) or
        (target_dir == "bearish" and last_ev["type"] in ("BOS_BEAR", "CHOCH_BEAR"))
    ))
    if bos_aligned:
        confluences.append("BOS" if last_ev["type"].startswith("BOS") else "CHOCH")

    # ── FVG detection on 1M post-sweep ──────────────────────────────────────
    df_1m_post_reset = df_1m_post.reset_index(drop=True)
    fvgs = detect_fvg(df_1m_post_reset, direction=target_dir, lookback=50)
    for f in fvgs:
        score_fvg(
            f,
            htf_direction = "BULLISH" if htf_direction == "LONG" else "BEARISH",
            poi_zones     = poi_zones,
            recent_sweep  = True,                       # we're here because of a sweep
            recent_bos    = bos_aligned,
            ema_200       = ema_200_5m,
            pip_size      = pip,
        )
    chosen_fvg = best_actionable(fvgs, direction=target_dir, require_unfilled=True)
    if chosen_fvg:
        confluences.append("FVG")

    # ── Rejection candle inside FVG (only relevant if FVG exists) ──────────
    rejection = False
    if chosen_fvg:
        rejection = _has_rejection_candle(df_1m_post_reset, chosen_fvg, target_dir)
        if rejection:
            confluences.append("REJECTION")

    # ── Confluence threshold check ─────────────────────────────────────────
    optional_set = {c for c in confluences if c not in ("HTF_BIAS", "POI", "SWEEP")}
    if len(optional_set) < 2:
        return _no_entry(
            f"Only {len(optional_set)} optional confluence(s) — need ≥ 2. Have: {sorted(optional_set)}"
        )

    # ── Confidence tier ─────────────────────────────────────────────────────
    has_fvg = "FVG" in optional_set
    has_bos = "BOS" in optional_set or "CHOCH" in optional_set

    if has_fvg and has_bos:
        confidence = "PREMIUM"
    elif has_fvg or has_bos:
        confidence = "HIGH"
    elif "REJECTION" in optional_set:
        confidence = "MEDIUM"
    else:
        confidence = "MEDIUM"

    # ── Entry & SL ──────────────────────────────────────────────────────────
    atr_1m = ta.atr(df_1m["high"], df_1m["low"], df_1m["close"], length=14)
    atr_buf = float(atr_1m.iloc[-1]) if atr_1m is not None else pip

    if chosen_fvg:
        entry = chosen_fvg.mid
        if target_dir == "bullish":
            sl = chosen_fvg.bottom - max(0.3 * atr_buf, 2 * pip)
        else:
            sl = chosen_fvg.top    + max(0.3 * atr_buf, 2 * pip)
    else:
        # Fallback to sweep-based SL when no FVG (medium-tier rejection-only setups)
        if target_dir == "bullish":
            entry = sweep.swept_level
            sl    = sweep.wick_extreme - max(0.3 * atr_buf, 2 * pip)
        else:
            entry = sweep.swept_level
            sl    = sweep.wick_extreme + max(0.3 * atr_buf, 2 * pip)

    sl_pips = abs(entry - sl) / pip

    logger.info(
        "LTF [%s]: confidence=%s · confluences=%s · FVG=%s · entry=%.5f · sl=%.5f (%.1fp)",
        target_dir, confidence, confluences,
        f"score {chosen_fvg.score}" if chosen_fvg else "none",
        entry, sl, sl_pips,
    )

    return {
        "tradeable":     confidence in ("PREMIUM", "HIGH", "MEDIUM"),
        "confidence":    confidence,
        "score":         _confidence_score(confidence),
        "confluences":   confluences,
        "reasons":       confluences,
        "direction":     htf_direction,
        "entry_price":   round(float(entry), 6),
        "sl_price":      round(float(sl),    6),
        "sl_pips":       round(sl_pips, 1),
        "fvg":           fvg_to_dict(chosen_fvg) if chosen_fvg else None,
        "sweep":         sweep_to_dict(sweep),
        "bos_event":     last_ev if bos_aligned else None,
        "rejection":     rejection,
        "mode":          "PASSIVE_LIMIT",
    }


# ── Helpers ─────────────────────────────────────────────────────────────────
def _has_rejection_candle(df: pd.DataFrame, fvg, target_dir: str) -> bool:
    """Look for a 1M pin / engulfing close inside the FVG zone (last 5 bars)."""
    if len(df) < 2:
        return False
    last_5 = df.tail(5)
    for _, row in last_5.iterrows():
        o, h, l, c = float(row["open"]), float(row["high"]), \
                     float(row["low"]),  float(row["close"])
        body = abs(c - o)
        rng  = h - l or 1e-9
        in_fvg = fvg.bottom <= c <= fvg.top
        if not in_fvg:
            continue
        if target_dir == "bullish":
            # Bullish pin: long lower wick, close near high
            if (o - l) > 2 * body and c > o:
                return True
            # Bullish engulfing: close beyond previous body
        else:
            if (h - o) > 2 * body and c < o:
                return True
    return False


def _confidence_score(label: str) -> int:
    return {"PREMIUM": 9, "HIGH": 7, "MEDIUM": 5}.get(label, 0)


def _no_entry(reason: str) -> dict:
    return {
        "tradeable":   False, "confidence": "NO_ENTRY",
        "score": 0, "confluences": [], "reasons": [reason], "reason": reason,
        "entry_price": 0.0, "sl_price": 0.0, "sl_pips": 0.0,
        "fvg": None, "sweep": None, "bos_event": None, "rejection": False,
        "mode": "PASSIVE_LIMIT",
    }


def _pip(pair: str) -> float:
    if "JPY"  in pair.upper(): return 0.01
    if "USDT" in pair.upper(): return 1.0
    return 0.0001
