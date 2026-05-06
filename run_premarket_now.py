"""Manually trigger the pre-market scan immediately."""
import sys, logging
from dotenv import load_dotenv
load_dotenv()
logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from premarket_scan import run_premarket_scan
run_premarket_scan()
