"""Phase 5B local incident feedback and predictive validation.

Only explicitly labelled outcomes and explicitly completed observation periods
are eligible. Missing feedback is never interpreted as a negative outcome.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from agent.config import get_database_path
from analytics.validation_config import (
    ALGORITHM_VERSION,
    CATEGORY_COMPATIBILITY,
    CATEGORY_WARNING_HORIZONS_SECONDS,
    CONFIGURATION_VERSION,
    DEFAULT_POLICY,
    INTERPRETATION,
    MATCHING_VERSION,
    MATURITY_LEVELS,
    ValidationPolicy,
)
from backend.database import database_connection, initialize_database
from backend.phase5b_repository import (
    append_match_event,
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
            "incident_reporting_complete, coverage_ratio, declared_outcome, "
            "related_incident_id, eligibility_state, updated_at_utc",
        ),
        (
            "alert_outcome_events",
            "id, alert_occurrence_id, previous_outcome, new_outcome, "
            "event_timestamp_utc, audit_reason",
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
    alerts = [dict(row) for row in connection.execute(
        """SELECT id, device_id, category, evidence_domain,
        first_observed_utc, workload_context, consecutive_window_count
        FROM alerts WHERE (? IS NULL OR device_id = ?)""",
        (device, device),
    )]
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
    for incident in incidents:
        candidates: list[dict[str, Any]] = []
        incident_time = _parse(incident["start_utc"])
        for alert in alerts:
            if incident["device_id"] != alert["device_id"]:
                continue
            alert_time = _parse(alert["first_observed_utc"])
            compatible = CATEGORY_COMPATIBILITY.get(alert["category"], set())
            horizon = CATEGORY_WARNING_HORIZONS_SECONDS.get(
                alert["category"], policy.matching_lookback_seconds
            )
            difference = (incident_time - alert_time).total_seconds()
            if difference < 0 or difference > horizon:
                continue
            category_match = incident["category"] in compatible
            if not category_match:
                continue
            existing = connection.execute(
                """SELECT origin FROM alert_incident_links
                WHERE alert_id = ? AND incident_id = ? AND matching_version = ?""",
                (alert["id"], incident["id"], MATCHING_VERSION),
            ).fetchone()
            if existing and existing["origin"] == "manual":
                continue
            time_score = max(
                0.0,
                1.0 - difference / horizon,
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
            if score >= policy.possible_match_score:
                candidates.append({
                    "alert": alert,
                    "difference": difference,
                    "horizon": horizon,
                    "score": round(score, 6),
                    "workload_match": workload_match,
                    "event_match": event_match,
                    "persistence_support": persistence_support,
                })

        # A single compatible candidate is deterministic. Multiple candidates
        # remain proposals for a simple user decision; time proximity alone
        # must not be presented as proof of causation.
        candidates.sort(key=lambda item: (-item["score"], item["difference"], item["alert"]["id"]))
        uniquely_determined = len(candidates) == 1
        if not candidates:
            append_match_event(
                connection,
                incident_id=incident["id"], alert_id=None, link_id=None,
                previous_decision=None,
                new_decision="no_qualifying_preceding_alert",
                decision_basis={
                    "device_match_required": True,
                    "category_compatibility_required": True,
                    "preceding_alert_required": True,
                    "result": "potential_false_negative_subject_to_monitoring_eligibility",
                },
                warning_horizon_seconds=None, matching_score=None,
                method_version=MATCHING_VERSION,
            )
            continue
        for candidate in candidates:
            alert = candidate["alert"]
            match_type = "confirmed_match" if uniquely_determined else (
                "probable_match" if candidate["score"] >= policy.probable_match_score
                else "possible_match"
            )
            upsert_alert_incident_link(
                connection,
                alert_id=alert["id"],
                incident_id=incident["id"],
                match_type=match_type,
                origin="automatic",
                confirmed_by_user=False,
                matching_score=candidate["score"],
                time_difference_seconds=candidate["difference"],
                category_compatible=True,
                matching_rule="unique_category_time_workload_v2",
                supporting_evidence=[
                    value
                    for value, present in (
                        ("compatible_category", True),
                        ("within_matching_horizon", True),
                        ("same_workload_context", candidate["workload_match"]),
                        ("linked_windows_event", candidate["event_match"]),
                        ("persistent_alert_evidence", candidate["persistence_support"]),
                    )
                    if present
                ],
                contradictory_evidence=[],
                reason_codes=(
                    ["unique_deterministic_temporal_match", "temporal_match_not_causation"]
                    if uniquely_determined
                    else ["ambiguous_match_requires_user_confirmation"]
                ),
            )
            link = connection.execute(
                """SELECT id FROM alert_incident_links WHERE alert_id = ?
                AND incident_id = ? AND matching_version = ?""",
                (alert["id"], incident["id"], MATCHING_VERSION),
            ).fetchone()
            append_match_event(
                connection,
                incident_id=incident["id"], alert_id=alert["id"],
                link_id=int(link["id"]) if link else None,
                previous_decision=None,
                new_decision=("accepted_unique_match" if uniquely_determined else "proposed_ambiguous_match"),
                decision_basis={
                    "alert_category": alert["category"],
                    "incident_category": incident["category"],
                    "time_difference_seconds": candidate["difference"],
                    "workload_match": candidate["workload_match"],
                    "candidate_count": len(candidates),
                    "causation_claimed": False,
                },
                warning_horizon_seconds=candidate["horizon"],
                matching_score=candidate["score"],
                method_version=MATCHING_VERSION,
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
    enough = denominator > 0
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
            [] if value is not None else [f"{name}_denominator_is_zero"]
        ),
        "reconstruction": reconstruction,
        "confidence_interval_lower": None,
        "confidence_interval_upper": None,
        "confidence_interval_method": None,
        "maturity_label": "preliminary" if value is not None else "insufficient",
    }


def _wilson_interval(successes: float, total: float) -> tuple[float, float] | tuple[None, None]:
    """Return a two-sided 95% Wilson score interval for a binomial rate."""
    if total <= 0:
        return None, None
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + (z * z / total)
    centre = (proportion + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt((proportion * (1 - proportion) / total) + z * z / (4 * total * total))
        / denominator
    )
    return round(max(0.0, centre - margin), 6), round(min(1.0, centre + margin), 6)


def _proportion_metric(
    name: str,
    numerator: float,
    denominator: float,
    minimum: int,
    *,
    reconstruction: dict[str, Any],
    maturity_label: str,
) -> dict[str, Any]:
    item = _metric(
        name, numerator, denominator, minimum,
        confidence=0.0, reconstruction=reconstruction,
    )
    low, high = _wilson_interval(numerator, denominator)
    item.update({
        "confidence_interval_lower": low,
        "confidence_interval_upper": high,
        "confidence_interval_method": "wilson_score_95" if low is not None else None,
        "maturity_label": maturity_label if denominator > 0 else "insufficient",
    })
    return item


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
        reconstruction_json, confidence_interval_lower,
        confidence_interval_upper, confidence_interval_method, maturity_label
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            item.get("confidence_interval_lower"),
            item.get("confidence_interval_upper"),
            item.get("confidence_interval_method"),
            item.get("maturity_label", "insufficient"),
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
        if item.get("declared_outcome") not in {
            "no_meaningful_issue", "issue_occurred"
        }:
            reasons.append("validation_outcome_not_declared")
        if item["end_utc"] is None:
            reasons.append("observation_period_has_no_end")
        if (item["coverage_ratio"] or 0) < policy.minimum_window_coverage:
            reasons.append("observation_period_coverage_below_threshold")
        if item.get("missing_intervals"):
            reasons.append("observation_period_has_unexplained_gaps")
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


def _incident_monitoring_eligibility(
    connection: sqlite3.Connection,
    incident: dict[str, Any],
    policy: ValidationPolicy,
) -> tuple[bool, list[str], dict[str, Any]]:
    """Verify that monitoring existed immediately before a reported incident.

    A missed alert can only be called a false negative when SmartOps had a fair
    opportunity to observe the machine. Deliberate shutdown and missing data
    are excluded rather than interpreted as Normal.
    """
    incident_time = _parse(incident["start_utc"])
    period_start = incident_time.timestamp() - policy.incident_monitoring_lookback_seconds
    start_utc = datetime.fromtimestamp(period_start, tz=timezone.utc).isoformat()
    expected = max(1, math.ceil(policy.incident_monitoring_lookback_seconds / 300))
    row = connection.execute(
        """SELECT COUNT(*) total,
        SUM(CASE WHEN is_complete = 1 AND coverage_ratio >= ?
          AND (finalization_state IS NULL OR finalization_state IN ('finalized','audited_correction'))
          THEN 1 ELSE 0 END) eligible
        FROM feature_windows WHERE device_id = ?
        AND window_start_utc >= ? AND window_end_utc <= ?""",
        (
            policy.minimum_window_coverage,
            incident["device_id"], start_utc, incident["start_utc"],
        ),
    ).fetchone()
    eligible = int(row["eligible"] or 0)
    coverage = min(1.0, eligible / expected)
    reasons: list[str] = []
    if coverage < policy.minimum_incident_monitoring_coverage:
        reasons.append("insufficient_monitoring_before_incident")
    return not reasons, reasons, {
        "monitoring_start_utc": start_utc,
        "incident_start_utc": incident["start_utc"],
        "expected_finalized_windows": expected,
        "eligible_finalized_windows": eligible,
        "coverage_ratio": round(coverage, 6),
        "minimum_coverage": policy.minimum_incident_monitoring_coverage,
    }


def _maturity(
    positive_units: int,
    negative_units: int,
    distinct_days: int,
) -> tuple[str, int, int]:
    stronger, moderate, preliminary = MATURITY_LEVELS
    if all((positive_units >= stronger[1], negative_units >= stronger[2], distinct_days >= stronger[3])):
        return stronger[0], 0, 0
    if all((positive_units >= moderate[1], negative_units >= moderate[2], distinct_days >= moderate[3])):
        return moderate[0], max(0, stronger[1] - positive_units), max(0, stronger[2] - negative_units)
    if all((positive_units >= preliminary[1], negative_units >= preliminary[2], distinct_days >= preliminary[3])):
        return preliminary[0], max(0, moderate[1] - positive_units), max(0, moderate[2] - negative_units)
    return (
        "insufficient",
        max(0, preliminary[1] - positive_units),
        max(0, preliminary[2] - negative_units),
    )


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
                    """SELECT link.*, alert.category alert_category,
                    alert.first_observed_utc alert_first_observed_utc,
                    incident.category incident_category,
                    incident.start_utc incident_start_utc
                    FROM alert_incident_links link
                    JOIN alerts alert ON alert.id = link.alert_id
                    JOIN incident_reports incident ON incident.id = link.incident_id
                    WHERE link.match_type = 'confirmed_match'
                    AND link.category_compatible = 1
                    AND link.matching_version = ?""",
                    (MATCHING_VERSION,),
                ):
                    lead = (
                        _parse(link["incident_start_utc"])
                        - _parse(link["alert_first_observed_utc"])
                    ).total_seconds()
                    horizon = CATEGORY_WARNING_HORIZONS_SECONDS.get(
                        link["alert_category"], policy.matching_lookback_seconds
                    )
                    category_ok = link["incident_category"] in CATEGORY_COMPATIBILITY.get(
                        link["alert_category"], set()
                    )
                    if category_ok and 0 <= lead <= horizon:
                        confirmed_links[int(link["incident_id"])].append(link)
                qualifying_confirmed_alert_ids = {
                    int(link["alert_id"])
                    for links in confirmed_links.values()
                    for link in links
                }

                for incident in incidents:
                    reasons: list[str] = []
                    if incident["status"] != "active":
                        reasons.append("incident_withdrawn")
                    if incident["verification_status"] not in VERIFIED:
                        reasons.append("incident_not_verified")
                    monitoring_ok, monitoring_reasons, monitoring_details = (
                        _incident_monitoring_eligibility(connection, incident, policy)
                    )
                    containing_periods = [
                        period for period in periods
                        if period["device_id"] == incident["device_id"]
                        and period["start_utc"] <= incident["start_utc"] <= period["end_utc"]
                    ]
                    if not monitoring_ok and containing_periods:
                        monitoring_ok = True
                        monitoring_reasons = []
                        monitoring_details["eligible_observation_period_ids"] = [
                            period["id"] for period in containing_periods
                        ]
                        monitoring_details["eligibility_basis"] = (
                            "eligible_user_confirmed_observation_period"
                        )
                    if not monitoring_ok:
                        reasons.extend(monitoring_reasons)
                    if reasons:
                        _record_evidence(
                            connection, run_id, "incident", incident["id"], False,
                            None, reasons, monitoring_details,
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
                        classification, [], monitoring_details,
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
                canonical_outcomes = {
                    int(row["alert_id"]): dict(row)
                    for row in connection.execute(
                        """WITH ranked AS (
                          SELECT occurrence.alert_id, outcome.new_outcome,
                          outcome.event_timestamp_utc,
                          ROW_NUMBER() OVER (
                            PARTITION BY occurrence.alert_id
                            ORDER BY outcome.event_timestamp_utc DESC, outcome.id DESC
                          ) position
                          FROM alert_outcome_events outcome
                          JOIN alert_occurrences occurrence
                            ON occurrence.id = outcome.alert_occurrence_id
                        ) SELECT * FROM ranked WHERE position = 1"""
                    )
                }
                for alert in alerts:
                    reasons: list[str] = []
                    classification: str | None = None
                    canonical = canonical_outcomes.get(int(alert["id"]))
                    if canonical and canonical["new_outcome"] == "false_positive":
                        classification = "false_positive"
                    elif canonical and canonical["new_outcome"] == "confirmed":
                        if int(alert["id"]) in qualifying_confirmed_alert_ids:
                            classification = "true_positive"
                        else:
                            reasons.append("confirmed_alert_requires_incident_match")
                    elif canonical and canonical["new_outcome"] == "pending":
                        reasons.append("alert_outcome_pending")
                    elif canonical and canonical["new_outcome"] == "inconclusive":
                        reasons.append("alert_outcome_inconclusive")
                    elif alert["feedback_id"] is None:
                        reasons.append("alert_feedback_unavailable")
                    elif alert["feedback_status"] != "active":
                        reasons.append("alert_feedback_withdrawn")
                    elif alert["feedback_verification_status"] not in VERIFIED:
                        reasons.append("alert_feedback_not_verified")
                    elif alert["feedback_outcome"] == "confirmed_related_issue":
                        if int(alert["id"]) in qualifying_confirmed_alert_ids:
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
                        # Unreviewed alerts are counted in the status summary,
                        # but writing one exclusion row for every historical
                        # alert on every label revision creates no additional
                        # scientific evidence. Reviewed/pending/inconclusive
                        # records keep their exact auditable exclusion.
                        if reasons != ["alert_feedback_unavailable"]:
                            _record_evidence(
                                connection, run_id, "alert", alert["id"], False,
                                None, reasons,
                            )

                # One completed, explicitly no-incident period is one negative
                # validation unit. It is TN when no qualifying alert occurred,
                # otherwise FP. Explicitly false-positive alerts outside those
                # periods are separate reviewed negative units.
                period_class: dict[int, str] = {}
                period_alert_ids: set[int] = set()
                eligible_negative_periods: list[dict[str, Any]] = []
                for period in periods:
                    if period.get("declared_outcome") != "no_meaningful_issue":
                        connection.execute(
                            """UPDATE validation_inclusion_exclusion
                            SET included = 0, classification = NULL,
                            reason_codes_json = ?
                            WHERE evaluation_run_id = ? AND evidence_type = 'observation_period'
                            AND evidence_id = ?""",
                            (dumps(["period_reports_issue_not_negative_evidence"]), run_id, period["id"]),
                        )
                        continue
                    overlapping_incidents = [
                        item["id"] for item in incidents
                        if item["status"] == "active"
                        and item["device_id"] == period["device_id"]
                        and period["start_utc"] <= item["start_utc"] <= period["end_utc"]
                    ]
                    if overlapping_incidents:
                        connection.execute(
                            """UPDATE validation_inclusion_exclusion
                            SET included = 0, classification = NULL,
                            reason_codes_json = ?, details_json = ?
                            WHERE evaluation_run_id = ? AND evidence_type = 'observation_period'
                            AND evidence_id = ?""",
                            (
                                dumps(["no_issue_declaration_conflicts_with_reported_incident"]),
                                dumps({"incident_ids": overlapping_incidents}),
                                run_id, period["id"],
                            ),
                        )
                        continue
                    qualifying = sorted({
                        int(item["id"]) for item in alerts
                        if item["device_id"] == period["device_id"]
                        and item["first_observed_utc"] < period["end_utc"]
                        and item["latest_observed_utc"] >= period["start_utc"]
                    })
                    classification = "false_positive" if qualifying else "true_negative"
                    period_class[int(period["id"])] = classification
                    period_alert_ids.update(qualifying)
                    eligible_negative_periods.append(period)
                    connection.execute(
                        """UPDATE validation_inclusion_exclusion
                        SET classification = ?, details_json = ?
                        WHERE evaluation_run_id = ? AND evidence_type = 'observation_period'
                        AND evidence_id = ?""",
                        (
                            classification,
                            dumps({
                                "qualifying_alert_ids": qualifying,
                                "unit": "completed_user_confirmed_no_incident_period",
                            }),
                            run_id, period["id"],
                        ),
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
                    eligible_links: list[tuple[sqlite3.Row, sqlite3.Row]] = []
                    for link in links:
                        alert = connection.execute(
                            "SELECT first_observed_utc FROM alerts WHERE id = ?",
                            (link["alert_id"],),
                        ).fetchone()
                        if alert is None:
                            continue
                        eligible_links.append((link, alert))
                    if eligible_links:
                        # Earliest qualifying warning is the reproducible lead
                        # time for one incident; an incident is never counted
                        # repeatedly because several related alerts exist.
                        link, alert = min(
                            eligible_links,
                            key=lambda pair: pair[1]["first_observed_utc"],
                        )
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
                period_counts = Counter(period_class.values())
                explicit_fp_outside_periods = {
                    alert_id for alert_id, classification in alert_class.items()
                    if classification == "false_positive"
                    and alert_id not in period_alert_ids
                }
                confusion_counts = Counter({
                    "true_positive": incident_counts["true_positive"],
                    "false_negative": incident_counts["false_negative"],
                    "false_positive": (
                        period_counts["false_positive"]
                        + len(explicit_fp_outside_periods)
                    ),
                    "true_negative": period_counts["true_negative"],
                })
                distinct_days = len(
                    {
                        _parse(value).date().isoformat()
                        for value in (
                            [item["start_utc"] for item in incidents if item["id"] in incident_class]
                            + [item["start_utc"] for item in eligible_negative_periods]
                        )
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
                eligible_total = sum(confusion_counts.values())
                mean_period_coverage = (
                    sum(float(item["coverage_ratio"]) for item in periods)
                    / len(periods)
                    if periods
                    else 0.0
                )
                # Do not manufacture a validation-confidence percentage. Sample
                # maturity and Wilson intervals communicate uncertainty.
                confidence = 0.0
                positive_units = (
                    confusion_counts["true_positive"]
                    + confusion_counts["false_negative"]
                )
                negative_units = (
                    confusion_counts["true_negative"]
                    + confusion_counts["false_positive"]
                )
                maturity_label, additional_positive, additional_negative = _maturity(
                    positive_units, negative_units, distinct_days
                )
                confidence_level = maturity_label
                tp = confusion_counts["true_positive"]
                tn = confusion_counts["true_negative"]
                fp = confusion_counts["false_positive"]
                fn = confusion_counts["false_negative"]
                reconstruction = {
                    "true_positive": tp,
                    "true_negative": tn,
                    "false_positive": fp,
                    "false_negative": fn,
                    "unit_policy": "unique_incidents_and_verified_negative_units_v2",
                }
                precision_den = tp + fp
                recall_den = tp + fn
                specificity_den = tn + fp
                total_den = tp + tn + fp + fn
                metrics = [
                    _proportion_metric("precision", tp, precision_den, policy.minimum_precision_alerts,
                                       reconstruction=reconstruction, maturity_label=maturity_label),
                    _proportion_metric("recall", tp, recall_den, policy.minimum_recall_incidents,
                                       reconstruction=reconstruction, maturity_label=maturity_label),
                    _proportion_metric("accuracy", tp + tn, total_den, policy.minimum_accuracy_windows,
                                       reconstruction=reconstruction, maturity_label=maturity_label),
                    _proportion_metric("specificity", tn, specificity_den, 1,
                                       reconstruction=reconstruction, maturity_label=maturity_label),
                    _proportion_metric("false_positive_rate", fp, specificity_den, 1,
                                       reconstruction=reconstruction, maturity_label=maturity_label),
                    _proportion_metric("false_negative_rate", fn, recall_den, 1,
                                       reconstruction=reconstruction, maturity_label=maturity_label),
                ]
                balanced_ready = recall_den > 0 and specificity_den > 0
                tpr = tp / recall_den if recall_den else None
                tnr = tn / specificity_den if specificity_den else None
                tpr_low, tpr_high = _wilson_interval(tp, recall_den)
                tnr_low, tnr_high = _wilson_interval(tn, specificity_den)
                balanced_value = (tpr + tnr) / 2 if tpr is not None and tnr is not None else None
                metrics.append({
                    "metric_name": "balanced_accuracy", "numerator": None,
                    "denominator": total_den,
                    "metric_value": round(balanced_value, 6) if balanced_value is not None else None,
                    "evaluation_state": "evaluated" if balanced_ready else "insufficient_labeled_evidence",
                    "data_confidence": 0.0,
                    "minimum_requirement": 1,
                    "reason_codes": [] if balanced_ready else ["balanced_accuracy_requires_positive_and_negative_denominators"],
                    "reconstruction": {**reconstruction, "true_positive_rate": tpr, "true_negative_rate": tnr},
                    "confidence_interval_lower": round((tpr_low + tnr_low) / 2, 6) if tpr_low is not None and tnr_low is not None else None,
                    "confidence_interval_upper": round((tpr_high + tnr_high) / 2, 6) if tpr_high is not None and tnr_high is not None else None,
                    "confidence_interval_method": "mean_of_tpr_tnr_wilson_95" if balanced_ready else None,
                    "maturity_label": maturity_label if balanced_ready else "insufficient",
                })
                precision_value = tp / precision_den if precision_den else None
                recall_value = tp / recall_den if recall_den else None
                f1_value = (
                    2 * precision_value * recall_value / (precision_value + recall_value)
                    if precision_value is not None and recall_value is not None
                    and precision_value + recall_value > 0 else None
                )
                metrics.append({
                    "metric_name": "f1_score", "numerator": None,
                    "denominator": total_den,
                    "metric_value": round(f1_value, 6) if f1_value is not None else None,
                    "evaluation_state": "evaluated" if f1_value is not None else "insufficient_labeled_evidence",
                    "data_confidence": 0.0, "minimum_requirement": 1,
                    "reason_codes": [] if f1_value is not None else ["f1_requires_precision_and_recall"],
                    "reconstruction": {**reconstruction, "precision": precision_value, "recall": recall_value},
                    "confidence_interval_lower": None, "confidence_interval_upper": None,
                    "confidence_interval_method": None,
                    "maturity_label": maturity_label if f1_value is not None else "insufficient",
                })
                metrics.append(_proportion_metric(
                    "false_alert_proportion", fp, precision_den,
                    policy.minimum_precision_alerts,
                    reconstruction=reconstruction, maturity_label=maturity_label,
                ))
                mean_lead = sum(lead_times) / len(lead_times) if lead_times else None
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
                            if mean_lead is not None else ["no_matched_incident_lead_time"]
                        ),
                        "reconstruction": {
                            "lead_times_seconds": lead_times,
                            "late_detection_count": sum(value < 0 for value in lead_times),
                        },
                        "confidence_interval_lower": None,
                        "confidence_interval_upper": None,
                        "confidence_interval_method": None,
                        "maturity_label": maturity_label if mean_lead is not None else "insufficient",
                    }
                )
                sorted_leads = sorted(lead_times)
                lead_ready = bool(sorted_leads)
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
                                else ["no_matched_incident_lead_time"]
                            ),
                            "reconstruction": {
                                "lead_times_seconds": sorted_leads,
                                "method": name.removesuffix("_warning_lead_time_seconds"),
                            },
                            "confidence_interval_lower": None,
                            "confidence_interval_upper": None,
                            "confidence_interval_method": None,
                            "maturity_label": maturity_label if value is not None else "insufficient",
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
                        "confidence_interval_lower": None,
                        "confidence_interval_upper": None,
                        "confidence_interval_method": None,
                        "maturity_label": maturity_label,
                    }

                for name, value in (
                    ("verified_alert_count", len(alert_class)),
                    ("true_positive_alert_count", tp),
                    ("false_positive_alert_count", fp),
                    ("confirmed_incident_count", len(incident_class)),
                    ("detected_incident_count", tp),
                    ("missed_incident_count", fn),
                    ("true_positive_count", tp),
                    ("true_negative_count", tn),
                    ("false_positive_count", fp),
                    ("false_negative_count", fn),
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
                range_values = (
                    [item["start_utc"] for item in incidents if item["id"] in incident_class]
                    + [item["start_utc"] for item in eligible_negative_periods]
                    + [item["end_utc"] for item in eligible_negative_periods if item["end_utc"]]
                )
                validation_start = min(range_values) if range_values else start
                validation_end = max(range_values) if range_values else end
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
                    confidence_level = ?, reason_codes_json = ?,
                    eligible_observation_period_count = ?, observation_coverage = ?,
                    maturity_label = ?, additional_positive_needed = ?,
                    additional_negative_needed = ?, validation_start_utc = ?,
                    validation_end_utc = ?
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
                        len(eligible_negative_periods),
                        mean_period_coverage if eligible_negative_periods else None,
                        maturity_label,
                        additional_positive,
                        additional_negative,
                        validation_start,
                        validation_end,
                        run_id,
                    ),
                )
                return validation_run(connection, run_id) or {}
        except Exception:
            # The surrounding transaction rolls back every partial matching,
            # run, classification and metric write.
            raise


def validation_run(
    connection: sqlite3.Connection,
    run_id: int,
    *,
    evidence_limit: int | None = None,
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
    evidence_sql = """SELECT * FROM validation_inclusion_exclusion
            WHERE evaluation_run_id = ?
            ORDER BY included DESC,
              CASE evidence_type
                WHEN 'incident' THEN 0
                WHEN 'observation_period' THEN 1
                WHEN 'alert' THEN 2
                ELSE 3
              END,
              evidence_id DESC"""
    evidence_values: tuple[Any, ...] = (run_id,)
    if evidence_limit is not None:
        evidence_sql += " LIMIT ?"
        evidence_values = (run_id, evidence_limit)
    item["evidence_decisions_total"] = int(connection.execute(
        "SELECT COUNT(*) FROM validation_inclusion_exclusion WHERE evaluation_run_id = ?",
        (run_id,),
    ).fetchone()[0])
    item["evidence_decisions"] = [
        expand(value) or {}
        for value in connection.execute(evidence_sql, evidence_values)
    ]
    overall = {
        metric["metric_name"]: metric
        for metric in item["metrics"]
        if metric["scope_type"] == "overall"
    }
    item["confusion_matrix"] = {
        key: int((overall.get(metric_name) or {}).get("metric_value") or 0)
        for key, metric_name in (
            ("true_positive", "true_positive_count"),
            ("true_negative", "true_negative_count"),
            ("false_positive", "false_positive_count"),
            ("false_negative", "false_negative_count"),
        )
    }
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
        latest = (
            validation_run(connection, int(run["id"]), evidence_limit=100)
            if run is not None else None
        )
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
                    """SELECT COUNT(DISTINCT alert_id) FROM (
                    SELECT f.alert_id FROM alert_feedback f JOIN alerts a
                    ON a.id = f.alert_id WHERE f.status = 'active'
                    AND (? IS NULL OR a.device_id = ?)
                    UNION ALL
                    SELECT occurrence.alert_id FROM alert_outcome_events outcome
                    JOIN alert_occurrences occurrence ON occurrence.id = outcome.alert_occurrence_id
                    JOIN alerts a ON a.id = occurrence.alert_id
                    WHERE (? IS NULL OR a.device_id = ?))""",
                    (device, device, device, device),
                ).fetchone()[0]
            ),
            "unverified_alert_count": int(
                connection.execute(
                    """SELECT COUNT(*) FROM alerts alert
                    LEFT JOIN alert_feedback feedback ON feedback.alert_id = alert.id
                    WHERE feedback.id IS NULL
                    AND NOT EXISTS (
                      SELECT 1 FROM alert_occurrences occurrence
                      JOIN alert_outcome_events outcome
                        ON outcome.alert_occurrence_id = occurrence.id
                      WHERE occurrence.alert_id = alert.id
                    )
                    AND (? IS NULL OR alert.device_id = ?)""",
                    (device, device),
                ).fetchone()[0]
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
                else (latest or {}).get("status")
            ),
            "device_id": device,
            **counts,
            "latest_run": latest,
            "algorithm_version": ALGORITHM_VERSION,
            "configuration_version": CONFIGURATION_VERSION,
            "matching_version": MATCHING_VERSION,
            "category_warning_horizons_seconds": CATEGORY_WARNING_HORIZONS_SECONDS,
            "metric_definitions": {
                "precision": "TP / (TP + FP)",
                "recall": "TP / (TP + FN)",
                "accuracy": "(TP + TN) / (TP + TN + FP + FN)",
                "balanced_accuracy": "(TPR + TNR) / 2",
                "f1_score": "2 * precision * recall / (precision + recall)",
                "false_positive_rate": "FP / (FP + TN)",
                "false_negative_rate": "FN / (FN + TP)",
            },
            "maturity_policy": [
                {"label": label, "minimum_positive_units": positive,
                 "minimum_negative_units": negative,
                 "minimum_distinct_days": days}
                for label, positive, negative, days in MATURITY_LEVELS
            ],
            "confidence_policy": (
                "No arbitrary validation-confidence percentage is calculated. "
                "Proportion metrics use 95% Wilson intervals; evidence maturity "
                "is a versioned engineering/reporting policy."
            ),
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
