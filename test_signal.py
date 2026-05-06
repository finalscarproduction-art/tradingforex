"""Send a real-formatted SMC signal to WhatsApp as a delivery test."""
import os
import sys
from dotenv import load_dotenv
from datetime import datetime, timezone
from whatsapp_notifier import send_whatsapp_alert

load_dotenv()
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

msg = (
    "🧪 *TEST SIGNAL — SMC v3*\n"
    "─────────────────────────────────────\n"
    "🟢 *▲ LONG — EUR/USD*\n"
    "💰 *Entry:*     1.17495\n"
    "🛡 *Stop Loss:* 1.17375 (12 pips)\n"
    "📦 *Anchored:*  4H Bullish Order Block (1.17440–1.17536)\n"
    "📏 *200 EMA:*   above | Slope: rising\n"
    "🔔 *4H Event:*  BOS_BULL @ 1.17495\n"
    "🧱 *Near S/R:*  Res 1.17600 (+10p, 3t) | Sup 1.17440 (-5p, 4t)\n\n"
    "🎯 *TP1:* 1.17675 — R:R 1:1.5 — close 35%\n"
    "🎯 *TP2:* 1.17795 — R:R 1:2.5 — close 40%\n"
    "🎯 *TP3:* 1.17900 — R:R 1:3.4 — trail 25%\n\n"
    "🕐 *Session:*    London session\n"
    "⚡ *Confidence:* HIGH (6/7 conditions)\n"
    "📅 *Next event:* None within 6h\n"
    "✅ *SMC Gates:*  EMA200 ✓ | BOS ✓ | OB Touch ✓\n\n"
    f"🧪 This is a TEST. Engine is live.\n"
    f"🕐 {ts} UTC"
)

print("Sending test signal to WhatsApp...")
print(msg)
print("\n" + "=" * 50)

success = send_whatsapp_alert(msg)
if success:
    print("\n✅ SENT — Check WhatsApp at +447429260847")
else:
    print("\n❌ FAILED — see logs")
