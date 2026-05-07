# Changelog

All notable changes to the SMC Trading Signal Engine.

## [Unreleased] — 2026-05-06

### Fixed

- **TP buffer can no longer push TP across entry** (`risk_manager._adjust_tp`)
  - When an S/R level sat within 5 pips of entry, the `±5×pip` buffer used to
    overshoot — producing SHORT TPs above entry or LONG TPs below entry.
  - Reproduced today on USD/CHF SHORT: `entry=0.77759`, level `0.77735` (3 touches),
    `0.77735 + 0.0005 = 0.77785` ended up **above** entry.
  - Fix: clamp the buffered result; if it would cross entry, fall back to the
    base R-multiple TP. (`risk_manager.py`)

- **Validator now hard-rejects wrong-side TP geometry** (`trade_validator._check_input_sanity`)
  - Defensive gate: SHORT signals with any TP ≥ entry, or LONG signals with
    any TP ≤ entry, are rejected with `INVALID_INPUT` before ever reaching MT5.
  - Catches malformed signals from any upstream source, not just risk_manager.

- **RR floor tolerates exact-threshold equality** (`trade_validator._check_rr`)
  - Strict `<` comparison was rejecting setups that landed exactly at the
    1.5R floor due to float-noise (`1.4999999 < 1.5` returned True).
  - Changed to `rr1 < MIN_RR_TP1 - 1e-6` — accepts exact equality and minor
    noise; anything materially below the floor still rejected.

### Tests

- 7 new tests covering both fixes:
  - `test_short_with_tp1_above_entry_rejected` (today's USD/CHF case)
  - `test_short_with_tp2_above_entry_rejected`
  - `test_long_with_tp1_below_entry_rejected`
  - `test_long_with_tp3_below_entry_rejected`
  - `test_rr_exactly_equal_to_min_passes`
  - `test_short_tp_buffer_does_not_overshoot_entry`
  - `test_long_tp_buffer_does_not_undershoot_entry`
- Full suite: **138 / 138 passing** (was 131).

### Files changed

- `risk_manager.py`
- `trade_validator.py`
- `tests/test_trade_validator.py`
- `tests/test_risk_manager.py`
- `CHANGELOG.md` (new)

### Deployment

- Local-only changes — running production engine **NOT touched**.
- Ready for git commit + push + coordinated VPS deploy.
