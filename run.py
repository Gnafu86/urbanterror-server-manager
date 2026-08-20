#!/usr/bin/env python3
"""Launch the Urban Terror Server Manager."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from utsm.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
