"""
Builds an HTFBias from Daily (and optionally Weekly) candles.
Single source of truth for "what does the higher timeframe say?"
"""

import logging
import pandas as pd
import pandas_ta as ta

from trade_validator import HTFBias

logger = logging.getLogger(__name__)


def compute_bias(symbol: str, df_daily: pd.DataFrame | None,
                 df_weekly: pd.DataFrame | None = None) -> HTFBias:
    """
    BULL / BEAR / NEUTRAL based on:
      • Price vs Daily 200 EMA
      • Daily 200 EMA slope (rising / flat / falling)
      • Optional weekly close direction (boosts confidence, doesn't override)

    Returns HTFBias with hard veto flags (blocks_long / blocks_short)
    that the validator will respect as absolute.
    """
    if df_daily is None or len(df_daily) < 50:
        return HTFBias(symbol=symbol, direction="NEUTRAL",
                       note="Insufficient daily data — neutral by default")

    close = df_daily["close"]
    last  = float(close.iloc[-1])
    ema_d = ta.ema(close, length=min(200, len(df_daily) - 1))
    if ema_d is None:
        return HTFBias(symbol=symbol, direction="NEUTRAL",
                       note="Daily EMA could not be computed")
    ema_val = float(ema_d.iloc[-1])

    # Slope over last 5 days
    if len(ema_d) >= 5:
        slope_pct = (ema_d.iloc[-1] - ema_d.iloc[-5]) / ema_d.iloc[-5] * 100
    else:
        slope_pct = 0.0

    above   = last > ema_val
    rising  = slope_pct >  0.10
    falling = slope_pct < -0.10

    # Optional weekly confirmation
    weekly_dir = "FLAT"
    if df_weekly is not None and len(df_weekly) >= 2:
        wlast = df_weekly.iloc[-1]
        weekly_dir = "BULL" if wlast["close"] > wlast["open"] else "BEAR"

    # Decision matrix — strict: both EMA position AND slope must agree
    if above and not falling:
        direction = "BULL"
        blocks_long, blocks_short = False, True
        note = (f"Price above Daily 200EMA ({last:.5f} vs {ema_val:.5f}), "
                f"slope {slope_pct:+.2f}%, weekly {weekly_dir}")
    elif (not above) and not rising:
        direction = "BEAR"
        blocks_long, blocks_short = True, False
        note = (f"Price below Daily 200EMA ({last:.5f} vs {ema_val:.5f}), "
                f"slope {slope_pct:+.2f}%, weekly {weekly_dir}")
    else:
        direction = "NEUTRAL"
        blocks_long, blocks_short = True, True
        note = (f"Conflicting signals: price {'above' if above else 'below'} EMA "
                f"but slope {slope_pct:+.2f}% — no directional bias")

    bias = HTFBias(
        symbol=symbol, direction=direction,
        blocks_long=blocks_long, blocks_short=blocks_short, note=note,
    )
    logger.info("HTFBias %s: %s · %s", symbol, direction, note)
    return bias
