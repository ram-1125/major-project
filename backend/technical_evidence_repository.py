"""Bounded, read-only access to advanced SmartOps evidence.

The allowlist prevents caller-controlled table or column names from reaching
SQLite. Audit Records deliberately remain outside this catalogue in Settings.
"""

from __future__ import annotations

import sqlite3
from typing import Any


DATASETS: dict[str, dict[str, Any]] = {
    "monitoring": {
        "table": "metrics", "order": "timestamp_utc", "context": "id", "search": ["id", "device_id", "workload_class", "foreground_process_name", "timestamp_utc"],
        "label": "Raw telemetry", "description": "Recorded 30-second local system samples.",
    },
    "monitoring-processes": {
        "table": "process_snapshots", "order": "metric_id", "search": ["id", "metric_id", "process_name", "category", "pid"],
        "label": "Process snapshots", "description": "Bounded top-process rows linked to raw metric samples.",
    },
    "advanced-signals": {
        "table": "enhanced_signal_samples", "order": "timestamp_utc", "search": ["signal_group", "signal_key", "availability_status", "reason_code", "source_name"],
        "label": "Advanced Signal provenance", "description": "Shadow-only signal values, availability, source, frequency, and structured details.",
    },
    "windows-events": {
        "table": "windows_events", "order": "event_timestamp_utc", "search": ["provider_name", "event_level", "smartops_category", "safe_summary"],
        "label": "Windows events", "description": "All safely mapped Windows event metadata retained locally.",
    },
    "alerts": {
        "table": "alert_occurrences", "order": "observed_at_utc", "context": "alert_id", "search": ["id", "alert_id", "feature_window_id", "severity", "temporal_pattern", "trend_direction"],
        "label": "Alert occurrences", "description": "Material and recovery observations retained for lifecycle audit.",
    },
    "alert-evidence": {
        "table": "alert_evidence", "order": "id", "search": ["id", "occurrence_id", "source_id", "evidence_key", "correlation_group", "reason_code", "explanation"],
        "context": "occurrence_id IN (SELECT id FROM alert_occurrences WHERE alert_id = ?)",
        "label": "Alert evidence", "description": "Exact raw/effective alert evidence and suppression decisions.",
    },
    "alert-transitions": {
        "table": "alert_state_transitions", "order": "transition_timestamp_utc", "search": ["transition_type", "previous_state", "new_state", "reason_code"],
        "context": "alert_id = ?",
        "label": "Alert transitions", "description": "Complete lifecycle transition history.",
    },
    "notification-deliveries": {
        "table": "notification_deliveries", "order": "attempted_at_utc", "search": ["severity", "notification_type", "delivery_status", "provider_name", "notification_category"],
        "context": "alert_id = ?",
        "label": "Notification delivery", "description": "Genuine predictive-notification delivery audit; test and enable-confirmation toasts are excluded.",
    },
    "root-causes": {
        "table": "root_cause_candidates", "order": "id", "context": "risk_assessment_id", "search": ["id", "risk_assessment_id", "candidate_domain", "explanation", "workload_context"],
        "label": "Root-cause evidence", "description": "Ranked stored hypotheses and their evidence references.",
    },
    "root-cause-evidence": {
        "table": "root_cause_candidate_evidence", "order": "id", "search": ["evidence_kind", "evidence_key", "reason_code"],
        "context": "candidate_id IN (SELECT id FROM root_cause_candidates WHERE risk_assessment_id = ?)",
        "label": "Root-cause relationships", "description": "Stored supporting and contradicting evidence for ranked candidates.",
    },
    "system-health": {
        "table": "health_assessments", "order": "assessed_at_utc", "search": ["health_band", "evaluation_state", "workload_context"],
        "label": "Health reconstruction", "description": "Versioned System Health assessments and normalization inputs.",
    },
    "health-components": {
        "table": "health_component_scores", "order": "id", "search": ["component_name", "data_quality_status", "reason_codes_json"],
        "label": "Health components", "description": "Component weights and reconstructable deduction totals.",
    },
    "health-deductions": {
        "table": "health_deductions", "order": "id", "search": ["component_name", "signal_name", "reason_code", "explanation"],
        "label": "Health deductions", "description": "Raw and effective capped System Health contributions.",
    },
    "pc-quality": {
        "table": "fine_quality_assessments", "order": "assessed_at_utc", "search": ["profile_key", "profile_name", "evaluation_state"],
        "label": "PC Quality calculations", "description": "Fine-profile operating-headroom assessment records.",
    },
    "pc-quality-contributions": {
        "table": "fine_quality_metric_contributions", "order": "id", "search": ["metric_key", "metric_label", "availability_status", "explanation"],
        "label": "PC Quality contributions", "description": "Per-metric operating-headroom calculations.",
    },
    "personal-baseline": {
        "table": "baseline_version_profiles", "order": "updated_at_utc", "search": ["workload_scope", "readiness_state"],
        "label": "Personal Baseline", "description": "Versioned profile readiness and learning provenance.",
    },
    "baseline-membership": {
        "table": "baseline_training_membership_events", "order": "event_timestamp_utc", "search": ["previous_state", "new_state", "event_type", "reason_codes_json", "eligibility_rule_version"],
        "label": "Baseline membership audit", "description": "Append-only accepted, rejected, removed, and reaccepted membership history.",
    },
    "analytical-records": {
        "table": "feature_windows", "order": "window_end_utc", "search": ["device_id", "dominant_workload_class", "finalization_state"],
        "label": "Five-minute analysis periods", "description": "Finalised feature windows and source provenance.",
    },
    "validation-decisions": {
        "table": "validation_inclusion_exclusion", "order": "id",
        "search": ["evidence_type", "classification", "reason_codes_json", "details_json"],
        "label": "Validation decisions", "description": "Included and excluded evidence with exact reconstruction details.",
    },
    "validation-matches": {
        "table": "validation_match_events", "order": "event_timestamp_utc",
        "search": ["incident_id", "alert_id", "new_decision", "decision_basis_json", "method_version"],
        "label": "Incident matching audit", "description": "Append-only automatic and user-reviewed incident-to-alert decisions.",
    },
    "validation-metrics": {
        "table": "validation_metric_results", "order": "id",
        "search": ["metric_name", "evaluation_state", "maturity_label", "reconstruction_json"],
        "label": "Validation calculations", "description": "Exact numerators, denominators, intervals and reconstruction records.",
    },
}


def catalogue() -> list[dict[str, str]]:
    return [
        {"key": key, "label": value["label"], "description": value["description"]}
        for key, value in DATASETS.items()
    ]


def list_technical_evidence(
    connection: sqlite3.Connection,
    dataset: str,
    *,
    limit: int,
    offset: int,
    search: str | None,
    sort: str,
    context_id: int | None = None,
) -> tuple[list[dict[str, Any]], int]:
    config = DATASETS.get(dataset)
    if config is None:
        raise ValueError("Unknown technical evidence dataset.")
    table = config["table"]
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is None:
        return [], 0
    clauses: list[str] = []
    values: list[Any] = []
    if context_id is not None:
        context_clause = config.get("context")
        if context_clause is None:
            raise ValueError("This technical dataset does not support an exact context link.")
        # Context clauses are fixed in the server-side allowlist above. Caller
        # input is always bound as a value and can never become SQL syntax.
        clauses.append(context_clause if "?" in context_clause else f"{context_clause} = ?")
        values.append(context_id)
    if search and search.strip():
        escaped = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        clauses.append("(" + " OR ".join(
            f"CAST({column} AS TEXT) LIKE ? ESCAPE '\\' COLLATE NOCASE"
            for column in config["search"]
        ) + ")")
        values.extend([pattern] * len(config["search"]))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    total = int(connection.execute(f"SELECT COUNT(*) FROM {table} {where}", values).fetchone()[0])
    direction = "ASC" if sort == "oldest" else "DESC"
    rows = connection.execute(
        f"SELECT * FROM {table} {where} ORDER BY {config['order']} {direction}, id {direction} LIMIT ? OFFSET ?",
        (*values, limit, offset),
    ).fetchall()
    return [dict(row) for row in rows], total
