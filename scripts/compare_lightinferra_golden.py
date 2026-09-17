#!/usr/bin/env python3
"""Compare Turn-2 AIPerf results for cache regeneration and remote KV."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lightinferra_golden_results import main

if __name__ == "__main__":
    main()
