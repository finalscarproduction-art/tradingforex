"""
Module 9 — Twilio WhatsApp sender + refined message templates.
"""

from twilio.rest import Client
from datetime import datetime, timezone
import os
import logging

logger = logging.getLogger(__name__)


def send_whatsapp_alert(message: str) -> bool:
    try:
        client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
        msg = client.messages.create(
            body=message,
            from_=os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886"),
            to=os.getenv("ALERT_WHATSAPP_TO"),
        )
        logger.info("WhatsApp sent: %s", msg.sid)
        return True
    except Exception as e:
        logger.error("WhatsApp failed: %s", e)
        return False


def format_signal(signal: dict, calendar: dict, sr_near: dict, gate_4h: dict, pine_path: str) -> str:
    is_long = signal["direction"] == "LONG"
    icon = "🟢 *▲ LONG" if is_long else "🔴 *▼ SHORT"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

    res = sr_near.get("nearest_resistance")
    sup = sr_near.get("nearest_support")
    res_line = f"Res {res['price']:.5f} (+{res['distance_pips']:.0f}p, {res['touches']}t)" if res else "—"
    sup_line = f"Sup {sup['price']:.5f} (-{sup['distance_pips']:.0f}p, {sup['touches']}t)" if sup else "—"

    risk_note = []
    if gate_4h.get("extended"):
        risk_note.append(f"Extended {gate_4h['distance_pips']:.0f} pips from 200 EMA")
    if calendar.get("warning"):
        risk_note.append(calendar["warning"])
    risk_str = " | ".join(risk_note) if risk_note else "None"

    # ── FVG line (v4 — confluence scorer) ──────────────────────────────
    fvg = signal.get("fvg") or {}
    if fvg:
        fvg_aligned = "Y" if fvg.get("score_breakdown", {}).get("htf_aligned") else "N"
        fvg_mit     = "Y" if fvg.get("mitigated") else "N"
        fvg_line = (f"🟦 *FVG:* {fvg.get('top', 0):.5f}–{fvg.get('bottom', 0):.5f} "
                    f"| mitigated {fvg_mit} | aligned {fvg_aligned} "
                    f"(score {fvg.get('score', 0)}/10)\n")
    else:
        fvg_line = ""

    # ── Confluence stack line ──────────────────────────────────────────
    confs = signal.get("confluences") or []
    conf_line = f"🧩 *Confluences:* {' + '.join(confs)}\n" if confs else ""

    return (
        f"{icon} — {signal['asset']}*\n"
        f"─────────────────────────────────────\n"
        f"💰 *Entry:*     {signal['entry']:.5f}\n"
        f"🛡 *Stop Loss:* {signal['sl_price']:.5f} ({signal['sl_pips']} pips)\n"
        f"📏 *200 EMA:*   {gate_4h.get('price_vs_ema','?')} | Slope: {gate_4h.get('ema_slope','?')}\n"
        f"🧱 *Near S/R:*  {res_line} | {sup_line}\n"
        f"{fvg_line}"
        f"{conf_line}\n"
        f"🎯 *TP1:* {signal['tp1']:.5f} — R:R {signal['rr1']} — close 35%\n"
        f"🎯 *TP2:* {signal['tp2']:.5f} — R:R {signal['rr2']} — close 40%\n"
        f"🎯 *TP3:* {signal['tp3']:.5f} — R:R {signal['rr3']} — trail 25%\n\n"
        f"🕐 *Session:*    {signal.get('session','')}\n"
        f"⚡ *Confidence:* {signal['confidence']}\n"
        f"📅 *Next event:* {calendar.get('next_event','None')}\n"
        f"⚠️ *Risk note:*  {risk_str}\n\n"
        f"📊 *TradingView script:* {os.path.basename(pine_path)}\n"
        f"🕐 {ts} UTC"
    )


def format_session_pause(reason: str, next_prime: str) -> str:
    return f"🔕 *Engine paused* — {reason}.\nNext prime window: {next_prime}."


def format_blocked(asset: str, reason: str, direction: str, entry: float) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    return (
        f"⚠️ *SIGNAL BLOCKED — {asset}*\n"
        f"Reason: {reason}\nDirection was: {direction} at {entry}\n"
        f"Will re-scan automatically. 🕐 {ts} UTC"
    )


def format_no_setup(best_score: float, best_pair: str) -> str:
    return (f"📊 *No valid setup found* — best score was "
            f"{best_score:.1f}/10 on {best_pair}. Re-scanning at next candle close.")


def format_daily_summary(sent: int, premium: int, high: int, med: int, note: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    return (
        f"📈 *DAILY SIGNAL SUMMARY*\n"
        f"Total sent: {sent}\n"
        f"Premium: {premium} | High: {high} | Medium: {med}\n"
        f"Market: {note}\n🕐 {ts} UTC"
    )
