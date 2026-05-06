"""
Module 5 — 4H Trend Analysis (structure + momentum)
"""

import pandas as pd
import pandas_ta as ta
from ema_analysis import compute_emas, stack_alignment


def analyse_4h(df: pd.DataFrame) -> dict:
    if df is None or len(df) < 50:
        return {"error": "insufficient 4H data"}

    close, high, low = df["close"], df["high"], df["low"]
    last = close.iloc[-1]

    emas = compute_emas(df)
    e21, e50, e200 = emas["ema21"], emas["ema50"], emas["ema200"]
    stack = stack_alignment(last, e21, e50, e200)

    # Trend
    if stack == "BULLISH_ALIGNED":
        trend = "BULLISH"
    elif stack == "BEARISH_ALIGNED":
        trend = "BEARISH"
    else:
        trend = "BULLISH" if (e21 and e50 and e21 > e50 and last > e50) else \
                "BEARISH" if (e21 and e50 and e21 < e50 and last < e50) else "NEUTRAL"

    # Market structure (last 20 bars HH/HL or LH/LL)
    structure = _structure(high.tail(20), low.tail(20))

    # Momentum
    rsi = ta.rsi(close, length=14)
    rsi_val = round(rsi.iloc[-1], 2) if rsi is not None else None

    macd_df = ta.macd(close, fast=12, slow=26, signal=9)
    if macd_df is not None and not macd_df.empty:
        m_line = macd_df.iloc[-1, 0]
        m_sig  = macd_df.iloc[-1, 2]
        hist_now = macd_df.iloc[-1, 1]
        hist_prev = macd_df.iloc[-2, 1] if len(macd_df) > 1 else 0
        macd_signal = "BULLISH" if m_line > m_sig else "BEARISH"
        macd_strength = "STRONG" if abs(hist_now) > abs(hist_prev) else "WEAKENING"
    else:
        macd_signal, macd_strength = "UNKNOWN", "UNKNOWN"

    momentum = "STRONG" if (rsi_val and ((rsi_val > 55 and trend == "BULLISH") or
                                          (rsi_val < 45 and trend == "BEARISH"))) else \
               "MODERATE" if rsi_val and 45 <= rsi_val <= 55 else "WEAK"

    atr = ta.atr(high, low, close, length=14)
    atr_val = round(atr.iloc[-1], 6) if atr is not None else None

    return {
        "trend":        trend,
        "ema_stack":    stack,
        "structure":    structure,
        "momentum":     momentum,
        "rsi":          rsi_val,
        "macd_signal":  macd_signal,
        "macd_strength":macd_strength,
        "ema21":        round(e21,  6) if e21  else None,
        "ema50":        round(e50,  6) if e50  else None,
        "ema200":       round(e200, 6) if e200 else None,
        "ema200_slope": emas["slope_200"],
        "last_close":   round(last, 6),
        "atr_4h":       atr_val,
    }


def _structure(highs: pd.Series, lows: pd.Series) -> str:
    if len(highs) < 5: return "INTACT"
    recent_high = highs.iloc[-1]
    prev_high   = highs.iloc[:-3].max()
    recent_low  = lows.iloc[-1]
    prev_low    = lows.iloc[:-3].min()
    if recent_high > prev_high and recent_low > prev_low:
        return "INTACT"  # bullish structure intact
    if recent_high < prev_high and recent_low < prev_low:
        return "INTACT"  # bearish structure intact
    if recent_high > prev_high * 1.002 or recent_low < prev_low * 0.998:
        return "BREAKING"
    return "INTACT"
