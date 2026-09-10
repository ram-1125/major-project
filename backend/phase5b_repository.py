"""Phase 5B repositories and append-only user-evidence revisions."""

from __future__ import annotations

import json
import hashlib
import math
import sqlite3
from datetime import datetime, timezone
from typing import Any

from analytics.validation_config import (
    ALGORITHM_VERSION,
    CONFIGURATION_VERSION,
    MATCHING_VERSION,
)


JSON_FIELDS = (
    "windows_event_ids_json",
    "reason_codes_json",
    "limitations_json",
    "missing_intervals_json",
    "supporting_evidence_json",
    "contradictory_evidence_json",
    "reconstruction_json",
    "details_json",
    "user_reason_codes_json",
    "decision_basis_json",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def expand(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    for field in tuple(item):
        if field in JSON_FIELDS:
            item[field.removesuffix("_json")] = loads(item.pop(field), [])
    for field in (
        "incident_reporting_complete",
        "preventive_action_taken",
        "confirmed_by_user",
        "category_compatible",
        "included",
        "eligible",
    ):
        if field in item:
            item[field] = bool(item[field])
    return item


def create_observation_period(
    connection: sqlite3.Connection,
    *,
    device_id: str,
    start_utc: str,
    interruption_notes: str | None = None,
) -> dict[str, Any]:
    existing = connection.execute(
        """SELECT id FROM validation_observation_periods
        WHERE device_id = ? AND state = 'open' LIMIT 1""",
        (device_id,),
    ).fetchone()
    if existing is not None:
        raise ValueError("An observation period is already open for this device.")
    now = utc_now()
    cursor = connection.execute(
        """INSERT INTO validation_observation_periods (
        device_id, start_utc, state, incident_reporting_complete,
        missing_intervals_json, interruption_notes, reason_codes_json,
        limitations_json, algorithm_version, configuration_version,
        created_at_utc, updated_at_utc
        ) VALUES (?, ?, 'open', 0, '[]', ?, '[]', '[]', ?, ?, ?, ?)""",
        (
            device_id,
            start_utc,
            interruption_notes,
            ALGORITHM_VERSION,
            CONFIGURATION_VERSION,
            now,
            now,
        ),
    )
    return expand(
        connection.execute(
            "SELECT * FROM validation_observation_periods WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
    ) or {}


def close_observation_period(
    connection: sqlite3.Connection,
    period_id: int,
    *,
    end_utc: str,
    incident_reporting_complete: bool,
    state: str,
    missing_intervals: list[dict[str, Any]],
    interruption_notes: str | None,
    declared_outcome: str = "no_meaningful_issue",
    related_incident_id: int | None = None,
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM validation_observation_periods WHERE id = ?", (period_id,)
    ).fetchone()
    if row is None or row["state"] != "open":
        return None
    windows = connection.execute(
        """SELECT COUNT(*) total,
        SUM(CASE WHEN is_complete = 1 AND coverage_ratio >= 0.8
          AND (finalization_state IS NULL OR finalization_state IN ('finalized','audited_correction'))
          THEN 1 ELSE 0 END) eligible
        FROM feature_windows WHERE device_id = ?
        AND window_start_utc >= ? AND window_end_utc <= ?""",
        (row["device_id"], row["start_utc"], end_utc),
    ).fetchone()
    total = int(windows["total"] or 0)
    eligible = int(windows["eligible"] or 0)
    start_time = datetime.fromisoformat(row["start_utc"].replace("Z", "+00:00"))
    end_time = datetime.fromisoformat(end_utc.replace("Z", "+00:00"))
    expected = max(
        1,
        math.ceil(max(0.0, (end_time - start_time).total_seconds()) / 300),
    )
    coverage = min(1.0, eligible / expected)
    reason_codes: list[str] = []
    if declared_outcome not in {"no_meaningful_issue", "issue_occurred"}:
        reason_codes.append("validation_outcome_not_declared")
    if declared_outcome == "issue_occurred" and related_incident_id is None:
        reason_codes.append("issue_outcome_requires_incident_report")
    if declared_outcome == "issue_occurred" and related_incident_id is not None:
        related = connection.execute(
            """SELECT device_id, start_utc, status FROM incident_reports
            WHERE id = ?""",
            (related_incident_id,),
        ).fetchone()
        if related is None:
            reason_codes.append("related_incident_not_found")
        elif related["status"] != "active":
            reason_codes.append("related_incident_is_withdrawn")
        elif related["device_id"] != row["device_id"]:
            reason_codes.append("related_incident_device_mismatch")
        elif not (row["start_utc"] <= related["start_utc"] <= end_utc):
            reason_codes.append("related_incident_outside_observation_period")
    if not incident_reporting_complete:
        reason_codes.append("incident_reporting_not_declared_complete")
    if coverage < 0.8:
        reason_codes.append("eligible_window_coverage_below_0_8")
    overlap = connection.execute(
        """SELECT id FROM validation_observation_periods
        WHERE id != ? AND device_id = ? AND state = 'completed'
        AND start_utc < ? AND end_utc > ? LIMIT 1""",
        (period_id, row["device_id"], end_utc, row["start_utc"]),
    ).fetchone()
    if overlap is not None:
        reason_codes.append("overlaps_existing_completed_validation_period")
    server_missing = max(0, expected - eligible)
    computed_missing = list(missing_intervals)
    if server_missing and not computed_missing:
        computed_missing = [{
            "reason": "missing_or_ineligible_finalized_feature_windows",
            "count": server_missing,
        }]
    if computed_missing:
        reason_codes.append("unexplained_or_ineligible_analysis_gap")
    final_state = (
        "completed"
        if state == "completed"
        and incident_reporting_complete
        and coverage >= 0.8
        and not reason_codes
        else state if state in {"incomplete", "withdrawn"} else "incomplete"
    )
    eligibility_state = "eligible" if final_state == "completed" else "excluded"
    now = utc_now()
    connection.execute(
        """UPDATE validation_observation_periods SET end_utc = ?, state = ?,
        incident_reporting_complete = ?, expected_window_count = ?,
        eligible_window_count = ?, coverage_ratio = ?, missing_intervals_json = ?,
        interruption_notes = ?, reason_codes_json = ?, closed_at_utc = ?,
        updated_at_utc = ?, declared_outcome = ?, related_incident_id = ?,
        eligibility_state = ?
        WHERE id = ?""",
        (
            end_utc,
            final_state,
            int(incident_reporting_complete),
            expected,
            eligible,
            coverage,
            dumps(computed_missing),
            interruption_notes,
            dumps(reason_codes),
            now,
            now,
            declared_outcome,
            related_incident_id,
            eligibility_state,
            period_id,
        ),
    )
    return get_observation_period(connection, period_id)


def get_observation_period(
    connection: sqlite3.Connection, period_id: int
) -> dict[str, Any] | None:
    return expand(
        connection.execute(
            "SELECT * FROM validation_observation_periods WHERE id = ?", (period_id,)
        ).fetchone()
    )


def list_observation_periods(
    connection: sqlite3.Connection,
    *,
    device_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    where = "WHERE device_id = ?" if device_id else ""
    values: list[Any] = [device_id] if device_id else []
    total = int(
        connection.execute(
            f"SELECT COUNT(*) FROM validation_observation_periods {where}", values
        ).fetchone()[0]
    )
    rows = connection.execute(
        f"""SELECT * FROM validation_observation_periods {where}
        ORDER BY start_utc DESC, id DESC LIMIT ? OFFSET ?""",
        (*values, limit, offset),
    )
    return [expand(row) or {} for row in rows], total


INCIDENT_MUTABLE_FIELDS = (
    "category",
    "severity",
    "start_utc",
    "end_utc",
    "timestamp_precision",
    "verification_status",
    "verification_source",
    "workload_context",
    "symptoms_text",
    "windows_event_ids_json",
    "action_taken",
    "observed_outcome",
    "status",
    "data_confidence",
    "reason_codes_json",
    "limitations_json",
)


def _insert_incident_revision(
    connection: sqlite3.Connection,
    incident_id: int,
    revision_number: int,
    values: dict[str, Any],
    revision_reason: str,
    supersedes_revision_id: int | None,
) -> None:
    columns = ", ".join(INCIDENT_MUTABLE_FIELDS)
    placeholders = ", ".join("?" for _ in INCIDENT_MUTABLE_FIELDS)
    connection.execute(
        f"""INSERT INTO incident_report_revisions (
        incident_id, revision_number, {columns}, revision_reason,
        supersedes_revision_id, created_at_utc
        ) VALUES (?, ?, {placeholders}, ?, ?, ?)""",
        (
            incident_id,
            revision_number,
            *(values[field] for field in INCIDENT_MUTABLE_FIELDS),
            revision_reason,
            supersedes_revision_id,
            utc_now(),
        ),
    )


def create_incident(
    connection: sqlite3.Connection,
    *,
    device_id: str,
    values: dict[str, Any],
) -> dict[str, Any]:
    now = utc_now()
    columns = ", ".join(INCIDENT_MUTABLE_FIELDS)
    placeholders = ", ".join("?" for _ in INCIDENT_MUTABLE_FIELDS)
    cursor = connection.execute(
        f"""INSERT INTO incident_reports (
        device_id, current_revision_number, {columns}, created_at_utc, updated_at_utc
        ) VALUES (?, 1, {placeholders}, ?, ?)""",
        (
            device_id,
            *(values[field] for field in INCIDENT_MUTABLE_FIELDS),
            now,
            now,
        ),
    )
    incident_id = int(cursor.lastrowid)
    _insert_incident_revision(
        connection, incident_id, 1, values, "initial_report", None
    )
    return get_incident(connection, incident_id) or {}


def revise_incident(
    connection: sqlite3.Connection,
    incident_id: int,
    *,
    updates: dict[str, Any],
    revision_reason: str,
) -> dict[str, Any] | None:
    current = connection.execute(
        "SELECT * FROM incident_reports WHERE id = ?", (incident_id,)
    ).fetchone()
    if current is None:
        return None
    values = {field: current[field] for field in INCIDENT_MUTABLE_FIELDS}
    values.update(updates)
    revision_number = int(current["current_revision_number"]) + 1
    previous_revision = connection.execute(
        """SELECT id FROM incident_report_revisions
        WHERE incident_id = ? ORDER BY revision_number DESC LIMIT 1""",
        (incident_id,),
    ).fetchone()
    _insert_incident_revision(
        connection,
        incident_id,
        revision_number,
        values,
        revision_reason,
        previous_revision["id"] if previous_revision else None,
    )
    assignments = ", ".join(f"{field} = ?" for field in INCIDENT_MUTABLE_FIELDS)
    connection.execute(
        f"""UPDATE incident_reports SET current_revision_number = ?,
        {assignments}, updated_at_utc = ? WHERE id = ?""",
        (
            revision_number,
            *(values[field] for field in INCIDENT_MUTABLE_FIELDS),
            utc_now(),
            incident_id,
        ),
    )
    return get_incident(connection, incident_id)


def get_incident(
    connection: sqlite3.Connection, incident_id: int
) -> dict[str, Any] | None:
    item = expand(
        connection.execute(
            "SELECT * FROM incident_reports WHERE id = ?", (incident_id,)
        ).fetchone()
    )
    if item is not None:
        item["revisions"] = [
            expand(row) or {}
            for row in connection.execute(
                """SELECT * FROM incident_report_revisions WHERE incident_id = ?
                ORDER BY revision_number""",
                (incident_id,),
            )
        ]
        item["alert_links"] = [
            expand(row) or {}
            for row in connection.execute(
                """SELECT * FROM alert_incident_links WHERE incident_id = ?
                ORDER BY updated_at_utc DESC""",
                (incident_id,),
            )
        ]
    return item


def list_incidents(
    connection: sqlite3.Connection,
    *,
    limit: int = 50,
    offset: int = 0,
    device_id: str | None = None,
    category: str | None = None,
    severity: str | None = None,
    verification_status: str | None = None,
    status: str | None = None,
    minimum_confidence: float | None = None,
    maximum_confidence: float | None = None,
    start: str | None = None,
    end: str | None = None,
    sort: str = "newest",
) -> tuple[list[dict[str, Any]], int]:
    clauses: list[str] = []
    values: list[Any] = []
    for clause, value in (
        ("device_id = ?", device_id),
        ("category = ?", category),
        ("severity = ?", severity),
        ("verification_status = ?", verification_status),
        ("status = ?", status),
        ("data_confidence >= ?", minimum_confidence),
        ("data_confidence <= ?", maximum_confidence),
        ("start_utc >= ?", start),
        ("start_utc <= ?", end),
    ):
        if value is not None:
            clauses.append(clause)
            values.append(value)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    total = int(
        connection.execute(
            f"SELECT COUNT(*) FROM incident_reports {where}", values
        ).fetchone()[0]
    )
    direction = "ASC" if sort == "oldest" else "DESC"
    rows = connection.execute(
        f"""SELECT * FROM incident_reports {where}
        ORDER BY start_utc {direction}, id {direction} LIMIT ? OFFSET ?""",
        (*values, limit, offset),
    )
    return [expand(row) or {} for row in rows], total


FEEDBACK_MUTABLE_FIELDS = (
    "outcome",
    "observation_horizon_seconds",
    "verification_status",
    "verification_timestamp_utc",
    "verification_source",
    "notes",
    "user_reason_codes_json",
    "structured_action_taken",
    "condition_state",
    "preventive_action_taken",
    "status",
    "data_confidence",
    "reason_codes_json",
    "limitations_json",
)


def _insert_feedback_revision(
    connection: sqlite3.Connection,
    feedback_id: int,
    revision_number: int,
    values: dict[str, Any],
    revision_reason: str,
    supersedes_revision_id: int | None,
) -> None:
    columns = ", ".join(FEEDBACK_MUTABLE_FIELDS)
    placeholders = ", ".join("?" for _ in FEEDBACK_MUTABLE_FIELDS)
    connection.execute(
        f"""INSERT INTO alert_feedback_revisions (
        feedback_id, revision_number, {columns}, revision_reason,
        supersedes_revision_id, created_at_utc
        ) VALUES (?, ?, {placeholders}, ?, ?, ?)""",
        (
            feedback_id,
            revision_number,
            *(values[field] for field in FEEDBACK_MUTABLE_FIELDS),
            revision_reason,
            supersedes_revision_id,
            utc_now(),
        ),
    )


def create_feedback(
    connection: sqlite3.Connection,
    *,
    alert_id: int,
    values: dict[str, Any],
) -> dict[str, Any]:
    now = utc_now()
    columns = ", ".join(FEEDBACK_MUTABLE_FIELDS)
    placeholders = ", ".join("?" for _ in FEEDBACK_MUTABLE_FIELDS)
    cursor = connection.execute(
        f"""INSERT INTO alert_feedback (
        alert_id, current_revision_number, {columns}, created_at_utc, updated_at_utc
        ) VALUES (?, 1, {placeholders}, ?, ?)""",
        (
            alert_id,
            *(values[field] for field in FEEDBACK_MUTABLE_FIELDS),
            now,
            now,
        ),
    )
    feedback_id = int(cursor.lastrowid)
    _insert_feedback_revision(
        connection, feedback_id, 1, values, "initial_feedback", None
    )
    return get_feedback(connection, alert_id) or {}


def revise_feedback(
    connection: sqlite3.Connection,
    alert_id: int,
    *,
    updates: dict[str, Any],
    revision_reason: str,
) -> dict[str, Any] | None:
    current = connection.execute(
        "SELECT * FROM alert_feedback WHERE alert_id = ?", (alert_id,)
    ).fetchone()
    if current is None:
        return None
    values = {field: current[field] for field in FEEDBACK_MUTABLE_FIELDS}
    values.update(updates)
    revision_number = int(current["current_revision_number"]) + 1
    previous = connection.execute(
        """SELECT id FROM alert_feedback_revisions WHERE feedback_id = ?
        ORDER BY revision_number DESC LIMIT 1""",
        (current["id"],),
    ).fetchone()
    _insert_feedback_revision(
        connection,
        current["id"],
        revision_number,
        values,
        revision_reason,
        previous["id"] if previous else None,
    )
    assignments = ", ".join(f"{field} = ?" for field in FEEDBACK_MUTABLE_FIELDS)
    connection.execute(
        f"""UPDATE alert_feedback SET current_revision_number = ?,
        {assignments}, updated_at_utc = ? WHERE id = ?""",
        (
            revision_number,
            *(values[field] for field in FEEDBACK_MUTABLE_FIELDS),
            utc_now(),
            current["id"],
        ),
    )
    return get_feedback(connection, alert_id)


def get_feedback(
    connection: sqlite3.Connection, alert_id: int
) -> dict[str, Any] | None:
    item = expand(
        connection.execute(
            "SELECT * FROM alert_feedback WHERE alert_id = ?", (alert_id,)
        ).fetchone()
    )
    if item is not None:
        item["revisions"] = [
            expand(row) or {}
            for row in connection.execute(
                """SELECT * FROM alert_feedback_revisions WHERE feedback_id = ?
                ORDER BY revision_number""",
                (item["id"],),
            )
        ]
    return item


def list_unverified_alerts(
    connection: sqlite3.Connection,
    *,
    device_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    clauses = [
        "f.id IS NULL",
        "NOT EXISTS (SELECT 1 FROM alert_occurrences occurrence "
        "JOIN alert_outcome_events outcome ON outcome.alert_occurrence_id = occurrence.id "
        "WHERE occurrence.alert_id = a.id)",
    ]
    values: list[Any] = []
    if device_id:
        clauses.append("a.device_id = ?")
        values.append(device_id)
    return [
        dict(row)
        for row in connection.execute(
            f"""SELECT a.id, a.device_id, a.alert_code, a.category, a.title,
            a.current_severity, a.state, a.first_observed_utc, a.latest_observed_utc
            FROM alerts a LEFT JOIN alert_feedback f ON f.alert_id = a.id
            WHERE {' AND '.join(clauses)}
            ORDER BY a.latest_observed_utc DESC, a.id DESC LIMIT ?""",
            (*values, limit),
        )
    ]


def list_feedback(
    connection: sqlite3.Connection,
    *,
    limit: int = 50,
    offset: int = 0,
    device_id: str | None = None,
    outcome: str | None = None,
    verification_status: str | None = None,
    minimum_confidence: float | None = None,
    start: str | None = None,
    end: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    clauses: list[str] = []
    values: list[Any] = []
    for clause, value in (
        ("a.device_id = ?", device_id),
        ("f.outcome = ?", outcome),
        ("f.verification_status = ?", verification_status),
        ("f.data_confidence >= ?", minimum_confidence),
        ("f.verification_timestamp_utc >= ?", start),
        ("f.verification_timestamp_utc <= ?", end),
    ):
        if value is not None:
            clauses.append(clause)
            values.append(value)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    total = int(
        connection.execute(
            f"""SELECT COUNT(*) FROM alert_feedback f
            JOIN alerts a ON a.id = f.alert_id {where}""",
            values,
        ).fetchone()[0]
    )
    rows = connection.execute(
        f"""SELECT f.*, a.device_id, a.title alert_title,
        a.category alert_category, a.current_severity alert_severity,
        a.state alert_lifecycle_state
        FROM alert_feedback f JOIN alerts a ON a.id = f.alert_id
        {where} ORDER BY f.verification_timestamp_utc DESC, f.id DESC
        LIMIT ? OFFSET ?""",
        (*values, limit, offset),
    )
    return [expand(row) or {} for row in rows], total


def upsert_alert_incident_link(
    connection: sqlite3.Connection,
    *,
    alert_id: int,
    incident_id: int,
    match_type: str,
    origin: str,
    confirmed_by_user: bool,
    matching_score: float,
    time_difference_seconds: float | None,
    category_compatible: bool,
    matching_rule: str,
    supporting_evidence: list[str],
    contradictory_evidence: list[str],
    reason_codes: list[str],
) -> dict[str, Any]:
    now = utc_now()
    connection.execute(
        """INSERT INTO alert_incident_links (
        alert_id, incident_id, match_type, origin, confirmed_by_user,
        matching_score, time_difference_seconds, category_compatible,
        matching_rule, supporting_evidence_json, contradictory_evidence_json,
        reason_codes_json, algorithm_version, configuration_version,
        matching_version, created_at_utc, updated_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(alert_id, incident_id, matching_version) DO UPDATE SET
        match_type = excluded.match_type,
        origin = excluded.origin,
        confirmed_by_user = excluded.confirmed_by_user,
        matching_score = excluded.matching_score,
        time_difference_seconds = excluded.time_difference_seconds,
        category_compatible = excluded.category_compatible,
        matching_rule = excluded.matching_rule,
        supporting_evidence_json = excluded.supporting_evidence_json,
        contradictory_evidence_json = excluded.contradictory_evidence_json,
        reason_codes_json = excluded.reason_codes_json,
        updated_at_utc = excluded.updated_at_utc
        WHERE alert_incident_links.match_type != excluded.match_type
        OR alert_incident_links.origin != excluded.origin
        OR alert_incident_links.confirmed_by_user != excluded.confirmed_by_user
        OR alert_incident_links.matching_score != excluded.matching_score
        OR alert_incident_links.time_difference_seconds
            IS NOT excluded.time_difference_seconds
        OR alert_incident_links.category_compatible
            != excluded.category_compatible
        OR alert_incident_links.matching_rule != excluded.matching_rule
        OR alert_incident_links.supporting_evidence_json
            != excluded.supporting_evidence_json
        OR alert_incident_links.contradictory_evidence_json
            != excluded.contradictory_evidence_json
        OR alert_incident_links.reason_codes_json != excluded.reason_codes_json""",
        (
            alert_id,
            incident_id,
            match_type,
            origin,
            int(confirmed_by_user),
            matching_score,
            time_difference_seconds,
            int(category_compatible),
            matching_rule,
            dumps(supporting_evidence),
            dumps(contradictory_evidence),
            dumps(reason_codes),
            ALGORITHM_VERSION,
            CONFIGURATION_VERSION,
            MATCHING_VERSION,
            now,
            now,
        ),
    )
    return expand(
        connection.execute(
            """SELECT * FROM alert_incident_links
            WHERE alert_id = ? AND incident_id = ? AND matching_version = ?""",
            (alert_id, incident_id, MATCHING_VERSION),
        ).fetchone()
    ) or {}


def append_match_event(
    connection: sqlite3.Connection,
    *,
    incident_id: int,
    alert_id: int | None,
    link_id: int | None,
    previous_decision: str | None,
    new_decision: str,
    decision_basis: dict[str, Any],
    warning_horizon_seconds: float | None,
    matching_score: float | None,
    method_version: str,
) -> dict[str, Any]:
    """Append one idempotent matching decision without rewriting its history."""
    material = {
        "incident_id": incident_id,
        "alert_id": alert_id,
        "previous_decision": previous_decision,
        "new_decision": new_decision,
        "decision_basis": decision_basis,
        "warning_horizon_seconds": warning_horizon_seconds,
        "matching_score": matching_score,
        "method_version": method_version,
    }
    signature = hashlib.sha256(dumps(material).encode("utf-8")).hexdigest()
    now = utc_now()
    connection.execute(
        """INSERT OR IGNORE INTO validation_match_events (
        link_id, alert_id, incident_id, previous_decision, new_decision,
        decision_basis_json, warning_horizon_seconds, matching_score,
        decision_signature, method_version, event_timestamp_utc, created_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            link_id, alert_id, incident_id, previous_decision, new_decision,
            dumps(decision_basis), warning_horizon_seconds, matching_score,
            signature, method_version, now, now,
        ),
    )
    row = connection.execute(
        """SELECT * FROM validation_match_events
        WHERE incident_id = ? AND alert_id IS ? AND method_version = ?
        AND decision_signature = ?""",
        (incident_id, alert_id, method_version, signature),
    ).fetchone()
    return expand(row) or {}


def incident_matching_result(
    connection: sqlite3.Connection, incident_id: int
) -> dict[str, Any]:
    links = [
        expand(row) or {}
        for row in connection.execute(
            """SELECT * FROM alert_incident_links WHERE incident_id = ?
            AND matching_version = ? ORDER BY matching_score DESC, id""",
            (incident_id, MATCHING_VERSION),
        )
    ]
    confirmed = [item for item in links if item["match_type"] == "confirmed_match"]
    proposed = [item for item in links if item["match_type"] in {"probable_match", "possible_match"}]
    latest_event = expand(connection.execute(
        """SELECT * FROM validation_match_events WHERE incident_id = ?
        ORDER BY event_timestamp_utc DESC, id DESC LIMIT 1""",
        (incident_id,),
    ).fetchone())
    state = (
        "matched_automatically" if confirmed
        else "confirmation_required" if proposed
        else "no_qualifying_preceding_alert"
    )
    return {
        "state": state,
        "confirmed_link": confirmed[0] if confirmed else None,
        "proposed_matches": proposed,
        "latest_decision": latest_event,
        "plain_language": (
            f"Alert #{confirmed[0]['alert_id']} was uniquely matched before this incident. "
            "Timing supports evaluation but does not prove causation."
            if confirmed else
            "More than one plausible preceding alert was found. Please confirm the best match."
            if proposed else
            "No qualifying preceding alert was found. If monitoring coverage is eligible, this incident is a potential false negative."
        ),
    }


def list_validation_runs(
    connection: sqlite3.Connection,
    *,
    limit: int = 50,
    offset: int = 0,
    device_id: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    where = "WHERE device_id = ?" if device_id else ""
    values: list[Any] = [device_id] if device_id else []
    total = int(
        connection.execute(
            f"SELECT COUNT(*) FROM validation_evaluation_runs {where}", values
        ).fetchone()[0]
    )
    rows = connection.execute(
        f"""SELECT * FROM validation_evaluation_runs {where}
        ORDER BY finished_at_utc DESC, id DESC LIMIT ? OFFSET ?""",
        (*values, limit, offset),
    )
    items = []
    for row in rows:
        item = expand(row) or {}
        item["metrics"] = [
            expand(metric) or {}
            for metric in connection.execute(
                """SELECT * FROM validation_metric_results
                WHERE evaluation_run_id = ? ORDER BY scope_type, scope_value,
                metric_name""",
                (item["id"],),
            )
        ]
        item["lead_times"] = [
            expand(value) or {}
            for value in connection.execute(
                """SELECT * FROM warning_lead_time_results
                WHERE evaluation_run_id = ? ORDER BY incident_start_utc DESC""",
                (item["id"],),
            )
        ]
        item["evidence_decisions"] = [
            expand(value) or {}
            for value in connection.execute(
                """SELECT * FROM validation_inclusion_exclusion
                WHERE evaluation_run_id = ?
                ORDER BY included, evidence_type, evidence_id""",
                (item["id"],),
            )
        ]
        items.append(item)
    return items, total
