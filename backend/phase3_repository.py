"""Read-only API queries for Phase 3A baselines and deviations."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from analytics.baseline import get_profile


def get_profiles(
    connection: sqlite3.Connection,
    device_id: str | None = None,
    workload: str | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    parameters: list[Any] = []
    if device_id:
        clauses.append("device_id = ?")
        parameters.append(device_id)
    if workload:
        clauses.append("workload_scope = ?")
        parameters.append(workload)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    ids = [
        row["id"]
        for row in connection.execute(
            f"""SELECT id FROM baseline_profiles {where}
            ORDER BY device_id, workload_scope""",
            parameters,
        )
    ]
    return [get_profile(connection, baseline_id) for baseline_id in ids]


def _decode_assessment(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
) -> dict[str, Any]:
    item = dict(row)
    for stored, public in (
        ("statistical_summary_json", "statistical_summary"),
        ("top_contributing_metrics_json", "top_contributing_metrics"),
        ("reason_codes_json", "reason_codes"),
        ("relevant_event_context_json", "relevant_event_context"),
    ):
        item[public] = json.loads(item.pop(stored))
    item["feature_results"] = [
        dict(result)
        for result in connection.execute(
            """SELECT feature_name, observed_value, baseline_centre,
            expected_low, expected_high, deviation_direction,
            deviation_magnitude, normalized_deviation_score, severity_band,
            reason_code, baseline_scope
            FROM deviation_feature_results
            WHERE assessment_id = ?
            ORDER BY normalized_deviation_score DESC, feature_name""",
            (item["id"],),
        )
    ]
    return item


def get_deviations(
    connection: sqlite3.Connection,
    limit: int,
    offset: int,
    start: str | None = None,
    end: str | None = None,
    sort: str = "newest",
    device_id: str | None = None,
    workload: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    clauses: list[str] = []
    parameters: list[Any] = []
    if start:
        clauses.append("evaluation_timestamp_utc >= ?")
        parameters.append(start)
    if end:
        clauses.append("evaluation_timestamp_utc <= ?")
        parameters.append(end)
    if device_id:
        clauses.append("device_id = ?")
        parameters.append(device_id)
    if workload:
        clauses.append("workload_context = ?")
        parameters.append(workload)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    total = connection.execute(
        f"SELECT COUNT(*) FROM deviation_assessments {where}",
        parameters,
    ).fetchone()[0]
    direction = "ASC" if sort == "oldest" else "DESC"
    rows = connection.execute(
        f"""SELECT * FROM deviation_assessments {where}
        ORDER BY evaluation_timestamp_utc {direction}, id {direction}
        LIMIT ? OFFSET ?""",
        [*parameters, limit, offset],
    ).fetchall()
    return [_decode_assessment(connection, row) for row in rows], int(total)


def get_deviation_for_window(
    connection: sqlite3.Connection,
    window_id: int,
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM deviation_assessments WHERE feature_window_id = ?",
        (window_id,),
    ).fetchone()
    return _decode_assessment(connection, row) if row else None

