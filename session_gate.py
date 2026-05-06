"""
Module 0 — Session Gate
Runs first, every cycle. Blocks all signals outside prime UTC trading windows.
"""

from datetime import datetime, timezone, timedelta

PRIME_WINDOWS = [
    (7,  9,  "Tokyo/London overlap",   "HIGH",
        ["USD/JPY", "EUR/JPY", "GBP/JPY", "AUD/JPY"],
        ["EUR/USD", "GBP/USD", "XAU/USD"]),
    (9,  12, "London session",         "MEDIUM",
        ["EUR/USD", "GBP/USD", "EUR/GBP", "USD/CHF", "XAU/USD"],
        ["EUR/JPY", "AUD/USD"]),
    (12, 16, "London/NY overlap",      "HIGHEST",
        ["EUR/USD", "GBP/USD", "USD/CAD", "USD/JPY", "XAU/USD"],
        ["NZD/USD", "AUD/USD", "EUR/GBP", "BTC/USDT", "ETH/USDT"]),
]

BLOCKED_WINDOWS = [
    (0,  7,  "Asian dead zone — tight ranges, false breakouts"),
    (16, 21, "NY tail — fading volume, widening spreads"),
    (21, 24, "Dead zone — Sydney only, near-zero movement"),
]


def get_session_status() -> dict:
    """
    Returns:
      {
        "allowed": bool,
        "session": str,
        "priority": "HIGH"|"MEDIUM"|"HIGHEST"|None,
        "priority_pairs": list[str],
        "secondary_pairs": list[str],
        "reason": str (if blocked),
        "next_prime_in": str (e.g. "2h 30m"),
        "hour_utc": int,
      }
    """
    now = datetime.now(timezone.utc)
    hour, weekday = now.hour, now.weekday()

    if weekday >= 5:
        return _blocked("Weekend — market closed", now)
    if weekday == 4 and hour >= 19:
        return _blocked("Friday close — weekend gap risk", now)

    for start, end, label, priority, prio_pairs, sec_pairs in PRIME_WINDOWS:
        if start <= hour < end:
            return {
                "allowed":         True,
                "session":         label,
                "priority":        priority,
                "priority_pairs":  prio_pairs,
                "secondary_pairs": sec_pairs,
                "hour_utc":        hour,
                "reason":          "",
                "next_prime_in":   "now",
            }

    for start, end, label in BLOCKED_WINDOWS:
        if start <= hour < end:
            return _blocked(label, now)

    return _blocked("Outside trading windows", now)


def _blocked(reason: str, now: datetime) -> dict:
    return {
        "allowed":         False,
        "session":         "BLOCKED",
        "priority":        None,
        "priority_pairs":  [],
        "secondary_pairs": [],
        "hour_utc":        now.hour,
        "reason":          reason,
        "next_prime_in":   _time_to_next_prime(now),
    }


def _time_to_next_prime(now: datetime) -> str:
    """Compute time until next prime window starts."""
    h = now.hour
    candidates = []

    today_starts = [7, 9, 12]
    for start in today_starts:
        if start > h:
            target = now.replace(hour=start, minute=0, second=0, microsecond=0)
            candidates.append(target)

    # Tomorrow's first prime
    tomorrow = now + timedelta(days=1)
    if tomorrow.weekday() < 5:
        candidates.append(tomorrow.replace(hour=7, minute=0, second=0, microsecond=0))
    else:
        # Skip to Monday
        days_ahead = 7 - tomorrow.weekday()
        monday = tomorrow + timedelta(days=days_ahead - 1)
        candidates.append(monday.replace(hour=7, minute=0, second=0, microsecond=0))

    nxt = min(candidates)
    delta = nxt - now
    hours = int(delta.total_seconds() // 3600)
    mins  = int((delta.total_seconds() % 3600) // 60)
    return f"{hours}h {mins}m ({nxt.strftime('%H:%M')} UTC)"
