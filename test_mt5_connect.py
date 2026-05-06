"""Verify FxPro MT5 demo login + show account info + symbol availability."""
import sys
from dotenv import load_dotenv
load_dotenv()

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from mt5_executor import connect, MT5_AVAILABLE
from mt5_config   import SYMBOL_MAP, MT5_MODE
import MetaTrader5 as mt5

if not MT5_AVAILABLE:
    print("❌ MetaTrader5 package missing. Run: pip install MetaTrader5")
    sys.exit(1)

print(f"Mode: {MT5_MODE}")
print("Connecting to FxPro MT5...")

if not connect():
    err = mt5.last_error()
    print(f"\n❌ Connection failed: {err}")
    print("\nCheck:")
    print("  1. MT5 terminal is OPEN on this Windows machine")
    print("  2. Tools → Options → Expert Advisors → 'Allow algorithmic trading' is ✅")
    print("  3. Login/password/server in .env match exactly")
    sys.exit(1)

info = mt5.account_info()
print(f"\n✅ Connected — server: {info.server}")
print(f"   Account:    {info.login}")
print(f"   Name:       {info.name}")
print(f"   Balance:    {info.balance:.2f} {info.currency}")
print(f"   Equity:     {info.equity:.2f} {info.currency}")
print(f"   Leverage:   1:{info.leverage}")
print(f"   Trade mode: {'DEMO' if info.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO else 'LIVE'}")

print("\n── Symbol availability check ──")
ok, missing = [], []
for engine_pair, broker_sym in SYMBOL_MAP.items():
    sel = mt5.symbol_select(broker_sym, True)
    if sel and mt5.symbol_info(broker_sym):
        ok.append(broker_sym)
    else:
        missing.append((engine_pair, broker_sym))

print(f"\n✅ Available ({len(ok)}): {', '.join(ok)}")
if missing:
    print(f"\n⚠️  Missing/wrong symbol on FxPro:")
    for ep, bs in missing:
        # Suggest variants
        for v in [bs+"r", bs+"x", bs+".r", bs+"m", bs+".m"]:
            if mt5.symbol_select(v, True) and mt5.symbol_info(v):
                print(f"   {ep}: try {v} instead of {bs}")
                break
        else:
            print(f"   {ep}: no variant found for {bs}")

mt5.shutdown()
print("\nDone.")
