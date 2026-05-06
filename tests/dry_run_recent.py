"""
DRY-RUN — walks each recent signal example through every gate
without touching MT5, Telegram, WhatsApp, or TradingView.

Stages printed per example:
  1. SMC detected      (assumed by the input)
  2. Cooldown result
  3. Validator result
  4. MT5 safety result
  5. Final action      (BLOCKED / WOULD_ALERT / WOULD_EXECUTE)
"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import logging, json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock

# silence module logs — dry-run only prints stage results
logging.basicConfig(level=logging.CRITICAL)

import cooldown_guard
TMP_STATE   = Path(__file__).parent / "_dry_state.json"
TMP_BLOCKED = Path(__file__).parent / "_dry_blocked.json"
TMP_STATE.unlink(missing_ok=True); TMP_BLOCKED.unlink(missing_ok=True)
cooldown_guard.STATE_FILE         = TMP_STATE
cooldown_guard.BLOCKED_AUDIT_FILE = TMP_BLOCKED

from cooldown_guard  import (
    evaluate_cooldown, decision_to_payload, record_signal_fired,
    OpenPositionRef,
)
from trade_validator import (
    SetupCandidate, HTFBias, OpenPosition, RecentSignal,
    validate, is_approved,
)
from mt5_safety import (
    OrderIntent, precheck_signal, validate_order_request,
    _order_type_for_intent,
)
from pipeline_helper import _build_structure_id


# ── Fake MT5 module ─────────────────────────────────────────────────────────
class FakeMT5:
    ORDER_TYPE_BUY        = 0
    ORDER_TYPE_SELL       = 1
    ORDER_TYPE_BUY_LIMIT  = 2
    ORDER_TYPE_SELL_LIMIT = 3
    ORDER_TYPE_BUY_STOP   = 4
    ORDER_TYPE_SELL_STOP  = 5
fake_mt5 = FakeMT5()


# ── Helpers ─────────────────────────────────────────────────────────────────
def _setup_from(sig: dict, ts: datetime) -> SetupCandidate:
    return SetupCandidate(
        setup_id   = _build_structure_id(sig),
        symbol     = sig["asset"], direction = sig["direction"],
        entry      = sig["entry"],  sl       = sig["sl_price"],
        tp1        = sig["tp1"],    tp2      = sig["tp2"], tp3 = sig["tp3"],
        confidence = (sig.get("confidence", "HIGH") or "HIGH").split(" ")[0],
        atr_4h     = sig.get("atr_4h", 0.0030),
        timestamp  = ts,
    )


def banner(title: str) -> None:
    print("\n" + "═" * 74)
    print(f"  {title}")
    print("═" * 74)


def show_input(sig: dict) -> None:
    print("  📥 INPUT SIGNAL:")
    print(f"     asset={sig['asset']} · direction={sig['direction']} · conf={sig.get('confidence','HIGH')}")
    print(f"     entry={sig['entry']:.5f}  SL={sig['sl_price']:.5f}")
    print(f"     TP1={sig['tp1']:.5f}  TP2={sig['tp2']:.5f}  TP3={sig['tp3']:.5f}")
    print(f"     atr_4h={sig.get('atr_4h', 0):.5f}")


def dry_run(sig: dict, htf_bias: HTFBias,
             open_positions=None, recent_signals=None):
    open_positions = open_positions or []
    recent_signals = recent_signals or []
    structure_id   = _build_structure_id(sig)
    ts             = datetime.now(timezone.utc)

    show_input(sig)

    # ── 1. SMC detected (input pre-condition) ─────────────────────────
    print("\n  [1] SMC detected     : ✅ (assumed by input)")
    print(f"      structure_id     : {structure_id}")

    # ── 2. Cooldown ──────────────────────────────────────────────────
    cd_pos = [OpenPositionRef(symbol=p.symbol, direction=p.direction,
                              opened_at=getattr(p, "opened_at", None))
              for p in open_positions]
    cd  = evaluate_cooldown(sig["asset"], sig["direction"], structure_id, cd_pos)
    cdp = decision_to_payload(cd)
    print(f"  [2] Cooldown result  : {cdp['status']}"
          + (f" · {cdp['cooldown_code']}" if cdp['cooldown_code'] else ""))
    if cdp["cooldown_reason"]:
        print(f"      reason           : {cdp['cooldown_reason']}")

    if cdp["status"] == "BLOCKED_SIGNAL":
        print(f"  [3] Validator        : ⏭  not run")
        print(f"  [4] MT5 safety       : ⏭  not run")
        print(f"  [5] FINAL ACTION     : 🚫 BLOCKED  (audit-only · no side effects)")
        _print_payload({
            "status":          "BLOCKED_SIGNAL",
            "stage":           "cooldown",
            "cooldown_code":   cdp["cooldown_code"],
            "cooldown_reason": cdp["cooldown_reason"],
            "age_minutes":     cdp["age_minutes"],
            "asset":           sig["asset"],
            "direction":       sig["direction"],
        })
        return

    # ── 3. Validator ─────────────────────────────────────────────────
    setup  = _setup_from(sig, ts)
    val    = validate(setup, htf_bias, open_positions, recent_signals)

    if not is_approved(val):
        print(f"  [3] Validator result : ❌ REJECTED · {val.code.value}")
        print(f"      reason           : {val.message}")
        print(f"  [4] MT5 safety       : ⏭  not run")
        print(f"  [5] FINAL ACTION     : 🚫 BLOCKED  (no alerts · no MT5)")
        _print_payload({
            "status":            "BLOCKED_SIGNAL",
            "stage":             "validator",
            "rejection_code":    val.code.value,
            "rejection_message": val.message,
            "asset":             sig["asset"],
            "direction":         sig["direction"],
        })
        return

    print(f"  [3] Validator result : ✅ APPROVED · gates={val.reasons_passed}")
    print(f"      RR1 = 1:{val.rr1}  ·  confidence = {val.confidence}")

    # ── 4. MT5 safety ────────────────────────────────────────────────
    geo = precheck_signal(sig)
    if not geo.ok:
        print(f"  [4] MT5 safety       : ❌ BLOCKED · {geo.code}")
        print(f"      reason           : {geo.reason}")
        print(f"  [5] FINAL ACTION     : ⚠️  WOULD_ALERT only "
              f"(Telegram/WhatsApp would fire BEFORE MT5 — but MT5 refused)")
        _print_payload({
            "status":     "BLOCKED_SIGNAL",
            "stage":      "mt5_safety",
            "geometry_code":   geo.code,
            "geometry_reason": geo.reason,
            "asset":      sig["asset"],
            "direction":  sig["direction"],
        })
        return

    # Final pre-send simulated request
    order_type = _order_type_for_intent(sig["direction"], OrderIntent.ENTRY_ORDER, fake_mt5)
    req = {"type": order_type, "symbol": sig["asset"].replace("/", ""),
           "volume": 0.10, "price": sig["entry"],
           "sl": sig["sl_price"], "tp": 0.0}
    audit = validate_order_request(req, sig["direction"],
                                   OrderIntent.ENTRY_ORDER, fake_mt5)
    if not audit.ok:
        print(f"  [4] MT5 safety       : ❌ BLOCKED · {audit.code}")
        print(f"  [5] FINAL ACTION     : 🚫 BLOCKED")
        return

    type_name = {0:"BUY",1:"SELL",2:"BUY_LIMIT",3:"SELL_LIMIT",
                 4:"BUY_STOP",5:"SELL_STOP"}[order_type]
    print(f"  [4] MT5 safety       : ✅ PASSED · order_type={type_name} · intent=ENTRY_ORDER")

    # ── 5. Final ─────────────────────────────────────────────────────
    print(f"  [5] FINAL ACTION     : ✅ WOULD_EXECUTE")
    print(f"      → would alert Telegram + WhatsApp + TradingView")
    print(f"      → would open MT5 {type_name} {req['symbol']} @ {req['price']:.5f}")
    print(f"      → would attach SL={req['sl']:.5f}")
    print(f"      → would place 3 partial-TP exits (35/40/25 split)")
    _print_payload({
        "status":      "WOULD_EXECUTE",
        "asset":       sig["asset"],
        "direction":   sig["direction"],
        "order_type":  type_name,
        "intent":      "ENTRY_ORDER",
        "entry":       sig["entry"],
        "sl":          sig["sl_price"],
        "tp1":         sig["tp1"], "tp2": sig["tp2"], "tp3": sig["tp3"],
        "confidence":  sig.get("confidence", "HIGH"),
        "rr1":         val.rr1,
    })


def _print_payload(payload: dict):
    print("\n  📦 Final payload:")
    for line in json.dumps(payload, indent=2).split("\n"):
        print(f"     {line}")


# ───────────────────────────────────────────────────────────────────
# EXAMPLE 1 — USD/CHF duplicate SHORT
# ───────────────────────────────────────────────────────────────────
banner("EXAMPLE 1 · USD/CHF — duplicate SHORT (within 4h cooldown)")

usdchf_short = {
    "asset": "USD/CHF", "direction": "SHORT",
    "entry": 0.78100, "sl_price": 0.78250,
    "tp1": 0.77900, "tp2": 0.77750, "tp3": 0.77500,
    "confidence": "HIGH", "atr_4h": 0.00400,
    "matched_ob": {"idx_end": 191, "low": 0.78100, "high": 0.78180,
                   "direction": "bearish"},
}
bear_chf = HTFBias(symbol="USD/CHF", direction="BEAR")

print("\n  → Pre-firing one signal so the next is a duplicate …")
record_signal_fired("USD/CHF", "SHORT", _build_structure_id(usdchf_short))
print()
dry_run(usdchf_short, bear_chf)


# ───────────────────────────────────────────────────────────────────
# EXAMPLE 2 — Valid USD/CHF SHORT (TPs below entry — geometrically correct)
# ───────────────────────────────────────────────────────────────────
banner("EXAMPLE 2 · USD/CHF SHORT with TPs below entry (clean signal)")

# Use a different OB so cooldown doesn't block this one
usdchf_short_ok = {**usdchf_short,
                   "matched_ob": {"idx_end": 220, "low": 0.78400, "high": 0.78440,
                                  "direction": "bearish"},
                   "entry": 0.78400, "sl_price": 0.78550,
                   "tp1": 0.78175, "tp2": 0.78050, "tp3": 0.77800}
print()
dry_run(usdchf_short_ok, bear_chf)


# ───────────────────────────────────────────────────────────────────
# EXAMPLE 3 — USD/JPY LONG against bearish Daily bias
# ───────────────────────────────────────────────────────────────────
banner("EXAMPLE 3 · USD/JPY LONG against bearish Daily bias")

usdjpy_long = {
    "asset": "USD/JPY", "direction": "LONG",
    "entry": 159.723, "sl_price": 159.523,
    "tp1": 160.073, "tp2": 160.273, "tp3": 160.573,
    "confidence": "HIGH", "atr_4h": 0.40,
    "matched_ob": {"idx_end": 311, "low": 159.700, "high": 159.750,
                   "direction": "bullish"},
}
bear_daily_jpy = HTFBias(symbol="USD/JPY", direction="BEAR",
                         blocks_long=True,
                         note="Daily 200 EMA above price · DXY divergence")
print()
dry_run(usdjpy_long, bear_daily_jpy)


# ───────────────────────────────────────────────────────────────────
# EXAMPLE 4 — XAU/USD (Gold) — full pipeline approval
# ───────────────────────────────────────────────────────────────────
banner("EXAMPLE 4 · XAU/USD (Gold) SHORT — should pass all gates")

gold_short = {
    "asset": "XAU/USD", "direction": "SHORT",
    "entry": 4593.40, "sl_price": 4623.00,
    "tp1": 4540.00, "tp2": 4500.00, "tp3": 4450.00,
    "confidence": "HIGH", "atr_4h": 30.0,
    "matched_ob": {"idx_end": 510, "low": 4615.00, "high": 4625.00,
                   "direction": "bearish"},
}
bear_gold = HTFBias(symbol="XAU/USD", direction="BEAR",
                    note="Daily downtrend · price below 200D EMA")
print()
dry_run(gold_short, bear_gold)


# ───────────────────────────────────────────────────────────────────
# EXAMPLE 5 — MEDIUM-confidence signal
# ───────────────────────────────────────────────────────────────────
banner("EXAMPLE 5 · GBP/USD MEDIUM-confidence signal")

gbpusd_medium = {
    "asset": "GBP/USD", "direction": "LONG",
    "entry": 1.35400, "sl_price": 1.35280,
    "tp1": 1.35580, "tp2": 1.35700, "tp3": 1.35900,
    "confidence": "MEDIUM", "atr_4h": 0.0040,
    "matched_ob": {"idx_end": 412, "low": 1.35350, "high": 1.35420,
                   "direction": "bullish"},
}
bull_gbp = HTFBias(symbol="GBP/USD", direction="BULL")
print()
dry_run(gbpusd_medium, bull_gbp)


# ───────────────────────────────────────────────────────────────────
# Cleanup
TMP_STATE.unlink(missing_ok=True)
TMP_BLOCKED.unlink(missing_ok=True)
print("\n" + "═" * 74)
print("  Dry-run complete · no MT5/Telegram/WhatsApp/TradingView calls made")
print("═" * 74)
