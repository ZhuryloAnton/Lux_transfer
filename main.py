#!/usr/bin/env python3
"""TaxiBOT Luxembourg — entry point. Run from project root."""

from __future__ import annotations

from pathlib import Path

# Ensure src is on path when running main.py from project root (or Docker /app)
import sys
_root = Path(__file__).resolve().parent
_src = _root / "src"
if _src.exists() and str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from taxibot import run

if __name__ == "__main__":
    run()
