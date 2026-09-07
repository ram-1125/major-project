"""Phase 5A local predictive-alert evaluation and lifecycle management."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from agent.config import get_database_path
from analytics.alert_catalogue import (
    ALGORITHM_VERSION,
    CATALOGUE_VERSION,
    CONFIGURATION_VERSION,
    DEFINITIONS,
    INTERPRETATION,
    SEVERITY_ORDER,
)
from analytics.alert_explainability import persist_alert_snapshot
from backend.database import (
    database_connection,
    initialize_database,
    run_write_transaction,
)


WINDOW_SECONDS = 300
MINIMUM_COVERAGE = 0.8
EXPECTED_WORKLOAD_CONFIDENCE = 0.65


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _fingerprint(
    device_id: str,
    category: str,
    evidence_domain: str,
    probable_factor: str,
) -> str:
    material = "|".join(
        (
            device_id,
            category,
            evidence_domain,
            probable_factor,
            ALGORITHM_VERSION,
            CONFIGURATION_VERSION,
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _source_signature(bundle: dict[str, Any]) -> str:
    material = {
        "feature_window_id": bundle["window"]["id"],
        "health_id": bundle["health"]["id"],
        # Use reconstructable semantics, not bookkeeping timestamps.  Phase 4A
        # may rebuild an identical assessment after upstream maintenance; that
        # must not create a new Phase 5A evaluation run.
        "health_feature_signature": bundle["health"]["feature_signature"],
        "health_score": bundle["health"]["system_health_score"],
        "health_band": bundle["health"]["health_band"],
        "health_state": bundle["health"]["evaluation_state"],
        "health_confidence": bundle["health"]["data_confidence"],
        "health_reasons": bundle["health"]["reason_codes_json"],
        "health_baseline_id": bundle["health"]["baseline_id"],
        "health_deviation_id": bundle["health"]["deviation_assessment_id"],
        "health_risk_id": bundle["health"]["risk_assessment_id"],
        "risk_id": bundle["risk"]["id"] if bundle["risk"] else None,
        "risk_index": (
            bundle["risk"]["risk_evidence_index"] if bundle["risk"] else None
        ),
        "deviation_id": (
            bundle["deviation"]["id"] if bundle["deviation"] else None
        ),
        "event_ids": [event["id"] for event in bundle["events"]],
        "algorithm": ALGORITHM_VERSION,
        "configuration": CONFIGURATION_VERSION,
        "catalogue": CATALOGUE_VERSION,
    }
    return hashlib.sha256(_json(material).encode("utf-8")).hexdigest()


def _material_evidence_signature(
    candidate: dict[str, Any] | None,
    condition_met: bool,
    temporal_pattern: str,
) -> str:
    """Fingerprint meaningful evidence state, excluding raw-value jitter.

    Exact measurements remain in the source assessment. Occurrence rows are
    reserved for changes a person would recognize as a lifecycle/evidence
    event, rather than every poll or completed window.
    """
    material = {
        "condition_met": condition_met,
        "temporal_pattern": temporal_pattern,
        "category": candidate.get("category") if candidate else None,
        "factor": candidate.get("factor") if candidate else None,
        "severity": candidate.get("severity") if candidate else "informational",
        "trend": candidate.get("trend") if candidate else "recovery",
        "reason_codes": sorted(candidate.get("reason_codes", [])) if candidate else [],
        "evidence": sorted(
            (
                item.get("evidence_type"), item.get("evidence_key"),
                item.get("correlation_group"), bool(item.get("suppressed")),
                item.get("reason_code"),
            )
            for item in (candidate.get("evidence", []) if candidate else [])
        ),
    }
    return hashlib.sha256(_json(material).encode("utf-8")).hexdigest()


def _bundle_for_window(
    connection: sqlite3.Connection,
    window: sqlite3.Row | dict[str, Any],
) -> dict[str, Any] | None:
    item = dict(window)
    health_row = connection.execute(
        """SELECT * FROM health_assessments
        WHERE feature_window_id = ?
        ORDER BY assessed_at_utc DESC, id DESC LIMIT 1""",
        (item["id"],),
    ).fetchone()
    if health_row is None:
        return None
    health = dict(health_row)
    risk_row = (
        connection.execute(
            "SELECT * FROM risk_assessments WHERE id = ?",
            (health["risk_assessment_id"],),
        ).fetchone()
        if health["risk_assessment_id"] is not None
        else connection.execute(
            """SELECT * FROM risk_assessments WHERE feature_window_id = ?
            ORDER BY evaluated_at_utc DESC, id DESC LIMIT 1""",
            (item["id"],),
        ).fetchone()
    )
    deviation_row = (
        connection.execute(
            "SELECT * FROM deviation_assessments WHERE id = ?",
            (health["deviation_assessment_id"],),
        ).fetchone()
        if health["deviation_assessment_id"] is not None
        else None
    )
    events = [
        dict(row)
        for row in connection.execute(
            """SELECT id, event_timestamp_utc, channel, provider_name, event_id,
            event_level, smartops_category, safe_summary
            FROM windows_events
            WHERE device_id = ? AND event_timestamp_utc >= ?
            AND event_timestamp_utc < ?
            ORDER BY event_timestamp_utc, id""",
            (item["device_id"], item["window_start_utc"], item["window_end_utc"]),
        )
    ]
    components = {
        row["component_name"]: dict(row)
        for row in connection.execute(
            "SELECT * FROM health_component_scores WHERE health_assessment_id = ?",
            (health["id"],),
        )
    }
    deductions = [
        dict(row)
        for row in connection.execute(
            "SELECT * FROM health_deductions WHERE health_assessment_id = ?",
            (health["id"],),
        )
    ]
    inputs = [
        dict(row)
        for row in connection.execute(
            "SELECT * FROM health_input_status WHERE health_assessment_id = ?",
            (health["id"],),
        )
    ]
    candidates: list[dict[str, Any]] = []
    risk = dict(risk_row) if risk_row else None
    if risk:
        candidates = [
            dict(row)
            for row in connection.execute(
                """SELECT candidate_domain, rank, evidence_confidence, explanation
                FROM root_cause_candidates WHERE risk_assessment_id = ?
                ORDER BY rank""",
                (risk["id"],),
            )
        ]
    return {
        "window": item,
        "health": health,
        "risk": risk,
        "deviation": dict(deviation_row) if deviation_row else None,
        "events": events,
        "components": components,
        "deductions": deductions,
        "inputs": inputs,
        "root_causes": candidates,
    }


def _eligible(bundle: dict[str, Any] | None) -> tuple[bool, str | None]:
    if bundle is None:
        return False, "health_assessment_unavailable"
    window, health = bundle["window"], bundle["health"]
    if not bool(window["is_complete"]):
        return False, "incomplete_feature_window"
    if float(window["coverage_ratio"]) < MINIMUM_COVERAGE:
        return False, "coverage_below_0_8"
    if health["evaluation_state"] == "not_evaluated":
        return False, "health_not_evaluated"
    if health["system_health_score"] is None:
        return False, "health_score_unavailable"
    return True, None


def _severity(strength: float, *, serious: bool = False) -> str:
    if serious and strength >= 90:
        return "urgent"
    if strength >= 70:
        return "warning"
    if strength >= 40:
        return "advisory"
    return "informational"


def _evidence(
    evidence_type: str,
    key: str,
    source_table: str,
    source_id: int | None,
    group: str,
    raw: Any,
    effective: Any,
    reason: str,
    explanation: str,
    *,
    suppressed: bool = False,
    suppression_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "evidence_type": evidence_type,
        "evidence_key": key,
        "source_table": source_table,
        "source_id": source_id,
        "correlation_group": group,
        "raw": raw,
        "effective": effective,
        "suppressed": suppressed,
        "suppression_reason": suppression_reason,
        "reason_code": reason,
        "explanation": explanation,
    }


def _candidate(
    category: str,
    strength: float,
    factor: str,
    reasons: list[str],
    evidence: list[dict[str, Any]],
    *,
    serious: bool = False,
    contradictory: list[str] | None = None,
) -> dict[str, Any]:
    definition = DEFINITIONS[category]
    return {
        "category": category,
        "definition": definition,
        "factor": factor,
        "raw_strength": round(max(0.0, min(100.0, strength)), 4),
        "effective_strength": round(max(0.0, min(100.0, strength)), 4),
        "severity": _severity(strength, serious=serious),
        "reason_codes": reasons,
        "evidence": evidence,
        "contradictory": contradictory or [],
    }


def derive_candidates(bundle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return one correlated candidate per alert category for an eligible window."""
    window, health = bundle["window"], bundle["health"]
    workload = window.get("dominant_workload_class")
    workload_confidence = _number(window.get("workload_confidence")) or 0.0
    result: dict[str, dict[str, Any]] = {}

    cpu = _number(window.get("cpu_avg"))
    cpu_high = _number(window.get("cpu_high_ratio"))
    if cpu is not None and (cpu >= 85 or (cpu_high or 0) >= 0.7):
        raw = 40 + max(0.0, cpu - 85) * 2.5 + max(0.0, (cpu_high or 0) - 0.7) * 40
        effective = raw
        reasons = ["cpu_pressure"]
        contradictory: list[str] = []
        expected = (
            workload_confidence >= EXPECTED_WORKLOAD_CONFIDENCE
            and workload in DEFINITIONS["resource_pressure"].workload_exceptions
        )
        if expected:
            effective *= 0.3
            reasons.append("workload_exception_applied")
            contradictory.append(
                f"High CPU is consistent with the {workload} workload "
                f"(confidence {workload_confidence:.2f})."
            )
        candidate = _candidate(
            "resource_pressure", effective, "cpu_pressure", reasons,
            [
                _evidence(
                    "metric", "cpu_summary", "feature_windows", window["id"],
                    "cpu", {"cpu_avg": cpu, "cpu_high_ratio": cpu_high},
                    {"effective_strength": effective},
                    "correlated_cpu_summary",
                    "CPU average and high-ratio are combined into one CPU group.",
                ),
                _evidence(
                    "metric", "cpu_p95", "feature_windows", window["id"],
                    "cpu", {"cpu_p95": window.get("cpu_p95")}, {},
                    "correlated_cpu_metric_suppressed",
                    "CPU p95 supports the same CPU group and is not counted again.",
                    suppressed=True,
                    suppression_reason="correlated_with_cpu_summary",
                ),
            ],
            contradictory=contradictory,
        )
        candidate["raw_strength"] = round(min(100.0, raw), 4)
        result["resource_pressure"] = candidate

    ram = _number(window.get("ram_avg"))
    swap = _number(window.get("swap_avg"))
    if ram is not None and ram >= 85:
        raw = 35 + (ram - 85) * 2.2
        reasons = ["ram_pressure"]
        if swap is not None and swap >= 10:
            raw += min(25, swap * 0.8)
            reasons.append("swap_corroboration")
        result["memory_and_swap_pressure"] = _candidate(
            "memory_and_swap_pressure", raw, "memory_pressure", reasons,
            [
                _evidence(
                    "metric", "memory_summary", "feature_windows", window["id"],
                    "memory", {"ram_avg": ram, "swap_avg": swap},
                    {"combined_strength": min(100, raw)},
                    "correlated_memory_summary",
                    "RAM and page-file evidence are capped in one memory group.",
                ),
                _evidence(
                    "metric", "swap_support", "feature_windows", window["id"],
                    "memory", {"swap_avg": swap}, {},
                    "swap_not_double_counted",
                    "Swap is corroboration inside the memory group, not a second alert.",
                    suppressed=True,
                    suppression_reason="correlated_with_ram_pressure",
                ),
            ],
        )

    disk_usage = _number(window.get("disk_usage_avg"))
    if disk_usage is not None and disk_usage >= 90:
        strength = 40 + (disk_usage - 90) * 5
        result["disk_capacity_pressure"] = _candidate(
            "disk_capacity_pressure", strength, "disk_capacity",
            ["low_disk_capacity_headroom"],
            [_evidence(
                "metric", "disk_usage", "feature_windows", window["id"],
                "disk_capacity", {"disk_usage_avg": disk_usage},
                {"effective_strength": min(100, strength)},
                "disk_capacity_pressure",
                "Disk-capacity headroom is low regardless of workload type.",
            )],
        )

    read_rate = _number(window.get("disk_read_avg"))
    write_rate = _number(window.get("disk_write_avg"))
    io_total = (read_rate or 0.0) + (write_rate or 0.0)
    if (read_rate is not None or write_rate is not None) and io_total >= 50 * 1024 * 1024:
        raw = min(90.0, 35 + io_total / (20 * 1024 * 1024))
        effective = raw
        reasons = ["disk_io_pressure"]
        contradictory = []
        if (
            workload_confidence >= EXPECTED_WORKLOAD_CONFIDENCE
            and workload in DEFINITIONS["disk_io_pressure"].workload_exceptions
        ):
            effective *= 0.35
            reasons.append("workload_exception_applied")
            contradictory.append(
                f"Disk activity is consistent with {workload} at sufficient confidence."
            )
        candidate = _candidate(
            "disk_io_pressure", effective, "disk_io", reasons,
            [_evidence(
                "metric", "disk_io_summary", "feature_windows", window["id"],
                "disk_io", {"read_bytes_per_second": read_rate,
                            "write_bytes_per_second": write_rate},
                {"effective_strength": effective},
                "correlated_disk_io_summary",
                "Read and write throughput are interpreted as one disk-I/O group.",
            )],
            contradictory=contradictory,
        )
        candidate["raw_strength"] = round(raw, 4)
        result["disk_io_pressure"] = candidate

    cpu_temp = _number(window.get("cpu_temperature_avg"))
    gpu_temp = _number(window.get("gpu_temperature_avg"))
    temperature = max(value for value in (cpu_temp, gpu_temp) if value is not None) \
        if any(value is not None for value in (cpu_temp, gpu_temp)) else None
    if temperature is not None and temperature >= 85:
        result["thermal_evidence"] = _candidate(
            "thermal_evidence", 40 + (temperature - 85) * 4,
            "thermal_condition", ["elevated_temperature"],
            [_evidence(
                "metric", "available_temperature", "feature_windows", window["id"],
                "thermal", {"cpu_celsius": cpu_temp, "gpu_celsius": gpu_temp},
                {"highest_celsius": temperature},
                "available_thermal_evidence",
                "Only available temperature sensors contribute to this evidence.",
            )],
        )

    serious_events = [
        event for event in bundle["events"]
        if event["event_level"] in {"Critical", "Error"}
        and event["smartops_category"] in {
            "hardware", "storage", "power", "application_crash",
            "service_failure", "resource_exhaustion",
        }
    ]
    critical = [event for event in serious_events if event["event_level"] == "Critical"]
    if critical or len(serious_events) >= 2:
        strength = 95.0 if critical else min(85.0, 55 + len(serious_events) * 8)
        result["repeated_serious_event"] = _candidate(
            "repeated_serious_event", strength,
            serious_events[0]["smartops_category"],
            ["mapped_critical_event" if critical else "repeated_mapped_error_events"],
            [
                _evidence(
                    "windows_event", f"event_{event['id']}", "windows_events",
                    event["id"], "serious_events",
                    {
                        "level": event["event_level"],
                        "category": event["smartops_category"],
                        "provider": event["provider_name"],
                        "event_id": event["event_id"],
                    },
                    {"counted_once": True},
                    "mapped_serious_event",
                    event["safe_summary"],
                )
                for event in serious_events
            ],
            serious=True,
        )

    stability = bundle["components"].get("operating_stability")
    stability_score = _number(stability["component_score"]) if stability else None
    if stability_score is not None and stability_score < 70:
        result["system_stability"] = _candidate(
            "system_stability", 45 + (70 - stability_score),
            "operating_stability", ["reduced_operating_stability"],
            [_evidence(
                "health_component", "operating_stability", "health_component_scores",
                stability["id"], "stability",
                {"component_score": stability_score},
                {"effective_strength": min(100, 45 + 70 - stability_score)},
                "health_stability_component",
                "Phase 4A operating-stability evidence is used once.",
            )],
        )

    risk = bundle["risk"]
    if risk and _number(risk.get("risk_evidence_index")) is not None:
        risk_index = float(risk["risk_evidence_index"])
        if risk_index >= 50:
            root_causes = bundle["root_causes"]
            factor = root_causes[0]["candidate_domain"] if root_causes else "risk_evidence"
            result["increasing_risk_evidence"] = _candidate(
                "increasing_risk_evidence", risk_index, factor,
                ["eligible_phase3b_risk_evidence"],
                [_evidence(
                    "risk_assessment", "risk_evidence_index", "risk_assessments",
                    risk["id"], "learned_evidence",
                    {"risk_evidence_index": risk_index,
                     "evidence_level": risk["evidence_level"]},
                    {"effective_strength": risk_index},
                    "phase3b_primary_evidence",
                    "Eligible Phase 3B evidence is the primary learned-evidence input.",
                )],
            )

    score = float(health["system_health_score"])
    band = health["health_band"]
    if score < 70:
        strength = 40 + (70 - score) * 1.6
        independent_groups = {
            row["contribution_group"]
            for row in bundle["deductions"]
            if float(row["effective_deduction"]) > 0
        }
        serious = score < 30 and (
            len(independent_groups) >= 2 or bool(critical)
        )
        item = _candidate(
            "degraded_system_health", strength, "current_operating_condition",
            ["degraded_system_health", f"health_band_{band}"],
            [
                _evidence(
                    "health_assessment", "system_health_score",
                    "health_assessments", health["id"], "system_health",
                    {"score": score, "band": band,
                     "evaluation_state": health["evaluation_state"]},
                    {"effective_strength": min(100, strength)},
                    "phase4a_health_primary_evidence",
                    "The evaluated current operating condition supports this alert.",
                ),
                *(
                    [_evidence(
                        "risk_assessment", "risk_supporting_context",
                        "risk_assessments", risk["id"], "learned_evidence",
                        {"risk_evidence_index": risk["risk_evidence_index"]}, {},
                        "phase3b_not_double_counted",
                        "Phase 3B evidence already represented through Phase 4A is supporting only.",
                        suppressed=True,
                        suppression_reason="represented_in_phase4a_health",
                    )]
                    if risk else []
                ),
            ],
            serious=serious,
        )
        if item["severity"] == "urgent" and not serious:
            item["severity"] = "warning"
        result["degraded_system_health"] = item

    confidence = float(health["data_confidence"])
    core_quality_limit = (
        float(window["coverage_ratio"]) < 0.9
        or workload_confidence < EXPECTED_WORKLOAD_CONFIDENCE
        or any(
            row["availability_status"]
            in {"unavailable_required", "excluded_by_context"}
            for row in bundle["inputs"]
        )
    )
    if confidence < 60 and core_quality_limit:
        result["data_quality_limitation"] = _candidate(
            "data_quality_limitation", max(10, 60 - confidence),
            "data_confidence", ["limited_data_confidence"],
            [_evidence(
                "health_assessment", "data_confidence", "health_assessments",
                health["id"], "data_quality", {"data_confidence": confidence},
                {"effective_strength": max(10, 60 - confidence)},
                "evaluated_data_quality_limitation",
                "The score was evaluated, but available evidence has limited confidence.",
            )],
        )
    return result


def _prior_persistence(
    connection: sqlite3.Connection,
    bundle: dict[str, Any],
    category: str,
) -> tuple[int, str, str]:
    current = bundle["window"]
    count = 1
    first = current["window_start_utc"]
    strengths = [derive_candidates(bundle).get(category, {}).get("effective_strength", 0)]
    previous_end = datetime.fromisoformat(current["window_start_utc"])
    rows = connection.execute(
        """SELECT * FROM feature_windows
        WHERE device_id = ? AND window_start_utc < ?
        ORDER BY window_start_utc DESC, id DESC LIMIT 12""",
        (current["device_id"], current["window_start_utc"]),
    ).fetchall()
    for row in rows:
        prior = _bundle_for_window(connection, row)
        eligible, _ = _eligible(prior)
        if not eligible or prior is None:
            break
        end_time = datetime.fromisoformat(prior["window"]["window_end_utc"])
        if abs((previous_end - end_time).total_seconds()) > 1:
            break
        candidate = derive_candidates(prior).get(category)
        if candidate is None:
            break
        count += 1
        first = prior["window"]["window_start_utc"]
        strengths.append(candidate["effective_strength"])
        previous_end = datetime.fromisoformat(prior["window"]["window_start_utc"])
    chronological = list(reversed(strengths))
    trend = "stable"
    if len(chronological) >= 2:
        difference = chronological[-1] - chronological[0]
        if difference >= 12:
            trend = "increasing"
        elif difference <= -12:
            trend = "decreasing"
    return count, first, trend


def _excluded_inputs(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "input": row["input_name"],
            "status": row["availability_status"],
            "reason": row["excluded_reason"],
        }
        for row in bundle["inputs"]
        if row["availability_status"] != "available"
    ]


def _insert_occurrence(
    connection: sqlite3.Connection,
    alert_id: int,
    bundle: dict[str, Any],
    candidate: dict[str, Any] | None,
    condition_met: bool,
    temporal_pattern: str,
) -> int:
    window, health = bundle["window"], bundle["health"]
    evidence_signature = _material_evidence_signature(
        candidate, condition_met, temporal_pattern
    )
    severity = candidate["severity"] if candidate else "informational"
    raw = candidate["raw_strength"] if candidate else 0.0
    effective = candidate["effective_strength"] if candidate else 0.0
    existing = connection.execute(
        "SELECT id FROM alert_occurrences WHERE alert_id = ? AND feature_window_id = ?",
        (alert_id, window["id"]),
    ).fetchone()
    if existing:
        return int(existing["id"])
    cursor = connection.execute(
        """INSERT INTO alert_occurrences (
        alert_id, feature_window_id, health_assessment_id, risk_assessment_id,
        deviation_assessment_id, observed_at_utc, severity, condition_met,
        raw_evidence_strength, effective_evidence_strength, temporal_pattern,
        trend_direction, evidence_signature, created_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            alert_id, window["id"], health["id"],
            bundle["risk"]["id"] if bundle["risk"] else None,
            bundle["deviation"]["id"] if bundle["deviation"] else None,
            window["window_end_utc"], severity, int(condition_met), raw,
            effective, temporal_pattern,
            candidate.get("trend", "stable") if candidate else "recovery",
            evidence_signature, _utc_now().isoformat(),
        ),
    )
    occurrence_id = int(cursor.lastrowid)
    for item in candidate["evidence"] if candidate else []:
        connection.execute(
            """INSERT INTO alert_evidence (
            occurrence_id, evidence_type, evidence_key, source_table, source_id,
            correlation_group, raw_evidence_json, effective_evidence_json,
            suppressed, suppression_reason, reason_code, explanation
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                occurrence_id, item["evidence_type"], item["evidence_key"],
                item["source_table"], item["source_id"],
                item["correlation_group"], _json(item["raw"]),
                _json(item["effective"]), int(item["suppressed"]),
                item["suppression_reason"], item["reason_code"],
                item["explanation"],
            ),
        )
    if candidate:
        definition = candidate["definition"]
        explanations = [
            definition.explanation_template.format(
                consecutive=candidate.get("consecutive", 1),
                health_band=health["health_band"],
            ),
            *candidate["contradictory"],
            *definition.limitations,
        ]
        for sequence, explanation in enumerate(explanations, 1):
            connection.execute(
                """INSERT INTO alert_explanations
                (occurrence_id, explanation_type, sequence, explanation_text)
                VALUES (?, ?, ?, ?)""",
                (
                    occurrence_id,
                    "limitation" if explanation in definition.limitations
                    else "contradictory" if explanation in candidate["contradictory"]
                    else "summary",
                    sequence,
                    explanation,
                ),
            )
        for kind, values in (
            ("diagnostic_verification", definition.diagnostic_steps),
            ("preventive_guidance", definition.preventive_guidance),
        ):
            for sequence, recommendation in enumerate(values, 1):
                connection.execute(
                    """INSERT INTO alert_recommendations
                    (occurrence_id, recommendation_type, sequence, recommendation_text)
                    VALUES (?, ?, ?, ?)""",
                    (occurrence_id, kind, sequence, recommendation),
                )
        # Schema-18 snapshots are immutable and attach only to newly persisted
        # occurrences. Legacy alerts intentionally remain labelled as legacy.
        persist_alert_snapshot(
            connection,
            alert_id=alert_id,
            occurrence_id=occurrence_id,
            bundle=bundle,
            candidate=candidate,
            algorithm_version=ALGORITHM_VERSION,
            configuration_version=CONFIGURATION_VERSION,
        )
    else:
        connection.execute(
            """INSERT INTO alert_explanations
            (occurrence_id, explanation_type, sequence, explanation_text)
            VALUES (?, 'recovery', 1, ?)""",
            (
                occurrence_id,
                "A subsequent completed window no longer supported the alert condition.",
            ),
        )
    return occurrence_id


def _transition(
    connection: sqlite3.Connection,
    alert_id: int,
    at: str,
    previous_state: str | None,
    new_state: str,
    previous_severity: str | None,
    new_severity: str,
    transition_type: str,
    reason: str,
    window_id: int | None,
    metadata: dict[str, Any] | None = None,
) -> None:
    connection.execute(
        """INSERT INTO alert_state_transitions (
        alert_id, transition_timestamp_utc, previous_state, new_state,
        previous_severity, new_severity, transition_type, reason_code,
        source_feature_window_id, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            alert_id, at, previous_state, new_state, previous_severity,
            new_severity, transition_type, reason, window_id,
            _json(metadata or {}),
        ),
    )


def _open_or_update(
    connection: sqlite3.Connection,
    bundle: dict[str, Any],
    candidate: dict[str, Any],
) -> str:
    window, health = bundle["window"], bundle["health"]
    definition = candidate["definition"]
    consecutive, first, trend = _prior_persistence(
        connection, bundle, candidate["category"]
    )
    candidate["consecutive"] = consecutive
    candidate["trend"] = trend
    sudden = candidate["category"] == "repeated_serious_event" and (
        candidate["severity"] == "urgent"
    )
    minimum = definition.minimum_persistence
    if candidate["category"] == "resource_pressure" and (
        "workload_exception_applied" in candidate["reason_codes"]
    ):
        minimum += 1
    if consecutive < minimum and not sudden:
        return "isolated"
    fingerprint = _fingerprint(
        window["device_id"], candidate["category"],
        definition.evidence_domain, candidate["factor"],
    )
    active = connection.execute(
        """SELECT * FROM alerts
        WHERE alert_fingerprint = ? AND state != 'resolved'""",
        (fingerprint,),
    ).fetchone()
    now = _utc_now().isoformat()
    excluded = _excluded_inputs(bundle)
    factors = [
        {
            "domain": row["candidate_domain"],
            "rank": row["rank"],
            "confidence": row["evidence_confidence"],
        }
        for row in bundle["root_causes"]
    ] or [{"domain": candidate["factor"], "rank": 1, "confidence": None}]
    evaluation_state = health["evaluation_state"]
    if active is None:
        predecessor = connection.execute(
            """SELECT * FROM alerts WHERE alert_fingerprint = ?
            ORDER BY lifecycle_number DESC LIMIT 1""",
            (fingerprint,),
        ).fetchone()
        if predecessor and predecessor["cooldown_until_utc"]:
            if window["window_end_utc"] <= predecessor["cooldown_until_utc"]:
                return "cooldown"
        lifecycle = int(predecessor["lifecycle_number"]) + 1 if predecessor else 1
        cursor = connection.execute(
            """INSERT INTO alerts (
            device_id, alert_fingerprint, lifecycle_number, predecessor_alert_id,
            alert_code, category, title, description, evidence_domain,
            probable_factor, current_severity, peak_severity, state,
            evaluation_state, data_confidence, workload_context,
            workload_confidence, first_observed_utc, latest_observed_utc,
            last_evidence_utc, occurrence_count, consecutive_window_count,
            duration_seconds, trend_direction, recovery_window_count,
            recovery_state, cooldown_until_utc, baseline_id,
            deviation_assessment_id, risk_assessment_id, health_assessment_id,
            baseline_version_id, baseline_rule_version, workload_rule_version,
            source_feature_window_id, probable_factors_json,
            contradictory_evidence_json, excluded_inputs_json, reason_codes_json,
            algorithm_version, configuration_version, catalogue_version,
            created_at_utc, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, 0, 'not_recovering', NULL, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                window["device_id"], fingerprint, lifecycle,
                int(predecessor["id"]) if predecessor else None,
                definition.code, candidate["category"], definition.title,
                definition.description, definition.evidence_domain,
                candidate["factor"], candidate["severity"], candidate["severity"],
                evaluation_state, health["data_confidence"],
                window.get("dominant_workload_class"),
                window.get("workload_confidence"), first,
                window["window_end_utc"], window["window_end_utc"], consecutive,
                consecutive,
                max(0.0, (
                    datetime.fromisoformat(window["window_end_utc"])
                    - datetime.fromisoformat(first)
                ).total_seconds()),
                trend, health["baseline_id"],
                health["deviation_assessment_id"], health["risk_assessment_id"],
                health["id"], health["baseline_version_id"],
                health["baseline_rule_version"],
                window.get("workload_rule_version"), window["id"], _json(factors),
                _json(candidate["contradictory"]), _json(excluded),
                _json(candidate["reason_codes"]), ALGORITHM_VERSION,
                CONFIGURATION_VERSION, CATALOGUE_VERSION, now, now,
            ),
        )
        alert_id = int(cursor.lastrowid)
        material_signature = _material_evidence_signature(
            candidate,
            True,
            "sudden_serious_event" if sudden else
            "sustained" if consecutive >= 3 else "repeated",
        )
        connection.execute(
            """UPDATE alerts SET occurrence_count = 1, observation_count = ?,
            last_seen_at_utc = ?, last_material_evidence_signature = ?
            WHERE id = ?""",
            (consecutive, window["window_end_utc"], material_signature, alert_id),
        )
        _insert_occurrence(
            connection, alert_id, bundle, candidate, True,
            "sudden_serious_event" if sudden else
            "sustained" if consecutive >= 3 else "repeated",
        )
        _transition(
            connection, alert_id, window["window_end_utc"], None, "open",
            None, candidate["severity"], "opened", "eligible_evidence",
            window["id"], {"consecutive_windows": consecutive},
        )
        return "created"

    current = dict(active)
    alert_id = int(current["id"])
    new_state = (
        "acknowledged" if current["acknowledged_at_utc"] else "open"
    )
    peak = (
        candidate["severity"]
        if SEVERITY_ORDER[candidate["severity"]]
        > SEVERITY_ORDER[current["peak_severity"]]
        else current["peak_severity"]
    )
    duration = max(
        0.0,
        (
            datetime.fromisoformat(window["window_end_utc"])
            - datetime.fromisoformat(current["first_observed_utc"])
        ).total_seconds(),
    )
    temporal_pattern = "sustained" if consecutive >= 3 else "repeated"
    material_signature = _material_evidence_signature(
        candidate, True, temporal_pattern
    )
    severity_escalated = (
        SEVERITY_ORDER[candidate["severity"]]
        > SEVERITY_ORDER[current["current_severity"]]
    )
    reopened = current["state"] == "recovering"
    prior_material = current.get("last_material_evidence_signature")
    material_changed = (
        prior_material is not None and prior_material != material_signature
    )
    durable_occurrence = severity_escalated or reopened or material_changed
    connection.execute(
        """UPDATE alerts SET current_severity = ?, peak_severity = ?, state = ?,
        evaluation_state = ?, data_confidence = ?, workload_context = ?,
        workload_confidence = ?, latest_observed_utc = ?, last_evidence_utc = ?,
        occurrence_count = occurrence_count + ?, observation_count = observation_count + 1,
        last_seen_at_utc = ?, last_material_evidence_signature = ?,
        consecutive_window_count = ?, duration_seconds = ?, trend_direction = ?,
        recovery_window_count = 0, recovery_state = 'not_recovering',
        baseline_id = ?, deviation_assessment_id = ?, risk_assessment_id = ?,
        health_assessment_id = ?, baseline_version_id = ?, baseline_rule_version = ?,
        workload_rule_version = ?,
        source_feature_window_id = ?,
        probable_factors_json = ?, contradictory_evidence_json = ?,
        excluded_inputs_json = ?, reason_codes_json = ?, updated_at_utc = ?
        WHERE id = ?""",
        (
            candidate["severity"], peak, new_state, evaluation_state,
            health["data_confidence"], window.get("dominant_workload_class"),
            window.get("workload_confidence"), window["window_end_utc"],
            window["window_end_utc"], int(durable_occurrence),
            window["window_end_utc"], material_signature,
            consecutive, duration, trend,
            health["baseline_id"], health["deviation_assessment_id"],
            health["risk_assessment_id"], health["id"],
            health["baseline_version_id"], health["baseline_rule_version"],
            window.get("workload_rule_version"),
            window["id"],
            _json(factors), _json(candidate["contradictory"]), _json(excluded),
            _json(candidate["reason_codes"]), now, alert_id,
        ),
    )
    if durable_occurrence:
        _insert_occurrence(
            connection, alert_id, bundle, candidate, True, temporal_pattern,
        )
    if current["state"] == "recovering":
        _transition(
            connection, alert_id, window["window_end_utc"], "recovering",
            new_state, current["current_severity"], candidate["severity"],
            "reopened_during_recovery", "condition_returned", window["id"],
        )
    elif severity_escalated:
        _transition(
            connection, alert_id, window["window_end_utc"], current["state"],
            new_state, current["current_severity"], candidate["severity"],
            "severity_escalated", "evidence_strength_increased", window["id"],
        )
    return "updated"


def _recover_absent(
    connection: sqlite3.Connection,
    bundle: dict[str, Any],
    active_categories: set[str],
) -> tuple[int, int]:
    window = bundle["window"]
    recovering = resolved = 0
    rows = connection.execute(
        """SELECT * FROM alerts WHERE device_id = ? AND state != 'resolved'
        AND source_feature_window_id != ? AND latest_observed_utc < ?""",
        (window["device_id"], window["id"], window["window_end_utc"]),
    ).fetchall()
    for row in rows:
        current = dict(row)
        if current["category"] in active_categories:
            continue
        definition = DEFINITIONS.get(current["category"])
        if definition is None:
            continue
        recovery_count = int(current["recovery_window_count"]) + 1
        new_state = (
            "resolved"
            if recovery_count >= definition.recovery_windows
            else "recovering"
        )
        now = _utc_now().isoformat()
        state_changed = current["state"] != new_state
        cooldown_until = (
            datetime.fromisoformat(window["window_end_utc"])
            + timedelta(seconds=definition.cooldown_windows * WINDOW_SECONDS)
        ).isoformat() if new_state == "resolved" else None
        connection.execute(
            """UPDATE alerts SET state = ?, latest_observed_utc = ?,
            occurrence_count = occurrence_count + ?,
            observation_count = observation_count + 1,
            last_seen_at_utc = ?,
            consecutive_window_count = 0, trend_direction = 'recovering',
            recovery_window_count = ?, recovery_state = ?,
            cooldown_until_utc = COALESCE(?, cooldown_until_utc),
            resolved_at_utc = CASE WHEN ? = 'resolved' THEN ? ELSE resolved_at_utc END,
            source_feature_window_id = ?, health_assessment_id = ?,
            updated_at_utc = ? WHERE id = ?""",
            (
                new_state, window["window_end_utc"], int(state_changed),
                window["window_end_utc"], recovery_count,
                "resolved" if new_state == "resolved" else "recovering",
                cooldown_until, new_state, window["window_end_utc"],
                window["id"], bundle["health"]["id"], now, current["id"],
            ),
        )
        if state_changed:
            _insert_occurrence(
                connection, int(current["id"]), bundle, None, False,
                "resolved" if new_state == "resolved" else "recovering",
            )
            _transition(
                connection, int(current["id"]), window["window_end_utc"],
                current["state"], new_state, current["current_severity"],
                current["current_severity"],
                "resolved" if new_state == "resolved" else "recovery_started",
                "condition_absent_in_completed_window", window["id"],
                {"recovery_windows": recovery_count},
            )
        if new_state == "resolved":
            resolved += 1
        else:
            recovering += 1
    return recovering, resolved


def _apply_alert_plan(
    connection: sqlite3.Connection,
    bundle: dict[str, Any],
    candidates: dict[str, dict[str, Any]],
    signature: str,
    *,
    force: bool,
    command: str,
) -> Counter[str]:
    """Atomically persist one precomputed window without long analytical reads."""
    window = bundle["window"]
    local: Counter[str] = Counter()
    prior_run = connection.execute(
        """SELECT id FROM alert_evaluation_runs
        WHERE feature_window_id = ? AND algorithm_version = ?
        AND configuration_version = ? ORDER BY id DESC LIMIT 1""",
        (window["id"], ALGORITHM_VERSION, CONFIGURATION_VERSION),
    ).fetchone()
    # Recheck under the writer transaction. A retry or competing process may
    # have committed this exact window after the read/compute phase.
    if prior_run and not force:
        local["unchanged"] += 1
        return local
    started = _utc_now().isoformat()
    active_categories: set[str] = set()
    for candidate in candidates.values():
        result = _open_or_update(connection, bundle, candidate)
        local[result] += 1
        if result in {"created", "updated", "isolated", "cooldown"}:
            active_categories.add(candidate["category"])
    recovered, resolved = _recover_absent(connection, bundle, active_categories)
    local["recovered"] += recovered
    local["resolved"] += resolved
    finished = _utc_now().isoformat()
    if prior_run:
        connection.execute(
            """UPDATE alert_evaluation_runs SET
            started_at_utc = ?, finished_at_utc = ?, command = ?,
            force_requested = 1, status = 'completed',
            created_count = ?, updated_count = ?, recovered_count = ?,
            resolved_count = ?, skipped_count = ?,
            skip_reasons_json = '{}', error_code = NULL WHERE id = ?""",
            (
                started, finished, command, local["created"], local["updated"],
                local["recovered"], local["resolved"],
                local["isolated"] + local["cooldown"], prior_run["id"],
            ),
        )
    else:
        connection.execute(
            """INSERT INTO alert_evaluation_runs (
            feature_window_id, device_id, started_at_utc, finished_at_utc,
            command, force_requested, algorithm_version,
            configuration_version, catalogue_version, source_signature,
            status, created_count, updated_count, recovered_count,
            resolved_count, skipped_count, skip_reasons_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed',
            ?, ?, ?, ?, ?, '{}')""",
            (
                window["id"], window["device_id"], started, finished, command,
                int(force), ALGORITHM_VERSION, CONFIGURATION_VERSION,
                CATALOGUE_VERSION, signature, local["created"], local["updated"],
                local["recovered"], local["resolved"],
                local["isolated"] + local["cooldown"],
            ),
        )
    local["evaluated_windows"] += 1
    return local


def evaluate_database(
    database_path: Path | None = None,
    *,
    device_id: str | None = None,
    start: str | None = None,
    end: str | None = None,
    window_id: int | None = None,
    force: bool = False,
    command: str = "evaluate",
) -> dict[str, Any]:
    """Compute with read access, then atomically persist one window at a time."""
    path = initialize_database(database_path or get_database_path())
    where = ["1 = 1"]
    parameters: list[Any] = []
    if device_id:
        where.append("device_id = ?")
        parameters.append(device_id)
    if start:
        where.append("window_start_utc >= ?")
        parameters.append(start)
    if end:
        where.append("window_end_utc <= ?")
        parameters.append(end)
    if window_id:
        where.append("id = ?")
        parameters.append(window_id)
    if command == "agent" and not force:
        # Routine maintenance should never rescan half a million completed
        # run rows just to rediscover that their source windows are unchanged.
        where.append(
            """NOT EXISTS (
            SELECT 1 FROM alert_evaluation_runs run
            WHERE run.feature_window_id = feature_windows.id
              AND run.algorithm_version = ?
              AND run.configuration_version = ?
            )"""
        )
        parameters.extend((ALGORITHM_VERSION, CONFIGURATION_VERSION))
    counts: Counter[str] = Counter()
    skip_reasons: Counter[str] = Counter()
    plans: list[
        tuple[dict[str, Any], dict[str, dict[str, Any]], str]
    ] = []
    with database_connection(path) as connection:
        windows = connection.execute(
            f"""SELECT * FROM feature_windows WHERE {' AND '.join(where)}
            ORDER BY window_start_utc, id""",
            parameters,
        ).fetchall()
        for window in windows:
            bundle = _bundle_for_window(connection, window)
            eligible, reason = _eligible(bundle)
            if not eligible or bundle is None:
                skip_reasons[reason or "not_evaluated"] += 1
                continue
            prior_run = connection.execute(
                """SELECT id FROM alert_evaluation_runs
                WHERE feature_window_id = ? AND algorithm_version = ?
                AND configuration_version = ? ORDER BY id DESC LIMIT 1""",
                (window["id"], ALGORITHM_VERSION, CONFIGURATION_VERSION),
            ).fetchone()
            if prior_run and not force:
                counts["unchanged"] += 1
                continue
            plans.append(
                (bundle, derive_candidates(bundle), _source_signature(bundle))
            )
    if plans:
        def persist_plans(connection: sqlite3.Connection) -> Counter[str]:
            persisted: Counter[str] = Counter()
            for bundle, candidates, signature in plans:
                persisted.update(_apply_alert_plan(
                    connection,
                    bundle,
                    candidates,
                    signature,
                    force=force,
                    command=command,
                ))
            return persisted

        counts.update(run_write_transaction(
            path,
            persist_plans,
            priority="maintenance",
        ))
    return {
        "evaluated_windows": counts["evaluated_windows"],
        "created": counts["created"],
        "updated": counts["updated"],
        "recovering": counts["recovered"],
        "resolved": counts["resolved"],
        "isolated_protected": counts["isolated"],
        "cooldown_suppressed": counts["cooldown"],
        "unchanged": counts["unchanged"],
        "not_evaluated": sum(skip_reasons.values()),
        "skip_reasons": dict(skip_reasons),
        "algorithm_version": ALGORITHM_VERSION,
        "configuration_version": CONFIGURATION_VERSION,
        "catalogue_version": CATALOGUE_VERSION,
    }


def acknowledge_alert(
    database_path: Path | None,
    alert_id: int,
) -> dict[str, Any] | None:
    path = initialize_database(database_path or get_database_path())
    with database_connection(path) as connection:
        with connection:
            row = connection.execute(
                "SELECT * FROM alerts WHERE id = ?", (alert_id,)
            ).fetchone()
            if row is None:
                return None
            current = dict(row)
            if current["state"] == "resolved":
                return {"id": alert_id, "state": "resolved", "changed": False}
            if current["acknowledged_at_utc"]:
                return {
                    "id": alert_id, "state": current["state"], "changed": False
                }
            now = _utc_now().isoformat()
            new_state = (
                "recovering" if current["state"] == "recovering"
                else "acknowledged"
            )
            connection.execute(
                """UPDATE alerts SET state = ?, acknowledged_at_utc = ?,
                updated_at_utc = ? WHERE id = ?""",
                (new_state, now, now, alert_id),
            )
            _transition(
                connection, alert_id, now, current["state"], new_state,
                current["current_severity"], current["current_severity"],
                "acknowledged", "user_acknowledged", None,
            )
            return {"id": alert_id, "state": new_state, "changed": True}


def alert_status(
    database_path: Path | None = None,
    device_id: str | None = None,
) -> dict[str, Any]:
    path = initialize_database(database_path or get_database_path())
    with database_connection(path) as connection:
        resolved_device = device_id
        if resolved_device is None:
            row = connection.execute(
                "SELECT device_id FROM feature_windows ORDER BY window_end_utc DESC LIMIT 1"
            ).fetchone()
            resolved_device = row["device_id"] if row else None
        state_counts = {
            row["state"]: int(row["count"])
            for row in connection.execute(
                """SELECT state, COUNT(*) count FROM alerts
                WHERE (? IS NULL OR device_id = ?) GROUP BY state""",
                (resolved_device, resolved_device),
            )
        }
        severity_counts = {
            row["current_severity"]: int(row["count"])
            for row in connection.execute(
                """SELECT current_severity, COUNT(*) count FROM alerts
                WHERE state != 'resolved' AND (? IS NULL OR device_id = ?)
                GROUP BY current_severity""",
                (resolved_device, resolved_device),
            )
        }
        latest_run = connection.execute(
            """SELECT * FROM alert_evaluation_runs
            WHERE (? IS NULL OR device_id = ?)
            ORDER BY finished_at_utc DESC, id DESC LIMIT 1""",
            (resolved_device, resolved_device),
        ).fetchone()
    return {
        "status": "evaluated" if latest_run else "not_evaluated",
        "device_id": resolved_device,
        "open_alert_count": sum(
            state_counts.get(state, 0)
            for state in ("open", "acknowledged", "recovering")
        ),
        "state_counts": state_counts,
        "active_severity_counts": severity_counts,
        "latest_run": dict(latest_run) if latest_run else None,
        "algorithm_version": ALGORITHM_VERSION,
        "configuration_version": CONFIGURATION_VERSION,
        "catalogue_version": CATALOGUE_VERSION,
        "interpretation": INTERPRETATION,
    }


def maybe_evaluate_alerts(database_path: Path | None = None) -> None:
    result = evaluate_database(
        database_path or get_database_path(), command="agent"
    )
    # Evaluation is cheap when all source signatures are unchanged.
    _ = result


def _print_open(
    database_path: Path | None,
    severity: str | None,
    state: str | None,
) -> list[dict[str, Any]]:
    path = initialize_database(database_path or get_database_path())
    clauses = ["state != 'resolved'"]
    values: list[Any] = []
    if severity:
        clauses.append("current_severity = ?")
        values.append(severity)
    if state:
        clauses.append("state = ?")
        values.append(state)
    with database_connection(path) as connection:
        return [
            dict(row)
            for row in connection.execute(
                f"""SELECT * FROM alerts WHERE {' AND '.join(clauses)}
                ORDER BY latest_observed_utc DESC""",
                values,
            )
        ]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SmartOps local predictive alerts"
    )
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--status", action="store_true")
    actions.add_argument("--evaluate", action="store_true")
    actions.add_argument("--backfill", action="store_true")
    actions.add_argument("--list-open", action="store_true")
    parser.add_argument("--device")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--window-id", type=int)
    parser.add_argument(
        "--severity",
        choices=("informational", "advisory", "warning", "urgent"),
    )
    parser.add_argument(
        "--state", choices=("open", "acknowledged", "recovering", "resolved")
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.status:
        result: Any = alert_status(device_id=args.device)
    elif args.list_open:
        result = _print_open(None, args.severity, args.state)
    else:
        result = evaluate_database(
            device_id=args.device, start=args.start, end=args.end,
            window_id=args.window_id, force=args.force,
            command="backfill" if args.backfill else "evaluate",
        )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
