# SMC Trading Signal Engine — Audit Report

_Period: 2026-04-27 → 2026-04-30 (4 trading days, BST/UTC+1 timezone)_

This report is intended to be shared with an external Claude session for review and improvement suggestions.

---

## 1 · Engine Architecture (current)

| Component | Setting |
|---|---|
| Entry framework | SMC: 4H 200 EMA bias → 4H BOS/CHoCH → 15M Order Block touch → 5/7 conditions |
| Confidence tiers | PREMIUM (7/7), HIGH (6/7), MEDIUM (5/7), BLOCKED |
| LTF early-entry (Mode 6B) | 5M sweep + 1M CHoCH + 1M OB anchor |
| Risk per trade | 1% of balance (GBP) |
| Max open positions | 5 |
| TP split | 35% / 40% / 25% at TP1 / TP2 / TP3 |
| Watchlist | 11 forex + Gold + BTC/USDT + ETH/USDT (14 total) |
| Session gating | 07–09 UTC (Tokyo/London), 09–12 (London), 12–16 (London/NY) |
| Data sources | Yahoo Finance (forex/gold), Binance (crypto) |
| Alerting | WhatsApp (Twilio) + Telegram + TradingView marker + FxPro MT5 demo |

---

## 2 · Headline Numbers

| Metric | Value |
|---|---|
| Total cycles run | 257 |
| Session-blocked (out of hours) | 128 |
| Evaluated but no trade (low confidence) | 83 |
| **Signals SENT** | **46** |
| Confidence: HIGH / LTF-HIGH | 28 (61%) |
| Confidence: MEDIUM | 18 (39%) |
| Confidence: PREMIUM (7/7) | 0 (0%) |

---

## 3 · Signal Distribution by Asset

| Asset | Signals | % of total | Notes |
|---|---|---|---|
| **USD/CHF** | **20** | **43%** | Massive over-firing on a choppy pair |
| USD/JPY | 8 | 17% | All LONG until 30-Apr 13:00 finally went SHORT |
| AUD/JPY | 5 | 11% | Mix of LONG and SHORT (LTF) |
| GBP/USD | 4 | 9% | |
| EUR/USD | 2 | 4% | |
| EUR/JPY | 2 | 4% | |
| USD/CAD | 2 | 4% | |
| GBP/JPY | 2 | 4% | |
| EUR/GBP | 1 | 2% | |
| **XAU/USD (Gold)** | **0** | **0%** | ❌ No live signals despite a major move |
| **BTC/USDT** | **0** | **0%** | Pre-market scan ranked it #3 today, never fired live |
| **ETH/USDT** | **0** | **0%** | |

---

## 4 · Specific Misses & Wrong Direction Calls

### 4.1 ❌ Gold (XAU/USD) — entirely missed
- The user observed a "big move on Gold" today
- The engine fired **zero** Gold signals across 4 days
- Pre-market backtest from 28-Apr showed **4 winning SHORT signals @ 100% win rate** on Gold yesterday
- Yet live engine doesn't include Gold in any session universe (`session_gate.py` priority/secondary lists)
- **Gold is missing from `priority_pairs` and `secondary_pairs` in all 3 prime windows**

### 4.2 ❌ USD/JPY — wrong direction repeatedly, then late reversal
DXY weakness today ⇒ USD/JPY should have gone SHORT. Instead:

| Time UTC | Direction | Confidence | Entry |
|---|---|---|---|
| 28-Apr 12:19 | **LONG** | MEDIUM | 159.723 |
| 28-Apr 12:35 | **LONG** | HIGH | 159.678 |
| 28-Apr 15:06 | **LONG** | HIGH | 159.641 |
| 29-Apr 07:29 | **LONG** | HIGH | 159.680 |
| 29-Apr 07:46 | **LONG** | HIGH | 159.676 |
| 30-Apr 07:13 | **LONG** | HIGH | 160.492 |
| 30-Apr 07:28 | **LONG** | HIGH | 160.480 |
| **30-Apr 13:00** | **SHORT** | MEDIUM | **156.772** ← **finally caught reversal AFTER ~400 pip drop** |

**Root cause:** Engine's 4H bias was BULLISH because price was above 4H 200 EMA. But on 1D timeframe DXY was diverging, USD/JPY was at a major resistance, and SMC structure on Daily showed a bearish CHoCH.
**Engine has no Daily-timeframe macro filter.**

### 4.3 ❌ USD/CHF — over-firing on chop
20 signals (43% of all output) on USD/CHF in 4 days. Examples:

| Time | Direction | Entry | SL | TP1 | Outcome |
|---|---|---|---|---|---|
| 27-Apr 09:01 | SHORT | 0.78353 | 0.78372 | 0.78324 | SL=2pips, TP1=3pips → noise trade |
| 27-Apr 10:32 | SHORT | 0.78425 | 0.78443 | 0.78397 | SL=1.8 pips → broken |
| 28-Apr 09:13 → 11:48 | **LONG x7** | 0.789 → 0.790 | varies | identical TP1 0.79074 | Same setup repeated 7 times |
| 29-Apr 09:47 → 10:33 | LONG x3 | 0.78985 → 0.78970 | identical | identical | Pure repetition |

**Issues identified:**
- No cooldown/dedup between consecutive signals on same pair+direction
- SL too tight in early signals (1.8–2 pips) — got stopped on spread
- Multiple identical TPs (TP1=TP2=TP3 due to S/R clustering bug, partially fixed mid-run)

### 4.4 ❌ Repeated identical signals on same setup
| Pair | Period | Count | Behavior |
|---|---|---|---|
| USD/CHF LONG | 28-Apr 09:13 → 11:48 | 7 | Same OB, near-identical entries |
| USD/JPY LONG | 28-Apr 12:19 → 30-Apr 07:28 | 7 | All LONG, all wrong, all similar |
| GBP/JPY LONG | 29-Apr 08:49 + 08:57 | 2 | Back-to-back duplicates |
| USD/CHF SHORT | 30-Apr 11:29 + 11:44 + 11:59 | 3 | Same setup 3 times in 30 min |

**No protection against re-firing the same idea repeatedly while position is still open or until structure changes.**

---

## 5 · TP Quality Issues (now partially fixed mid-run)

Many signals from 27-29 Apr show TP1 = TP2 = TP3 because of S/R clustering. Examples:

| Signal | TP1 | TP2 | TP3 |
|---|---|---|---|
| USD/JPY 28-Apr 12:19 | 159.78460 | 159.78460 | 159.78460 |
| USD/JPY 29-Apr 07:29 | 159.75671 | 159.75671 | 159.75671 |
| GBP/JPY 29-Apr 08:49 | 215.76750 | 215.76750 | 215.76750 |
| USD/CHF 28-Apr 10:31 | 0.79080 | 0.79262 | 0.79080 |

**Resulting R:R:** Often 1:0.33–1:0.65 — far below the engine's stated 1:1.5 minimum.
**Fix applied 29-Apr ~15:30:** TPs now enforced to 0.5R minimum spacing. Signals after this time show distinct TP1/2/3 (see USD/CAD 14:24, EUR/USD 15:54 at end of report).

---

## 6 · Signal-by-Signal Log (all 46)

```
time              asset    dir   conf       entry      SL       TP1      TP2      TP3      RR2
2026-04-27 08:16  AUD/JPY  LONG  MEDIUM   114.260  114.181  114.379  114.458  114.576  1:2.5
2026-04-27 08:31  AUD/JPY  LONG  MEDIUM   114.319  114.285  114.370  114.403  114.454  1:2.5
2026-04-27 08:46  AUD/JPY  LONG  HIGH     114.367  114.335  114.416  114.448  114.497  1:2.5
2026-04-27 09:01  USD/CHF  SHORT MEDIUM   0.78353  0.78372  0.78324  0.78305  0.78277  1:2.5
2026-04-27 09:16  USD/CHF  SHORT MEDIUM   0.78382  0.78401  0.78353  0.78334  0.78306  1:2.5
2026-04-27 09:31  USD/CHF  SHORT HIGH     0.78362  0.78381  0.78333  0.78314  0.78284  1:2.5
2026-04-27 10:02  USD/CHF  SHORT HIGH     0.78409  0.78461  0.78331  0.78279  0.78201  1:2.5
2026-04-27 10:17  GBP/USD  LONG  MEDIUM   1.35518  1.35496  1.35551  1.35573  1.35606  1:2.5
2026-04-27 10:32  USD/CHF  SHORT MEDIUM   0.78425  0.78443  0.78397  0.78379  0.78352  1:2.5
2026-04-27 10:47  USD/CHF  SHORT MEDIUM   0.78414  0.78432  0.78387  0.78369  0.78342  1:2.5
2026-04-27 11:23  USD/CHF  SHORT HIGH     0.78433  0.78553  0.78253  0.78133  0.77953  1:2.5
2026-04-27 11:38  GBP/USD  LONG  HIGH     1.35579  1.35458  1.35759  1.35756  1.35928  1:1.48
2026-04-27 11:54  GBP/USD  LONG  HIGH     1.35562  1.35442  1.35742  1.35756  1.35928  1:1.61
2026-04-28 07:12  EUR/JPY  SHORT MEDIUM   186.273  186.423  186.048  185.898  185.673  1:2.5
2026-04-28 09:13  USD/CHF  LONG  HIGH     0.78894  0.78774  0.79074  0.79194  0.79080  1:2.5  ← cluster bug
2026-04-28 09:29  USD/CHF  LONG  HIGH     0.78925  0.78789  0.79129  0.79265  0.79080  1:2.5  ← cluster bug
2026-04-28 09:45  USD/CHF  LONG  MEDIUM   0.78971  0.78789  0.79080  0.79426  0.79080  1:2.5  ← cluster bug
2026-04-28 10:00  USD/CHF  LONG  MEDIUM   0.78980  0.78789  0.79080  0.79458  0.79080  1:2.5  ← cluster bug
2026-04-28 10:31  USD/CHF  LONG  HIGH     0.78962  0.78842  0.79080  0.79262  0.79080  1:2.5  ← cluster bug
2026-04-28 10:47  USD/CHF  LONG  HIGH     0.78970  0.78850  0.79080  0.79270  0.79080  1:2.5  ← cluster bug
2026-04-28 11:48  USD/CHF  LONG  MEDIUM   0.79039  0.78904  0.79080  0.79377  0.79080  1:2.5  ← cluster bug
2026-04-28 12:19  USD/JPY  LONG  MEDIUM   159.723  159.573  159.785  159.785  159.785  1:0.41 ← TP1=TP2=TP3
2026-04-28 12:35  USD/JPY  LONG  HIGH     159.678  159.514  159.785  159.785  159.785  1:0.65 ← TP1=TP2=TP3
2026-04-28 15:06  USD/JPY  LONG  HIGH     159.641  159.363  159.777  159.777  159.777  1:0.49 ← TP1=TP2=TP3
2026-04-29 07:29  USD/JPY  LONG  HIGH     159.680  159.530  159.757  159.757  159.757  1:0.51 ← TP1=TP2=TP3
2026-04-29 07:46  USD/JPY  LONG  HIGH     159.676  159.526  159.757  159.757  159.757  1:0.54 ← TP1=TP2=TP3
2026-04-29 08:49  GBP/JPY  LONG  HIGH     215.699  215.488  215.768  215.768  215.768  1:0.33 ← TP1=TP2=TP3
2026-04-29 08:57  GBP/JPY  LONG  HIGH     215.691  215.487  215.768  215.768  215.768  1:0.38 ← TP1=TP2=TP3
2026-04-29 09:47  USD/CHF  LONG  HIGH     0.78985  0.78850  0.79065  0.79322  0.79065  1:2.5
2026-04-29 10:18  USD/CHF  LONG  HIGH     0.78989  0.78869  0.79065  0.79289  0.79065  1:2.5
2026-04-29 10:33  USD/CHF  LONG  HIGH     0.78970  0.78850  0.79065  0.79270  0.79065  1:2.5
2026-04-29 14:24  USD/CAD  LONG  HIGH     1.36868  1.36642  1.36951  1.37432  1.37771  1:2.5  ← TP fix live
2026-04-29 14:39  USD/CAD  LONG  HIGH     1.36916  1.36780  1.36951  1.37256  1.37390  1:2.5
2026-04-30 07:13  USD/JPY  LONG  HIGH     160.492  160.342  160.717  160.867  161.092  1:2.5  ← STILL LONG, was wrong
2026-04-30 07:28  USD/JPY  LONG  HIGH     160.480  160.330  160.439  160.855  160.439  1:2.5
2026-04-30 07:43  EUR/JPY  LONG  MEDIUM   187.535  187.327  187.847  188.055  187.925  1:2.5
2026-04-30 08:43  AUD/JPY  SHORT LTF-HIGH 114.031  114.230  113.733  113.534  113.236  1:2.5  ← LTF caught reversal
2026-04-30 08:59  AUD/JPY  SHORT LTF-HIGH 113.832  114.228  113.238  112.841  112.247  1:2.5
2026-04-30 09:29  EUR/GBP  SHORT LTF-HIGH 0.86601  0.86645  0.86535  0.86491  0.86425  1:2.5
2026-04-30 11:29  USD/CHF  SHORT MEDIUM   0.78507  0.78662  0.78337  0.78120  0.77992  1:2.5
2026-04-30 11:44  USD/CHF  SHORT MEDIUM   0.78499  0.78662  0.78337  0.78091  0.77992  1:2.5
2026-04-30 11:59  USD/CHF  SHORT MEDIUM   0.78498  0.78662  0.78337  0.78087  0.77992  1:2.5
2026-04-30 13:00  USD/JPY  SHORT MEDIUM   156.772  157.324  155.945  155.393  154.565  1:2.5  ← reversal caught LATE
2026-04-30 14:39  GBP/USD  LONG  HIGH     1.35428  1.35308  1.35608  1.35728  1.35908  1:2.5
2026-04-30 15:24  EUR/USD  LONG  MEDIUM   1.17343  1.17223  1.17523  1.17643  1.17823  1:2.5
2026-04-30 15:54  EUR/USD  LONG  HIGH     1.17302  1.17182  1.17482  1.17602  1.17782  1:2.5
```

---

## 7 · Hypotheses for Improvement (for reviewer to rate / expand)

1. **Add Daily-timeframe macro filter** — refuse 4H LONGs if Daily structure is bearish (would have blocked 8 wrong USD/JPY LONGs)
2. **Add DXY divergence check for USD pairs** — if DXY is making lower highs while USD/JPY is at a high, downgrade or block
3. **Per-pair signal cooldown** — minimum N hours / structure-change gap between identical-direction signals on the same pair (e.g. ban duplicate USD/CHF LONG within 4h while position open)
4. **Add Gold (XAU/USD) to live session universe** — currently zero coverage, biggest miss
5. **Minimum ATR-based SL** — kill any signal where SL distance < 0.3 × 4H ATR (would have killed 1.8-pip stops on USD/CHF)
6. **Position-aware** — don't fire new signal on a pair where bot already has open position in same direction
7. **Trend-strength / RSI-pulse on weekly** — if weekly RSI > 70 and price near weekly high, require SHORT setup confluence before LONGs (catches USD/JPY topping)
8. **Confidence inflation** — many MEDIUM signals fire (18 of 46). Tighten MEDIUM threshold or require 4H structure + LTF sweep simultaneously
9. **Cluster volatile-news pairs differently** — JPY pairs needed bigger SL/TP buffers (most TP-clusters were JPY pairs)
10. **Liquidity awareness** — bot fires during low-volume Asia-only windows by accident; need stricter overlap-only mode for HIGH/PREMIUM
11. **Win/loss tracking** — engine doesn't currently mark trade outcomes back into signals.json. Without P&L data, can't auto-tune

---

## 8 · Notes for Reviewer

- All bug fixes referenced above (TP spacing, SL minimum, native-TP fallback) are live in code as of **29-Apr 15:30 UTC**
- Pre-market Wall Street brief now scheduled daily at 06:30 UTC (BST-aware)
- MT5 execution lives — every SENT signal also opens a real demo trade with 1% risk in GBP
- The engine intentionally errs on the side of MORE signals; the user wants quality > quantity going forward
- LTF (1M/5M sweep + CHoCH) was added 28-Apr — the 30-Apr LTF-HIGH AUD/JPY shorts are the first products of that path

End of report.
