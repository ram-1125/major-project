"""Configuration shared by the SmartOps agent and local API."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from agent.notification_policy import (
    DEFAULT_NOTIFICATION_CATEGORIES,
    NOTIFICATION_CATEGORIES,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_DATABASE_PATH = DATA_DIR / "smartops.db"
DEFAULT_DEVICE_ID_PATH = DATA_DIR / "device_id"
DEFAULT_SAMPLING_INTERVAL_SECONDS = 30
DEFAULT_DASHBOARD_URL = "http://localhost:5173"
# Backward-compatible names remain importable, but values are canonical
# notification categories rather than display labels or alert severities.
DEFAULT_NOTIFICATION_SEVERITIES = DEFAULT_NOTIFICATION_CATEGORIES
ALLOWED_NOTIFICATION_SEVERITIES = NOTIFICATION_CATEGORIES


def get_database_path() -> Path:
    """Return the configured database path, or the local project default."""
    configured_path = os.getenv("SMARTOPS_DB_PATH")
    return Path(configured_path).expanduser() if configured_path else DEFAULT_DATABASE_PATH


def get_device_id_path() -> Path:
    """Return the configured device ID path, or the local project default."""
    configured_path = os.getenv("SMARTOPS_DEVICE_ID_PATH")
    return Path(configured_path).expanduser() if configured_path else DEFAULT_DEVICE_ID_PATH


def get_sampling_interval() -> int:
    """Read the interval from the environment while keeping a safe default."""
    raw_value = os.getenv(
        "SMARTOPS_INTERVAL_SECONDS",
        str(DEFAULT_SAMPLING_INTERVAL_SECONDS),
    )
    try:
        interval = int(raw_value)
    except ValueError as error:
        raise ValueError("SMARTOPS_INTERVAL_SECONDS must be a whole number.") from error

    if interval < 1:
        raise ValueError("SMARTOPS_INTERVAL_SECONDS must be at least 1 second.")
    return interval


def get_dashboard_url() -> str:
    """Return a validated loopback dashboard origin for notification links."""
    raw_value = os.getenv("SMARTOPS_DASHBOARD_URL", DEFAULT_DASHBOARD_URL).strip()
    parsed = urlsplit(raw_value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(
            "SMARTOPS_DASHBOARD_URL must be a local http(s) URL using "
            "localhost, 127.0.0.1, or ::1."
        )
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")


def get_default_notification_severities() -> tuple[str, ...]:
    """Read the local first-run notification-category policy."""
    raw_value = os.getenv("SMARTOPS_NOTIFICATION_SEVERITIES")
    if raw_value is None:
        return DEFAULT_NOTIFICATION_SEVERITIES
    values = tuple(
        dict.fromkeys(
            value.strip().lower()
            for value in raw_value.split(",")
            if value.strip()
        )
    )
    invalid = set(values) - set(ALLOWED_NOTIFICATION_SEVERITIES)
    if invalid or not values:
        allowed = ", ".join(ALLOWED_NOTIFICATION_SEVERITIES)
        raise ValueError(
            "SMARTOPS_NOTIFICATION_SEVERITIES must contain one or more of: "
            f"{allowed}."
        )
    return values
