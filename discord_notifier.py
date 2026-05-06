"""
Discord webhook notifier — fallback / parallel alerting to WhatsApp.

Supports rich embeds: color-coded sides, structured fields, timestamp.
Setup: Discord server → Edit Channel → Integrations → Webhooks → New Webhook
       Copy URL → paste into .env as DISCORD_WEBHOOK_URL.
"""

import os
import requests
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL", "")
COLOR_LONG  = 0x00E676    # green
COLOR_SHORT = 0xFF1744    # red
COLOR_INFO  = 0xFFD700    # gold
COLOR_BLOCK = 0x9E9E9E    # grey


def send_discord_alert(content: str = "", embed: dict | None = None) -> bool:
    """Send to Discord webhook. Returns True on 2xx."""
    if not DISCORD_WEBHOOK:
        logger.debug("Discord webhook not configured")
        return False
    payload = {}
    if content:
        payload["content"] = content
    if embed:
        payload["embeds"] = [embed]

    try:
        resp = requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        if 200 <= resp.status_code < 300:
            logger.info("Discord sent ✓ (status %d)", resp.status_code)
            return True
        logger.warning("Discord failed: %d %s", resp.status_code, resp.text[:200])
        return False
    except Exception as e:
        logger.error("Discord exception: %s", e)
        return False


def signal_embed(signal: dict, calendar: dict, sr_near: dict, gate_4h: dict,
                 pine_path: str = "") -> dict:
    """Build a rich embed for a confirmed trade signal."""
    is_long = signal["direction"] == "LONG"
    arrow   = "▲ LONG" if is_long else "▼ SHORT"
    color   = COLOR_LONG if is_long else COLOR_SHORT

    res = sr_near.get("nearest_resistance")
    sup = sr_near.get("nearest_support")
    res_str = (f"{res['price']:.5f}  (+{res['distance_pips']:.0f}p, "
               f"{res['touches']}t)" if res else "—")
    sup_str = (f"{sup['price']:.5f}  (-{sup['distance_pips']:.0f}p, "
               f"{sup['touches']}t)" if sup else "—")

    ob = signal.get("matched_ob")
    ob_str = (f"{ob['low']:.5f}–{ob['high']:.5f}  ({ob['direction']})"
              if ob else "—")

    bos = signal.get("smc_4h_event")
    bos_str = f"{bos['type']} @ {bos['price']:.5f}" if bos else "—"

    risk_notes = []
    if gate_4h.get("extended"):
        risk_notes.append(f"Extended {gate_4h['distance_pips']:.0f} pips from 200 EMA")
    if calendar.get("warning"):
        risk_notes.append(calendar["warning"])
    risk_str = " · ".join(risk_notes) if risk_notes else "None"

    embed = {
        "title":       f"{arrow} — {signal['asset']}",
        "description": f"**Confidence:** `{signal['confidence']}`  ·  "
                       f"**Session:** {signal.get('session','-')}",
        "color":       color,
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "fields": [
            {"name": "💰 Entry",     "value": f"`{signal['entry']:.5f}`",        "inline": True},
            {"name": "🛡 Stop Loss", "value": f"`{signal['sl_price']:.5f}` ({signal['sl_pips']}p)", "inline": True},
            {"name": "📏 200 EMA",   "value": f"{gate_4h.get('price_vs_ema','?')} · {gate_4h.get('ema_slope','?')}", "inline": True},
            {"name": "📦 Order Block",  "value": f"`{ob_str}`",  "inline": False},
            {"name": "🔔 4H Structure", "value": f"`{bos_str}`", "inline": False},
            {"name": "🧱 S/R", "value": f"**Res:** {res_str}\n**Sup:** {sup_str}", "inline": False},
            {"name": "🎯 TP1", "value": f"`{signal['tp1']:.5f}`  ·  R:R {signal['rr1']}  ·  35% out",
             "inline": False},
            {"name": "🎯 TP2", "value": f"`{signal['tp2']:.5f}`  ·  R:R {signal['rr2']}  ·  40% out",
             "inline": False},
            {"name": "🎯 TP3", "value": f"`{signal['tp3']:.5f}`  ·  R:R {signal['rr3']}  ·  trail 25%",
             "inline": False},
            {"name": "📅 Next event", "value": calendar.get("next_event", "None"), "inline": False},
            {"name": "⚠️ Risk note",  "value": risk_str, "inline": False},
        ],
        "footer": {"text": f"Pine: {os.path.basename(pine_path)}" if pine_path else "Trading Engine v3"},
    }
    return embed


def session_pause_embed(reason: str, next_prime: str) -> dict:
    return {
        "title":       "🔕 Engine paused",
        "description": f"**Reason:** {reason}\n**Next prime window:** {next_prime}",
        "color":       COLOR_BLOCK,
        "timestamp":   datetime.now(timezone.utc).isoformat(),
    }


def blocked_embed(asset: str, reason: str, direction: str, entry: float) -> dict:
    return {
        "title":       f"⚠️ Signal Blocked — {asset}",
        "description": f"**Reason:** {reason}\nDirection was: **{direction}** at `{entry}`\n"
                       f"Will re-scan automatically.",
        "color":       0xFFA000,
        "timestamp":   datetime.now(timezone.utc).isoformat(),
    }


def summary_embed(sent: int, premium: int, high: int, med: int, note: str) -> dict:
    return {
        "title":       "📈 Daily Summary",
        "color":       COLOR_INFO,
        "fields": [
            {"name": "Total sent",   "value": str(sent),    "inline": True},
            {"name": "PREMIUM",      "value": str(premium), "inline": True},
            {"name": "HIGH",         "value": str(high),    "inline": True},
            {"name": "MEDIUM",       "value": str(med),     "inline": True},
            {"name": "Notes",        "value": note,         "inline": False},
        ],
        "timestamp":   datetime.now(timezone.utc).isoformat(),
    }
