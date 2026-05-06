"""
Pre-market analysis — runs before London opens (06:30 UTC weekdays).
Acts like a Wall Street trading desk: reads previous day close, weekly candle,
ranks the watchlist by macro bias clarity + setup quality, and sends a
pre-market brief to Telegram + WhatsApp.

If the top pick already meets PREMIUM/HIGH SMC criteria right now, it fires
a normal signal (with MT5 execution) immediately.
"""

import logging
import requests
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timezone

from asset_scanner    import get_ohlcv
from htf_analysis     import analyse_4h
from sr_detector      import detect_sr, nearest_levels
from entry_trigger    import evaluate_entry
from risk_manager     import calculate as calc_risk
from pine_generator   import save_pine_script
from chart_marker     import push_to_chart
from whatsapp_notifier import format_signal, send_whatsapp_alert
from telegram_notifier import format_signal_telegram, send_telegram_alert
from signal_logger    import log_signal

try:
    from mt5_executor import execute_signal as mt5_execute
    MT5_ENABLED = True
except ImportError:
    MT5_ENABLED = False

logger = logging.getLogger(__name__)

# Universe — same set the engine trades, plus gold
PAIRS = [
    ("EUR/USD", False), ("GBP/USD", False), ("USD/JPY", False),
    ("AUD/USD", False), ("USD/CAD", False), ("NZD/USD", False),
    ("EUR/GBP", False), ("GBP/JPY", False), ("EUR/JPY", False),
    ("USD/CHF", False), ("AUD/JPY", False),
    ("XAU/USD", False),
    ("BTC/USDT", True), ("ETH/USDT", True),
]


# ── Yahoo daily/weekly fetchers (works for spot FX + Gold via futures) ──────
_YF_DAILY = "https://query1.finance.yahoo.com/v8/finance/chart"

def _fetch_yf(symbol: str, interval: str, rng: str) -> pd.DataFrame | None:
    try:
        r = requests.get(f"{_YF_DAILY}/{symbol}",
                         params={"interval": interval, "range": rng},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=15).json()
        res = r["chart"]["result"][0]
        ts = res["timestamp"]
        q  = res["indicators"]["quote"][0]
        df = pd.DataFrame({"open": q["open"], "high": q["high"],
                           "low": q["low"],   "close": q["close"]},
                          index=pd.to_datetime(ts, unit="s")).dropna(subset=["close"])
        return df
    except Exception:
        return None


def _yf_symbol(asset: str, is_crypto: bool) -> str:
    if asset == "XAU/USD": return "GC=F"
    if is_crypto:          return asset.replace("/USDT", "-USD")
    return asset.replace("/", "") + "=X"


# ── Macro context per asset ──────────────────────────────────────────────────
def _macro_context(asset: str, is_crypto: bool) -> dict | None:
    sym = _yf_symbol(asset, is_crypto)
    df_d = _fetch_yf(sym, "1d", "6mo")
    df_w = _fetch_yf(sym, "1wk", "1y")
    if df_d is None or len(df_d) < 50 or df_w is None or len(df_w) < 8:
        return None

    last_d = df_d.iloc[-1]
    prev_d = df_d.iloc[-2]
    last_w = df_w.iloc[-1]
    prev_w = df_w.iloc[-2]

    ema_200_d = ta.ema(df_d["close"], length=min(200, len(df_d)-1))
    ema_50_d  = ta.ema(df_d["close"], length=50)
    rsi_d     = ta.rsi(df_d["close"], length=14)
    atr_d     = ta.atr(df_d["high"], df_d["low"], df_d["close"], length=14)

    above_200 = last_d["close"] > ema_200_d.iloc[-1] if ema_200_d is not None else None
    above_50  = last_d["close"] > ema_50_d.iloc[-1]  if ema_50_d  is not None else None
    weekly_dir = "BULL" if last_w["close"] > last_w["open"] else "BEAR"
    prev_weekly_dir = "BULL" if prev_w["close"] > prev_w["open"] else "BEAR"

    # Daily / weekly key levels for the chart
    pdh = float(prev_d["high"])     # previous day high
    pdl = float(prev_d["low"])      # previous day low
    pwh = float(prev_w["high"])     # previous week high
    pwl = float(prev_w["low"])      # previous week low

    # Score the macro setup (0–10)
    score = 0
    notes = []

    if above_200 is not None:
        score += 2; notes.append("Above 200D EMA" if above_200 else "Below 200D EMA")
    if above_50 is not None:
        score += 1; notes.append("Above 50D EMA" if above_50 else "Below 50D EMA")

    # Aligned weekly + EMA bias
    if (above_200 and weekly_dir == "BULL") or (not above_200 and weekly_dir == "BEAR"):
        score += 2; notes.append(f"Weekly aligned ({weekly_dir})")
    else:
        notes.append(f"Weekly conflict (weekly {weekly_dir})")

    # Two consecutive weekly closes same direction = strong trend
    if weekly_dir == prev_weekly_dir:
        score += 1; notes.append("2-week trend continuation")

    # ATR % of price — avoid dead/extreme volatility
    atr_pct = (atr_d.iloc[-1] / last_d["close"]) * 100 if atr_d is not None else 0
    if 0.3 <= atr_pct <= 3.0:
        score += 1; notes.append(f"ATR healthy ({atr_pct:.2f}%)")

    # Distance to PDH/PDL — close to a level = setup-rich
    last_close = last_d["close"]
    pip = 0.01 if "JPY" in asset else (1.0 if is_crypto or "XAU" in asset else 0.0001)
    dist_pdh = abs(last_close - pdh) / pip
    dist_pdl = abs(last_close - pdl) / pip
    near_pdh = dist_pdh < 50
    near_pdl = dist_pdl < 50
    if near_pdh or near_pdl:
        score += 2; notes.append(f"Near {'PDH' if near_pdh else 'PDL'}")

    # RSI sweet spot
    rsi_v = float(rsi_d.iloc[-1]) if rsi_d is not None else 50
    if 35 <= rsi_v <= 65:
        score += 1; notes.append(f"RSI {rsi_v:.0f} (room to run)")

    direction = "LONG" if above_200 else "SHORT"

    return {
        "asset":        asset,
        "is_crypto":    is_crypto,
        "score":        score,
        "direction":    direction,
        "weekly_dir":   weekly_dir,
        "prev_weekly":  prev_weekly_dir,
        "rsi_d":        rsi_v,
        "atr_pct":      atr_pct,
        "last_close":   last_close,
        "pdh":          pdh, "pdl": pdl,
        "pwh":          pwh, "pwl": pwl,
        "ema_200_d":    float(ema_200_d.iloc[-1]) if ema_200_d is not None else None,
        "notes":        notes,
    }


# ── Format the trader desk note ──────────────────────────────────────────────
def _format_brief(ranked: list[dict]) -> tuple[str, str]:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    top3 = ranked[:3]

    # Plain text for WhatsApp
    wa = (
        "🌅 *PRE-MARKET BRIEF*\n"
        "─────────────────────────────────────\n"
        f"Date: {ts} UTC | London opens in ~30 min\n\n"
        "Top 3 setups by macro confluence:\n\n"
    )
    tg = (
        "🌅 *PRE\\-MARKET BRIEF*\n"
        "─────────────────────────────\n"
        f"`{ts}` UTC · London in \\~30 min\n\n"
        "*Top 3 by macro confluence:*\n\n"
    )

    for i, r in enumerate(top3, 1):
        arrow = "🟢▲" if r["direction"] == "LONG" else "🔴▼"
        wa += (f"{i}. {arrow} *{r['asset']}* — score {r['score']}/10\n"
               f"   Last: {r['last_close']:.5f} | 200D EMA: {r['ema_200_d']:.5f}\n"
               f"   Weekly: {r['weekly_dir']} (prev {r['prev_weekly']}) | RSI: {r['rsi_d']:.0f}\n"
               f"   PDH: {r['pdh']:.5f} | PDL: {r['pdl']:.5f}\n"
               f"   PWH: {r['pwh']:.5f} | PWL: {r['pwl']:.5f}\n"
               f"   {' · '.join(r['notes'][:5])}\n\n")
        # Telegram MarkdownV2 — escape some chars to be safe
        tg += (f"*{i}\\. {arrow} {r['asset']}* — `{r['score']}/10`\n"
               f"   Last `{r['last_close']:.5f}` · 200D EMA `{r['ema_200_d']:.5f}`\n"
               f"   Weekly: `{r['weekly_dir']}` · prev `{r['prev_weekly']}` · RSI `{r['rsi_d']:.0f}`\n"
               f"   PDH `{r['pdh']:.5f}` · PDL `{r['pdl']:.5f}`\n"
               f"   PWH `{r['pwh']:.5f}` · PWL `{r['pwl']:.5f}`\n"
               f"   _{' · '.join(r['notes'][:5])}_\n\n")

    leader = top3[0]
    wa += f"🎯 *Plan:* monitoring {leader['asset']} {leader['direction']} on London open. Engine will fire SMC entry the moment confluence completes.\n"
    tg += f"🎯 *Plan:* monitor *{leader['asset']}* `{leader['direction']}` on London open\\."

    return wa, tg


# ── Public entrypoint — call this from the scheduler ─────────────────────────
def run_premarket_scan():
    logger.info("=" * 70)
    logger.info("PRE-MARKET SCAN — %s UTC", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))

    ranked = []
    for asset, is_crypto in PAIRS:
        ctx = _macro_context(asset, is_crypto)
        if ctx and ctx["score"] >= 5:
            ranked.append(ctx)

    if not ranked:
        logger.warning("No pre-market candidates scored ≥5 — skipping brief")
        return

    ranked.sort(key=lambda x: -x["score"])
    logger.info("Top 3 pre-market: %s",
                [(r["asset"], r["score"], r["direction"]) for r in ranked[:3]])

    # Send brief to both channels (don't run an SMC trade yet — engine handles that)
    wa, tg = _format_brief(ranked)
    send_whatsapp_alert(wa)
    send_telegram_alert(tg)

    # If the top pick ALREADY has a confluent SMC entry on 4H/15M right now,
    # fire it immediately as a high-priority London-open trade.
    leader = ranked[0]
    try:
        df_4h  = get_ohlcv(leader["asset"], "4H",  not leader["is_crypto"], 300)
        df_15m = get_ohlcv(leader["asset"], "15M", not leader["is_crypto"], 200)
        htf = analyse_4h(df_4h); htf["asset"] = leader["asset"]
        sr  = detect_sr(df_4h, htf["last_close"], leader["asset"], lookback=5)
        sr_near = nearest_levels(sr, htf["last_close"])
        entry = evaluate_entry(df_15m, df_4h, htf, sr)

        if not entry["tradeable"] or entry["confidence"] not in ("PREMIUM", "HIGH"):
            logger.info("Leader %s not at HIGH/PREMIUM yet — engine will catch it later",
                        leader["asset"])
            return

        risk = calc_risk(
            entry["entry_price"], entry["direction"], entry["atr_15m"],
            htf.get("atr_4h"), df_15m, sr, leader["asset"],
            matched_ob=entry.get("matched_ob"), size_factor=1.0,
        )
        if not risk.get("valid"):
            logger.info("Leader %s risk plan invalid — skipped", leader["asset"])
            return

        signal = {
            "asset":         leader["asset"],
            "session":       "Pre-market (Wall Street brief)",
            "confidence":    f"{entry['confidence']} · PRE-MARKET",
            "matched_ob":    entry.get("matched_ob"),
            "smc_4h_event":  entry.get("smc_4h_event"),
            "smc_4h_obs":    entry.get("smc_4h_obs", []),
            "smc_15m_obs":   entry.get("smc_15m_obs", []),
            **risk,
        }

        cal = {"action": "PROCEED", "next_event": "Monitor live calendar",
               "size_factor": 1.0, "warning": ""}

        pine = save_pine_script(signal, sr)
        push_to_chart(signal)

        wa_msg = format_signal(signal, cal, sr_near, entry["gate_4h"], pine)
        tg_msg = format_signal_telegram(signal, cal, sr_near, entry["gate_4h"], pine)
        send_whatsapp_alert(wa_msg)
        send_telegram_alert(tg_msg)

        if MT5_ENABLED:
            mt5_result = mt5_execute(signal)
            logger.info("Pre-market MT5: %s", mt5_result)

        log_signal({**signal, "pine_path": pine, "premarket": True}, "SENT", True)
        logger.info("✅ PRE-MARKET SIGNAL FIRED: %s %s %s",
                    leader["asset"], entry["direction"], entry["confidence"])

    except Exception as e:
        logger.warning("Pre-market signal eval error: %s", e)
