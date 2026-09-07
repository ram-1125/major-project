"""Schema 18 persistence for permanent post-calibration features.

The tables in this module are additive.  They never modify baseline statistics,
historical workload labels, alert severity, or Phase 7B shadow evidence.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping


SCHEMA_VERSION = 18

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS fine_quality_observations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        feature_window_id INTEGER NOT NULL,
        device_id TEXT NOT NULL,
        observed_at_utc TEXT NOT NULL,
        detected_profile_key TEXT NOT NULL,
        parent_workload_profile TEXT,
        detection_confidence REAL NOT NULL CHECK(detection_confidence BETWEEN 0 AND 100),
        detection_reason TEXT NOT NULL,
        evidence_identifiers_json TEXT NOT NULL,
        taxonomy_version TEXT NOT NULL,
        detection_rule_version TEXT NOT NULL,
        derivation_mode TEXT NOT NULL,
        created_at_utc TEXT NOT NULL,
        FOREIGN KEY(feature_window_id) REFERENCES feature_windows(id),
        UNIQUE(feature_window_id, detection_rule_version)
    )""",
    """CREATE TABLE IF NOT EXISTS fine_quality_assessments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        observation_id INTEGER NOT NULL,
        feature_window_id INTEGER NOT NULL,
        device_id TEXT NOT NULL,
        profile_key TEXT NOT NULL,
        profile_name TEXT NOT NULL,
        parent_workload_profile TEXT,
        assessed_at_utc TEXT NOT NULL,
        evaluation_state TEXT NOT NULL,
        profile_quality_score REAL,
        evidence_confidence REAL,
        baseline_version_id INTEGER NOT NULL,
        taxonomy_version TEXT NOT NULL,
        detection_rule_version TEXT NOT NULL,
        scoring_method_version TEXT NOT NULL,
        normalization_method TEXT NOT NULL,
        reason_codes_json TEXT NOT NULL,
        missing_evidence_json TEXT NOT NULL,
        explanation TEXT NOT NULL,
        created_at_utc TEXT NOT NULL,
        FOREIGN KEY(observation_id) REFERENCES fine_quality_observations(id),
        FOREIGN KEY(feature_window_id) REFERENCES feature_windows(id),
        FOREIGN KEY(baseline_version_id) REFERENCES baseline_versions(id),
        UNIQUE(observation_id, profile_key, scoring_method_version, baseline_version_id)
    )""",
    """CREATE TABLE IF NOT EXISTS fine_quality_metric_contributions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        assessment_id INTEGER NOT NULL,
        metric_key TEXT NOT NULL,
        metric_label TEXT NOT NULL,
        observed_value REAL,
        baseline_centre REAL,
        expected_low REAL,
        expected_high REAL,
        configured_threshold_json TEXT NOT NULL,
        direction TEXT NOT NULL,
        availability_status TEXT NOT NULL,
        configured_weight REAL NOT NULL,
        effective_weight REAL NOT NULL,
        metric_score REAL,
        weighted_contribution REAL,
        explanation TEXT NOT NULL,
        FOREIGN KEY(assessment_id) REFERENCES fine_quality_assessments(id) ON DELETE CASCADE,
        UNIQUE(assessment_id, metric_key)
    )""",
    """CREATE TABLE IF NOT EXISTS validation_registry (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rule_identifier TEXT NOT NULL,
        rule_version TEXT NOT NULL,
        applicable_workload_scope TEXT NOT NULL,
        validation_type TEXT NOT NULL,
        true_positives INTEGER,
        true_negatives INTEGER,
        false_positives INTEGER,
        false_negatives INTEGER,
        accuracy REAL,
        precision_value REAL,
        recall_value REAL,
        specificity REAL,
        f1_score REAL,
        false_positive_rate REAL,
        sample_size INTEGER NOT NULL DEFAULT 0,
        dataset_description TEXT NOT NULL,
        validation_procedure TEXT NOT NULL,
        validation_period TEXT,
        validation_date_utc TEXT,
        limitations_json TEXT NOT NULL,
        supporting_research_json TEXT NOT NULL,
        code_configuration_version TEXT NOT NULL,
        created_at_utc TEXT NOT NULL,
        CHECK(validation_type IN ('real_world_labelled','controlled_experimental','synthetic','not_yet_validated')),
        UNIQUE(rule_identifier, rule_version, applicable_workload_scope,
               validation_type, code_configuration_version)
    )""",
    """CREATE TABLE IF NOT EXISTS alert_explanation_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        alert_id INTEGER NOT NULL,
        alert_occurrence_id INTEGER NOT NULL UNIQUE,
        alert_category TEXT NOT NULL,
        alert_severity TEXT NOT NULL,
        workload_profile TEXT,
        baseline_id INTEGER,
        baseline_version_id INTEGER,
        feature_window_id INTEGER NOT NULL,
        evidence_start_utc TEXT NOT NULL,
        evidence_end_utc TEXT NOT NULL,
        source_sample_count INTEGER NOT NULL,
        evidence_completeness REAL NOT NULL,
        triggering_rule_identifier TEXT NOT NULL,
        triggering_rule_version TEXT NOT NULL,
        triggering_metrics_json TEXT NOT NULL,
        observed_values_json TEXT NOT NULL,
        baseline_values_json TEXT NOT NULL,
        thresholds_json TEXT NOT NULL,
        deviations_json TEXT NOT NULL,
        top_contributors_json TEXT NOT NULL,
        missing_evidence_json TEXT NOT NULL,
        risk_context_json TEXT NOT NULL,
        health_context_json TEXT NOT NULL,
        plain_language_explanation TEXT NOT NULL,
        explanation_version TEXT NOT NULL,
        alert_confidence REAL,
        confidence_label TEXT,
        confidence_calculation_version TEXT,
        validation_registry_id INTEGER,
        created_at_utc TEXT NOT NULL,
        FOREIGN KEY(alert_id) REFERENCES alerts(id) ON DELETE CASCADE,
        FOREIGN KEY(alert_occurrence_id) REFERENCES alert_occurrences(id) ON DELETE CASCADE,
        FOREIGN KEY(baseline_id) REFERENCES baseline_profiles(id),
        FOREIGN KEY(baseline_version_id) REFERENCES baseline_versions(id),
        FOREIGN KEY(feature_window_id) REFERENCES feature_windows(id),
        FOREIGN KEY(validation_registry_id) REFERENCES validation_registry(id)
    )""",
    """CREATE TABLE IF NOT EXISTS alert_confidence_components (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_id INTEGER NOT NULL,
        component_key TEXT NOT NULL,
        component_value REAL,
        configured_weight REAL NOT NULL,
        weighted_contribution REAL,
        availability_status TEXT NOT NULL,
        explanation TEXT NOT NULL,
        FOREIGN KEY(snapshot_id) REFERENCES alert_explanation_snapshots(id) ON DELETE CASCADE,
        UNIQUE(snapshot_id, component_key)
    )""",
    """CREATE TABLE IF NOT EXISTS alert_outcome_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        alert_occurrence_id INTEGER NOT NULL,
        previous_outcome TEXT,
        new_outcome TEXT NOT NULL,
        event_timestamp_utc TEXT NOT NULL,
        optional_note TEXT,
        audit_reason TEXT NOT NULL,
        created_at_utc TEXT NOT NULL,
        CHECK(new_outcome IN ('pending','confirmed','false_positive','inconclusive')),
        FOREIGN KEY(alert_occurrence_id) REFERENCES alert_occurrences(id) ON DELETE CASCADE
    )""",
    """CREATE TABLE IF NOT EXISTS pipeline_stage_status (
        stage_key TEXT PRIMARY KEY,
        current_state TEXT NOT NULL,
        last_attempted_at_utc TEXT,
        last_successful_at_utc TEXT,
        expected_interval_seconds INTEGER,
        processing_duration_ms REAL,
        failure_or_overdue_reason TEXT,
        runtime_session_id TEXT,
        updated_at_utc TEXT NOT NULL,
        CHECK(current_state IN ('processing','successfully_waiting','overdue','failed','not_applicable','not_yet_executed'))
    )""",
)

INDEX_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS idx_fine_quality_observation_device_time ON fine_quality_observations(device_id, observed_at_utc DESC)",
    "CREATE INDEX IF NOT EXISTS idx_fine_quality_observation_profile_time ON fine_quality_observations(detected_profile_key, observed_at_utc DESC)",
    "CREATE INDEX IF NOT EXISTS idx_fine_quality_assessment_profile_time ON fine_quality_assessments(device_id, profile_key, assessed_at_utc DESC)",
    "CREATE INDEX IF NOT EXISTS idx_alert_snapshot_alert ON alert_explanation_snapshots(alert_id, created_at_utc DESC)",
    "CREATE INDEX IF NOT EXISTS idx_validation_registry_rule ON validation_registry(rule_identifier, rule_version, applicable_workload_scope)",
    "CREATE INDEX IF NOT EXISTS idx_alert_outcome_occurrence_time ON alert_outcome_events(alert_occurrence_id, event_timestamp_utc DESC, id DESC)",
)

PIPELINE_STAGES = {
    "telemetry_collection": 30,
    "feature_window_generation": 300,
    "active_baseline": None,
    "risk_evaluation": 300,
    "health_evaluation": 300,
    "predictive_alert_evaluation": 300,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def finish_migration(connection: sqlite3.Connection) -> None:
    """Seed neutral status rows only; no operational success is fabricated."""
    timestamp = _now()
    for stage, interval in PIPELINE_STAGES.items():
        connection.execute(
            """INSERT OR IGNORE INTO pipeline_stage_status (
            stage_key, current_state, expected_interval_seconds, updated_at_utc
            ) VALUES (?, 'not_yet_executed', ?, ?)""",
            (stage, interval, timestamp),
        )


def update_pipeline_stage(
    connection: sqlite3.Connection,
    stage_key: str,
    state: str,
    *,
    attempted_at_utc: str | None = None,
    successful_at_utc: str | None = None,
    duration_ms: float | None = None,
    reason: str | None = None,
    runtime_session_id: str | None = None,
) -> None:
    if stage_key not in PIPELINE_STAGES:
        raise ValueError("Unknown pipeline stage.")
    now = _now()
    connection.execute(
        """INSERT INTO pipeline_stage_status (
        stage_key, current_state, last_attempted_at_utc, last_successful_at_utc,
        expected_interval_seconds, processing_duration_ms,
        failure_or_overdue_reason, runtime_session_id, updated_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(stage_key) DO UPDATE SET
          current_state=excluded.current_state,
          last_attempted_at_utc=COALESCE(excluded.last_attempted_at_utc, pipeline_stage_status.last_attempted_at_utc),
          last_successful_at_utc=COALESCE(excluded.last_successful_at_utc, pipeline_stage_status.last_successful_at_utc),
          expected_interval_seconds=excluded.expected_interval_seconds,
          processing_duration_ms=COALESCE(excluded.processing_duration_ms, pipeline_stage_status.processing_duration_ms),
          failure_or_overdue_reason=excluded.failure_or_overdue_reason,
          runtime_session_id=COALESCE(excluded.runtime_session_id, pipeline_stage_status.runtime_session_id),
          updated_at_utc=excluded.updated_at_utc""",
        (
            stage_key, state, attempted_at_utc, successful_at_utc,
            PIPELINE_STAGES[stage_key], duration_ms, reason,
            runtime_session_id, now,
        ),
    )


def get_pipeline_status(
    connection: sqlite3.Connection,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return genuine persisted states with deterministic overdue protection."""
    current = now or datetime.now(timezone.utc)
    rows = {
        row["stage_key"]: dict(row)
        for row in connection.execute("SELECT * FROM pipeline_stage_status")
    }
    latest_metric = connection.execute(
        "SELECT timestamp_utc FROM metrics ORDER BY timestamp_utc DESC,id DESC LIMIT 1"
    ).fetchone()
    latest_feature = connection.execute(
        """SELECT finalized_at_utc FROM feature_windows
        WHERE finalization_state IN ('finalized','audited_correction')
        ORDER BY window_end_utc DESC,id DESC LIMIT 1"""
    ).fetchone()
    latest_risk = connection.execute(
        "SELECT evaluated_at_utc FROM risk_assessments ORDER BY evaluated_at_utc DESC,id DESC LIMIT 1"
    ).fetchone()
    latest_health = connection.execute(
        "SELECT assessed_at_utc FROM health_assessments ORDER BY assessed_at_utc DESC,id DESC LIMIT 1"
    ).fetchone()
    latest_alert = connection.execute(
        "SELECT finished_at_utc FROM alert_evaluation_runs ORDER BY finished_at_utc DESC,id DESC LIMIT 1"
    ).fetchone()
    active = connection.execute(
        """SELECT id,version_number,lifecycle_state,configuration_version
        FROM baseline_versions WHERE lifecycle_state='active'
        ORDER BY version_number DESC LIMIT 1"""
    ).fetchone()
    derived_success = {
        "telemetry_collection": latest_metric[0] if latest_metric else None,
        "feature_window_generation": latest_feature[0] if latest_feature else None,
        "risk_evaluation": latest_risk[0] if latest_risk else None,
        "health_evaluation": latest_health[0] if latest_health else None,
        "predictive_alert_evaluation": latest_alert[0] if latest_alert else None,
    }
    output = []
    for stage, interval in PIPELINE_STAGES.items():
        item = rows.get(stage, {
            "stage_key": stage, "current_state": "not_yet_executed",
            "last_attempted_at_utc": None, "last_successful_at_utc": None,
            "expected_interval_seconds": interval, "processing_duration_ms": None,
            "failure_or_overdue_reason": None, "updated_at_utc": None,
        })
        if stage == "active_baseline":
            state = "successfully_waiting" if active else "failed"
            item.update({
                "current_state": state,
                "last_successful_at_utc": active and item.get("last_successful_at_utc"),
                "failure_or_overdue_reason": None if active else "no_usable_active_baseline",
                "active_baseline_version": int(active["version_number"]) if active else None,
            })
        else:
            persisted_success = item.get("last_successful_at_utc")
            success = max(
                [value for value in (persisted_success, derived_success[stage]) if value],
                default=None,
            )
            item["last_successful_at_utc"] = success
            tolerance = 90 if stage == "telemetry_collection" else 900
            age = (
                max(0.0, (current - datetime.fromisoformat(success)).total_seconds())
                if success else None
            )
            state = item["current_state"]
            processing_age = (
                max(0.0, (current - datetime.fromisoformat(item["updated_at_utc"])).total_seconds())
                if state == "processing" and item.get("updated_at_utc") else None
            )
            if state == "processing" and processing_age is not None and processing_age <= tolerance:
                pass
            elif state == "failed" and item.get("failure_or_overdue_reason"):
                pass
            elif success is None:
                state = "not_yet_executed"
            elif age is not None and age > tolerance:
                state = "overdue"
                item["failure_or_overdue_reason"] = "latest_success_exceeded_overdue_tolerance"
            else:
                state = "successfully_waiting"
                item["failure_or_overdue_reason"] = None
            item["current_state"] = state
            item["success_age_seconds"] = round(age, 3) if age is not None else None
            item["overdue_tolerance_seconds"] = tolerance
        output.append(item)
    return {
        "database_connected": True,
        "generated_at_utc": current.isoformat(),
        "stages": output,
        "state_meaning": {
            "processing": "Currently processing genuine pipeline work.",
            "successfully_waiting": "Latest expected work succeeded; normal waiting is healthy.",
            "overdue": "No success arrived within the deterministic tolerance.",
            "failed": "The latest attempted operation failed.",
            "not_applicable": "The stage is legitimately not applicable.",
            "not_yet_executed": "No eligible input has been processed yet.",
        },
    }


def _loads(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


def get_profile_quality_assessments(
    connection: sqlite3.Connection,
    *,
    device_id: str | None = None,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """WITH ranked AS (
          SELECT assessment.id,
                 ROW_NUMBER() OVER (
                   PARTITION BY assessment.profile_key
                   ORDER BY
                     CASE WHEN assessment.profile_quality_score IS NOT NULL
                                AND baseline.activated_at_utc IS NOT NULL
                                AND observation.observed_at_utc > baseline.activated_at_utc
                          THEN 2
                          WHEN assessment.profile_quality_score IS NOT NULL THEN 1
                          ELSE 0 END DESC,
                     observation.observed_at_utc DESC,
                     assessment.id DESC
                 ) AS result_rank
          FROM fine_quality_assessments assessment
          JOIN fine_quality_observations observation
            ON observation.id = assessment.observation_id
          JOIN baseline_versions baseline
            ON baseline.id = assessment.baseline_version_id
          WHERE baseline.lifecycle_state = 'active'
            AND (? IS NULL OR assessment.device_id = ?)
        )
        SELECT assessment.*, observation.detection_reason,
                  observation.detection_confidence,
                  observation.observed_at_utc,
                  observation.evidence_identifiers_json AS detection_evidence_json,
                  baseline.activated_at_utc AS baseline_activated_at_utc,
                  baseline.version_number AS baseline_version_number
        FROM fine_quality_assessments assessment
        JOIN fine_quality_observations observation
          ON observation.id = assessment.observation_id
        JOIN baseline_versions baseline
          ON baseline.id = assessment.baseline_version_id
        JOIN ranked ON ranked.id = assessment.id AND ranked.result_rank = 1
        ORDER BY assessment.profile_name""",
        (device_id, device_id),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["reason_codes"] = _loads(item.pop("reason_codes_json"), [])
        item["missing_evidence"] = _loads(item.pop("missing_evidence_json"), [])
        item["detection_evidence"] = _loads(
            item.pop("detection_evidence_json", None), {}
        )
        contributions = []
        for stored in connection.execute(
            """SELECT * FROM fine_quality_metric_contributions
            WHERE assessment_id = ? ORDER BY id""",
            (item["id"],),
        ):
            value = dict(stored)
            value["configured_threshold"] = _loads(
                value.pop("configured_threshold_json"), {}
            )
            contributions.append(value)
        item["metric_contributions"] = contributions
        baseline_scope = next(
            (
                reason.removeprefix("baseline_scope_")
                for reason in item["reason_codes"]
                if reason.startswith("baseline_scope_")
            ),
            "unavailable",
        )
        training_member = False
        if baseline_scope != "unavailable":
            training_member = connection.execute(
                """SELECT 1
                FROM baseline_version_training_windows training
                JOIN baseline_version_profiles profile
                  ON profile.id = training.version_profile_id
                WHERE profile.baseline_version_id = ?
                  AND profile.workload_scope = ?
                  AND training.feature_window_id = ?
                LIMIT 1""",
                (
                    item["baseline_version_id"], baseline_scope,
                    item["feature_window_id"],
                ),
            ).fetchone() is not None
        post_activation = bool(
            item.get("baseline_activated_at_utc")
            and item.get("observed_at_utc")
            and item["observed_at_utc"] > item["baseline_activated_at_utc"]
        )
        independent = post_activation and not training_member
        item["baseline_source"] = baseline_scope
        item["baseline_training_evidence"] = training_member
        item["independent_post_activation_evidence"] = independent
        item["evidence_independence_state"] = (
            "baseline_training_evidence" if training_member
            else "independent_post_calibration_evidence" if independent
            else "historical_independence_unavailable"
        )
        item["current_evidence_quality"] = item.get("evidence_confidence")
        result.append(item)
    return result


def get_alert_snapshot(
    connection: sqlite3.Connection,
    alert_id: int,
) -> dict[str, Any] | None:
    row = connection.execute(
        """SELECT * FROM alert_explanation_snapshots
        WHERE alert_id = ? ORDER BY created_at_utc DESC, id DESC LIMIT 1""",
        (alert_id,),
    ).fetchone()
    if row is None:
        return None
    item = dict(row)
    for field in (
        "triggering_metrics", "observed_values", "baseline_values",
        "thresholds", "deviations", "top_contributors", "missing_evidence",
        "risk_context", "health_context",
    ):
        item[field] = _loads(item.pop(f"{field}_json"), [] if field in {
            "triggering_metrics", "top_contributors", "missing_evidence"
        } else {})
    item["confidence_components"] = [
        dict(value) for value in connection.execute(
            """SELECT component_key, component_value, configured_weight,
            weighted_contribution, availability_status, explanation
            FROM alert_confidence_components WHERE snapshot_id = ? ORDER BY id""",
            (item["id"],),
        )
    ]
    baseline_version = connection.execute(
        "SELECT version_number FROM baseline_versions WHERE id = ?",
        (item["baseline_version_id"],),
    ).fetchone()
    item["baseline_version"] = (
        int(baseline_version["version_number"]) if baseline_version else None
    )
    validation = (
        connection.execute(
            "SELECT * FROM validation_registry WHERE id = ?",
            (item["validation_registry_id"],),
        ).fetchone()
        if item["validation_registry_id"] else None
    )
    item["validation"] = decode_validation(validation) if validation else {
        "validation_type": "not_yet_validated",
        "display_label": "Not yet validated",
        "sample_size": 0,
        "accuracy": None,
        "precision": None,
        "recall": None,
        "specificity": None,
        "f1_score": None,
        "false_positive_rate": None,
        "limitations": [
            "No applicable labelled method-level validation record is registered."
        ],
    }
    return item


def decode_validation(row: Mapping[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["precision"] = item.pop("precision_value")
    item["recall"] = item.pop("recall_value")
    item["limitations"] = _loads(item.pop("limitations_json"), [])
    item["supporting_research"] = _loads(
        item.pop("supporting_research_json"), []
    )
    labels = {
        "real_world_labelled": "Real-world labelled validation",
        "controlled_experimental": "Controlled experimental validation",
        "synthetic": "Synthetic validation",
        "not_yet_validated": "Not yet validated",
    }
    item["display_label"] = labels.get(item["validation_type"], "Not yet validated")
    return item


def validation_metrics_from_counts(
    true_positives: int,
    true_negatives: int,
    false_positives: int,
    false_negatives: int,
) -> dict[str, float | int | None]:
    values = (true_positives, true_negatives, false_positives, false_negatives)
    if any(value < 0 for value in values):
        raise ValueError("Validation counts cannot be negative.")
    total = sum(values)
    precision_denominator = true_positives + false_positives
    recall_denominator = true_positives + false_negatives
    specificity_denominator = true_negatives + false_positives
    precision = true_positives / precision_denominator if precision_denominator else None
    recall = true_positives / recall_denominator if recall_denominator else None
    specificity = true_negatives / specificity_denominator if specificity_denominator else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall > 0
        else None
    )
    fpr = false_positives / specificity_denominator if specificity_denominator else None
    return {
        "accuracy": (true_positives + true_negatives) / total if total else None,
        "precision": precision,
        "recall": recall,
        "specificity": specificity,
        "f1_score": f1,
        "false_positive_rate": fpr,
        "sample_size": total,
    }


def register_validation(
    connection: sqlite3.Connection,
    *,
    rule_identifier: str,
    rule_version: str,
    workload_scope: str,
    validation_type: str,
    counts: tuple[int, int, int, int] | None,
    dataset_description: str,
    validation_procedure: str,
    validation_period: str | None,
    validation_date_utc: str | None,
    limitations: list[str],
    supporting_research: list[dict[str, Any]],
    code_configuration_version: str,
) -> int:
    allowed = {
        "real_world_labelled", "controlled_experimental", "synthetic",
        "not_yet_validated",
    }
    if validation_type not in allowed:
        raise ValueError("Unsupported validation type.")
    if validation_type == "not_yet_validated":
        counts = None
    metrics = (
        validation_metrics_from_counts(*counts) if counts is not None
        else {
            "accuracy": None, "precision": None, "recall": None,
            "specificity": None, "f1_score": None,
            "false_positive_rate": None, "sample_size": 0,
        }
    )
    timestamp = _now()
    cursor = connection.execute(
        """INSERT INTO validation_registry (
        rule_identifier,rule_version,applicable_workload_scope,validation_type,
        true_positives,true_negatives,false_positives,false_negatives,
        accuracy,precision_value,recall_value,specificity,f1_score,
        false_positive_rate,sample_size,dataset_description,
        validation_procedure,validation_period,validation_date_utc,
        limitations_json,supporting_research_json,code_configuration_version,
        created_at_utc
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            rule_identifier, rule_version, workload_scope, validation_type,
            counts[0] if counts else None, counts[1] if counts else None,
            counts[2] if counts else None, counts[3] if counts else None,
            metrics["accuracy"], metrics["precision"], metrics["recall"],
            metrics["specificity"], metrics["f1_score"],
            metrics["false_positive_rate"], metrics["sample_size"],
            dataset_description, validation_procedure, validation_period,
            validation_date_utc, json.dumps(limitations),
            json.dumps(supporting_research), code_configuration_version,
            timestamp,
        ),
    )
    return int(cursor.lastrowid)


def list_validations(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        decode_validation(row) for row in connection.execute(
            "SELECT * FROM validation_registry ORDER BY created_at_utc DESC,id DESC"
        )
    ]


def get_validation_for_rule(
    connection: sqlite3.Connection,
    rule_identifier: str,
    rule_version: str,
    workload_scope: str | None,
) -> sqlite3.Row | None:
    return connection.execute(
        """SELECT * FROM validation_registry
        WHERE rule_identifier = ? AND rule_version = ?
          AND applicable_workload_scope IN (?, '*')
        ORDER BY CASE applicable_workload_scope WHEN ? THEN 0 ELSE 1 END,
                 validation_date_utc DESC, id DESC LIMIT 1""",
        (rule_identifier, rule_version, workload_scope or "*", workload_scope or "*"),
    ).fetchone()


def latest_outcome(
    connection: sqlite3.Connection,
    occurrence_id: int,
) -> dict[str, Any] | None:
    row = connection.execute(
        """SELECT * FROM alert_outcome_events WHERE alert_occurrence_id = ?
        ORDER BY event_timestamp_utc DESC, id DESC LIMIT 1""",
        (occurrence_id,),
    ).fetchone()
    return dict(row) if row else None


def append_outcome(
    connection: sqlite3.Connection,
    occurrence_id: int,
    new_outcome: str,
    *,
    note: str | None,
    reason: str,
) -> dict[str, Any]:
    allowed = {"pending", "confirmed", "false_positive", "inconclusive"}
    if new_outcome not in allowed:
        raise ValueError("Unsupported alert outcome.")
    if connection.execute(
        "SELECT 1 FROM alert_occurrences WHERE id = ?", (occurrence_id,)
    ).fetchone() is None:
        raise ValueError("Alert occurrence does not exist.")
    previous = latest_outcome(connection, occurrence_id)
    if previous and previous["new_outcome"] == new_outcome and (
        previous.get("optional_note") or None
    ) == (note or None):
        return {**previous, "changed": False}
    timestamp = _now()
    cursor = connection.execute(
        """INSERT INTO alert_outcome_events (
        alert_occurrence_id, previous_outcome, new_outcome,
        event_timestamp_utc, optional_note, audit_reason, created_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            occurrence_id,
            previous["new_outcome"] if previous else None,
            new_outcome, timestamp, note, reason, timestamp,
        ),
    )
    return {
        "id": int(cursor.lastrowid),
        "alert_occurrence_id": occurrence_id,
        "previous_outcome": previous["new_outcome"] if previous else None,
        "new_outcome": new_outcome,
        "event_timestamp_utc": timestamp,
        "optional_note": note,
        "audit_reason": reason,
        "changed": True,
    }
