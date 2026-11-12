"""The agent's tests run on any system: the Windows-only layer (driver.py, win32.py, ui.py)
is replaced by stand-ins. Tests that drive the real API routes need ``apps/api`` and its
requirements; they are skipped when those are missing."""

import sys
from pathlib import Path

API = Path(__file__).resolve().parents[2] / "api"
if API.is_dir() and str(API) not in sys.path:
    sys.path.insert(0, str(API))
