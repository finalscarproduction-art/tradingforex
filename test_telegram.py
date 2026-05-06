"""Send the LTF test signal to Telegram (and WhatsApp via broadcast)."""
import sys
from dotenv import load_dotenv
from datetime import datetime, timezone

load_dotenv()
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from telegram_notifier import send_telegram_alert
from whatsapp_notifier import send_whatsapp_alert

ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

tg_msg = (
    "🧪 *TEST — LTF EARLY ENTRY*\n"
    "─────────────────────────────\n"
    "🟢 *▲ LONG — GBP/USD*\n\n"
    "💰 *Entry:* `1.35140` (passive limit at OB)\n"
    "🛡 *SL:*    `1.35095` (4.5 pips)\n"
    "📦 *Anchor:* 1M Bullish OB `1.35112–1.35140`\n"
    "🎯 *Trigger:* 5M sweep of `1.35153` + 1M BOS\\_BULL\n"
    "📏 *200 EMA 4H:* above · slope: rising\n\n"
    "🎯 *TP1:* `1.35208` — R:R 1:1.5 — close 35%\n"
    "🎯 *TP2:* `1.35253` — R:R 1:2.5 — close 40%\n"
    "🎯 *TP3:* `1.35320` — R:R 1:4.0 — trail 25%\n\n"
    "🕐 *Session:* London\n"
    "⚡ *Confidence:* HIGH (6/7)\n"
    "🔄 *Mode:* PASSIVE\\_LIMIT\n"
    "✅ EMA200 ✓ | 4H bias ✓ | Sweep ✓ | 1M CHoCH ✓\n\n"
    "⏱ ~30 min earlier than 15M trigger\n"
    "🧪 TEST signal — engine running live\n"
    f"🕐 {ts} UTC"
)

# Plain WhatsApp version
wa_msg = tg_msg.replace("\\_", "_").replace("`", "")

print("Sending to Telegram + WhatsApp...\n")

tg_ok = send_telegram_alert(tg_msg)
wa_ok = send_whatsapp_alert(wa_msg)

print(f"  Telegram: {'✅ DELIVERED' if tg_ok else '❌ failed'}")
print(f"  WhatsApp: {'✅ sent (check status)' if wa_ok else '❌ failed'}")
