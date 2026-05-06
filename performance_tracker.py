"""
Performance Tracker
===================
Tracks every signal that fires, then matches outcomes back from MT5 closed
deals. Aggregates win/loss, R achieved, per-pair, and per-confidence-tier
stats. Emits a daily summary to Telegram + WhatsApp.

Storage:
  performance.json — list of TrackedSignal records (append-only, atomic write)

Lifecycle:
  1. After a SENT signal:   record_signal(signal, mt5_ticket)
  2. Each cycle:            update_outcomes()        (polls MT5 deal history)
  3. End of day (17:30 UTC): daily_summary()        (formats and broadcasts)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PERF_FILE = Path(__file__).parent / "performance.json"

# Outcome codes
OPEN          = "OPEN"
WIN_TP1       = "TP1"
WIN_TP2       = "TP2"
WIN_TP3       = "TP3"
LOSS_SL       = "SL"
CLOSED_MANUAL = "CLOSED_MANUAL"

# R values per outcome
_R_BY_OUTCOME = {
    WIN_TP1:       1.5,
    WIN_TP2:       2.5,
    WIN_TP3:       4.0,
    LOSS_SL:      -1.0,
    OPEN:          None,
    CLOSED_MANUAL: None,
}


# ── Data contract ────────────────────────────────────────────────────────────
@dataclass
class TrackedSignal:
    signal_id:    str
    timestamp:    str          # ISO UTC
    symbol:       str
    direction:    str
    confidence:   str
    entry:        float
    sl:           float
    tp1:          float
    tp2:          float
    tp3:          float
    setup_id:     str = ""
    mt5_ticket:   Optional[int] = None
    outcome:      str = OPEN
    closed_at:    Optional[str] = None
    final_price:  Optional[float] = None
    final_r:      Optional[float] = None
    profit_money: Optional[float] = None


# ── Persistence (atomic) ─────────────────────────────────────────────────────
def _load() -> list[dict]:
    if not PERF_FILE.exists():
        return []
    try:
        return json.loads(PERF_FILE.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("performance.json unreadable — starting fresh")
        return []


def _save(records: list[dict]) -> None:
    tmp = PERF_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(records, indent=2, default=str), encoding="utf-8")
    tmp.replace(PERF_FILE)


# ── Public API ───────────────────────────────────────────────────────────────
def record_signal(signal: dict, mt5_ticket: Optional[int] = None) -> str:
    """Append a new signal to the tracker. Returns the signal_id."""
    ts = datetime.now(timezone.utc).isoformat()
    setup_id = signal.get("setup_id") or signal.get("structure_id") or ""
    sid = f"{signal['asset'].replace('/','')}_{ts}_{signal['direction']}"
    rec = TrackedSignal(
        signal_id  = sid,
        timestamp  = ts,
        symbol     = signal["asset"],
        direction  = signal["direction"],
        confidence = (signal.get("confidence") or "HIGH").split(" ")[0],
        entry      = float(signal["entry"]),
        sl         = float(signal["sl_price"]),
        tp1        = float(signal["tp1"]),
        tp2        = float(signal["tp2"]),
        tp3        = float(signal["tp3"]),
        setup_id   = setup_id,
        mt5_ticket = mt5_ticket,
    )
    records = _load()
    records.append(asdict(rec))
    _save(records)
    logger.info("Performance: tracked %s %s · ticket=%s", rec.symbol, rec.direction, mt5_ticket)
    return sid


def _classify_outcome(rec: dict, close_price: float) -> tuple[str, float]:
    """Decide which outcome bucket a closed price falls into."""
    is_long = rec["direction"] == "LONG"
    sl_dist = abs(rec["entry"] - rec["sl"])
    if sl_dist == 0:
        return CLOSED_MANUAL, 0.0

    # R achieved relative to original risk
    move = (close_price - rec["entry"]) if is_long else (rec["entry"] - close_price)
    r_actual = round(move / sl_dist, 2)

    # Choose discrete bucket by proximity to engineered targets
    def hit(target):
        return (close_price >= target - 1e-6) if is_long else (close_price <= target + 1e-6)

    if hit(rec["tp3"]):  return WIN_TP3, r_actual
    if hit(rec["tp2"]):  return WIN_TP2, r_actual
    if hit(rec["tp1"]):  return WIN_TP1, r_actual

    # Was SL hit?
    sl_hit = (close_price <= rec["sl"] + 1e-6) if is_long else (close_price >= rec["sl"] - 1e-6)
    if sl_hit:
        return LOSS_SL, r_actual

    # Closed somewhere in between
    return CLOSED_MANUAL, r_actual


def update_outcomes(mt5_module=None) -> int:
    """
    Poll MT5 closed deals for our magic number, match to OPEN tracker entries,
    record outcomes. Returns the number of records updated this cycle.

    `mt5_module` is injectable for tests. In production it's the real
    MetaTrader5 module.
    """
    if mt5_module is None:
        try:
            import MetaTrader5 as mt5_module
            from mt5_config import MAGIC_NUMBER
        except Exception:
            return 0
    else:
        try:
            from mt5_config import MAGIC_NUMBER
        except Exception:
            MAGIC_NUMBER = 0

    records = _load()
    open_recs = [r for r in records if r["outcome"] == OPEN]
    if not open_recs:
        return 0

    try:
        if not mt5_module.initialize():
            return 0
    except Exception:
        return 0

    # Pull recent deal history (last 7 days is plenty for cycle-rate updates)
    date_from = datetime.now(timezone.utc) - timedelta(days=7)
    date_to   = datetime.now(timezone.utc) + timedelta(days=1)
    try:
        deals = mt5_module.history_deals_get(date_from, date_to) or []
    except Exception:
        deals = []

    updated = 0
    for rec in open_recs:
        ticket = rec.get("mt5_ticket")
        if not ticket:
            continue
        # Find the deal that CLOSED this position (entry deal opens, exit closes)
        closing_deals = [d for d in deals if getattr(d, "position_id", 0) == ticket
                         and getattr(d, "entry", None) == getattr(mt5_module, "DEAL_ENTRY_OUT", 1)]
        if not closing_deals:
            continue
        close_deal = closing_deals[-1]
        close_price = float(close_deal.price)
        profit      = float(close_deal.profit)
        outcome, r_actual = _classify_outcome(rec, close_price)

        rec["outcome"]      = outcome
        rec["closed_at"]    = datetime.fromtimestamp(close_deal.time, tz=timezone.utc).isoformat()
        rec["final_price"]  = close_price
        rec["final_r"]      = r_actual
        rec["profit_money"] = profit
        updated += 1
        logger.info(
            "Performance: closed %s %s · outcome=%s · R=%.2f · P&L=%.2f",
            rec["symbol"], rec["direction"], outcome, r_actual, profit,
        )

    if updated:
        _save(records)
    return updated


# ── Aggregation ──────────────────────────────────────────────────────────────
def _aggregate(records: list[dict], on_date: Optional[date] = None) -> dict:
    if on_date:
        records = [r for r in records
                   if r.get("timestamp", "").startswith(on_date.isoformat())]

    closed = [r for r in records if r["outcome"] not in (OPEN, CLOSED_MANUAL)]
    wins   = [r for r in closed if r["outcome"].startswith("TP")]
    losses = [r for r in closed if r["outcome"] == LOSS_SL]
    open_n = sum(1 for r in records if r["outcome"] == OPEN)

    total_r = sum((r.get("final_r") or 0) for r in closed)
    avg_r   = (total_r / len(closed)) if closed else 0.0
    win_rate= (len(wins) / len(closed) * 100) if closed else 0.0

    by_pair = {}
    for r in records:
        s = r["symbol"]
        bucket = by_pair.setdefault(s, {"signals": 0, "wins": 0, "losses": 0,
                                        "open": 0, "total_r": 0.0})
        bucket["signals"] += 1
        if r["outcome"].startswith("TP"): bucket["wins"]   += 1
        if r["outcome"] == LOSS_SL:        bucket["losses"] += 1
        if r["outcome"] == OPEN:           bucket["open"]   += 1
        bucket["total_r"] += (r.get("final_r") or 0)

    by_conf = {}
    for r in records:
        c = r.get("confidence", "?")
        bucket = by_conf.setdefault(c, {"signals": 0, "wins": 0, "losses": 0,
                                        "open": 0, "total_r": 0.0})
        bucket["signals"] += 1
        if r["outcome"].startswith("TP"): bucket["wins"]   += 1
        if r["outcome"] == LOSS_SL:        bucket["losses"] += 1
        if r["outcome"] == OPEN:           bucket["open"]   += 1
        bucket["total_r"] += (r.get("final_r") or 0)

    return {
        "total_signals": len(records),
        "wins":          len(wins),
        "losses":        len(losses),
        "open":          open_n,
        "win_rate":      round(win_rate, 1),
        "avg_r":         round(avg_r, 2),
        "total_r":       round(total_r, 2),
        "by_pair":       by_pair,
        "by_confidence": by_conf,
    }


def daily_stats(on: Optional[date] = None) -> dict:
    return _aggregate(_load(), on or datetime.now(timezone.utc).date())


def all_time_stats() -> dict:
    return _aggregate(_load(), None)


# ── Formatting ───────────────────────────────────────────────────────────────
def format_summary_text(stats: dict, label: str = "DAILY PERFORMANCE") -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        f"📊 *{label} — {today} UTC*",
        "─────────────────────────────────",
        f"Signals: {stats['total_signals']}  ·  "
        f"Wins: {stats['wins']}  ·  Losses: {stats['losses']}  ·  "
        f"Open: {stats['open']}",
        f"Win rate: {stats['win_rate']}%  ·  "
        f"Avg R: {stats['avg_r']:+.2f}  ·  "
        f"Total R: {stats['total_r']:+.2f}",
        "",
        "*By pair:*",
    ]
    if not stats["by_pair"]:
        lines.append("  (no signals today)")
    else:
        for pair, b in sorted(stats["by_pair"].items(), key=lambda kv: -kv[1]["total_r"]):
            wl = f"{b['wins']}W {b['losses']}L"
            extra = f" {b['open']}o" if b["open"] else ""
            lines.append(f"  {pair:<10} {wl:>9}{extra}  {b['total_r']:+.1f}R")

    lines.append("")
    lines.append("*By confidence:*")
    if not stats["by_confidence"]:
        lines.append("  (no signals today)")
    else:
        for conf, b in sorted(stats["by_confidence"].items(),
                              key=lambda kv: -kv[1]["total_r"]):
            wl = f"{b['wins']}W {b['losses']}L"
            extra = f" {b['open']}o" if b["open"] else ""
            lines.append(f"  {conf:<10} {wl:>9}{extra}  {b['total_r']:+.1f}R")

    return "\n".join(lines)


# ── Entry point used by the engine scheduler ─────────────────────────────────
def daily_summary(send_telegram=None, send_whatsapp=None) -> dict:
    """
    Build today's stats, broadcast via injected notifiers, return the dict.
    """
    stats = daily_stats()
    msg = format_summary_text(stats)

    if send_telegram:
        try: send_telegram(msg)
        except Exception as e: logger.warning("Telegram summary failed: %s", e)
    if send_whatsapp:
        try: send_whatsapp(msg.replace("*", ""))
        except Exception as e: logger.warning("WhatsApp summary failed: %s", e)

    logger.info("Daily summary sent · %d signals · %.1f%% win-rate",
                stats["total_signals"], stats["win_rate"])
    return stats
