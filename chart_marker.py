"""
Push live SMC zones (OB / BOS / FVG / Entry / SL / TPs) to the TradingView chart
via the tradingview-mcp-jackson Node CLI by spawning a small marker script.
"""

import json
import subprocess
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

NODE_PATH = r"C:\Program Files\nodejs\node.exe"
TV_REPO   = Path.home() / "tradingview-mcp-jackson"
MARKER_JS = TV_REPO / "smc_marker.mjs"


def push_to_chart(signal: dict) -> str | None:
    """
    Spawns the smc_marker.mjs script with the signal payload as JSON.
    Returns the screenshot path or None on failure.
    """
    if not MARKER_JS.exists():
        logger.warning("smc_marker.mjs missing at %s — skipping chart push", MARKER_JS)
        return None

    # Fire-and-forget: spawn detached, never block the engine cycle.
    # The marker writes to TradingView in the background; if it hangs, the
    # engine still finishes the cycle and fires alerts/MT5 instantly.
    try:
        payload_path = TV_REPO / "_signal_payload.json"
        payload_path.write_text(json.dumps(signal, default=str), encoding="utf-8")

        # Detach the process — DETACHED_PROCESS = 0x00000008 on Windows
        DETACHED_PROCESS = 0x00000008
        subprocess.Popen(
            [NODE_PATH, str(MARKER_JS), str(payload_path)],
            cwd=str(TV_REPO),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=DETACHED_PROCESS,
            close_fds=True,
        )
        logger.info("Chart marker dispatched (background)")
        return "background"
    except Exception as e:
        logger.warning("Chart push spawn failed: %s", e)
        return None
