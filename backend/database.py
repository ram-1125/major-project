"""Small sqlite3 helpers for the local SmartOps database."""

from __future__ import annotations

import sqlite3
import random
import threading
import time
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

from agent.config import get_database_path
from backend.enhanced_repository import ENHANCED_INDEXES, ENHANCED_SCHEMA_STATEMENTS
from backend.phase7b1_repository import (
    ADDITIONAL_COLUMNS as PHASE7B1_ADDITIONAL_COLUMNS,
    INDEX_STATEMENTS as PHASE7B1_INDEXES,
    SCHEMA_STATEMENTS as PHASE7B1_SCHEMA_STATEMENTS,
    SCHEMA_VERSION as PHASE7B1_SCHEMA_VERSION,
    finish_phase7b1_migration,
)
from backend.phase7b2_repository import (
    ADDITIONAL_COLUMNS as PHASE7B2_ADDITIONAL_COLUMNS,
    INDEX_STATEMENTS as PHASE7B2_INDEXES,
    SCHEMA_STATEMENTS as PHASE7B2_SCHEMA_STATEMENTS,
    SCHEMA_VERSION as PHASE7B2_SCHEMA_VERSION,
    finish_phase7b2_migration,
)
from backend.postcalibration_repository import (
    INDEX_STATEMENTS as POSTCALIBRATION_INDEXES,
    SCHEMA_STATEMENTS as POSTCALIBRATION_SCHEMA_STATEMENTS,
    SCHEMA_VERSION,
    finish_migration as finish_postcalibration_migration,
)


SQLITE_CONNECTION_TIMEOUT_SECONDS = 15.0
SQLITE_BUSY_TIMEOUT_MS = 15_000
SQLITE_SYNCHRONOUS_POLICY = "NORMAL"
SQLITE_JOURNAL_MODE = "WAL"
SQLITE_WRITE_RETRY_ATTEMPTS = 4
SQLITE_WRITE_RETRY_BASE_SECONDS = 0.05
SQLITE_WRITE_RETRY_JITTER_SECONDS = 0.025
SQLITE_WRITER_GATE_TIMEOUT_SECONDS = 30.0

_T = TypeVar("_T")
_PERSISTENT_POLICY_PATHS: set[str] = set()
_PERSISTENT_POLICY_LOCK = threading.Lock()
_WRITER_COORDINATORS: dict[str, "_WriterCoordinator"] = {}
_WRITER_COORDINATORS_LOCK = threading.Lock()
_WRITER_CONTEXT = threading.local()


class _WriterCoordinator:
    """Serialize in-process writers while allowing concurrent readers.

    SQLite/WAL still performs the authoritative cross-process serialization.
    This small gate prevents SmartOps' sampler and maintenance thread from
    needlessly competing at SQLite's busy handler. Waiting raw writers are
    admitted ahead of maintenance writers after the current transaction ends.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._active = False
        self._raw_waiters = 0

    def acquire(self, priority: str, timeout: float) -> None:
        raw = priority == "raw"
        deadline = time.monotonic() + timeout
        with self._condition:
            if raw:
                self._raw_waiters += 1
            try:
                while self._active or (not raw and self._raw_waiters > 0):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise sqlite3.OperationalError(
                            "database writer gate is busy"
                        )
                    self._condition.wait(remaining)
                self._active = True
            finally:
                if raw:
                    self._raw_waiters -= 1

    def release(self) -> None:
        with self._condition:
            if self._active:
                self._active = False
                self._condition.notify_all()


def _database_key(path: Path) -> str:
    return str(path.resolve()).casefold()


def _writer_coordinator(path: Path) -> _WriterCoordinator:
    key = _database_key(path)
    with _WRITER_COORDINATORS_LOCK:
        return _WRITER_COORDINATORS.setdefault(key, _WriterCoordinator())


@contextmanager
def writer_priority(priority: str) -> Iterator[None]:
    """Set the priority used by connections opened in the current thread."""
    previous = getattr(_WRITER_CONTEXT, "priority", "normal")
    _WRITER_CONTEXT.priority = priority
    try:
        yield
    finally:
        _WRITER_CONTEXT.priority = previous


def _is_write_statement(sql: str) -> bool:
    statement = sql.lstrip().upper()
    return statement.startswith(
        (
            "INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER",
            "DROP", "VACUUM", "REINDEX", "ANALYZE", "BEGIN IMMEDIATE",
            "BEGIN EXCLUSIVE", "PRAGMA USER_VERSION", "PRAGMA JOURNAL_MODE",
        )
    )


class SmartOpsConnection(sqlite3.Connection):
    """sqlite3 connection that owns the in-process writer gate correctly."""

    _smartops_coordinator: _WriterCoordinator | None = None
    _smartops_priority: str = "normal"
    _smartops_gate_owned: bool = False

    def configure_writer(self, path: Path, priority: str) -> None:
        self._smartops_coordinator = _writer_coordinator(path)
        self._smartops_priority = priority

    def _acquire_for_sql(self, sql: str) -> None:
        if (
            not self._smartops_gate_owned
            and _is_write_statement(sql)
            and self._smartops_coordinator is not None
        ):
            self._smartops_coordinator.acquire(
                self._smartops_priority,
                SQLITE_WRITER_GATE_TIMEOUT_SECONDS,
            )
            self._smartops_gate_owned = True

    def _release_writer(self) -> None:
        if self._smartops_gate_owned and self._smartops_coordinator is not None:
            self._smartops_gate_owned = False
            self._smartops_coordinator.release()

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        self._acquire_for_sql(sql)
        try:
            return super().execute(sql, parameters)
        except Exception:
            if not self.in_transaction:
                self._release_writer()
            raise

    def executemany(self, sql: str, parameters: Any, /) -> sqlite3.Cursor:
        self._acquire_for_sql(sql)
        try:
            return super().executemany(sql, parameters)
        except Exception:
            if not self.in_transaction:
                self._release_writer()
            raise

    def executescript(self, sql_script: str, /) -> sqlite3.Cursor:
        # Migration scripts may contain several statements; treat the script
        # as a writer when any non-read statement is present.
        if any(_is_write_statement(part) for part in sql_script.split(";")):
            self._acquire_for_sql("CREATE")
        try:
            return super().executescript(sql_script)
        except Exception:
            if not self.in_transaction:
                self._release_writer()
            raise

    def commit(self) -> None:
        super().commit()
        self._release_writer()

    def rollback(self) -> None:
        try:
            super().rollback()
        finally:
            self._release_writer()

    def __enter__(self) -> "SmartOpsConnection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False

    def close(self) -> None:
        try:
            if self.in_transaction:
                super().rollback()
        finally:
            self._release_writer()
            super().close()


def _open_connection(path: Path, *, priority: str = "normal") -> SmartOpsConnection:
    connection = sqlite3.connect(
        path,
        timeout=SQLITE_CONNECTION_TIMEOUT_SECONDS,
        factory=SmartOpsConnection,
    )
    connection.configure_writer(path, priority)
    connection.row_factory = sqlite3.Row
    _configure_connection(connection)
    return connection


def is_transient_sqlite_lock(error: BaseException) -> bool:
    """Return true only for SQLite's transient busy/locked conditions."""
    if not isinstance(error, sqlite3.OperationalError):
        return False
    message = str(error).casefold()
    return (
        "database is locked" in message
        or "database table is locked" in message
        or "database is busy" in message
        or "writer gate is busy" in message
    )


def run_write_transaction(
    database_path: Path,
    operation: Callable[[sqlite3.Connection], _T],
    *,
    priority: str = "normal",
    attempts: int = SQLITE_WRITE_RETRY_ATTEMPTS,
    base_delay_seconds: float = SQLITE_WRITE_RETRY_BASE_SECONDS,
    jitter_seconds: float = SQLITE_WRITE_RETRY_JITTER_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    random_value: Callable[[], float] = random.random,
) -> _T:
    """Run one short idempotent write transaction with lock-only retries."""
    if attempts < 1:
        raise ValueError("attempts must be at least one")
    for attempt in range(attempts):
        try:
            with database_connection(database_path, priority=priority) as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    result = operation(connection)
                    connection.commit()
                    return result
                except BaseException:
                    connection.rollback()
                    raise
        except BaseException as error:
            if not is_transient_sqlite_lock(error) or attempt + 1 >= attempts:
                raise
            delay = base_delay_seconds * (2**attempt)
            delay += jitter_seconds * random_value()
            sleep(delay)
    raise AssertionError("unreachable SQLite retry state")


CREATE_METRICS_TABLE = """
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_utc TEXT NOT NULL,
    device_id TEXT NOT NULL,
    cpu_percent REAL,
    cpu_per_core_json TEXT,
    cpu_physical_cores INTEGER,
    cpu_logical_cores INTEGER,
    cpu_frequency_mhz REAL,
    process_count INTEGER,
    thread_count INTEGER,
    ram_percent REAL,
    ram_used_bytes INTEGER,
    ram_available_bytes INTEGER,
    ram_total_bytes INTEGER,
    swap_percent REAL,
    swap_used_bytes INTEGER,
    swap_total_bytes INTEGER,
    disk_percent REAL,
    disk_used_bytes INTEGER,
    disk_free_bytes INTEGER,
    disk_total_bytes INTEGER,
    disk_partitions_json TEXT,
    disk_read_bytes_per_second REAL,
    disk_write_bytes_per_second REAL,
    disk_read_ops_per_second REAL,
    disk_write_ops_per_second REAL,
    disk_read_bytes_total INTEGER,
    disk_write_bytes_total INTEGER,
    disk_read_ops_total INTEGER,
    disk_write_ops_total INTEGER,
    network_upload_bytes_per_second REAL,
    network_download_bytes_per_second REAL,
    network_packets_sent_per_second REAL,
    network_packets_received_per_second REAL,
    network_bytes_sent_total INTEGER,
    network_bytes_received_total INTEGER,
    network_packets_sent_total INTEGER,
    network_packets_received_total INTEGER,
    network_interface_available INTEGER,
    battery_percent REAL,
    battery_charging INTEGER,
    ac_power_connected INTEGER,
    battery_seconds_remaining REAL,
    uptime_seconds REAL,
    boot_timestamp_utc TEXT,
    user_idle_seconds REAL,
    user_state TEXT,
    foreground_process_name TEXT,
    cpu_temperature_celsius REAL,
    gpu_utilization_percent REAL,
    gpu_memory_percent REAL,
    gpu_temperature_celsius REAL,
    workload_class TEXT,
    workload_confidence REAL,
    workload_reasons_json TEXT
)
"""

CREATE_PROCESS_SNAPSHOTS_TABLE = """
CREATE TABLE IF NOT EXISTS process_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_id INTEGER NOT NULL,
    category TEXT NOT NULL CHECK(category IN ('cpu', 'memory')),
    rank INTEGER NOT NULL,
    pid INTEGER NOT NULL,
    process_name TEXT NOT NULL,
    cpu_percent REAL,
    memory_percent REAL,
    FOREIGN KEY(metric_id) REFERENCES metrics(id) ON DELETE CASCADE,
    UNIQUE(metric_id, category, rank)
)
"""

CREATE_WINDOWS_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS windows_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    event_timestamp_utc TEXT NOT NULL,
    channel TEXT NOT NULL,
    provider_name TEXT NOT NULL,
    event_id INTEGER NOT NULL,
    record_id INTEGER NOT NULL,
    event_level TEXT NOT NULL,
    smartops_category TEXT NOT NULL,
    safe_summary TEXT NOT NULL,
    collected_at_utc TEXT NOT NULL,
    UNIQUE(channel, record_id)
)
"""

CREATE_EVENT_CHECKPOINTS_TABLE = """
CREATE TABLE IF NOT EXISTS event_channel_checkpoints (
    channel TEXT PRIMARY KEY,
    last_record_id INTEGER NOT NULL DEFAULT 0,
    available INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'available',
    updated_at_utc TEXT NOT NULL
)
"""

CREATE_FEATURE_WINDOWS_TABLE = """
CREATE TABLE IF NOT EXISTS feature_windows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    window_start_utc TEXT NOT NULL,
    window_end_utc TEXT NOT NULL,
    sample_count INTEGER NOT NULL,
    expected_sample_count INTEGER NOT NULL,
    coverage_ratio REAL NOT NULL,
    is_complete INTEGER NOT NULL,
    dominant_workload_class TEXT,
    workload_confidence REAL,
    missing_indicators_json TEXT NOT NULL,
    cpu_avg REAL, cpu_min REAL, cpu_max REAL, cpu_stddev REAL,
    cpu_p95 REAL, cpu_slope REAL, cpu_high_ratio REAL,
    ram_avg REAL, ram_min REAL, ram_max REAL, ram_stddev REAL,
    ram_slope REAL, swap_avg REAL, swap_max REAL, memory_high_ratio REAL,
    disk_usage_avg REAL, disk_usage_max REAL, disk_read_avg REAL,
    disk_read_max REAL, disk_write_avg REAL, disk_write_max REAL,
    disk_usage_slope REAL,
    network_upload_avg REAL, network_upload_max REAL,
    network_download_avg REAL, network_download_max REAL,
    active_ratio REAL, idle_ratio REAL,
    process_count_avg REAL, process_count_max REAL,
    cpu_temperature_avg REAL, cpu_temperature_max REAL,
    gpu_utilization_avg REAL, gpu_utilization_max REAL,
    gpu_memory_avg REAL, gpu_memory_max REAL,
    gpu_temperature_avg REAL, gpu_temperature_max REAL,
    cpu_temperature_missing_ratio REAL,
    gpu_utilization_missing_ratio REAL,
    gpu_memory_missing_ratio REAL,
    gpu_temperature_missing_ratio REAL,
    critical_event_count INTEGER NOT NULL DEFAULT 0,
    error_event_count INTEGER NOT NULL DEFAULT 0,
    warning_event_count INTEGER NOT NULL DEFAULT 0,
    hardware_event_count INTEGER NOT NULL DEFAULT 0,
    storage_event_count INTEGER NOT NULL DEFAULT 0,
    power_event_count INTEGER NOT NULL DEFAULT 0,
    application_crash_count INTEGER NOT NULL DEFAULT 0,
    service_failure_count INTEGER NOT NULL DEFAULT 0,
    resource_exhaustion_count INTEGER NOT NULL DEFAULT 0,
    updated_at_utc TEXT NOT NULL,
    UNIQUE(device_id, window_start_utc)
)
"""

CREATE_BASELINE_PROFILES_TABLE = """
CREATE TABLE IF NOT EXISTS baseline_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    workload_scope TEXT NOT NULL,
    training_start_utc TEXT,
    training_end_utc TEXT,
    eligible_window_count INTEGER NOT NULL,
    excluded_window_count INTEGER NOT NULL,
    distinct_day_count INTEGER NOT NULL,
    readiness_state TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    configuration_version TEXT NOT NULL,
    feature_names_json TEXT NOT NULL,
    missing_features_json TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    UNIQUE(device_id, workload_scope)
)
"""

CREATE_BASELINE_FEATURE_STATS_TABLE = """
CREATE TABLE IF NOT EXISTS baseline_feature_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    baseline_id INTEGER NOT NULL,
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
    FOREIGN KEY(baseline_id) REFERENCES baseline_profiles(id) ON DELETE CASCADE,
    UNIQUE(baseline_id, feature_name)
)
"""

CREATE_BASELINE_TRAINING_RUNS_TABLE = """
CREATE TABLE IF NOT EXISTS baseline_training_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    workload_scope TEXT NOT NULL,
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT NOT NULL,
    status TEXT NOT NULL,
    eligible_window_count INTEGER NOT NULL DEFAULT 0,
    excluded_window_count INTEGER NOT NULL DEFAULT 0,
    error_code TEXT
)
"""

CREATE_BASELINE_TRAINING_WINDOWS_TABLE = """
CREATE TABLE IF NOT EXISTS baseline_training_windows (
    baseline_id INTEGER NOT NULL,
    feature_window_id INTEGER NOT NULL,
    PRIMARY KEY(baseline_id, feature_window_id),
    FOREIGN KEY(baseline_id) REFERENCES baseline_profiles(id) ON DELETE CASCADE,
    FOREIGN KEY(feature_window_id) REFERENCES feature_windows(id) ON DELETE CASCADE
)
"""

CREATE_ISOLATION_MODEL_METADATA_TABLE = """
CREATE TABLE IF NOT EXISTS isolation_model_metadata (
    baseline_id INTEGER PRIMARY KEY,
    readiness_state TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    feature_names_json TEXT NOT NULL,
    preprocessing_json TEXT NOT NULL,
    training_count INTEGER NOT NULL,
    random_seed INTEGER NOT NULL,
    contamination REAL NOT NULL,
    created_at_utc TEXT NOT NULL,
    FOREIGN KEY(baseline_id) REFERENCES baseline_profiles(id) ON DELETE CASCADE
)
"""

CREATE_DEVIATION_ASSESSMENTS_TABLE = """
CREATE TABLE IF NOT EXISTS deviation_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feature_window_id INTEGER NOT NULL UNIQUE,
    evaluation_timestamp_utc TEXT NOT NULL,
    device_id TEXT NOT NULL,
    baseline_id INTEGER NOT NULL,
    baseline_scope TEXT NOT NULL,
    baseline_readiness TEXT NOT NULL,
    statistical_summary_json TEXT NOT NULL,
    isolation_forest_score REAL,
    isolation_forest_result TEXT NOT NULL,
    deviation_index REAL,
    overall_level TEXT NOT NULL,
    top_contributing_metrics_json TEXT NOT NULL,
    reason_codes_json TEXT NOT NULL,
    workload_context TEXT,
    relevant_event_context_json TEXT NOT NULL,
    data_quality_status TEXT NOT NULL,
    FOREIGN KEY(feature_window_id) REFERENCES feature_windows(id) ON DELETE CASCADE,
    FOREIGN KEY(baseline_id) REFERENCES baseline_profiles(id)
)
"""

CREATE_DEVIATION_FEATURE_RESULTS_TABLE = """
CREATE TABLE IF NOT EXISTS deviation_feature_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assessment_id INTEGER NOT NULL,
    feature_name TEXT NOT NULL,
    observed_value REAL,
    baseline_centre REAL,
    expected_low REAL,
    expected_high REAL,
    deviation_direction TEXT NOT NULL,
    deviation_magnitude REAL,
    normalized_deviation_score REAL,
    severity_band TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    baseline_scope TEXT NOT NULL,
    FOREIGN KEY(assessment_id) REFERENCES deviation_assessments(id) ON DELETE CASCADE,
    UNIQUE(assessment_id, feature_name)
)
"""

CREATE_RISK_EVALUATION_RUNS_TABLE = """
CREATE TABLE IF NOT EXISTS risk_evaluation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT NOT NULL,
    command TEXT NOT NULL,
    device_id TEXT,
    start_timestamp_utc TEXT,
    end_timestamp_utc TEXT,
    force_requested INTEGER NOT NULL DEFAULT 0,
    algorithm_version TEXT NOT NULL,
    configuration_version TEXT NOT NULL,
    catalogue_version TEXT NOT NULL,
    status TEXT NOT NULL,
    assessed_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    skip_reasons_json TEXT NOT NULL,
    error_code TEXT
)
"""

CREATE_RISK_ASSESSMENTS_TABLE = """
CREATE TABLE IF NOT EXISTS risk_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feature_window_id INTEGER NOT NULL,
    deviation_assessment_id INTEGER NOT NULL,
    baseline_id INTEGER NOT NULL,
    device_id TEXT NOT NULL,
    window_start_utc TEXT NOT NULL,
    window_end_utc TEXT NOT NULL,
    workload_context TEXT NOT NULL,
    workload_confidence REAL NOT NULL,
    evaluated_at_utc TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    configuration_version TEXT NOT NULL,
    catalogue_version TEXT NOT NULL,
    baseline_updated_at_utc TEXT NOT NULL,
    deviation_evaluated_at_utc TEXT NOT NULL,
    risk_evidence_index REAL NOT NULL,
    evidence_level TEXT NOT NULL,
    data_quality_status TEXT NOT NULL,
    temporal_pattern TEXT NOT NULL,
    persistence_window_count INTEGER NOT NULL,
    reason_codes_json TEXT NOT NULL,
    FOREIGN KEY(feature_window_id) REFERENCES feature_windows(id) ON DELETE CASCADE,
    FOREIGN KEY(deviation_assessment_id) REFERENCES deviation_assessments(id) ON DELETE CASCADE,
    FOREIGN KEY(baseline_id) REFERENCES baseline_profiles(id),
    UNIQUE(feature_window_id, algorithm_version, configuration_version)
)
"""

CREATE_RISK_EVIDENCE_COMPONENTS_TABLE = """
CREATE TABLE IF NOT EXISTS risk_evidence_components (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    risk_assessment_id INTEGER NOT NULL,
    component_name TEXT NOT NULL,
    correlation_group TEXT NOT NULL DEFAULT '',
    raw_value REAL,
    normalized_value REAL,
    weight REAL NOT NULL,
    contribution REAL NOT NULL,
    evidence_json TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    FOREIGN KEY(risk_assessment_id) REFERENCES risk_assessments(id) ON DELETE CASCADE,
    UNIQUE(risk_assessment_id, component_name, correlation_group)
)
"""

CREATE_ROOT_CAUSE_CANDIDATES_TABLE = """
CREATE TABLE IF NOT EXISTS root_cause_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    risk_assessment_id INTEGER NOT NULL,
    candidate_domain TEXT NOT NULL,
    rank INTEGER NOT NULL,
    evidence_confidence REAL NOT NULL,
    workload_context TEXT NOT NULL,
    first_observed_utc TEXT NOT NULL,
    persistence_duration_seconds REAL NOT NULL,
    reason_codes_json TEXT NOT NULL,
    explanation TEXT NOT NULL,
    recommended_verification_steps_json TEXT NOT NULL,
    limitations_json TEXT NOT NULL,
    FOREIGN KEY(risk_assessment_id) REFERENCES risk_assessments(id) ON DELETE CASCADE,
    UNIQUE(risk_assessment_id, candidate_domain),
    UNIQUE(risk_assessment_id, rank)
)
"""

CREATE_CANDIDATE_EVIDENCE_TABLE = """
CREATE TABLE IF NOT EXISTS root_cause_candidate_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL,
    evidence_kind TEXT NOT NULL,
    evidence_key TEXT NOT NULL,
    observed_value_json TEXT NOT NULL,
    supports_candidate INTEGER NOT NULL,
    reason_code TEXT NOT NULL,
    FOREIGN KEY(candidate_id) REFERENCES root_cause_candidates(id) ON DELETE CASCADE
)
"""

CREATE_HEALTH_EVALUATION_RUNS_TABLE = """
CREATE TABLE IF NOT EXISTS health_evaluation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT NOT NULL,
    command TEXT NOT NULL,
    device_id TEXT,
    start_timestamp_utc TEXT,
    end_timestamp_utc TEXT,
    feature_window_id INTEGER,
    force_requested INTEGER NOT NULL DEFAULT 0,
    algorithm_version TEXT NOT NULL,
    configuration_version TEXT NOT NULL,
    status TEXT NOT NULL,
    assessed_count INTEGER NOT NULL DEFAULT 0,
    not_evaluated_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    skip_reasons_json TEXT NOT NULL,
    error_code TEXT,
    FOREIGN KEY(feature_window_id) REFERENCES feature_windows(id)
)
"""

CREATE_HEALTH_ASSESSMENTS_TABLE = """
CREATE TABLE IF NOT EXISTS health_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feature_window_id INTEGER NOT NULL,
    device_id TEXT NOT NULL,
    window_start_utc TEXT NOT NULL,
    window_end_utc TEXT NOT NULL,
    assessed_at_utc TEXT NOT NULL,
    system_health_score REAL,
    health_band TEXT NOT NULL,
    evaluation_state TEXT NOT NULL,
    data_confidence REAL NOT NULL,
    coverage_ratio REAL NOT NULL,
    workload_context TEXT,
    workload_confidence REAL,
    available_component_weight REAL NOT NULL,
    excluded_component_weight REAL NOT NULL,
    normalization_method TEXT NOT NULL,
    baseline_id INTEGER,
    baseline_algorithm_version TEXT,
    baseline_configuration_version TEXT,
    baseline_updated_at_utc TEXT,
    deviation_assessment_id INTEGER,
    deviation_evaluated_at_utc TEXT,
    risk_assessment_id INTEGER,
    risk_evaluated_at_utc TEXT,
    algorithm_version TEXT NOT NULL,
    configuration_version TEXT NOT NULL,
    reason_codes_json TEXT NOT NULL,
    first_observed_utc TEXT,
    most_recent_observed_utc TEXT,
    consecutive_window_count INTEGER NOT NULL DEFAULT 0,
    persistence_duration_seconds REAL NOT NULL DEFAULT 0,
    trend_direction TEXT NOT NULL,
    recovery_state TEXT NOT NULL,
    feature_signature TEXT NOT NULL,
    feature_updated_at_utc TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    FOREIGN KEY(feature_window_id) REFERENCES feature_windows(id) ON DELETE CASCADE,
    FOREIGN KEY(baseline_id) REFERENCES baseline_profiles(id),
    FOREIGN KEY(deviation_assessment_id) REFERENCES deviation_assessments(id),
    FOREIGN KEY(risk_assessment_id) REFERENCES risk_assessments(id),
    UNIQUE(feature_window_id, algorithm_version, configuration_version)
)
"""

CREATE_HEALTH_COMPONENT_SCORES_TABLE = """
CREATE TABLE IF NOT EXISTS health_component_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    health_assessment_id INTEGER NOT NULL,
    component_name TEXT NOT NULL,
    component_score REAL NOT NULL,
    configured_weight REAL NOT NULL,
    effective_weight REAL NOT NULL,
    available_subcomponent_weight REAL NOT NULL,
    excluded_subcomponent_weight REAL NOT NULL,
    raw_deduction_total REAL NOT NULL,
    effective_deduction_total REAL NOT NULL,
    data_quality_status TEXT NOT NULL,
    reason_codes_json TEXT NOT NULL,
    details_json TEXT NOT NULL,
    FOREIGN KEY(health_assessment_id) REFERENCES health_assessments(id) ON DELETE CASCADE,
    UNIQUE(health_assessment_id, component_name)
)
"""

CREATE_HEALTH_DEDUCTIONS_TABLE = """
CREATE TABLE IF NOT EXISTS health_deductions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    health_assessment_id INTEGER NOT NULL,
    component_name TEXT NOT NULL,
    contribution_group TEXT NOT NULL,
    signal_name TEXT NOT NULL,
    raw_deduction REAL NOT NULL,
    effective_deduction REAL NOT NULL,
    maximum_deduction REAL NOT NULL,
    correlation_or_cap_reason TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    explanation TEXT NOT NULL,
    supporting_value_json TEXT NOT NULL,
    supporting_event_ids_json TEXT NOT NULL,
    workload_context TEXT,
    FOREIGN KEY(health_assessment_id) REFERENCES health_assessments(id) ON DELETE CASCADE
)
"""

CREATE_HEALTH_INPUT_STATUS_TABLE = """
CREATE TABLE IF NOT EXISTS health_input_status (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    health_assessment_id INTEGER NOT NULL,
    input_name TEXT NOT NULL,
    input_category TEXT NOT NULL,
    availability_status TEXT NOT NULL,
    observed_value_json TEXT NOT NULL,
    excluded_reason TEXT,
    applicable_weight REAL NOT NULL DEFAULT 0,
    FOREIGN KEY(health_assessment_id) REFERENCES health_assessments(id) ON DELETE CASCADE,
    UNIQUE(health_assessment_id, input_name)
)
"""

CREATE_HEALTH_GUIDANCE_TABLE = """
CREATE TABLE IF NOT EXISTS health_guidance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    health_assessment_id INTEGER NOT NULL,
    guidance_type TEXT NOT NULL,
    related_component TEXT NOT NULL DEFAULT '',
    sequence INTEGER NOT NULL,
    guidance_text TEXT NOT NULL,
    FOREIGN KEY(health_assessment_id) REFERENCES health_assessments(id) ON DELETE CASCADE,
    UNIQUE(health_assessment_id, guidance_type, related_component, sequence)
)
"""

CREATE_INVENTORY_SNAPSHOTS_TABLE = """
CREATE TABLE IF NOT EXISTS device_inventory_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    captured_at_utc TEXT NOT NULL,
    last_checked_at_utc TEXT NOT NULL,
    inventory_signature TEXT NOT NULL,
    provider_version TEXT NOT NULL,
    detection_confidence REAL NOT NULL,
    inventory_state TEXT NOT NULL,
    reason_codes_json TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    UNIQUE(device_id, inventory_signature)
)
"""

EXPANDED_INVENTORY_SNAPSHOT_COLUMNS = {
    # Nullable during ALTER TABLE so a database created by an early development
    # build can be upgraded; initialize_database immediately backfills it.
    "last_checked_at_utc": "TEXT",
}

CREATE_INVENTORY_VALUES_TABLE = """
CREATE TABLE IF NOT EXISTS inventory_component_values (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inventory_snapshot_id INTEGER NOT NULL,
    component_group TEXT NOT NULL,
    field_name TEXT NOT NULL,
    value_json TEXT NOT NULL,
    availability_status TEXT NOT NULL,
    reliability_note TEXT,
    source_name TEXT NOT NULL,
    FOREIGN KEY(inventory_snapshot_id)
        REFERENCES device_inventory_snapshots(id) ON DELETE CASCADE,
    UNIQUE(inventory_snapshot_id, field_name)
)
"""

CREATE_QUALITY_RUNS_TABLE = """
CREATE TABLE IF NOT EXISTS quality_evaluation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT NOT NULL,
    command TEXT NOT NULL,
    device_id TEXT,
    profile_key TEXT,
    force_requested INTEGER NOT NULL DEFAULT 0,
    algorithm_version TEXT NOT NULL,
    configuration_version TEXT NOT NULL,
    catalogue_version TEXT NOT NULL,
    status TEXT NOT NULL,
    assessed_count INTEGER NOT NULL DEFAULT 0,
    provisional_count INTEGER NOT NULL DEFAULT 0,
    not_evaluated_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    reason_codes_json TEXT NOT NULL,
    error_code TEXT
)
"""

CREATE_QUALITY_ASSESSMENTS_TABLE = """
CREATE TABLE IF NOT EXISTS workload_suitability_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    inventory_snapshot_id INTEGER NOT NULL,
    inventory_timestamp_utc TEXT NOT NULL,
    assessed_at_utc TEXT NOT NULL,
    profile_key TEXT NOT NULL,
    profile_name TEXT NOT NULL,
    profile_version TEXT NOT NULL,
    suitability_index REAL,
    suitability_result TEXT NOT NULL,
    evaluation_state TEXT NOT NULL,
    detection_confidence REAL NOT NULL,
    available_component_weight REAL NOT NULL,
    excluded_component_weight REAL NOT NULL,
    normalization_method TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    configuration_version TEXT NOT NULL,
    catalogue_version TEXT NOT NULL,
    health_assessment_id INTEGER,
    risk_assessment_id INTEGER,
    current_readiness_json TEXT NOT NULL,
    reason_codes_json TEXT NOT NULL,
    explanation TEXT NOT NULL,
    limitations_json TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    FOREIGN KEY(inventory_snapshot_id)
        REFERENCES device_inventory_snapshots(id),
    FOREIGN KEY(health_assessment_id) REFERENCES health_assessments(id),
    FOREIGN KEY(risk_assessment_id) REFERENCES risk_assessments(id),
    UNIQUE(
        device_id, inventory_snapshot_id, profile_key, profile_version,
        algorithm_version, configuration_version
    )
)
"""

CREATE_QUALITY_COMPONENT_RESULTS_TABLE = """
CREATE TABLE IF NOT EXISTS suitability_component_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assessment_id INTEGER NOT NULL,
    component_name TEXT NOT NULL,
    detected_value_json TEXT NOT NULL,
    detection_status TEXT NOT NULL,
    minimum_threshold_json TEXT NOT NULL,
    recommended_threshold_json TEXT NOT NULL,
    raw_component_score REAL,
    effective_component_score REAL,
    configured_weight REAL NOT NULL,
    effective_weight REAL NOT NULL,
    minimum_passed INTEGER,
    recommended_passed INTEGER,
    hard_gate_status TEXT NOT NULL,
    reason_codes_json TEXT NOT NULL,
    explanation TEXT NOT NULL,
    limitations_json TEXT NOT NULL,
    suggested_action TEXT,
    FOREIGN KEY(assessment_id)
        REFERENCES workload_suitability_assessments(id) ON DELETE CASCADE,
    UNIQUE(assessment_id, component_name)
)
"""

CREATE_QUALITY_GATES_TABLE = """
CREATE TABLE IF NOT EXISTS suitability_gates_caps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assessment_id INTEGER NOT NULL,
    component_name TEXT NOT NULL,
    rule_type TEXT NOT NULL,
    configured_cap REAL,
    applied INTEGER NOT NULL,
    pre_cap_score REAL,
    post_cap_score REAL,
    reason_code TEXT NOT NULL,
    explanation TEXT NOT NULL,
    FOREIGN KEY(assessment_id)
        REFERENCES workload_suitability_assessments(id) ON DELETE CASCADE
)
"""

CREATE_QUALITY_LIMITING_TABLE = """
CREATE TABLE IF NOT EXISTS suitability_limiting_components (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assessment_id INTEGER NOT NULL,
    component_name TEXT NOT NULL,
    rank INTEGER NOT NULL,
    severity TEXT NOT NULL,
    capability_gap REAL,
    reason_code TEXT NOT NULL,
    explanation TEXT NOT NULL,
    FOREIGN KEY(assessment_id)
        REFERENCES workload_suitability_assessments(id) ON DELETE CASCADE,
    UNIQUE(assessment_id, rank),
    UNIQUE(assessment_id, component_name)
)
"""

CREATE_QUALITY_RECOMMENDATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS suitability_recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assessment_id INTEGER NOT NULL,
    component_name TEXT NOT NULL,
    priority TEXT NOT NULL,
    rank INTEGER NOT NULL,
    current_capability_json TEXT NOT NULL,
    target_minimum_json TEXT NOT NULL,
    target_recommended_json TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    explanation TEXT NOT NULL,
    expected_suitability_benefit TEXT NOT NULL,
    limitation TEXT NOT NULL,
    FOREIGN KEY(assessment_id)
        REFERENCES workload_suitability_assessments(id) ON DELETE CASCADE,
    UNIQUE(assessment_id, rank)
)
"""

CREATE_ALERT_EVALUATION_RUNS_TABLE = """
CREATE TABLE IF NOT EXISTS alert_evaluation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feature_window_id INTEGER,
    device_id TEXT,
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT NOT NULL,
    command TEXT NOT NULL,
    force_requested INTEGER NOT NULL DEFAULT 0,
    algorithm_version TEXT NOT NULL,
    configuration_version TEXT NOT NULL,
    catalogue_version TEXT NOT NULL,
    source_signature TEXT NOT NULL,
    status TEXT NOT NULL,
    created_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    recovered_count INTEGER NOT NULL DEFAULT 0,
    resolved_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    skip_reasons_json TEXT NOT NULL,
    error_code TEXT,
    FOREIGN KEY(feature_window_id) REFERENCES feature_windows(id) ON DELETE CASCADE,
    UNIQUE(feature_window_id, algorithm_version, configuration_version, source_signature)
)
"""

CREATE_ALERTS_TABLE = """
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    alert_fingerprint TEXT NOT NULL,
    lifecycle_number INTEGER NOT NULL,
    predecessor_alert_id INTEGER,
    alert_code TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    evidence_domain TEXT NOT NULL,
    probable_factor TEXT NOT NULL,
    current_severity TEXT NOT NULL,
    peak_severity TEXT NOT NULL,
    state TEXT NOT NULL,
    evaluation_state TEXT NOT NULL,
    data_confidence REAL NOT NULL,
    workload_context TEXT,
    workload_confidence REAL,
    first_observed_utc TEXT NOT NULL,
    latest_observed_utc TEXT NOT NULL,
    last_evidence_utc TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL,
    consecutive_window_count INTEGER NOT NULL,
    duration_seconds REAL NOT NULL,
    trend_direction TEXT NOT NULL,
    recovery_window_count INTEGER NOT NULL DEFAULT 0,
    recovery_state TEXT NOT NULL,
    cooldown_until_utc TEXT,
    baseline_id INTEGER,
    deviation_assessment_id INTEGER,
    risk_assessment_id INTEGER,
    health_assessment_id INTEGER NOT NULL,
    source_feature_window_id INTEGER NOT NULL,
    probable_factors_json TEXT NOT NULL,
    contradictory_evidence_json TEXT NOT NULL,
    excluded_inputs_json TEXT NOT NULL,
    reason_codes_json TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    configuration_version TEXT NOT NULL,
    catalogue_version TEXT NOT NULL,
    acknowledged_at_utc TEXT,
    resolved_at_utc TEXT,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    FOREIGN KEY(predecessor_alert_id) REFERENCES alerts(id),
    FOREIGN KEY(baseline_id) REFERENCES baseline_profiles(id),
    FOREIGN KEY(deviation_assessment_id) REFERENCES deviation_assessments(id),
    FOREIGN KEY(risk_assessment_id) REFERENCES risk_assessments(id),
    FOREIGN KEY(health_assessment_id) REFERENCES health_assessments(id),
    FOREIGN KEY(source_feature_window_id) REFERENCES feature_windows(id),
    UNIQUE(alert_fingerprint, lifecycle_number)
)
"""

CREATE_ALERT_OCCURRENCES_TABLE = """
CREATE TABLE IF NOT EXISTS alert_occurrences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id INTEGER NOT NULL,
    feature_window_id INTEGER NOT NULL,
    health_assessment_id INTEGER NOT NULL,
    risk_assessment_id INTEGER,
    deviation_assessment_id INTEGER,
    observed_at_utc TEXT NOT NULL,
    severity TEXT NOT NULL,
    condition_met INTEGER NOT NULL,
    raw_evidence_strength REAL NOT NULL,
    effective_evidence_strength REAL NOT NULL,
    temporal_pattern TEXT NOT NULL,
    trend_direction TEXT NOT NULL,
    evidence_signature TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    FOREIGN KEY(alert_id) REFERENCES alerts(id) ON DELETE CASCADE,
    FOREIGN KEY(feature_window_id) REFERENCES feature_windows(id),
    FOREIGN KEY(health_assessment_id) REFERENCES health_assessments(id),
    FOREIGN KEY(risk_assessment_id) REFERENCES risk_assessments(id),
    FOREIGN KEY(deviation_assessment_id) REFERENCES deviation_assessments(id),
    UNIQUE(alert_id, feature_window_id)
)
"""

CREATE_ALERT_EVIDENCE_TABLE = """
CREATE TABLE IF NOT EXISTS alert_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurrence_id INTEGER NOT NULL,
    evidence_type TEXT NOT NULL,
    evidence_key TEXT NOT NULL,
    source_table TEXT NOT NULL,
    source_id INTEGER,
    correlation_group TEXT NOT NULL,
    raw_evidence_json TEXT NOT NULL,
    effective_evidence_json TEXT NOT NULL,
    suppressed INTEGER NOT NULL DEFAULT 0,
    suppression_reason TEXT,
    reason_code TEXT NOT NULL,
    explanation TEXT NOT NULL,
    FOREIGN KEY(occurrence_id) REFERENCES alert_occurrences(id) ON DELETE CASCADE,
    UNIQUE(occurrence_id, evidence_type, evidence_key, source_table, source_id)
)
"""

CREATE_ALERT_TRANSITIONS_TABLE = """
CREATE TABLE IF NOT EXISTS alert_state_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id INTEGER NOT NULL,
    transition_timestamp_utc TEXT NOT NULL,
    previous_state TEXT,
    new_state TEXT NOT NULL,
    previous_severity TEXT,
    new_severity TEXT NOT NULL,
    transition_type TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    source_feature_window_id INTEGER,
    metadata_json TEXT NOT NULL,
    FOREIGN KEY(alert_id) REFERENCES alerts(id) ON DELETE CASCADE,
    FOREIGN KEY(source_feature_window_id) REFERENCES feature_windows(id)
)
"""

CREATE_ALERT_EXPLANATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS alert_explanations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurrence_id INTEGER NOT NULL,
    explanation_type TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    explanation_text TEXT NOT NULL,
    FOREIGN KEY(occurrence_id) REFERENCES alert_occurrences(id) ON DELETE CASCADE,
    UNIQUE(occurrence_id, explanation_type, sequence)
)
"""

CREATE_ALERT_RECOMMENDATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS alert_recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurrence_id INTEGER NOT NULL,
    recommendation_type TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    recommendation_text TEXT NOT NULL,
    FOREIGN KEY(occurrence_id) REFERENCES alert_occurrences(id) ON DELETE CASCADE,
    UNIQUE(occurrence_id, recommendation_type, sequence)
)
"""

CREATE_NOTIFICATION_PREFERENCES_TABLE = """
CREATE TABLE IF NOT EXISTS notification_preferences (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
    eligible_severities_json TEXT NOT NULL,
    feature_started_at_utc TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
)
"""

CREATE_NOTIFICATION_DELIVERIES_TABLE = """
CREATE TABLE IF NOT EXISTS notification_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id INTEGER NOT NULL,
    lifecycle_number INTEGER NOT NULL,
    severity TEXT NOT NULL,
    notification_type TEXT NOT NULL
        CHECK(notification_type IN ('activation', 'escalation')),
    attempted_at_utc TEXT NOT NULL,
    delivery_status TEXT NOT NULL
        CHECK(delivery_status IN ('attempting', 'delivered', 'failed')),
    failure_reason TEXT,
    deep_link_url TEXT NOT NULL,
    provider_name TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    FOREIGN KEY(alert_id) REFERENCES alerts(id) ON DELETE CASCADE,
    UNIQUE(alert_id, lifecycle_number, severity, notification_type)
)
"""

CREATE_VALIDATION_OBSERVATION_PERIODS_TABLE = """
CREATE TABLE IF NOT EXISTS validation_observation_periods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    start_utc TEXT NOT NULL,
    end_utc TEXT,
    state TEXT NOT NULL,
    incident_reporting_complete INTEGER NOT NULL DEFAULT 0,
    expected_window_count INTEGER,
    eligible_window_count INTEGER,
    coverage_ratio REAL,
    missing_intervals_json TEXT NOT NULL,
    interruption_notes TEXT,
    reason_codes_json TEXT NOT NULL,
    limitations_json TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    configuration_version TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    closed_at_utc TEXT,
    updated_at_utc TEXT NOT NULL,
    CHECK(state IN ('open', 'completed', 'incomplete', 'withdrawn')),
    CHECK(incident_reporting_complete IN (0, 1)),
    CHECK(end_utc IS NULL OR end_utc >= start_utc)
)
"""

CREATE_INCIDENT_REPORTS_TABLE = """
CREATE TABLE IF NOT EXISTS incident_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    current_revision_number INTEGER NOT NULL,
    category TEXT NOT NULL,
    severity TEXT NOT NULL,
    start_utc TEXT NOT NULL,
    end_utc TEXT,
    timestamp_precision TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    verification_source TEXT,
    workload_context TEXT,
    symptoms_text TEXT NOT NULL,
    windows_event_ids_json TEXT NOT NULL,
    action_taken TEXT,
    observed_outcome TEXT,
    status TEXT NOT NULL,
    data_confidence REAL NOT NULL,
    reason_codes_json TEXT NOT NULL,
    limitations_json TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    CHECK(status IN ('active', 'withdrawn')),
    CHECK(timestamp_precision IN ('exact', 'approximate')),
    CHECK(end_utc IS NULL OR end_utc >= start_utc),
    UNIQUE(id, current_revision_number)
)
"""

CREATE_INCIDENT_REVISIONS_TABLE = """
CREATE TABLE IF NOT EXISTS incident_report_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL,
    revision_number INTEGER NOT NULL,
    category TEXT NOT NULL,
    severity TEXT NOT NULL,
    start_utc TEXT NOT NULL,
    end_utc TEXT,
    timestamp_precision TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    verification_source TEXT,
    workload_context TEXT,
    symptoms_text TEXT NOT NULL,
    windows_event_ids_json TEXT NOT NULL,
    action_taken TEXT,
    observed_outcome TEXT,
    status TEXT NOT NULL,
    data_confidence REAL NOT NULL,
    reason_codes_json TEXT NOT NULL,
    limitations_json TEXT NOT NULL,
    revision_reason TEXT NOT NULL,
    supersedes_revision_id INTEGER,
    created_at_utc TEXT NOT NULL,
    FOREIGN KEY(incident_id) REFERENCES incident_reports(id) ON DELETE CASCADE,
    FOREIGN KEY(supersedes_revision_id) REFERENCES incident_report_revisions(id),
    UNIQUE(incident_id, revision_number)
)
"""

CREATE_ALERT_FEEDBACK_TABLE = """
CREATE TABLE IF NOT EXISTS alert_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id INTEGER NOT NULL UNIQUE,
    current_revision_number INTEGER NOT NULL,
    outcome TEXT NOT NULL,
    observation_horizon_seconds REAL,
    verification_status TEXT NOT NULL,
    verification_timestamp_utc TEXT NOT NULL,
    verification_source TEXT,
    notes TEXT,
    user_reason_codes_json TEXT NOT NULL,
    structured_action_taken TEXT,
    condition_state TEXT NOT NULL,
    preventive_action_taken INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    data_confidence REAL NOT NULL,
    reason_codes_json TEXT NOT NULL,
    limitations_json TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    FOREIGN KEY(alert_id) REFERENCES alerts(id) ON DELETE CASCADE,
    CHECK(preventive_action_taken IN (0, 1)),
    CHECK(status IN ('active', 'withdrawn')),
    UNIQUE(id, current_revision_number)
)
"""

CREATE_ALERT_FEEDBACK_REVISIONS_TABLE = """
CREATE TABLE IF NOT EXISTS alert_feedback_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feedback_id INTEGER NOT NULL,
    revision_number INTEGER NOT NULL,
    outcome TEXT NOT NULL,
    observation_horizon_seconds REAL,
    verification_status TEXT NOT NULL,
    verification_timestamp_utc TEXT NOT NULL,
    verification_source TEXT,
    notes TEXT,
    user_reason_codes_json TEXT NOT NULL,
    structured_action_taken TEXT,
    condition_state TEXT NOT NULL,
    preventive_action_taken INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    data_confidence REAL NOT NULL,
    reason_codes_json TEXT NOT NULL,
    limitations_json TEXT NOT NULL,
    revision_reason TEXT NOT NULL,
    supersedes_revision_id INTEGER,
    created_at_utc TEXT NOT NULL,
    FOREIGN KEY(feedback_id) REFERENCES alert_feedback(id) ON DELETE CASCADE,
    FOREIGN KEY(supersedes_revision_id) REFERENCES alert_feedback_revisions(id),
    CHECK(preventive_action_taken IN (0, 1)),
    UNIQUE(feedback_id, revision_number)
)
"""

CREATE_ALERT_INCIDENT_LINKS_TABLE = """
CREATE TABLE IF NOT EXISTS alert_incident_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id INTEGER NOT NULL,
    incident_id INTEGER NOT NULL,
    match_type TEXT NOT NULL,
    origin TEXT NOT NULL,
    confirmed_by_user INTEGER NOT NULL DEFAULT 0,
    matching_score REAL NOT NULL,
    time_difference_seconds REAL,
    category_compatible INTEGER NOT NULL,
    matching_rule TEXT NOT NULL,
    supporting_evidence_json TEXT NOT NULL,
    contradictory_evidence_json TEXT NOT NULL,
    reason_codes_json TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    configuration_version TEXT NOT NULL,
    matching_version TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    FOREIGN KEY(alert_id) REFERENCES alerts(id) ON DELETE CASCADE,
    FOREIGN KEY(incident_id) REFERENCES incident_reports(id) ON DELETE CASCADE,
    CHECK(origin IN ('automatic', 'manual')),
    CHECK(confirmed_by_user IN (0, 1)),
    UNIQUE(alert_id, incident_id, matching_version)
)
"""

CREATE_VALIDATION_RUNS_TABLE = """
CREATE TABLE IF NOT EXISTS validation_evaluation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT,
    start_utc TEXT,
    end_utc TEXT,
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT NOT NULL,
    status TEXT NOT NULL,
    evidence_signature TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    configuration_version TEXT NOT NULL,
    matching_version TEXT NOT NULL,
    eligible_alert_count INTEGER NOT NULL DEFAULT 0,
    eligible_incident_count INTEGER NOT NULL DEFAULT 0,
    eligible_window_count INTEGER NOT NULL DEFAULT 0,
    matched_count INTEGER NOT NULL DEFAULT 0,
    excluded_count INTEGER NOT NULL DEFAULT 0,
    distinct_observation_days INTEGER NOT NULL DEFAULT 0,
    data_confidence REAL NOT NULL DEFAULT 0,
    confidence_level TEXT NOT NULL DEFAULT 'insufficient',
    reason_codes_json TEXT NOT NULL,
    limitations_json TEXT NOT NULL,
    error_code TEXT,
    created_at_utc TEXT NOT NULL,
    UNIQUE(device_id, start_utc, end_utc, evidence_signature,
           algorithm_version, configuration_version, matching_version)
)
"""

CREATE_VALIDATION_METRICS_TABLE = """
CREATE TABLE IF NOT EXISTS validation_metric_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluation_run_id INTEGER NOT NULL,
    scope_type TEXT NOT NULL,
    scope_value TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    numerator REAL,
    denominator REAL,
    metric_value REAL,
    evaluation_state TEXT NOT NULL,
    data_confidence REAL NOT NULL,
    minimum_requirement INTEGER NOT NULL,
    reason_codes_json TEXT NOT NULL,
    reconstruction_json TEXT NOT NULL,
    FOREIGN KEY(evaluation_run_id)
        REFERENCES validation_evaluation_runs(id) ON DELETE CASCADE,
    UNIQUE(evaluation_run_id, scope_type, scope_value, metric_name)
)
"""

CREATE_WARNING_LEAD_TIMES_TABLE = """
CREATE TABLE IF NOT EXISTS warning_lead_time_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluation_run_id INTEGER NOT NULL,
    alert_incident_link_id INTEGER NOT NULL,
    alert_id INTEGER NOT NULL,
    incident_id INTEGER NOT NULL,
    alert_first_observed_utc TEXT NOT NULL,
    incident_start_utc TEXT NOT NULL,
    lead_time_seconds REAL NOT NULL,
    timing_state TEXT NOT NULL,
    eligible INTEGER NOT NULL,
    reason_codes_json TEXT NOT NULL,
    FOREIGN KEY(evaluation_run_id)
        REFERENCES validation_evaluation_runs(id) ON DELETE CASCADE,
    FOREIGN KEY(alert_incident_link_id) REFERENCES alert_incident_links(id),
    FOREIGN KEY(alert_id) REFERENCES alerts(id),
    FOREIGN KEY(incident_id) REFERENCES incident_reports(id),
    UNIQUE(evaluation_run_id, alert_incident_link_id)
)
"""

CREATE_VALIDATION_INCLUSION_TABLE = """
CREATE TABLE IF NOT EXISTS validation_inclusion_exclusion (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluation_run_id INTEGER NOT NULL,
    evidence_type TEXT NOT NULL,
    evidence_id INTEGER NOT NULL,
    included INTEGER NOT NULL,
    classification TEXT,
    reason_codes_json TEXT NOT NULL,
    details_json TEXT NOT NULL,
    FOREIGN KEY(evaluation_run_id)
        REFERENCES validation_evaluation_runs(id) ON DELETE CASCADE,
    CHECK(included IN (0, 1)),
    UNIQUE(evaluation_run_id, evidence_type, evidence_id)
)
"""

EXPANDED_HEALTH_ASSESSMENT_COLUMNS = {
    "feature_signature": "TEXT NOT NULL DEFAULT ''",
}

EXPANDED_VALIDATION_PERIOD_COLUMNS = {
    "closed_at_utc": "TEXT",
    "declared_outcome": "TEXT NOT NULL DEFAULT 'not_declared'",
    "related_incident_id": "INTEGER",
    "eligibility_state": "TEXT NOT NULL DEFAULT 'not_evaluated'",
}

EXPANDED_ALERT_FEEDBACK_COLUMNS = {
    "verification_timestamp_utc": "TEXT NOT NULL DEFAULT ''",
    "user_reason_codes_json": "TEXT NOT NULL DEFAULT '[]'",
    "structured_action_taken": "TEXT",
    "condition_state": "TEXT NOT NULL DEFAULT 'unclear'",
}

EXPANDED_ALERT_FEEDBACK_REVISION_COLUMNS = {
    "verification_timestamp_utc": "TEXT NOT NULL DEFAULT ''",
    "user_reason_codes_json": "TEXT NOT NULL DEFAULT '[]'",
    "structured_action_taken": "TEXT",
    "condition_state": "TEXT NOT NULL DEFAULT 'unclear'",
}

EXPANDED_VALIDATION_RUN_COLUMNS = {
    "distinct_observation_days": "INTEGER NOT NULL DEFAULT 0",
    "data_confidence": "REAL NOT NULL DEFAULT 0",
    "confidence_level": "TEXT NOT NULL DEFAULT 'insufficient'",
    "eligible_observation_period_count": "INTEGER NOT NULL DEFAULT 0",
    "observation_coverage": "REAL",
    "maturity_label": "TEXT NOT NULL DEFAULT 'insufficient'",
    "additional_positive_needed": "INTEGER NOT NULL DEFAULT 0",
    "additional_negative_needed": "INTEGER NOT NULL DEFAULT 0",
    "validation_start_utc": "TEXT",
    "validation_end_utc": "TEXT",
}

EXPANDED_VALIDATION_METRIC_COLUMNS = {
    "confidence_interval_lower": "REAL",
    "confidence_interval_upper": "REAL",
    "confidence_interval_method": "TEXT",
    "maturity_label": "TEXT NOT NULL DEFAULT 'insufficient'",
}

# Phase 1 databases are migrated by adding only missing nullable columns.
EXPANDED_METRIC_COLUMNS = {
    "cpu_per_core_json": "TEXT",
    "cpu_physical_cores": "INTEGER",
    "cpu_logical_cores": "INTEGER",
    "cpu_frequency_mhz": "REAL",
    "process_count": "INTEGER",
    "thread_count": "INTEGER",
    "ram_available_bytes": "INTEGER",
    "swap_percent": "REAL",
    "swap_used_bytes": "INTEGER",
    "swap_total_bytes": "INTEGER",
    "disk_free_bytes": "INTEGER",
    "disk_partitions_json": "TEXT",
    "disk_read_bytes_per_second": "REAL",
    "disk_write_bytes_per_second": "REAL",
    "disk_read_ops_per_second": "REAL",
    "disk_write_ops_per_second": "REAL",
    "disk_read_bytes_total": "INTEGER",
    "disk_write_bytes_total": "INTEGER",
    "disk_read_ops_total": "INTEGER",
    "disk_write_ops_total": "INTEGER",
    "network_upload_bytes_per_second": "REAL",
    "network_download_bytes_per_second": "REAL",
    "network_packets_sent_per_second": "REAL",
    "network_packets_received_per_second": "REAL",
    "network_bytes_sent_total": "INTEGER",
    "network_bytes_received_total": "INTEGER",
    "network_packets_sent_total": "INTEGER",
    "network_packets_received_total": "INTEGER",
    "network_interface_available": "INTEGER",
    "battery_percent": "REAL",
    "battery_charging": "INTEGER",
    "ac_power_connected": "INTEGER",
    "battery_seconds_remaining": "REAL",
    "boot_timestamp_utc": "TEXT",
    "user_idle_seconds": "REAL",
    "user_state": "TEXT",
    "foreground_process_name": "TEXT",
    "cpu_temperature_celsius": "REAL",
    "gpu_utilization_percent": "REAL",
    "gpu_memory_percent": "REAL",
    "gpu_temperature_celsius": "REAL",
    "workload_class": "TEXT",
    "workload_confidence": "REAL",
    "workload_reasons_json": "TEXT",
}

EXPANDED_FEATURE_COLUMNS = {
    "gpu_memory_avg": "REAL",
    "gpu_memory_max": "REAL",
    "gpu_temperature_avg": "REAL",
    "gpu_temperature_max": "REAL",
    "gpu_utilization_missing_ratio": "REAL",
    "gpu_memory_missing_ratio": "REAL",
    "gpu_temperature_missing_ratio": "REAL",
}

CREATE_INDEXES = (
    """
    CREATE INDEX IF NOT EXISTS idx_metrics_timestamp
    ON metrics(timestamp_utc DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_metrics_device_timestamp
    ON metrics(device_id, timestamp_utc DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_process_snapshots_metric
    ON process_snapshots(metric_id, category, rank)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_events_device_timestamp
    ON windows_events(device_id, event_timestamp_utc DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_events_category
    ON windows_events(smartops_category, event_level)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_features_device_start
    ON feature_windows(device_id, window_start_utc DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_baselines_device_scope
    ON baseline_profiles(device_id, workload_scope)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_training_runs_device_time
    ON baseline_training_runs(device_id, finished_at_utc DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_deviations_device_time
    ON deviation_assessments(device_id, evaluation_timestamp_utc DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_deviations_scope_level
    ON deviation_assessments(baseline_scope, overall_level)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_risk_device_window
    ON risk_assessments(device_id, window_start_utc DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_risk_level_window
    ON risk_assessments(evidence_level, window_start_utc DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_risk_workload_window
    ON risk_assessments(workload_context, window_start_utc DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_risk_runs_time
    ON risk_evaluation_runs(finished_at_utc DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_root_causes_assessment_rank
    ON root_cause_candidates(risk_assessment_id, rank)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_candidate_evidence_candidate
    ON root_cause_candidate_evidence(candidate_id, supports_candidate)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_health_device_window
    ON health_assessments(device_id, window_start_utc DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_health_band_state_window
    ON health_assessments(health_band, evaluation_state, window_start_utc DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_health_workload_window
    ON health_assessments(workload_context, window_start_utc DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_health_runs_time
    ON health_evaluation_runs(finished_at_utc DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_health_deductions_assessment
    ON health_deductions(health_assessment_id, component_name)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_inventory_device_time
    ON device_inventory_snapshots(device_id, captured_at_utc DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_inventory_values_snapshot
    ON inventory_component_values(inventory_snapshot_id, component_group)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_quality_device_time
    ON workload_suitability_assessments(device_id, assessed_at_utc DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_quality_profile_result
    ON workload_suitability_assessments(
        profile_key, suitability_result, evaluation_state, assessed_at_utc DESC
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_quality_components_assessment
    ON suitability_component_results(assessment_id, component_name)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_quality_runs_time
    ON quality_evaluation_runs(finished_at_utc DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_alert_runs_window
    ON alert_evaluation_runs(feature_window_id, finished_at_utc DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_alerts_device_state_time
    ON alerts(device_id, state, latest_observed_utc DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_alerts_category_severity
    ON alerts(category, current_severity, latest_observed_utc DESC)
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_alerts_one_active_fingerprint
    ON alerts(alert_fingerprint) WHERE state != 'resolved'
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_alert_occurrences_window
    ON alert_occurrences(feature_window_id, observed_at_utc DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_alert_transitions_alert_time
    ON alert_state_transitions(alert_id, transition_timestamp_utc)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_notification_deliveries_alert
    ON notification_deliveries(alert_id, lifecycle_number, attempted_at_utc DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_notification_deliveries_status_time
    ON notification_deliveries(delivery_status, attempted_at_utc DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_validation_period_device_time
    ON validation_observation_periods(device_id, start_utc DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_incidents_device_time
    ON incident_reports(device_id, start_utc DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_incidents_category_status
    ON incident_reports(category, verification_status, status)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_feedback_outcome_status
    ON alert_feedback(outcome, verification_status, status)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_alert_incident_links_alert
    ON alert_incident_links(alert_id, match_type)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_alert_incident_links_incident
    ON alert_incident_links(incident_id, match_type)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_validation_runs_time
    ON validation_evaluation_runs(finished_at_utc DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_validation_metrics_run
    ON validation_metric_results(evaluation_run_id, metric_name)
    """,
)


def _configure_connection(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    # NORMAL is SQLite's recommended durability/performance balance for WAL:
    # committed data remains transactionally consistent while avoiding a full
    # filesystem sync for every small local telemetry transaction.
    connection.execute(f"PRAGMA synchronous = {SQLITE_SYNCHRONOUS_POLICY}")


def _configure_persistent_database_policy(
    path: Path,
    connection: sqlite3.Connection,
) -> None:
    """Persist WAL once per database/process, never once per sample."""
    key = _database_key(path)
    with _PERSISTENT_POLICY_LOCK:
        if key in _PERSISTENT_POLICY_PATHS:
            return
        mode = connection.execute(
            f"PRAGMA journal_mode = {SQLITE_JOURNAL_MODE}"
        ).fetchone()[0]
        if str(mode).casefold() != SQLITE_JOURNAL_MODE.casefold():
            raise sqlite3.OperationalError(
                f"Could not enable SQLite {SQLITE_JOURNAL_MODE} journal mode."
            )
        _PERSISTENT_POLICY_PATHS.add(key)


def _add_missing_columns(
    connection: sqlite3.Connection,
    table: str,
    expected_columns: dict[str, str],
) -> None:
    existing_columns = {
        row[1] for row in connection.execute(f"PRAGMA table_info({table})")
    }
    for column_name, column_type in expected_columns.items():
        if column_name not in existing_columns:
            connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {column_name} {column_type}"
            )


def initialize_database(database_path: Path | None = None) -> Path:
    """Create or safely migrate the local database without deleting records."""
    path = database_path or get_database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # sqlite3.Connection's own context manager commits/rolls back but does not
    # close. `closing` guarantees startup/migration connections cannot leak.
    with closing(_open_connection(path, priority="migration")) as connection:
        _configure_persistent_database_policy(path, connection)
        # Status/read API helpers call initialize_database defensively.  Once
        # the current additive migration is present, return before opening a
        # write transaction.  This keeps concurrent GET requests read-only and
        # avoids contending with the telemetry agent's short insert transaction.
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version == 15:
            # Schema 16 is deliberately tiny: it adds only the two durable
            # single-runtime ownership triggers. Avoid replaying every older
            # additive migration against a large, already-current database.
            with connection:
                for statement in PHASE7B1_SCHEMA_STATEMENTS[-2:]:
                    connection.execute(statement)
                connection.execute(f"PRAGMA user_version = {PHASE7B1_SCHEMA_VERSION}")
            version = PHASE7B1_SCHEMA_VERSION
        if version == 16:
            with connection:
                for statement in PHASE7B2_SCHEMA_STATEMENTS:
                    connection.execute(statement)
                for table_name, columns in PHASE7B2_ADDITIONAL_COLUMNS.items():
                    _add_missing_columns(connection, table_name, columns)
                for statement in PHASE7B2_INDEXES:
                    connection.execute(statement)
                finish_phase7b2_migration(connection)
                connection.execute(f"PRAGMA user_version = {PHASE7B2_SCHEMA_VERSION}")
            version = PHASE7B2_SCHEMA_VERSION
        if version == PHASE7B2_SCHEMA_VERSION:
            # Python's sqlite3 does not implicitly open a transaction for DDL.
            # Begin explicitly so a failed additive migration cannot leave a
            # partially-created schema behind.
            try:
                connection.execute("BEGIN IMMEDIATE")
                for statement in POSTCALIBRATION_SCHEMA_STATEMENTS:
                    connection.execute(statement)
                for statement in POSTCALIBRATION_INDEXES:
                    connection.execute(statement)
                finish_postcalibration_migration(connection)
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            return path
        if version == 18:
            # Schema 19 extends validation only. Replaying the older baseline
            # migration finalizers here would rebuild immutable archived
            # membership audit data, so migrate this path narrowly.
            try:
                connection.execute("BEGIN IMMEDIATE")
                for table_name, columns in (
                    (
                        "validation_observation_periods",
                        EXPANDED_VALIDATION_PERIOD_COLUMNS,
                    ),
                    ("validation_evaluation_runs", EXPANDED_VALIDATION_RUN_COLUMNS),
                    ("validation_metric_results", EXPANDED_VALIDATION_METRIC_COLUMNS),
                ):
                    _add_missing_columns(connection, table_name, columns)
                connection.execute(POSTCALIBRATION_SCHEMA_STATEMENTS[-1])
                for statement in POSTCALIBRATION_INDEXES[-2:]:
                    connection.execute(statement)
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            return path
        if version >= SCHEMA_VERSION:
            enhanced_tables = {
                row[0]
                for row in connection.execute(
                    """SELECT name FROM sqlite_master
                    WHERE type = 'table' AND name LIKE 'enhanced_%'"""
                )
            }
            feedback_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(alert_feedback)")
            }
            run_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(validation_evaluation_runs)"
                )
            }
            metric_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(metrics)")
            }
            feature_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(feature_windows)")
            }
            version_profile_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(baseline_version_profiles)"
                )
            }
            baseline_version_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(baseline_versions)"
                )
            }
            assessment_rule_columns = {
                table: {
                    row[1]
                    for row in connection.execute(f"PRAGMA table_info({table})")
                }
                for table in (
                    "deviation_assessments",
                    "risk_assessments",
                    "health_assessments",
                    "alerts",
                )
            }
            if (
                set(EXPANDED_ALERT_FEEDBACK_COLUMNS) <= feedback_columns
                and set(EXPANDED_VALIDATION_RUN_COLUMNS) <= run_columns
                and connection.execute(
                    """SELECT 1 FROM sqlite_master
                    WHERE type = 'table' AND name = 'notification_preferences'"""
                ).fetchone()
                and connection.execute(
                    """SELECT 1 FROM sqlite_master
                    WHERE type = 'table' AND name = 'notification_deliveries'"""
                ).fetchone()
                and {
                    "enhanced_collection_runs",
                    "enhanced_signal_samples",
                    "enhanced_signal_hourly",
                    "enhanced_event_evidence",
                    "enhanced_collector_state",
                    "enhanced_retention_runs",
                }
                <= enhanced_tables
                and {
                    "user_activity_state",
                    "system_activity_state",
                    "workload_rule_version",
                    "workload_provenance_json",
                }
                <= metric_columns
                and {
                    "dominant_user_activity_state",
                    "dominant_system_activity_state",
                    "workload_rule_version",
                    "workload_distribution_json",
                    "workload_majority_explanation",
                    "workload_composition_json",
                    "secondary_workload_context",
                    "secondary_workload_rule_version",
                    "secondary_workload_reason_codes_json",
                }
                <= feature_columns
                and {
                    "applicability_state",
                    "applicability_reasons_json",
                    "exclusion_reason_counts_json",
                }
                <= version_profile_columns
                and "learning_state" in baseline_version_columns
                and all(
                    "workload_rule_version" in columns
                    for columns in assessment_rule_columns.values()
                )
                and all(
                    connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                        (table_name,),
                    ).fetchone()
                    for table_name in (
                        "notification_decisions",
                        "agent_runtime_sessions",
                        "baseline_versions",
                        "baseline_version_profiles",
                        "baseline_version_feature_stats",
                        "baseline_version_training_windows",
                        "baseline_recalibration_events",
                    )
                )
            ):
                return path
        with connection:
            connection.execute(CREATE_METRICS_TABLE)
            _add_missing_columns(connection, "metrics", EXPANDED_METRIC_COLUMNS)
            connection.execute(CREATE_PROCESS_SNAPSHOTS_TABLE)
            connection.execute(CREATE_WINDOWS_EVENTS_TABLE)
            connection.execute(CREATE_EVENT_CHECKPOINTS_TABLE)
            connection.execute(CREATE_FEATURE_WINDOWS_TABLE)
            _add_missing_columns(
                connection,
                "feature_windows",
                EXPANDED_FEATURE_COLUMNS,
            )
            connection.execute(CREATE_BASELINE_PROFILES_TABLE)
            connection.execute(CREATE_BASELINE_FEATURE_STATS_TABLE)
            connection.execute(CREATE_BASELINE_TRAINING_RUNS_TABLE)
            connection.execute(CREATE_BASELINE_TRAINING_WINDOWS_TABLE)
            connection.execute(CREATE_ISOLATION_MODEL_METADATA_TABLE)
            connection.execute(CREATE_DEVIATION_ASSESSMENTS_TABLE)
            connection.execute(CREATE_DEVIATION_FEATURE_RESULTS_TABLE)
            connection.execute(CREATE_RISK_EVALUATION_RUNS_TABLE)
            connection.execute(CREATE_RISK_ASSESSMENTS_TABLE)
            connection.execute(CREATE_RISK_EVIDENCE_COMPONENTS_TABLE)
            connection.execute(CREATE_ROOT_CAUSE_CANDIDATES_TABLE)
            connection.execute(CREATE_CANDIDATE_EVIDENCE_TABLE)
            connection.execute(CREATE_HEALTH_EVALUATION_RUNS_TABLE)
            connection.execute(CREATE_HEALTH_ASSESSMENTS_TABLE)
            _add_missing_columns(
                connection,
                "health_assessments",
                EXPANDED_HEALTH_ASSESSMENT_COLUMNS,
            )
            connection.execute(CREATE_HEALTH_COMPONENT_SCORES_TABLE)
            connection.execute(CREATE_HEALTH_DEDUCTIONS_TABLE)
            connection.execute(CREATE_HEALTH_INPUT_STATUS_TABLE)
            connection.execute(CREATE_HEALTH_GUIDANCE_TABLE)
            connection.execute(CREATE_INVENTORY_SNAPSHOTS_TABLE)
            _add_missing_columns(
                connection,
                "device_inventory_snapshots",
                EXPANDED_INVENTORY_SNAPSHOT_COLUMNS,
            )
            connection.execute(
                """UPDATE device_inventory_snapshots
                SET last_checked_at_utc = captured_at_utc
                WHERE last_checked_at_utc IS NULL"""
            )
            connection.execute(CREATE_INVENTORY_VALUES_TABLE)
            connection.execute(CREATE_QUALITY_RUNS_TABLE)
            connection.execute(CREATE_QUALITY_ASSESSMENTS_TABLE)
            connection.execute(CREATE_QUALITY_COMPONENT_RESULTS_TABLE)
            connection.execute(CREATE_QUALITY_GATES_TABLE)
            connection.execute(CREATE_QUALITY_LIMITING_TABLE)
            connection.execute(CREATE_QUALITY_RECOMMENDATIONS_TABLE)
            connection.execute(CREATE_ALERT_EVALUATION_RUNS_TABLE)
            connection.execute(CREATE_ALERTS_TABLE)
            connection.execute(CREATE_ALERT_OCCURRENCES_TABLE)
            connection.execute(CREATE_ALERT_EVIDENCE_TABLE)
            connection.execute(CREATE_ALERT_TRANSITIONS_TABLE)
            connection.execute(CREATE_ALERT_EXPLANATIONS_TABLE)
            connection.execute(CREATE_ALERT_RECOMMENDATIONS_TABLE)
            connection.execute(CREATE_NOTIFICATION_PREFERENCES_TABLE)
            connection.execute(CREATE_NOTIFICATION_DELIVERIES_TABLE)
            connection.execute(CREATE_VALIDATION_OBSERVATION_PERIODS_TABLE)
            _add_missing_columns(
                connection,
                "validation_observation_periods",
                EXPANDED_VALIDATION_PERIOD_COLUMNS,
            )
            connection.execute(CREATE_INCIDENT_REPORTS_TABLE)
            connection.execute(CREATE_INCIDENT_REVISIONS_TABLE)
            connection.execute(CREATE_ALERT_FEEDBACK_TABLE)
            _add_missing_columns(
                connection,
                "alert_feedback",
                EXPANDED_ALERT_FEEDBACK_COLUMNS,
            )
            connection.execute(CREATE_ALERT_FEEDBACK_REVISIONS_TABLE)
            _add_missing_columns(
                connection,
                "alert_feedback_revisions",
                EXPANDED_ALERT_FEEDBACK_REVISION_COLUMNS,
            )
            connection.execute(CREATE_ALERT_INCIDENT_LINKS_TABLE)
            connection.execute(CREATE_VALIDATION_RUNS_TABLE)
            _add_missing_columns(
                connection,
                "validation_evaluation_runs",
                EXPANDED_VALIDATION_RUN_COLUMNS,
            )
            connection.execute(CREATE_VALIDATION_METRICS_TABLE)
            _add_missing_columns(
                connection,
                "validation_metric_results",
                EXPANDED_VALIDATION_METRIC_COLUMNS,
            )
            connection.execute(CREATE_WARNING_LEAD_TIMES_TABLE)
            connection.execute(CREATE_VALIDATION_INCLUSION_TABLE)
            for statement in ENHANCED_SCHEMA_STATEMENTS:
                connection.execute(statement)
            for statement in PHASE7B1_SCHEMA_STATEMENTS:
                connection.execute(statement)
            for table_name, columns in PHASE7B1_ADDITIONAL_COLUMNS.items():
                _add_missing_columns(connection, table_name, columns)
            for statement in PHASE7B2_SCHEMA_STATEMENTS:
                connection.execute(statement)
            for table_name, columns in PHASE7B2_ADDITIONAL_COLUMNS.items():
                _add_missing_columns(connection, table_name, columns)
            finish_phase7b1_migration(connection)
            for statement in CREATE_INDEXES:
                connection.execute(statement)
            for statement in ENHANCED_INDEXES:
                connection.execute(statement)
            for statement in PHASE7B1_INDEXES:
                connection.execute(statement)
            for statement in PHASE7B2_INDEXES:
                connection.execute(statement)
            finish_phase7b2_migration(connection)
            for statement in POSTCALIBRATION_SCHEMA_STATEMENTS:
                connection.execute(statement)
            for statement in POSTCALIBRATION_INDEXES:
                connection.execute(statement)
            finish_postcalibration_migration(connection)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return path


@contextmanager
def database_connection(
    database_path: Path | None = None,
    *,
    priority: str | None = None,
) -> Iterator[sqlite3.Connection]:
    """Open one canonically configured, always-closed SQLite connection."""
    path = database_path or get_database_path()
    resolved_priority = priority or getattr(_WRITER_CONTEXT, "priority", "normal")
    connection = _open_connection(path, priority=resolved_priority)
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def read_only_database_connection(database_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Open an immutable-intent query-only connection without running migrations."""
    path = (database_path or get_database_path()).resolve()
    uri = f"file:{path.as_posix()}?mode=ro"
    connection = sqlite3.connect(
        uri, uri=True, timeout=SQLITE_CONNECTION_TIMEOUT_SECONDS,
        factory=SmartOpsConnection,
    )
    connection.configure_writer(path, "normal")
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    connection.execute("PRAGMA query_only = ON")
    try:
        yield connection
    finally:
        connection.close()


def backup_database(source_path: Path, target_path: Path) -> None:
    """Create a consistent SQLite backup without copying live sidecar files."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with read_only_database_connection(source_path) as source:
        target = sqlite3.connect(target_path, timeout=SQLITE_CONNECTION_TIMEOUT_SECONDS)
        try:
            source.backup(target, pages=2048, sleep=0.05)
            target.commit()
        finally:
            target.close()
