"""Persistence and read queries for Phase 7B shadow-mode evidence."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from agent.enhanced_catalogue import (
    COLLECTION_RUN_RETENTION_DAYS,
    EVENT_EVIDENCE_RETENTION_DAYS,
    EXISTING_RAW_SIGNAL_DETAILS,
    HOURLY_AGGREGATE_RETENTION_DAYS,
    RAW_SIGNAL_RETENTION_DAYS,
    RETENTION_AUDIT_DAYS,
    DISPLAY_POLICY_VERSION,
    PERMANENT_UNSUPPORTED_REASONS,
)


ENHANCED_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS enhanced_collection_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id TEXT NOT NULL,
        started_at_utc TEXT NOT NULL,
        finished_at_utc TEXT,
        status TEXT NOT NULL,
        due_collectors_json TEXT NOT NULL,
        successful_collectors_json TEXT NOT NULL DEFAULT '[]',
        failed_collectors_json TEXT NOT NULL DEFAULT '[]',
        collection_duration_ms REAL,
        full_cycle_duration_ms REAL,
        process_cpu_time_ms REAL,
        approximate_process_cpu_percent REAL,
        process_rss_before_bytes INTEGER,
        process_rss_after_bytes INTEGER,
        database_bytes_before INTEGER,
        database_bytes_after INTEGER,
        algorithm_version TEXT NOT NULL,
        configuration_version TEXT NOT NULL,
        shadow_mode INTEGER NOT NULL DEFAULT 1 CHECK(shadow_mode IN (0, 1)),
        error_code TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS enhanced_signal_samples (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        collection_run_id INTEGER NOT NULL,
        device_id TEXT NOT NULL,
        timestamp_utc TEXT NOT NULL,
        signal_group TEXT NOT NULL,
        signal_key TEXT NOT NULL,
        signal_label TEXT NOT NULL,
        numeric_value REAL,
        unit TEXT NOT NULL,
        availability_status TEXT NOT NULL,
        reason_code TEXT,
        source_name TEXT NOT NULL,
        source_status TEXT NOT NULL,
        collection_frequency_seconds INTEGER NOT NULL,
        shadow_mode INTEGER NOT NULL DEFAULT 1 CHECK(shadow_mode IN (0, 1)),
        details_json TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY(collection_run_id)
            REFERENCES enhanced_collection_runs(id) ON DELETE CASCADE,
        UNIQUE(collection_run_id, signal_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS enhanced_signal_hourly (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id TEXT NOT NULL,
        signal_key TEXT NOT NULL,
        signal_group TEXT NOT NULL,
        signal_label TEXT NOT NULL,
        hour_start_utc TEXT NOT NULL,
        unit TEXT NOT NULL,
        minimum_value REAL,
        average_value REAL,
        maximum_value REAL,
        first_value REAL,
        last_value REAL,
        available_count INTEGER NOT NULL,
        total_count INTEGER NOT NULL,
        last_availability_status TEXT NOT NULL,
        last_reason_code TEXT,
        source_name TEXT NOT NULL,
        shadow_mode INTEGER NOT NULL DEFAULT 1 CHECK(shadow_mode IN (0, 1)),
        updated_at_utc TEXT NOT NULL,
        UNIQUE(device_id, signal_key, hour_start_utc)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS enhanced_event_evidence (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        windows_event_id INTEGER,
        device_id TEXT NOT NULL,
        event_timestamp_utc TEXT NOT NULL,
        evidence_group TEXT NOT NULL,
        evidence_type TEXT NOT NULL,
        evidence_subtype TEXT NOT NULL,
        evidence_level TEXT NOT NULL,
        source_name TEXT NOT NULL,
        source_record_key TEXT NOT NULL UNIQUE,
        reason_code TEXT NOT NULL,
        safe_summary TEXT NOT NULL,
        shadow_mode INTEGER NOT NULL DEFAULT 1 CHECK(shadow_mode IN (0, 1)),
        created_at_utc TEXT NOT NULL,
        details_json TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY(windows_event_id) REFERENCES windows_events(id),
        UNIQUE(windows_event_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS enhanced_collector_state (
        collector_key TEXT PRIMARY KEY,
        last_attempt_utc TEXT,
        last_success_utc TEXT,
        next_due_utc TEXT,
        availability_status TEXT NOT NULL,
        reason_code TEXT,
        source_name TEXT NOT NULL,
        collection_frequency_seconds INTEGER NOT NULL,
        updated_at_utc TEXT NOT NULL,
        details_json TEXT NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS enhanced_retention_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at_utc TEXT NOT NULL,
        finished_at_utc TEXT NOT NULL,
        status TEXT NOT NULL,
        raw_samples_aggregated INTEGER NOT NULL DEFAULT 0,
        raw_samples_deleted INTEGER NOT NULL DEFAULT 0,
        collection_runs_deleted INTEGER NOT NULL DEFAULT 0,
        event_rows_deleted INTEGER NOT NULL DEFAULT 0,
        hourly_rows_deleted INTEGER NOT NULL DEFAULT 0,
        policy_json TEXT NOT NULL,
        error_code TEXT
    )
    """,
)

ENHANCED_INDEXES = (
    """
    CREATE INDEX IF NOT EXISTS idx_enhanced_signal_device_key_time
    ON enhanced_signal_samples(device_id, signal_key, timestamp_utc DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_enhanced_signal_group_time
    ON enhanced_signal_samples(signal_group, timestamp_utc DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_enhanced_hourly_device_key_time
    ON enhanced_signal_hourly(device_id, signal_key, hour_start_utc DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_enhanced_events_device_time
    ON enhanced_event_evidence(device_id, event_timestamp_utc DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_enhanced_events_type_time
    ON enhanced_event_evidence(evidence_type, event_timestamp_utc DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_enhanced_runs_device_time
    ON enhanced_collection_runs(device_id, started_at_utc DESC)
    """,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def create_collection_run(
    connection: sqlite3.Connection,
    *,
    device_id: str,
    started_at_utc: str,
    due_collectors: Iterable[str],
    process_rss_before_bytes: int | None,
    database_bytes_before: int | None,
    algorithm_version: str,
    configuration_version: str,
    scheduled_at_utc: str | None = None,
    schedule_delay_seconds: float | None = None,
    agent_session_id: str | None = None,
) -> int:
    with connection:
        # A hard process termination can prevent the prior run's final update.
        # The next real run closes that stale marker before creating its own.
        connection.execute(
            """
            UPDATE enhanced_collection_runs
            SET finished_at_utc = ?, status = 'error',
                error_code = 'abandoned_before_next_run'
            WHERE status = 'running'
            """,
            (started_at_utc,),
        )
        cursor = connection.execute(
            """
            INSERT INTO enhanced_collection_runs (
                device_id, started_at_utc, status, due_collectors_json,
                process_rss_before_bytes, database_bytes_before,
                algorithm_version, configuration_version, shadow_mode,
                scheduled_at_utc, schedule_delay_seconds, agent_session_id
            ) VALUES (?, ?, 'running', ?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                device_id,
                started_at_utc,
                _json(list(due_collectors)),
                process_rss_before_bytes,
                database_bytes_before,
                algorithm_version,
                configuration_version,
                scheduled_at_utc,
                schedule_delay_seconds,
                agent_session_id,
            ),
        )
    return int(cursor.lastrowid)


def mark_running_collection_runs(
    connection: sqlite3.Connection,
    *,
    finished_at_utc: str,
    error_code: str,
) -> int:
    """Close unfinished run metadata without touching collected evidence."""
    with connection:
        return connection.execute(
            """
            UPDATE enhanced_collection_runs
            SET finished_at_utc = ?, status = 'error', error_code = ?
            WHERE status = 'running'
            """,
            (finished_at_utc, error_code),
        ).rowcount


def store_signal_samples(
    connection: sqlite3.Connection,
    run_id: int,
    device_id: str,
    timestamp_utc: str,
    signals: Iterable[Mapping[str, Any]],
) -> int:
    rows = list(signals)
    stored = 0
    with connection:
        for signal in rows:
            reason = str(signal.get("reason_code") or "")
            status = str(signal.get("availability_status") or "")
            if (
                status in {"unsupported", "not_applicable"}
                and reason in PERMANENT_UNSUPPORTED_REASONS
            ):
                previous = connection.execute(
                    """SELECT availability_status, reason_code
                    FROM enhanced_signal_samples
                    WHERE device_id = ? AND signal_key = ?
                    ORDER BY timestamp_utc DESC, id DESC LIMIT 1""",
                    (device_id, signal["signal_key"]),
                ).fetchone()
                if (
                    previous is not None
                    and previous["availability_status"] == status
                    and previous["reason_code"] == reason
                ):
                    continue
            connection.execute(
                """
                INSERT INTO enhanced_signal_samples (
                    collection_run_id, device_id, timestamp_utc,
                    signal_group, signal_key, signal_label, numeric_value, unit,
                    availability_status, reason_code, source_name,
                    source_status, collection_frequency_seconds, shadow_mode,
                    details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    run_id,
                    device_id,
                    timestamp_utc,
                    signal["signal_group"],
                    signal["signal_key"],
                    signal["signal_label"],
                    signal.get("numeric_value"),
                    signal["unit"],
                    signal["availability_status"],
                    signal.get("reason_code"),
                    signal["source_name"],
                    signal["source_status"],
                    signal["collection_frequency_seconds"],
                    _json(signal.get("details", {})),
                ),
            )
            stored += 1
    return stored


def collector_is_due(
    connection: sqlite3.Connection,
    collector_key: str,
    now_utc: datetime,
) -> bool:
    row = connection.execute(
        "SELECT next_due_utc FROM enhanced_collector_state WHERE collector_key = ?",
        (collector_key,),
    ).fetchone()
    if row is None or row[0] is None:
        return True
    try:
        return datetime.fromisoformat(row[0]) <= now_utc
    except ValueError:
        return True


def update_collector_state(
    connection: sqlite3.Connection,
    *,
    collector_key: str,
    attempted_at_utc: datetime,
    frequency_seconds: int,
    availability_status: str,
    reason_code: str | None,
    source_name: str,
    successful: bool,
    details: Mapping[str, Any] | None = None,
    scheduled_at_utc: datetime | None = None,
    collection_duration_ms: float | None = None,
    gap_classification: str | None = None,
    agent_session_id: str | None = None,
) -> None:
    attempted = attempted_at_utc.isoformat()
    scheduled = scheduled_at_utc or attempted_at_utc
    next_due_value = scheduled + timedelta(seconds=frequency_seconds)
    while next_due_value <= attempted_at_utc:
        next_due_value += timedelta(seconds=frequency_seconds)
    next_due = next_due_value.isoformat()
    delay_seconds = max(0.0, (attempted_at_utc - scheduled).total_seconds())
    with connection:
        connection.execute(
            """
            INSERT INTO enhanced_collector_state (
                collector_key, last_attempt_utc, last_success_utc, next_due_utc,
                availability_status, reason_code, source_name,
                collection_frequency_seconds, updated_at_utc, details_json
                , last_scheduled_utc, last_schedule_delay_seconds,
                last_collection_duration_ms, last_gap_classification,
                last_agent_session_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(collector_key) DO UPDATE SET
                last_attempt_utc=excluded.last_attempt_utc,
                last_success_utc=CASE
                    WHEN excluded.last_success_utc IS NOT NULL
                    THEN excluded.last_success_utc
                    ELSE enhanced_collector_state.last_success_utc
                END,
                next_due_utc=excluded.next_due_utc,
                availability_status=excluded.availability_status,
                reason_code=excluded.reason_code,
                source_name=excluded.source_name,
                collection_frequency_seconds=excluded.collection_frequency_seconds,
                updated_at_utc=excluded.updated_at_utc,
                details_json=excluded.details_json,
                last_scheduled_utc=excluded.last_scheduled_utc,
                last_schedule_delay_seconds=excluded.last_schedule_delay_seconds,
                last_collection_duration_ms=excluded.last_collection_duration_ms,
                last_gap_classification=excluded.last_gap_classification,
                last_agent_session_id=excluded.last_agent_session_id
            """,
            (
                collector_key,
                attempted,
                attempted if successful else None,
                next_due,
                availability_status,
                reason_code,
                source_name,
                frequency_seconds,
                attempted,
                _json(details or {}),
                scheduled.isoformat(),
                delay_seconds,
                collection_duration_ms,
                gap_classification or (
                    "on_schedule" if delay_seconds < frequency_seconds / 2
                    else "missed_while_running"
                ),
                agent_session_id,
            ),
        )


def collector_schedule(
    connection: sqlite3.Connection,
    collector_key: str,
    now_utc: datetime,
) -> datetime:
    """Return the persisted scheduled instant, or now for the first attempt."""
    row = connection.execute(
        "SELECT next_due_utc FROM enhanced_collector_state WHERE collector_key = ?",
        (collector_key,),
    ).fetchone()
    if row is None or row[0] is None:
        return now_utc
    try:
        return datetime.fromisoformat(row[0])
    except ValueError:
        return now_utc


def finish_collection_run(
    connection: sqlite3.Connection,
    run_id: int,
    *,
    finished_at_utc: str,
    status: str,
    successful_collectors: Iterable[str],
    failed_collectors: Iterable[str],
    collection_duration_ms: float,
    full_cycle_duration_ms: float,
    process_cpu_time_ms: float,
    approximate_process_cpu_percent: float | None,
    process_rss_after_bytes: int | None,
    database_bytes_after: int | None,
    error_code: str | None = None,
) -> None:
    with connection:
        connection.execute(
            """
            UPDATE enhanced_collection_runs
            SET finished_at_utc = ?, status = ?,
                successful_collectors_json = ?, failed_collectors_json = ?,
                collection_duration_ms = ?, full_cycle_duration_ms = ?,
                process_cpu_time_ms = ?, approximate_process_cpu_percent = ?,
                process_rss_after_bytes = ?, database_bytes_after = ?,
                error_code = ?
            WHERE id = ?
            """,
            (
                finished_at_utc,
                status,
                _json(list(successful_collectors)),
                _json(list(failed_collectors)),
                collection_duration_ms,
                full_cycle_duration_ms,
                process_cpu_time_ms,
                approximate_process_cpu_percent,
                process_rss_after_bytes,
                database_bytes_after,
                error_code,
                run_id,
            ),
        )


def unmirrored_windows_events(
    connection: sqlite3.Connection,
    limit: int = 500,
    now_utc: datetime | None = None,
) -> list[sqlite3.Row]:
    # Evidence outside the Phase 7B retention horizon must not be re-created
    # from the permanent core event table after retention removes it.
    cutoff = (
        (now_utc or _utc_now()) - timedelta(days=EVENT_EVIDENCE_RETENTION_DAYS)
    ).isoformat()
    return connection.execute(
        """
        SELECT event.* FROM windows_events event
        LEFT JOIN enhanced_event_evidence enhanced
          ON enhanced.windows_event_id = event.id
        WHERE enhanced.id IS NULL
          AND event.event_timestamp_utc >= ?
        ORDER BY event.id
        LIMIT ?
        """,
        (cutoff, limit),
    ).fetchall()


def store_enhanced_events(
    connection: sqlite3.Connection,
    events: Iterable[Mapping[str, Any]],
) -> int:
    inserted = 0
    with connection:
        for event in events:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO enhanced_event_evidence (
                    windows_event_id, device_id, event_timestamp_utc,
                    evidence_group, evidence_type, evidence_subtype,
                    evidence_level, source_name, source_record_key, reason_code,
                    safe_summary, shadow_mode, created_at_utc, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    event.get("windows_event_id"),
                    event["device_id"],
                    event["event_timestamp_utc"],
                    event["evidence_group"],
                    event["evidence_type"],
                    event["evidence_subtype"],
                    event["evidence_level"],
                    event["source_name"],
                    event["source_record_key"],
                    event["reason_code"],
                    event["safe_summary"],
                    event["created_at_utc"],
                    _json(event.get("details", {})),
                ),
            )
            inserted += cursor.rowcount
    return inserted


def run_retention(
    connection: sqlite3.Connection,
    now_utc: datetime | None = None,
) -> dict[str, int | str]:
    """Aggregate and retain only Phase 7B enhanced data; core history is untouched."""
    now = now_utc or _utc_now()
    raw_cutoff = (now - timedelta(days=RAW_SIGNAL_RETENTION_DAYS)).isoformat()
    hourly_cutoff = (
        now - timedelta(days=HOURLY_AGGREGATE_RETENTION_DAYS)
    ).isoformat()
    event_cutoff = (now - timedelta(days=EVENT_EVIDENCE_RETENTION_DAYS)).isoformat()
    run_cutoff = (now - timedelta(days=COLLECTION_RUN_RETENTION_DAYS)).isoformat()
    audit_cutoff = (now - timedelta(days=RETENTION_AUDIT_DAYS)).isoformat()
    started = now.isoformat()
    policy = {
        "raw_signal_days": RAW_SIGNAL_RETENTION_DAYS,
        "hourly_aggregate_days": HOURLY_AGGREGATE_RETENTION_DAYS,
        "event_evidence_days": EVENT_EVIDENCE_RETENTION_DAYS,
        "collection_run_days": COLLECTION_RUN_RETENTION_DAYS,
        "core_tables_affected": False,
    }
    with connection:
        eligible = int(
            connection.execute(
                "SELECT COUNT(*) FROM enhanced_signal_samples WHERE timestamp_utc < ?",
                (raw_cutoff,),
            ).fetchone()[0]
        )
        connection.execute(
            """
            INSERT INTO enhanced_signal_hourly (
                device_id, signal_key, signal_group, signal_label,
                hour_start_utc, unit, minimum_value, average_value,
                maximum_value, first_value, last_value, available_count,
                total_count, last_availability_status, last_reason_code,
                source_name, shadow_mode, updated_at_utc
            )
            SELECT
                device_id, signal_key, signal_group, signal_label,
                substr(timestamp_utc, 1, 13) || ':00:00+00:00',
                unit,
                MIN(numeric_value), AVG(numeric_value), MAX(numeric_value),
                (
                    SELECT first.numeric_value
                    FROM enhanced_signal_samples first
                    WHERE first.device_id = sample.device_id
                      AND first.signal_key = sample.signal_key
                      AND substr(first.timestamp_utc, 1, 13)
                          = substr(sample.timestamp_utc, 1, 13)
                    ORDER BY first.timestamp_utc, first.id LIMIT 1
                ),
                (
                    SELECT last.numeric_value
                    FROM enhanced_signal_samples last
                    WHERE last.device_id = sample.device_id
                      AND last.signal_key = sample.signal_key
                      AND substr(last.timestamp_utc, 1, 13)
                          = substr(sample.timestamp_utc, 1, 13)
                    ORDER BY last.timestamp_utc DESC, last.id DESC LIMIT 1
                ),
                SUM(CASE WHEN availability_status = 'available' THEN 1 ELSE 0 END),
                COUNT(*),
                (
                    SELECT status.availability_status
                    FROM enhanced_signal_samples status
                    WHERE status.device_id = sample.device_id
                      AND status.signal_key = sample.signal_key
                      AND substr(status.timestamp_utc, 1, 13)
                          = substr(sample.timestamp_utc, 1, 13)
                    ORDER BY status.timestamp_utc DESC, status.id DESC LIMIT 1
                ),
                (
                    SELECT reason.reason_code
                    FROM enhanced_signal_samples reason
                    WHERE reason.device_id = sample.device_id
                      AND reason.signal_key = sample.signal_key
                      AND substr(reason.timestamp_utc, 1, 13)
                          = substr(sample.timestamp_utc, 1, 13)
                    ORDER BY reason.timestamp_utc DESC, reason.id DESC LIMIT 1
                ),
                source_name, 1, ?
            FROM enhanced_signal_samples sample
            WHERE timestamp_utc < ?
            GROUP BY device_id, signal_key, substr(timestamp_utc, 1, 13)
            ON CONFLICT(device_id, signal_key, hour_start_utc) DO UPDATE SET
                minimum_value=excluded.minimum_value,
                average_value=excluded.average_value,
                maximum_value=excluded.maximum_value,
                first_value=excluded.first_value,
                last_value=excluded.last_value,
                available_count=excluded.available_count,
                total_count=excluded.total_count,
                last_availability_status=excluded.last_availability_status,
                last_reason_code=excluded.last_reason_code,
                source_name=excluded.source_name,
                updated_at_utc=excluded.updated_at_utc
            """,
            (now.isoformat(), raw_cutoff),
        )
        raw_deleted = connection.execute(
            "DELETE FROM enhanced_signal_samples WHERE timestamp_utc < ?",
            (raw_cutoff,),
        ).rowcount
        runs_deleted = connection.execute(
            "DELETE FROM enhanced_collection_runs WHERE started_at_utc < ?",
            (run_cutoff,),
        ).rowcount
        events_deleted = connection.execute(
            "DELETE FROM enhanced_event_evidence WHERE event_timestamp_utc < ?",
            (event_cutoff,),
        ).rowcount
        hourly_deleted = connection.execute(
            "DELETE FROM enhanced_signal_hourly WHERE hour_start_utc < ?",
            (hourly_cutoff,),
        ).rowcount
        connection.execute(
            "DELETE FROM enhanced_retention_runs WHERE finished_at_utc < ?",
            (audit_cutoff,),
        )
        finished = _utc_now().isoformat()
        connection.execute(
            """
            INSERT INTO enhanced_retention_runs (
                started_at_utc, finished_at_utc, status,
                raw_samples_aggregated, raw_samples_deleted,
                collection_runs_deleted, event_rows_deleted,
                hourly_rows_deleted, policy_json
            ) VALUES (?, ?, 'success', ?, ?, ?, ?, ?, ?)
            """,
            (
                started,
                finished,
                eligible,
                raw_deleted,
                runs_deleted,
                events_deleted,
                hourly_deleted,
                _json(policy),
            ),
        )
    return {
        "status": "success",
        "raw_samples_aggregated": eligible,
        "raw_samples_deleted": raw_deleted,
        "collection_runs_deleted": runs_deleted,
        "event_rows_deleted": events_deleted,
        "hourly_rows_deleted": hourly_deleted,
    }


def _trend(values: list[float]) -> str:
    if len(values) < 2:
        return "insufficient_history"
    first, last = values[-1], values[0]
    scale = max(abs(first), abs(last), 1.0)
    change = (last - first) / scale
    if change > 0.05:
        return "increasing"
    if change < -0.05:
        return "decreasing"
    return "stable"


def _readiness_state(item: Mapping[str, Any]) -> str:
    status = str(item.get("availability_status") or "unavailable")
    reason = str(item.get("reason_code") or "")
    key = str(item.get("signal_key") or "")
    details = item.get("details") or {}
    if status == "permission_limited" or "permission_limited" in reason:
        return "permission_limited"
    if status in {"collector_failure", "timeout"} or reason in {
        "query_timeout", "physical_disk_query_failed",
    }:
        return "collector_failure"
    if status in {"unsupported", "not_applicable"} or reason in {
        "not_reliably_exposed_by_supported_counter",
        "process_creation_auditing_not_safely_available",
        "battery_metric_not_exposed",
        "battery_not_present",
        "storage_reliability_not_exposed_by_hardware",
    }:
        return "unsupported_by_device"
    if key == "storage_io_active_percent" and not details.get("bounded_percent"):
        return "redesign_required"
    if key in {
        "battery_full_charge_capacity_mwh",
        "battery_remaining_capacity_mwh",
        "cpu_performance_limit_percent",
    }:
        return "shadow_only"
    return "needs_more_history" if status == "available" else "collector_failure"


def _display_state(item: Mapping[str, Any], available_count: int) -> str:
    """Map internal provenance to explicit user-facing availability states."""
    status = str(item.get("availability_status") or "unavailable")
    reason = str(item.get("reason_code") or "")
    if status == "available":
        return "Available" if available_count >= 3 else "Collecting history"
    if status == "not_applicable" or reason in {"battery_not_present", "battery_metric_not_exposed"}:
        return "Not applicable"
    if status == "unsupported" or reason in PERMANENT_UNSUPPORTED_REASONS:
        return "Unsupported on this device"
    if status in {"timeout", "unavailable"} or reason in {"query_timeout", "counter_unavailable"}:
        return "Temporarily unavailable"
    if status in {"collector_failure", "permission_limited"}:
        return "Collector failure"
    return "Temporarily unavailable"


def get_enhanced_status(connection: sqlite3.Connection) -> dict[str, Any]:
    device_row = connection.execute(
        "SELECT device_id FROM metrics ORDER BY timestamp_utc DESC, id DESC LIMIT 1"
    ).fetchone()
    device_id = device_row[0] if device_row else None
    latest_rows = connection.execute(
        """
        SELECT sample.* FROM enhanced_signal_samples sample
        JOIN (
            SELECT signal_key, MAX(id) id
            FROM enhanced_signal_samples
            WHERE (? IS NULL OR device_id = ?)
            GROUP BY signal_key
        ) latest ON latest.id = sample.id
        ORDER BY sample.signal_group, sample.signal_label
        """,
        (device_id, device_id),
    ).fetchall()
    signals: list[dict[str, Any]] = []
    for row in latest_rows:
        item = dict(row)
        values = [
            float(value[0])
            for value in connection.execute(
                """
                SELECT numeric_value FROM enhanced_signal_samples
                WHERE signal_key = ? AND device_id = ?
                  AND availability_status = 'available'
                  AND numeric_value IS NOT NULL
                ORDER BY timestamp_utc DESC, id DESC LIMIT 20
                """,
                (item["signal_key"], item["device_id"]),
            )
        ]
        item["trend"] = _trend(values)
        item["shadow_mode"] = bool(item["shadow_mode"])
        item["details"] = json.loads(item.pop("details_json") or "{}")
        item["readiness_state"] = _readiness_state(item)
        item["available_history_count"] = len(values)
        item["display_state"] = _display_state(item, len(values))
        signals.append(item)

    if device_id is not None:
        latest_metric = connection.execute(
            """
            SELECT * FROM metrics
            WHERE device_id = ?
            ORDER BY timestamp_utc DESC, id DESC LIMIT 1
            """,
            (device_id,),
        ).fetchone()
        if latest_metric is not None:
            for key, group, label, unit, column in EXISTING_RAW_SIGNAL_DETAILS:
                numeric_value = latest_metric[column]
                values = [
                    float(row[0])
                    for row in connection.execute(
                        f"""
                        SELECT {column} FROM metrics
                        WHERE device_id = ? AND {column} IS NOT NULL
                        ORDER BY timestamp_utc DESC, id DESC LIMIT 20
                        """,
                        (device_id,),
                    )
                ]
                signals.append(
                    {
                        "id": None,
                        "collection_run_id": None,
                        "device_id": device_id,
                        "timestamp_utc": latest_metric["timestamp_utc"],
                        "signal_group": group,
                        "signal_key": key,
                        "signal_label": label,
                        "numeric_value": numeric_value,
                        "unit": unit,
                        "availability_status": (
                            "available"
                            if numeric_value is not None
                            else "unavailable"
                        ),
                        "reason_code": (
                            None
                            if numeric_value is not None
                            else "existing_raw_metric_unavailable"
                        ),
                        "source_name": "Existing SmartOps psutil telemetry",
                        "source_status": (
                            "available"
                            if numeric_value is not None
                            else "unavailable"
                        ),
                        "collection_frequency_seconds": 30,
                        "shadow_mode": True,
                        "details": {
                            "stored_once_in_metrics_column": column,
                            "duplicated_in_enhanced_table": False,
                        },
                        "trend": _trend(values),
                        "readiness_state": "shadow_only",
                        "available_history_count": len(values),
                        "display_state": _display_state(
                            {"availability_status": (
                                "available" if numeric_value is not None else "unavailable"
                            ), "reason_code": (
                                None if numeric_value is not None else "existing_raw_metric_unavailable"
                            )},
                            len(values),
                        ),
                    }
                )
            signals.sort(
                key=lambda item: (item["signal_group"], item["signal_label"])
            )

    hidden_signals = [
        {
            "signal_key": item["signal_key"],
            "signal_label": item["signal_label"],
            "availability_status": item["availability_status"],
            "reason_code": item["reason_code"],
            "source_name": item["source_name"],
            "display_state": "Unsupported on this device",
        }
        for item in signals
        if item["readiness_state"] == "unsupported_by_device"
        and str(item.get("reason_code") or "") in PERMANENT_UNSUPPORTED_REASONS
    ]
    signals = [
        item
        for item in signals
        if not (
            item["readiness_state"] == "unsupported_by_device"
            and str(item.get("reason_code") or "")
            in PERMANENT_UNSUPPORTED_REASONS
        )
    ]

    states = []
    for row in connection.execute(
        "SELECT * FROM enhanced_collector_state ORDER BY collector_key"
    ):
        item = dict(row)
        item["details"] = json.loads(item.pop("details_json") or "{}")
        states.append(item)

    latest_run = connection.execute(
        """
        SELECT * FROM enhanced_collection_runs
        ORDER BY started_at_utc DESC, id DESC LIMIT 1
        """
    ).fetchone()
    run = dict(latest_run) if latest_run else None
    if run:
        for key in (
            "due_collectors_json",
            "successful_collectors_json",
            "failed_collectors_json",
        ):
            run[key.removesuffix("_json")] = json.loads(run.pop(key) or "[]")
        run["shadow_mode"] = bool(run["shadow_mode"])

    event_count = int(
        connection.execute("SELECT COUNT(*) FROM enhanced_event_evidence").fetchone()[0]
    )
    diagnostics: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in [*signals, *hidden_signals]:
        state = str(item.get("display_state") or (
            "Unsupported on this device" if item in hidden_signals
            else "Temporarily unavailable"
        ))
        if state in {"Available", "Collecting history"}:
            continue
        key = (
            state,
            str(item.get("reason_code") or "reason_not_reported"),
            str(item.get("source_name") or "Unknown local source"),
        )
        diagnostics.setdefault(key, {
            "display_state": state,
            "reason_code": key[1],
            "source_name": key[2],
            "signal_count": 0,
        })["signal_count"] += 1
    return {
        "status": "available" if signals else "collecting_data",
        "shadow_mode": True,
        "device_id": device_id,
        "signals": signals,
        "hidden_unsupported_signals": hidden_signals,
        "capability_display_policy_version": DISPLAY_POLICY_VERSION,
        "capability_display_message": (
            "Only evidence supported by this device is displayed."
        ),
        "collectors": states,
        "technical_diagnostics": list(diagnostics.values()),
        "latest_run": run,
        "structured_event_count": event_count,
        "interpretation": (
            "Enhanced evidence is collected and displayed in shadow mode. "
            "It does not change Risk Evidence, System Health, or predictive-alert severity."
        ),
    }


def get_signal_history(
    connection: sqlite3.Connection,
    signal_key: str,
    limit: int,
    start: str | None = None,
    end: str | None = None,
) -> list[dict[str, Any]]:
    clauses = ["signal_key = ?"]
    parameters: list[Any] = [signal_key]
    if start:
        clauses.append("timestamp_utc >= ?")
        parameters.append(start)
    if end:
        clauses.append("timestamp_utc <= ?")
        parameters.append(end)
    rows = connection.execute(
        f"""
        SELECT timestamp_utc, signal_key, signal_group, signal_label,
               numeric_value, unit, availability_status, reason_code,
               source_name, source_status, shadow_mode
        FROM enhanced_signal_samples
        WHERE {' AND '.join(clauses)}
        ORDER BY timestamp_utc DESC, id DESC LIMIT ?
        """,
        [*parameters, limit],
    ).fetchall()
    return [{**dict(row), "shadow_mode": bool(row["shadow_mode"])} for row in rows]


def get_enhanced_events(
    connection: sqlite3.Connection,
    limit: int,
    offset: int,
    evidence_type: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    where = "WHERE evidence_type = ?" if evidence_type else ""
    parameters: list[Any] = [evidence_type] if evidence_type else []
    total = int(
        connection.execute(
            f"SELECT COUNT(*) FROM enhanced_event_evidence {where}",
            parameters,
        ).fetchone()[0]
    )
    rows = connection.execute(
        f"""
        SELECT * FROM enhanced_event_evidence {where}
        ORDER BY event_timestamp_utc DESC, id DESC LIMIT ? OFFSET ?
        """,
        [*parameters, limit, offset],
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["shadow_mode"] = bool(item["shadow_mode"])
        item["details"] = json.loads(item.pop("details_json") or "{}")
        items.append(item)
    return items, total


def get_overhead_history(
    connection: sqlite3.Connection,
    limit: int,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT * FROM enhanced_collection_runs
        WHERE status != 'running'
        ORDER BY started_at_utc DESC, id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]
