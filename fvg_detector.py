"""
FVG (Fair Value Gap) Detector & Quality Scorer
==============================================

A Fair Value Gap is a 3-candle imbalance — the gap between candle[i-1] and
candle[i+1] that the middle candle didn't fill, indicating institutional
displacement.

  Bullish FVG:  candle[i-1].high < candle[i+1].low
                gap = (top = candle[i+1].low, bottom = candle[i-1].high)
  Bearish FVG:  candle[i-1].low  > candle[i+1].high
                gap = (top = candle[i-1].low, bottom = candle[i+1].high)

Lifecycle:
  • mitigated — price has re-entered the gap (any wick touch)
  • filled    — price has fully closed through the gap (invalid for entry)

Quality score (0–10) — per the v4 confluence spec:
  +3  aligns with HTF trend bias
  +2  inside or adjacent to active POI zone (OB / S/R)
  +2  forms after a liquidity sweep
  +2  follows a confirmed BOS or CHoCH
  +1  within 50 pips of 200 EMA
  Threshold: act only on FVGs scoring ≥ 7
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, asdict, field
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

ACTIONABLE_THRESHOLD = 7   # FVG quality score must be ≥ this to act on


# ── Data contract ────────────────────────────────────────────────────────────
@dataclass
class FVG:
    idx:        int                    # bar index of the middle candle
    direction:  str                    # "bullish" | "bearish"
    top:        float
    bottom:     float
    mid:        float
    created_at: int                    # bar index where the gap formed
    filled:     bool = False
    mitigated:  bool = False
    score:      int  = 0
    score_breakdown: dict = field(default_factory=dict)


# ── Detection ────────────────────────────────────────────────────────────────
def detect_fvg(df: pd.DataFrame, direction: Optional[str] = None,
                lookback: int = 50) -> list[FVG]:
    """
    Scan the last `lookback` bars for 3-candle FVG patterns.
    `direction`: "bullish" | "bearish" | None (both).
    Returns FVGs sorted with most-recent first.
    """
    if df is None or len(df) < 3:
        return []
    n = len(df)
    start = max(1, n - lookback - 1)
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values

    out: list[FVG] = []
    for i in range(start, n - 1):
        prev_high, prev_low = float(h[i - 1]), float(l[i - 1])
        next_high, next_low = float(h[i + 1]), float(l[i + 1])

        # Bullish FVG — gap up
        if prev_high < next_low and (direction in (None, "bullish")):
            top, bot = next_low, prev_high
            fvg = FVG(idx=i, direction="bullish",
                      top=top, bottom=bot, mid=(top + bot) / 2.0,
                      created_at=i + 1)
            _set_lifecycle(fvg, df, n)
            out.append(fvg)
            continue

        # Bearish FVG — gap down
        if prev_low > next_high and (direction in (None, "bearish")):
            top, bot = prev_low, next_high
            fvg = FVG(idx=i, direction="bearish",
                      top=top, bottom=bot, mid=(top + bot) / 2.0,
                      created_at=i + 1)
            _set_lifecycle(fvg, df, n)
            out.append(fvg)

    out.sort(key=lambda f: -f.idx)   # most recent first
    return out


def _set_lifecycle(fvg: FVG, df: pd.DataFrame, n: int) -> None:
    """Set mitigated/filled flags by walking forward from creation."""
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    for k in range(fvg.created_at + 1, n):
        if fvg.direction == "bullish":
            # Mitigation = wick re-enters from above
            if l[k] <= fvg.top and not fvg.mitigated:
                fvg.mitigated = True
            # Filled = close fully below the bottom (gap voided)
            if c[k] < fvg.bottom:
                fvg.filled = True
                break
        else:
            if h[k] >= fvg.bottom and not fvg.mitigated:
                fvg.mitigated = True
            if c[k] > fvg.top:
                fvg.filled = True
                break


# ── Quality scoring ──────────────────────────────────────────────────────────
def score_fvg(fvg: FVG, *,
               htf_direction: str,
               poi_zones: list[dict] | None = None,
               recent_sweep: bool = False,
               recent_bos:   bool = False,
               ema_200:      Optional[float] = None,
               pip_size:     float = 0.0001) -> FVG:
    """
    Mutate `fvg` in-place — add `.score` and `.score_breakdown` —
    then return it.
    """
    score = 0
    bd: dict = {}

    # +3 HTF bias alignment
    htf = (htf_direction or "").upper()
    if (htf == "BULLISH" and fvg.direction == "bullish") or \
       (htf == "BEARISH" and fvg.direction == "bearish"):
        score += 3; bd["htf_aligned"] = True
    else:
        bd["htf_aligned"] = False

    # +2 inside / adjacent to POI
    poi_hit = False
    for z in (poi_zones or []):
        zlo, zhi = float(z.get("low",  z.get("price", 0))), float(z.get("high", z.get("price", 0)))
        if zlo == zhi:
            zlo -= 5 * pip_size; zhi += 5 * pip_size
        # Overlap check
        if not (fvg.top < zlo or fvg.bottom > zhi):
            poi_hit = True; break
    if poi_hit:
        score += 2; bd["inside_poi"] = True
    else:
        bd["inside_poi"] = False

    # +2 follows a liquidity sweep
    if recent_sweep:
        score += 2; bd["after_sweep"] = True
    else:
        bd["after_sweep"] = False

    # +2 follows BOS / CHoCH
    if recent_bos:
        score += 2; bd["after_bos"] = True
    else:
        bd["after_bos"] = False

    # +1 within 50 pips of 200 EMA
    if ema_200 is not None and abs(fvg.mid - ema_200) <= 50 * pip_size:
        score += 1; bd["near_200ema"] = True
    else:
        bd["near_200ema"] = False

    fvg.score = score
    fvg.score_breakdown = bd
    return fvg


def best_actionable(fvgs: list[FVG],
                    direction: str | None = None,
                    require_unfilled: bool = True) -> Optional[FVG]:
    """Return the highest-scoring FVG meeting the actionable threshold."""
    candidates = [
        f for f in fvgs
        if f.score >= ACTIONABLE_THRESHOLD
        and (not require_unfilled or not f.filled)
        and (direction is None or f.direction == direction)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda f: (f.score, -f.idx))


def to_dict(fvg: FVG) -> dict:
    return asdict(fvg)
