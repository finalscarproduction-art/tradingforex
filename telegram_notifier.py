"""
Telegram Bot notifier — unlimited messaging, no 24h window like WhatsApp.

Setup:
  1. On Telegram, talk to @BotFather → /newbot → follow prompts → get TOKEN
  2. Start a chat with your new bot, send any message
  3. Get your chat_id by visiting:
       https://api.telegram.org/bot<TOKEN>/getUpdates
     Look for "chat":{"id": <NUMBER>} — that's your TELEGRAM_CHAT_ID
  4. Add to .env:
       TELEGRAM_BOT_TOKEN=...
       TELEGRAM_CHAT_ID=...
"""

import os
import requests
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def send_telegram_alert(message: str, parse_mode: str = "Markdown") -> bool:
    """Send a Telegram message. Returns True on success."""
    token   = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        logger.debug("Telegram not configured — skipping")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id":    chat_id,
        "text":       message,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            logger.info("Telegram sent ✓")
            return True
        logger.warning("Telegram failed: %d %s", resp.status_code, resp.text[:200])
        return False
    except Exception as e:
        logger.error("Telegram exception: %s", e)
        return False


# ── Message formatters (Telegram Markdown) ────────────────────────────────────

def format_signal_telegram(signal: dict, calendar: dict, sr_near: dict,
                            gate_4h: dict, pine_path: str = "") -> str:
    is_long = signal["direction"] == "LONG"
    arrow = "🟢 *▲ LONG*" if is_long else "🔴 *▼ SHORT*"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

    res = sr_near.get("nearest_resistance")
    sup = sr_near.get("nearest_support")
    res_line = (f"Res `{res['price']:.5f}` (+{res['distance_pips']:.0f}p, {res['touches']}t)"
                if res else "Res —")
    sup_line = (f"Sup `{sup['price']:.5f}` (-{sup['distance_pips']:.0f}p, {sup['touches']}t)"
                if sup else "Sup —")

    ob = signal.get("matched_ob")
    ob_line = (f"📦 *OB:* `{ob['low']:.5f}–{ob['high']:.5f}` ({ob['direction']})"
               if ob else "")

    bos = signal.get("smc_4h_event")
    bos_line = f"🔔 *4H Event:* `{bos['type']}` @ `{bos['price']:.5f}`" if bos else ""

    risk_notes = []
    if gate_4h.get("extended"):
        risk_notes.append(f"Extended {gate_4h['distance_pips']:.0f}p from 200 EMA")
    if calendar.get("warning"):
        risk_notes.append(calendar["warning"])
    risk_str = " · ".join(risk_notes) if risk_notes else "None"

    parts = [
        f"{arrow} *— {signal['asset']}*",
        "─────────────────────────────",
        f"💰 *Entry:* `{signal['entry']:.5f}`",
        f"🛡 *SL:*    `{signal['sl_price']:.5f}` ({signal['sl_pips']}p)",
        f"📏 *200EMA:* {gate_4h.get('price_vs_ema','?')} · slope: {gate_4h.get('ema_slope','?')}",
    ]
    if ob_line:  parts.append(ob_line)
    if bos_line: parts.append(bos_line)
    parts.append(f"🧱 {res_line} | {sup_line}")

    # FVG + confluence stack (v4)
    fvg = signal.get("fvg") or {}
    if fvg:
        fvg_aligned = "Y" if fvg.get("score_breakdown", {}).get("htf_aligned") else "N"
        fvg_mit     = "Y" if fvg.get("mitigated") else "N"
        parts.append(
            f"🟦 *FVG:* `{fvg.get('top',0):.5f}`–`{fvg.get('bottom',0):.5f}` "
            f"· mit `{fvg_mit}` · aligned `{fvg_aligned}` "
            f"· score `{fvg.get('score',0)}/10`"
        )
    confs = signal.get("confluences") or []
    if confs:
        parts.append(f"🧩 *Confluences:* `{' + '.join(confs)}`")
    parts.append("")
    parts.append(f"🎯 *TP1:* `{signal['tp1']:.5f}` — R:R {signal['rr1']} — close 35%")
    parts.append(f"🎯 *TP2:* `{signal['tp2']:.5f}` — R:R {signal['rr2']} — close 40%")
    parts.append(f"🎯 *TP3:* `{signal['tp3']:.5f}` — R:R {signal['rr3']} — trail 25%")
    parts.append("")
    parts.append(f"🕐 *Session:* {signal.get('session','-')}")
    parts.append(f"⚡ *Confidence:* `{signal['confidence']}`")
    parts.append(f"📅 *Next event:* {calendar.get('next_event','None')}")
    parts.append(f"⚠️ *Risk:* {risk_str}")
    if pine_path:
        parts.append(f"📊 `{os.path.basename(pine_path)}`")
    parts.append(f"🕐 {ts} UTC")

    return "\n".join(parts)


def format_session_pause_tg(reason: str, next_prime: str) -> str:
    return (f"🔕 *Engine paused*\n"
            f"Reason: {reason}\n"
            f"Next prime window: `{next_prime}`")


def format_blocked_tg(asset: str, reason: str, direction: str, entry: float) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    return (f"⚠️ *SIGNAL BLOCKED — {asset}*\n"
            f"Reason: {reason}\n"
            f"Direction: *{direction}* at `{entry}`\n"
            f"🕐 {ts} UTC")
