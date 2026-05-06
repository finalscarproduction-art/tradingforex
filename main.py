"""
Trading Signal Engine v3 — Orchestrator
Runs every 15 minutes. Modules in strict order: 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9.
"""

import schedule
import time
import sys
import logging
from datetime import datetime, timezone

from config import SCAN_INTERVAL_MINUTES, MIN_ASSET_SCORE, LOG_FILE
from session_gate     import get_session_status
from asset_scanner    import scan_session, get_ohlcv
from sr_detector      import detect_sr, nearest_levels
from calendar_check   import check_calendar
from htf_analysis     import analyse_4h
from entry_trigger    import evaluate_entry
from ltf_trigger       import evaluate_ltf_entry
from risk_manager     import calculate as calc_risk
from pine_generator   import save_pine_script
from chart_marker     import push_to_chart
from whatsapp_notifier import (
    send_whatsapp_alert, format_signal,
    format_session_pause, format_blocked,
    format_no_setup, format_daily_summary,
)
from telegram_notifier import (
    send_telegram_alert, format_signal_telegram,
    format_session_pause_tg, format_blocked_tg,
)

# MT5 execution (optional — only fires if MetaTrader5 package + creds installed)
try:
    from mt5_executor import execute_signal as mt5_execute, connect as mt5_connect
    MT5_ENABLED = True
except ImportError:
    MT5_ENABLED = False


def broadcast(whatsapp_msg: str, telegram_msg: str) -> bool:
    """Fire both channels in parallel. Returns True if EITHER succeeded."""
    wa_ok = send_whatsapp_alert(whatsapp_msg)
    tg_ok = send_telegram_alert(telegram_msg)
    return wa_ok or tg_ok
from signal_logger    import log_signal, daily_stats

# ── Logging (UTF-8 safe on Windows) ──────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler(LOG_FILE, encoding="utf-8"),
              logging.FileHandler((__import__("pathlib").Path("logs").mkdir(exist_ok=True) or __import__("pathlib").Path("logs") / f"dryrun_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.log"), encoding="utf-8")],
)
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logger = logging.getLogger("main")

# Track last session-pause reason so we don't spam WhatsApp
_last_pause_reason = None


def _refresh_outcomes_safely() -> int:
    """
    Per-cycle performance refresh. Lightweight by design:
      - Bails immediately if there are no OPEN signals to refresh
      - Catches every exception so a flaky MT5 connection cannot crash the cycle
    Returns the number of outcomes updated this cycle (0 if nothing to do).
    """
    try:
        from performance_tracker import update_outcomes
        return update_outcomes()
    except Exception as e:
        logger.debug("Outcome refresh skipped (non-fatal): %s", e)
        return 0


def run_cycle() -> None:
    global _last_pause_reason
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    logger.info("=" * 70)
    logger.info("CYCLE START — %s UTC", now_str)

    # Per-cycle outcome refresh — never crashes, no-op if no open trades
    updated = _refresh_outcomes_safely()
    if updated:
        logger.info("Performance: %d outcome(s) updated this cycle", updated)

    # ── Module 0: Session Gate ────────────────────────────────────────────────
    session = get_session_status()
    if not session["allowed"]:
        logger.info("Session gate BLOCKED: %s", session["reason"])
        if _last_pause_reason != session["reason"]:
            broadcast(
                format_session_pause(session["reason"], session["next_prime_in"]),
                format_session_pause_tg(session["reason"], session["next_prime_in"]),
            )
            _last_pause_reason = session["reason"]
        log_signal({"reason": session["reason"], "next_prime": session["next_prime_in"]},
                   "SESSION_BLOCKED", False)
        return
    _last_pause_reason = None
    logger.info("Session: %s [%s priority]", session["session"], session["priority"])

    # ── Module 1: Asset Selection ─────────────────────────────────────────────
    ranked = scan_session(session)
    if not ranked:
        logger.info("No assets passed filters — scoring all returned 0")
        return
    if ranked[0]["score"] < MIN_ASSET_SCORE:
        send_whatsapp_alert(format_no_setup(ranked[0]["score"], ranked[0]["asset"]))
        log_signal({"asset": ranked[0]["asset"], "score": ranked[0]["score"]},
                   "BELOW_THRESHOLD", True)
        return

    top = ranked[0]
    asset, is_forex, direction = top["asset"], top["type"] == "forex", top["direction"]
    logger.info("Top: %s | Score %d/10 | %s | priority=%s",
                asset, top["score"], direction, top["is_priority"])

    # ── Module 4: Calendar (early — fail fast) ────────────────────────────────
    cal = check_calendar(asset)
    if cal["action"] == "BLOCK":
        broadcast(
            format_blocked(asset, cal["reason"], direction, top["last_close"]),
            format_blocked_tg(asset, cal["reason"], direction, top["last_close"]),
        )
        log_signal({"asset": asset, "direction": direction,
                    "reason": cal["reason"], "confidence": "BLOCKED"},
                   "CAL_BLOCKED", True)
        return

    # ── Modules 2 & 5: 4H analysis ────────────────────────────────────────────
    df_4h = get_ohlcv(asset, "4H", is_forex, limit=300)
    htf = analyse_4h(df_4h)
    htf["asset"] = asset
    if "error" in htf:
        logger.warning("4H analysis failed: %s", htf["error"])
        return
    logger.info("4H: trend=%s | stack=%s | structure=%s | momentum=%s",
                htf["trend"], htf["ema_stack"], htf["structure"], htf["momentum"])

    # ── Module 3: S/R detection ───────────────────────────────────────────────
    sr_levels = detect_sr(df_4h, htf["last_close"], asset, lookback=5)
    sr_near = nearest_levels(sr_levels, htf["last_close"])
    logger.info("S/R: %d levels in range. Nearest res=%s sup=%s",
                len(sr_levels),
                sr_near["nearest_resistance"]["price"] if sr_near["nearest_resistance"] else None,
                sr_near["nearest_support"]["price"]    if sr_near["nearest_support"]    else None)

    # ── Module 6: 15M Entry Trigger (SMC-gated) ───────────────────────────────
    df_15m = get_ohlcv(asset, "15M", is_forex, limit=200)
    entry  = evaluate_entry(df_15m, df_4h, htf, sr_levels)

    # ── Module 6B: LTF Early-Entry (1M/5M sweep + CHoCH) ──────────────────────
    # Only if 4H bias is clear (HTF trend BULLISH/BEARISH). LTF can fire
    # BEFORE the 15M trigger — catching moves before they reach 200 EMA.
    ltf_entry = None
    if htf["trend"] in ("BULLISH", "BEARISH"):
        try:
            df_5m = get_ohlcv(asset, "5M", is_forex, limit=200)
            df_1m = get_ohlcv(asset, "1M", is_forex, limit=300)
            ltf_direction = "LONG" if htf["trend"] == "BULLISH" else "SHORT"
            ltf_entry = evaluate_ltf_entry(df_1m, df_5m, df_15m,
                                           ltf_direction, asset)
            if ltf_entry["tradeable"]:
                logger.info(
                    "LTF EARLY ENTRY [%s] %s: entry=%.5f SL=%.5f (%.1fp) — score=%d (%s)",
                    ltf_entry["confidence"], ltf_direction,
                    ltf_entry["entry_price"], ltf_entry["sl_price"],
                    ltf_entry["sl_pips"], ltf_entry["score"],
                    " | ".join(ltf_entry["reasons"])
                )
            else:
                logger.info("LTF: %s", ltf_entry.get("reason", "no early entry"))
        except Exception as e:
            logger.warning("LTF trigger error: %s", e)

    # Prefer LTF early entry if it qualifies — catches moves earlier with tighter SL
    use_ltf = ltf_entry and ltf_entry.get("tradeable")
    if not use_ltf and not entry["tradeable"]:
        reason = ltf_entry.get("reason", "") if ltf_entry else entry.get("reason", "")
        logger.info("Not tradeable: 15M=%s, LTF=%s",
                    entry["confidence"],
                    ltf_entry["confidence"] if ltf_entry else "N/A")
        log_signal({"asset": asset, "direction": direction,
                    "confidence": entry["confidence"],
                    "reason": reason},
                   "NO_TRADE", False)
        return

    # ── Module 7: Risk + 3 TPs ────────────────────────────────────────────────
    if use_ltf:
        # LTF entry — use sweep-based SL directly, compute TPs from R:R
        ltf_entry_price = ltf_entry["entry_price"]
        ltf_sl          = ltf_entry["sl_price"]
        risk_pts = abs(ltf_entry_price - ltf_sl)
        if entry["direction"] == "LONG":
            tp1 = ltf_entry_price + 1.5 * risk_pts
            tp2 = ltf_entry_price + 2.5 * risk_pts
            tp3 = ltf_entry_price + 4.0 * risk_pts
        else:
            tp1 = ltf_entry_price - 1.5 * risk_pts
            tp2 = ltf_entry_price - 2.5 * risk_pts
            tp3 = ltf_entry_price - 4.0 * risk_pts

        risk = {
            "valid":       True,
            "entry":       round(ltf_entry_price, 6),
            "direction":   entry["direction"],
            "sl_price":    round(ltf_sl, 6),
            "sl_pips":     ltf_entry["sl_pips"],
            "sl_anchor":   "LTF_Sweep",
            "tp1":         round(tp1, 6),
            "tp2":         round(tp2, 6),
            "tp3":         round(tp3, 6),
            "rr1":         "1:1.5",
            "rr2":         "1:2.5",
            "rr3":         "1:4.0",
            "risk_pct":    cal["size_factor"] * 1.0,
            "tp1_action":  "Close 35% — move SL to BE",
            "tp2_action":  "Close 40%",
            "tp3_action":  "Trail remaining 25%",
            "invalidation": f"Trade invalid if 1M closes {'below' if entry['direction']=='LONG' else 'above'} {ltf_sl:.5f}",
        }
        # Promote LTF score to confidence label for the message
        entry["confidence"] = f"LTF-{ltf_entry['confidence']}"
        entry["matched_ob"] = ltf_entry.get("ob")
        logger.info("Using LTF early entry — mode=%s", ltf_entry.get("mode"))
    else:
        risk = calc_risk(
            entry      = entry["entry_price"],
            direction  = entry["direction"],
            atr_15m    = entry["atr_15m"],
            atr_4h     = htf.get("atr_4h"),
            df_15m     = df_15m,
            sr_levels  = sr_levels,
            pair       = asset,
            matched_ob = entry.get("matched_ob"),
            size_factor= cal["size_factor"],
        )
    if not risk.get("valid", True):
        logger.info("Risk plan invalid: %s", risk.get("reason"))
        return

    # Build composite signal
    signal = {
        "asset":         asset,
        "session":       session["session"],
        "confidence":    entry["confidence"],
        "matched_ob":    entry.get("matched_ob"),
        "smc_4h_event":  entry.get("smc_4h_event"),
        "smc_4h_obs":    entry.get("smc_4h_obs", []),
        "smc_15m_obs":   entry.get("smc_15m_obs", []),
        "atr_4h":        htf.get("atr_4h"),
        **risk,
    }

    # ────────────────────────────────────────────────────────────────────────
    # 🛑  STAGE 7a — COOLDOWN GUARD (runs BEFORE validator)
    # Blocks duplicates / open-position conflicts / opposite-without-CHoCH.
    # If blocked here → no validator, no alerts, no MT5, audit-log only.
    # ────────────────────────────────────────────────────────────────────────
    from cooldown_guard import (
        evaluate_cooldown, record_signal_fired, record_structure_event,
        decision_to_payload, write_blocked_signal, OpenPositionRef,
    )
    from validation_bridge import (
        validate_signal, rejection_to_payload, approval_to_payload,
        _build_setup_id, _open_positions_from_mt5,
    )
    from trade_validator import is_approved
    from asset_scanner import get_ohlcv as _get_ohlcv  # for daily data
    from mt5_config import SYMBOL_MAP

    # Always update structure-event cache (whether or not we trade)
    if entry.get("smc_4h_event"):
        ev_now = entry["smc_4h_event"]
        record_structure_event(asset, ev_now.get("type", ""), int(ev_now.get("idx", 0)))

    # Build structure_id (same scheme used by validator → consistent dedup)
    structure_id = _build_setup_id(signal)

    # Pull live MT5 open-position refs (symbol, direction, opened_at)
    sym_map_reverse = {v: k for k, v in SYMBOL_MAP.items()}
    cooldown_open_pos = []
    try:
        for op in _open_positions_from_mt5(sym_map_reverse):
            cooldown_open_pos.append(OpenPositionRef(
                symbol=op.symbol, direction=op.direction, opened_at=None,
            ))
    except Exception as e:
        logger.warning("Cooldown: open-pos fetch failed (%s) — using empty list", e)

    cooldown_dec = evaluate_cooldown(asset, entry["direction"], structure_id, cooldown_open_pos)
    cooldown_response = decision_to_payload(cooldown_dec)
    logger.info("Cooldown response: %s", cooldown_response)

    if cooldown_response["status"] == "BLOCKED_SIGNAL":
        # Persist to blocked_signals.json (audit only — separate from main signals.json)
        write_blocked_signal(signal, cooldown_dec)
        # Mirror the structured response into the main signal log too
        log_signal({**signal, **cooldown_response}, "BLOCKED_SIGNAL", False)
        logger.info(
            "⏸ COOLDOWN BLOCK — %s · %s · age=%s min",
            cooldown_response["cooldown_code"],
            cooldown_response["cooldown_reason"],
            cooldown_response["age_minutes"],
        )
        logger.info("=" * 70)
        return  # NO validator · NO Telegram · NO WhatsApp · NO TradingView · NO MT5

    # status == "PASSED_COOLDOWN" — falls through into the validation gate below

    # ────────────────────────────────────────────────────────────────────────
    # 🛡  STAGE 6 — VALIDATION GATE (runs only if cooldown allowed)
    # Nothing below this line runs unless the validator approves.
    # ────────────────────────────────────────────────────────────────────────

    # Daily candles for HTF bias check
    df_daily = None
    try:
        if is_forex:
            import requests, pandas as pd
            yfsym = asset.replace("/", "") + "=X"
            r = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{yfsym}",
                params={"interval": "1d", "range": "1y"},
                headers={"User-Agent": "Mozilla/5.0"}, timeout=15,
            ).json()
            res = r["chart"]["result"][0]
            ts  = res["timestamp"]
            q   = res["indicators"]["quote"][0]
            df_daily = pd.DataFrame({
                "open": q["open"], "high": q["high"],
                "low":  q["low"],  "close": q["close"],
            }, index=pd.to_datetime(ts, unit="s")).dropna(subset=["close"])
        else:
            df_daily = _get_ohlcv(asset, "4H", False, 500)  # crypto fallback
    except Exception as e:
        logger.warning("Daily fetch failed for %s: %s", asset, e)

    validation = validate_signal(signal, df_daily)

    if not is_approved(validation):
        # ❌ BLOCKED — log only, no alerts, no MT5, no chart
        payload = rejection_to_payload(validation, signal)
        log_signal(payload, "BLOCKED_SIGNAL", False)
        logger.info("🚫 SIGNAL BLOCKED — %s · %s · %s",
                    asset, validation.code.value, validation.message)
        logger.info("=" * 70)
        return

    # ✅ APPROVED — every side effect below uses this single result
    signal = approval_to_payload(validation, signal)
    logger.info("✅ Validation passed · gates=%s", validation.reasons_passed)

    # ── Module 8: Pine Script ─────────────────────────────────────────────────
    pine_path = save_pine_script(signal, sr_levels)
    logger.info("Pine saved: %s", pine_path)

    # ── Module 8b: Push live zones to TradingView chart ───────────────────────
    try:
        chart_shot = push_to_chart(signal)
        if chart_shot:
            logger.info("Chart marked: %s", chart_shot)
    except Exception as e:
        logger.warning("Chart push error: %s", e)

    # ── Module 9: Broadcast (WhatsApp + Telegram) ─────────────────────────────
    gate_4h_default = {"price_vs_ema": "?", "ema_slope": "?", "extended": False, "distance_pips": 0}
    gate_4h_used = entry.get("gate_4h", gate_4h_default) or gate_4h_default
    wa_msg = format_signal(signal, cal, sr_near, gate_4h_used, pine_path)
    tg_msg = format_signal_telegram(signal, cal, sr_near, gate_4h_used, pine_path)
    sent = broadcast(wa_msg, tg_msg)

    # ── Module 10: MT5 auto-execute ───────────────────────────────────────────
    mt5_result = None
    if MT5_ENABLED:
        try:
            mt5_result = mt5_execute(signal)
            if mt5_result.get("success"):
                logger.info("🤖 MT5 trade opened: ticket=%d lot=%.2f mode=%s",
                            mt5_result["ticket"], mt5_result["lot"], mt5_result["mode"])
            else:
                logger.info("MT5 skipped: %s", mt5_result.get("reason"))
        except Exception as e:
            logger.warning("MT5 execution error: %s", e)

    log_signal({**signal, "pine_path": pine_path,
                "next_event": cal["next_event"],
                "warning": cal["warning"],
                "mt5": mt5_result},
               "SENT" if sent else "LOGGED_ONLY", sent)

    # Record successful signal in cooldown cache so future duplicates are blocked
    if sent:
        try:
            record_signal_fired(asset, entry["direction"], structure_id)
        except Exception as e:
            logger.warning("Failed to record cooldown entry: %s", e)

        # Performance tracker — record this signal so we can match the outcome later
        try:
            from performance_tracker import record_signal as perf_record
            ticket = (mt5_result or {}).get("ticket") if mt5_result else None
            perf_record({**signal, "structure_id": structure_id}, mt5_ticket=ticket)
        except Exception as e:
            logger.warning("Performance tracker record failed: %s", e)

    logger.info("SIGNAL %s: %s %s @ %s | SL %s | TP1 %s | conf %s",
                "SENT" if sent else "LOGGED_ONLY", entry["direction"], asset,
                risk["entry"], risk["sl_price"], risk["tp1"], entry["confidence"])
    logger.info("=" * 70)


def daily_summary_job():
    """Performance-aware daily summary — wins/losses/R/per-pair/per-tier."""
    try:
        from performance_tracker import (
            update_outcomes, daily_summary as perf_daily_summary,
        )
        update_outcomes()                                 # final outcome refresh
        perf_daily_summary(
            send_telegram = send_telegram_alert,
            send_whatsapp = send_whatsapp_alert,
        )
    except Exception as e:
        logger.warning("Performance daily summary failed: %s", e)


if __name__ == "__main__":
    from mt5_config import (
        DRY_RUN, MT5_MODE, MT5_LOGIN, RISK_PCT_PER_TRADE,
        MAX_OPEN_POSITIONS, MAX_DAILY_DRAWDOWN_PCT,
    )
    mode_tag = "DRY RUN" if DRY_RUN else MT5_MODE
    startup_banner = (
        f"🤖 Bot online — {mode_tag} — {MT5_MODE.lower()} account #{MT5_LOGIN}\n"
        f"Risk {RISK_PCT_PER_TRADE}% · Max trades {MAX_OPEN_POSITIONS} · "
        f"Daily DD cap {MAX_DAILY_DRAWDOWN_PCT}%"
    )
    logger.info("Trading Signal Engine v3 starting...")
    logger.info(startup_banner.replace("\n", " | "))
    logger.info("Cycle: every %d min | Module order: 0→1→4→5→3→6→7→8→9", SCAN_INTERVAL_MINUTES)

    # Verify MT5 connection on startup
    try:
        from mt5_executor import connect as mt5_connect
        if mt5_connect():
            logger.info("✅ MT5 connection success")
        else:
            logger.warning("⚠️  MT5 connection failed — engine will continue, MT5 calls will skip")
    except Exception as e:
        logger.warning("MT5 startup check error: %s", e)

    # Broadcast startup banner
    try:
        send_whatsapp_alert(startup_banner)
        send_telegram_alert(startup_banner)
        logger.info("Startup banner broadcast to WhatsApp + Telegram")
    except Exception as e:
        logger.warning("Startup broadcast failed: %s", e)

    # Log session-gate status
    from session_gate import get_session_status
    sess = get_session_status()
    logger.info("Session gate: allowed=%s · session=%s · reason=%s",
                sess["allowed"], sess["session"], sess.get("reason", "—"))

    run_cycle()  # initial run

    schedule.every(SCAN_INTERVAL_MINUTES).minutes.do(run_cycle)
    schedule.every().day.at("18:00").do(daily_summary_job)

    # Pre-market Wall Street brief — every weekday at 06:30 UTC.
    # The schedule library uses LOCAL time, so we convert 06:30 UTC into
    # whatever local time that maps to today (auto-handles BST/GMT, etc.)
    try:
        from premarket_scan import run_premarket_scan
        def _premkt():
            if datetime.now(timezone.utc).weekday() < 5:
                run_premarket_scan()
        utc_now    = datetime.now(timezone.utc)
        local_now  = datetime.now()
        offset_h   = round((local_now.replace(tzinfo=None) - utc_now.replace(tzinfo=None)).total_seconds() / 3600)
        local_hour = (6 + offset_h) % 24
        local_str  = f"{local_hour:02d}:30"
        schedule.every().day.at(local_str).do(_premkt)
        logger.info("Pre-market scan scheduled — daily %s local = 06:30 UTC (Mon–Fri)", local_str)
    except Exception as e:
        logger.warning("Could not schedule pre-market scan: %s", e)

    logger.info("Scheduler running — Ctrl+C to stop.")
    while True:
        schedule.run_pending()
        time.sleep(1)
