"""Canonical notification categories and explicit alert-severity mapping."""

from __future__ import annotations

from typing import Literal


NotificationCategory = Literal["advisory", "warning", "urgent"]
NOTIFICATION_CATEGORIES: tuple[NotificationCategory, ...] = (
    "advisory",
    "warning",
    "urgent",
)
DEFAULT_NOTIFICATION_CATEGORIES: tuple[NotificationCategory, ...] = (
    "warning",
    "urgent",
)

# Alert severities and notification categories are separate concepts. Every
# supported internal severity is mapped exactly once; unknown legacy values
# return None and are suppressed safely.
ALERT_SEVERITY_TO_NOTIFICATION_CATEGORY: dict[str, NotificationCategory] = {
    "informational": "advisory",
    "advisory": "advisory",
    "warning": "warning",
    "urgent": "urgent",
}

NOTIFICATION_CATEGORY_ORDER = {
    "advisory": 0,
    "warning": 1,
    "urgent": 2,
}


def notification_category_for_alert_severity(
    alert_severity: object,
) -> NotificationCategory | None:
    """Map an exact internal severity; never interpret labels or substrings."""
    if not isinstance(alert_severity, str):
        return None
    return ALERT_SEVERITY_TO_NOTIFICATION_CATEGORY.get(alert_severity)


def normalize_notification_categories(values: object) -> list[str]:
    """Validate stored/API categories while preserving deterministic order."""
    if not isinstance(values, list):
        return []
    selected = {value for value in values if value in NOTIFICATION_CATEGORIES}
    return [value for value in NOTIFICATION_CATEGORIES if value in selected]
