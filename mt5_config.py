"""
MT5 trading rules — FxPro / GBP account / 1% risk / max 5 positions
DEMO is enforced by default. Set MT5_MODE=LIVE in .env only after demo period.
"""

import os
from dotenv import load_dotenv
load_dotenv()

# ── Mode (DEMO is the only safe default) ─────────────────────────────────────
MT5_MODE = os.getenv("MT5_MODE", "DEMO").upper()
LIVE_TRADING = MT5_MODE == "LIVE"

# DRY_RUN — when true, the executor logs the order it WOULD send but never
# calls mt5.order_send. Alerts (Telegram/WhatsApp/chart) still fire so you
# can audit the full pipeline without any real fills.
DRY_RUN = os.getenv("DRY_RUN", "false").lower() in ("true", "1", "yes")

# ── Account credentials (entered by user in .env) ────────────────────────────
MT5_LOGIN    = int(os.getenv("MT5_LOGIN", "0"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER   = os.getenv("MT5_SERVER", "FxPro-MT5")   # FxPro demo: FxPro-MT5

# ── Risk parameters ──────────────────────────────────────────────────────────
ACCOUNT_CURRENCY     = "GBP"
RISK_PCT_PER_TRADE   = 1.0
MAX_OPEN_POSITIONS   = 3
MAX_DAILY_DRAWDOWN_PCT = 3.0     # kill switch: stop trading if account down >3% on the day
MAX_SLIPPAGE_PIPS    = 3
MIN_LOT              = 0.01
MAX_LOT              = 5.0       # hard cap regardless of risk math

# ── TP partial-close split ───────────────────────────────────────────────────
TP1_CLOSE_PCT = 0.35
TP2_CLOSE_PCT = 0.40
TP3_CLOSE_PCT = 0.25

# ── Symbol map (engine → FxPro broker symbol) ────────────────────────────────
# FxPro generally uses standard names. Edit if your account uses suffixes.
SYMBOL_MAP = {
    "EUR/USD":  "EURUSD",
    "GBP/USD":  "GBPUSD",
    "USD/JPY":  "USDJPY",
    "AUD/USD":  "AUDUSD",
    "USD/CAD":  "USDCAD",
    "NZD/USD":  "NZDUSD",
    "EUR/GBP":  "EURGBP",
    "GBP/JPY":  "GBPJPY",
    "EUR/JPY":  "EURJPY",
    "USD/CHF":  "USDCHF",
    "AUD/JPY":  "AUDJPY",
    "XAU/USD":  "GOLD",       # FxPro names gold "GOLD"
    "BTC/USDT": "BITCOIN",    # FxPro crypto CFD
    "ETH/USDT": "ETHEREUM",
}

# ── Symbols this bot is allowed to trade (whitelist) ─────────────────────────
ALLOWED_SYMBOLS = set(SYMBOL_MAP.values())

# ── Spread filter ────────────────────────────────────────────────────────────
MAX_SPREAD_PIPS_FX     = 2.0
MAX_SPREAD_PIPS_JPY    = 3.0
MAX_SPREAD_POINTS_GOLD = 50

MAGIC_NUMBER = 20260428   # tag every order so the EA only manages its own trades
EXPERT_NAME  = "SMC_Engine_v3"
