"""Schema and persistence helpers for the Phase 7B.1 stability upgrade.

The migration is additive. Existing telemetry, assessments, alerts, notification
deliveries, and baseline rows are never deleted or rewritten.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

import psutil


SCHEMA_VERSION = 16


SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS notification_decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        alert_id INTEGER NOT NULL,
        lifecycle_number INTEGER NOT NULL,
        alert_severity TEXT NOT NULL,
        notification_category TEXT,
        decision_type TEXT NOT NULL,
        decision_reason TEXT NOT NULL,
        preference_revision INTEGER NOT NULL,
        transition_type TEXT NOT NULL,
        decided_at_utc TEXT NOT NULL,
        details_json TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY(alert_id) REFERENCES alerts(id) ON DELETE CASCADE,
        UNIQUE(
            alert_id, lifecycle_number, alert_severity,
            transition_type, preference_revision
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_runtime_sessions (
        session_id TEXT PRIMARY KEY,
        process_id INTEGER NOT NULL,
        started_at_utc TEXT NOT NULL,
        heartbeat_at_utc TEXT NOT NULL,
        stopped_at_utc TEXT,
        status TEXT NOT NULL,
        stop_reason TEXT,
        CHECK(status IN ('running', 'stopped', 'abandoned'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS baseline_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id TEXT NOT NULL,
        version_number INTEGER NOT NULL,
        version_label TEXT NOT NULL,
        lifecycle_state TEXT NOT NULL,
        algorithm_version TEXT NOT NULL,
        configuration_version TEXT NOT NULL,
        previous_version_id INTEGER,
        created_at_utc TEXT NOT NULL,
        learning_started_at_utc TEXT,
        paused_at_utc TEXT,
        completed_at_utc TEXT,
        activated_at_utc TEXT,
        cancelled_at_utc TEXT,
        last_learning_at_utc TEXT,
        reason_codes_json TEXT NOT NULL DEFAULT '[]',
        FOREIGN KEY(previous_version_id) REFERENCES baseline_versions(id),
        UNIQUE(device_id, version_number),
        CHECK(lifecycle_state IN (
            'active', 'candidate', 'paused', 'ready', 'cancelled', 'archived'
        ))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS baseline_version_profiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        baseline_version_id INTEGER NOT NULL,
        workload_scope TEXT NOT NULL,
        legacy_baseline_id INTEGER,
        observed_window_count INTEGER NOT NULL DEFAULT 0,
        eligible_window_count INTEGER NOT NULL DEFAULT 0,
        excluded_window_count INTEGER NOT NULL DEFAULT 0,
        distinct_day_count INTEGER NOT NULL DEFAULT 0,
        sampling_completeness REAL,
        readiness_state TEXT NOT NULL,
        training_start_utc TEXT,
        training_end_utc TEXT,
        last_learning_at_utc TEXT,
        reason_codes_json TEXT NOT NULL DEFAULT '[]',
        feature_names_json TEXT NOT NULL DEFAULT '[]',
        missing_features_json TEXT NOT NULL DEFAULT '[]',
        created_at_utc TEXT NOT NULL,
        updated_at_utc TEXT NOT NULL,
        FOREIGN KEY(baseline_version_id)
            REFERENCES baseline_versions(id) ON DELETE CASCADE,
        FOREIGN KEY(legacy_baseline_id) REFERENCES baseline_profiles(id),
        UNIQUE(baseline_version_id, workload_scope)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS baseline_version_feature_stats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        version_profile_id INTEGER NOT NULL,
        feature_name TEXT NOT NULL,
        direction TEXT NOT NULL,
        valid_count INTEGER NOT NULL,
        missing_count INTEGER NOT NULL,
        missing_ratio REAL NOT NULL,
        mean REAL,
        population_stddev REAL,
        median REAL,
        median_absolute_deviation REAL,
        minimum REAL,
        maximum REAL,
        percentile_05 REAL,
        percentile_25 REAL,
        percentile_75 REAL,
        percentile_95 REAL,
        interquartile_range REAL,
        FOREIGN KEY(version_profile_id)
            REFERENCES baseline_version_profiles(id) ON DELETE CASCADE,
        UNIQUE(version_profile_id, feature_name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS baseline_version_training_windows (
        version_profile_id INTEGER NOT NULL,
        feature_window_id INTEGER NOT NULL,
        PRIMARY KEY(version_profile_id, feature_window_id),
        FOREIGN KEY(version_profile_id)
            REFERENCES baseline_version_profiles(id) ON DELETE CASCADE,
        FOREIGN KEY(feature_window_id) REFERENCES feature_windows(id)
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE IF NOT EXISTS baseline_recalibration_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        baseline_version_id INTEGER NOT NULL,
        event_type TEXT NOT NULL,
        event_timestamp_utc TEXT NOT NULL,
        previous_state TEXT,
        new_state TEXT NOT NULL,
        reason_code TEXT NOT NULL,
        details_json TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY(baseline_version_id)
            REFERENCES baseline_versions(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS prevent_multiple_running_agent_inserts
    BEFORE INSERT ON agent_runtime_sessions
    WHEN NEW.status = 'running' AND EXISTS (
        SELECT 1 FROM agent_runtime_sessions WHERE status = 'running'
    )
    BEGIN
        SELECT RAISE(ABORT, 'another SmartOps agent session is active');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS prevent_multiple_running_agent_updates
    BEFORE UPDATE OF status ON agent_runtime_sessions
    WHEN NEW.status = 'running' AND OLD.status != 'running' AND EXISTS (
        SELECT 1 FROM agent_runtime_sessions
        WHERE status = 'running' AND session_id != NEW.session_id
    )
    BEGIN
        SELECT RAISE(ABORT, 'another SmartOps agent session is active');
    END
    """,
)


INDEX_STATEMENTS = (
    """CREATE INDEX IF NOT EXISTS idx_notification_decisions_alert
       ON notification_decisions(alert_id, lifecycle_number, decided_at_utc DESC)""",
    """CREATE INDEX IF NOT EXISTS idx_agent_sessions_status_heartbeat
       ON agent_runtime_sessions(status, heartbeat_at_utc DESC)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_baseline_one_active_version
       ON baseline_versions(device_id) WHERE lifecycle_state = 'active'""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_baseline_one_candidate_version
       ON baseline_versions(device_id)
       WHERE lifecycle_state IN ('candidate', 'paused', 'ready')""",
    """CREATE INDEX IF NOT EXISTS idx_baseline_version_profiles_state
       ON baseline_version_profiles(baseline_version_id, readiness_state)""",
    """CREATE INDEX IF NOT EXISTS idx_baseline_recalibration_events_version
       ON baseline_recalibration_events(baseline_version_id, event_timestamp_utc)""",
    """CREATE INDEX IF NOT EXISTS idx_alert_occurrences_alert_signature
       ON alert_occurrences(alert_id, evidence_signature)""",
    """CREATE INDEX IF NOT EXISTS idx_feature_windows_secondary_workload
       ON feature_windows(device_id, secondary_workload_context, window_start_utc)""",
)


ADDITIONAL_COLUMNS: dict[str, dict[str, str]] = {
    "metrics": {
        "user_activity_state": "TEXT",
        "system_activity_state": "TEXT",
        "workload_rule_version": "TEXT",
        "workload_provenance_json": "TEXT",
    },
    "feature_windows": {
        "dominant_user_activity_state": "TEXT",
        "dominant_system_activity_state": "TEXT",
        "workload_rule_version": "TEXT",
        "workload_distribution_json": "TEXT",
        "workload_majority_explanation": "TEXT",
        "workload_composition_json": "TEXT",
        "secondary_workload_context": "TEXT",
        "secondary_workload_rule_version": "TEXT",
        "secondary_workload_reason_codes_json": "TEXT",
    },
    "baseline_version_profiles": {
        "applicability_state": "TEXT",
        "applicability_reasons_json": "TEXT NOT NULL DEFAULT '[]'",
        "exclusion_reason_counts_json": "TEXT NOT NULL DEFAULT '{}'",
    },
    "baseline_versions": {
        "learning_state": "TEXT",
    },
    "notification_preferences": {
        "eligible_categories_json": "TEXT",
        "preference_revision": "INTEGER NOT NULL DEFAULT 1",
    },
    "notification_deliveries": {
        "notification_category": "TEXT",
    },
    "alerts": {
        "observation_count": "INTEGER NOT NULL DEFAULT 0",
        "last_seen_at_utc": "TEXT",
        "last_material_evidence_signature": "TEXT",
        "baseline_version_id": "INTEGER REFERENCES baseline_versions(id)",
        "baseline_rule_version": "TEXT",
        "workload_rule_version": "TEXT",
    },
    "deviation_assessments": {
        "baseline_version_id": "INTEGER REFERENCES baseline_versions(id)",
        "baseline_rule_version": "TEXT",
        "workload_rule_version": "TEXT",
    },
    "risk_assessments": {
        "baseline_version_id": "INTEGER REFERENCES baseline_versions(id)",
        "baseline_rule_version": "TEXT",
        "workload_rule_version": "TEXT",
    },
    "health_assessments": {
        "baseline_version_id": "INTEGER REFERENCES baseline_versions(id)",
        "baseline_rule_version": "TEXT",
        "workload_rule_version": "TEXT",
    },
    "enhanced_collection_runs": {
        "scheduled_at_utc": "TEXT",
        "schedule_delay_seconds": "REAL",
        "agent_session_id": "TEXT",
    },
    "enhanced_collector_state": {
        "last_scheduled_utc": "TEXT",
        "last_schedule_delay_seconds": "REAL",
        "last_collection_duration_ms": "REAL",
        "last_gap_classification": "TEXT",
        "last_agent_session_id": "TEXT",
    },
}


SEVERITY_TO_CATEGORY = {
    "informational": "advisory",
    "advisory": "advisory",
    "warning": "warning",
    "urgent": "urgent",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _migrate_notification_categories(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        """SELECT id, eligible_severities_json, eligible_categories_json
        FROM notification_preferences"""
    ).fetchall()
    for row in rows:
        if row[2] is not None:
            continue
        try:
            severities = json.loads(row[1] or "[]")
        except (TypeError, json.JSONDecodeError):
            severities = []
        categories = list(dict.fromkeys(
            category
            for severity in severities
            if (category := SEVERITY_TO_CATEGORY.get(str(severity))) is not None
        ))
        connection.execute(
            "UPDATE notification_preferences SET eligible_categories_json = ? WHERE id = ?",
            (json.dumps(categories), row[0]),
        )
    connection.execute(
        """UPDATE notification_deliveries
        SET notification_category = CASE severity
            WHEN 'informational' THEN 'advisory'
            WHEN 'advisory' THEN 'advisory'
            WHEN 'warning' THEN 'warning'
            WHEN 'urgent' THEN 'urgent'
            ELSE NULL END
        WHERE notification_category IS NULL"""
    )


def _snapshot_legacy_baselines(connection: sqlite3.Connection) -> None:
    """Create immutable version-1 snapshots without changing legacy profiles."""
    devices = [
        row[0]
        for row in connection.execute(
            "SELECT DISTINCT device_id FROM baseline_profiles ORDER BY device_id"
        )
    ]
    for device_id in devices:
        existing = connection.execute(
            "SELECT id FROM baseline_versions WHERE device_id = ? AND version_number = 1",
            (device_id,),
        ).fetchone()
        if existing:
            version_id = int(existing[0])
        else:
            timestamp = _now()
            cursor = connection.execute(
                """INSERT INTO baseline_versions (
                device_id, version_number, version_label, lifecycle_state,
                algorithm_version, configuration_version, created_at_utc,
                completed_at_utc, activated_at_utc, last_learning_at_utc,
                reason_codes_json
                ) VALUES (?, 1, 'Original established baseline', 'active',
                'robust-baseline-v1', 'phase3a-v1', ?, ?, ?, ?, '[]')""",
                (device_id, timestamp, timestamp, timestamp, timestamp),
            )
            version_id = int(cursor.lastrowid)

        for profile in connection.execute(
            "SELECT * FROM baseline_profiles WHERE device_id = ? ORDER BY workload_scope",
            (device_id,),
        ).fetchall():
            cursor = connection.execute(
                """INSERT OR IGNORE INTO baseline_version_profiles (
                baseline_version_id, workload_scope, legacy_baseline_id,
                observed_window_count, eligible_window_count, excluded_window_count,
                distinct_day_count, sampling_completeness, readiness_state,
                training_start_utc, training_end_utc, last_learning_at_utc,
                reason_codes_json, feature_names_json, missing_features_json,
                created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1.0, ?, ?, ?, ?, '[]', ?, ?, ?, ?)""",
                (
                    version_id, profile["workload_scope"], profile["id"],
                    profile["eligible_window_count"], profile["eligible_window_count"],
                    profile["excluded_window_count"], profile["distinct_day_count"],
                    profile["readiness_state"], profile["training_start_utc"],
                    profile["training_end_utc"], profile["updated_at_utc"],
                    profile["feature_names_json"], profile["missing_features_json"],
                    profile["created_at_utc"], profile["updated_at_utc"],
                ),
            )
            version_profile = connection.execute(
                """SELECT id FROM baseline_version_profiles
                WHERE baseline_version_id = ? AND workload_scope = ?""",
                (version_id, profile["workload_scope"]),
            ).fetchone()[0]
            if cursor.rowcount:
                stats = connection.execute(
                    "SELECT * FROM baseline_feature_stats WHERE baseline_id = ?",
                    (profile["id"],),
                ).fetchall()
                for stat in stats:
                    columns = [
                        "feature_name", "direction", "valid_count", "missing_count",
                        "missing_ratio", "mean", "population_stddev", "median",
                        "median_absolute_deviation", "minimum", "maximum",
                        "percentile_05", "percentile_25", "percentile_75",
                        "percentile_95", "interquartile_range",
                    ]
                    connection.execute(
                        f"""INSERT INTO baseline_version_feature_stats
                        (version_profile_id, {', '.join(columns)})
                        VALUES (?, {', '.join('?' for _ in columns)})""",
                        (version_profile, *(stat[column] for column in columns)),
                    )
                connection.execute(
                    """INSERT OR IGNORE INTO baseline_version_training_windows
                    (version_profile_id, feature_window_id)
                    SELECT ?, feature_window_id FROM baseline_training_windows
                    WHERE baseline_id = ?""",
                    (version_profile, profile["id"]),
                )

        connection.execute(
            """UPDATE deviation_assessments
            SET baseline_version_id = ?, baseline_rule_version = 'phase3a-v1'
            WHERE device_id = ? AND baseline_version_id IS NULL""",
            (version_id, device_id),
        )
        connection.execute(
            """UPDATE risk_assessments
            SET baseline_version_id = ?, baseline_rule_version = 'phase3a-v1'
            WHERE device_id = ? AND baseline_version_id IS NULL""",
            (version_id, device_id),
        )
        connection.execute(
            """UPDATE health_assessments
            SET baseline_version_id = ?, baseline_rule_version = 'phase3a-v1'
            WHERE device_id = ? AND baseline_version_id IS NULL""",
            (version_id, device_id),
        )
        connection.execute(
            """UPDATE alerts
            SET baseline_version_id = ?, baseline_rule_version = 'phase3a-v1'
            WHERE device_id = ? AND baseline_version_id IS NULL""",
            (version_id, device_id),
        )


def finish_phase7b1_migration(connection: sqlite3.Connection) -> None:
    _migrate_notification_categories(connection)
    connection.execute(
        """UPDATE alerts SET
        observation_count = CASE
            WHEN observation_count = 0 THEN occurrence_count ELSE observation_count END,
        last_seen_at_utc = COALESCE(last_seen_at_utc, latest_observed_utc)"""
    )
    _snapshot_legacy_baselines(connection)
    connection.execute(
        """UPDATE baseline_version_profiles
        SET applicability_state = CASE
            WHEN observed_window_count = 0 THEN 'not_observed'
            WHEN readiness_state IN ('ready', 'established') THEN 'applicable'
            ELSE 'observed_insufficient'
        END
        WHERE applicability_state IS NULL"""
    )
    # Schema 15 separates an immutable candidate lifecycle from whether the
    # user currently wants that candidate to keep learning.  Existing ready
    # candidates deliberately stop at ready-for-validation during migration;
    # the user must explicitly choose Continue Calibration.
    connection.execute(
        """UPDATE baseline_versions SET learning_state = CASE
            WHEN lifecycle_state = 'candidate' THEN 'collecting'
            WHEN lifecycle_state = 'paused' THEN 'paused'
            WHEN lifecycle_state = 'ready' THEN 'ready_for_validation'
            ELSE 'inactive'
        END
        WHERE learning_state IS NULL"""
    )


def ensure_versioned_baseline_snapshot(
    connection: sqlite3.Connection,
    device_id: str | None = None,
) -> None:
    """Snapshot a newly-created legacy baseline without mutating older snapshots."""
    _snapshot_legacy_baselines(connection)


def record_notification_decision(
    connection: sqlite3.Connection,
    *,
    alert_id: int,
    lifecycle_number: int,
    alert_severity: str,
    notification_category: str | None,
    decision_type: str,
    decision_reason: str,
    preference_revision: int,
    transition_type: str,
    details: dict[str, Any] | None = None,
) -> bool:
    cursor = connection.execute(
        """INSERT OR IGNORE INTO notification_decisions (
        alert_id, lifecycle_number, alert_severity, notification_category,
        decision_type, decision_reason, preference_revision, transition_type,
        decided_at_utc, details_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            alert_id, lifecycle_number, alert_severity, notification_category,
            decision_type, decision_reason, preference_revision, transition_type,
            _now(), json.dumps(details or {}, sort_keys=True, separators=(",", ":")),
        ),
    )
    return bool(cursor.rowcount)


def claim_agent_session(
    connection: sqlite3.Connection,
    *,
    session_id: str,
    process_id: int,
    started_at_utc: datetime,
    stale_after_seconds: int = 120,
) -> None:
    """Claim the single automatic agent role, recovering only stale sessions."""
    cutoff = (started_at_utc - timedelta(seconds=stale_after_seconds)).isoformat()
    live = connection.execute(
        """SELECT session_id, process_id, heartbeat_at_utc
        FROM agent_runtime_sessions
        WHERE status = 'running' AND heartbeat_at_utc >= ?
        ORDER BY heartbeat_at_utc DESC LIMIT 1""",
        (cutoff,),
    ).fetchone()
    if live and not psutil.pid_exists(int(live["process_id"])):
        connection.execute(
            """UPDATE agent_runtime_sessions
            SET status = 'abandoned', stopped_at_utc = ?,
                stop_reason = 'owner_process_no_longer_running'
            WHERE session_id = ?""",
            (started_at_utc.isoformat(), live["session_id"]),
        )
        live = None
    if live and live["session_id"] != session_id:
        raise RuntimeError(
            "Another SmartOps agent session is already active "
            f"(process {live['process_id']})."
        )
    connection.execute(
        """UPDATE agent_runtime_sessions
        SET status = 'abandoned', stopped_at_utc = ?,
            stop_reason = 'stale_session_replaced'
        WHERE status = 'running' AND heartbeat_at_utc < ?""",
        (started_at_utc.isoformat(), cutoff),
    )
    try:
        connection.execute(
            """INSERT INTO agent_runtime_sessions (
            session_id, process_id, started_at_utc, heartbeat_at_utc, status
            ) VALUES (?, ?, ?, ?, 'running')""",
            (
                session_id,
                process_id,
                started_at_utc.isoformat(),
                started_at_utc.isoformat(),
            ),
        )
    except sqlite3.IntegrityError as error:
        # The schema-16 trigger closes the cross-process race between the
        # live-session SELECT and this INSERT without rewriting old sessions.
        raise RuntimeError(
            "Another SmartOps agent session became active while claiming ownership."
        ) from error


def heartbeat_agent_session(
    connection: sqlite3.Connection,
    session_id: str,
    timestamp_utc: datetime,
) -> None:
    connection.execute(
        """UPDATE agent_runtime_sessions SET heartbeat_at_utc = ?
        WHERE session_id = ? AND status = 'running'""",
        (timestamp_utc.isoformat(), session_id),
    )


def close_orphaned_agent_sessions(
    connection: sqlite3.Connection,
    timestamp_utc: datetime,
) -> int:
    """Close durable ownership rows after the supervisor has stopped children."""
    closed = 0
    for row in connection.execute(
        "SELECT session_id, process_id FROM agent_runtime_sessions WHERE status='running'"
    ).fetchall():
        if not psutil.pid_exists(int(row["process_id"])):
            connection.execute(
                """UPDATE agent_runtime_sessions SET status='stopped',
                stopped_at_utc=?, stop_reason='supervisor_shutdown'
                WHERE session_id=? AND status='running'""",
                (timestamp_utc.isoformat(), row["session_id"]),
            )
            closed += 1
    return closed


def close_agent_session(
    connection: sqlite3.Connection,
    session_id: str,
    timestamp_utc: datetime,
    reason: str,
) -> None:
    connection.execute(
        """UPDATE agent_runtime_sessions
        SET heartbeat_at_utc = ?, stopped_at_utc = ?, status = 'stopped',
            stop_reason = ?
        WHERE session_id = ? AND status = 'running'""",
        (timestamp_utc.isoformat(), timestamp_utc.isoformat(), reason, session_id),
    )
