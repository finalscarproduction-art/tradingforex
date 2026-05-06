"""
Module 8 — TradingView Pine Script generator.
Renders a v5 indicator with EMAs, S/R lines, position box, TP/SL lines, label.
"""

from datetime import datetime, timezone
from pathlib import Path
from config import OUTPUTS_DIR


def generate_pine_script(signal: dict, sr_levels: list[dict]) -> str:
    is_long = signal["direction"] == "LONG"
    pair    = signal["asset"].replace("/", "")
    ts      = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    arrow   = "▲ LONG" if is_long else "▼ SHORT"
    box_clr = "color.green" if is_long else "color.red"
    inv_clr = "color.red"   if is_long else "color.green"
    lbl_sty = "label.style_label_up" if is_long else "label.style_label_down"

    # Build S/R hlines (max 6)
    sr_lines = []
    for i, lvl in enumerate(sr_levels[:6]):
        alpha  = 20 + i * 8
        clr    = "color.red" if lvl["type"] == "resistance" else "color.green"
        if lvl["type"] == "round":
            clr = "color.gray"
        title  = f"{lvl['type'].title()} {lvl['touches']}t"
        sr_lines.append(
            f'hline({lvl["price"]:.5f}, "{title}", '
            f'color=color.new({clr}, {alpha}), linewidth=1)'
        )

    sr_block = "\n".join(sr_lines) if sr_lines else "// no S/R within range"

    # ── FVG box (auto-extends until mitigated/filled) ──────────────────
    fvg = signal.get("fvg") or {}
    if fvg and fvg.get("top") and fvg.get("bottom"):
        fvg_color = "color.green" if fvg["direction"] == "bullish" else "color.red"
        fvg_alpha = 80 if fvg.get("filled") else (60 if fvg.get("mitigated") else 70)
        fvg_block = f"""
// ── FVG zone ({fvg['direction']}, score {fvg.get('score',0)}/10) ────────
var box fvgBox = box.new(bar_index - 30, {fvg['top']:.5f},
    bar_index + 50, {fvg['bottom']:.5f},
    bgcolor=color.new({fvg_color}, {fvg_alpha}),
    border_color=color.new({fvg_color}, 30),
    extend=extend.right)
"""
    else:
        fvg_block = "// no actionable FVG on this signal\n"

    pine = f"""//@version=5
indicator("{signal['asset']} {arrow} {ts}", overlay=true)

// ── EMAs ─────────────────────────────────────────────────────────────
ema200 = ta.ema(close, 200)
ema50  = ta.ema(close, 50)
ema21  = ta.ema(close, 21)
plot(ema200, "200 EMA", color=color.new(color.yellow, 0),  linewidth=2)
plot(ema50,  "50 EMA",  color=color.new(color.orange, 0),  linewidth=1)
plot(ema21,  "21 EMA",  color=color.new(color.blue,   0),  linewidth=1)

// ── Support / Resistance ─────────────────────────────────────────────
{sr_block}

{fvg_block}

// ── Trade plan ───────────────────────────────────────────────────────
entry_price = {signal['entry']:.5f}
sl_price    = {signal['sl_price']:.5f}
tp1         = {signal['tp1']:.5f}
tp2         = {signal['tp2']:.5f}
tp3         = {signal['tp3']:.5f}

// Position box: reward zone (entry → TP2)
var box rewardBox = box.new(bar_index, {('entry_price, bar_index + 40, tp2' if is_long else 'tp2, bar_index + 40, entry_price')},
    bgcolor=color.new({box_clr}, 88), border_color=color.new({box_clr}, 50))

// Stop zone (entry → SL)
var box stopBox = box.new(bar_index, {('sl_price, bar_index + 40, entry_price' if is_long else 'entry_price, bar_index + 40, sl_price')},
    bgcolor=color.new({inv_clr}, 88), border_color=color.new({inv_clr}, 50))

// TP lines
hline(tp1, "TP1 35% out",  color=color.new(color.blue,   0), linestyle=hline.style_dashed)
hline(tp2, "TP2 40% out",  color=color.new(color.teal,   0), linestyle=hline.style_dashed)
hline(tp3, "TP3 trail 25%",color=color.new(color.purple, 0), linestyle=hline.style_dashed)

// ── Signal label ─────────────────────────────────────────────────────
if barstate.islast
    label.new(bar_index, {'high' if is_long else 'low'},
        "{arrow} {signal['asset']}\\n" +
        "Entry: {signal['entry']:.5f}  SL: {signal['sl_price']:.5f} ({signal['sl_pips']}p)\\n" +
        "TP1: {signal['tp1']:.5f}  TP2: {signal['tp2']:.5f}  TP3: {signal['tp3']:.5f}\\n" +
        "R:R TP2 {signal['rr2']}  |  Confidence: {signal['confidence']}\\n" +
        "Session: {signal.get('session','')}  |  {ts} UTC",
        style={lbl_sty},
        color=color.new({box_clr}, 10),
        textcolor=color.white, size=size.small)
"""
    return pine.strip() + "\n"


def save_pine_script(signal: dict, sr_levels: list[dict]) -> str:
    """Generates and writes a .pine file. Returns the absolute path."""
    Path(OUTPUTS_DIR).mkdir(parents=True, exist_ok=True)
    pair_clean = signal["asset"].replace("/", "")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    fname = f"tv_{pair_clean}_{ts}.pine"
    fpath = Path(OUTPUTS_DIR) / fname

    fpath.write_text(generate_pine_script(signal, sr_levels), encoding="utf-8")
    return str(fpath)
