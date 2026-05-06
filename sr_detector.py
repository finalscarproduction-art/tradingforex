"""
Module 3 — Support & Resistance Detection
Swing-pivot + clustering + round numbers.
"""

import pandas as pd


def detect_sr(df: pd.DataFrame, current_price: float, pair: str,
              lookback: int = 5, filter_pips: int = 300) -> list[dict]:
    """
    Returns sorted list of S/R levels (strongest first):
      [{"price": 1.08245, "type": "support"|"resistance"|"round",
        "touches": 3, "distance_pips": 12.4}, ...]
    """
    if df is None or len(df) < 2 * lookback + 1:
        return []

    pip = _pip_size(pair)
    levels: list[dict] = []

    high = df["high"]
    low  = df["low"]

    # Swing highs / lows
    for i in range(lookback, len(df) - lookback):
        h = high.iloc[i]
        l = low.iloc[i]

        is_high = all(h > high.iloc[i - j] for j in range(1, lookback + 1)) and \
                  all(h > high.iloc[i + j] for j in range(1, lookback + 1))
        is_low  = all(l < low.iloc[i - j]  for j in range(1, lookback + 1)) and \
                  all(l < low.iloc[i + j]  for j in range(1, lookback + 1))

        if is_high:
            levels.append({"price": float(h), "type": "resistance", "touches": 1})
        if is_low:
            levels.append({"price": float(l), "type": "support",    "touches": 1})

    # Round-number psychological levels
    if pip == 0.0001:
        round_step = 0.0050
    elif pip == 0.01:
        round_step = 0.500
    else:
        round_step = max(round(current_price / 100), 1)

    base = round(current_price / round_step) * round_step
    for i in range(-3, 4):
        rnd = round(base + i * round_step, 5)
        if rnd > 0:
            levels.append({"price": rnd, "type": "round", "touches": 1})

    # Cluster levels within 0.1%
    levels = _cluster(levels, current_price)

    # Filter by proximity (±filter_pips)
    nearby = []
    for lvl in levels:
        dist_pips = abs(lvl["price"] - current_price) / pip
        if dist_pips <= filter_pips:
            lvl["distance_pips"] = round(dist_pips, 1)
            lvl["side"] = "above" if lvl["price"] > current_price else "below"
            nearby.append(lvl)

    nearby.sort(key=lambda x: (-x["touches"], x["distance_pips"]))
    return nearby


def nearest_levels(sr_levels: list[dict], current_price: float) -> dict:
    """Pulls nearest support, resistance, and an immediate-block (within 20 pips)."""
    above = [l for l in sr_levels if l["side"] == "above" and l["type"] != "round"]
    below = [l for l in sr_levels if l["side"] == "below" and l["type"] != "round"]
    above.sort(key=lambda x: x["distance_pips"])
    below.sort(key=lambda x: x["distance_pips"])

    nearest_res = above[0] if above else None
    nearest_sup = below[0] if below else None

    immediate = next(
        (l for l in sr_levels if l["distance_pips"] <= 20 and l["touches"] >= 2),
        None,
    )

    return {
        "nearest_resistance": nearest_res,
        "nearest_support":    nearest_sup,
        "immediate_block":    immediate,
    }


def _cluster(levels: list[dict], price_ref: float, threshold_pct: float = 0.001) -> list[dict]:
    """Merge levels within 0.1% of each other. Sum the touches."""
    if not levels:
        return []

    levels_sorted = sorted(levels, key=lambda x: x["price"])
    clustered: list[dict] = []

    for lvl in levels_sorted:
        if clustered and abs(lvl["price"] - clustered[-1]["price"]) / price_ref < threshold_pct:
            existing = clustered[-1]
            total_touches = existing["touches"] + lvl["touches"]
            avg_price = (existing["price"] * existing["touches"] +
                         lvl["price"] * lvl["touches"]) / total_touches
            existing["price"]   = round(avg_price, 6)
            existing["touches"] = total_touches
            # round + swing combined: keep stronger type
            if lvl["type"] != "round":
                existing["type"] = lvl["type"]
        else:
            clustered.append(dict(lvl))

    return clustered


def _pip_size(pair: str) -> float:
    p = pair.upper()
    if "JPY" in p:
        return 0.01
    if any(c in p for c in ["BTC", "ETH", "SOL", "BNB"]):
        return 1.0
    return 0.0001
