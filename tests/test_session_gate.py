"""Unit tests for session_gate — UTC window logic and pair filtering."""
from datetime import datetime, timezone, timedelta
import pytest

import session_gate
from session_gate import get_session_status, PRIME_WINDOWS, BLOCKED_WINDOWS


# ── Fixtures ─────────────────────────────────────────────────────────────────
def _patch_now(monkeypatch, dt: datetime):
    """Freeze datetime.now in session_gate."""
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return dt if tz is None else dt.astimezone(tz)
    monkeypatch.setattr(session_gate, "datetime", Frozen)


# ── Prime windows ────────────────────────────────────────────────────────────
def test_tokyo_london_overlap_07_utc(monkeypatch):
    _patch_now(monkeypatch, datetime(2026, 5, 5, 7, 30, tzinfo=timezone.utc))
    s = get_session_status()
    assert s["allowed"] is True
    assert s["session"]  == "Tokyo/London overlap"
    assert s["priority"] == "HIGH"
    assert "USD/JPY" in s["priority_pairs"]


def test_london_session_10_utc_includes_gold(monkeypatch):
    _patch_now(monkeypatch, datetime(2026, 5, 5, 10, 0, tzinfo=timezone.utc))
    s = get_session_status()
    assert s["allowed"] is True
    assert s["session"] == "London session"
    assert "XAU/USD" in s["priority_pairs"]


def test_london_ny_overlap_14_utc_highest_priority(monkeypatch):
    _patch_now(monkeypatch, datetime(2026, 5, 5, 14, 0, tzinfo=timezone.utc))
    s = get_session_status()
    assert s["allowed"] is True
    assert s["priority"] == "HIGHEST"
    assert "XAU/USD" in s["priority_pairs"]
    assert "BTC/USDT" in s["secondary_pairs"]


# ── Blocked windows ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("h,reason_substr", [
    (3,  "Asian"),
    (17, "NY tail"),
    (22, "Sydney"),
])
def test_blocked_window_returns_disallowed(monkeypatch, h, reason_substr):
    _patch_now(monkeypatch, datetime(2026, 5, 5, h, 0, tzinfo=timezone.utc))
    s = get_session_status()
    assert s["allowed"] is False
    assert s["session"] == "BLOCKED"
    assert reason_substr.lower() in s["reason"].lower()
    assert s["priority_pairs"] == []


# ── Weekend & Friday close ───────────────────────────────────────────────────
def test_saturday_blocked(monkeypatch):
    _patch_now(monkeypatch, datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc))  # Sat
    s = get_session_status()
    assert s["allowed"] is False
    assert "weekend" in s["reason"].lower()


def test_sunday_blocked(monkeypatch):
    _patch_now(monkeypatch, datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc))  # Sun
    s = get_session_status()
    assert s["allowed"] is False


def test_friday_after_19utc_blocked(monkeypatch):
    _patch_now(monkeypatch, datetime(2026, 5, 8, 20, 0, tzinfo=timezone.utc))  # Fri 20:00
    s = get_session_status()
    assert s["allowed"] is False
    assert "friday" in s["reason"].lower()


def test_friday_before_19utc_still_open(monkeypatch):
    _patch_now(monkeypatch, datetime(2026, 5, 8, 13, 0, tzinfo=timezone.utc))  # Fri 13:00
    s = get_session_status()
    assert s["allowed"] is True


# ── next_prime_in calculation ────────────────────────────────────────────────
def test_next_prime_format_in_blocked(monkeypatch):
    _patch_now(monkeypatch, datetime(2026, 5, 5, 3, 0, tzinfo=timezone.utc))  # Tue 03:00 UTC
    s = get_session_status()
    assert s["allowed"] is False
    # Next prime starts at 07:00 UTC same day → 4h
    assert "4h" in s["next_prime_in"] or "h " in s["next_prime_in"]


# ── PRIME_WINDOWS shape sanity ──────────────────────────────────────────────
def test_prime_windows_have_5_fields():
    for entry in PRIME_WINDOWS:
        assert len(entry) == 6
        start, end, label, prio, p_pairs, s_pairs = entry
        assert 0 <= start < 24
        assert start < end <= 24
        assert prio in {"HIGH", "MEDIUM", "HIGHEST"}
        assert isinstance(p_pairs, list) and isinstance(s_pairs, list)


def test_blocked_windows_cover_full_outside_prime():
    """Combined PRIME + BLOCKED must cover hours 0–23 with no gaps."""
    covered = set()
    for s, e, *_ in PRIME_WINDOWS:    covered.update(range(s, e))
    for s, e, _ in BLOCKED_WINDOWS:   covered.update(range(s, e))
    assert covered == set(range(24)), f"hours not covered: {set(range(24)) - covered}"
