"""
SMC Detector — Break of Structure, Change of Character, Order Blocks, Fair Value Gaps.
Layered on top of 200 EMA bias for "perfect entry" confluence.
"""

import pandas as pd
from dataclasses import dataclass, asdict


@dataclass
class Swing:
    idx: int
    price: float
    kind: str   # "high" | "low"


@dataclass
class StructureEvent:
    idx: int
    price: float
    type: str   # "BOS_BULL" | "BOS_BEAR" | "CHOCH_BULL" | "CHOCH_BEAR"


@dataclass
class OrderBlock:
    idx_start: int
    idx_end: int
    high: float
    low: float
    direction: str   # "bullish" | "bearish"
    mitigated: bool
    bos_idx: int     # which BOS event it's tied to


@dataclass
class FairValueGap:
    idx: int
    high: float
    low: float
    direction: str   # "bullish" | "bearish"
    filled: bool


# ── Swing detection ──────────────────────────────────────────────────────────
def find_swings(df: pd.DataFrame, lookback: int = 3) -> list[Swing]:
    swings = []
    h = df["high"].values
    l = df["low"].values
    for i in range(lookback, len(df) - lookback):
        if all(h[i] > h[i - j] for j in range(1, lookback + 1)) and \
           all(h[i] > h[i + j] for j in range(1, lookback + 1)):
            swings.append(Swing(i, float(h[i]), "high"))
        if all(l[i] < l[i - j] for j in range(1, lookback + 1)) and \
           all(l[i] < l[i + j] for j in range(1, lookback + 1)):
            swings.append(Swing(i, float(l[i]), "low"))
    return swings


# ── Structure events ──────────────────────────────────────────────────────────
def detect_structure(df: pd.DataFrame, swings: list[Swing]) -> list[StructureEvent]:
    """
    Walk forward through closes and flag BOS / CHoCH events.
    BOS = continuation break of last swing in direction of trend.
    CHoCH = first opposite break = trend shift.
    """
    if len(swings) < 2:
        return []

    events: list[StructureEvent] = []
    closes = df["close"].values

    last_high = next((s for s in reversed(swings) if s.kind == "high"), None)
    last_low  = next((s for s in reversed(swings) if s.kind == "low"),  None)
    trend = "neutral"   # "bull" | "bear" | "neutral"

    for i in range(1, len(closes)):
        # Recompute most-recent swings up to bar i
        relevant_highs = [s for s in swings if s.idx < i and s.kind == "high"]
        relevant_lows  = [s for s in swings if s.idx < i and s.kind == "low"]
        if not relevant_highs or not relevant_lows:
            continue
        last_h = relevant_highs[-1]
        last_l = relevant_lows[-1]

        c = closes[i]

        if c > last_h.price:
            ev_type = "BOS_BULL" if trend == "bull" else "CHOCH_BULL"
            events.append(StructureEvent(i, c, ev_type))
            trend = "bull"
        elif c < last_l.price:
            ev_type = "BOS_BEAR" if trend == "bear" else "CHOCH_BEAR"
            events.append(StructureEvent(i, c, ev_type))
            trend = "bear"

    return events


# ── Order Block detection ────────────────────────────────────────────────────
def detect_order_blocks(df: pd.DataFrame, events: list[StructureEvent],
                        max_lookback: int = 15) -> list[OrderBlock]:
    """
    For each BOS/CHoCH event, find the LAST OPPOSING candle before the impulse.
    - Bullish BOS/CHoCH → last bearish candle before the move = bullish OB
    - Bearish BOS/CHoCH → last bullish candle before the move = bearish OB
    """
    obs: list[OrderBlock] = []
    o, h, l, c = df["open"].values, df["high"].values, df["low"].values, df["close"].values

    for ev in events:
        bullish_event = ev.type.endswith("BULL")
        # Walk back from event idx to find last opposing candle
        ob_idx = None
        for k in range(ev.idx - 1, max(0, ev.idx - max_lookback), -1):
            is_bear = c[k] < o[k]
            is_bull = c[k] > o[k]
            if bullish_event and is_bear:
                ob_idx = k
                break
            if not bullish_event and is_bull:
                ob_idx = k
                break

        if ob_idx is None:
            continue

        ob = OrderBlock(
            idx_start = ob_idx,
            idx_end   = ev.idx,
            high      = float(h[ob_idx]),
            low       = float(l[ob_idx]),
            direction = "bullish" if bullish_event else "bearish",
            mitigated = False,
            bos_idx   = ev.idx,
        )

        # Has price returned to mitigate the OB after the event?
        for k in range(ev.idx + 1, len(df)):
            if ob.direction == "bullish":
                # mitigated if price retraced into OB high (touch from above)
                if l[k] <= ob.high:
                    ob.mitigated = True
                    break
            else:
                if h[k] >= ob.low:
                    ob.mitigated = True
                    break

        obs.append(ob)
    return obs


# ── Fair Value Gap detection ─────────────────────────────────────────────────
def detect_fvgs(df: pd.DataFrame) -> list[FairValueGap]:
    """3-candle imbalances. Current implementation flags unfilled gaps only."""
    fvgs = []
    h = df["high"].values
    l = df["low"].values

    for i in range(2, len(df)):
        # Bullish FVG: candle[i-2].high < candle[i].low
        if h[i - 2] < l[i]:
            gap = FairValueGap(i - 1, float(l[i]), float(h[i - 2]), "bullish", False)
            # Check if filled in subsequent bars
            for k in range(i + 1, len(df)):
                if l[k] <= gap.low:
                    gap.filled = True
                    break
            fvgs.append(gap)
        # Bearish FVG: candle[i-2].low > candle[i].high
        elif l[i - 2] > h[i]:
            gap = FairValueGap(i - 1, float(l[i - 2]), float(h[i]), "bearish", False)
            for k in range(i + 1, len(df)):
                if h[k] >= gap.high:
                    gap.filled = True
                    break
            fvgs.append(gap)
    return fvgs


# ── Main public API ──────────────────────────────────────────────────────────
def analyse_smc(df: pd.DataFrame, lookback: int = 3) -> dict:
    """
    Returns:
      {
        "swings":        [...],
        "events":        [...],   # BOS / CHoCH chronological
        "last_event":    {...},
        "trend":         "bullish"|"bearish"|"neutral",
        "order_blocks":  [...],
        "active_obs":    [...],   # unmitigated OBs aligned with trend
        "fvgs":          [...],
        "active_fvgs":   [...],
      }
    """
    if df is None or len(df) < 30:
        return {"error": "insufficient data"}

    swings = find_swings(df, lookback)
    events = detect_structure(df, swings)

    last_event = events[-1] if events else None
    trend = "neutral"
    if last_event:
        trend = "bullish" if last_event.type.endswith("BULL") else "bearish"

    obs = detect_order_blocks(df, events)
    fvgs = detect_fvgs(df)

    # Active = unmitigated OBs aligned with current trend
    active_obs = [
        ob for ob in obs
        if not ob.mitigated and (
            (trend == "bullish" and ob.direction == "bullish") or
            (trend == "bearish" and ob.direction == "bearish")
        )
    ]
    # Latest first
    active_obs.sort(key=lambda x: -x.idx_end)

    active_fvgs = [
        f for f in fvgs
        if not f.filled and (
            (trend == "bullish" and f.direction == "bullish") or
            (trend == "bearish" and f.direction == "bearish")
        )
    ]
    active_fvgs.sort(key=lambda x: -x.idx)

    return {
        "swings":       [asdict(s) for s in swings],
        "events":       [asdict(e) for e in events],
        "last_event":   asdict(last_event) if last_event else None,
        "trend":        trend,
        "order_blocks": [asdict(ob) for ob in obs],
        "active_obs":   [asdict(ob) for ob in active_obs],
        "fvgs":         [asdict(f) for f in fvgs],
        "active_fvgs":  [asdict(f) for f in active_fvgs],
    }


def price_in_ob(price: float, ob: dict, buffer_pct: float = 0.0005) -> bool:
    """Check if current price is inside (or just outside) an order block zone."""
    high = ob["high"] * (1 + buffer_pct)
    low  = ob["low"]  * (1 - buffer_pct)
    return low <= price <= high
