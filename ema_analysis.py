"""
Module 2 — EMA Analysis (200 master filter)
Hard gate: signals violating the 4H 200 EMA are blocked.
"""

import pandas as pd
import pandas_ta as ta


def ema_slope(ema_series: pd.Series, lookback: int = 3) -> str:
    """Returns 'rising', 'falling', or 'flat' based on recent slope (% change)."""
    if ema_series is None or len(ema_series) < lookback:
        return "flat"
    recent = ema_series.iloc[-lookback:]
    if recent.iloc[0] == 0:
        return "flat"
    change_pct = (recent.iloc[-1] - recent.iloc[0]) / recent.iloc[0] * 100
    if change_pct > 0.05:  return "rising"
    if change_pct < -0.05: return "falling"
    return "flat"


def compute_emas(df: pd.DataFrame) -> dict:
    """Returns dict with 21/50/200 EMA values and slopes."""
    if df is None or len(df) < 30:
        return {"ema21": None, "ema50": None, "ema200": None,
                "slope_200": "flat", "slope_50": "flat", "slope_21": "flat"}

    close = df["close"]
    e21   = ta.ema(close, length=21)
    e50   = ta.ema(close, length=50)
    e200  = ta.ema(close, length=min(200, len(df) - 1))

    return {
        "ema21":     e21.iloc[-1]  if e21  is not None else None,
        "ema50":     e50.iloc[-1]  if e50  is not None else None,
        "ema200":    e200.iloc[-1] if e200 is not None else None,
        "ema21_series":  e21,
        "ema50_series":  e50,
        "ema200_series": e200,
        "slope_21":  ema_slope(e21),
        "slope_50":  ema_slope(e50),
        "slope_200": ema_slope(e200),
    }


def ema200_gate(price: float, ema_4h: float, slope_4h: str, direction: str) -> dict:
    """
    Hard gate. Returns whether the signal is allowed past 200 EMA filter.
    """
    if ema_4h is None:
        return {"passed": False, "reason": "200 EMA not computable"}

    if direction == "LONG":
        passed = price > ema_4h and slope_4h != "falling"
    elif direction == "SHORT":
        passed = price < ema_4h and slope_4h != "rising"
    else:
        return {"passed": False, "reason": "Direction unclear"}

    pip = _pip_size_from_price(price)

    return {
        "passed":        passed,
        "price_vs_ema":  "above" if price > ema_4h else "below",
        "ema_slope":     slope_4h,
        "distance_pips": round(abs(price - ema_4h) / pip, 1),
        "extended":      abs(price - ema_4h) / pip > 150,
        "reason":        ("OK" if passed else
                          f"Price {('above' if price > ema_4h else 'below')} 200 EMA "
                          f"contradicts {direction} or slope {slope_4h}"),
    }


def stack_alignment(price: float, e21: float, e50: float, e200: float) -> str:
    """Bullish/Bearish/Mixed stack."""
    if None in (e21, e50, e200):
        return "MIXED"
    if price > e21 > e50 > e200:
        return "BULLISH_ALIGNED"
    if price < e21 < e50 < e200:
        return "BEARISH_ALIGNED"
    return "MIXED"


def ltf_15m_gate(price: float, ema_15m: float, slope_15m: str, direction: str) -> dict:
    """15M EMA filter — downgrades confidence if violated."""
    if ema_15m is None:
        return {"aligned": False, "downgrade": True}
    if direction == "LONG":
        aligned = price >= ema_15m and slope_15m != "falling"
    else:
        aligned = price <= ema_15m and slope_15m != "rising"
    return {
        "aligned":   aligned,
        "downgrade": not aligned,
        "side":      "above" if price > ema_15m else "below",
    }


def _pip_size_from_price(price: float) -> float:
    if price > 100:    return 0.01     # JPY pairs
    if price > 1000:   return 1.0      # crypto / commodities
    return 0.0001                       # standard FX
