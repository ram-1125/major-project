"""Read-only queries for Phase 4A System Health Score assessments."""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def _json(value: str) -> Any:
    return json.loads(value)


def _decode_assessment(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    full: bool = True,
) -> dict[str, Any]:
    item = dict(row)
    item["reason_codes"] = _json(item.pop("reason_codes_json"))
    item["score_reconstruction"] = None
    if not full:
        return item

    components: list[dict[str, Any]] = []
    for component_row in connection.execute(
        """SELECT * FROM health_component_scores
        WHERE health_assessment_id = ? ORDER BY id""",
        (item["id"],),
    ):
        component = dict(component_row)
        component["reason_codes"] = _json(component.pop("reason_codes_json"))
        component["details"] = _json(component.pop("details_json"))
        components.append(component)
    item["components"] = components
    if item["system_health_score"] is not None:
        item["score_reconstruction"] = round(
            sum(
                float(component["component_score"])
                * float(component["effective_weight"])
                for component in components
            ),
            4,
        )

    deductions: list[dict[str, Any]] = []
    for deduction_row in connection.execute(
        """SELECT * FROM health_deductions
        WHERE health_assessment_id = ?
        ORDER BY component_name, contribution_group, id""",
        (item["id"],),
    ):
        deduction = dict(deduction_row)
        deduction["supporting_value"] = _json(
            deduction.pop("supporting_value_json")
        )
        deduction["supporting_event_ids"] = _json(
            deduction.pop("supporting_event_ids_json")
        )
        deductions.append(deduction)
    item["deductions"] = deductions

    inputs: list[dict[str, Any]] = []
    for input_row in connection.execute(
        """SELECT * FROM health_input_status
        WHERE health_assessment_id = ? ORDER BY input_category, input_name""",
        (item["id"],),
    ):
        input_item = dict(input_row)
        input_item["observed_value"] = _json(
            input_item.pop("observed_value_json")
        )
        inputs.append(input_item)
    item["inputs"] = inputs
    item["excluded_inputs"] = [
        input_item
        for input_item in inputs
        if input_item["availability_status"]
        in {
            "unavailable_optional",
            "unavailable_required",
            "excluded_by_context",
            "not_applicable",
        }
    ]

    guidance = {
        "explanations": [],
        "recommendations": [],
        "improvements": [],
        "limitations": [],
    }
    key_by_type = {
        "explanation": "explanations",
        "recommendation": "recommendations",
        "improvement": "improvements",
        "limitation": "limitations",
    }
    for guidance_row in connection.execute(
        """SELECT guidance_type, related_component, sequence, guidance_text
        FROM health_guidance
        WHERE health_assessment_id = ?
        ORDER BY guidance_type, sequence""",
        (item["id"],),
    ):
        guidance_item = dict(guidance_row)
        key = key_by_type.get(guidance_item["guidance_type"])
        if key is not None:
            guidance[key].append(guidance_item)
    item["guidance"] = guidance
    item["baseline_reference"] = {
        "id": item["baseline_id"],
        "algorithm_version": item["baseline_algorithm_version"],
        "configuration_version": item["baseline_configuration_version"],
        "updated_at_utc": item["baseline_updated_at_utc"],
    } if item["baseline_id"] is not None else None
    item["deviation_reference"] = {
        "id": item["deviation_assessment_id"],
        "evaluated_at_utc": item["deviation_evaluated_at_utc"],
    } if item["deviation_assessment_id"] is not None else None
    item["risk_reference"] = {
        "id": item["risk_assessment_id"],
        "evaluated_at_utc": item["risk_evaluated_at_utc"],
    } if item["risk_assessment_id"] is not None else None
    return item


def get_health_assessments(
    connection: sqlite3.Connection,
    limit: int,
    offset: int,
    start: str | None = None,
    end: str | None = None,
    sort: str = "newest",
    device_id: str | None = None,
    workload: str | None = None,
    health_band: str | None = None,
    evaluation_state: str | None = None,
    *,
    full: bool = True,
) -> tuple[list[dict[str, Any]], int]:
    clauses: list[str] = []
    parameters: list[Any] = []
    for value, clause in (
        (start, "window_start_utc >= ?"),
        (end, "window_start_utc <= ?"),
        (device_id, "device_id = ?"),
        (workload, "workload_context = ?"),
        (health_band, "health_band = ?"),
        (evaluation_state, "evaluation_state = ?"),
    ):
        if value is not None:
            clauses.append(clause)
            parameters.append(value)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    total = int(
        connection.execute(
            f"SELECT COUNT(*) FROM health_assessments {where}",
            parameters,
        ).fetchone()[0]
    )
    direction = "ASC" if sort == "oldest" else "DESC"
    rows = connection.execute(
        f"""SELECT * FROM health_assessments {where}
        ORDER BY window_start_utc {direction}, id {direction}
        LIMIT ? OFFSET ?""",
        [*parameters, limit, offset],
    ).fetchall()
    return [
        _decode_assessment(connection, row, full=full)
        for row in rows
    ], total


def get_health_for_window(
    connection: sqlite3.Connection,
    window_id: int,
) -> dict[str, Any] | None:
    row = connection.execute(
        """SELECT * FROM health_assessments
        WHERE feature_window_id = ?
        ORDER BY assessed_at_utc DESC, id DESC LIMIT 1""",
        (window_id,),
    ).fetchone()
    return _decode_assessment(connection, row) if row else None
