"""Immutable future-alert explanations and evidence-confidence calculation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from analytics.alert_catalogue import ALERT_TRIGGER_THRESHOLDS
from backend.postcalibration_repository import get_validation_for_rule


EXPLANATION_VERSION = "alert-explanation-v1"
CONFIDENCE_VERSION = "alert-evidence-confidence-v1"
CONFIDENCE_WEIGHTS = {
    "evidence_completeness": 0.25,
    "active_baseline_adequacy": 0.20,
    "required_metric_availability": 0.15,
    "sampling_continuity": 0.15,
    "threshold_exceedance_margin": 0.15,
    "indicator_agreement": 0.05,
    "evidence_freshness": 0.05,
}


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if result == result else None
    except (TypeError, ValueError):
        return None


def _threshold_margin(category: str, window: dict[str, Any], candidate: dict[str, Any]) -> float | None:
    values: list[float] = []
    pairs = {
        "resource_pressure": (("cpu_avg", 85.0, 100.0),),
        "memory_and_swap_pressure": (("ram_avg", 85.0, 100.0),),
        "disk_capacity_pressure": (("disk_usage_avg", 90.0, 100.0),),
        "thermal_evidence": (("cpu_temperature_avg", 85.0, 105.0), ("gpu_temperature_avg", 85.0, 105.0)),
    }.get(category, ())
    for key, threshold, upper in pairs:
        value = _number(window.get(key))
        if value is not None:
            values.append(max(0.0, min(100.0, (value - threshold) / (upper - threshold) * 100.0)))
    if values:
        return max(values)
    # For composite event/health rules the candidate's effective evidence is
    # used only as a bounded exceedance-margin input, not copied as confidence.
    return _number(candidate.get("effective_strength"))


def calculate_confidence(
    connection,
    bundle: dict[str, Any],
    candidate: dict[str, Any],
    *,
    created_at_utc: str,
) -> tuple[float | None, str | None, list[dict[str, Any]]]:
    window = bundle["window"]
    alert_inputs = bundle["inputs"]
    required = [row for row in alert_inputs if row["input_category"] == "required_core"]
    available_required = [row for row in required if row["availability_status"] == "available"]
    baseline = connection.execute(
        """SELECT id,lifecycle_state FROM baseline_versions
        WHERE id = ?""",
        (bundle["health"].get("baseline_version_id"),),
    ).fetchone() if bundle["health"].get("baseline_version_id") else None
    gap = _number(window.get("source_max_internal_gap_seconds"))
    continuity = (
        100.0 if gap is not None and gap <= 35 else
        80.0 if gap is not None and gap <= 45 else
        50.0 if gap is not None and gap <= 60 else
        0.0 if gap is not None else None
    )
    created = datetime.fromisoformat(created_at_utc)
    ended = datetime.fromisoformat(window["window_end_utc"])
    age = max(0.0, (created - ended).total_seconds())
    freshness = 100.0 if age <= 600 else max(0.0, 100.0 - (age - 600) / 30.0)
    supporting = len([item for item in candidate["evidence"] if not item["suppressed"]])
    contradiction = len(candidate.get("contradictory", []))
    agreement = max(0.0, min(100.0, 65.0 + supporting * 12.0 - contradiction * 18.0))
    component_values = {
        "evidence_completeness": max(0.0, min(100.0, float(window["coverage_ratio"]) * 100.0)),
        "active_baseline_adequacy": 100.0 if baseline and baseline["lifecycle_state"] == "active" else None,
        "required_metric_availability": (
            len(available_required) / len(required) * 100.0 if required else 100.0
        ),
        "sampling_continuity": continuity,
        "threshold_exceedance_margin": _threshold_margin(candidate["category"], window, candidate),
        "indicator_agreement": agreement,
        "evidence_freshness": freshness,
    }
    explanations = {
        "evidence_completeness": "Completed-window source coverage.",
        "active_baseline_adequacy": "The baseline version actually referenced by the assessment is active.",
        "required_metric_availability": "Availability of required core health inputs.",
        "sampling_continuity": "Maximum gap between authoritative raw source samples.",
        "threshold_exceedance_margin": "Margin beyond the documented triggering threshold or composite boundary.",
        "indicator_agreement": "Agreement among independent supporting indicators after correlated suppression.",
        "evidence_freshness": "Delay between the evidence window and immutable snapshot creation.",
    }
    components = []
    available_weight = sum(
        CONFIDENCE_WEIGHTS[key] for key, value in component_values.items()
        if value is not None
    )
    for key, weight in CONFIDENCE_WEIGHTS.items():
        value = component_values[key]
        effective = weight / available_weight if value is not None and available_weight else 0.0
        components.append({
            "component_key": key,
            "component_value": round(value, 4) if value is not None else None,
            "configured_weight": weight,
            "weighted_contribution": round(value * effective, 4) if value is not None else None,
            "availability_status": "available" if value is not None else "unavailable",
            "explanation": explanations[key],
        })
    required_keys = {
        "evidence_completeness", "active_baseline_adequacy",
        "required_metric_availability", "sampling_continuity",
    }
    if any(component_values[key] is None for key in required_keys):
        return None, None, components
    confidence = round(sum(
        float(item["weighted_contribution"] or 0.0) for item in components
    ), 4)
    label = "low" if confidence < 50 else "moderate" if confidence < 75 else "high"
    return confidence, label, components


def persist_alert_snapshot(
    connection,
    *,
    alert_id: int,
    occurrence_id: int,
    bundle: dict[str, Any],
    candidate: dict[str, Any],
    algorithm_version: str,
    configuration_version: str,
) -> None:
    """Persist exactly once for a new future occurrence."""
    if connection.execute(
        "SELECT 1 FROM alert_explanation_snapshots WHERE alert_occurrence_id=?",
        (occurrence_id,),
    ).fetchone():
        return
    alert = connection.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
    if alert is None:
        return
    window, health = bundle["window"], bundle["health"]
    created = datetime.now(timezone.utc).isoformat()
    confidence, label, components = calculate_confidence(
        connection, bundle, candidate, created_at_utc=created
    )
    triggering = [item["evidence_key"] for item in candidate["evidence"] if not item["suppressed"]]
    observed = {item["evidence_key"]: item["raw"] for item in candidate["evidence"]}
    deviations = []
    baseline_values: dict[str, Any] = {}
    if bundle.get("deviation"):
        for row in connection.execute(
            """SELECT feature_name,observed_value,baseline_centre,expected_low,
            expected_high,normalized_deviation_score,severity_band
            FROM deviation_feature_results WHERE assessment_id=?
            ORDER BY normalized_deviation_score DESC""",
            (bundle["deviation"]["id"],),
        ):
            value = dict(row)
            baseline_values[value["feature_name"]] = {
                "centre": value["baseline_centre"], "expected_low": value["expected_low"],
                "expected_high": value["expected_high"],
            }
            deviations.append(value)
    rule_identifier = f"smartops-alert:{candidate['category']}"
    validation = get_validation_for_rule(
        connection, rule_identifier, algorithm_version,
        window.get("dominant_workload_class"),
    )
    plain = candidate["definition"].explanation_template.format(
        consecutive=candidate.get("consecutive", 1),
        health_band=health["health_band"],
    )
    cursor = connection.execute(
        """INSERT INTO alert_explanation_snapshots (
        alert_id,alert_occurrence_id,alert_category,alert_severity,workload_profile,
        baseline_id,baseline_version_id,feature_window_id,evidence_start_utc,
        evidence_end_utc,source_sample_count,evidence_completeness,
        triggering_rule_identifier,triggering_rule_version,triggering_metrics_json,
        observed_values_json,baseline_values_json,thresholds_json,deviations_json,
        top_contributors_json,missing_evidence_json,risk_context_json,
        health_context_json,plain_language_explanation,explanation_version,
        alert_confidence,confidence_label,confidence_calculation_version,
        validation_registry_id,created_at_utc
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            alert_id, occurrence_id, candidate["category"], candidate["severity"],
            window.get("dominant_workload_class"), alert["baseline_id"],
            alert["baseline_version_id"], window["id"], window["window_start_utc"],
            window["window_end_utc"], int(window.get("source_sample_count") or window["sample_count"]),
            float(window["coverage_ratio"]), rule_identifier,
            f"{algorithm_version}/{configuration_version}", json.dumps(triggering),
            json.dumps(observed, sort_keys=True), json.dumps(baseline_values, sort_keys=True),
            json.dumps(ALERT_TRIGGER_THRESHOLDS.get(candidate["category"], {}), sort_keys=True),
            json.dumps(deviations, sort_keys=True),
            json.dumps([{"factor": candidate["factor"], "strength": candidate["effective_strength"]}]),
            json.dumps([
                {"input": row["input_name"], "status": row["availability_status"], "reason": row["excluded_reason"]}
                for row in bundle["inputs"] if row["availability_status"] != "available"
            ]),
            json.dumps({
                "assessment_id": bundle["risk"]["id"] if bundle["risk"] else None,
                "risk_evidence_index": bundle["risk"]["risk_evidence_index"] if bundle["risk"] else None,
                "evidence_level": bundle["risk"]["evidence_level"] if bundle["risk"] else None,
            }),
            json.dumps({
                "assessment_id": health["id"], "score": health["system_health_score"],
                "band": health["health_band"], "evaluation_state": health["evaluation_state"],
            }),
            plain, EXPLANATION_VERSION, confidence, label, CONFIDENCE_VERSION,
            validation["id"] if validation else None, created,
        ),
    )
    snapshot_id = int(cursor.lastrowid)
    for item in components:
        connection.execute(
            """INSERT INTO alert_confidence_components (
            snapshot_id,component_key,component_value,configured_weight,
            weighted_contribution,availability_status,explanation
            ) VALUES (?,?,?,?,?,?,?)""",
            (
                snapshot_id, item["component_key"], item["component_value"],
                item["configured_weight"], item["weighted_contribution"],
                item["availability_status"], item["explanation"],
            ),
        )
