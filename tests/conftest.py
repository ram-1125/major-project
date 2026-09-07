"""Keep import-time FastAPI construction away from genuine production data."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


_TEST_RUNTIME = Path(tempfile.mkdtemp(prefix="smartops-pytest-runtime-"))
os.environ["SMARTOPS_DB_PATH"] = str(_TEST_RUNTIME / "import-time.db")
os.environ["SMARTOPS_DEVICE_ID_PATH"] = str(_TEST_RUNTIME / "device-id")

