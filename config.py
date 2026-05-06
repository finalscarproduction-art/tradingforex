import os
from dotenv import load_dotenv

load_dotenv()

# Full universe (filtered per-session by asset_scanner)
FOREX_PAIRS = [
    "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CAD",
    "NZD/USD", "EUR/GBP", "GBP/JPY", "EUR/JPY", "USD/CHF",
    "AUD/JPY",
]

CRYPTO_PAIRS = ["BTC/USDT", "ETH/USDT"]

# ── API Keys ──────────────────────────────────────────────────────────────────
TWILIO_SID    = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN  = os.getenv("TWILIO_AUTH_TOKEN")
WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
WHATSAPP_TO   = os.getenv("ALERT_WHATSAPP_TO")

# ── Engine settings ──────────────────────────────────────────────────────────
SCAN_INTERVAL_MINUTES = 15
MIN_ASSET_SCORE = 7

# ── Confidence tiers ─────────────────────────────────────────────────────────
TIER_PREMIUM_CONDITIONS = 5   # 5/5 + EMA aligned both TFs
TIER_HIGH_CONDITIONS    = 4   # 4/5 + EMA aligned 4H
TIER_MEDIUM_CONDITIONS  = 3   # 3/5 + EMA aligned 4H

# ── Risk ─────────────────────────────────────────────────────────────────────
RISK_PER_TRADE_PCT = 1.0
ATR_BUFFER_MULTIPLIER = 0.5
MAX_SL_ATR_MULTIPLIER = 2.0   # SL must not exceed 2× 4H ATR
MIN_SL_PIPS_FX = 12           # never tighter than 12 pips on FX
MIN_SL_PIPS_JPY = 15
MIN_SL_PIPS_CRYPTO = 30       # crypto in points

# ── SMC ──────────────────────────────────────────────────────────────────────
SMC_SWING_LOOKBACK_4H = 3
SMC_SWING_LOOKBACK_15M = 2
SMC_OB_PROXIMITY_PCT = 0.0015  # within 0.15% of OB = "in zone"
REQUIRE_OB_TOUCH = True        # entry must be inside or just outside an active OB

# ── Calendar ─────────────────────────────────────────────────────────────────
HARD_BLOCK_MINUTES  = 30   # 30 min around HIGH-impact events
REDUCE_HOURS        = 2
WARN_HOURS          = 6

# ── 200 EMA / S/R ────────────────────────────────────────────────────────────
EXTENDED_FROM_EMA_PIPS = 150   # flag pullback risk if price >150 pips from 4H 200 EMA
SR_PROXIMITY_PIPS      = 20    # asset scores +1 if price within 20 pips of S/R
SR_LOOKBACK_BARS_4H    = 5
SR_LOOKBACK_BARS_15M   = 3
SR_CLUSTER_THRESHOLD   = 0.001 # 0.1% to merge swing levels into a zone
SR_FILTER_PIPS         = 300   # only show levels within ±300 pips of price

OUTPUTS_DIR = "outputs"
LOG_FILE    = "engine.log"
SIGNALS_DB  = "signals.json"
