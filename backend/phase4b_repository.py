"""Read-only repository queries for Phase 4B PC Quality Check."""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def decode_inventory(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
) -> dict[str, Any]:
    item = dict(row)
    item["reason_codes"] = json.loads(item.pop("reason_codes_json"))
    values = []
    for value_row in connection.execute(
        """SELECT * FROM inventory_component_values
        WHERE inventory_snapshot_id = ?
        ORDER BY component_group, field_name""",
        (item["id"],),
    ):
        value = dict(value_row)
        value["value"] = json.loads(value.pop("value_json"))
        values.append(value)
    item["values"] = values
    item["by_field"] = {value["field_name"]: value for value in values}
    item["unavailable_or_unreliable"] = [
        value for value in values
        if value["availability_status"] != "available"
    ]
    return item


def get_latest_inventory(
    connection: sqlite3.Connection,
    device_id: str | None = None,
) -> dict[str, Any] | None:
    row = connection.execute(
        """SELECT * FROM device_inventory_snapshots
        WHERE (? IS NULL OR device_id = ?)
        ORDER BY captured_at_utc DESC, id DESC LIMIT 1""",
        (device_id, device_id),
    ).fetchone()
    return decode_inventory(connection, row) if row else None


def _decode_assessment(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    full: bool = True,
) -> dict[str, Any]:
    item = dict(row)
    item["reason_codes"] = json.loads(item.pop("reason_codes_json"))
    item["limitations"] = json.loads(item.pop("limitations_json"))
    item["current_operating_readiness"] = json.loads(
        item.pop("current_readiness_json")
    )
    if not full:
        return item
    components = []
    for component_row in connection.execute(
        """SELECT * FROM suitability_component_results
        WHERE assessment_id = ? ORDER BY id""",
        (item["id"],),
    ):
        component = dict(component_row)
        for stored, public in (
            ("detected_value_json", "detected_value"),
            ("minimum_threshold_json", "minimum_threshold"),
            ("recommended_threshold_json", "recommended_threshold"),
            ("reason_codes_json", "reason_codes"),
            ("limitations_json", "limitations"),
        ):
            component[public] = json.loads(component.pop(stored))
        for boolean_name in ("minimum_passed", "recommended_passed"):
            if component[boolean_name] is not None:
                component[boolean_name] = bool(component[boolean_name])
        components.append(component)
    item["components"] = components
    item["gates_and_caps"] = [
        {**dict(gate), "applied": bool(gate["applied"])}
        for gate in connection.execute(
            """SELECT * FROM suitability_gates_caps
            WHERE assessment_id = ? ORDER BY id""",
            (item["id"],),
        )
    ]
    item["limiting_components"] = [
        dict(value) for value in connection.execute(
            """SELECT * FROM suitability_limiting_components
            WHERE assessment_id = ? ORDER BY rank""",
            (item["id"],),
        )
    ]
    recommendations = []
    for recommendation_row in connection.execute(
        """SELECT * FROM suitability_recommendations
        WHERE assessment_id = ? ORDER BY rank""",
        (item["id"],),
    ):
        recommendation = dict(recommendation_row)
        for stored, public in (
            ("current_capability_json", "current_capability"),
            ("target_minimum_json", "target_minimum"),
            ("target_recommended_json", "target_recommended"),
        ):
            recommendation[public] = json.loads(recommendation.pop(stored))
        recommendations.append(recommendation)
    item["recommendations"] = recommendations
    weighted = round(
        sum(
            float(component["raw_component_score"])
            * float(component["effective_weight"])
            for component in components
            if component["raw_component_score"] is not None
        ),
        4,
    )
    reconstructed = weighted
    for gate in item["gates_and_caps"]:
        if gate["applied"] and gate["configured_cap"] is not None:
            reconstructed = min(reconstructed, float(gate["configured_cap"]))
    item["score_reconstruction"] = {
        "weighted_score_before_caps": weighted,
        "final_score_after_caps": (
            round(reconstructed, 4)
            if item["suitability_index"] is not None else None
        ),
        "stored_score": item["suitability_index"],
        "method": item["normalization_method"],
    }
    item["excluded_components"] = [
        component for component in components
        if component["raw_component_score"] is None
    ]
    return item


def get_quality_assessments(
    connection: sqlite3.Connection,
    limit: int,
    offset: int,
    start: str | None = None,
    end: str | None = None,
    sort: str = "newest",
    device_id: str | None = None,
    profile_key: str | None = None,
    suitability_result: str | None = None,
    evaluation_state: str | None = None,
    *,
    full: bool = True,
) -> tuple[list[dict[str, Any]], int]:
    clauses: list[str] = []
    parameters: list[Any] = []
    for value, clause in (
        (start, "assessed_at_utc >= ?"),
        (end, "assessed_at_utc <= ?"),
        (device_id, "device_id = ?"),
        (profile_key, "profile_key = ?"),
        (suitability_result, "suitability_result = ?"),
        (evaluation_state, "evaluation_state = ?"),
    ):
        if value is not None:
            clauses.append(clause)
            parameters.append(value)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    total = int(connection.execute(
        f"SELECT COUNT(*) FROM workload_suitability_assessments {where}",
        parameters,
    ).fetchone()[0])
    direction = "ASC" if sort == "oldest" else "DESC"
    rows = connection.execute(
        f"""SELECT * FROM workload_suitability_assessments {where}
        ORDER BY assessed_at_utc {direction}, id {direction}
        LIMIT ? OFFSET ?""",
        [*parameters, limit, offset],
    ).fetchall()
    return [
        _decode_assessment(connection, row, full=full) for row in rows
    ], total


def get_quality_assessment(
    connection: sqlite3.Connection,
    assessment_id: int,
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM workload_suitability_assessments WHERE id = ?",
        (assessment_id,),
    ).fetchone()
    return _decode_assessment(connection, row) if row else None
