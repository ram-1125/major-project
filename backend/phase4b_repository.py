"""Read-only repository queries for Phase 4B PC Quality Check."""

from __future__ import annotations

import json
import sqlite3
from typing import Any


LOCAL_AI_EXECUTABLES = (
    "ollama.exe",
    "lm studio.exe",
    "lmstudio.exe",
    "llama-server.exe",
    "koboldcpp.exe",
    "comfyui.exe",
)


def get_profile_observation(
    connection: sqlite3.Connection,
    profile_key: str,
    device_id: str | None = None,
) -> dict[str, Any]:
    """Return privacy-safe relevance evidence, separate from hardware scoring."""
    if profile_key == "modern_3d_gaming":
        clauses = ["dominant_workload_class = 'gaming_or_3d'"]
        values: list[Any] = []
        if device_id:
            clauses.append("device_id = ?")
            values.append(device_id)
        row = connection.execute(
            f"""SELECT COUNT(*) evidence_count, MIN(window_start_utc) first_observed_utc,
            MAX(window_end_utc) last_observed_utc FROM feature_windows
            WHERE {' AND '.join(clauses)}""",
            values,
        ).fetchone()
        count = int(row["evidence_count"])
        return {
            "state": "observed" if count else "not_observed",
            "evidence_count": count,
            "first_observed_utc": row["first_observed_utc"],
            "last_observed_utc": row["last_observed_utc"],
            "evidence_basis": "stored_gaming_or_3d_feature_windows",
            "rule_version": "suitability-observation-v1",
        }
    if profile_key == "local_ai_and_gpu_compute":
        placeholders = ",".join("?" for _ in LOCAL_AI_EXECUTABLES)
        device_metric = "AND metric.device_id = ?" if device_id else ""
        parameters: list[Any] = [*LOCAL_AI_EXECUTABLES]
        if device_id:
            parameters.append(device_id)
        parameters.extend(LOCAL_AI_EXECUTABLES)
        if device_id:
            parameters.append(device_id)
        row = connection.execute(
            f"""WITH evidence AS (
              SELECT metric.timestamp_utc timestamp_utc
              FROM metrics metric
              WHERE LOWER(COALESCE(metric.foreground_process_name,'')) IN ({placeholders})
                {device_metric}
              UNION
              SELECT metric.timestamp_utc
              FROM process_snapshots process
              JOIN metrics metric ON metric.id = process.metric_id
              WHERE LOWER(process.process_name) IN ({placeholders})
                {device_metric}
            )
            SELECT COUNT(*) evidence_count, MIN(timestamp_utc) first_observed_utc,
                   MAX(timestamp_utc) last_observed_utc FROM evidence""",
            parameters,
        ).fetchone()
        count = int(row["evidence_count"])
        return {
            "state": "observed" if count else "not_observed",
            "evidence_count": count,
            "first_observed_utc": row["first_observed_utc"],
            "last_observed_utc": row["last_observed_utc"],
            "evidence_basis": "recognized_local_ai_executable_metadata",
            "recognized_executables": list(LOCAL_AI_EXECUTABLES),
            "privacy_boundary": "no_commands_titles_urls_history_or_content",
            "rule_version": "suitability-observation-v1",
        }
    return {
        "state": "not_applicable",
        "evidence_count": 0,
        "first_observed_utc": None,
        "last_observed_utc": None,
        "evidence_basis": "no_profile_specific_observation_rule",
        "rule_version": "suitability-observation-v1",
    }


def suitability_presentation(
    assessment: dict[str, Any] | None,
    observation: dict[str, Any],
    *,
    profile_known: bool,
) -> dict[str, Any]:
    """Disambiguate observation from inventory-based evaluation state."""
    if not profile_known:
        return {
            "state": "not_applicable", "label": "Not applicable",
            "explanation": "This hardware-suitability profile is not available.",
            "missing_requirements": [], "observation": observation,
            "supporting_factor": None, "limiting_factor": None,
        }
    if assessment and assessment["evaluation_state"] in {"assessed", "provisional"}:
        supporting = next(
            (item for item in assessment.get("components", []) if item.get("recommended_passed") is True),
            None,
        )
        limiting = (assessment.get("limiting_components") or [None])[0]
        return {
            "state": "evaluated", "label": "Evaluated",
            "explanation": "Genuine stored hardware inventory was evaluated for this scenario.",
            "missing_requirements": [], "observation": observation,
            "supporting_factor": supporting, "limiting_factor": limiting,
            "evaluation_time_utc": assessment.get("assessed_at_utc"),
            "evidence_source": f"hardware_inventory_snapshot_{assessment.get('inventory_snapshot_id')}",
        }
    if assessment:
        missing = [
            str(item).removesuffix("_unavailable_required").replace("_", " ")
            for item in (assessment.get("reason_codes") or [])
            if str(item).endswith("_unavailable_required")
        ]
        for component in assessment.get("components", []):
            if component.get("detection_status") == "unavailable_required":
                precise = [
                    str(reason).removesuffix("_unavailable_required").replace("_", " ")
                    for reason in component.get("reason_codes", [])
                    if str(reason).endswith("_unavailable_required")
                ]
                for readable in precise or [
                    str(component["component_name"]).replace("_", " ")
                ]:
                    if readable not in missing:
                        missing.append(readable)
        return {
            "state": "cannot_evaluate_required_hardware_missing",
            "label": "Cannot evaluate — required hardware information missing",
            "explanation": (
                "Workload observation does not substitute for the required hardware inventory."
            ),
            "missing_requirements": missing,
            "observation": observation,
            "supporting_factor": None,
            "limiting_factor": None,
            "evaluation_time_utc": assessment.get("assessed_at_utc"),
            "evidence_source": f"hardware_inventory_snapshot_{assessment.get('inventory_snapshot_id')}",
        }
    if observation["state"] == "observed":
        state, label = "observed_evaluation_pending", "Observed — evaluation pending"
        explanation = "Relevant local workload evidence exists, but no hardware assessment is stored yet."
    elif observation["state"] == "not_observed":
        state, label = "not_observed", "Not observed"
        explanation = "No privacy-safe stored evidence identifies this workload on this device."
    else:
        state, label = "not_applicable", "Not applicable"
        explanation = "No profile-specific observation rule applies."
    return {
        "state": state, "label": label, "explanation": explanation,
        "missing_requirements": [], "observation": observation,
        "supporting_factor": None, "limiting_factor": None,
    }


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
        ORDER BY COALESCE(last_checked_at_utc, captured_at_utc) DESC,
                 id DESC LIMIT 1""",
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
    inventory_snapshot_id: int | None = None,
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
        (inventory_snapshot_id, "inventory_snapshot_id = ?"),
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
