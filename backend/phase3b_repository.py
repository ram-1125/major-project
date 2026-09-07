"""Read-only repository queries for Phase 3B risk evidence and candidates."""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def _decode_component(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["evidence"] = json.loads(item.pop("evidence_json"))
    return item


def _decode_candidate(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
) -> dict[str, Any]:
    item = dict(row)
    for stored, public in (
        ("reason_codes_json", "reason_codes"),
        ("recommended_verification_steps_json", "recommended_verification_steps"),
        ("limitations_json", "limitations"),
    ):
        item[public] = json.loads(item.pop(stored))
    evidence = []
    for evidence_row in connection.execute(
        """SELECT evidence_kind, evidence_key, observed_value_json,
        supports_candidate, reason_code
        FROM root_cause_candidate_evidence
        WHERE candidate_id = ? ORDER BY supports_candidate DESC, id""",
        (item["id"],),
    ):
        evidence_item = dict(evidence_row)
        evidence_item["observed_value"] = json.loads(
            evidence_item.pop("observed_value_json")
        )
        evidence_item["supports_candidate"] = bool(
            evidence_item["supports_candidate"]
        )
        evidence.append(evidence_item)
    item["evidence"] = evidence
    item["supporting_metrics"] = [
        row for row in evidence
        if row["supports_candidate"] and row["evidence_kind"] == "metric"
    ]
    item["supporting_events"] = [
        row for row in evidence
        if row["supports_candidate"] and row["evidence_kind"] == "event"
    ]
    item["contradictory_evidence"] = [
        row for row in evidence if not row["supports_candidate"]
    ]
    return item


def _decode_assessment(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    include_candidates: bool = True,
) -> dict[str, Any]:
    item = dict(row)
    item["reason_codes"] = json.loads(item.pop("reason_codes_json"))
    item["components"] = [
        _decode_component(component)
        for component in connection.execute(
            """SELECT id, component_name, correlation_group, raw_value,
            normalized_value, weight, contribution, evidence_json, reason_code
            FROM risk_evidence_components
            WHERE risk_assessment_id = ?
            ORDER BY component_name, correlation_group""",
            (item["id"],),
        )
    ]
    item["score_reconstruction"] = round(
        sum(float(component["contribution"]) for component in item["components"]),
        4,
    )
    item["candidates"] = (
        [
            _decode_candidate(connection, candidate)
            for candidate in connection.execute(
                """SELECT * FROM root_cause_candidates
                WHERE risk_assessment_id = ? ORDER BY rank""",
                (item["id"],),
            )
        ]
        if include_candidates
        else []
    )
    return item


def get_risk_assessments(
    connection: sqlite3.Connection,
    limit: int,
    offset: int,
    start: str | None = None,
    end: str | None = None,
    sort: str = "newest",
    device_id: str | None = None,
    workload: str | None = None,
    evidence_level: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    clauses: list[str] = []
    parameters: list[Any] = []
    for value, clause in (
        (start, "window_start_utc >= ?"),
        (end, "window_start_utc <= ?"),
        (device_id, "device_id = ?"),
        (workload, "workload_context = ?"),
        (evidence_level, "evidence_level = ?"),
    ):
        if value is not None:
            clauses.append(clause)
            parameters.append(value)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    total = int(connection.execute(
        f"SELECT COUNT(*) FROM risk_assessments {where}",
        parameters,
    ).fetchone()[0])
    direction = "ASC" if sort == "oldest" else "DESC"
    rows = connection.execute(
        f"""SELECT * FROM risk_assessments {where}
        ORDER BY window_start_utc {direction}, id {direction}
        LIMIT ? OFFSET ?""",
        [*parameters, limit, offset],
    ).fetchall()
    return [_decode_assessment(connection, row) for row in rows], total


def get_risk_for_window(
    connection: sqlite3.Connection,
    window_id: int,
) -> dict[str, Any] | None:
    row = connection.execute(
        """SELECT * FROM risk_assessments
        WHERE feature_window_id = ?
        ORDER BY evaluated_at_utc DESC, id DESC LIMIT 1""",
        (window_id,),
    ).fetchone()
    return _decode_assessment(connection, row) if row else None


def get_root_causes(
    connection: sqlite3.Connection,
    limit: int,
    offset: int,
    start: str | None = None,
    end: str | None = None,
    sort: str = "newest",
    device_id: str | None = None,
    workload: str | None = None,
    evidence_level: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    clauses: list[str] = []
    parameters: list[Any] = []
    for value, clause in (
        (start, "r.window_start_utc >= ?"),
        (end, "r.window_start_utc <= ?"),
        (device_id, "r.device_id = ?"),
        (workload, "r.workload_context = ?"),
        (evidence_level, "r.evidence_level = ?"),
    ):
        if value is not None:
            clauses.append(clause)
            parameters.append(value)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    join = """FROM root_cause_candidates c
        JOIN risk_assessments r ON r.id = c.risk_assessment_id"""
    total = int(connection.execute(
        f"SELECT COUNT(*) {join} {where}",
        parameters,
    ).fetchone()[0])
    direction = "ASC" if sort == "oldest" else "DESC"
    rows = connection.execute(
        f"""SELECT c.*, r.feature_window_id, r.window_start_utc,
        r.window_end_utc, r.risk_evidence_index, r.evidence_level
        {join} {where}
        ORDER BY r.window_start_utc {direction}, c.rank ASC
        LIMIT ? OFFSET ?""",
        [*parameters, limit, offset],
    ).fetchall()
    return [_decode_candidate(connection, row) for row in rows], total


def get_root_causes_for_window(
    connection: sqlite3.Connection,
    window_id: int,
) -> list[dict[str, Any]]:
    return [
        _decode_candidate(connection, row)
        for row in connection.execute(
            """SELECT c.*, r.feature_window_id, r.window_start_utc,
            r.window_end_utc, r.risk_evidence_index, r.evidence_level
            FROM root_cause_candidates c
            JOIN risk_assessments r ON r.id = c.risk_assessment_id
            WHERE r.feature_window_id = ? ORDER BY c.rank""",
            (window_id,),
        )
    ]

