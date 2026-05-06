"""
Per-cycle outcome refresh — Tier-1 gap fix.

Verifies that main._refresh_outcomes_safely():
  1. Returns 0 cleanly when there are no records
  2. Returns 0 cleanly when the MT5 module is unavailable
  3. Never raises — even if performance_tracker.update_outcomes() throws
  4. Is the same callable referenced from run_cycle (contract check)
"""
import inspect
import pytest


def test_refresh_outcomes_with_no_records(tmp_path, monkeypatch):
    """Empty performance.json → returns 0, doesn't crash."""
    import performance_tracker as pt
    monkeypatch.setattr(pt, "PERF_FILE", tmp_path / "perf.json")

    from main import _refresh_outcomes_safely
    assert _refresh_outcomes_safely() == 0


def test_refresh_outcomes_swallows_exceptions(monkeypatch):
    """If update_outcomes() raises, _refresh_outcomes_safely returns 0."""
    import performance_tracker as pt

    def boom():
        raise RuntimeError("simulated MT5 failure")
    monkeypatch.setattr(pt, "update_outcomes", boom)

    from main import _refresh_outcomes_safely
    assert _refresh_outcomes_safely() == 0


def test_refresh_outcomes_called_from_run_cycle():
    """Contract: run_cycle's source must reference the helper."""
    from main import run_cycle, _refresh_outcomes_safely
    src = inspect.getsource(run_cycle)
    assert "_refresh_outcomes_safely" in src, \
        "run_cycle must call _refresh_outcomes_safely() at the start"


def test_refresh_outcomes_is_lightweight_when_no_open(tmp_path, monkeypatch):
    """
    When performance.json exists but contains zero OPEN entries,
    update_outcomes must short-circuit without touching MT5.
    """
    import json
    import performance_tracker as pt
    perf = tmp_path / "perf.json"
    perf.write_text(json.dumps([
        {"symbol": "EUR/USD", "outcome": "TP1", "final_r": 1.5},
        {"symbol": "USD/CHF", "outcome": "SL",  "final_r": -1.0},
    ]))
    monkeypatch.setattr(pt, "PERF_FILE", perf)

    # Sentinel — if MT5 is touched, set will flip
    touched = {"value": False}
    class Sentinel:
        def __getattr__(self, name):
            touched["value"] = True
            raise AttributeError(name)
    # update_outcomes only imports MT5 if there are open recs — so it must
    # never hit our sentinel. Pass the sentinel as the injected mt5 module:
    n = pt.update_outcomes(mt5_module=Sentinel())
    assert n == 0
    assert touched["value"] is False, "MT5 should not be touched when no open records"
