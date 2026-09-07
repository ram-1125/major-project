"""Read-only alert queries for the Phase 5A local API."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from backend.postcalibration_repository import get_alert_snapshot, latest_outcome


JSON_FIELDS = (
    "probable_factors_json",
    "contradictory_evidence_json",
    "excluded_inputs_json",
    "reason_codes_json",
)


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _expand_alert(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    full: bool = True,
) -> dict[str, Any]:
    item = dict(row)
    for field in JSON_FIELDS:
        item[field.removesuffix("_json")] = _loads(item.pop(field), [])
    snapshot = get_alert_snapshot(connection, int(item["id"]))
    item["explanation_snapshot"] = snapshot
    item["short_alert_basis"] = (
        snapshot["plain_language_explanation"] if snapshot else
        "Legacy alert — detailed explanation was not recorded when this alert was generated."
    )
    item["alert_confidence"] = snapshot["alert_confidence"] if snapshot else None
    item["confidence_label"] = snapshot["confidence_label"] if snapshot else None
    item["validation"] = snapshot["validation"] if snapshot else {
        "validation_type": "not_yet_validated",
        "display_label": "Not yet validated",
        "sample_size": 0,
        "accuracy": None,
        "precision": None,
        "recall": None,
        "specificity": None,
        "f1_score": None,
        "false_positive_rate": None,
        "limitations": ["No immutable method-level validation was recorded for this legacy alert."],
    }
    latest_occurrence_row = connection.execute(
        """SELECT * FROM alert_occurrences WHERE alert_id = ?
        ORDER BY observed_at_utc DESC, id DESC LIMIT 1""",
        (item["id"],),
    ).fetchone()
    item["outcome"] = (
        latest_outcome(connection, int(latest_occurrence_row["id"]))
        if latest_occurrence_row else None
    )
    if not full:
        item["summary_record"] = True
        item["evidence"] = []
        item["explanations"] = []
        item["diagnostic_recommendations"] = []
        item["preventive_guidance"] = []
        item["transitions"] = []
        item["occurrences"] = []
        item["notification_deliveries"] = []
        return item
    item["summary_record"] = False
    occurrence = latest_occurrence_row
    item["latest_occurrence"] = dict(occurrence) if occurrence else None
    item["evidence"] = []
    item["explanations"] = []
    item["diagnostic_recommendations"] = []
    item["preventive_guidance"] = []
    if occurrence:
        occurrence_id = occurrence["id"]
        for evidence in connection.execute(
            """SELECT * FROM alert_evidence WHERE occurrence_id = ?
            ORDER BY suppressed, id""",
            (occurrence_id,),
        ):
            evidence_item = dict(evidence)
            evidence_item["raw_evidence"] = _loads(
                evidence_item.pop("raw_evidence_json"), {}
            )
            evidence_item["effective_evidence"] = _loads(
                evidence_item.pop("effective_evidence_json"), {}
            )
            evidence_item["suppressed"] = bool(evidence_item["suppressed"])
            item["evidence"].append(evidence_item)
        item["explanations"] = [
            dict(value)
            for value in connection.execute(
                """SELECT explanation_type, sequence, explanation_text
                FROM alert_explanations WHERE occurrence_id = ?
                ORDER BY sequence, id""",
                (occurrence_id,),
            )
        ]
        for recommendation in connection.execute(
            """SELECT recommendation_type, sequence, recommendation_text
            FROM alert_recommendations WHERE occurrence_id = ?
            ORDER BY recommendation_type, sequence""",
            (occurrence_id,),
        ):
            target = (
                "diagnostic_recommendations"
                if recommendation["recommendation_type"]
                == "diagnostic_verification"
                else "preventive_guidance"
            )
            item[target].append(recommendation["recommendation_text"])
    item["transitions"] = [
        {
            **dict(transition),
            "metadata": _loads(transition["metadata_json"], {}),
        }
        for transition in connection.execute(
            """SELECT * FROM alert_state_transitions WHERE alert_id = ?
            ORDER BY transition_timestamp_utc, id""",
            (item["id"],),
        )
    ]
    for transition in item["transitions"]:
        transition.pop("metadata_json", None)
    item["occurrences"] = [
        dict(value)
        for value in connection.execute(
            """SELECT * FROM alert_occurrences WHERE alert_id = ?
            ORDER BY observed_at_utc, id""",
            (item["id"],),
        )
    ]
    # Notification attempts are existing lifecycle evidence. Returning the
    # bounded rows with a single alert detail is read-only and lets the UI
    # distinguish attempted, delivered, and failed states without inference.
    item["notification_deliveries"] = [
        dict(value)
        for value in connection.execute(
            """SELECT * FROM notification_deliveries WHERE alert_id = ?
            ORDER BY attempted_at_utc, id""",
            (item["id"],),
        )
    ]
    return item


def get_alert(
    connection: sqlite3.Connection,
    alert_id: int,
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM alerts WHERE id = ?", (alert_id,)
    ).fetchone()
    return _expand_alert(connection, row) if row else None


def get_alerts(
    connection: sqlite3.Connection,
    limit: int = 50,
    offset: int = 0,
    start: str | None = None,
    end: str | None = None,
    sort: str = "newest",
    device_id: str | None = None,
    category: str | None = None,
    severity: str | None = None,
    state: str | None = None,
    workload: str | None = None,
    *,
    full: bool = True,
) -> tuple[list[dict[str, Any]], int]:
    clauses: list[str] = []
    values: list[Any] = []
    for sql, value in (
        ("latest_observed_utc >= ?", start),
        ("latest_observed_utc <= ?", end),
        ("device_id = ?", device_id),
        ("category = ?", category),
        ("current_severity = ?", severity),
        ("state = ?", state),
        ("workload_context = ?", workload),
    ):
        if value is not None:
            clauses.append(sql)
            values.append(value)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    total = int(
        connection.execute(
            f"SELECT COUNT(*) FROM alerts {where}", values
        ).fetchone()[0]
    )
    direction = "ASC" if sort == "oldest" else "DESC"
    rows = connection.execute(
        f"""SELECT * FROM alerts {where}
        ORDER BY latest_observed_utc {direction}, id {direction}
        LIMIT ? OFFSET ?""",
        (*values, limit, offset),
    ).fetchall()
    return [
        _expand_alert(connection, row, full=full) for row in rows
    ], total


def get_latest_alerts(
    connection: sqlite3.Connection,
    device_id: str | None = None,
) -> list[dict[str, Any]]:
    clauses = ["state != 'resolved'"]
    values: list[Any] = []
    if device_id:
        clauses.append("device_id = ?")
        values.append(device_id)
    rows = connection.execute(
        f"""SELECT * FROM alerts WHERE {' AND '.join(clauses)}
        ORDER BY CASE current_severity
            WHEN 'urgent' THEN 3 WHEN 'warning' THEN 2
            WHEN 'advisory' THEN 1 ELSE 0 END DESC,
        latest_observed_utc DESC, id DESC""",
        values,
    ).fetchall()
    return [_expand_alert(connection, row) for row in rows]


def get_alert_status(
    connection: sqlite3.Connection,
    device_id: str | None = None,
) -> dict[str, Any]:
    resolved = device_id
    if resolved is None:
        row = connection.execute(
            "SELECT device_id FROM feature_windows ORDER BY window_end_utc DESC LIMIT 1"
        ).fetchone()
        resolved = row["device_id"] if row else None
    # Avoid the optional-parameter OR pattern once a concrete device is known.
    # On a mature database that expression prevents SQLite from using the
    # existing device/state index and turns this small status read into a scan
    # of the full alert payload table.
    device_where = "WHERE device_id = ?" if resolved is not None else ""
    device_values: tuple[Any, ...] = (resolved,) if resolved is not None else ()
    state_counts = {
        row["state"]: int(row["count"])
        for row in connection.execute(
            f"""SELECT state, COUNT(*) count FROM alerts
            {device_where} GROUP BY state""",
            device_values,
        )
    }
    active_where = (
        "WHERE state != 'resolved' AND device_id = ?"
        if resolved is not None else "WHERE state != 'resolved'"
    )
    severity_counts = {
        row["current_severity"]: int(row["count"])
        for row in connection.execute(
            f"""SELECT current_severity, COUNT(*) count FROM alerts
            {active_where} GROUP BY current_severity""",
            device_values,
        )
    }
    run = connection.execute(
        """SELECT * FROM alert_evaluation_runs
        WHERE (? IS NULL OR device_id = ?)
        ORDER BY finished_at_utc DESC, id DESC LIMIT 1""",
        (resolved, resolved),
    ).fetchone()
    return {
        "status": "evaluated" if run else "not_evaluated",
        "device_id": resolved,
        "open_alert_count": sum(
            state_counts.get(value, 0)
            for value in ("open", "acknowledged", "recovering")
        ),
        "state_counts": state_counts,
        "active_severity_counts": severity_counts,
        "latest_run": dict(run) if run else None,
    }
