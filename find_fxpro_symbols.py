"""Search FxPro's full symbol list for gold/crypto variants."""
import sys
from dotenv import load_dotenv
load_dotenv()
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import MetaTrader5 as mt5
from mt5_executor import connect

if not connect():
    sys.exit(1)

print("Scanning all FxPro symbols for gold + crypto...\n")

all_syms = mt5.symbols_get()
keywords = ["XAU", "GOLD", "BTC", "ETH", "BITCOIN", "ETHEREUM"]

found = {}
for s in all_syms:
    for kw in keywords:
        if kw in s.name.upper():
            found.setdefault(kw, []).append(s.name)

for kw, names in found.items():
    print(f"  {kw:10s} → {names[:8]}")

if not found:
    print("  ⚠️ No gold/crypto symbols found at all on this account.")
    print("  FxPro demo accounts may not include CFDs by default.")
mt5.shutdown()
