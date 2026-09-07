"""Phase 5B local incident feedback and predictive validation.

Only explicitly labelled outcomes and explicitly completed observation periods
are eligible. Missing feedback is never interpreted as a negative outcome.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from agent.config import get_database_path
from analytics.validation_config import (
    ALGORITHM_VERSION,
    CATEGORY_COMPATIBILITY,
    CONFIGURATION_VERSION,
    DEFAULT_POLICY,
    INTERPRETATION,
    MATCHING_VERSION,
    ValidationPolicy,
)
from backend.database import database_connection, initialize_database
from backend.phase5b_repository import (
    dumps,
    expand,
    list_incidents,
    list_unverified_alerts,
    list_validation_runs,
    upsert_alert_incident_link,
    utc_now,
)


VERIFIED = {"user_reported", "externally_verified"}


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _in_range(value: str, start: str | None, end: str | None) -> bool:
    return (start is None or value >= start) and (end is None or value <= end)


def _signature(connection: sqlite3.Connection, device: str | None) -> str:
    material: dict[str, Any] = {
        "algorithm": ALGORITHM_VERSION,
        "configuration": CONFIGURATION_VERSION,
        "matching": MATCHING_VERSION,
    }
    for table, columns in (
        (
            "alerts",
            "id, device_id, category, first_observed_utc, latest_observed_utc, "
            "workload_context, algorithm_version, configuration_version",
        ),
        (
            "alert_feedback",
            "id, alert_id, current_revision_number, outcome, "
            "observation_horizon_seconds, verification_status, status, updated_at_utc",
        ),
        (
            "incident_reports",
            "id, device_id, current_revision_number, category, severity, start_utc, "
            "verification_status, status, updated_at_utc",
        ),
        (
            "validation_observation_periods",
            "id, device_id, start_utc, end_utc, state, "
            "incident_reporting_complete, coverage_ratio, updated_at_utc",
        ),
        (
            "alert_incident_links",
            "id, alert_id, incident_id, match_type, origin, confirmed_by_user, "
            "matching_version, updated_at_utc",
        ),
    ):
        where = ""
        values: tuple[Any, ...] = ()
        if device and table in {
            "alerts",
            "incident_reports",
            "validation_observation_periods",
        }:
            where = " WHERE device_id = ?"
            values = (device,)
        material[table] = [
            tuple(row)
            for row in connection.execute(
                f"SELECT {columns} FROM {table}{where} ORDER BY id", values
            )
        ]
    encoded = json.dumps(material, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _automatic_matches(
    connection: sqlite3.Connection,
    *,
    device: str | None,
    start: str | None,
    end: str | None,
    policy: ValidationPolicy,
) -> int:
    alerts = [
        dict(row)
        for row in connection.execute(
            """SELECT id, device_id, category, evidence_domain,
            first_observed_utc, workload_context, consecutive_window_count
            FROM alerts WHERE (? IS NULL OR device_id = ?)""",
            (device, device),
        )
        if _in_range(row["first_observed_utc"], start, end)
    ]
    incidents = [
        dict(row)
        for row in connection.execute(
            """SELECT id, device_id, category, start_utc, workload_context,
            windows_event_ids_json, status
            FROM incident_reports WHERE status = 'active'
            AND (? IS NULL OR device_id = ?)""",
            (device, device),
        )
        if _in_range(row["start_utc"], start, end)
    ]
    created = 0
    for alert in alerts:
        alert_time = _parse(alert["first_observed_utc"])
        compatible = CATEGORY_COMPATIBILITY.get(alert["category"], set())
        for incident in incidents:
            if incident["device_id"] != alert["device_id"]:
                continue
            existing = connection.execute(
                """SELECT origin FROM alert_incident_links
                WHERE alert_id = ? AND incident_id = ? AND matching_version = ?""",
                (alert["id"], incident["id"], MATCHING_VERSION),
            ).fetchone()
            if existing and existing["origin"] == "manual":
                continue
            difference = (_parse(incident["start_utc"]) - alert_time).total_seconds()
            if not (
                -policy.matching_late_seconds
                <= difference
                <= policy.matching_lookback_seconds
            ):
                continue
            category_match = incident["category"] in compatible
            time_score = max(
                0.0,
                1.0
                - abs(difference)
                / max(policy.matching_lookback_seconds, policy.matching_late_seconds),
            )
            workload_match = bool(
                alert["workload_context"]
                and incident["workload_context"]
                and alert["workload_context"] == incident["workload_context"]
            )
            incident_event_ids = set(
                json.loads(incident["windows_event_ids_json"] or "[]")
            )
            alert_event_ids = {
                row["source_id"]
                for row in connection.execute(
                    """SELECT ae.source_id FROM alert_evidence ae
                    JOIN alert_occurrences occurrence
                    ON occurrence.id = ae.occurrence_id
                    WHERE occurrence.alert_id = ?
                    AND ae.source_table = 'windows_events'
                    AND ae.source_id IS NOT NULL""",
                    (alert["id"],),
                )
            }
            event_match = bool(incident_event_ids & alert_event_ids)
            persistence_support = int(alert["consecutive_window_count"] or 0) >= 2
            score = min(
                1.0,
                (0.45 if category_match else 0.0)
                + 0.30 * time_score
                + (0.10 if workload_match else 0.0)
                + (0.10 if event_match else 0.0)
                + (0.05 if persistence_support else 0.0),
            )
            if score >= policy.probable_match_score:
                match_type = "probable_match"
            elif score >= policy.possible_match_score:
                match_type = "possible_match"
            else:
                match_type = "rejected_match"
            upsert_alert_incident_link(
                connection,
                alert_id=alert["id"],
                incident_id=incident["id"],
                match_type=match_type,
                origin="automatic",
                confirmed_by_user=False,
                matching_score=round(score, 6),
                time_difference_seconds=difference,
                category_compatible=category_match,
                matching_rule="category_time_workload_v1",
                supporting_evidence=[
                    value
                    for value, present in (
                        ("compatible_category", category_match),
                        ("within_matching_horizon", True),
                        ("same_workload_context", workload_match),
                        ("linked_windows_event", event_match),
                        ("persistent_alert_evidence", persistence_support),
                    )
                    if present
                ],
                contradictory_evidence=(
                    [] if category_match else ["category_not_explicitly_compatible"]
                ),
                reason_codes=["automatic_candidate_not_confirmed"],
            )
            created += 1
    return created


def _metric(
    name: str,
    numerator: float | None,
    denominator: float,
    minimum: int,
    *,
    confidence: float,
    reconstruction: dict[str, Any],
) -> dict[str, Any]:
    enough = denominator >= minimum
    value = (
        round(float(numerator or 0) / denominator, 6)
        if enough and denominator > 0
        else None
    )
    return {
        "metric_name": name,
        "numerator": numerator,
        "denominator": denominator,
        "metric_value": value,
        "evaluation_state": (
            "evaluated" if value is not None else "insufficient_labeled_evidence"
        ),
        "data_confidence": round(confidence, 6),
        "minimum_requirement": minimum,
        "reason_codes": (
            []
            if value is not None
            else [f"minimum_{name}_evidence_not_met"]
        ),
        "reconstruction": reconstruction,
    }


def _insert_metric(
    connection: sqlite3.Connection,
    run_id: int,
    scope_type: str,
    scope_value: str,
    item: dict[str, Any],
) -> None:
    connection.execute(
        """INSERT INTO validation_metric_results (
        evaluation_run_id, scope_type, scope_value, metric_name,
        numerator, denominator, metric_value, evaluation_state,
        data_confidence, minimum_requirement, reason_codes_json,
        reconstruction_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run_id,
            scope_type,
            scope_value,
            item["metric_name"],
            item["numerator"],
            item["denominator"],
            item["metric_value"],
            item["evaluation_state"],
            item["data_confidence"],
            item["minimum_requirement"],
            dumps(item["reason_codes"]),
            dumps(item["reconstruction"]),
        ),
    )


def _eligible_periods(
    connection: sqlite3.Connection,
    device: str | None,
    start: str | None,
    end: str | None,
    policy: ValidationPolicy,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    included: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for raw in connection.execute(
        """SELECT * FROM validation_observation_periods
        WHERE (? IS NULL OR device_id = ?)""",
        (device, device),
    ):
        item = dict(raw)
        reasons: list[str] = []
        if item["state"] != "completed":
            reasons.append("observation_period_not_completed")
        if not item["incident_reporting_complete"]:
            reasons.append("incident_reporting_not_declared_complete")
        if item["end_utc"] is None:
            reasons.append("observation_period_has_no_end")
        if (item["coverage_ratio"] or 0) < policy.minimum_window_coverage:
            reasons.append("observation_period_coverage_below_threshold")
        if item["end_utc"] and (
            (start and item["end_utc"] < start)
            or (end and item["start_utc"] > end)
        ):
            reasons.append("outside_requested_time_range")
        item["_reasons"] = reasons
        (excluded if reasons else included).append(item)
    return included, excluded


def _record_evidence(
    connection: sqlite3.Connection,
    run_id: int,
    evidence_type: str,
    evidence_id: int,
    included: bool,
    classification: str | None,
    reasons: Iterable[str],
    details: dict[str, Any] | None = None,
) -> None:
    connection.execute(
        """INSERT INTO validation_inclusion_exclusion (
        evaluation_run_id, evidence_type, evidence_id, included,
        classification, reason_codes_json, details_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            run_id,
            evidence_type,
            evidence_id,
            int(included),
            classification,
            dumps(list(reasons)),
            dumps(details or {}),
        ),
    )


def evidence_signature(
    connection: sqlite3.Connection, device: str | None = None
) -> str:
    """Return the versioned signature of evidence visible to validation.

    The public wrapper lets read-only reporting tools detect that a stored
    evaluation predates newer labels without running a new evaluation.
    """
    return _signature(connection, device)


def _baseline_training_references(
    connection: sqlite3.Connection, feature_window_id: int
) -> list[dict[str, Any]]:
    """Identify baseline versions that learned from a feature window."""
    version_rows = connection.execute(
        """SELECT DISTINCT bv.id baseline_version_id, bv.version_number,
        bv.lifecycle_state, profile.legacy_baseline_id
        FROM baseline_version_training_windows training
        JOIN baseline_version_profiles profile
          ON profile.id = training.version_profile_id
        JOIN baseline_versions bv ON bv.id = profile.baseline_version_id
        WHERE training.feature_window_id = ?
        ORDER BY bv.version_number""",
        (feature_window_id,),
    )
    references = [dict(row) for row in version_rows]
    represented_legacy_ids = {
        int(row["legacy_baseline_id"])
        for row in references
        if row["legacy_baseline_id"] is not None
    }
    for row in connection.execute(
        """SELECT DISTINCT baseline_id legacy_baseline_id
        FROM baseline_training_windows WHERE feature_window_id = ?""",
        (feature_window_id,),
    ):
        legacy_id = int(row["legacy_baseline_id"])
        if legacy_id not in represented_legacy_ids:
            references.append(
                {
                    "baseline_version_id": None,
                    "version_number": None,
                    "lifecycle_state": "legacy_baseline_training",
                    "legacy_baseline_id": legacy_id,
                }
            )
    return references


def _window_evaluation_details(
    connection: sqlite3.Connection,
    window: sqlite3.Row,
    classification: str,
    *,
    relevant_alert_ids: list[int],
    relevant_incident_ids: list[int],
) -> dict[str, Any]:
    """Build the immutable inputs needed to audit one labelled window.

    Risk Evidence Index and evidence confidence are deliberately named as
    evidence values. Neither is a calibrated prediction probability.
    """
    labels = {
        "true_positive": (1, 1),
        "true_negative": (0, 0),
        "false_positive": (1, 0),
        "false_negative": (0, 1),
    }
    prediction, truth = labels[classification]
    risk = connection.execute(
        """SELECT risk_evidence_index, evidence_level, algorithm_version,
        configuration_version, baseline_id, baseline_version_id,
        baseline_rule_version, workload_rule_version, evaluated_at_utc
        FROM risk_assessments WHERE feature_window_id = ?
        ORDER BY evaluated_at_utc DESC, id DESC LIMIT 1""",
        (window["id"],),
    ).fetchone()
    alert_rows = list(
        connection.execute(
            f"""SELECT id, data_confidence, algorithm_version,
            configuration_version, baseline_id, baseline_version_id
            FROM alerts WHERE id IN ({','.join('?' for _ in relevant_alert_ids)})
            ORDER BY id""",
            relevant_alert_ids,
        )
    ) if relevant_alert_ids else []
    evidence_confidence = (
        max(float(row["data_confidence"]) for row in alert_rows)
        if alert_rows else None
    )
    baseline_id = risk["baseline_id"] if risk else None
    baseline_version_id = risk["baseline_version_id"] if risk else None
    if baseline_id is None and alert_rows:
        baseline_id = alert_rows[0]["baseline_id"]
    if baseline_version_id is None and alert_rows:
        baseline_version_id = alert_rows[0]["baseline_version_id"]
    return {
        "evaluation_unit": "completed_feature_window",
        "class_mapping": {"0": "normal_no_failure_risk", "1": "failure_risk"},
        "predicted_class": prediction,
        "true_class": truth,
        "classification": classification,
        "timestamp_utc": window["window_end_utc"],
        "window_start_utc": window["window_start_utc"],
        "window_end_utc": window["window_end_utc"],
        "workload_context": window["dominant_workload_class"] or "unknown",
        "workload_confidence": window["workload_confidence"],
        "workload_rule_version": window["workload_rule_version"],
        "coverage_ratio": window["coverage_ratio"],
        "source_sample_count": window["source_sample_count"] or window["sample_count"],
        "risk_evidence_index": risk["risk_evidence_index"] if risk else None,
        "risk_evidence_level": risk["evidence_level"] if risk else None,
        "prediction_confidence": None,
        "prediction_confidence_reason": "no_calibrated_prediction_probability",
        "evidence_confidence": evidence_confidence,
        "evidence_confidence_meaning": "evidence_quality_not_prediction_probability",
        "baseline_id": baseline_id,
        "baseline_version_id": baseline_version_id,
        "baseline_rule_version": risk["baseline_rule_version"] if risk else None,
        "prediction_rule_versions": [
            {
                "algorithm_version": row["algorithm_version"],
                "configuration_version": row["configuration_version"],
            }
            for row in alert_rows
        ],
        "validation_algorithm_version": ALGORITHM_VERSION,
        "validation_configuration_version": CONFIGURATION_VERSION,
        "matching_version": MATCHING_VERSION,
        "relevant_alert_ids": relevant_alert_ids,
        "relevant_incident_ids": relevant_incident_ids,
        "training_separation": "excluded_if_used_for_baseline_training",
    }


def evaluate_database(
    database_path: Path | None = None,
    *,
    device: str | None = None,
    start: str | None = None,
    end: str | None = None,
    force: bool = False,
    policy: ValidationPolicy = DEFAULT_POLICY,
) -> dict[str, Any]:
    """Evaluate labelled evidence transactionally and return a run summary."""
    path = initialize_database(database_path)
    started = utc_now()
    with database_connection(path) as connection:
        try:
            with connection:
                _automatic_matches(
                    connection,
                    device=device,
                    start=start,
                    end=end,
                    policy=policy,
                )
                signature = _signature(connection, device)
                existing = connection.execute(
                    """SELECT id FROM validation_evaluation_runs
                    WHERE device_id IS ? AND start_utc IS ? AND end_utc IS ?
                    AND evidence_signature = ? AND algorithm_version = ?
                    AND configuration_version = ? AND matching_version = ?""",
                    (
                        device,
                        start,
                        end,
                        signature,
                        ALGORITHM_VERSION,
                        CONFIGURATION_VERSION,
                        MATCHING_VERSION,
                    ),
                ).fetchone()
                if existing and not force:
                    return validation_run(connection, int(existing["id"])) or {}
                if existing and force:
                    connection.execute(
                        "DELETE FROM validation_evaluation_runs WHERE id = ?",
                        (existing["id"],),
                    )

                cursor = connection.execute(
                    """INSERT INTO validation_evaluation_runs (
                    device_id, start_utc, end_utc, started_at_utc, finished_at_utc,
                    status, evidence_signature, algorithm_version,
                    configuration_version, matching_version, reason_codes_json,
                    limitations_json, created_at_utc
                    ) VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, '[]', ?, ?)""",
                    (
                        device,
                        start,
                        end,
                        started,
                        started,
                        signature,
                        ALGORITHM_VERSION,
                        CONFIGURATION_VERSION,
                        MATCHING_VERSION,
                        dumps([INTERPRETATION]),
                        started,
                    ),
                )
                run_id = int(cursor.lastrowid)

                periods, excluded_periods = _eligible_periods(
                    connection, device, start, end, policy
                )
                for period in periods:
                    _record_evidence(
                        connection, run_id, "observation_period", period["id"], True,
                        "eligible_observation_period", [],
                    )
                for period in excluded_periods:
                    _record_evidence(
                        connection, run_id, "observation_period", period["id"], False,
                        None, period["_reasons"],
                    )

                incidents = [
                    dict(row)
                    for row in connection.execute(
                        """SELECT * FROM incident_reports
                        WHERE (? IS NULL OR device_id = ?)""",
                        (device, device),
                    )
                    if _in_range(row["start_utc"], start, end)
                ]
                incident_class: dict[int, str] = {}
                incident_scope: dict[int, dict[str, str]] = {}
                confirmed_links: dict[int, list[sqlite3.Row]] = defaultdict(list)
                for link in connection.execute(
                    """SELECT * FROM alert_incident_links
                    WHERE match_type = 'confirmed_match'
                    AND origin = 'manual' AND confirmed_by_user = 1
                    AND category_compatible = 1
                    AND matching_version = ?""",
                    (MATCHING_VERSION,),
                ):
                    confirmed_links[int(link["incident_id"])].append(link)

                for incident in incidents:
                    reasons: list[str] = []
                    if incident["status"] != "active":
                        reasons.append("incident_withdrawn")
                    if incident["verification_status"] not in VERIFIED:
                        reasons.append("incident_not_verified")
                    containing = [
                        period
                        for period in periods
                        if period["device_id"] == incident["device_id"]
                        and period["start_utc"] <= incident["start_utc"]
                        and period["end_utc"] >= incident["start_utc"]
                    ]
                    if not containing:
                        reasons.append("incident_outside_eligible_observation_period")
                    if reasons:
                        _record_evidence(
                            connection, run_id, "incident", incident["id"], False,
                            None, reasons,
                        )
                        continue
                    classification = (
                        "true_positive"
                        if confirmed_links.get(incident["id"])
                        else "false_negative"
                    )
                    incident_class[incident["id"]] = classification
                    incident_scope[incident["id"]] = {
                        "category": incident["category"],
                        "severity": incident["severity"],
                        "workload": incident["workload_context"] or "unknown",
                    }
                    _record_evidence(
                        connection, run_id, "incident", incident["id"], True,
                        classification, [],
                    )

                alerts = [
                    dict(row)
                    for row in connection.execute(
                        """SELECT a.*, f.id feedback_id, f.outcome feedback_outcome,
                        f.observation_horizon_seconds, f.verification_status
                        feedback_verification_status, f.preventive_action_taken,
                        f.status feedback_status,
                        f.data_confidence feedback_data_confidence
                        FROM alerts a LEFT JOIN alert_feedback f ON f.alert_id = a.id
                        WHERE (? IS NULL OR a.device_id = ?)""",
                        (device, device),
                    )
                    if _in_range(row["first_observed_utc"], start, end)
                ]
                alert_class: dict[int, str] = {}
                alert_scope: dict[int, dict[str, str]] = {}
                for alert in alerts:
                    reasons: list[str] = []
                    classification: str | None = None
                    if alert["feedback_id"] is None:
                        reasons.append("alert_feedback_unavailable")
                    elif alert["feedback_status"] != "active":
                        reasons.append("alert_feedback_withdrawn")
                    elif alert["feedback_verification_status"] not in VERIFIED:
                        reasons.append("alert_feedback_not_verified")
                    elif alert["feedback_outcome"] == "confirmed_related_issue":
                        link = connection.execute(
                            """SELECT id FROM alert_incident_links
                            WHERE alert_id = ? AND match_type = 'confirmed_match'
                            AND origin = 'manual' AND confirmed_by_user = 1
                            AND category_compatible = 1
                            AND matching_version = ? LIMIT 1""",
                            (alert["id"], MATCHING_VERSION),
                        ).fetchone()
                        if link:
                            classification = "true_positive"
                        else:
                            reasons.append("confirmed_outcome_requires_confirmed_incident_link")
                    elif alert["feedback_outcome"] == "no_issue_observed":
                        if alert["preventive_action_taken"]:
                            reasons.append("preventive_action_confounds_no_issue_outcome")
                        elif (
                            alert["observation_horizon_seconds"] is None
                            or alert["observation_horizon_seconds"]
                            < policy.minimum_no_issue_horizon_seconds
                        ):
                            reasons.append("no_issue_observation_horizon_too_short")
                        else:
                            classification = "false_positive"
                    elif alert["feedback_outcome"] == "preventive_action_taken":
                        reasons.append("preventive_action_outcome_not_validation_label")
                    elif alert["feedback_outcome"] == "likely_related_issue":
                        reasons.append("likely_related_outcome_not_confirmed")
                    elif alert["feedback_outcome"] == "incorrect_category":
                        reasons.append("incorrect_category_requires_manual_correction")
                    elif alert["feedback_outcome"] in {"withdrawn", "not_yet_verified"}:
                        reasons.append("alert_outcome_not_currently_verified")
                    else:
                        reasons.append("uncertain_alert_outcome")
                    if classification:
                        alert_class[alert["id"]] = classification
                        alert_scope[alert["id"]] = {
                            "category": alert["category"],
                            "severity": alert["current_severity"],
                            "workload": alert["workload_context"] or "unknown",
                            "algorithm_configuration": (
                                f"{alert['algorithm_version']}|"
                                f"{alert['configuration_version']}"
                            ),
                        }
                        _record_evidence(
                            connection, run_id, "alert", alert["id"], True,
                            classification, [],
                        )
                    else:
                        _record_evidence(
                            connection, run_id, "alert", alert["id"], False,
                            None, reasons,
                        )

                window_classes: dict[int, str] = {}
                window_scopes: dict[int, str] = {}
                for period in periods:
                    for window in connection.execute(
                        """SELECT id, window_start_utc, window_end_utc,
                        dominant_workload_class, workload_confidence,
                        workload_rule_version, sample_count,
                        source_sample_count, is_complete, coverage_ratio
                        FROM feature_windows WHERE device_id = ?
                        AND window_start_utc >= ? AND window_end_utc <= ?""",
                        (period["device_id"], period["start_utc"], period["end_utc"]),
                    ):
                        if window["id"] in window_classes:
                            continue
                        if (
                            not window["is_complete"]
                            or window["coverage_ratio"] < policy.minimum_window_coverage
                        ):
                            _record_evidence(
                                connection, run_id, "feature_window", window["id"],
                                False, None, ["feature_window_inadequate"],
                            )
                            continue
                        training_references = _baseline_training_references(
                            connection, int(window["id"])
                        )
                        if training_references:
                            _record_evidence(
                                connection,
                                run_id,
                                "feature_window",
                                window["id"],
                                False,
                                None,
                                ["feature_window_used_for_baseline_training"],
                                {"baseline_training_references": training_references},
                            )
                            continue
                        incident_labels = [
                            value
                            for incident_id, value in incident_class.items()
                            if next(
                                item["start_utc"]
                                for item in incidents
                                if item["id"] == incident_id
                            ) >= window["window_start_utc"]
                            and next(
                                item["start_utc"]
                                for item in incidents
                                if item["id"] == incident_id
                            ) < window["window_end_utc"]
                        ]
                        fp_alert = any(
                            value == "false_positive"
                            and next(
                                item["first_observed_utc"]
                                for item in alerts
                                if item["id"] == alert_id
                            ) >= window["window_start_utc"]
                            and next(
                                item["first_observed_utc"]
                                for item in alerts
                                if item["id"] == alert_id
                            ) < window["window_end_utc"]
                            for alert_id, value in alert_class.items()
                        )
                        relevant_incident_ids = [
                            incident_id
                            for incident_id in incident_class
                            if next(
                                item["start_utc"]
                                for item in incidents
                                if item["id"] == incident_id
                            ) >= window["window_start_utc"]
                            and next(
                                item["start_utc"]
                                for item in incidents
                                if item["id"] == incident_id
                            ) < window["window_end_utc"]
                        ]
                        relevant_alert_ids = [
                            alert_id
                            for alert_id in alert_class
                            if next(
                                item["first_observed_utc"]
                                for item in alerts
                                if item["id"] == alert_id
                            ) >= window["window_start_utc"]
                            and next(
                                item["first_observed_utc"]
                                for item in alerts
                                if item["id"] == alert_id
                            ) < window["window_end_utc"]
                        ]
                        for incident_id in relevant_incident_ids:
                            relevant_alert_ids.extend(
                                int(link["alert_id"])
                                for link in confirmed_links.get(incident_id, [])
                            )
                        relevant_alert_ids = sorted(set(relevant_alert_ids))
                        if "true_positive" in incident_labels:
                            classification = "true_positive"
                        elif "false_negative" in incident_labels:
                            classification = "false_negative"
                        elif fp_alert:
                            classification = "false_positive"
                        else:
                            classification = "true_negative"
                        window_classes[window["id"]] = classification
                        window_scopes[window["id"]] = (
                            window["dominant_workload_class"] or "unknown"
                        )
                        _record_evidence(
                            connection, run_id, "feature_window", window["id"], True,
                            classification, [],
                            _window_evaluation_details(
                                connection,
                                window,
                                classification,
                                relevant_alert_ids=relevant_alert_ids,
                                relevant_incident_ids=relevant_incident_ids,
                            ),
                        )

                lead_times: list[float] = []
                for incident_id, links in confirmed_links.items():
                    if incident_id not in incident_class:
                        continue
                    incident = next(item for item in incidents if item["id"] == incident_id)
                    for link in links:
                        alert = connection.execute(
                            "SELECT first_observed_utc FROM alerts WHERE id = ?",
                            (link["alert_id"],),
                        ).fetchone()
                        if alert is None:
                            continue
                        lead = (
                            _parse(incident["start_utc"])
                            - _parse(alert["first_observed_utc"])
                        ).total_seconds()
                        lead_times.append(lead)
                        connection.execute(
                            """INSERT INTO warning_lead_time_results (
                            evaluation_run_id, alert_incident_link_id, alert_id,
                            incident_id, alert_first_observed_utc, incident_start_utc,
                            lead_time_seconds, timing_state, eligible,
                            reason_codes_json
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, '[]')""",
                            (
                                run_id,
                                link["id"],
                                link["alert_id"],
                                incident_id,
                                alert["first_observed_utc"],
                                incident["start_utc"],
                                lead,
                                "advance_warning" if lead >= 0 else "late_detection",
                            ),
                        )

                alert_counts = Counter(alert_class.values())
                incident_counts = Counter(incident_class.values())
                window_counts = Counter(window_classes.values())
                distinct_days = len(
                    {
                        _parse(
                            next(
                                row["window_start_utc"]
                                for row in connection.execute(
                                    "SELECT window_start_utc FROM feature_windows WHERE id = ?",
                                    (window_id,),
                                )
                            )
                        ).date().isoformat()
                        for window_id in window_classes
                    }
                )
                observation_seconds = sum(
                    max(
                        0.0,
                        (_parse(period["end_utc"]) - _parse(period["start_utc"]))
                        .total_seconds(),
                    )
                    for period in periods
                    if period["end_utc"]
                )
                eligible_total = (
                    len(alert_class) + len(incident_class) + len(window_classes)
                )
                label_confidences = [
                    float(item["data_confidence"])
                    * (0.9 if item["timestamp_precision"] == "approximate" else 1)
                    * (
                        1
                        if item["verification_status"] == "externally_verified"
                        else 0.85
                    )
                    for item in incidents
                    if item["id"] in incident_class
                ] + [
                    float(item["feedback_data_confidence"])
                    * (
                        1
                        if item["feedback_verification_status"]
                        == "externally_verified"
                        else 0.85
                    )
                    for item in alerts
                    if item["id"] in alert_class
                ]
                mean_label_confidence = (
                    sum(label_confidences) / len(label_confidences)
                    if label_confidences
                    else 0.0
                )
                mean_period_coverage = (
                    sum(float(item["coverage_ratio"]) for item in periods)
                    / len(periods)
                    if periods
                    else 0.0
                )
                confidence = min(
                    1.0,
                    0.35 * mean_label_confidence
                    + 0.30 * mean_period_coverage
                    + 0.20 * min(1.0, eligible_total / 100)
                    + 0.15
                    * min(
                        1.0,
                        distinct_days / policy.minimum_accuracy_distinct_days,
                    ),
                ) if eligible_total else 0.0
                confidence_level = (
                    "high"
                    if confidence >= 0.8
                    else "moderate"
                    if confidence >= 0.5
                    else "limited"
                    if confidence > 0
                    else "insufficient"
                )
                metrics = [
                    _metric(
                        "precision",
                        alert_counts["true_positive"],
                        alert_counts["true_positive"] + alert_counts["false_positive"],
                        policy.minimum_precision_alerts,
                        confidence=confidence,
                        reconstruction=dict(alert_counts),
                    ),
                    _metric(
                        "recall",
                        incident_counts["true_positive"],
                        incident_counts["true_positive"]
                        + incident_counts["false_negative"],
                        policy.minimum_recall_incidents,
                        confidence=confidence,
                        reconstruction=dict(incident_counts),
                    ),
                ]
                accuracy_denominator = sum(window_counts.values())
                accuracy_ready = (
                    accuracy_denominator >= policy.minimum_accuracy_windows
                    and observation_seconds
                    >= policy.minimum_accuracy_period_seconds
                    and distinct_days >= policy.minimum_accuracy_distinct_days
                )
                metrics.append(
                    {
                        "metric_name": "accuracy",
                        "numerator": (
                            window_counts["true_positive"]
                            + window_counts["true_negative"]
                        ),
                        "denominator": accuracy_denominator,
                        "metric_value": (
                            round(
                                (
                                    window_counts["true_positive"]
                                    + window_counts["true_negative"]
                                )
                                / accuracy_denominator,
                                6,
                            )
                            if accuracy_ready and accuracy_denominator
                            else None
                        ),
                        "evaluation_state": (
                            "evaluated"
                            if accuracy_ready and accuracy_denominator
                            else "insufficient_labeled_evidence"
                        ),
                        "data_confidence": round(confidence, 6),
                        "minimum_requirement": policy.minimum_accuracy_windows,
                        "reason_codes": (
                            []
                            if accuracy_ready and accuracy_denominator
                            else [
                                value
                                for value, missing in (
                                    (
                                        "minimum_accuracy_windows_not_met",
                                        accuracy_denominator
                                        < policy.minimum_accuracy_windows,
                                    ),
                                    (
                                        "minimum_observation_duration_not_met",
                                        observation_seconds
                                        < policy.minimum_accuracy_period_seconds,
                                    ),
                                    (
                                        "minimum_distinct_observation_days_not_met",
                                        distinct_days
                                        < policy.minimum_accuracy_distinct_days,
                                    ),
                                )
                                if missing
                            ]
                        ),
                        "reconstruction": {
                            **dict(window_counts),
                            "observation_seconds": observation_seconds,
                            "minimum_observation_seconds": (
                                policy.minimum_accuracy_period_seconds
                            ),
                            "distinct_observation_days": distinct_days,
                            "minimum_distinct_observation_days": (
                                policy.minimum_accuracy_distinct_days
                            ),
                        },
                    }
                )
                sensitivity_den = (
                    window_counts["true_positive"] + window_counts["false_negative"]
                )
                specificity_den = (
                    window_counts["true_negative"] + window_counts["false_positive"]
                )
                balanced_ready = (
                    accuracy_ready
                    and sensitivity_den > 0
                    and specificity_den > 0
                )
                metrics.append(
                    {
                        "metric_name": "balanced_accuracy",
                        "numerator": None,
                        "denominator": sum(window_counts.values()),
                        "metric_value": (
                            round(
                                (
                                    window_counts["true_positive"] / sensitivity_den
                                    + window_counts["true_negative"] / specificity_den
                                )
                                / 2,
                                6,
                            )
                            if balanced_ready
                            else None
                        ),
                        "evaluation_state": (
                            "evaluated"
                            if balanced_ready
                            else "insufficient_labeled_evidence"
                        ),
                        "data_confidence": round(confidence, 6),
                        "minimum_requirement": policy.minimum_accuracy_windows,
                        "reason_codes": (
                            []
                            if balanced_ready
                            else ["balanced_accuracy_requires_both_outcome_classes"]
                        ),
                        "reconstruction": {
                            "sensitivity_denominator": sensitivity_den,
                            "specificity_denominator": specificity_den,
                            **dict(window_counts),
                        },
                    }
                )
                false_alert_denominator = (
                    alert_counts["true_positive"] + alert_counts["false_positive"]
                )
                metrics.append(
                    _metric(
                        "false_alert_proportion",
                        alert_counts["false_positive"],
                        false_alert_denominator,
                        policy.minimum_precision_alerts,
                        confidence=confidence,
                        reconstruction=dict(alert_counts),
                    )
                )
                mean_lead = (
                    sum(lead_times) / len(lead_times)
                    if len(lead_times) >= policy.minimum_lead_time_matches
                    else None
                )
                metrics.append(
                    {
                        "metric_name": "mean_warning_lead_time_seconds",
                        "numerator": sum(lead_times) if lead_times else None,
                        "denominator": len(lead_times),
                        "metric_value": round(mean_lead, 6) if mean_lead is not None else None,
                        "evaluation_state": (
                            "evaluated"
                            if mean_lead is not None
                            else "insufficient_labeled_evidence"
                        ),
                        "data_confidence": round(confidence, 6),
                        "minimum_requirement": policy.minimum_lead_time_matches,
                        "reason_codes": (
                            []
                            if mean_lead is not None
                            else ["minimum_matched_incidents_not_met"]
                        ),
                        "reconstruction": {
                            "lead_times_seconds": lead_times,
                            "late_detection_count": sum(value < 0 for value in lead_times),
                        },
                    }
                )
                sorted_leads = sorted(lead_times)
                lead_ready = len(sorted_leads) >= policy.minimum_lead_time_matches
                median_lead = (
                    (
                        sorted_leads[len(sorted_leads) // 2]
                        if len(sorted_leads) % 2
                        else (
                            sorted_leads[len(sorted_leads) // 2 - 1]
                            + sorted_leads[len(sorted_leads) // 2]
                        )
                        / 2
                    )
                    if lead_ready
                    else None
                )
                for name, value in (
                    ("median_warning_lead_time_seconds", median_lead),
                    ("minimum_warning_lead_time_seconds", min(sorted_leads) if lead_ready else None),
                    ("maximum_warning_lead_time_seconds", max(sorted_leads) if lead_ready else None),
                ):
                    metrics.append(
                        {
                            "metric_name": name,
                            "numerator": value,
                            "denominator": len(sorted_leads),
                            "metric_value": value,
                            "evaluation_state": (
                                "evaluated"
                                if value is not None
                                else "insufficient_labeled_evidence"
                            ),
                            "data_confidence": round(confidence, 6),
                            "minimum_requirement": policy.minimum_lead_time_matches,
                            "reason_codes": (
                                []
                                if value is not None
                                else ["minimum_matched_incidents_not_met"]
                            ),
                            "reconstruction": {
                                "lead_times_seconds": sorted_leads,
                                "method": name.removesuffix("_warning_lead_time_seconds"),
                            },
                        }
                    )

                def count_metric(name: str, value: int) -> dict[str, Any]:
                    return {
                        "metric_name": name,
                        "numerator": value,
                        "denominator": 1,
                        "metric_value": float(value),
                        "evaluation_state": "evaluated",
                        "data_confidence": round(confidence, 6),
                        "minimum_requirement": 0,
                        "reason_codes": [],
                        "reconstruction": {"count": value},
                    }

                for name, value in (
                    ("verified_alert_count", len(alert_class)),
                    ("true_positive_alert_count", alert_counts["true_positive"]),
                    ("false_positive_alert_count", alert_counts["false_positive"]),
                    ("confirmed_incident_count", len(incident_class)),
                    ("detected_incident_count", incident_counts["true_positive"]),
                    ("missed_incident_count", incident_counts["false_negative"]),
                    ("true_positive_window_count", window_counts["true_positive"]),
                    ("false_positive_window_count", window_counts["false_positive"]),
                    ("false_negative_window_count", window_counts["false_negative"]),
                    ("true_negative_window_count", window_counts["true_negative"]),
                    ("late_detection_count", sum(value < 0 for value in lead_times)),
                    ("no_warning_count", incident_counts["false_negative"]),
                ):
                    metrics.append(count_metric(name, int(value)))

                feedback_total = len(alerts)
                metrics.append(
                    {
                        "metric_name": "feedback_completion_rate",
                        "numerator": len(alert_class),
                        "denominator": feedback_total,
                        "metric_value": (
                            round(len(alert_class) / feedback_total, 6)
                            if feedback_total
                            else None
                        ),
                        "evaluation_state": (
                            "evaluated"
                            if feedback_total
                            else "insufficient_labeled_evidence"
                        ),
                        "data_confidence": round(confidence, 6),
                        "minimum_requirement": 1,
                        "reason_codes": (
                            [] if feedback_total else ["no_alerts_in_requested_scope"]
                        ),
                        "reconstruction": {
                            "eligible_feedback": len(alert_class),
                            "alerts_in_scope": feedback_total,
                        },
                    }
                )
                expected_period_windows = sum(
                    int(item["expected_window_count"] or 0) for item in periods
                )
                eligible_period_windows = sum(
                    int(item["eligible_window_count"] or 0) for item in periods
                )
                metrics.append(
                    {
                        "metric_name": "observation_coverage",
                        "numerator": eligible_period_windows,
                        "denominator": expected_period_windows,
                        "metric_value": (
                            round(eligible_period_windows / expected_period_windows, 6)
                            if expected_period_windows
                            else None
                        ),
                        "evaluation_state": (
                            "evaluated"
                            if expected_period_windows
                            else "insufficient_labeled_evidence"
                        ),
                        "data_confidence": round(confidence, 6),
                        "minimum_requirement": 1,
                        "reason_codes": (
                            [] if expected_period_windows
                            else ["no_eligible_observation_period"]
                        ),
                        "reconstruction": {
                            "eligible_windows": eligible_period_windows,
                            "expected_windows": expected_period_windows,
                        },
                    }
                )
                for metric in metrics:
                    _insert_metric(connection, run_id, "overall", "all", metric)

                # Store transparent stratified results even when their sample
                # sizes are too small for publication.
                for scope_name in (
                    "category",
                    "severity",
                    "workload",
                    "algorithm_configuration",
                ):
                    alert_groups: dict[str, Counter[str]] = defaultdict(Counter)
                    incident_groups: dict[str, Counter[str]] = defaultdict(Counter)
                    for identifier, classification in alert_class.items():
                        alert_groups[alert_scope[identifier][scope_name]][classification] += 1
                    if scope_name != "algorithm_configuration":
                        for identifier, classification in incident_class.items():
                            incident_groups[
                                incident_scope[identifier][scope_name]
                            ][classification] += 1
                    for value, counts in alert_groups.items():
                        _insert_metric(
                            connection,
                            run_id,
                            scope_name,
                            value,
                            _metric(
                                "precision",
                                counts["true_positive"],
                                counts["true_positive"] + counts["false_positive"],
                                policy.minimum_precision_alerts,
                                confidence=confidence,
                                reconstruction=dict(counts),
                            ),
                        )
                    for value, counts in incident_groups.items():
                        _insert_metric(
                            connection,
                            run_id,
                            scope_name,
                            value,
                            _metric(
                                "recall",
                                counts["true_positive"],
                                counts["true_positive"] + counts["false_negative"],
                                policy.minimum_recall_incidents,
                                confidence=confidence,
                                reconstruction=dict(counts),
                            ),
                        )

                excluded_count = int(
                    connection.execute(
                        """SELECT COUNT(*) FROM validation_inclusion_exclusion
                        WHERE evaluation_run_id = ? AND included = 0""",
                        (run_id,),
                    ).fetchone()[0]
                )
                finished = utc_now()
                reason_codes = (
                    ["insufficient_labeled_evidence"]
                    if not any(
                        item["metric_value"] is not None
                        for item in metrics
                        if item["metric_name"] in {
                            "precision",
                            "recall",
                            "accuracy",
                            "balanced_accuracy",
                            "mean_warning_lead_time_seconds",
                        }
                    )
                    else []
                )
                connection.execute(
                    """UPDATE validation_evaluation_runs SET finished_at_utc = ?,
                    status = ?, eligible_alert_count = ?,
                    eligible_incident_count = ?, eligible_window_count = ?,
                    matched_count = ?, excluded_count = ?,
                    distinct_observation_days = ?, data_confidence = ?,
                    confidence_level = ?, reason_codes_json = ?
                    WHERE id = ?""",
                    (
                        finished,
                        (
                            "evaluated"
                            if not reason_codes
                            else "insufficient_labeled_evidence"
                        ),
                        len(alert_class),
                        len(incident_class),
                        len(window_classes),
                        len(lead_times),
                        excluded_count,
                        distinct_days,
                        confidence,
                        confidence_level,
                        dumps(reason_codes),
                        run_id,
                    ),
                )
                return validation_run(connection, run_id) or {}
        except Exception:
            # The surrounding transaction rolls back every partial matching,
            # run, classification and metric write.
            raise


def validation_run(
    connection: sqlite3.Connection, run_id: int
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM validation_evaluation_runs WHERE id = ?", (run_id,)
    ).fetchone()
    item = expand(row)
    if item is None:
        return None
    item["metrics"] = [
        expand(metric) or {}
        for metric in connection.execute(
            """SELECT * FROM validation_metric_results
            WHERE evaluation_run_id = ?
            ORDER BY scope_type, scope_value, metric_name""",
            (run_id,),
        )
    ]
    item["lead_times"] = [
        expand(value) or {}
        for value in connection.execute(
            """SELECT * FROM warning_lead_time_results
            WHERE evaluation_run_id = ? ORDER BY lead_time_seconds DESC""",
            (run_id,),
        )
    ]
    item["evidence_decisions"] = [
        expand(value) or {}
        for value in connection.execute(
            """SELECT * FROM validation_inclusion_exclusion
            WHERE evaluation_run_id = ?
            ORDER BY included, evidence_type, evidence_id""",
            (run_id,),
        )
    ]
    return item


def validation_status(
    database_path: Path | None = None,
    *,
    device: str | None = None,
) -> dict[str, Any]:
    path = initialize_database(database_path)
    with database_connection(path) as connection:
        run = connection.execute(
            """SELECT id FROM validation_evaluation_runs
            WHERE (? IS NULL OR device_id = ?)
            ORDER BY finished_at_utc DESC, id DESC LIMIT 1""",
            (device, device),
        ).fetchone()
        counts = {
            "incident_count": int(
                connection.execute(
                    """SELECT COUNT(*) FROM incident_reports
                    WHERE status = 'active' AND (? IS NULL OR device_id = ?)""",
                    (device, device),
                ).fetchone()[0]
            ),
            "verified_incident_count": int(
                connection.execute(
                    """SELECT COUNT(*) FROM incident_reports
                    WHERE status = 'active'
                    AND verification_status IN ('user_reported', 'externally_verified')
                    AND (? IS NULL OR device_id = ?)""",
                    (device, device),
                ).fetchone()[0]
            ),
            "feedback_count": int(
                connection.execute(
                    """SELECT COUNT(*) FROM alert_feedback f JOIN alerts a
                    ON a.id = f.alert_id WHERE f.status = 'active'
                    AND (? IS NULL OR a.device_id = ?)""",
                    (device, device),
                ).fetchone()[0]
            ),
            "unverified_alert_count": len(
                list_unverified_alerts(connection, device_id=device, limit=5000)
            ),
            "completed_observation_period_count": int(
                connection.execute(
                    """SELECT COUNT(*) FROM validation_observation_periods
                    WHERE state = 'completed'
                    AND (? IS NULL OR device_id = ?)""",
                    (device, device),
                ).fetchone()[0]
            ),
        }
        return {
            "status": (
                "not_evaluated"
                if run is None
                else (validation_run(connection, int(run["id"])) or {}).get("status")
            ),
            "device_id": device,
            **counts,
            "latest_run": (
                validation_run(connection, int(run["id"])) if run is not None else None
            ),
            "algorithm_version": ALGORITHM_VERSION,
            "configuration_version": CONFIGURATION_VERSION,
            "matching_version": MATCHING_VERSION,
            "interpretation": INTERPRETATION,
        }


def backfill_database(
    database_path: Path | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    # Validation is a dataset-level reconstruction; its evidence signature
    # makes repeated backfill calls idempotent.
    return evaluate_database(database_path, **kwargs)


def _print(value: Any) -> None:
    print(json.dumps(value, indent=2, default=str))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SmartOps Phase 5B validation")
    command = parser.add_mutually_exclusive_group(required=True)
    command.add_argument("--status", action="store_true")
    command.add_argument("--evaluate", action="store_true")
    command.add_argument("--backfill", action="store_true")
    command.add_argument("--summary", action="store_true")
    command.add_argument("--list-incidents", action="store_true")
    command.add_argument("--list-unverified-alerts", action="store_true")
    parser.add_argument("--device")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--category")
    parser.add_argument("--severity")
    parser.add_argument("--algorithm-version")
    parser.add_argument("--observation-period", type=int)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--limit", type=int, default=50)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    path = get_database_path()
    if args.observation_period is not None:
        initialize_database(path)
        with database_connection(path) as connection:
            period = connection.execute(
                """SELECT device_id, start_utc, end_utc
                FROM validation_observation_periods WHERE id = ?""",
                (args.observation_period,),
            ).fetchone()
        if period is None:
            raise SystemExit("Observation period not found.")
        args.device = args.device or period["device_id"]
        args.start = args.start or period["start_utc"]
        args.end = args.end or period["end_utc"]
    if args.status:
        _print(validation_status(path, device=args.device))
    elif args.evaluate or args.backfill:
        _print(
            evaluate_database(
                path,
                device=args.device,
                start=args.start,
                end=args.end,
                force=args.force,
            )
        )
    else:
        initialize_database(path)
        with database_connection(path) as connection:
            if args.summary:
                runs, total = list_validation_runs(
                    connection, device_id=args.device, limit=args.limit
                )
                if args.algorithm_version:
                    runs = [
                        item for item in runs
                        if item["algorithm_version"] == args.algorithm_version
                    ]
                    total = len(runs)
                _print({"total": total, "items": runs})
            elif args.list_incidents:
                items, total = list_incidents(
                    connection,
                    device_id=args.device,
                    category=args.category,
                    severity=args.severity,
                    start=args.start,
                    end=args.end,
                    limit=args.limit,
                )
                _print({"total": total, "items": items})
            else:
                _print(
                    list_unverified_alerts(
                        connection, device_id=args.device, limit=args.limit
                    )
                )


if __name__ == "__main__":
    main()
