"""Pytest fixtures shared across the test suite.

Each test gets its own isolated cooldown state file via tmp_path,
so tests never see each other's signals or events.
"""
import sys, os
# Engine root (so `from cooldown_guard import …` works)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))
# Tests dir itself (so `from pipeline_helper import …` works in any pytest mode)
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import pytest
from datetime import datetime, timezone

from trade_validator import SetupCandidate, HTFBias, OpenPosition, RecentSignal
from cooldown_guard  import OpenPositionRef


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Re-point cooldown_guard's persistence files to a tmp dir per test."""
    state_file   = tmp_path / "cooldown_state.json"
    blocked_file = tmp_path / "blocked_signals.json"
    monkeypatch.setattr("cooldown_guard.STATE_FILE",         state_file)
    monkeypatch.setattr("cooldown_guard.BLOCKED_AUDIT_FILE", blocked_file)
    return {"state": state_file, "blocked": blocked_file}


@pytest.fixture
def now():
    return datetime.now(timezone.utc)


@pytest.fixture
def usdchf_long(now):
    return {
        "asset":      "USD/CHF",
        "direction":  "LONG",
        "entry":      0.78900,
        "sl_price":   0.78700,
        "tp1":        0.79200,
        "tp2":        0.79400,
        "tp3":        0.79600,
        "confidence": "HIGH",
        "atr_4h":     0.0030,
        "session":    "London",
        "matched_ob": {"idx_end": 191, "low": 0.78870, "high": 0.78920,
                       "direction": "bullish"},
        "smc_4h_event": {"type": "BOS_BULL", "idx": 295,
                         "price": 0.78920},
    }


@pytest.fixture
def setup_candidate(now):
    return SetupCandidate(
        setup_id="USDCHF_191_LONG", symbol="USD/CHF", direction="LONG",
        entry=0.78900, sl=0.78700, tp1=0.79200, tp2=0.79400, tp3=0.79600,
        confidence="HIGH", atr_4h=0.0030, timestamp=now,
    )


@pytest.fixture
def bullish_bias():
    return HTFBias(symbol="USD/CHF", direction="BULL")


@pytest.fixture
def bearish_bias():
    return HTFBias(symbol="USD/CHF", direction="BEAR",
                   blocks_long=True, note="Daily 200 EMA above price")


@pytest.fixture
def fake_side_effects(mocker):
    """Returns a dict of mocks for every side-effect the pipeline may call."""
    return {
        "send_telegram":          mocker.MagicMock(return_value=True),
        "send_whatsapp":          mocker.MagicMock(return_value=True),
        "push_chart":             mocker.MagicMock(return_value="background"),
        "execute_mt5":            mocker.MagicMock(return_value={"success": True, "ticket": 123}),
        "log_signal":             mocker.MagicMock(),
        "write_blocked":          mocker.MagicMock(),
        "record_signal_fired":    mocker.MagicMock(),
        "record_structure_event": mocker.MagicMock(),
    }
