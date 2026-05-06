"""
MT5 Safety Layer — direct functional tests + the audit's exact USD/CHF
SHORT scenario reproduced end-to-end against a fake MT5 module.
"""
import logging
from unittest.mock import MagicMock

import pytest

from mt5_safety import (
    OrderIntent, SafetyResult,
    validate_signal_geometry, validate_order_request,
    _order_type_for_intent, precheck_signal,
)


# Fake MT5 module that mirrors the constants we use ──────────────────────────
class FakeMT5:
    ORDER_TYPE_BUY         = 0
    ORDER_TYPE_SELL        = 1
    ORDER_TYPE_BUY_LIMIT   = 2
    ORDER_TYPE_SELL_LIMIT  = 3
    ORDER_TYPE_BUY_STOP    = 4
    ORDER_TYPE_SELL_STOP   = 5
    DEAL_ENTRY_OUT         = 1


@pytest.fixture
def fake_mt5():
    return FakeMT5()


# ── 1. Signal geometry validation ────────────────────────────────────────
class TestSignalGeometry:
    def test_long_with_correct_geometry_passes(self):
        r = validate_signal_geometry("LONG", entry=1.10, sl=1.09,
                                     tps=[1.11, 1.12, 1.13])
        assert r.ok

    def test_short_with_correct_geometry_passes(self):
        r = validate_signal_geometry("SHORT", entry=0.78100, sl=0.78200,
                                     tps=[0.77950, 0.77850, 0.77700])
        assert r.ok

    def test_long_with_sl_above_entry_blocked(self):
        r = validate_signal_geometry("LONG", entry=1.10, sl=1.11, tps=[1.12])
        assert not r.ok
        assert r.code == "SL_WRONG_SIDE"

    def test_short_with_sl_below_entry_blocked(self):
        # If a SHORT signal had SL 0.78000 (below entry 0.78100) — bug
        r = validate_signal_geometry("SHORT", entry=0.78100, sl=0.78000,
                                     tps=[0.77950])
        assert not r.ok
        assert r.code == "SL_WRONG_SIDE"

    def test_long_with_tp_below_entry_blocked(self):
        r = validate_signal_geometry("LONG", entry=1.10, sl=1.09,
                                     tps=[1.11, 1.08])  # TP2 below entry — bug
        assert not r.ok
        assert r.code == "TP2_WRONG_SIDE"

    def test_short_with_tp_above_entry_blocked(self):
        r = validate_signal_geometry("SHORT", entry=0.78100, sl=0.78200,
                                     tps=[0.77950, 0.78300])  # TP2 above entry — bug
        assert not r.ok
        assert r.code == "TP2_WRONG_SIDE"


# ── 2. Order-type ↔ direction agreement ─────────────────────────────────
class TestOrderTypeMapping:
    def test_long_entry_returns_buy(self, fake_mt5):
        assert _order_type_for_intent("LONG", OrderIntent.ENTRY_ORDER, fake_mt5) \
               == fake_mt5.ORDER_TYPE_BUY

    def test_short_entry_returns_sell(self, fake_mt5):
        assert _order_type_for_intent("SHORT", OrderIntent.ENTRY_ORDER, fake_mt5) \
               == fake_mt5.ORDER_TYPE_SELL

    def test_long_tp_exit_returns_sell_limit(self, fake_mt5):
        """Exits a LONG by selling at a higher price."""
        assert _order_type_for_intent("LONG", OrderIntent.TAKE_PROFIT_EXIT,
                                      fake_mt5) == fake_mt5.ORDER_TYPE_SELL_LIMIT

    def test_short_tp_exit_returns_buy_limit(self, fake_mt5):
        """Exits a SHORT by buying back at a lower price (correct MT5 mechanic)."""
        assert _order_type_for_intent("SHORT", OrderIntent.TAKE_PROFIT_EXIT,
                                      fake_mt5) == fake_mt5.ORDER_TYPE_BUY_LIMIT


# ── 3. Pre-send audit — refuses bad request shapes ──────────────────────
class TestOrderRequestAudit:
    def test_short_entry_with_buy_type_blocked(self, fake_mt5):
        """The exact bug class the user reported — must be blocked."""
        req = {
            "type": fake_mt5.ORDER_TYPE_BUY_LIMIT, "symbol": "USDCHF",
            "volume": 0.1, "price": 0.78100, "sl": 0.78200, "tp": 0.0,
        }
        r = validate_order_request(req, "SHORT", OrderIntent.ENTRY_ORDER, fake_mt5)
        assert not r.ok
        assert r.code == "ORDER_TYPE_DIRECTION_MISMATCH"

    def test_long_entry_with_sell_type_blocked(self, fake_mt5):
        req = {
            "type": fake_mt5.ORDER_TYPE_SELL_LIMIT, "symbol": "EURUSD",
            "volume": 0.1, "price": 1.10, "sl": 1.09, "tp": 0.0,
        }
        r = validate_order_request(req, "LONG", OrderIntent.ENTRY_ORDER, fake_mt5)
        assert not r.ok
        assert r.code == "ORDER_TYPE_DIRECTION_MISMATCH"

    def test_short_tp_exit_buy_limit_passes(self, fake_mt5):
        """BUY_LIMIT to close a SHORT is the correct exit mechanic."""
        req = {
            "type": fake_mt5.ORDER_TYPE_BUY_LIMIT, "symbol": "USDCHF",
            "volume": 0.05, "price": 0.77950,
        }
        r = validate_order_request(req, "SHORT", OrderIntent.TAKE_PROFIT_EXIT, fake_mt5)
        assert r.ok

    def test_long_entry_buy_with_sl_above_entry_blocked(self, fake_mt5):
        req = {
            "type": fake_mt5.ORDER_TYPE_BUY, "symbol": "EURUSD",
            "volume": 0.1, "price": 1.10, "sl": 1.11, "tp": 0.0,
        }
        r = validate_order_request(req, "LONG", OrderIntent.ENTRY_ORDER, fake_mt5)
        assert not r.ok
        assert r.code == "SL_WRONG_SIDE"


# ── 4. The exact audit example end-to-end ───────────────────────────────
class TestUSDCHFShortAuditScenario:
    """
    Reproduces the exact signal from the bug report:
        SHORT USD/CHF · entry 0.78100 · SL 0.78200 · TP1/2/3 below entry
    Expected: opens SELL at 0.78100, SL is ABOVE entry, all 3 TP exits are
    BUY_LIMIT below entry. No new BUY entries get created.
    """
    SIGNAL = {
        "asset": "USD/CHF", "direction": "SHORT",
        "entry": 0.78100, "sl_price": 0.78200,
        "tp1": 0.77950, "tp2": 0.77850, "tp3": 0.77700,
    }

    def test_signal_geometry_passes(self):
        r = precheck_signal(self.SIGNAL)
        assert r.ok, f"Geometry should be valid: {r.reason}"

    def test_entry_must_be_sell(self, fake_mt5):
        ot = _order_type_for_intent("SHORT", OrderIntent.ENTRY_ORDER, fake_mt5)
        assert ot == fake_mt5.ORDER_TYPE_SELL
        assert ot != fake_mt5.ORDER_TYPE_BUY
        assert ot != fake_mt5.ORDER_TYPE_BUY_LIMIT

    def test_all_three_tps_use_buy_limit_intent_exit(self, fake_mt5):
        """
        Each TP exit for a SHORT is BUY_LIMIT — but it carries the
        TAKE_PROFIT_EXIT intent, so it can never be confused with an entry.
        """
        for tp in [self.SIGNAL["tp1"], self.SIGNAL["tp2"], self.SIGNAL["tp3"]]:
            ot = _order_type_for_intent("SHORT", OrderIntent.TAKE_PROFIT_EXIT, fake_mt5)
            assert ot == fake_mt5.ORDER_TYPE_BUY_LIMIT
            req = {"type": ot, "price": tp, "volume": 0.05, "symbol": "USDCHF"}
            audit = validate_order_request(req, "SHORT",
                                           OrderIntent.TAKE_PROFIT_EXIT, fake_mt5)
            assert audit.ok, f"TP at {tp} should pass audit: {audit.reason}"

    def test_entry_request_with_buy_type_is_blocked(self, fake_mt5):
        """Simulating the exact bug — a BUY order built for a SHORT signal."""
        bad_req = {
            "type":   fake_mt5.ORDER_TYPE_BUY_LIMIT,        # the dangerous bug
            "symbol": "USDCHF", "volume": 0.1,
            "price":  self.SIGNAL["entry"],
            "sl":     self.SIGNAL["sl_price"],
            "tp":     0.0,
        }
        audit = validate_order_request(bad_req, "SHORT",
                                       OrderIntent.ENTRY_ORDER, fake_mt5)
        assert not audit.ok
        assert audit.code == "ORDER_TYPE_DIRECTION_MISMATCH"

    def test_sl_for_short_is_above_entry(self):
        assert self.SIGNAL["sl_price"] > self.SIGNAL["entry"]

    def test_all_tps_for_short_are_below_entry(self):
        for tp in [self.SIGNAL["tp1"], self.SIGNAL["tp2"], self.SIGNAL["tp3"]]:
            assert tp < self.SIGNAL["entry"], f"TP {tp} must be below entry"


# ── 5. Pre-send audit logs the full request ─────────────────────────────
def test_audit_logs_full_request_object(fake_mt5, caplog):
    """The fail-safe must print/log the full request before sending."""
    req = {
        "type":   fake_mt5.ORDER_TYPE_SELL,
        "symbol": "USDCHF", "volume": 0.1,
        "price":  0.78100, "sl": 0.78200, "tp": 0.0,
    }
    with caplog.at_level(logging.INFO, logger="mt5_safety"):
        validate_order_request(req, "SHORT", OrderIntent.ENTRY_ORDER, fake_mt5)
    log_msg = " ".join(r.message for r in caplog.records)
    assert "PRE-SEND AUDIT" in log_msg
    assert "SHORT"          in log_msg
    assert "ENTRY_ORDER"    in log_msg
    assert "USDCHF"         in log_msg
