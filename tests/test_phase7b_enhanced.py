"""Controlled Phase 7B enhanced-evidence tests use isolated databases only."""

from __future__ import annotations

import sqlite3
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import agent.enhanced as enhanced
from agent.enhanced import (
    classify_structured_event,
    collect_enhanced_evidence,
    collect_hardware_signals,
    collect_performance_signals,
    finish_enhanced_collection,
)
from agent.enhanced_catalogue import PERFORMANCE_SIGNALS
from backend.database import database_connection, initialize_database
from backend.enhanced_repository import (
    create_collection_run,
    get_enhanced_status,
    run_retention,
    store_enhanced_events,
    store_signal_samples,
    unmirrored_windows_events,
)
from backend.main import create_app
from backend.repository import insert_metric
from tests.test_database import sample


def performance_result() -> list[dict[str, object]]:
    return [
        {
            "path": definition.counter_path,
            "cooked_value": 0.0 if "queue" in definition.key else 12.5,
            "status": 0,
        }
        for definition in PERFORMANCE_SIGNALS
    ]


def hardware_result(*, battery_present: bool = True) -> dict[str, object]:
    return {
        "storage_reason": None,
        "reliability": [
            {
                "temperature": 38,
                "wear": 4,
                "read_errors_total": 2,
                "read_errors_uncorrected": 0,
                "write_errors_total": 1,
                "write_errors_uncorrected": 0,
            }
        ],
        "battery_present": battery_present,
        "full_capacity": [40_000] if battery_present else [],
        "design_capacity": [50_000] if battery_present else [],
        "battery_status": (
            [
                {
                    "discharge_rate": 8_000,
                    "remaining_capacity": 30_000,
                    "power_online": False,
                    "discharging": True,
                }
            ]
            if battery_present
            else []
        ),
    }


def combined_runner(script: str, _timeout: int) -> object:
    return performance_result() if "Get-Counter" in script else hardware_result()


def current_samples(path: Path, count: int = 5) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    latest: dict[str, object] = {}
    with database_connection(path) as connection:
        for index in range(count):
            latest = sample((now - timedelta(seconds=(count - index) * 30)).isoformat())
            latest["cpu_per_core_percent"] = [99.0, 25.0]
            latest["ac_power_connected"] = index % 2 == 0
            insert_metric(connection, latest)
    return latest


def test_supported_windows_counters_preserve_real_zero(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(enhanced.sys, "platform", "win32")
    signals, succeeded, reason = collect_performance_signals(
        lambda _script, _timeout: performance_result()
    )
    by_key = {item["signal_key"]: item for item in signals}
    assert succeeded is True
    assert reason is None
    assert by_key["cpu_processor_queue_length"]["numeric_value"] == 0.0
    assert by_key["cpu_processor_queue_length"]["availability_status"] == "available"
    assert by_key["memory_paging_duration_percent"]["numeric_value"] is None
    assert by_key["memory_paging_duration_percent"]["reason_code"] == (
        "not_reliably_exposed_by_supported_counter"
    )


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_reason"),
    [
        (
            subprocess.TimeoutExpired("powershell", 5),
            "timeout",
            "query_timeout",
        ),
        (
            subprocess.CalledProcessError(1, "powershell"),
            "permission_limited",
            "counter_query_failed_or_permission_limited",
        ),
    ],
)
def test_counter_timeout_and_permission_failure_are_graceful(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_status: str,
    expected_reason: str,
):
    monkeypatch.setattr(enhanced.sys, "platform", "win32")

    def failed(_script: str, _timeout: int) -> object:
        raise error

    signals, succeeded, reason = collect_performance_signals(failed)
    assert succeeded is False
    assert reason == expected_reason
    assert signals
    assert all(item["numeric_value"] is None for item in signals)
    assert all(item["availability_status"] == expected_status for item in signals)


def test_missing_optional_hardware_is_not_zero(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(enhanced.sys, "platform", "win32")
    payload = hardware_result(battery_present=False)
    payload["reliability"] = []
    payload["storage_reason"] = "storage_reliability_not_exposed_by_hardware"
    signals, succeeded, _ = collect_hardware_signals(
        lambda _script, _timeout: payload
    )
    by_key = {item["signal_key"]: item for item in signals}
    assert succeeded is True
    assert by_key["storage_temperature_celsius"]["numeric_value"] is None
    assert by_key["storage_temperature_celsius"]["reason_code"] == (
        "storage_reliability_not_exposed_by_hardware"
    )
    assert by_key["battery_health_percent"]["availability_status"] == "not_applicable"
    assert by_key["battery_health_percent"]["numeric_value"] is None


def test_hardware_capacity_and_reliability_values_are_structured(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(enhanced.sys, "platform", "win32")
    signals, succeeded, reason = collect_hardware_signals(
        lambda _script, _timeout: hardware_result()
    )
    by_key = {item["signal_key"]: item for item in signals}
    assert succeeded is True
    assert reason is None
    assert by_key["storage_temperature_celsius"]["numeric_value"] == 38.0
    assert by_key["battery_health_percent"]["numeric_value"] == 80.0
    assert by_key["battery_discharge_rate_mw"]["numeric_value"] == 8_000.0


@pytest.mark.parametrize(
    ("provider", "event_id", "level", "expected_subtype"),
    [
        ("Microsoft-Windows-WHEA-Logger", 17, "Warning", "whea_corrected"),
        ("Microsoft-Windows-Kernel-Power", 41, "Critical", "unexpected_shutdown_or_power_loss"),
        ("Application Hang", 1002, "Error", "application_hang"),
        ("Service Control Manager", 7032, "Error", "service_recovery_attempt"),
        ("disk", 7, "Warning", "storage_driver_warning"),
    ],
)
def test_structured_event_classification(
    provider: str,
    event_id: int,
    level: str,
    expected_subtype: str,
):
    result = classify_structured_event(
        {
            "id": 1,
            "device_id": "device",
            "event_timestamp_utc": "2026-01-01T00:00:00+00:00",
            "channel": "System",
            "provider_name": provider,
            "event_id": event_id,
            "record_id": 22,
            "event_level": level,
            "smartops_category": "system_warning",
            "safe_summary": "Safe mapped summary.",
        }
    )
    assert result["evidence_subtype"] == expected_subtype
    assert "message" not in result["details"]


def test_enhanced_event_storage_is_deduplicated(tmp_path: Path):
    path = tmp_path / "events.db"
    initialize_database(path)
    event = {
        "windows_event_id": None,
        "device_id": "device",
        "event_timestamp_utc": "2026-01-01T00:00:00+00:00",
        "evidence_group": "application",
        "evidence_type": "application_stability",
        "evidence_subtype": "application_crash",
        "evidence_level": "error",
        "source_name": "test source",
        "source_record_key": "test:1",
        "reason_code": "application_crash_event",
        "safe_summary": "Safe mapped summary.",
        "created_at_utc": "2026-01-01T00:00:01+00:00",
        "details": {},
    }
    with database_connection(path) as connection:
        assert store_enhanced_events(connection, [event]) == 1
        assert store_enhanced_events(connection, [event]) == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM enhanced_event_evidence"
        ).fetchone()[0] == 1


def test_expired_core_events_are_not_recreated_after_retention(tmp_path: Path):
    path = tmp_path / "expired-events.db"
    initialize_database(path)
    now = datetime(2026, 7, 31, tzinfo=timezone.utc)
    with database_connection(path) as connection:
        for record_id, event_time in (
            (1, "2025-01-01T00:00:00+00:00"),
            (2, "2026-07-30T00:00:00+00:00"),
        ):
            connection.execute(
                """
                INSERT INTO windows_events (
                    device_id, event_timestamp_utc, channel, provider_name,
                    event_id, record_id, event_level, smartops_category,
                    safe_summary, collected_at_utc
                ) VALUES (
                    'device', ?, 'System', 'disk', 7, ?, 'warning',
                    'storage_warning', 'Safe mapped summary.', ?
                )
                """,
                (event_time, record_id, event_time),
            )
        rows = unmirrored_windows_events(connection, now_utc=now)
    assert [row["record_id"] for row in rows] == [2]


def test_schema10_migrates_to_schema11_without_data_loss(tmp_path: Path):
    path = tmp_path / "schema10.db"
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """INSERT INTO metrics (timestamp_utc, device_id)
            VALUES ('2026-01-01T00:00:00+00:00', 'preserved')"""
        )
        for table in (
            "enhanced_signal_samples",
            "enhanced_signal_hourly",
            "enhanced_event_evidence",
            "enhanced_collector_state",
            "enhanced_retention_runs",
            "enhanced_collection_runs",
        ):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("PRAGMA user_version = 10")
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        preserved = connection.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
        enhanced_tables = {
            row[0]
            for row in connection.execute(
                """SELECT name FROM sqlite_master
                WHERE type = 'table' AND name LIKE 'enhanced_%'"""
            )
        }
    assert version == 18
    assert preserved == 1
    assert len(enhanced_tables) == 6


def test_next_collection_closes_an_abandoned_running_run(tmp_path: Path):
    path = tmp_path / "abandoned-run.db"
    initialize_database(path)
    with database_connection(path) as connection:
        first_id = create_collection_run(
            connection,
            device_id="device",
            started_at_utc="2026-07-31T10:00:00+00:00",
            due_collectors=["performance_counters"],
            process_rss_before_bytes=None,
            database_bytes_before=None,
            algorithm_version="test",
            configuration_version="test",
        )
        create_collection_run(
            connection,
            device_id="device",
            started_at_utc="2026-07-31T10:01:00+00:00",
            due_collectors=["performance_counters"],
            process_rss_before_bytes=None,
            database_bytes_before=None,
            algorithm_version="test",
            configuration_version="test",
        )
        first = connection.execute(
            """SELECT status, finished_at_utc, error_code
            FROM enhanced_collection_runs WHERE id = ?""",
            (first_id,),
        ).fetchone()
    assert tuple(first) == (
        "error",
        "2026-07-31T10:01:00+00:00",
        "abandoned_before_next_run",
    )


def test_ctrl_c_closes_current_enhanced_run_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import agent.main as agent_main

    path = tmp_path / "interrupted-agent.db"
    metric = sample("2026-07-31T10:00:00+00:00")

    class Workload:
        workload_class = "idle"
        confidence = 0.9
        reason_codes: list[str] = []

    monkeypatch.setattr(agent_main, "get_or_create_device_id", lambda: "test-device")
    monkeypatch.setattr(
        agent_main,
        "collect_metrics",
        lambda *_args, **_kwargs: dict(metric),
    )
    monkeypatch.setattr(agent_main, "classify_workload", lambda _sample: Workload())
    monkeypatch.setattr(
        agent_main,
        "poll_channel",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("event test")),
    )

    def interrupted(database_path: Path, *_args, **_kwargs):
        with database_connection(database_path) as connection:
            create_collection_run(
                connection,
                device_id="test-device",
                started_at_utc="2026-07-31T10:00:01+00:00",
                due_collectors=["performance_counters"],
                process_rss_before_bytes=None,
                database_bytes_before=None,
                algorithm_version="test",
                configuration_version="test",
            )
        raise KeyboardInterrupt

    monkeypatch.setattr(agent_main, "collect_enhanced_evidence", interrupted)
    with pytest.raises(KeyboardInterrupt):
        agent_main.collect_and_store(path)
    with database_connection(path) as connection:
        run = connection.execute(
            """SELECT status, error_code FROM enhanced_collection_runs
            ORDER BY id DESC LIMIT 1"""
        ).fetchone()
        metric_count = connection.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
    assert tuple(run) == ("error", "collection_interrupted")
    assert metric_count == 1


def test_retention_aggregates_only_old_enhanced_rows(tmp_path: Path):
    path = tmp_path / "retention.db"
    initialize_database(path)
    metric = sample("2026-06-01T00:00:00+00:00")
    with database_connection(path) as connection:
        insert_metric(connection, metric)
        run_id = create_collection_run(
            connection,
            device_id="test-device",
            started_at_utc="2026-06-01T00:00:00+00:00",
            due_collectors=["performance_counters"],
            process_rss_before_bytes=1,
            database_bytes_before=1,
            algorithm_version="test",
            configuration_version="test",
        )
        store_signal_samples(
            connection,
            run_id,
            "test-device",
            "2026-06-01T00:10:00+00:00",
            [
                {
                    "signal_group": "cpu",
                    "signal_key": "cpu_processor_queue_length",
                    "signal_label": "Processor queue length",
                    "numeric_value": 2,
                    "unit": "threads",
                    "availability_status": "available",
                    "reason_code": None,
                    "source_name": "test",
                    "source_status": "available",
                    "collection_frequency_seconds": 60,
                }
            ],
        )
        result = run_retention(
            connection,
            datetime(2026, 7, 31, tzinfo=timezone.utc),
        )
        raw_count = connection.execute(
            "SELECT COUNT(*) FROM enhanced_signal_samples"
        ).fetchone()[0]
        hourly_count = connection.execute(
            "SELECT COUNT(*) FROM enhanced_signal_hourly"
        ).fetchone()[0]
        metric_count = connection.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
    assert result["raw_samples_deleted"] == 1
    assert raw_count == 0
    assert hourly_count == 1
    assert metric_count == 1


def test_collection_is_shadow_only_and_records_overhead(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(enhanced.sys, "platform", "win32")
    path = tmp_path / "shadow.db"
    initialize_database(path)
    latest = current_samples(path)
    with database_connection(path) as connection:
        before = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("risk_assessments", "health_assessments", "alerts")
        }
    context = collect_enhanced_evidence(
        path,
        latest,
        force=True,
        runner=combined_runner,
    )
    context.started_monotonic = 100.0
    context.process_cpu_started = 10.0
    monkeypatch.setattr(enhanced.time, "monotonic", lambda: 110.0)
    monkeypatch.setattr(enhanced.time, "process_time", lambda: 14.0)
    monkeypatch.setattr(enhanced.psutil, "cpu_count", lambda logical=True: 4)
    finish_enhanced_collection(path, context)
    with database_connection(path) as connection:
        after = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("risk_assessments", "health_assessments", "alerts")
        }
        status = get_enhanced_status(connection)
        run = connection.execute(
            "SELECT * FROM enhanced_collection_runs WHERE id = ?",
            (context.run_id,),
        ).fetchone()
    assert after == before
    assert status["shadow_mode"] is True
    assert status["signals"]
    assert run["status"] == "success"
    assert run["collection_duration_ms"] >= 0
    assert run["full_cycle_duration_ms"] >= run["collection_duration_ms"]
    assert run["approximate_process_cpu_percent"] == pytest.approx(10.0)
    assert run["database_bytes_after"] is not None


def test_optional_collector_failure_does_not_prevent_other_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(enhanced.sys, "platform", "win32")
    path = tmp_path / "partial.db"
    initialize_database(path)
    latest = current_samples(path)

    def partly_failed(script: str, _timeout: int) -> object:
        if "Get-Counter" in script:
            raise subprocess.TimeoutExpired("powershell", 5)
        return hardware_result()

    context = collect_enhanced_evidence(
        path,
        latest,
        force=True,
        runner=partly_failed,
    )
    finish_enhanced_collection(path, context)
    with database_connection(path) as connection:
        statuses = {
            row["signal_key"]: row["availability_status"]
            for row in connection.execute(
                "SELECT signal_key, availability_status FROM enhanced_signal_samples"
            )
        }
        run_status = connection.execute(
            "SELECT status FROM enhanced_collection_runs WHERE id = ?",
            (context.run_id,),
        ).fetchone()[0]
    assert statuses["cpu_processor_queue_length"] == "timeout"
    assert statuses["battery_health_percent"] == "available"
    assert run_status == "partial"


def test_enhanced_api_is_read_only_and_reports_shadow_mode(tmp_path: Path):
    path = tmp_path / "api.db"
    initialize_database(path)
    current_samples(path)
    with TestClient(create_app(path)) as client:
        status = client.get("/api/enhanced-evidence/status")
        history = client.get(
            "/api/enhanced-evidence/history",
            params={"signal": "cpu_processor_queue_length"},
        )
        events = client.get("/api/enhanced-evidence/events")
        overhead = client.get("/api/enhanced-evidence/overhead")
    assert status.status_code == 200
    assert status.json()["shadow_mode"] is True
    assert history.status_code == 200
    assert history.json()["shadow_mode"] is True
    assert events.status_code == 200
    assert overhead.status_code == 200


def test_capability_filter_hides_only_permanent_unsupported_signals(tmp_path: Path):
    path = tmp_path / "capability-filter.db"
    initialize_database(path)
    metric = sample()
    with database_connection(path) as connection:
        insert_metric(connection, metric)
        first_run = create_collection_run(
            connection,
            device_id=metric["device_id"],
            started_at_utc=metric["timestamp_utc"],
            due_collectors=["performance_counters"],
            process_rss_before_bytes=None,
            database_bytes_before=None,
            algorithm_version="test",
            configuration_version="test",
        )
        signals = [
            {
                "signal_group": "storage",
                "signal_key": "storage_wear_percent",
                "signal_label": "Storage wear indication",
                "numeric_value": None,
                "unit": "percent",
                "availability_status": "unsupported",
                "reason_code": "storage_reliability_not_exposed_by_hardware",
                "source_name": "Windows Storage PowerShell",
                "source_status": "unsupported",
                "collection_frequency_seconds": 900,
            },
            {
                "signal_group": "storage",
                "signal_key": "storage_latency",
                "signal_label": "Storage latency",
                "numeric_value": None,
                "unit": "seconds",
                "availability_status": "timeout",
                "reason_code": "query_timeout",
                "source_name": "Windows Performance Counters",
                "source_status": "timeout",
                "collection_frequency_seconds": 60,
            },
        ]
        assert store_signal_samples(
            connection, first_run, metric["device_id"], metric["timestamp_utc"], signals
        ) == 2
        second_run = create_collection_run(
            connection,
            device_id=metric["device_id"],
            started_at_utc="2026-01-01T00:01:00+00:00",
            due_collectors=["performance_counters"],
            process_rss_before_bytes=None,
            database_bytes_before=None,
            algorithm_version="test",
            configuration_version="test",
        )
        # The permanent capability result is not repeatedly stored; the timeout
        # remains visible and auditable because it may recover.
        assert store_signal_samples(
            connection, second_run, metric["device_id"],
            "2026-01-01T00:01:00+00:00", signals
        ) == 1
        status = get_enhanced_status(connection)
        stored_unsupported = connection.execute(
            """SELECT COUNT(*) FROM enhanced_signal_samples
            WHERE signal_key='storage_wear_percent'"""
        ).fetchone()[0]
    assert stored_unsupported == 1
    visible_keys = {item["signal_key"] for item in status["signals"]}
    assert "storage_wear_percent" not in visible_keys
    assert "storage_latency" in visible_keys
    assert status["hidden_unsupported_signals"][0]["signal_key"] == "storage_wear_percent"
    timeout = next(item for item in status["signals"] if item["signal_key"] == "storage_latency")
    assert timeout["numeric_value"] is None
    assert timeout["readiness_state"] == "collector_failure"


def test_mocked_collection_overhead_stays_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(enhanced.sys, "platform", "win32")
    path = tmp_path / "overhead.db"
    initialize_database(path)
    latest = current_samples(path)
    started = time.perf_counter()
    context = collect_enhanced_evidence(
        path,
        latest,
        force=True,
        runner=combined_runner,
    )
    elapsed = time.perf_counter() - started
    assert elapsed < 2.0
    assert context.collection_duration_ms < 2_000
