"""SQLite persistence helpers for local Windows alert notifications."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

from agent.notification_policy import (
    NOTIFICATION_CATEGORIES,
    normalize_notification_categories,
    notification_category_for_alert_severity,
)
from agent.config import (
    get_default_notification_severities,
)


def utc_now() -> str:
    """Return a sortable ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def ensure_notification_preferences(
    connection: sqlite3.Connection,
) -> dict[str, Any]:
    """Create the one local preference row without changing an existing choice."""
    timestamp = utc_now()
    severities = list(get_default_notification_severities())
    connection.execute(
        """INSERT OR IGNORE INTO notification_preferences (
            id, enabled, eligible_severities_json, eligible_categories_json,
            preference_revision, feature_started_at_utc,
            created_at_utc, updated_at_utc
        ) VALUES (1, 1, ?, ?, 1, ?, ?, ?)""",
        (
            json.dumps(severities), json.dumps(severities), timestamp,
            timestamp, timestamp,
        ),
    )
    row = connection.execute(
        "SELECT * FROM notification_preferences WHERE id = 1"
    ).fetchone()
    if row is None:
        raise RuntimeError("The notification preference row could not be created.")
    return expand_notification_preferences(row)


def expand_notification_preferences(row: sqlite3.Row) -> dict[str, Any]:
    """Convert the durable preference row to its API/domain representation."""
    item = dict(row)
    legacy_json = item.pop("eligible_severities_json")
    categories_json = item.pop("eligible_categories_json", None)
    try:
        stored = json.loads(categories_json if categories_json is not None else legacy_json)
    except (TypeError, json.JSONDecodeError):
        stored = []
    item["enabled"] = bool(item["enabled"])
    categories = normalize_notification_categories(stored)
    item["eligible_categories"] = categories
    # Kept for Phase 7A API compatibility. New clients use eligible_categories.
    item["eligible_severities"] = categories
    return item


def get_notification_preferences(
    connection: sqlite3.Connection,
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM notification_preferences WHERE id = 1"
    ).fetchone()
    return expand_notification_preferences(row) if row else None


def update_notification_preferences(
    connection: sqlite3.Connection,
    *,
    enabled: bool,
    eligible_severities: Iterable[str] | None = None,
    eligible_categories: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Persist canonical categories and move the no-replay watermark."""
    current = ensure_notification_preferences(connection)
    if eligible_categories is not None:
        requested = eligible_categories
    elif eligible_severities is not None:
        requested = (
            notification_category_for_alert_severity(value)
            for value in eligible_severities
        )
        requested = [value for value in requested if value is not None]
    else:
        requested = None
    categories = (
        list(dict.fromkeys(requested))
        if requested is not None
        else list(current["eligible_categories"])
    )
    invalid = set(categories) - set(NOTIFICATION_CATEGORIES)
    if invalid:
        raise ValueError("Notification categories must be advisory, warning, or urgent.")
    categories = normalize_notification_categories(categories)
    timestamp = utc_now()
    policy_changed = (
        enabled != current["enabled"]
        or categories != current["eligible_categories"]
    )
    feature_started = (
        timestamp if policy_changed else current["feature_started_at_utc"]
    )
    connection.execute(
        """UPDATE notification_preferences
        SET enabled = ?, eligible_severities_json = ?,
            eligible_categories_json = ?,
            preference_revision = preference_revision + ?,
            feature_started_at_utc = ?, updated_at_utc = ?
        WHERE id = 1""",
        (
            int(enabled),
            json.dumps(categories),
            json.dumps(categories),
            int(policy_changed),
            feature_started,
            timestamp,
        ),
    )
    result = get_notification_preferences(connection)
    if result is None:
        raise RuntimeError("The notification preference row disappeared.")
    return result


def get_notification_delivery_status(
    connection: sqlite3.Connection,
) -> dict[str, Any]:
    """Return small operational counts without exposing internal polling."""
    counts = {
        row["delivery_status"]: int(row["count"])
        for row in connection.execute(
            """SELECT delivery_status, COUNT(*) count
            FROM notification_deliveries GROUP BY delivery_status"""
        )
    }
    last = connection.execute(
        """SELECT * FROM notification_deliveries
        ORDER BY attempted_at_utc DESC, id DESC LIMIT 1"""
    ).fetchone()
    return {
        "delivery_counts": counts,
        "last_delivery": dict(last) if last else None,
    }


def eligible_alert_rows(
    connection: sqlite3.Connection,
    feature_started_at_utc: str,
) -> list[sqlite3.Row]:
    """Find only new active alerts or later severity escalations."""
    return connection.execute(
        """SELECT a.*,
            EXISTS(
                SELECT 1 FROM alert_state_transitions transition
                WHERE transition.alert_id = a.id
                  AND transition.transition_type = 'severity_escalated'
                  AND transition.transition_timestamp_utc >= ?
            ) AS has_new_escalation
        FROM alerts a
        WHERE a.state != 'resolved'
          AND (
            a.created_at_utc >= ?
            OR EXISTS(
                SELECT 1 FROM alert_state_transitions transition
                WHERE transition.alert_id = a.id
                  AND transition.transition_type = 'severity_escalated'
                  AND transition.transition_timestamp_utc >= ?
            )
          )
        ORDER BY a.created_at_utc, a.id""",
        (
            feature_started_at_utc,
            feature_started_at_utc,
            feature_started_at_utc,
        ),
    ).fetchall()


def delivery_categories_for_alert(
    connection: sqlite3.Connection,
    alert_id: int,
    lifecycle_number: int,
) -> list[str]:
    return [
        row["notification_category"]
        or notification_category_for_alert_severity(row["severity"])
        for row in connection.execute(
            """SELECT severity, notification_category FROM notification_deliveries
            WHERE alert_id = ? AND lifecycle_number = ?
              AND delivery_status IN ('attempting', 'delivered', 'failed')
            ORDER BY id""",
            (alert_id, lifecycle_number),
        )
        if (
            row["notification_category"]
            or notification_category_for_alert_severity(row["severity"])
        ) is not None
    ]


def delivery_severities_for_alert(
    connection: sqlite3.Connection,
    alert_id: int,
    lifecycle_number: int,
) -> list[str]:
    """Legacy helper retained for existing callers/tests."""
    return [
        row["severity"]
        for row in connection.execute(
            """SELECT severity FROM notification_deliveries
            WHERE alert_id = ? AND lifecycle_number = ? ORDER BY id""",
            (alert_id, lifecycle_number),
        )
    ]


def reserve_notification_delivery(
    connection: sqlite3.Connection,
    *,
    alert_id: int,
    lifecycle_number: int,
    severity: str,
    notification_category: str,
    notification_type: str,
    deep_link_url: str,
    provider_name: str,
) -> int | None:
    """Reserve one attempt before invoking Windows to prevent restart duplicates."""
    timestamp = utc_now()
    cursor = connection.execute(
        """INSERT OR IGNORE INTO notification_deliveries (
            alert_id, lifecycle_number, severity, notification_category,
            notification_type,
            attempted_at_utc, delivery_status, failure_reason, deep_link_url,
            provider_name, created_at_utc, updated_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, 'attempting', NULL, ?, ?, ?, ?)""",
        (
            alert_id,
            lifecycle_number,
            severity,
            notification_category,
            notification_type,
            timestamp,
            deep_link_url,
            provider_name,
            timestamp,
            timestamp,
        ),
    )
    return int(cursor.lastrowid) if cursor.rowcount else None


def complete_notification_delivery(
    connection: sqlite3.Connection,
    delivery_id: int,
    *,
    delivered: bool,
    failure_reason: str | None,
) -> None:
    connection.execute(
        """UPDATE notification_deliveries
        SET delivery_status = ?, failure_reason = ?, updated_at_utc = ?
        WHERE id = ?""",
        (
            "delivered" if delivered else "failed",
            None if delivered else failure_reason,
            utc_now(),
            delivery_id,
        ),
    )
