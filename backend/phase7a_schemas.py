"""Request models for Phase 7A notification preferences."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


NotificationSeverity = Literal[
    "informational",
    "advisory",
    "warning",
    "urgent",
]
NotificationCategory = Literal["advisory", "warning", "urgent"]


class NotificationPreferencesUpdate(BaseModel):
    enabled: bool
    eligible_categories: list[NotificationCategory] | None = None
    # Phase 7A compatibility for clients that still send this field.
    eligible_severities: list[NotificationSeverity] | None = None
