"""
Quick walk-forward backtest on XAUUSD using the new SMC+200EMA logic.
Iterates through 15M bars from the last 7 days, evaluates entry at each bar,
then simulates SL/TP1 outcome from subsequent bars.
"""

import sys, logging
import pandas as pd
from asset_scanner    import get_ohlcv
from htf_analysis     import analyse_4h
from sr_detector      import detect_sr
from entry_trigger    import evaluate_entry
from risk_manager     import calculate as calc_risk

logging.basicConfig(level=logging.WARNING)
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PAIR = "GC=F"   # Gold spot via Yahoo
import requests
def fetch_yf(symbol, interval, days):
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                     params={"interval": interval, "range": f"{days}d"},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=15).json()
    res = r["chart"]["result"][0]
    ts  = res["timestamp"]
    q   = res["indicators"]["quote"][0]
    df = pd.DataFrame({"open": q["open"], "high": q["high"],
                       "low": q["low"],   "close": q["close"],
                       "volume": q.get("volume", [0]*len(ts))},
                      index=pd.to_datetime(ts, unit="s")).dropna(subset=["close"])
    df["volume"] = df["volume"].fillna(0)
    return df

print("Fetching XAUUSD data (7d 15M, 60d 4H)...")
df_15m_full = fetch_yf("GC=F", "15m", 7)
df_4h_full  = fetch_yf("GC=F", "60m", 60)

print(f"  15M bars: {len(df_15m_full)}")
print(f"  4H  bars: {len(df_4h_full)}")

trades = []
EVAL_EVERY = 4   # evaluate every 4 × 15M = 1 hour to mimic real cycle behavior
START = max(220, len(df_15m_full) - 7 * 96)   # start at last 7 days

print(f"\nWalking forward from index {START} to {len(df_15m_full) - 8}...")

for i in range(START, len(df_15m_full) - 8, EVAL_EVERY):
    df_15m = df_15m_full.iloc[:i].copy()
    bar_time = df_15m_full.index[i]

    # 4H slice ends at the same time
    df_4h = df_4h_full[df_4h_full.index <= bar_time].copy()
    if len(df_4h) < 220 or len(df_15m) < 50:
        continue

    htf = analyse_4h(df_4h); htf["asset"] = "XAU/USD"
    if "error" in htf:
        continue

    sr = detect_sr(df_4h, htf["last_close"], "XAU/USD", lookback=5)
    entry = evaluate_entry(df_15m, df_4h, htf, sr)

    if not entry["tradeable"]:
        continue

    risk = calc_risk(
        entry["entry_price"], entry["direction"], entry["atr_15m"],
        htf.get("atr_4h"), df_15m, sr, "XAU/USD",
        matched_ob=entry.get("matched_ob"), size_factor=1.0,
    )
    if not risk.get("valid"):
        continue

    # ── Simulate outcome on next 60 × 15M bars (~15 hours) ───────────────────
    future = df_15m_full.iloc[i+1 : i+1+60]
    outcome = None
    bars_held = 0

    for j, (ts, bar) in enumerate(future.iterrows(), 1):
        if entry["direction"] == "LONG":
            if bar["low"]  <= risk["sl_price"]: outcome = "LOSS"; bars_held = j; break
            if bar["high"] >= risk["tp1"]:      outcome = "WIN";  bars_held = j; break
        else:
            if bar["high"] >= risk["sl_price"]: outcome = "LOSS"; bars_held = j; break
            if bar["low"]  <= risk["tp1"]:      outcome = "WIN";  bars_held = j; break

    if outcome is None:
        outcome = "OPEN"

    trades.append({
        "time":       bar_time.strftime("%Y-%m-%d %H:%M"),
        "direction":  entry["direction"],
        "confidence": entry["confidence"],
        "entry":      risk["entry"],
        "sl":         risk["sl_price"],
        "tp1":        risk["tp1"],
        "outcome":    outcome,
        "bars":       bars_held,
    })

# ── Report ──────────────────────────────────────────────────────────────────
print(f"\n{'='*70}")
print(f"BACKTEST RESULTS — XAUUSD (Gold) · 7-day walk-forward · 15M trigger")
print(f"{'='*70}")

if not trades:
    print("No qualifying setups in the last 7 days. SMC gating very strict on Gold.")
    sys.exit(0)

w = sum(1 for t in trades if t["outcome"] == "WIN")
l = sum(1 for t in trades if t["outcome"] == "LOSS")
o = sum(1 for t in trades if t["outcome"] == "OPEN")
total_closed = w + l
wr = (w / total_closed * 100) if total_closed else 0

print(f"\nTotal signals fired: {len(trades)}")
print(f"Wins:    {w}")
print(f"Losses:  {l}")
print(f"Still open / not hit either target: {o}")
print(f"Win rate (closed only): {wr:.1f}% ({w}/{total_closed})")

# Avg P&L assuming 1:1.5 R:R
if total_closed:
    expectancy_R = (w * 1.5 - l * 1.0) / total_closed
    print(f"Expectancy: {expectancy_R:+.2f}R per trade")

print(f"\n{'time':<18}{'dir':<6}{'conf':<10}{'entry':>10}{'sl':>10}{'tp1':>10}{'result':>8}")
for t in trades:
    print(f"{t['time']:<18}{t['direction']:<6}{t['confidence']:<10}"
          f"{t['entry']:>10.3f}{t['sl']:>10.3f}{t['tp1']:>10.3f}{t['outcome']:>8}")
