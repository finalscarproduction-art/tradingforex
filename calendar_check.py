"""
Module 4 — Economic Calendar Check
ForexFactory RSS. Hard-blocks within 30 min of HIGH-impact events.
"""

import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
import logging
import re

from config import HARD_BLOCK_MINUTES, REDUCE_HOURS, WARN_HOURS

logger = logging.getLogger(__name__)
FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
HIGH = {"High", "high", "3"}
MED  = {"Medium", "medium", "2"}


def _fetch_events() -> list[dict]:
    try:
        resp = requests.get(FF_URL, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        root = ET.fromstring(resp.content)
        events = []
        for item in root.iter("event"):
            try:
                dt_str = f"{item.findtext('date','')} {item.findtext('time','')}".strip()
                dt = datetime.strptime(dt_str, "%m-%d-%Y %I:%M%p").replace(tzinfo=timezone.utc)
            except Exception:
                dt = None
            events.append({
                "title":   item.findtext("title", ""),
                "country": item.findtext("country", ""),
                "impact":  item.findtext("impact",  ""),
                "datetime": dt,
            })
        return events
    except Exception as e:
        logger.warning("Calendar fetch failed: %s", e)
        return []


def check_calendar(asset: str) -> dict:
    """
    Returns:
      {
        "action": "PROCEED"|"REDUCE"|"BLOCK",
        "reason": str,
        "next_event": str,
        "size_factor": 1.0|0.5|0.0,
        "warning": str,
      }
    """
    events = _fetch_events()
    now = datetime.now(timezone.utc)
    asset_codes = _currencies_for(asset)

    relevant = []
    for ev in events:
        if ev["datetime"] is None: continue
        delta = (ev["datetime"] - now).total_seconds()
        if delta < 0 or delta > WARN_HOURS * 3600 + 3600: continue
        if ev["impact"] not in HIGH and ev["impact"] not in MED: continue
        if asset_codes and ev["country"].upper() not in asset_codes and ev["country"]: continue
        relevant.append({**ev, "minutes_away": delta / 60})

    relevant.sort(key=lambda x: x["minutes_away"])

    next_event_str = "None within 6h"
    if relevant:
        nxt = relevant[0]
        h, m = divmod(int(nxt["minutes_away"]), 60)
        next_event_str = f"{nxt['title']} ({nxt['country']}) in {h}h {m}m [{nxt['impact']}]"

    high_only = [e for e in relevant if e["impact"] in HIGH]

    if high_only:
        soonest = high_only[0]
        mins = soonest["minutes_away"]

        if mins <= HARD_BLOCK_MINUTES:
            return {"action": "BLOCK", "reason": f"{soonest['title']} in {mins:.0f}m",
                    "next_event": next_event_str, "size_factor": 0.0,
                    "warning": f"HIGH-impact event in {mins:.0f}m — no entry"}
        if mins <= REDUCE_HOURS * 60:
            return {"action": "REDUCE", "reason": f"{soonest['title']} in {mins/60:.1f}h",
                    "next_event": next_event_str, "size_factor": 0.5,
                    "warning": f"HIGH-impact in {mins/60:.1f}h — size 50%"}
        if mins <= WARN_HOURS * 60:
            return {"action": "PROCEED", "reason": f"HIGH-impact in {mins/60:.1f}h",
                    "next_event": next_event_str, "size_factor": 1.0,
                    "warning": f"HIGH-impact in {mins/60:.1f}h — monitor"}

    return {"action": "PROCEED", "reason": "Calendar clear",
            "next_event": next_event_str, "size_factor": 1.0, "warning": ""}


def _currencies_for(asset: str) -> set[str]:
    m = {"EUR":"EU","GBP":"GB","USD":"US","JPY":"JP","AUD":"AU",
         "CAD":"CA","NZD":"NZ","CHF":"CH","BTC":"","ETH":""}
    codes = set()
    for part in re.split(r"[/\-_]", asset.upper()):
        if part in m and m[part]:
            codes.add(m[part])
    return codes
