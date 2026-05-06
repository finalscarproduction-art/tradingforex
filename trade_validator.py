"""
Stage 6 — Trade Validation Layer
================================

Single hard gate between SMC setup detection and execution.
Setup must pass every check or be rejected with a typed reason.

Rejection rules enforced (every one logs why):
  1. Confidence below HIGH                 → CONFIDENCE_TOO_LOW
  2. R:R on TP1 < 1.5                      → RR_TOO_LOW
  3. SL distance < 0.3 × ATR_4H            → SL_TOO_TIGHT
  4. Same setup_id seen within 4 hours     → DUPLICATE_SIGNAL
  5. Open position same symbol+direction   → POSITION_ALREADY_OPEN
  6. Daily HTF bias contradicts direction  → HTF_BIAS_CONFLICT

Pure function: given the same inputs, returns the same output. No I/O.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Literal, Optional, Union

logger = logging.getLogger(__name__)

# ── Tunable thresholds ───────────────────────────────────────────────────────
MIN_RR_TP1               = 1.5      # R:R floor on TP1
MIN_SL_ATR_RATIO         = 0.3      # SL must be ≥ this fraction of 4H ATR
DUPLICATE_WINDOW_HOURS   = 4        # cooldown for same setup_id
ALLOWED_CONFIDENCE_TIERS = {"HIGH", "PREMIUM", "LTF-HIGH"}


# ── Rejection taxonomy ───────────────────────────────────────────────────────
class RejectionCode(str, Enum):
    CONFIDENCE_TOO_LOW    = "CONFIDENCE_TOO_LOW"
    RR_TOO_LOW            = "RR_TOO_LOW"
    SL_TOO_TIGHT          = "SL_TOO_TIGHT"
    TP_CLUSTERED          = "TP_CLUSTERED"
    DUPLICATE_SIGNAL      = "DUPLICATE_SIGNAL"
    POSITION_ALREADY_OPEN = "POSITION_ALREADY_OPEN"
    HTF_BIAS_CONFLICT     = "HTF_BIAS_CONFLICT"
    INVALID_INPUT         = "INVALID_INPUT"


# ── Data contracts ───────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SetupCandidate:
    setup_id:   str
    symbol:     str
    direction:  Literal["LONG", "SHORT"]
    entry:      float
    sl:         float
    tp1:        float
    tp2:        float
    tp3:        float
    confidence: Literal["MEDIUM", "HIGH", "LTF-HIGH", "PREMIUM"]
    atr_4h:     float
    timestamp:  datetime


@dataclass(frozen=True)
class HTFBias:
    symbol:       str
    direction:    Literal["BULL", "BEAR", "NEUTRAL"]
    blocks_long:  bool = False
    blocks_short: bool = False
    note:         str = ""


@dataclass(frozen=True)
class OpenPosition:
    symbol:    str
    direction: Literal["LONG", "SHORT"]
    ticket:    int


@dataclass(frozen=True)
class RecentSignal:
    setup_id:  str
    symbol:    str
    direction: str
    timestamp: datetime


@dataclass(frozen=True)
class ValidatedTrade:
    setup_id:       str
    symbol:         str
    direction:      str
    entry:          float
    sl:             float
    tp1:            float
    tp2:            float
    tp3:            float
    rr1:            float
    confidence:     str
    timestamp:      datetime
    reasons_passed: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RejectionReason:
    code:     RejectionCode
    message:  str
    setup_id: str
    symbol:   str
    details:  dict = field(default_factory=dict)


ValidationResult = Union[ValidatedTrade, RejectionReason]


# ── Individual gates (each returns RejectionReason or None) ──────────────────
def _check_input_sanity(s: SetupCandidate) -> Optional[RejectionReason]:
    if s.entry <= 0 or s.sl <= 0 or s.tp1 <= 0:
        return _reject(RejectionCode.INVALID_INPUT, s,
                       "Non-positive price field",
                       {"entry": s.entry, "sl": s.sl, "tp1": s.tp1})
    if s.atr_4h <= 0:
        return _reject(RejectionCode.INVALID_INPUT, s,
                       "ATR_4H must be > 0", {"atr_4h": s.atr_4h})
    # Direction sanity vs SL position
    if s.direction == "LONG"  and s.sl >= s.entry:
        return _reject(RejectionCode.INVALID_INPUT, s,
                       "LONG: SL must be below entry",
                       {"entry": s.entry, "sl": s.sl})
    if s.direction == "SHORT" and s.sl <= s.entry:
        return _reject(RejectionCode.INVALID_INPUT, s,
                       "SHORT: SL must be above entry",
                       {"entry": s.entry, "sl": s.sl})
    return None


def _check_confidence(s: SetupCandidate) -> Optional[RejectionReason]:
    if s.confidence not in ALLOWED_CONFIDENCE_TIERS:
        return _reject(
            RejectionCode.CONFIDENCE_TOO_LOW, s,
            f"Confidence '{s.confidence}' below HIGH floor",
            {"confidence": s.confidence,
             "allowed": sorted(ALLOWED_CONFIDENCE_TIERS)},
        )
    return None


def _check_rr(s: SetupCandidate) -> Optional[RejectionReason]:
    risk   = abs(s.entry - s.sl)
    reward = abs(s.tp1   - s.entry)
    if risk == 0:
        return _reject(RejectionCode.RR_TOO_LOW, s,
                       "Risk distance is zero", {"entry": s.entry, "sl": s.sl})
    rr1 = reward / risk
    if rr1 < MIN_RR_TP1:
        return _reject(
            RejectionCode.RR_TOO_LOW, s,
            f"R:R on TP1 = 1:{rr1:.2f} < required 1:{MIN_RR_TP1}",
            {"rr1": round(rr1, 3), "min": MIN_RR_TP1,
             "entry": s.entry, "sl": s.sl, "tp1": s.tp1},
        )
    return None


def _check_sl_vs_atr(s: SetupCandidate) -> Optional[RejectionReason]:
    sl_dist = abs(s.entry - s.sl)
    min_sl  = MIN_SL_ATR_RATIO * s.atr_4h
    if sl_dist < min_sl:
        return _reject(
            RejectionCode.SL_TOO_TIGHT, s,
            f"SL distance {sl_dist:.6f} < {MIN_SL_ATR_RATIO} × ATR_4H ({min_sl:.6f})",
            {"sl_dist": sl_dist, "atr_4h": s.atr_4h,
             "ratio_required": MIN_SL_ATR_RATIO,
             "actual_ratio": round(sl_dist / s.atr_4h, 3) if s.atr_4h else None},
        )
    return None


def _check_tp_distinctness(s: SetupCandidate) -> Optional[RejectionReason]:
    """
    Reject clustered TPs. Two failure modes:
      1. Any two TPs are exactly equal (TP1==TP2, TP2==TP3, or TP1==TP3)
      2. Any pairwise gap is < 0.5 × |TP1 − Entry|  (the first reward unit)

    Both signal a broken risk_manager output that would either be rejected
    by MT5 (duplicate-price pendings) or compress R:R below intent.
    """
    tp1, tp2, tp3 = s.tp1, s.tp2, s.tp3

    # Exact-equality clusters
    pairs = [("TP1", tp1, "TP2", tp2),
             ("TP2", tp2, "TP3", tp3),
             ("TP1", tp1, "TP3", tp3)]
    for la, va, lb, vb in pairs:
        if va == vb:
            return _reject(
                RejectionCode.TP_CLUSTERED, s,
                f"{la} == {lb} ({va}) — TPs must be distinct",
                {"tp1": tp1, "tp2": tp2, "tp3": tp3, "duplicate_pair": f"{la}={lb}"},
            )

    # Min-gap rule — gap ≥ 0.5 × first reward unit
    first_reward = abs(tp1 - s.entry)
    if first_reward <= 0:
        return _reject(
            RejectionCode.TP_CLUSTERED, s,
            "TP1 equals entry — no reward unit to enforce spacing",
            {"tp1": tp1, "entry": s.entry},
        )
    min_gap = 0.5 * first_reward

    if abs(tp2 - tp1) < min_gap:
        return _reject(
            RejectionCode.TP_CLUSTERED, s,
            f"TP1↔TP2 gap {abs(tp2-tp1):.6f} < required {min_gap:.6f} (0.5 × first reward)",
            {"tp1": tp1, "tp2": tp2, "gap": abs(tp2-tp1), "min_gap": min_gap},
        )
    if abs(tp3 - tp2) < min_gap:
        return _reject(
            RejectionCode.TP_CLUSTERED, s,
            f"TP2↔TP3 gap {abs(tp3-tp2):.6f} < required {min_gap:.6f} (0.5 × first reward)",
            {"tp2": tp2, "tp3": tp3, "gap": abs(tp3-tp2), "min_gap": min_gap},
        )

    return None


def _check_duplicate(s: SetupCandidate,
                      recent_signals: list[RecentSignal]) -> Optional[RejectionReason]:
    cutoff = s.timestamp - timedelta(hours=DUPLICATE_WINDOW_HOURS)
    for r in recent_signals:
        if r.timestamp < cutoff:
            continue
        same_setup     = (r.setup_id == s.setup_id)
        same_pair_dir  = (r.symbol == s.symbol and r.direction == s.direction)
        if same_setup or same_pair_dir:
            age_min = round((s.timestamp - r.timestamp).total_seconds() / 60, 1)
            return _reject(
                RejectionCode.DUPLICATE_SIGNAL, s,
                f"{'Same setup_id' if same_setup else 'Same pair+direction'} fired {age_min} min ago",
                {"prior_setup_id": r.setup_id, "prior_timestamp": r.timestamp.isoformat(),
                 "window_hours": DUPLICATE_WINDOW_HOURS, "age_min": age_min},
            )
    return None


def _check_open_position(s: SetupCandidate,
                          open_positions: list[OpenPosition]) -> Optional[RejectionReason]:
    for p in open_positions:
        if p.symbol == s.symbol and p.direction == s.direction:
            return _reject(
                RejectionCode.POSITION_ALREADY_OPEN, s,
                f"Already have {p.direction} position on {p.symbol} (ticket {p.ticket})",
                {"existing_ticket": p.ticket, "existing_direction": p.direction},
            )
    return None


def _check_htf_bias(s: SetupCandidate,
                     bias: HTFBias) -> Optional[RejectionReason]:
    if bias.symbol != s.symbol:
        return _reject(
            RejectionCode.HTF_BIAS_CONFLICT, s,
            f"HTFBias is for {bias.symbol}, setup is for {s.symbol}",
            {"bias_symbol": bias.symbol},
        )
    # Hard veto flags first
    if s.direction == "LONG"  and bias.blocks_long:
        return _reject(
            RejectionCode.HTF_BIAS_CONFLICT, s,
            f"LONG vetoed by Daily bias: {bias.note or bias.direction}",
            {"bias_direction": bias.direction, "blocks_long": True},
        )
    if s.direction == "SHORT" and bias.blocks_short:
        return _reject(
            RejectionCode.HTF_BIAS_CONFLICT, s,
            f"SHORT vetoed by Daily bias: {bias.note or bias.direction}",
            {"bias_direction": bias.direction, "blocks_short": True},
        )
    # Direction-mismatch check
    expected = "LONG" if bias.direction == "BULL" else "SHORT" if bias.direction == "BEAR" else None
    if expected is None:
        return _reject(
            RejectionCode.HTF_BIAS_CONFLICT, s,
            "Daily bias is NEUTRAL — no directional trades allowed",
            {"bias_direction": bias.direction},
        )
    if expected != s.direction:
        return _reject(
            RejectionCode.HTF_BIAS_CONFLICT, s,
            f"Setup {s.direction} contradicts Daily bias {bias.direction}",
            {"bias_direction": bias.direction, "setup_direction": s.direction},
        )
    return None


# ── Helpers ──────────────────────────────────────────────────────────────────
def _reject(code: RejectionCode, s: SetupCandidate,
            message: str, details: dict) -> RejectionReason:
    rej = RejectionReason(
        code=code, message=message, setup_id=s.setup_id,
        symbol=s.symbol, details=details,
    )
    logger.warning(
        "REJECT [%s] %s %s — %s | details=%s",
        code.value, s.symbol, s.direction, message, details,
    )
    return rej


# ── Public API ───────────────────────────────────────────────────────────────
def validate(
    setup:           SetupCandidate,
    htf_bias:        HTFBias,
    open_positions:  list[OpenPosition],
    recent_signals:  list[RecentSignal],
) -> ValidationResult:
    """
    Run every gate. Return ValidatedTrade if all pass, RejectionReason otherwise.
    Gates run in cheap-to-expensive order so we fail fast.
    """
    passed: list[str] = []

    # Gate 0 — input sanity
    rej = _check_input_sanity(setup)
    if rej: return rej
    passed.append("input_sanity")

    # Gate 1 — confidence floor
    rej = _check_confidence(setup)
    if rej: return rej
    passed.append("confidence")

    # Gate 2 — R:R on TP1
    rej = _check_rr(setup)
    if rej: return rej
    passed.append("rr_tp1")

    # Gate 3 — SL ≥ 0.3 × ATR
    rej = _check_sl_vs_atr(setup)
    if rej: return rej
    passed.append("sl_vs_atr")

    # Gate 3b — TP distinctness (no clustering, min spacing)
    rej = _check_tp_distinctness(setup)
    if rej: return rej
    passed.append("tp_distinct")

    # Gate 4 — duplicate / cooldown
    rej = _check_duplicate(setup, recent_signals)
    if rej: return rej
    passed.append("no_duplicate")

    # Gate 5 — already-open position
    rej = _check_open_position(setup, open_positions)
    if rej: return rej
    passed.append("no_open_position")

    # Gate 6 — Daily HTF bias
    rej = _check_htf_bias(setup, htf_bias)
    if rej: return rej
    passed.append("htf_bias_aligned")

    # All passed — build approved trade
    risk = abs(setup.entry - setup.sl)
    rr1  = abs(setup.tp1 - setup.entry) / risk

    trade = ValidatedTrade(
        setup_id   = setup.setup_id,
        symbol     = setup.symbol,
        direction  = setup.direction,
        entry      = setup.entry,
        sl         = setup.sl,
        tp1        = setup.tp1,
        tp2        = setup.tp2,
        tp3        = setup.tp3,
        rr1        = round(rr1, 2),
        confidence = setup.confidence,
        timestamp  = setup.timestamp,
        reasons_passed = passed,
    )
    logger.info(
        "✅ APPROVED %s %s @ %.5f · SL %.5f · TP1 %.5f (RR 1:%.2f) · conf=%s · gates=%s",
        trade.symbol, trade.direction, trade.entry, trade.sl, trade.tp1,
        trade.rr1, trade.confidence, ",".join(passed),
    )
    return trade


def is_approved(result: ValidationResult) -> bool:
    return isinstance(result, ValidatedTrade)


# ── Self-test demonstrating each rejection path ──────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")

    now = datetime.now(timezone.utc)
    base = dict(setup_id="EURUSD_OB_2026-04-30T08:15", symbol="EUR/USD",
                direction="LONG", entry=1.17000, sl=1.16800, tp1=1.17400,
                tp2=1.17600, tp3=1.18000, atr_4h=0.0030, timestamp=now)

    bias_bull = HTFBias(symbol="EUR/USD", direction="BULL")
    bias_bear = HTFBias(symbol="EUR/USD", direction="BEAR", blocks_long=True,
                        note="Daily 200 EMA above price")

    print("\n── Test 1 · all gates pass ────────────────────────────────")
    s = SetupCandidate(**base, confidence="HIGH")
    r = validate(s, bias_bull, [], []); print(type(r).__name__)

    print("\n── Test 2 · MEDIUM confidence rejected ────────────────────")
    s = SetupCandidate(**base, confidence="MEDIUM")
    r = validate(s, bias_bull, [], []); print(r.code.value)

    print("\n── Test 3 · RR < 1.5 rejected ─────────────────────────────")
    s = SetupCandidate(**{**base, "tp1": 1.17050}, confidence="HIGH")
    r = validate(s, bias_bull, [], []); print(r.code.value)

    print("\n── Test 4 · SL too tight (< 0.3 × ATR) rejected ───────────")
    s = SetupCandidate(**{**base, "sl": 1.16995, "tp1": 1.17050}, confidence="HIGH")
    r = validate(s, bias_bull, [], []); print(r.code.value)

    print("\n── Test 5 · duplicate within 4h rejected ──────────────────")
    s = SetupCandidate(**base, confidence="HIGH")
    prior = RecentSignal(setup_id=s.setup_id, symbol=s.symbol,
                         direction=s.direction, timestamp=now - timedelta(hours=2))
    r = validate(s, bias_bull, [], [prior]); print(r.code.value)

    print("\n── Test 6 · open position same direction rejected ─────────")
    s = SetupCandidate(**base, confidence="HIGH")
    pos = OpenPosition(symbol="EUR/USD", direction="LONG", ticket=99999)
    r = validate(s, bias_bull, [pos], []); print(r.code.value)

    print("\n── Test 7 · Daily bias conflict rejected ──────────────────")
    s = SetupCandidate(**base, confidence="HIGH")
    r = validate(s, bias_bear, [], []); print(r.code.value)

    print("\nAll seven gates demonstrated.\n")
