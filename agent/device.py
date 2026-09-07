"""Create and persist a random identifier for this local SmartOps device."""

from __future__ import annotations

import uuid
from pathlib import Path

from agent.config import get_device_id_path


def get_or_create_device_id(path: Path | None = None) -> str:
    """Return the saved device ID, creating it locally on first use."""
    device_id_path = path or get_device_id_path()
    device_id_path.parent.mkdir(parents=True, exist_ok=True)

    if device_id_path.exists():
        saved_id = device_id_path.read_text(encoding="utf-8").strip()
        if saved_id:
            return saved_id

    device_id = str(uuid.uuid4())
    device_id_path.write_text(device_id, encoding="utf-8")
    return device_id

