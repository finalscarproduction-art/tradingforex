"""
Module 1 — Asset Selection (session-filtered)
Yahoo Finance for forex (free), Binance via ccxt for crypto.
"""

import ccxt
import pandas as pd
import pandas_ta as ta
import requests
import logging

from config import MIN_ASSET_SCORE
from ema_analysis import compute_emas, ema_slope
from sr_detector  import detect_sr, _pip_size

logger = logging.getLogger(__name__)
_exchange = ccxt.binance({"enableRateLimit": True})


# ── Data fetchers ────────────────────────────────────────────────────────────
def _fetch_crypto(symbol: str, tf: str = "4h", limit: int = 250) -> pd.DataFrame | None:
    try:
        raw = _exchange.fetch_ohlcv(symbol.replace("/", ""), tf, limit=limit)
        df = pd.DataFrame(raw, columns=["ts","open","high","low","close","volume"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms")
        return df.set_index("ts")
    except Exception as e:
        logger.warning("Crypto fetch failed %s: %s", symbol, e)
        return None


_YF_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
_YF_INTERVAL = {"4H": "60m", "15M": "15m", "5M": "5m", "1M": "1m"}
_YF_RANGE    = {"4H": "3mo", "15M": "5d",  "5M": "1d",  "1M": "1d"}

_YF_SYMBOL_OVERRIDES = {
    "XAU/USD": "GC=F",       # CME gold futures — Yahoo's XAUUSD=X returns no data
    "XAG/USD": "SI=F",       # silver futures
    "USOIL":   "CL=F",       # WTI crude futures
}

def _fetch_forex(pair: str, tf_label: str = "4H", limit: int = 250) -> pd.DataFrame | None:
    yf_symbol = _YF_SYMBOL_OVERRIDES.get(pair, pair.replace("/", "") + "=X")
    try:
        resp = requests.get(
            f"{_YF_BASE}/{yf_symbol}",
            params={"interval": _YF_INTERVAL[tf_label], "range": _YF_RANGE[tf_label]},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        data = resp.json()
        result = data.get("chart", {}).get("result")
        if not result:
            logger.warning("YF no data for %s", pair)
            return None
        ts = result[0]["timestamp"]
        q  = result[0]["indicators"]["quote"][0]
        df = pd.DataFrame({
            "open":   q["open"],   "high": q["high"],
            "low":    q["low"],    "close": q["close"],
            "volume": q.get("volume", [0]*len(ts)),
        }, index=pd.to_datetime(ts, unit="s"))
        df = df.dropna(subset=["close"])
        df["volume"] = df["volume"].fillna(0)
        return df.tail(limit)
    except Exception as e:
        logger.warning("Forex fetch failed %s: %s", pair, e)
        return None


# ── Scoring (0–10) ───────────────────────────────────────────────────────────
def _score(df: pd.DataFrame, pair: str, is_forex: bool) -> dict:
    if df is None or len(df) < 50:
        return {"score": 0, "notes": ["insufficient data"]}

    close, high, low, vol = df["close"], df["high"], df["low"], df["volume"]
    last = close.iloc[-1]
    score = 0
    notes = []

    emas = compute_emas(df)
    e21, e50, e200 = emas["ema21"], emas["ema50"], emas["ema200"]

    # +2 trend clarity (clean direction over last 20)
    if e21 and e50 and (e21 > e50 and last > e50 or e21 < e50 and last < e50):
        score += 2; notes.append("Trend clean: +2")
    else:
        notes.append("Trend clean: +0")

    # +2 volume above 20-period avg (forex tick volume from YF when available)
    if vol.mean() > 0:
        avg_vol = vol.rolling(20).mean().iloc[-1]
        if vol.iloc[-1] > avg_vol:
            score += 2; notes.append("Vol > avg: +2")
        else:
            notes.append("Vol < avg: +0")
    else:
        score += 1
        notes.append("Vol unavailable: +1")

    # +1 within 20 pips of S/R
    sr = detect_sr(df, last, pair, lookback=5, filter_pips=50)
    if sr and sr[0]["distance_pips"] <= 20:
        score += 1; notes.append(f"Near S/R ({sr[0]['distance_pips']:.0f}p): +1")

    # +2 ATR sweet spot
    atr = ta.atr(high, low, close, length=14)
    if atr is not None:
        atr_pct = (atr.iloc[-1] / last) * 100
        lo, hi = (0.1, 1.5) if is_forex else (0.5, 4.0)
        if lo <= atr_pct <= hi:
            score += 2; notes.append(f"ATR%={atr_pct:.2f}: +2")
        else:
            notes.append(f"ATR%={atr_pct:.2f}: +0")

    # +1 clean — no candle > 3×ATR in last 5
    if atr is not None and atr.iloc[-1] > 0:
        recent_ranges = (high - low).tail(5)
        if (recent_ranges > 3 * atr.iloc[-1]).sum() == 0:
            score += 1; notes.append("Clean candles: +1")

    # +1 200 EMA slope confirms direction
    direction = "LONG" if (e21 and e50 and e21 > e50 and last > e50) else \
                "SHORT" if (e21 and e50 and e21 < e50 and last < e50) else "NEUTRAL"
    if direction == "LONG"  and emas["slope_200"] in ("rising","flat"): score += 1; notes.append("200EMA slope OK: +1")
    if direction == "SHORT" and emas["slope_200"] in ("falling","flat"):score += 1; notes.append("200EMA slope OK: +1")

    # +1 price on correct side of 200 EMA
    if e200:
        if direction == "LONG"  and last > e200: score += 1; notes.append("Above 200EMA: +1")
        if direction == "SHORT" and last < e200: score += 1; notes.append("Below 200EMA: +1")

    return {
        "score":      score,
        "direction":  direction,
        "notes":      notes,
        "last_close": last,
        "atr":        atr.iloc[-1] if atr is not None else 0.0,
        "ema21":      e21,
        "ema50":      e50,
        "ema200":     e200,
        "slope_200":  emas["slope_200"],
    }


# ── Public API ───────────────────────────────────────────────────────────────
def scan_session(session: dict) -> list[dict]:
    """Scan only the pairs whitelisted for the active session."""
    universe = list(set(session["priority_pairs"] + session["secondary_pairs"]))
    logger.info("Session [%s]: scanning %d pairs", session["session"], len(universe))

    results = []
    for pair in universe:
        is_crypto = "USDT" in pair
        df = _fetch_crypto(pair) if is_crypto else _fetch_forex(pair, "4H")
        scored = _score(df, pair, is_forex=not is_crypto)
        if scored["score"] >= MIN_ASSET_SCORE:
            results.append({
                "asset": pair,
                "type":  "crypto" if is_crypto else "forex",
                "is_priority": pair in session["priority_pairs"],
                **scored,
            })

    # Priority pairs ranked first, then by score
    results.sort(key=lambda x: (not x["is_priority"], -x["score"]))
    return results


_CCXT_TF = {"4H": "4h", "15M": "15m", "5M": "5m", "1M": "1m"}

def get_ohlcv(asset: str, tf_label: str, is_forex: bool, limit: int = 250):
    if is_forex:
        return _fetch_forex(asset, tf_label, limit)
    return _fetch_crypto(asset, tf=_CCXT_TF.get(tf_label, "4h"), limit=limit)
