"""
Liquidity Sweep Detector
Detects when price wicks above a recent swing high (bearish sweep) or below
a swing low (bullish sweep) and closes back inside the prior range — the
classic "stop hunt" pattern that precedes a real move.
"""

import pandas as pd
from dataclasses import dataclass, asdict


@dataclass
class Sweep:
    idx: int
    direction: str        # "bullish" (swept low → reversal up)
                          # "bearish" (swept high → reversal down)
    swept_level: float    # the swing high/low that was taken
    wick_extreme: float   # the wick high/low (where stops were grabbed)
    close: float
    confirmed_idx: int | None = None   # bar index when reversal confirmed


def find_recent_swings(df: pd.DataFrame, lookback: int = 2) -> tuple[list[int], list[int]]:
    """Returns (swing_high_idxs, swing_low_idxs)."""
    h = df["high"].values
    l = df["low"].values
    highs, lows = [], []
    for i in range(lookback, len(df) - lookback):
        if all(h[i] > h[i - j] for j in range(1, lookback + 1)) and \
           all(h[i] > h[i + j] for j in range(1, lookback + 1)):
            highs.append(i)
        if all(l[i] < l[i - j] for j in range(1, lookback + 1)) and \
           all(l[i] < l[i + j] for j in range(1, lookback + 1)):
            lows.append(i)
    return highs, lows


def detect_sweeps(df: pd.DataFrame,
                  reference_df: pd.DataFrame | None = None,
                  lookback_swings: int = 5,
                  swing_lookback: int = 2) -> list[Sweep]:
    """
    Find liquidity sweeps on `df`. If `reference_df` is provided,
    sweeps test the prior swings of the higher timeframe (e.g., 5M sweeping 15M swings).

    Sweep criteria:
      Bearish sweep: bar.high > prior_swing_high AND bar.close < prior_swing_high
                     (wick took out highs, body closed below = rejection)
      Bullish sweep: bar.low  < prior_swing_low  AND bar.close > prior_swing_low
    """
    sweeps: list[Sweep] = []
    src = reference_df if reference_df is not None else df

    high_idxs, low_idxs = find_recent_swings(src, lookback=swing_lookback)
    swing_highs = [src["high"].iloc[i] for i in high_idxs[-lookback_swings:]]
    swing_lows  = [src["low"].iloc[i]  for i in low_idxs[-lookback_swings:]]

    if not swing_highs or not swing_lows:
        return []

    h, l, c = df["high"].values, df["low"].values, df["close"].values

    for i in range(2, len(df)):
        bar_high, bar_low, bar_close = h[i], l[i], c[i]

        # Bearish sweep: take out a recent swing high, close back below it
        for sh in swing_highs:
            if bar_high > sh and bar_close < sh:
                sweeps.append(Sweep(
                    idx=i, direction="bearish",
                    swept_level=float(sh), wick_extreme=float(bar_high),
                    close=float(bar_close),
                ))
                break

        # Bullish sweep: take out a recent swing low, close back above it
        for sl in swing_lows:
            if bar_low < sl and bar_close > sl:
                sweeps.append(Sweep(
                    idx=i, direction="bullish",
                    swept_level=float(sl), wick_extreme=float(bar_low),
                    close=float(bar_close),
                ))
                break

    return sweeps


def latest_unmitigated_sweep(sweeps: list[Sweep], df: pd.DataFrame,
                             max_age_bars: int = 20) -> Sweep | None:
    """
    Most recent sweep where price hasn't yet broken back through the swept level
    (i.e., the reversal is still potentially in play).
    """
    if not sweeps:
        return None
    sweeps_recent = [s for s in sweeps if s.idx >= len(df) - max_age_bars]
    sweeps_recent.sort(key=lambda s: -s.idx)

    for s in sweeps_recent:
        # Check the bars after the sweep — has price reclaimed the swept level?
        post = df.iloc[s.idx + 1:]
        if len(post) == 0:
            return s
        if s.direction == "bearish":
            # Reversal still valid if highs after sweep haven't exceeded the wick extreme
            if post["high"].max() < s.wick_extreme:
                return s
        else:
            if post["low"].min() > s.wick_extreme:
                return s
    return None


def to_dict(sweep: Sweep) -> dict:
    return asdict(sweep)
