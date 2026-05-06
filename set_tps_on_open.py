"""
Retroactively set native TP on every currently-open SMC bot position
that has no TP. Uses TP1 (1.5R) from the original SL distance.
"""
import sys
from dotenv import load_dotenv
load_dotenv()
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import MetaTrader5 as mt5
from mt5_executor import connect
from mt5_config   import MAGIC_NUMBER

if not connect():
    print("MT5 connect failed.")
    sys.exit(1)

positions = mt5.positions_get() or []
mine = [p for p in positions if p.magic == MAGIC_NUMBER]

if not mine:
    print("No open SMC positions on the account.")
    mt5.shutdown()
    sys.exit(0)

print(f"Found {len(mine)} open SMC positions:\n")

for p in mine:
    info = mt5.symbol_info(p.symbol)
    digits = info.digits
    sl_dist = abs(p.price_open - p.sl) if p.sl else 0
    is_long = (p.type == mt5.POSITION_TYPE_BUY)

    if sl_dist == 0:
        print(f"  #{p.ticket} {p.symbol} — no SL set, skipping")
        continue
    if p.tp:
        print(f"  #{p.ticket} {p.symbol} — already has TP {p.tp:.{digits}f}, skipping")
        continue

    # TP1 = 1.5 × risk distance from open price
    tp1 = p.price_open + 1.5 * sl_dist if is_long else p.price_open - 1.5 * sl_dist
    tp1 = round(tp1, digits)

    # Respect broker stops level
    stops_pts = info.trade_stops_level or 0
    point     = info.point
    min_dist  = (stops_pts + 1) * point
    tick      = mt5.symbol_info_tick(p.symbol)
    market    = tick.bid if is_long else tick.ask
    if is_long and tp1 < market + min_dist:
        tp1 = round(market + min_dist, digits)
    if not is_long and tp1 > market - min_dist:
        tp1 = round(market - min_dist, digits)

    req = {
        "action":   mt5.TRADE_ACTION_SLTP,
        "position": p.ticket,
        "symbol":   p.symbol,
        "sl":       p.sl,
        "tp":       tp1,
    }
    r = mt5.order_send(req)
    if r and r.retcode == mt5.TRADE_RETCODE_DONE:
        rr = abs(tp1 - p.price_open) / sl_dist
        print(f"  ✅ #{p.ticket} {p.symbol} {('LONG' if is_long else 'SHORT')} "
              f"@ {p.price_open:.{digits}f}  →  TP set to {tp1:.{digits}f} "
              f"(R:R 1:{rr:.2f})")
    else:
        err = (r.retcode, r.comment) if r else ("none", "no result")
        print(f"  ❌ #{p.ticket} {p.symbol} — failed: {err} | last_error={mt5.last_error()}")

mt5.shutdown()
print("\nDone.")
