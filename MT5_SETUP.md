# MT5 Auto-Trading Setup — FxPro

There are **two ways** to wire the SMC engine to MT5. Both ship with this engine.
Pick the one that fits — or run both simultaneously for redundancy.

---

## Path A · Python → MT5 directly (recommended)

The engine calls `mt5_executor.execute_signal()` after every confirmed signal.
No EA needed. MT5 just has to be open with AutoTrading enabled.

### Steps

1. **Download FxPro MT5** from fxpro.com → install → log in to your demo account.
2. **Enable algo trading**
   - MT5 → `Tools` → `Options` → `Expert Advisors`
   - ✅ "Allow algorithmic trading"
   - ✅ "Disable algo trading via external Python API" should be **OFF** (it's disabled by default; just don't turn it on).
3. **Get your FxPro demo credentials**
   - From the FxPro account opening email, you have:
     - **Login** (account number, e.g. `12345678`)
     - **Password** (investor or master password — use **master**)
     - **Server** (e.g. `FxPro-MT5`, `FxPro-MT5.2`, `FxPro-MT5-Demo01`)
4. **Paste them into `.env`**:
   ```
   MT5_MODE=DEMO
   MT5_LOGIN=12345678
   MT5_PASSWORD=your_master_password
   MT5_SERVER=FxPro-MT5    ← exact server name from MT5's login dialog
   ```
5. **Install the Python SDK**:
   ```
   pip install MetaTrader5
   ```
6. **Restart the engine** — it auto-detects MT5 and starts executing.

### What happens when a signal fires
- Resolves engine pair → FxPro symbol (e.g. `EUR/USD` → `EURUSD`)
- Checks: spread, max positions (5), daily DD (3%)
- Computes lot size from `risk_money / (sl_distance × point_value)` in **GBP**
- Opens market order with SL attached
- Places 3 limit orders at TP1/TP2/TP3 (35%/40%/25% partial closes)
- Auto-trails SL to break-even when price hits +1.5R

---

## Path B · Plug-and-play EA (downloadable)

If you want pure MT5-side automation (no Python running),
use the bundled `MT5_SignalBot.mq5`.

### Steps

1. **Locate your MT5 data folder**
   - In MT5 → `File` → `Open Data Folder`
2. **Copy `MT5_SignalBot.mq5`** into `MQL5/Experts/`
3. **Open MetaEditor** (F4 inside MT5) → open the file → click `Compile` (or F7)
   - You should see `0 errors, 0 warnings`. A `MT5_SignalBot.ex5` file is created.
4. **Drop the engine's `signals.json` file** into `MQL5/Files/` (or symlink it)
   - The engine writes signals there continuously.
5. **Drag the EA onto any chart**
   - Set `RiskPctPerTrade = 1.0`, `MaxOpenPositions = 5`, `DemoOnly = true`
   - Click OK
6. **Confirm AutoTrading is on** (button top toolbar — should be green)

The EA polls `signals.json` every 5 seconds, dedupes by timestamp+asset,
and trades anything new.

---

## Safety checklist before flipping to LIVE

Run on demo for **at least 2 weeks** with a minimum of 15 trades. Verify:

- [ ] Lot sizing correctly risks 1% per trade
- [ ] SL/TP orders are placed at the right prices
- [ ] Spread filter rejects bad fills
- [ ] Max position cap enforces correctly
- [ ] Daily DD kill switch fires when triggered
- [ ] Symbol map is correct for every pair you trade

When you're ready:
1. Edit `.env` → `MT5_MODE=LIVE`
2. Update `MT5_LOGIN/PASSWORD/SERVER` to your live account
3. In the EA: set `DemoOnly = false`
4. **Start with a small live balance** (£200–500) for the first week

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `MT5 initialize failed` | MT5 must be open. Run as admin if Windows blocks. |
| `Symbol XYZ unavailable` | FxPro names this pair differently — check `Market Watch` for exact symbol, edit `SYMBOL_MAP` in `mt5_config.py`. |
| `order_send failed: 10018` | Market closed — algo can't trade weekends. |
| `order_send failed: 10027` | AutoTrading disabled — click the green button in MT5 toolbar. |
| EA not reading file | Ensure `signals.json` is in `MQL5/Files/`, not the engine root. |

---

## Files in this bundle

```
trading-engine-v3/
├── mt5_config.py           ← FxPro/GBP/1%/5pos rules + symbol map
├── mt5_executor.py         ← Python-side execution (Path A)
├── MT5_SignalBot.mq5       ← Standalone EA source (Path B)
├── MT5_SETUP.md            ← This file
├── .env                    ← MT5_LOGIN/PASSWORD/SERVER
└── main.py                 ← Auto-calls mt5_execute() after every signal
```
