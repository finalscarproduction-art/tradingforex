"""
Persistent JSON log of every cycle outcome.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from config import SIGNALS_DB

LOG = Path(SIGNALS_DB)


def _load() -> list:
    if LOG.exists():
        try: return json.loads(LOG.read_text(encoding="utf-8"))
        except Exception: return []
    return []


def _save(records: list):
    LOG.write_text(json.dumps(records, indent=2, default=str), encoding="utf-8")


def log_signal(payload: dict, status: str, whatsapp_sent: bool):
    rec = _load()
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status":    status,
        "whatsapp":  whatsapp_sent,
        **payload,
    }
    rec.append(entry)
    _save(rec)

    # Also write the latest signal as a flat single-signal file the EA reads.
    # Only confirmed SENT signals make it into latest_signal.json.
    if status == "SENT" and payload.get("entry") and payload.get("sl_price"):
        try:
            ea_dir = Path.home() / "AppData" / "Roaming" / "MetaQuotes" / "Terminal" / "Common" / "Files"
            ea_dir.mkdir(parents=True, exist_ok=True)
            (ea_dir / "signals.json").write_text(
                json.dumps(entry, indent=2, default=str), encoding="utf-8"
            )
        except Exception:
            pass


def daily_stats() -> dict:
    rec = _load()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_recs = [r for r in rec if r.get("timestamp","").startswith(today)]
    sent = [r for r in today_recs if r["status"] == "SENT"]
    return {
        "sent":    len(sent),
        "premium": sum(1 for r in sent if r.get("confidence") == "PREMIUM"),
        "high":    sum(1 for r in sent if r.get("confidence") == "HIGH"),
        "med":     sum(1 for r in sent if r.get("confidence") == "MEDIUM"),
        "total":   len(today_recs),
    }
