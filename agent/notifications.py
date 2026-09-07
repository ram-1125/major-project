"""Native Windows notification delivery owned by the telemetry agent.

Automatic notification dispatch runs once, immediately after alert evaluation,
inside the existing non-overlapping agent cycle. The API may send an explicit
test toast, but it never runs a competing automatic dispatcher.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode

from agent.config import get_dashboard_url, get_database_path
from agent.notification_policy import (
    NOTIFICATION_CATEGORY_ORDER,
    notification_category_for_alert_severity,
)
from backend.database import (
    database_connection,
    initialize_database,
    run_write_transaction,
)
from backend.phase7a_repository import (
    complete_notification_delivery,
    delivery_categories_for_alert,
    eligible_alert_rows,
    ensure_notification_preferences,
    reserve_notification_delivery,
)
from backend.phase7b1_repository import record_notification_decision


LOGGER = logging.getLogger("smartops.notifications")
PROVIDER_NAME = "windows_toasts"
SEMANTIC_SEVERITIES = {
    "informational": "Low",
    "advisory": "Elevated",
    "warning": "High",
    "urgent": "Critical Evidence",
}


@dataclass(frozen=True)
class NotificationMessage:
    title: str
    body: str
    deep_link_url: str


@dataclass(frozen=True)
class NotificationResult:
    delivered: bool
    provider_name: str
    failure_reason: str | None = None


class NotificationProvider(Protocol):
    """Small provider seam so hardware-dependent behavior is mockable."""

    provider_name: str

    def support_status(self) -> dict[str, str | bool | None]:
        ...

    def send(self, message: NotificationMessage) -> NotificationResult:
        ...


class UnsupportedNotificationProvider:
    provider_name = "unsupported"

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def support_status(self) -> dict[str, str | bool | None]:
        return {
            "supported": False,
            "status": "unsupported",
            "provider_name": self.provider_name,
            "reason": self.reason,
        }

    def send(self, message: NotificationMessage) -> NotificationResult:
        del message
        return NotificationResult(False, self.provider_name, self.reason)


class WindowsToastNotificationProvider:
    """Submit a native Windows 10/11 toast through the WinRT toast API."""

    provider_name = PROVIDER_NAME

    def __init__(self) -> None:
        from windows_toasts import WindowsToaster

        self._toaster = WindowsToaster("SmartOps")

    def support_status(self) -> dict[str, str | bool | None]:
        return {
            "supported": True,
            "status": "supported",
            "provider_name": self.provider_name,
            "reason": None,
        }

    def send(self, message: NotificationMessage) -> NotificationResult:
        try:
            from windows_toasts import Toast

            toast = Toast()
            toast.text_fields = [message.title, message.body]
            toast.launch_action = message.deep_link_url
            self._toaster.show_toast(toast)
            return NotificationResult(True, self.provider_name)
        except Exception as error:  # WinRT availability varies by session/policy.
            return NotificationResult(
                False,
                self.provider_name,
                f"{type(error).__name__}: {error}",
            )


def create_notification_provider() -> NotificationProvider:
    """Create the safe platform provider without making startup fatal."""
    if sys.platform != "win32":
        return UnsupportedNotificationProvider(
            "Native SmartOps notifications require Windows 10 or Windows 11."
        )
    try:
        return WindowsToastNotificationProvider()
    except Exception as error:
        return UnsupportedNotificationProvider(
            "The Windows notification provider could not initialize: "
            f"{type(error).__name__}: {error}"
        )


def build_alert_deep_link(
    alert_id: int,
    dashboard_url: str | None = None,
) -> str:
    """Build an encoded local hash-route link to one exact alert."""
    if isinstance(alert_id, bool) or not isinstance(alert_id, int) or alert_id < 1:
        raise ValueError("alert_id must be a positive integer.")
    base_url = dashboard_url or get_dashboard_url()
    query = urlencode({"alertId": str(alert_id)})
    return f"{base_url}/#/predictive-alerts?{query}"


def build_test_deep_link(dashboard_url: str | None = None) -> str:
    return f"{dashboard_url or get_dashboard_url()}/#/settings"


def build_settings_deep_link(dashboard_url: str | None = None) -> str:
    """Build the validated local Settings route used by configuration feedback."""
    return f"{dashboard_url or get_dashboard_url()}/#/settings"


def _message_for_alert(alert: dict[str, object], deep_link: str) -> NotificationMessage:
    severity = str(alert["current_severity"])
    semantic = SEMANTIC_SEVERITIES.get(severity, "Important")
    factor = str(alert.get("probable_factor") or alert.get("category") or "system")
    factor = factor.replace("_", " ").strip()
    workload = str(alert.get("workload_context") or "current")
    title = (
        "SmartOps — High System Risk"
        if severity == "warning"
        else f"SmartOps — {semantic}"
    )
    body = (
        f"Important failure-risk signals were detected in {factor} during the "
        f"{workload} workload. Click to view details."
    )
    return NotificationMessage(title, body, deep_link)


def build_test_message(dashboard_url: str | None = None) -> NotificationMessage:
    return NotificationMessage(
        "SmartOps Test Notification",
        "Windows notifications are working correctly.",
        build_test_deep_link(dashboard_url),
    )


def build_enabled_confirmation_message(
    dashboard_url: str | None = None,
) -> NotificationMessage:
    """Return user-initiated feedback for one successful OFF-to-ON change."""
    return NotificationMessage(
        "SmartOps",
        "Notifications have been enabled.",
        build_settings_deep_link(dashboard_url),
    )


@dataclass
class DispatchSummary:
    considered: int = 0
    delivered: int = 0
    failed: int = 0
    deduplicated: int = 0
    suppressed: int = 0
    disabled: bool = False


class NotificationDispatcher:
    """Select, reserve, and submit eligible alert activations exactly once."""

    def __init__(
        self,
        database_path: Path | None = None,
        *,
        provider: NotificationProvider | None = None,
        dashboard_url: str | None = None,
    ) -> None:
        self.database_path = database_path or get_database_path()
        self.provider = provider or create_notification_provider()
        self.dashboard_url = dashboard_url or get_dashboard_url()

    def dispatch_pending(self) -> DispatchSummary:
        initialize_database(self.database_path)
        summary = DispatchSummary()
        preferences = run_write_transaction(
            self.database_path,
            ensure_notification_preferences,
            priority="maintenance",
        )
        with database_connection(self.database_path) as connection:
            if not preferences["enabled"]:
                summary.disabled = True
                return summary
            eligible = set(preferences["eligible_categories"])
            alerts = eligible_alert_rows(
                connection,
                preferences["feature_started_at_utc"],
            )

        for alert_row in alerts:
            alert = dict(alert_row)
            severity = str(alert["current_severity"])
            alert_id = int(alert["id"])
            lifecycle_number = int(alert["lifecycle_number"])
            category = notification_category_for_alert_severity(severity)

            def reserve_or_suppress(
                connection: sqlite3.Connection,
            ) -> tuple[str, int | None, str | None]:
                previous = delivery_categories_for_alert(
                    connection,
                    alert_id,
                    lifecycle_number,
                )
                if not previous:
                    if bool(alert["has_new_escalation"]):
                        notification_type = "escalation"
                    elif (
                        str(alert["created_at_utc"])
                        >= str(preferences["feature_started_at_utc"])
                    ):
                        notification_type = "activation"
                    else:
                        return "skip", None, None
                elif category is not None and max(
                    NOTIFICATION_CATEGORY_ORDER[value] for value in previous
                ) < NOTIFICATION_CATEGORY_ORDER[category]:
                    notification_type = "escalation"
                else:
                    return "deduplicated", None, None

                if category is None:
                    record_notification_decision(
                        connection,
                        alert_id=alert_id,
                        lifecycle_number=lifecycle_number,
                        alert_severity=severity,
                        notification_category=None,
                        decision_type="suppressed",
                        decision_reason="unknown_alert_severity",
                        preference_revision=int(preferences["preference_revision"]),
                        transition_type=notification_type,
                    )
                    return "suppressed", None, None
                if category not in eligible:
                    record_notification_decision(
                        connection,
                        alert_id=alert_id,
                        lifecycle_number=lifecycle_number,
                        alert_severity=severity,
                        notification_category=category,
                        decision_type="suppressed",
                        decision_reason="suppressed_by_preference",
                        preference_revision=int(preferences["preference_revision"]),
                        transition_type=notification_type,
                    )
                    return "suppressed", None, None

                deep_link = build_alert_deep_link(alert_id, self.dashboard_url)
                delivery_id = reserve_notification_delivery(
                    connection,
                    alert_id=alert_id,
                    lifecycle_number=lifecycle_number,
                    severity=severity,
                    notification_category=category,
                    notification_type=notification_type,
                    deep_link_url=deep_link,
                    provider_name=self.provider.provider_name,
                )
                return "reserved", delivery_id, deep_link

            action, delivery_id, deep_link = run_write_transaction(
                self.database_path,
                reserve_or_suppress,
                priority="maintenance",
            )
            if action == "skip":
                continue
            if action == "suppressed":
                summary.suppressed += 1
                continue
            if action == "deduplicated":
                summary.deduplicated += 1
                continue
            if delivery_id is None:
                summary.deduplicated += 1
                continue

            summary.considered += 1
            assert deep_link is not None
            # The reservation is committed before Windows is invoked. If the
            # completion audit later encounters a lock, the durable
            # `attempting` row prevents a retry from sending a second toast.
            result = self.provider.send(_message_for_alert(alert, deep_link))
            run_write_transaction(
                self.database_path,
                lambda connection: complete_notification_delivery(
                    connection,
                    delivery_id,
                    delivered=result.delivered,
                    failure_reason=result.failure_reason,
                ),
                priority="maintenance",
            )
            if result.delivered:
                summary.delivered += 1
                LOGGER.info(
                    "Submitted native notification for alert %s (%s).",
                    alert_id,
                    SEMANTIC_SEVERITIES.get(severity, severity),
                )
            else:
                summary.failed += 1
                LOGGER.warning(
                    "Notification for alert %s was not delivered: %s",
                    alert_id,
                    result.failure_reason,
                )
        return summary


def dispatch_pending_notifications(
    database_path: Path | None = None,
    *,
    provider: NotificationProvider | None = None,
) -> DispatchSummary:
    """Convenience entry point used by the sole automatic dispatcher."""
    return NotificationDispatcher(database_path, provider=provider).dispatch_pending()
