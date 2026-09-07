"""Schema 17 persistence for authoritative windows and operational audit.

All structures are additive.  Existing raw telemetry, feature rows, candidate
identity, assessments, alerts, and notification records are preserved.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any


SCHEMA_VERSION = 17
AGGREGATION_RULE_VERSION = "authoritative-window-v1"
ELIGIBILITY_RULE_VERSION = "phase7b2-membership-v1"
CYCLE_AUDIT_RETENTION_DAYS = 30


SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS feature_window_repair_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        feature_window_id INTEGER NOT NULL,
        repaired_at_utc TEXT NOT NULL,
        repair_reason TEXT NOT NULL,
        aggregation_rule_version TEXT NOT NULL,
        old_semantic_hash TEXT NOT NULL,
        new_semantic_hash TEXT NOT NULL,
        old_values_json TEXT NOT NULL,
        new_values_json TEXT NOT NULL,
        source_sample_count INTEGER NOT NULL,
        source_first_metric_id INTEGER,
        source_last_metric_id INTEGER,
        source_first_timestamp_utc TEXT,
        source_last_timestamp_utc TEXT,
        details_json TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY(feature_window_id) REFERENCES feature_windows(id),
        UNIQUE(feature_window_id, old_semantic_hash, new_semantic_hash, repair_reason)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS baseline_training_membership_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        baseline_version_id INTEGER NOT NULL,
        version_profile_id INTEGER NOT NULL,
        feature_window_id INTEGER NOT NULL,
        previous_state TEXT NOT NULL,
        new_state TEXT NOT NULL,
        event_type TEXT NOT NULL,
        eligibility_rule_version TEXT NOT NULL,
        reason_codes_json TEXT NOT NULL,
        event_timestamp_utc TEXT NOT NULL,
        details_json TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY(baseline_version_id) REFERENCES baseline_versions(id),
        FOREIGN KEY(version_profile_id)
            REFERENCES baseline_version_profiles(id) ON DELETE CASCADE,
        FOREIGN KEY(feature_window_id) REFERENCES feature_windows(id),
        CHECK(previous_state IN ('unknown', 'accepted', 'rejected', 'removed')),
        CHECK(new_state IN ('accepted', 'rejected', 'removed')),
        CHECK(event_type IN ('accepted', 'rejected', 'removed_after_correction',
                             'reaccepted_after_repair')),
        UNIQUE(version_profile_id, feature_window_id, previous_state, new_state,
               eligibility_rule_version, reason_codes_json)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS collection_cycle_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        runtime_session_id TEXT,
        scheduled_at_utc TEXT NOT NULL,
        collection_started_at_utc TEXT,
        collection_completed_at_utc TEXT,
        collection_duration_ms REAL,
        metric_id INTEGER,
        enhanced_run_id INTEGER,
        schedule_delay_seconds REAL,
        status TEXT NOT NULL,
        enhanced_status TEXT NOT NULL DEFAULT 'not_requested',
        reason_code TEXT,
        collector_failure_category TEXT,
        sqlite_retry_count INTEGER NOT NULL DEFAULT 0,
        lock_category TEXT,
        details_json TEXT NOT NULL DEFAULT '{}',
        created_at_utc TEXT NOT NULL,
        updated_at_utc TEXT NOT NULL,
        FOREIGN KEY(runtime_session_id) REFERENCES agent_runtime_sessions(session_id),
        FOREIGN KEY(metric_id) REFERENCES metrics(id),
        FOREIGN KEY(enhanced_run_id) REFERENCES enhanced_collection_runs(id),
        UNIQUE(runtime_session_id, scheduled_at_utc),
        CHECK(status IN ('started', 'stored', 'failed', 'skipped')),
        CHECK(enhanced_status IN ('not_requested', 'pending', 'running',
                                  'completed', 'partial', 'failed', 'coalesced'))
    )
    """,
)


ADDITIONAL_COLUMNS = {
    "feature_windows": {
        "source_sample_count": "INTEGER",
        "source_first_metric_id": "INTEGER",
        "source_last_metric_id": "INTEGER",
        "source_first_timestamp_utc": "TEXT",
        "source_last_timestamp_utc": "TEXT",
        "source_max_internal_gap_seconds": "REAL",
        "aggregation_rule_version": "TEXT",
        "finalization_state": "TEXT",
        "finalized_at_utc": "TEXT",
        "corrected_at_utc": "TEXT",
        "semantic_hash": "TEXT",
    },
}


INDEX_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS idx_feature_repair_window_time "
    "ON feature_window_repair_events(feature_window_id, repaired_at_utc)",
    "CREATE INDEX IF NOT EXISTS idx_membership_events_profile_time "
    "ON baseline_training_membership_events(version_profile_id, event_timestamp_utc)",
    "CREATE INDEX IF NOT EXISTS idx_membership_events_window "
    "ON baseline_training_membership_events(feature_window_id, baseline_version_id)",
    "CREATE INDEX IF NOT EXISTS idx_cycle_audit_session_schedule "
    "ON collection_cycle_audit(runtime_session_id, scheduled_at_utc)",
    "CREATE INDEX IF NOT EXISTS idx_cycle_audit_status_time "
    "ON collection_cycle_audit(status, scheduled_at_utc)",
)


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def record_cycle_start(
    connection: sqlite3.Connection,
    *,
    runtime_session_id: str | None,
    scheduled_at_utc: str,
    started_at_utc: str,
    delay_seconds: float,
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    connection.execute(
        """INSERT INTO collection_cycle_audit (
        runtime_session_id, scheduled_at_utc, collection_started_at_utc,
        schedule_delay_seconds, status, enhanced_status, created_at_utc,
        updated_at_utc) VALUES (?, ?, ?, ?, 'started', 'not_requested', ?, ?)
        ON CONFLICT(runtime_session_id, scheduled_at_utc) DO UPDATE SET
        collection_started_at_utc=excluded.collection_started_at_utc,
        schedule_delay_seconds=excluded.schedule_delay_seconds,
        status='started', updated_at_utc=excluded.updated_at_utc""",
        (runtime_session_id, scheduled_at_utc, started_at_utc, delay_seconds, now, now),
    )
    return int(connection.execute(
        "SELECT id FROM collection_cycle_audit WHERE runtime_session_id=? AND scheduled_at_utc=?",
        (runtime_session_id, scheduled_at_utc),
    ).fetchone()[0])


def complete_cycle(
    connection: sqlite3.Connection,
    cycle_id: int,
    *,
    metric_id: int,
    completed_at_utc: str,
    duration_ms: float,
    enhanced_status: str = "pending",
) -> None:
    connection.execute(
        """UPDATE collection_cycle_audit SET metric_id=?, collection_completed_at_utc=?,
        collection_duration_ms=?, status='stored', enhanced_status=?, updated_at_utc=?
        WHERE id=?""",
        (metric_id, completed_at_utc, duration_ms, enhanced_status,
         datetime.now(timezone.utc).isoformat(), cycle_id),
    )


def record_cycle_outcome(
    connection: sqlite3.Connection,
    *,
    runtime_session_id: str,
    scheduled_at_utc: str,
    status: str,
    reason_code: str,
    failure_category: str | None = None,
    sqlite_retry_count: int = 0,
    lock_category: str | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    connection.execute(
        """INSERT INTO collection_cycle_audit (
        runtime_session_id,scheduled_at_utc,status,enhanced_status,reason_code,
        collector_failure_category,sqlite_retry_count,lock_category,
        created_at_utc,updated_at_utc)
        VALUES (?, ?, ?, 'not_requested', ?, ?, ?, ?, ?, ?)
        ON CONFLICT(runtime_session_id,scheduled_at_utc) DO UPDATE SET
        status=excluded.status,reason_code=excluded.reason_code,
        collector_failure_category=excluded.collector_failure_category,
        sqlite_retry_count=excluded.sqlite_retry_count,
        lock_category=excluded.lock_category,updated_at_utc=excluded.updated_at_utc""",
        (runtime_session_id, scheduled_at_utc, status, reason_code,
         failure_category, sqlite_retry_count, lock_category, now, now),
    )


def update_cycle_enhanced(
    connection: sqlite3.Connection,
    cycle_id: int,
    status: str,
    *,
    enhanced_run_id: int | None = None,
    reason_code: str | None = None,
) -> None:
    connection.execute(
        """UPDATE collection_cycle_audit SET enhanced_status=?,
        enhanced_run_id=COALESCE(?, enhanced_run_id), reason_code=COALESCE(?, reason_code),
        updated_at_utc=? WHERE id=?""",
        (status, enhanced_run_id, reason_code, datetime.now(timezone.utc).isoformat(), cycle_id),
    )


def prune_cycle_audit(connection: sqlite3.Connection, now: datetime | None = None) -> int:
    threshold = ((now or datetime.now(timezone.utc)) -
                 timedelta(days=CYCLE_AUDIT_RETENTION_DAYS)).isoformat()
    cursor = connection.execute(
        "DELETE FROM collection_cycle_audit WHERE scheduled_at_utc < ?", (threshold,)
    )
    return cursor.rowcount


def finish_phase7b2_migration(connection: sqlite3.Connection) -> None:
    """Seed audit provenance for memberships that predate schema 17."""
    timestamp = datetime.now(timezone.utc).isoformat()
    connection.execute(
        """INSERT OR IGNORE INTO baseline_training_membership_events (
        baseline_version_id, version_profile_id, feature_window_id,
        previous_state, new_state, event_type, eligibility_rule_version,
        reason_codes_json, event_timestamp_utc, details_json)
        SELECT profile.baseline_version_id, training.version_profile_id,
        training.feature_window_id, 'unknown', 'accepted', 'accepted', ?,
        '["migrated_existing_accepted_membership"]', ?,
        '{"source":"schema_17_migration"}'
        FROM baseline_version_training_windows training
        JOIN baseline_version_profiles profile ON profile.id=training.version_profile_id""",
        (ELIGIBILITY_RULE_VERSION, timestamp),
    )
