"""
Manual SMC scan — runs the full pipeline on every eligible pair and reports
which ones currently meet all SMC criteria. Pushes markers for the best one.
"""

import sys, logging, json
from session_gate     import get_session_status
from asset_scanner    import get_ohlcv
from htf_analysis     import analyse_4h
from sr_detector      import detect_sr, nearest_levels
from entry_trigger    import evaluate_entry
from risk_manager     import calculate as calc_risk
from chart_marker     import push_to_chart

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PAIRS = [
    ("EUR/USD", False), ("GBP/USD", False), ("USD/JPY", False),
    ("AUD/USD", False), ("USD/CAD", False), ("NZD/USD", False),
    ("EUR/GBP", False), ("GBP/JPY", False), ("EUR/JPY", False),
    ("USD/CHF", False), ("AUD/JPY", False),
    ("BTC/USDT", True), ("ETH/USDT", True),
]

results = []
for asset, is_crypto in PAIRS:
    is_forex = not is_crypto
    df_4h  = get_ohlcv(asset, "4H", is_forex, limit=300)
    df_15m = get_ohlcv(asset, "15M", is_forex, limit=200)
    if df_4h is None or df_15m is None:
        continue

    htf = analyse_4h(df_4h); htf["asset"] = asset
    sr  = detect_sr(df_4h, htf["last_close"], asset, lookback=5)
    entry = evaluate_entry(df_15m, df_4h, htf, sr)

    if not entry["tradeable"]:
        print(f"  {asset:10s}  {entry['confidence']:9s}  reason={entry.get('reason','')}")
        continue

    risk = calc_risk(
        entry["entry_price"], entry["direction"], entry["atr_15m"],
        htf.get("atr_4h"), df_15m, sr, asset,
        matched_ob=entry.get("matched_ob"),
    )
    if not risk.get("valid"):
        print(f"  {asset:10s}  RISK_FAIL  {risk.get('reason')}")
        continue

    print(f"✓ {asset:10s}  {entry['direction']:5s}  {entry['confidence']:8s}  "
          f"{entry['conditions_met']}/7  entry={risk['entry']}  SL={risk['sl_price']} ({risk['sl_pips']}p)  "
          f"TP1={risk['tp1']} TP2={risk['tp2']} TP3={risk['tp3']}  RR2={risk['rr2']}")

    results.append({
        "asset":        asset,
        "session":      "Manual scan",
        "confidence":   entry["confidence"],
        "matched_ob":   entry.get("matched_ob"),
        "smc_4h_event": entry.get("smc_4h_event"),
        "smc_4h_obs":   entry.get("smc_4h_obs", []),
        "smc_15m_obs":  entry.get("smc_15m_obs", []),
        **risk,
    })

print(f"\n=== {len(results)} qualifying SMC setups ===")
results.sort(key=lambda x: {"PREMIUM":3,"HIGH":2,"MEDIUM":1}.get(x["confidence"],0), reverse=True)

if results:
    best = results[0]
    print(f"\n→ Pushing to chart: {best['asset']} {best['direction']} ({best['confidence']})")
    shot = push_to_chart(best)
    if shot:
        print(f"  Screenshot: {shot}")
else:
    print("No qualifying setups right now. Engine is being strict — exactly as designed.")
