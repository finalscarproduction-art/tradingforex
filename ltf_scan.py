"""
Manual LTF early-entry scan — checks every pair for sweep + 1M CHoCH setups.
"""

import sys, logging
from asset_scanner    import get_ohlcv
from htf_analysis     import analyse_4h
from ltf_trigger      import evaluate_ltf_entry

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

print("\n" + "=" * 80)
print("LTF EARLY-ENTRY SCAN (1M CHoCH after 5M sweep)")
print("=" * 80 + "\n")

found = 0
for asset, is_crypto in PAIRS:
    is_forex = not is_crypto
    df_4h  = get_ohlcv(asset, "4H", is_forex, limit=300)
    df_15m = get_ohlcv(asset, "15M", is_forex, limit=200)
    df_5m  = get_ohlcv(asset, "5M",  is_forex, limit=200)
    df_1m  = get_ohlcv(asset, "1M",  is_forex, limit=300)
    if df_4h is None or df_15m is None or df_5m is None or df_1m is None:
        print(f"  {asset:10s}  data missing")
        continue

    htf = analyse_4h(df_4h)
    direction = "LONG" if htf["trend"] == "BULLISH" else \
                "SHORT" if htf["trend"] == "BEARISH" else None
    if not direction:
        print(f"  {asset:10s}  {htf['trend']} on 4H — skipped")
        continue

    ltf = evaluate_ltf_entry(df_1m, df_5m, df_15m, direction, asset)
    if ltf["tradeable"]:
        found += 1
        print(f"\n✅ {asset}  {direction}  {ltf['confidence']}  score={ltf['score']}")
        print(f"   Entry: {ltf['entry_price']:.5f}")
        print(f"   SL:    {ltf['sl_price']:.5f}  ({ltf['sl_pips']} pips)")
        print(f"   Sweep: {ltf['sweep']['direction']} | swept={ltf['sweep']['swept_level']:.5f} wick={ltf['sweep']['wick_extreme']:.5f}")
        print(f"   1M:    {ltf['choch']['type']} @ {ltf['choch']['price']:.5f}")
        print(f"   OB:    {ltf['ob']['low']:.5f}–{ltf['ob']['high']:.5f}")
        print(f"   Reasons: {' | '.join(ltf['reasons'])}")
    else:
        print(f"  {asset:10s}  {direction:5s}  {ltf['confidence']:8s}  {ltf.get('reason','')}")

print(f"\n=== {found} LTF early entries found ===\n")
