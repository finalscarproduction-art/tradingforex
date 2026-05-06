"""Send a test LTF early-entry signal showing sweep + CHoCH + OB format."""
import sys
from dotenv import load_dotenv
from datetime import datetime, timezone
from whatsapp_notifier import send_whatsapp_alert

load_dotenv()
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

msg = (
    "🧪 *TEST — LTF EARLY ENTRY*\n"
    "─────────────────────────────────────\n"
    "🟢 *▲ LONG — GBP/USD*\n"
    "💰 *Entry:*     1.35140  (passive limit at OB)\n"
    "🛡 *Stop Loss:* 1.35095  (4.5 pips)\n"
    "📦 *Anchor:*    1M Bullish OB (1.35112–1.35140)\n"
    "🎯 *Trigger:*   5M sweep of 1.35153 + 1M BOS_BULL\n"
    "📏 *200 EMA 4H:* above | Slope: rising\n\n"
    "🎯 *TP1:* 1.35208 — R:R 1:1.5 — close 35%\n"
    "🎯 *TP2:* 1.35253 — R:R 1:2.5 — close 40%\n"
    "🎯 *TP3:* 1.35320 — R:R 1:4.0 — trail 25%\n\n"
    "🕐 *Session:*    London session\n"
    "⚡ *Confidence:* HIGH (score 6/7)\n"
    "🔄 *Mode:*       PASSIVE_LIMIT\n"
    "📊 *Method:*     Module 6B — early entry vs 15M trigger\n"
    "✅ *Gates:*      EMA200 ✓ | 4H bias ✓ | Sweep ✓ | 1M CHoCH ✓\n\n"
    "⏱ Catches move ~30 min earlier than 15M trigger\n"
    "🧪 This is a TEST signal. Engine running live.\n"
    f"🕐 {ts} UTC"
)

print("Sending LTF test signal...\n")
print(msg)
print("\n" + "=" * 50)

if send_whatsapp_alert(msg):
    print("\n✅ SENT — Check WhatsApp at +447429260847")
else:
    print("\n❌ FAILED — check engine.log")
