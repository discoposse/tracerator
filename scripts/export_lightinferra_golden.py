#!/usr/bin/env python3
"""CLI entry point for Tracerator's LightInferra-style golden trace set."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lightinferra_golden import main

if __name__ == "__main__":
    main()
