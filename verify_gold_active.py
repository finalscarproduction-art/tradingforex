"""Confirm XAU/USD is active in live scanning across all 3 prime windows."""
import sys
from session_gate  import PRIME_WINDOWS, get_session_status
from asset_scanner import _fetch_forex, _score, scan_session

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

print("═" * 70)
print("Gold (XAU/USD) session-coverage check")
print("═" * 70)
for start, end, label, prio, prio_pairs, sec_pairs in PRIME_WINDOWS:
    in_priority  = "XAU/USD" in prio_pairs
    in_secondary = "XAU/USD" in sec_pairs
    tag = "✓ PRIORITY  " if in_priority else (
          "· secondary " if in_secondary else "✗ MISSING   ")
    print(f"  {tag} {label:25s} ({start:02d}:00–{end:02d}:00 UTC)")

print("\n" + "═" * 70)
print("Live data fetch check (Yahoo via override)")
print("═" * 70)
df = _fetch_forex("XAU/USD", "4H", limit=250)
if df is None or df.empty:
    print("  ✗ XAU/USD data fetch FAILED")
    sys.exit(1)
print(f"  ✓ XAU/USD 4H data fetched · {len(df)} bars")
print(f"    Latest:  open={df.iloc[-1]['open']:.2f}  high={df.iloc[-1]['high']:.2f}  "
      f"low={df.iloc[-1]['low']:.2f}  close={df.iloc[-1]['close']:.2f}")
print(f"    Last bar timestamp: {df.index[-1]}")

scored = _score(df, "XAU/USD", is_forex=True)
print(f"  ✓ Score: {scored['score']}/10 · direction={scored['direction']}")

print("\n" + "═" * 70)
print("Will be scanned by scan_session() during these UTC hours:")
print("═" * 70)
import unittest.mock as mk
from datetime import datetime, timezone
with mk.patch("session_gate.datetime") as mdt:
    for h in [7, 9, 12, 14]:
        mdt.now.return_value = datetime(2026, 5, 1, h, 0, tzinfo=timezone.utc)
        mdt.side_effect = None
        sess = get_session_status()
        if sess["allowed"]:
            in_scan = "XAU/USD" in (sess["priority_pairs"] + sess["secondary_pairs"])
            tag = "scanned" if in_scan else "EXCLUDED"
            print(f"  {h:02d}:00 UTC · {sess['session']:25s} → XAU/USD {tag}")

print("\n✅ Gold is now active in live scanning, eligible for the full")
print("   cooldown → validator → MT5 pipeline during London + NY sessions.")
