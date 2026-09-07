import sqlite3
from pathlib import Path

from backend.database import database_connection, initialize_database
from backend.repository import (
    count_metrics,
    get_latest_metric,
    get_latest_process_snapshots,
    get_metric_history,
    insert_metric,
)


def sample(timestamp: str = "2026-01-01T00:00:00+00:00") -> dict[str, object]:
    return {
        "timestamp_utc": timestamp,
        "device_id": "test-device",
        "cpu_percent": 10.0,
        "cpu_per_core_percent": [5.0, 15.0],
        "cpu_physical_cores": 2,
        "cpu_logical_cores": 4,
        "cpu_frequency_mhz": 3000.0,
        "process_count": 20,
        "thread_count": 200,
        "ram_percent": 20.0,
        "ram_used_bytes": 200,
        "ram_available_bytes": 800,
        "ram_total_bytes": 1000,
        "swap_percent": 5.0,
        "swap_used_bytes": 50,
        "swap_total_bytes": 1000,
        "disk_percent": 30.0,
        "disk_used_bytes": 300,
        "disk_free_bytes": 700,
        "disk_total_bytes": 1000,
        "disk_partitions": [
            {
                "mountpoint": "C:\\",
                "percent": 30.0,
                "used_bytes": 300,
                "free_bytes": 700,
                "total_bytes": 1000,
            }
        ],
        "disk_read_bytes_per_second": 100.0,
        "disk_write_bytes_per_second": 200.0,
        "network_upload_bytes_per_second": 300.0,
        "network_download_bytes_per_second": 400.0,
        "network_interface_available": True,
        "uptime_seconds": 3600.0,
        "boot_timestamp_utc": "2025-12-31T23:00:00+00:00",
        "user_idle_seconds": 10.0,
        "user_state": "active",
        "foreground_process_name": "Code.exe",
        "process_snapshots": {
            "cpu": [
                {
                    "pid": 1,
                    "process_name": "cpu.exe",
                    "cpu_percent": 12.0,
                    "memory_percent": 2.0,
                }
            ],
            "memory": [
                {
                    "pid": 2,
                    "process_name": "memory.exe",
                    "cpu_percent": 1.0,
                    "memory_percent": 15.0,
                }
            ],
        },
    }


def test_phase_one_database_is_migrated_without_losing_rows(tmp_path: Path):
    database_path = tmp_path / "phase-one.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp_utc TEXT NOT NULL,
                device_id TEXT NOT NULL,
                cpu_percent REAL,
                ram_percent REAL,
                ram_used_bytes INTEGER,
                ram_total_bytes INTEGER,
                disk_percent REAL,
                disk_used_bytes INTEGER,
                disk_total_bytes INTEGER,
                uptime_seconds REAL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO metrics (
                timestamp_utc, device_id, cpu_percent, ram_percent,
                ram_used_bytes, ram_total_bytes, disk_percent,
                disk_used_bytes, disk_total_bytes, uptime_seconds
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-01-01T00:00:00+00:00",
                "legacy-device",
                10.0,
                20.0,
                200,
                1000,
                30.0,
                300,
                1000,
                3600.0,
            ),
        )

    initialize_database(database_path)

    with database_connection(database_path) as connection:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(metrics)")
        }
        latest = get_latest_metric(connection)
        process_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='process_snapshots'"
        ).fetchone()
        migrated_tables = {
            row["name"]
            for row in connection.execute(
                """SELECT name FROM sqlite_master
                WHERE type='table' AND name IN (
                    'windows_events', 'event_channel_checkpoints', 'feature_windows',
                    'baseline_profiles', 'baseline_feature_stats',
                    'baseline_training_runs', 'baseline_training_windows',
                    'isolation_model_metadata', 'deviation_assessments',
                    'deviation_feature_results', 'risk_evaluation_runs',
                    'risk_assessments', 'risk_evidence_components',
                    'root_cause_candidates', 'root_cause_candidate_evidence'
                )"""
            )
        }
        schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
        preserved_count = connection.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]

    assert "network_upload_bytes_per_second" in columns
    assert "cpu_per_core_json" in columns
    assert "workload_class" in columns
    assert latest is not None
    assert latest["device_id"] == "legacy-device"
    assert latest["cpu_per_core_percent"] is None
    assert process_table is not None
    assert migrated_tables == {
        "windows_events",
        "event_channel_checkpoints",
        "feature_windows",
        "baseline_profiles",
        "baseline_feature_stats",
        "baseline_training_runs",
        "baseline_training_windows",
        "isolation_model_metadata",
        "deviation_assessments",
        "deviation_feature_results",
        "risk_evaluation_runs",
        "risk_assessments",
        "risk_evidence_components",
        "root_cause_candidates",
        "root_cause_candidate_evidence",
    }
    assert schema_version == 18
    assert preserved_count == 1


def test_expanded_sample_and_processes_are_stored_transactionally(tmp_path: Path):
    database_path = tmp_path / "smartops.db"
    initialize_database(database_path)

    with database_connection(database_path) as connection:
        metric_id = insert_metric(connection, sample())
        latest = get_latest_metric(connection)
        processes = get_latest_process_snapshots(connection)

    assert metric_id == 1
    assert latest is not None
    assert latest["cpu_per_core_percent"] == [5.0, 15.0]
    assert latest["disk_partitions"][0]["mountpoint"] == "C:\\"
    assert latest["network_interface_available"] is True
    assert processes is not None
    assert processes["cpu"][0]["process_name"] == "cpu.exe"
    assert processes["memory"][0]["process_name"] == "memory.exe"


def test_history_pagination_filtering_and_sort_order(tmp_path: Path):
    database_path = tmp_path / "smartops.db"
    initialize_database(database_path)

    with database_connection(database_path) as connection:
        insert_metric(connection, sample("2026-01-01T00:00:00+00:00"))
        insert_metric(connection, sample("2026-01-01T01:00:00+00:00"))
        insert_metric(connection, sample("2026-01-01T02:00:00+00:00"))
        page, total = get_metric_history(
            connection,
            limit=1,
            offset=1,
            start_timestamp="2026-01-01T00:30:00+00:00",
            end_timestamp="2026-01-01T03:00:00+00:00",
            sort_order="oldest",
        )
        all_count = count_metrics(connection)

    assert total == 2
    assert all_count == 3
    assert len(page) == 1
    assert page[0]["timestamp_utc"] == "2026-01-01T02:00:00+00:00"
