"""
Unit tests for mt5_executor — covers position sizing math + execute_signal
with a fully-mocked MetaTrader5 module.

(There's no separate position_sizer module in this codebase; lot calculation
lives inside execute_signal. These tests pin its behaviour.)
"""
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock

import mt5_executor


# ── Fake MT5 ────────────────────────────────────────────────────────────────
class FakeMT5:
    # Order types
    ORDER_TYPE_BUY         = 0
    ORDER_TYPE_SELL        = 1
    ORDER_TYPE_BUY_LIMIT   = 2
    ORDER_TYPE_SELL_LIMIT  = 3
    ORDER_TYPE_BUY_STOP    = 4
    ORDER_TYPE_SELL_STOP   = 5
    # Actions / time / filling
    TRADE_ACTION_DEAL      = 10
    TRADE_ACTION_PENDING   = 11
    TRADE_ACTION_SLTP      = 12
    ORDER_TIME_GTC         = 0
    ORDER_FILLING_RETURN   = 4
    ORDER_FILLING_IOC      = 2
    ORDER_FILLING_FOK      = 1
    TRADE_RETCODE_DONE     = 10009
    POSITION_TYPE_BUY      = 0
    POSITION_TYPE_SELL     = 1
    DEAL_ENTRY_OUT         = 1

    def __init__(self):
        self._account = SimpleNamespace(
            login=12345678, server="FxPro-MT5 Demo",
            balance=10000.0, equity=10000.0, currency="GBP",
            trade_mode=0,
        )
        self._tick = SimpleNamespace(ask=1.10010, bid=1.09990)
        self._info = SimpleNamespace(
            point=0.0001, digits=5,
            trade_tick_value=1.0, trade_tick_size=0.0001,
            volume_step=0.01, volume_min=0.01, volume_max=10.0,
            trade_stops_level=10, filling_mode=4,
        )
        self._sent_requests = []
        self._next_retcode  = self.TRADE_RETCODE_DONE
        self._last_order_id = 100

    # API surface used by mt5_executor
    def initialize(self):                return True
    def login(self, *_a, **_k):          return True
    def shutdown(self):                  return None
    def account_info(self):              return self._account
    def symbol_select(self, sym, *_a):   return True
    def symbol_info(self, sym):          return self._info
    def symbol_info_tick(self, sym):     return self._tick
    def positions_get(self, *_a, **_k):  return []
    def last_error(self):                return (0, "OK")

    def order_send(self, request):
        self._sent_requests.append(request)
        self._last_order_id += 1
        return SimpleNamespace(retcode=self._next_retcode,
                               order=self._last_order_id,
                               price=request.get("price", 0.0),
                               comment="ok")


# ── Fixtures ────────────────────────────────────────────────────────────────
@pytest.fixture
def fake_mt5(monkeypatch):
    fm = FakeMT5()
    monkeypatch.setattr(mt5_executor, "mt5", fm)
    monkeypatch.setattr(mt5_executor, "MT5_AVAILABLE", True)
    monkeypatch.setattr(mt5_executor, "_connected", False)
    monkeypatch.setattr(mt5_executor, "_session_start_balance", None)
    # Tests need real order_send paths — disable DRY_RUN inside the executor
    monkeypatch.setattr(mt5_executor, "DRY_RUN", False)
    return fm


@pytest.fixture
def long_signal():
    return {
        "asset":      "EUR/USD",
        "direction":  "LONG",
        "entry":      1.10000,
        "sl_price":   1.09800,
        "tp1":        1.10300,
        "tp2":        1.10500,
        "tp3":        1.10800,
        "confidence": "HIGH",
    }


@pytest.fixture
def short_signal():
    return {
        "asset":      "USD/CHF",
        "direction":  "SHORT",
        "entry":      0.78100,
        "sl_price":   0.78250,
        "tp1":        0.77900,
        "tp2":        0.77750,
        "tp3":        0.77500,
        "confidence": "HIGH",
    }


# ── Connection ──────────────────────────────────────────────────────────────
def test_connect_initialises_and_logs_in(fake_mt5):
    assert mt5_executor.connect() is True
    assert mt5_executor._session_start_balance == 10000.0


# ── Position sizing math ────────────────────────────────────────────────────
def test_lot_size_correct_for_long(monkeypatch, fake_mt5, long_signal):
    """Risk = 1% × £10000 = £100. SL distance = 0.002. point_value = tick_value/tick_size = 10000.
       lot = risk_money / (sl_dist × point_value) = 100 / (0.002 × 10000) = 5.0
       (capped to MAX_LOT in config)."""
    # Map asset to itself for the test
    monkeypatch.setattr(mt5_executor, "SYMBOL_MAP", {"EUR/USD": "EURUSD"})
    monkeypatch.setattr(mt5_executor, "ALLOWED_SYMBOLS", {"EURUSD"})

    result = mt5_executor.execute_signal(long_signal)
    assert result["success"] is True
    sent = fake_mt5._sent_requests[0]
    # Math gives 5.0 — clipped by MAX_LOT (5.0). Allow either 5.0 or rounded.
    assert sent["volume"] == pytest.approx(5.0, rel=0.01)
    assert sent["type"] == FakeMT5.ORDER_TYPE_BUY


def test_lot_size_scales_with_size_factor_via_smaller_balance(monkeypatch, fake_mt5,
                                                                long_signal):
    """Smaller balance → smaller lot."""
    fake_mt5._account.balance = 1000.0
    monkeypatch.setattr(mt5_executor, "SYMBOL_MAP", {"EUR/USD": "EURUSD"})
    monkeypatch.setattr(mt5_executor, "ALLOWED_SYMBOLS", {"EURUSD"})

    result = mt5_executor.execute_signal(long_signal)
    sent = fake_mt5._sent_requests[0]
    # 1% of £1000 = £10 risk → lot = 10 / (0.002 × 10000) = 0.5
    assert sent["volume"] == pytest.approx(0.5, abs=0.05)


# ── Order direction ↔ signal direction ──────────────────────────────────────
def test_long_signal_produces_buy_order(monkeypatch, fake_mt5, long_signal):
    monkeypatch.setattr(mt5_executor, "SYMBOL_MAP", {"EUR/USD": "EURUSD"})
    monkeypatch.setattr(mt5_executor, "ALLOWED_SYMBOLS", {"EURUSD"})
    mt5_executor.execute_signal(long_signal)
    entry_req = fake_mt5._sent_requests[0]
    assert entry_req["type"] == FakeMT5.ORDER_TYPE_BUY


def test_short_signal_produces_sell_order(monkeypatch, fake_mt5, short_signal):
    monkeypatch.setattr(mt5_executor, "SYMBOL_MAP", {"USD/CHF": "USDCHF"})
    monkeypatch.setattr(mt5_executor, "ALLOWED_SYMBOLS", {"USDCHF"})
    fake_mt5._tick = SimpleNamespace(ask=0.78110, bid=0.78090)

    mt5_executor.execute_signal(short_signal)
    entry_req = fake_mt5._sent_requests[0]
    assert entry_req["type"] == FakeMT5.ORDER_TYPE_SELL


# ── Safety gates ────────────────────────────────────────────────────────────
def test_signal_with_sl_on_wrong_side_blocked(monkeypatch, fake_mt5, long_signal):
    """LONG with SL above entry must be refused before any broker call."""
    monkeypatch.setattr(mt5_executor, "SYMBOL_MAP", {"EUR/USD": "EURUSD"})
    monkeypatch.setattr(mt5_executor, "ALLOWED_SYMBOLS", {"EURUSD"})
    bad = {**long_signal, "sl_price": 1.10500}   # SL above entry — bug
    result = mt5_executor.execute_signal(bad)
    assert result["success"] is False
    assert "safety_geometry" in result["reason"]
    assert fake_mt5._sent_requests == []   # no broker call


def test_short_signal_with_tp_above_entry_blocked(monkeypatch, fake_mt5, short_signal):
    monkeypatch.setattr(mt5_executor, "SYMBOL_MAP", {"USD/CHF": "USDCHF"})
    monkeypatch.setattr(mt5_executor, "ALLOWED_SYMBOLS", {"USDCHF"})
    bad = {**short_signal, "tp1": 0.78400}   # TP above entry for SHORT — bug
    result = mt5_executor.execute_signal(bad)
    assert result["success"] is False
    assert fake_mt5._sent_requests == []


def test_unknown_symbol_blocked(monkeypatch, fake_mt5, long_signal):
    monkeypatch.setattr(mt5_executor, "SYMBOL_MAP", {})
    monkeypatch.setattr(mt5_executor, "ALLOWED_SYMBOLS", set())
    result = mt5_executor.execute_signal(long_signal)
    assert result["success"] is False
    assert "whitelist" in result["reason"]


# ── Spread filter ──────────────────────────────────────────────────────────
def test_wide_spread_rejected(monkeypatch, fake_mt5, long_signal):
    monkeypatch.setattr(mt5_executor, "SYMBOL_MAP", {"EUR/USD": "EURUSD"})
    monkeypatch.setattr(mt5_executor, "ALLOWED_SYMBOLS", {"EURUSD"})
    fake_mt5._tick = SimpleNamespace(ask=1.10100, bid=1.09900)   # 200 points
    result = mt5_executor.execute_signal(long_signal)
    assert result["success"] is False
    assert "spread" in result["reason"].lower()


# ── Max-positions cap ──────────────────────────────────────────────────────
def test_max_positions_cap_blocks_new_entry(monkeypatch, fake_mt5, long_signal):
    monkeypatch.setattr(mt5_executor, "SYMBOL_MAP", {"EUR/USD": "EURUSD"})
    monkeypatch.setattr(mt5_executor, "ALLOWED_SYMBOLS", {"EURUSD"})
    monkeypatch.setattr(mt5_executor, "MAX_OPEN_POSITIONS", 2)

    fake_pos = SimpleNamespace(magic=mt5_executor.MAGIC_NUMBER, ticket=1)
    fake_mt5.positions_get = lambda *_a, **_k: [fake_pos, fake_pos]

    result = mt5_executor.execute_signal(long_signal)
    assert result["success"] is False
    assert "Max positions" in result["reason"]


# ── TP partial orders are placed ───────────────────────────────────────────
def test_three_partial_tps_placed_after_entry(monkeypatch, fake_mt5, long_signal):
    monkeypatch.setattr(mt5_executor, "SYMBOL_MAP", {"EUR/USD": "EURUSD"})
    monkeypatch.setattr(mt5_executor, "ALLOWED_SYMBOLS", {"EURUSD"})

    result = mt5_executor.execute_signal(long_signal)
    assert result["success"]
    # 1 entry + up to 3 partial TPs = 4 requests sent
    actions = [r["action"] for r in fake_mt5._sent_requests]
    assert actions.count(FakeMT5.TRADE_ACTION_DEAL)    == 1
    assert actions.count(FakeMT5.TRADE_ACTION_PENDING) >= 1
    # All pending TPs for a LONG are SELL_LIMIT
    pending_types = [r["type"] for r in fake_mt5._sent_requests
                     if r["action"] == FakeMT5.TRADE_ACTION_PENDING]
    assert all(t == FakeMT5.ORDER_TYPE_SELL_LIMIT for t in pending_types)


def test_short_partial_tps_use_buy_limit(monkeypatch, fake_mt5, short_signal):
    monkeypatch.setattr(mt5_executor, "SYMBOL_MAP", {"USD/CHF": "USDCHF"})
    monkeypatch.setattr(mt5_executor, "ALLOWED_SYMBOLS", {"USDCHF"})
    fake_mt5._tick = SimpleNamespace(ask=0.78110, bid=0.78090)

    result = mt5_executor.execute_signal(short_signal)
    assert result["success"]
    pending_types = [r["type"] for r in fake_mt5._sent_requests
                     if r["action"] == FakeMT5.TRADE_ACTION_PENDING]
    # Closing a SHORT at TP = BUY_LIMIT below entry (correct mechanic)
    assert all(t == FakeMT5.ORDER_TYPE_BUY_LIMIT for t in pending_types)
