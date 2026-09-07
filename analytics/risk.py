"""Phase 3B deterministic risk-evidence fusion and candidate analysis.

Risk Evidence Index is an auditable evidence-strength score.  It is not a
failure probability, a confirmed failure, or proof of root cause.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agent.config import get_database_path
from analytics.baseline import baseline_status
from analytics.risk_catalogue import (
    ALGORITHM_VERSION,
    CATALOGUE_VERSION,
    COMPONENT_MAXIMUMS,
    CONFIGURATION_VERSION,
    CORE_QUALITY_METRICS,
    CORRELATION_GROUPS,
    CPU_EXPECTED_WORKLOADS,
    DEFAULT_POLICY,
    DOMAIN_RULES,
    EVIDENCE_LEVELS,
    EVENT_STRENGTH,
    GROUP_WEIGHTS,
    SEVERITY_MULTIPLIER,
    VALID_WORKLOADS,
    RiskPolicy,
)
from backend.database import database_connection, initialize_database


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def evidence_level_for_score(score: float) -> str:
    """Map an evidence score to its non-probabilistic display band."""
    for threshold, level in EVIDENCE_LEVELS:
        if score >= threshold:
            return level
    return "low"


def evaluation_eligibility(
    window: dict[str, Any],
    deviation: dict[str, Any] | None,
    baseline: dict[str, Any] | None,
    policy: RiskPolicy = DEFAULT_POLICY,
) -> tuple[bool, list[str], str]:
    """Return explicit prerequisites rather than manufacturing a score."""
    reasons: list[str] = []
    if not bool(window.get("is_complete")):
        reasons.append("incomplete_feature_window")
    coverage = _number(window.get("coverage_ratio"))
    if coverage is None or coverage < policy.minimum_coverage:
        reasons.append("coverage_below_minimum")
    workload = window.get("dominant_workload_class")
    if workload not in VALID_WORKLOADS:
        reasons.append("invalid_workload_context")
    confidence = _number(window.get("workload_confidence"))
    if confidence is None or confidence < policy.minimum_workload_confidence:
        reasons.append("workload_confidence_below_minimum")
    valid_core = sum(_number(window.get(name)) is not None for name in CORE_QUALITY_METRICS)
    if valid_core < policy.minimum_core_metrics:
        reasons.append("insufficient_core_metrics")
    if baseline is None or baseline.get("readiness_state") not in {
        "provisional",
        "established",
    }:
        reasons.append("baseline_not_ready")
    if deviation is None:
        reasons.append("deviation_assessment_missing")
    elif (
        deviation.get("deviation_index") is None
        or deviation.get("data_quality_status") != "sufficient"
        or deviation.get("overall_level") == "not_evaluated"
    ):
        reasons.append("deviation_not_evaluable")
    quality = "sufficient" if not reasons else "insufficient"
    return not reasons, reasons, quality


def _deviation_results(
    connection,
    deviation_id: int,
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            """SELECT feature_name, observed_value, deviation_direction,
            normalized_deviation_score, severity_band, reason_code
            FROM deviation_feature_results
            WHERE assessment_id = ?""",
            (deviation_id,),
        )
    ]


def _group_signals(
    results: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    by_name = {row["feature_name"]: row for row in results}
    signals: dict[str, dict[str, Any]] = {}
    for group, feature_names in CORRELATION_GROUPS.items():
        available = [
            by_name[name]
            for name in feature_names
            if name in by_name
            and _number(by_name[name].get("normalized_deviation_score")) is not None
        ]
        if not available:
            continue
        strongest = max(
            available,
            key=lambda row: float(row["normalized_deviation_score"]),
        )
        signals[group] = {
            "group": group,
            "score": float(strongest["normalized_deviation_score"]),
            "strongest_feature": strongest["feature_name"],
            "observed_value": strongest["observed_value"],
            "direction": strongest["deviation_direction"],
            "severity": strongest["severity_band"],
            "reason_code": strongest["reason_code"],
            "correlated_features": [row["feature_name"] for row in available],
        }
    return signals


def _previous_signals(
    connection,
    window: dict[str, Any],
    limit: int,
) -> list[tuple[dict[str, Any], dict[str, dict[str, Any]]]]:
    rows = connection.execute(
        """SELECT f.*, d.id AS deviation_id
        FROM feature_windows f
        JOIN deviation_assessments d ON d.feature_window_id = f.id
        WHERE f.device_id = ? AND f.window_start_utc < ?
        ORDER BY f.window_start_utc DESC, f.id DESC
        LIMIT ?""",
        (window["device_id"], window["window_start_utc"], limit),
    ).fetchall()
    return [
        (
            dict(row),
            _group_signals(_deviation_results(connection, int(row["deviation_id"]))),
        )
        for row in rows
    ]


def _temporal_evidence(
    connection,
    window: dict[str, Any],
    current_signals: dict[str, dict[str, Any]],
    policy: RiskPolicy,
) -> dict[str, Any]:
    elevated = {
        group for group, signal in current_signals.items()
        if signal["score"] >= 1.5 and group not in {"network", "activity"}
    }
    previous = _previous_signals(
        connection,
        window,
        max(policy.persistence_windows - 1, 0),
    )
    persistence = 1 if elevated else 0
    matching_groups = set(elevated)
    previous_start = datetime.fromisoformat(window["window_start_utc"])
    scores_newest_first = [
        max((current_signals[group]["score"] for group in elevated), default=0.0)
    ]
    first_observed = window["window_start_utc"]
    for prior_window, prior_signals in previous:
        prior_end = datetime.fromisoformat(prior_window["window_end_utc"])
        gap = (previous_start - prior_end).total_seconds()
        prior_elevated = {
            group for group, signal in prior_signals.items()
            if signal["score"] >= 1.5
        }
        overlap = matching_groups & prior_elevated
        if gap < 0 or gap > 300 or not overlap:
            break
        persistence += 1
        matching_groups = overlap
        previous_start = datetime.fromisoformat(prior_window["window_start_utc"])
        first_observed = prior_window["window_start_utc"]
        scores_newest_first.append(
            max(prior_signals[group]["score"] for group in overlap)
        )

    scores = list(reversed(scores_newest_first))
    pattern = (
        "not_applicable"
        if not elevated
        else "isolated_spike"
        if persistence == 1
        else "repeated_elevation"
        if persistence == 2
        else "sustained_elevation"
    )
    trend_contribution = 0.0
    if len(scores) >= 2 and scores[-1] >= scores[0] + 0.75:
        pattern = "increasing_trend"
        trend_contribution = COMPONENT_MAXIMUMS["trend"]
    elif len(scores) >= 2 and scores[-1] <= scores[0] - 0.75:
        pattern = "recovery_toward_baseline"
        trend_contribution = -COMPONENT_MAXIMUMS["trend"] / 2
    persistence_contribution = (
        0.0
        if persistence <= 1
        else 5.0
        if persistence == 2
        else 10.0
        if persistence < policy.persistence_windows
        else COMPONENT_MAXIMUMS["persistence"]
    )
    duration = max(
        0.0,
        (
            datetime.fromisoformat(window["window_end_utc"])
            - datetime.fromisoformat(first_observed)
        ).total_seconds(),
    )
    return {
        "pattern": pattern,
        "persistence_count": persistence,
        "persistence_duration_seconds": duration,
        "first_observed_utc": first_observed,
        "matching_groups": sorted(matching_groups),
        "scores": scores,
        "persistence_contribution": persistence_contribution,
        "trend_contribution": trend_contribution,
    }


def _correlated_events(
    connection,
    window: dict[str, Any],
    policy: RiskPolicy,
) -> list[dict[str, Any]]:
    start = datetime.fromisoformat(window["window_start_utc"])
    end = datetime.fromisoformat(window["window_end_utc"])
    margin = timedelta(minutes=policy.event_correlation_minutes)
    rows = connection.execute(
        """SELECT id, event_timestamp_utc, channel, provider_name, event_id,
        event_level, smartops_category, safe_summary
        FROM windows_events
        WHERE device_id = ? AND event_timestamp_utc >= ?
        AND event_timestamp_utc < ?
        ORDER BY event_timestamp_utc, id""",
        (
            window["device_id"],
            (start - margin).isoformat(),
            (end + margin).isoformat(),
        ),
    ).fetchall()
    events: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        timestamp = datetime.fromisoformat(item["event_timestamp_utc"])
        item["timing"] = (
            "before"
            if timestamp < start
            else "during"
            if timestamp < end
            else "after"
        )
        events.append(item)
    return events


def _event_contribution(events: list[dict[str, Any]]) -> tuple[float, list[str]]:
    strongest_by_category: dict[str, float] = {}
    counts = Counter(event["smartops_category"] for event in events)
    for event in events:
        strength = EVENT_STRENGTH.get(event["smartops_category"], 0.0)
        strength *= SEVERITY_MULTIPLIER.get(event["event_level"], 0.5)
        strongest_by_category[event["smartops_category"]] = max(
            strength,
            strongest_by_category.get(event["smartops_category"], 0.0),
        )
    strongest = max(strongest_by_category.values(), default=0.0)
    repeated_bonus = min(
        5.0,
        sum(max(0, count - 1) for count in counts.values()) * 2.0,
    )
    contribution = min(
        COMPONENT_MAXIMUMS["serious_events"],
        strongest + repeated_bonus,
    )
    return contribution, sorted(strongest_by_category)


def _statistical_components(
    signals: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    available = [
        (group, signal)
        for group, signal in signals.items()
        if group in GROUP_WEIGHTS
    ]
    denominator = sum(GROUP_WEIGHTS[group] for group, _ in available)
    components: list[dict[str, Any]] = []
    for group, signal in available:
        normalized = min(signal["score"], 5.0) / 5.0
        contribution = (
            normalized
            * GROUP_WEIGHTS[group]
            / denominator
            * COMPONENT_MAXIMUMS["statistical_deviation"]
            if denominator
            else 0.0
        )
        components.append({
            "component_name": "statistical_deviation",
            "correlation_group": group,
            "raw_value": signal["score"],
            "normalized_value": normalized,
            "weight": GROUP_WEIGHTS[group],
            "contribution": contribution,
            "evidence": signal,
            "reason_code": f"{group}_group_strongest_signal_only",
        })
    return components


def _candidate_evidence_row(
    kind: str,
    key: str,
    value: Any,
    supports: bool,
    reason: str,
) -> dict[str, Any]:
    return {
        "evidence_kind": kind,
        "evidence_key": key,
        "observed_value": value,
        "supports_candidate": supports,
        "reason_code": reason,
    }


def _build_candidates(
    window: dict[str, Any],
    signals: dict[str, dict[str, Any]],
    events: list[dict[str, Any]],
    temporal: dict[str, Any],
    isolation_result: str,
    event_contribution: float,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    workload = str(window["dominant_workload_class"])
    event_categories = {event["smartops_category"] for event in events}
    group_score = lambda name: signals.get(name, {}).get("score", 0.0)

    domain_inputs: list[tuple[str, list[str]]] = []
    if group_score("cpu") >= 1.5:
        domain_inputs.append(("cpu_pressure", ["cpu"]))
    if group_score("memory") >= 1.5:
        domain_inputs.append(("memory_pressure", ["memory"]))
        if temporal["pattern"] == "increasing_trend" and temporal["persistence_count"] >= 3:
            domain_inputs.append(("possible_memory_growth", ["memory"]))
    if group_score("swap") >= 1.5:
        domain_inputs.append(("swap_pressure", ["swap"]))
    if group_score("storage_capacity") >= 1.5:
        domain_inputs.append(("storage_capacity_pressure", ["storage_capacity"]))
    if group_score("storage_io") >= 1.5:
        domain_inputs.append(("storage_io_pressure", ["storage_io"]))
    if group_score("cpu_thermal") >= 1.5 or group_score("gpu_thermal") >= 1.5:
        domain_inputs.append(("thermal_stress", [
            group for group in ("cpu_thermal", "gpu_thermal")
            if group_score(group) >= 1.5
        ]))
    if "power" in event_categories:
        domain_inputs.append(("power_instability", []))
    if "hardware" in event_categories:
        domain_inputs.append(("hardware_error_evidence", []))
    if "application_crash" in event_categories:
        domain_inputs.append(("application_instability", []))
    if "service_failure" in event_categories:
        domain_inputs.append(("service_instability", []))
    if "resource_exhaustion" in event_categories:
        domain_inputs.append(("resource_exhaustion", []))
    idle_context = (
        (_number(window.get("idle_ratio")) or 0.0) >= 0.5
        or workload in {"idle", "background_activity"}
    )
    if group_score("cpu") >= 1.5 and idle_context:
        domain_inputs.append(("unusual_background_activity", ["cpu"]))
    if group_score("network") >= 2.5:
        domain_inputs.append(("network_activity_context", ["network"]))

    seen: set[str] = set()
    for domain, metric_groups in domain_inputs:
        if domain in seen:
            continue
        seen.add(domain)
        rule = DOMAIN_RULES[domain]
        evidence_rows: list[dict[str, Any]] = []
        metric_strength = 0.0
        for group in metric_groups:
            signal = signals[group]
            metric_strength = max(metric_strength, signal["score"] / 5.0)
            evidence_rows.append(_candidate_evidence_row(
                "metric",
                signal["strongest_feature"],
                signal,
                True,
                signal["reason_code"],
            ))
        matching_events = [
            event for event in events
            if event["smartops_category"] in rule.event_categories
        ]
        for event in matching_events:
            evidence_rows.append(_candidate_evidence_row(
                "event",
                f"{event['channel']}:{event['event_id']}:{event['id']}",
                {
                    "timestamp_utc": event["event_timestamp_utc"],
                    "timing": event["timing"],
                    "level": event["event_level"],
                    "category": event["smartops_category"],
                    "safe_summary": event["safe_summary"],
                },
                True,
                f"{event['smartops_category']}_event_{event['timing']}_window",
            ))
        context_expected = (
            domain == "cpu_pressure" and workload in CPU_EXPECTED_WORKLOADS
        ) or (
            domain == "storage_io_pressure" and workload in rule.workload_exceptions
        )
        if context_expected:
            evidence_rows.append(_candidate_evidence_row(
                "context",
                "workload_exception",
                {"workload": workload, "confidence": window["workload_confidence"]},
                False,
                f"{domain}_may_be_expected_for_{workload}",
            ))
        if isolation_result == "typical":
            evidence_rows.append(_candidate_evidence_row(
                "model",
                "isolation_forest",
                "typical",
                False,
                "multivariate_pattern_typical",
            ))
        event_strength = min(event_contribution / 25.0, 1.0) if matching_events else 0.0
        persistence_strength = min(temporal["persistence_count"] / 3.0, 1.0)
        confidence = (
            metric_strength * 0.55
            + event_strength * 0.25
            + persistence_strength * 0.20
        )
        if matching_events and not metric_groups:
            confidence = event_strength * 0.75 + persistence_strength * 0.10
        confidence *= rule.evidence_weight
        if context_expected:
            confidence *= 0.4
        confidence = max(0.05, min(confidence, 1.0))
        persistence_text = (
            f" for {temporal['persistence_count']} consecutive windows"
            if temporal["persistence_count"] > 1
            else ""
        )
        reasons = [
            row["reason_code"] for row in evidence_rows
            if row["supports_candidate"]
        ]
        if context_expected:
            reasons.append("workload_compatible_signal_reduced")
        candidates.append({
            "candidate_domain": domain,
            "evidence_confidence": confidence,
            "workload_context": workload,
            "first_observed_utc": temporal["first_observed_utc"],
            "persistence_duration_seconds": temporal[
                "persistence_duration_seconds"
            ],
            "reason_codes": sorted(set(reasons)),
            "explanation": rule.explanation_template.format(
                persistence=persistence_text
            ),
            "recommended_verification_steps": list(rule.verification_steps),
            "limitations": list(rule.limitations),
            "evidence": evidence_rows,
        })

    candidates.sort(
        key=lambda item: (-item["evidence_confidence"], item["candidate_domain"])
    )
    for rank, candidate in enumerate(candidates, start=1):
        candidate["rank"] = rank
    return candidates


def _build_assessment(
    connection,
    window: dict[str, Any],
    deviation: dict[str, Any],
    baseline: dict[str, Any],
    policy: RiskPolicy,
) -> dict[str, Any]:
    results = _deviation_results(connection, int(deviation["id"]))
    signals = _group_signals(results)
    temporal = _temporal_evidence(connection, window, signals, policy)
    events = _correlated_events(connection, window, policy)
    event_contribution, event_categories = _event_contribution(events)
    components = _statistical_components(signals)

    isolation_contribution = (
        COMPONENT_MAXIMUMS["isolation_forest"]
        if deviation["isolation_forest_result"] == "unusual"
        else 0.0
    )
    components.extend([
        {
            "component_name": "isolation_forest",
            "correlation_group": "",
            "raw_value": deviation["isolation_forest_score"],
            "normalized_value": 1.0 if isolation_contribution else 0.0,
            "weight": 1.0,
            "contribution": isolation_contribution,
            "evidence": {
                "result": deviation["isolation_forest_result"],
                "score": deviation["isolation_forest_score"],
            },
            "reason_code": (
                "isolation_pattern_unusual"
                if isolation_contribution
                else "isolation_not_unusual_or_not_ready"
            ),
        },
        {
            "component_name": "persistence",
            "correlation_group": "",
            "raw_value": temporal["persistence_count"],
            "normalized_value": min(temporal["persistence_count"] / policy.persistence_windows, 1.0),
            "weight": 1.0,
            "contribution": temporal["persistence_contribution"],
            "evidence": temporal,
            "reason_code": temporal["pattern"],
        },
        {
            "component_name": "trend",
            "correlation_group": "",
            "raw_value": temporal["scores"][-1] if temporal["scores"] else None,
            "normalized_value": None,
            "weight": 1.0,
            "contribution": temporal["trend_contribution"],
            "evidence": {
                "pattern": temporal["pattern"],
                "scores_oldest_to_newest": temporal["scores"],
            },
            "reason_code": temporal["pattern"],
        },
        {
            "component_name": "serious_events",
            "correlation_group": "",
            "raw_value": len(events),
            "normalized_value": event_contribution / 25.0,
            "weight": 1.0,
            "contribution": event_contribution,
            "evidence": {
                "categories": event_categories,
                "events": events,
            },
            "reason_code": (
                "correlated_operational_events"
                if events else "no_correlated_serious_events"
            ),
        },
    ])

    elevated_groups = [
        group for group, signal in signals.items()
        if signal["score"] >= 1.5 and group not in {"network", "activity"}
    ]
    cross_contribution = (
        10.0 if len(elevated_groups) >= 3
        else 5.0 if len(elevated_groups) >= 2
        else 0.0
    )
    components.append({
        "component_name": "cross_metric_corroboration",
        "correlation_group": "",
        "raw_value": len(elevated_groups),
        "normalized_value": min(len(elevated_groups) / 3.0, 1.0),
        "weight": 1.0,
        "contribution": cross_contribution,
        "evidence": {"elevated_correlation_groups": elevated_groups},
        "reason_code": (
            "multiple_independent_metric_groups"
            if cross_contribution else "no_cross_metric_corroboration"
        ),
    })

    workload = window["dominant_workload_class"]
    workload_contribution = 0.0
    workload_reasons: list[str] = []
    if signals.get("cpu", {}).get("score", 0.0) >= 1.5 and workload in CPU_EXPECTED_WORKLOADS:
        workload_contribution -= 10.0
        workload_reasons.append("high_cpu_compatible_with_workload")
    if signals.get("storage_io", {}).get("score", 0.0) >= 1.5 and workload == "development":
        workload_contribution -= 4.0
        workload_reasons.append("disk_io_compatible_with_development")
    workload_contribution = max(
        -COMPONENT_MAXIMUMS["workload_compatibility"],
        workload_contribution,
    )
    components.append({
        "component_name": "workload_compatibility",
        "correlation_group": "",
        "raw_value": window["workload_confidence"],
        "normalized_value": window["workload_confidence"],
        "weight": 1.0,
        "contribution": workload_contribution,
        "evidence": {
            "workload": workload,
            "confidence": window["workload_confidence"],
            "exceptions": workload_reasons,
        },
        "reason_code": (
            "workload_compatible_evidence_reduced"
            if workload_reasons else "no_workload_exception_applied"
        ),
    })

    coverage = float(window["coverage_ratio"])
    quality_penalty = -min(
        COMPONENT_MAXIMUMS["data_quality_penalty"],
        max(0.0, (1.0 - coverage) / 0.2 * 5.0),
    )
    components.append({
        "component_name": "data_quality_penalty",
        "correlation_group": "",
        "raw_value": coverage,
        "normalized_value": coverage,
        "weight": 1.0,
        "contribution": quality_penalty,
        "evidence": {
            "coverage_ratio": coverage,
            "missing_indicators": json.loads(
                window.get("missing_indicators_json") or "{}"
            ),
            "optional_sensors_do_not_reduce_score": True,
        },
        "reason_code": (
            "partial_coverage_penalty" if quality_penalty else "full_coverage"
        ),
    })

    raw_score = sum(float(component["contribution"]) for component in components)
    bounded_score = min(100.0, max(0.0, raw_score))
    if abs(raw_score - bounded_score) > 1e-9:
        components.append({
            "component_name": "score_boundary_adjustment",
            "correlation_group": "",
            "raw_value": raw_score,
            "normalized_value": bounded_score / 100.0,
            "weight": 1.0,
            "contribution": bounded_score - raw_score,
            "evidence": {"minimum": 0.0, "maximum": 100.0},
            "reason_code": "score_bounded_to_zero_to_one_hundred",
        })
    score = round(bounded_score, 4)
    candidates = _build_candidates(
        window,
        signals,
        events,
        temporal,
        deviation["isolation_forest_result"],
        event_contribution,
    )
    reason_codes = sorted({
        component["reason_code"]
        for component in components
        if abs(float(component["contribution"])) > 1e-9
    })
    return {
        "feature_window_id": window["id"],
        "deviation_assessment_id": deviation["id"],
        "baseline_id": baseline["id"],
        "baseline_version_id": deviation.get("baseline_version_id"),
        "baseline_rule_version": deviation.get("baseline_rule_version"),
        "workload_rule_version": window.get("workload_rule_version"),
        "device_id": window["device_id"],
        "window_start_utc": window["window_start_utc"],
        "window_end_utc": window["window_end_utc"],
        "workload_context": workload,
        "workload_confidence": window["workload_confidence"],
        "evaluated_at_utc": _utc_now().isoformat(),
        "algorithm_version": ALGORITHM_VERSION,
        "configuration_version": CONFIGURATION_VERSION,
        "catalogue_version": CATALOGUE_VERSION,
        "baseline_updated_at_utc": baseline["updated_at_utc"],
        "deviation_evaluated_at_utc": deviation["evaluation_timestamp_utc"],
        "risk_evidence_index": score,
        "evidence_level": evidence_level_for_score(score),
        "data_quality_status": "sufficient",
        "temporal_pattern": temporal["pattern"],
        "persistence_window_count": temporal["persistence_count"],
        "reason_codes": reason_codes,
        "components": components,
        "candidates": candidates,
    }


def _persist_assessment(connection, assessment: dict[str, Any]) -> int:
    columns = (
        "feature_window_id",
        "deviation_assessment_id",
        "baseline_id",
        "baseline_version_id",
        "baseline_rule_version",
        "workload_rule_version",
        "device_id",
        "window_start_utc",
        "window_end_utc",
        "workload_context",
        "workload_confidence",
        "evaluated_at_utc",
        "algorithm_version",
        "configuration_version",
        "catalogue_version",
        "baseline_updated_at_utc",
        "deviation_evaluated_at_utc",
        "risk_evidence_index",
        "evidence_level",
        "data_quality_status",
        "temporal_pattern",
        "persistence_window_count",
        "reason_codes_json",
    )
    values = [
        json.dumps(assessment["reason_codes"])
        if column == "reason_codes_json"
        else assessment[column]
        for column in columns
    ]
    updates = ", ".join(
        f"{column}=excluded.{column}"
        for column in columns
        if column not in {
            "feature_window_id",
            "algorithm_version",
            "configuration_version",
        }
    )
    connection.execute(
        f"""INSERT INTO risk_assessments ({", ".join(columns)})
        VALUES ({", ".join("?" for _ in columns)})
        ON CONFLICT(feature_window_id, algorithm_version, configuration_version)
        DO UPDATE SET {updates}""",
        values,
    )
    risk_id = int(connection.execute(
        """SELECT id FROM risk_assessments
        WHERE feature_window_id = ? AND algorithm_version = ?
        AND configuration_version = ?""",
        (
            assessment["feature_window_id"],
            assessment["algorithm_version"],
            assessment["configuration_version"],
        ),
    ).fetchone()[0])
    connection.execute(
        "DELETE FROM risk_evidence_components WHERE risk_assessment_id = ?",
        (risk_id,),
    )
    connection.execute(
        "DELETE FROM root_cause_candidates WHERE risk_assessment_id = ?",
        (risk_id,),
    )
    for component in assessment["components"]:
        connection.execute(
            """INSERT INTO risk_evidence_components (
                risk_assessment_id, component_name, correlation_group,
                raw_value, normalized_value, weight, contribution,
                evidence_json, reason_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                risk_id,
                component["component_name"],
                component["correlation_group"],
                component["raw_value"],
                component["normalized_value"],
                component["weight"],
                component["contribution"],
                json.dumps(component["evidence"]),
                component["reason_code"],
            ),
        )
    for candidate in assessment["candidates"]:
        cursor = connection.execute(
            """INSERT INTO root_cause_candidates (
                risk_assessment_id, candidate_domain, rank,
                evidence_confidence, workload_context, first_observed_utc,
                persistence_duration_seconds, reason_codes_json, explanation,
                recommended_verification_steps_json, limitations_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                risk_id,
                candidate["candidate_domain"],
                candidate["rank"],
                candidate["evidence_confidence"],
                candidate["workload_context"],
                candidate["first_observed_utc"],
                candidate["persistence_duration_seconds"],
                json.dumps(candidate["reason_codes"]),
                candidate["explanation"],
                json.dumps(candidate["recommended_verification_steps"]),
                json.dumps(candidate["limitations"]),
            ),
        )
        candidate_id = int(cursor.lastrowid)
        for evidence in candidate["evidence"]:
            connection.execute(
                """INSERT INTO root_cause_candidate_evidence (
                    candidate_id, evidence_kind, evidence_key,
                    observed_value_json, supports_candidate, reason_code
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    candidate_id,
                    evidence["evidence_kind"],
                    evidence["evidence_key"],
                    json.dumps(evidence["observed_value"]),
                    int(evidence["supports_candidate"]),
                    evidence["reason_code"],
                ),
            )
    return risk_id


def evaluate_database(
    database_path: Path | None = None,
    device_id: str | None = None,
    start: str | None = None,
    end: str | None = None,
    force: bool = False,
    command: str = "evaluate",
    policy: RiskPolicy = DEFAULT_POLICY,
) -> int:
    """Evaluate eligible Phase 3A results transactionally and idempotently."""
    path = initialize_database(database_path or get_database_path())
    started = _utc_now().isoformat()
    assessed = 0
    skipped: Counter[str] = Counter()
    failures: list[str] = []
    with database_connection(path) as connection:
        clauses = ["1 = 1"]
        parameters: list[Any] = []
        if device_id:
            clauses.append("f.device_id = ?")
            parameters.append(device_id)
        if start:
            clauses.append("f.window_start_utc >= ?")
            parameters.append(start)
        if end:
            clauses.append("f.window_start_utc <= ?")
            parameters.append(end)
        windows = [
            dict(row)
            for row in connection.execute(
                f"""SELECT f.* FROM feature_windows f
                WHERE {" AND ".join(clauses)}
                ORDER BY f.window_start_utc, f.id""",
                parameters,
            )
        ]
        for window in windows:
            deviation_row = connection.execute(
                "SELECT * FROM deviation_assessments WHERE feature_window_id = ?",
                (window["id"],),
            ).fetchone()
            deviation = dict(deviation_row) if deviation_row else None
            baseline_row = (
                connection.execute(
                    "SELECT * FROM baseline_profiles WHERE id = ?",
                    (deviation["baseline_id"],),
                ).fetchone()
                if deviation
                else None
            )
            baseline = dict(baseline_row) if baseline_row else None
            eligible, reasons, _ = evaluation_eligibility(
                window, deviation, baseline, policy
            )
            if not eligible:
                skipped.update(reasons)
                continue
            existing = connection.execute(
                """SELECT baseline_updated_at_utc, deviation_evaluated_at_utc
                FROM risk_assessments
                WHERE feature_window_id = ? AND algorithm_version = ?
                AND configuration_version = ?""",
                (window["id"], ALGORITHM_VERSION, CONFIGURATION_VERSION),
            ).fetchone()
            if (
                existing
                and not force
                and existing["baseline_updated_at_utc"] == baseline["updated_at_utc"]
                and existing["deviation_evaluated_at_utc"]
                == deviation["evaluation_timestamp_utc"]
            ):
                skipped["already_evaluated"] += 1
                continue
            try:
                assessment = _build_assessment(
                    connection, window, deviation, baseline, policy
                )
                with connection:
                    _persist_assessment(connection, assessment)
                assessed += 1
            except Exception as error:
                failures.append(type(error).__name__)
                skipped["evaluation_error"] += 1

        finished = _utc_now().isoformat()
        status = "error" if failures else "success"
        with connection:
            connection.execute(
                """INSERT INTO risk_evaluation_runs (
                    started_at_utc, finished_at_utc, command, device_id,
                    start_timestamp_utc, end_timestamp_utc, force_requested,
                    algorithm_version, configuration_version, catalogue_version,
                    status, assessed_count, skipped_count, skip_reasons_json,
                    error_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    started,
                    finished,
                    command,
                    device_id,
                    start,
                    end,
                    int(force),
                    ALGORITHM_VERSION,
                    CONFIGURATION_VERSION,
                    CATALOGUE_VERSION,
                    status,
                    assessed,
                    sum(skipped.values()),
                    json.dumps(dict(sorted(skipped.items()))),
                    ",".join(sorted(set(failures))) if failures else None,
                ),
            )
    return assessed


def risk_status(
    database_path: Path | None = None,
    device_id: str | None = None,
) -> dict[str, Any]:
    path = initialize_database(database_path or get_database_path())
    baseline = baseline_status(path, device_id=device_id)
    with database_connection(path) as connection:
        if device_id is None:
            row = connection.execute(
                """SELECT device_id FROM feature_windows
                ORDER BY window_start_utc DESC LIMIT 1"""
            ).fetchone()
            device_id = row[0] if row else None
        risk_count = int(connection.execute(
            """SELECT COUNT(*) FROM risk_assessments
            WHERE (? IS NULL OR device_id = ?)""",
            (device_id, device_id),
        ).fetchone()[0])
        deviation_count = int(connection.execute(
            """SELECT COUNT(*) FROM deviation_assessments
            WHERE (? IS NULL OR device_id = ?)""",
            (device_id, device_id),
        ).fetchone()[0])
        latest_run = connection.execute(
            """SELECT * FROM risk_evaluation_runs
            WHERE (? IS NULL OR device_id = ? OR device_id IS NULL)
            ORDER BY finished_at_utc DESC, id DESC LIMIT 1""",
            (device_id, device_id),
        ).fetchone()
        latest = connection.execute(
            """SELECT id, feature_window_id, risk_evidence_index,
            evidence_level, evaluated_at_utc FROM risk_assessments
            WHERE (? IS NULL OR device_id = ?)
            ORDER BY window_start_utc DESC, id DESC LIMIT 1""",
            (device_id, device_id),
        ).fetchone()
    reason = (
        None
        if latest
        else "insufficient_history"
        if baseline["state"] in {"collecting_data", "provisional", "unavailable"}
        else "missing_deviation_assessment"
        if deviation_count == 0
        else "no_eligible_feature_window"
    )
    return {
        "status": "evaluated" if latest else "not_evaluated",
        "reason_code": reason,
        "device_id": device_id,
        "baseline_state": baseline["state"],
        "deviation_assessment_count": deviation_count,
        "risk_assessment_count": risk_count,
        "latest_assessment": dict(latest) if latest else None,
        "algorithm_version": ALGORITHM_VERSION,
        "configuration_version": CONFIGURATION_VERSION,
        "catalogue_version": CATALOGUE_VERSION,
        "minimum_coverage": DEFAULT_POLICY.minimum_coverage,
        "minimum_workload_confidence": DEFAULT_POLICY.minimum_workload_confidence,
        "prerequisites": [
            "Completed five-minute feature window",
            "Coverage ratio of at least 80%",
            "Valid workload context and confidence",
            "Applicable provisional or established baseline",
            "Completed Phase 3A deviation assessment",
            "Sufficient core metric quality",
        ],
        "latest_run": dict(latest_run) if latest_run else None,
    }


def maybe_evaluate_risk(database_path: Path | None = None) -> None:
    """Agent hook: safely evaluates only new, fully eligible windows."""
    path = initialize_database(database_path or get_database_path())
    with database_connection(path) as connection:
        pending = connection.execute(
            """SELECT 1
            FROM deviation_assessments d
            JOIN feature_windows f ON f.id = d.feature_window_id
            JOIN baseline_profiles b ON b.id = d.baseline_id
            LEFT JOIN risk_assessments r
              ON r.feature_window_id = d.feature_window_id
             AND r.algorithm_version = ?
             AND r.configuration_version = ?
            WHERE r.id IS NULL
               OR r.baseline_updated_at_utc <> b.updated_at_utc
               OR r.deviation_evaluated_at_utc <> d.evaluation_timestamp_utc
            LIMIT 1""",
            (ALGORITHM_VERSION, CONFIGURATION_VERSION),
        ).fetchone()
    if pending:
        evaluate_database(path, command="agent")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SmartOps local risk-evidence and candidate analysis"
    )
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--evaluate", action="store_true")
    actions.add_argument("--status", action="store_true")
    actions.add_argument("--backfill", action="store_true")
    parser.add_argument("--device")
    parser.add_argument("--start", help="ISO-8601 feature-window start")
    parser.add_argument("--end", help="ISO-8601 feature-window end")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.status:
        print(json.dumps(risk_status(device_id=args.device), indent=2))
        return
    command = "backfill" if args.backfill else "evaluate"
    count = evaluate_database(
        device_id=args.device,
        start=args.start,
        end=args.end,
        force=args.force,
        command=command,
    )
    print(
        f"Evaluated {count} eligible five-minute windows for risk evidence. "
        "No score is produced when prerequisites are unavailable."
    )


if __name__ == "__main__":
    main()
