#!/usr/bin/env python3
"""Run operation-scoped workflow repair from the repository root."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modules.storage.workflow_repair import main

if __name__ == "__main__":
    raise SystemExit(main())
