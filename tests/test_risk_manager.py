"""Unit tests for risk_manager.calculate — SL/TP construction."""
import pandas as pd
import pytest

from risk_manager import calculate, _pip, _min_sl_pips


# ── _pip / _min_sl_pips ─────────────────────────────────────────────────────
@pytest.mark.parametrize("pair,pip", [
    ("EUR/USD", 0.0001), ("USD/JPY", 0.01),
    ("BTC/USDT", 1.0),   ("XAU/USD", 0.0001),
])
def test_pip_size_resolution(pair, pip):
    assert _pip(pair) == pip


def test_min_sl_pips_for_jpy_is_higher_than_fx():
    assert _min_sl_pips("USD/JPY")  >= _min_sl_pips("EUR/USD")
    assert _min_sl_pips("BTC/USDT") >= _min_sl_pips("EUR/USD")


# ── df_15m fixture (needed for SL anchor) ───────────────────────────────────
def _df15m(low=1.0900, high=1.0910):
    return pd.DataFrame([{
        "open": 1.0905, "high": high, "low": low, "close": 1.0908,
    }])


# ── Long: basic plan ────────────────────────────────────────────────────────
def test_long_basic_plan_returns_distinct_tps():
    res = calculate(
        entry=1.0900, direction="LONG",
        atr_15m=0.0010, atr_4h=0.0050,
        df_15m=_df15m(), sr_levels=[], pair="EUR/USD",
        matched_ob={"low": 1.0880, "high": 1.0895, "direction": "bullish"},
    )
    assert res["valid"] is True
    assert res["direction"] == "LONG"
    assert res["sl_price"] < res["entry"]
    assert res["tp1"] > res["entry"]
    assert res["tp2"] > res["tp1"]
    assert res["tp3"] > res["tp2"]


def test_long_min_sl_floor_enforced_when_atr_tiny():
    """If raw SL distance is tiny, min_sl_pips floor must kick in."""
    res = calculate(
        entry=1.0900, direction="LONG",
        atr_15m=0.00001, atr_4h=0.0050,
        df_15m=_df15m(low=1.08999, high=1.0901),  # wick almost at entry
        sr_levels=[], pair="EUR/USD",
        matched_ob=None,
    )
    # Floor for FX is 12 pips
    assert res["valid"] is True
    assert res["sl_pips"] >= 12.0


def test_long_rejects_when_sl_exceeds_2x_atr_4h():
    """A wide OB-anchored stop blows past the 2×ATR_4H ceiling."""
    res = calculate(
        entry=1.0900, direction="LONG",
        atr_15m=0.0010, atr_4h=0.0010,                  # tight 4H ATR
        df_15m=_df15m(low=1.0700, high=1.0910),         # wick far away
        sr_levels=[], pair="EUR/USD",
        matched_ob={"low": 1.0700, "high": 1.0900, "direction": "bullish"},
    )
    assert res["valid"] is False
    assert "SL" in res["reason"]


# ── Short: basic plan ──────────────────────────────────────────────────────
def test_short_basic_plan_geometry():
    res = calculate(
        entry=0.78100, direction="SHORT",
        atr_15m=0.0010, atr_4h=0.0050,
        df_15m=pd.DataFrame([{"open": 0.781, "high": 0.7818,
                              "low": 0.7805, "close": 0.7807}]),
        sr_levels=[], pair="USD/CHF",
        matched_ob={"low": 0.7810, "high": 0.7820, "direction": "bearish"},
    )
    assert res["valid"]   is True
    assert res["sl_price"] > res["entry"]
    assert res["tp1"] < res["entry"]
    assert res["tp2"] < res["tp1"]
    assert res["tp3"] < res["tp2"]


# ── R:R fields ─────────────────────────────────────────────────────────────
def test_rr_strings_format_correctly():
    res = calculate(
        entry=1.0900, direction="LONG",
        atr_15m=0.0010, atr_4h=0.0050,
        df_15m=_df15m(), sr_levels=[], pair="EUR/USD",
        matched_ob={"low": 1.0880, "high": 1.0895, "direction": "bullish"},
    )
    assert res["rr1"].startswith("1:")
    assert res["rr2"].startswith("1:")
    assert res["rr3"].startswith("1:")


# ── size_factor ────────────────────────────────────────────────────────────
def test_size_factor_50pct_halves_risk_pct_field():
    res = calculate(
        entry=1.0900, direction="LONG",
        atr_15m=0.0010, atr_4h=0.0050,
        df_15m=_df15m(), sr_levels=[], pair="EUR/USD",
        matched_ob={"low": 1.0880, "high": 1.0895, "direction": "bullish"},
        size_factor=0.5,
    )
    assert res["risk_pct"] == 0.5


# ── invalidation message ────────────────────────────────────────────────────
def test_invalidation_message_contains_sl_price():
    res = calculate(
        entry=1.0900, direction="LONG",
        atr_15m=0.0010, atr_4h=0.0050,
        df_15m=_df15m(), sr_levels=[], pair="EUR/USD",
        matched_ob={"low": 1.0880, "high": 1.0895, "direction": "bullish"},
    )
    assert str(res["sl_price"]) in res["invalidation"]
    assert "below" in res["invalidation"].lower()
