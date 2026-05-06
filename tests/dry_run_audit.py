"""
Dry-run simulation of the 5 audit examples from SIGNAL_AUDIT_REPORT.md.

Drives the actual cooldown + validator + side-effect pipeline (via
pipeline_helper.run_pipeline) with mocks, so every assertion is real but
nothing is sent to Telegram/WhatsApp/MT5.
"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock

from cooldown_guard   import (
    record_signal_fired, record_structure_event,
    evaluate_cooldown, decision_to_payload,
)
from trade_validator  import OpenPosition, HTFBias
from pipeline_helper  import run_pipeline, _build_structure_id

logging.basicConfig(level=logging.WARNING, format="%(message)s")

# Use a temp file for cooldown state so production is untouched
TMP_STATE = Path(__file__).parent / "_dryrun_state.json"
TMP_BLOCKED = Path(__file__).parent / "_dryrun_blocked.json"
TMP_STATE.unlink(missing_ok=True)
TMP_BLOCKED.unlink(missing_ok=True)

import cooldown_guard
cooldown_guard.STATE_FILE         = TMP_STATE
cooldown_guard.BLOCKED_AUDIT_FILE = TMP_BLOCKED


def fresh_mocks():
    return {
        "send_telegram":          MagicMock(return_value=True),
        "send_whatsapp":          MagicMock(return_value=True),
        "push_chart":             MagicMock(return_value="background"),
        "execute_mt5":            MagicMock(return_value={"success": True, "ticket": 0}),
        "log_signal":             MagicMock(),
        "write_blocked":          MagicMock(),
        "record_signal_fired":    MagicMock(),
        "record_structure_event": MagicMock(),
    }


def show_signal(label, sig):
    print(f"  {label}")
    print(f"    asset={sig['asset']:8s} direction={sig['direction']:5s} "
          f"entry={sig['entry']:.5f} SL={sig['sl_price']:.5f} "
          f"TP1={sig['tp1']:.5f} TP2={sig['tp2']:.5f} TP3={sig['tp3']:.5f}")
    print(f"    confidence={sig['confidence']:8s} "
          f"matched_ob={sig.get('matched_ob',{}).get('idx_end','-')}")


def show_decision(sig, htf_bias, open_pos, recent_sigs, mocks):
    """Run pipeline, then dump the four-line decision summary."""
    structure_id = _build_structure_id(sig)
    cooldown_pos = []
    for p in open_pos:
        cooldown_pos.append(type("P", (), {
            "symbol": p.symbol, "direction": p.direction,
            "opened_at": getattr(p, "opened_at", None)
        })())

    # Run cooldown alone first to capture decision payload
    from cooldown_guard import OpenPositionRef
    cd_input = [OpenPositionRef(symbol=p.symbol, direction=p.direction,
                                opened_at=getattr(p, "opened_at", None))
                for p in open_pos]
    cd = evaluate_cooldown(sig["asset"], sig["direction"], structure_id, cd_input)
    cd_payload = decision_to_payload(cd)

    # Now run the full pipeline so we know what side effects fire
    result = run_pipeline(sig, htf_bias, open_pos, recent_sigs, mocks)

    print(f"  Cooldown   : {cd_payload['status']}"
          + (f" · {cd_payload['cooldown_code']}" if cd_payload['cooldown_code'] else "")
          + (f" · age={cd_payload['age_minutes']} min" if cd_payload.get('age_minutes') is not None else ""))

    if result == "EXECUTED":
        print(f"  Validation : APPROVED · all gates passed")
        print(f"  Action     : EXECUTED → Telegram ✓ WhatsApp ✓ TradingView ✓ MT5 ✓")
        print(f"  Reason     : confluence complete · setup_id={structure_id}")
    elif result == "BLOCKED_VALIDATION":
        rej = mocks["log_signal"].call_args.args[0]
        print(f"  Validation : REJECTED · {rej.get('rejection_code')}")
        print(f"  Action     : BLOCKED — no alerts, no MT5")
        print(f"  Reason     : {rej.get('rejection_message')}")
    elif result == "BLOCKED_COOLDOWN":
        print(f"  Validation : not run (cooldown blocked first)")
        print(f"  Action     : BLOCKED — write_blocked + log_signal only")
        print(f"  Reason     : {cd_payload['cooldown_reason']}")

    # Verify side effects match the expected gating
    if result == "EXECUTED":
        assert mocks["send_telegram"].called and mocks["execute_mt5"].called
    else:
        assert not mocks["send_telegram"].called
        assert not mocks["execute_mt5"].called
    print()


# ═══════════════════════════════════════════════════════════════════
def banner(title):
    print("═" * 70)
    print(title)
    print("═" * 70)


# ───────────────────────────────────────────────────────────────────
# CASE 1 · USD/CHF repeated LONG (28-Apr 09:13 → 11:48, 7 signals)
# ───────────────────────────────────────────────────────────────────
banner("CASE 1 · USD/CHF repeated LONG signals from 28-Apr")
print()

usdchf_long_template = {
    "asset": "USD/CHF", "direction": "LONG",
    "entry": 0.78894, "sl_price": 0.78774,    # 120-pip SL
    "tp1": 0.79124, "tp2": 0.79294, "tp3": 0.79480,   # 1:1.92 / 1:3.33 / 1:4.88
    "confidence": "HIGH", "atr_4h": 0.0030,   # 300-pip ATR → SL/ATR ratio 0.40 (passes)
    "session": "London",
    "matched_ob": {"idx_end": 191, "low": 0.78870, "high": 0.78920,
                   "direction": "bullish"},
    "smc_4h_event": {"type": "BOS_BULL", "idx": 290, "price": 0.78920},
}
bull_bias = HTFBias(symbol="USD/CHF", direction="BULL")

# First signal — should pass everything
show_signal("Input #1 (09:13 — initial setup)", usdchf_long_template)
m = fresh_mocks()
show_decision(usdchf_long_template, bull_bias, [], [], m)

# Manually persist the signal so the next call sees it as a duplicate
record_signal_fired("USD/CHF", "LONG", _build_structure_id(usdchf_long_template))

# Second signal — same OB, same direction, 16 min later
sig2 = {**usdchf_long_template, "entry": 0.78925, "sl_price": 0.78789,
        "tp1": 0.79129, "tp2": 0.79265}
show_signal("Input #2 (09:29 — same OB, 16 min later)", sig2)
m = fresh_mocks()
show_decision(sig2, bull_bias, [], [], m)

# Third signal — same OB again, 32 min later
sig3 = {**usdchf_long_template, "entry": 0.78971, "sl_price": 0.78789,
        "tp1": 0.79080, "tp2": 0.79426}
show_signal("Input #3 (09:45 — same OB, 32 min later)", sig3)
m = fresh_mocks()
show_decision(sig3, bull_bias, [], [], m)


# ───────────────────────────────────────────────────────────────────
# CASE 2 · USD/JPY LONG against bearish Daily bias
# ───────────────────────────────────────────────────────────────────
banner("CASE 2 · USD/JPY LONG with bearish Daily bias (DXY weakness)")
print()

usdjpy_long = {
    "asset": "USD/JPY", "direction": "LONG",
    "entry": 159.723, "sl_price": 159.573,
    "tp1": 159.973, "tp2": 160.123, "tp3": 160.323,   # 1:1.67 / 1:2.67 / 1:4.0
    "confidence": "HIGH", "atr_4h": 0.40,
    "session": "London/NY overlap",
    "matched_ob": {"idx_end": 220, "low": 159.700, "high": 159.750,
                   "direction": "bullish"},
    "smc_4h_event": {"type": "BOS_BULL", "idx": 280, "price": 159.723},
}
bear_daily = HTFBias(symbol="USD/JPY", direction="BEAR",
                     blocks_long=True,
                     note="Daily 200 EMA above price · DXY divergence")
show_signal("Input (28-Apr 12:19 — engine wanted LONG)", usdjpy_long)
m = fresh_mocks()
show_decision(usdjpy_long, bear_daily, [], [], m)


# ───────────────────────────────────────────────────────────────────
# CASE 3 · TP1 = TP2 = TP3 cluster
# ───────────────────────────────────────────────────────────────────
banner("CASE 3 · USD/JPY signal with TP cluster (TP1 = TP2 = TP3)")
print()

cluster_signal = {
    "asset": "USD/JPY", "direction": "LONG",
    "entry": 159.723, "sl_price": 159.573,
    "tp1": 159.78460, "tp2": 159.78460, "tp3": 159.78460,
    "confidence": "HIGH", "atr_4h": 0.40,
    "session": "London/NY overlap",
    "matched_ob": {"idx_end": 311, "low": 159.700, "high": 159.750,
                   "direction": "bullish"},
    "smc_4h_event": {"type": "BOS_BULL", "idx": 290, "price": 159.78460},
}
bull_bias_jpy = HTFBias(symbol="USD/JPY", direction="BULL")
show_signal("Input (28-Apr 12:19 — TPs all equal 159.78460)", cluster_signal)
m = fresh_mocks()
show_decision(cluster_signal, bull_bias_jpy, [], [], m)


# ───────────────────────────────────────────────────────────────────
# CASE 4 · USD/CHF SL too tight (< 0.3 × ATR_4H)
# ───────────────────────────────────────────────────────────────────
banner("CASE 4 · USD/CHF with 1.8-pip SL (too tight vs ATR)")
print()

tight_sl = {
    "asset": "USD/CHF", "direction": "SHORT",
    "entry": 0.78425, "sl_price": 0.78443,   # 1.8 pip stop
    "tp1": 0.78397, "tp2": 0.78379, "tp3": 0.78352,
    "confidence": "HIGH", "atr_4h": 0.0040,  # 40-pip ATR — SL = 4.5% of ATR
    "session": "London",
    "matched_ob": {"idx_end": 401, "low": 0.78420, "high": 0.78443,
                   "direction": "bearish"},
    "smc_4h_event": {"type": "BOS_BEAR", "idx": 295, "price": 0.78400},
}
bear_chf = HTFBias(symbol="USD/CHF", direction="BEAR")
show_signal("Input (27-Apr 10:32 — 1.8-pip SL on USD/CHF)", tight_sl)
m = fresh_mocks()
show_decision(tight_sl, bear_chf, [], [], m)


# ───────────────────────────────────────────────────────────────────
# CASE 5 · Gold (XAU/USD) — should pass when bias aligns
# ───────────────────────────────────────────────────────────────────
banner("CASE 5 · XAU/USD (Gold) — eligibility check")
print()

gold_short = {
    "asset": "XAU/USD", "direction": "SHORT",
    "entry": 4593.40, "sl_price": 4621.65,   # 28.25-point stop
    "tp1": 4548.00, "tp2": 4521.00, "tp3": 4480.30,   # 1:1.61 / 1:2.56 / 1:4.0
    "confidence": "HIGH", "atr_4h": 30.0,
    "session": "London/NY overlap",
    "matched_ob": {"idx_end": 510, "low": 4615.00, "high": 4625.00,
                   "direction": "bearish"},
    "smc_4h_event": {"type": "BOS_BEAR", "idx": 505, "price": 4593.40},
}
gold_bias = HTFBias(symbol="XAU/USD", direction="BEAR",
                    note="Daily 200 EMA above price · clear downtrend")
show_signal("Input (28-Apr 14:00 — Gold short on bearish daily)", gold_short)
m = fresh_mocks()
show_decision(gold_short, gold_bias, [], [], m)
print("  Note: validator approves XAU/USD when given properly · live engine")
print("        currently doesn't include Gold in session_gate.priority_pairs")
print("        — that's a separate config gap (see audit improvement #4).")
print()


# Cleanup
TMP_STATE.unlink(missing_ok=True)
TMP_BLOCKED.unlink(missing_ok=True)
print("═" * 70)
print("Dry-run complete · all 5 audit cases simulated.")
print("═" * 70)
