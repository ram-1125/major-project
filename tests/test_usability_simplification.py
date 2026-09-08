from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from backend.database import database_connection, initialize_database
from backend.process_attribution import process_attribution_for_window
from backend.technical_evidence_repository import list_technical_evidence
from tests.test_phase5a import BASE, evaluate_database, make_database, prepare, seed_window


def _process_database(tmp_path: Path) -> tuple[Path, int]:
    path = tmp_path / "process-attribution.db"
    initialize_database(path)
    with database_connection(path) as connection, connection:
        window_id = seed_window(connection, 0, workload="gaming_or_3d")
        for index in range(6):
            timestamp = (BASE + timedelta(seconds=index * 30)).isoformat()
            metric_id = connection.execute(
                """INSERT INTO metrics (
                timestamp_utc, device_id, foreground_process_name,
                ram_total_bytes, cpu_percent
                ) VALUES (?, ?, ?, ?, ?)""",
                (timestamp, "alert-test-device", "VALORANT-Win64-Shipping.exe", 16 * 1024**3, 80),
            ).lastrowid
            connection.execute(
                """INSERT INTO process_snapshots
                (metric_id, category, rank, pid, process_name, cpu_percent, memory_percent)
                VALUES (?, 'cpu', 1, 101, 'VALORANT-Win64-Shipping.exe', 31, 20)""",
                (metric_id,),
            )
            connection.execute(
                """INSERT INTO process_snapshots
                (metric_id, category, rank, pid, process_name, cpu_percent, memory_percent)
                VALUES (?, 'cpu', 2, 202, 'BackgroundWorker.exe', 8, 5)""",
                (metric_id,),
            )
        # This tempting row is outside the half-open analysis period and must not leak in.
        outside_id = connection.execute(
            "INSERT INTO metrics (timestamp_utc, device_id, foreground_process_name) VALUES (?, ?, ?)",
            ((BASE + timedelta(minutes=5)).isoformat(), "alert-test-device", "Other.exe"),
        ).lastrowid
        connection.execute(
            """INSERT INTO process_snapshots
            (metric_id, category, rank, pid, process_name, cpu_percent, memory_percent)
            VALUES (?, 'cpu', 1, 303, 'Other.exe', 99, 1)""",
            (outside_id,),
        )
    return path, window_id


def test_process_attribution_uses_exact_window_and_records_primary_contributor(tmp_path: Path):
    path, window_id = _process_database(tmp_path)
    with database_connection(path) as connection:
        result = process_attribution_for_window(connection, window_id)
    assert result["state"] == "evaluated"
    assert result["attribution"] == "primary_recorded_contributor"
    assert result["primary_contributor"]["process_name"] == "VALORANT-Win64-Shipping.exe"
    assert result["primary_contributor"]["foreground_sample_count"] == 6
    assert all(item["process_name"] != "Other.exe" for item in result["contributors"])


def test_foreground_process_is_not_assumed_to_be_largest_consumer(tmp_path: Path):
    path, window_id = _process_database(tmp_path)
    with database_connection(path) as connection, connection:
        connection.execute(
            "UPDATE process_snapshots SET cpu_percent=45 WHERE process_name='BackgroundWorker.exe'"
        )
        result = process_attribution_for_window(connection, window_id)
    assert result["primary_contributor"]["process_name"] == "BackgroundWorker.exe"
    assert result["attribution"] == "likely_contributor"
    assert result["reason"] == "largest_recorded_contributor_without_foreground_causality"


def test_similar_processes_do_not_create_a_single_dominant_attribution(tmp_path: Path):
    path, window_id = _process_database(tmp_path)
    with database_connection(path) as connection, connection:
        connection.execute(
            "UPDATE process_snapshots SET cpu_percent=29 WHERE process_name='BackgroundWorker.exe'"
        )
        result = process_attribution_for_window(connection, window_id)
    assert result["attribution"] == "no_single_dominant_application_identified"
    assert result["primary_contributor"] is None
    assert result["reason"] == "multiple_similar_recorded_contributors"


def test_system_process_can_be_reported_without_being_called_a_proven_cause(tmp_path: Path):
    path, window_id = _process_database(tmp_path)
    with database_connection(path) as connection, connection:
        connection.execute(
            "UPDATE process_snapshots SET process_name='System', cpu_percent=48 "
            "WHERE process_name='BackgroundWorker.exe'"
        )
        result = process_attribution_for_window(connection, window_id)
    assert result["primary_contributor"]["process_name"] == "System"
    assert result["attribution"] == "likely_contributor"
    assert "foreground" in result["reason"]


def test_incomplete_and_missing_process_evidence_are_disclosed(tmp_path: Path):
    path, window_id = _process_database(tmp_path)
    with database_connection(path) as connection, connection:
        metric_ids = [row[0] for row in connection.execute(
            "SELECT id FROM metrics WHERE timestamp_utc >= ? AND timestamp_utc < ? ORDER BY id",
            (BASE.isoformat(), (BASE + timedelta(minutes=5)).isoformat()),
        )]
        connection.execute(
            f"DELETE FROM process_snapshots WHERE metric_id IN ({','.join('?' for _ in metric_ids[3:])})",
            metric_ids[3:],
        )
        incomplete = process_attribution_for_window(connection, window_id)
        connection.execute(
            f"DELETE FROM process_snapshots WHERE metric_id IN ({','.join('?' for _ in metric_ids[:3])})",
            metric_ids[:3],
        )
        missing = process_attribution_for_window(connection, window_id)
    assert incomplete["attribution"] == "process_attribution_unavailable"
    assert incomplete["reason"] == "incomplete_process_coverage"
    assert missing["state"] == "unavailable"
    assert missing["reason"] == "no_timestamp_matched_process_snapshots"


def test_technical_evidence_is_bounded_searchable_and_read_only(tmp_path: Path):
    path, _ = _process_database(tmp_path)
    with database_connection(path) as connection:
        before = connection.total_changes
        rows, total = list_technical_evidence(
            connection, "monitoring-processes", limit=2, offset=0,
            search="valorant", sort="newest",
        )
        after = connection.total_changes
    assert total == 6
    assert len(rows) == 2
    assert all(row["process_name"] == "VALORANT-Win64-Shipping.exe" for row in rows)
    assert before == after


def test_alert_technical_context_follows_only_the_selected_alert(tmp_path: Path):
    path = make_database(tmp_path)
    prepare(path, [{"cpu": 96}, {"cpu": 97}])
    evaluate_database(path)
    with database_connection(path) as connection:
        alert_id = int(connection.execute("SELECT id FROM alerts LIMIT 1").fetchone()[0])
        occurrence_ids = {
            int(row["id"]) for row in connection.execute(
                "SELECT id FROM alert_occurrences WHERE alert_id = ?", (alert_id,)
            )
        }
        rows, total = list_technical_evidence(
            connection, "alert-evidence", limit=100, offset=0,
            search=None, sort="newest", context_id=alert_id,
        )
    assert total == len(rows)
    assert total > 0
    assert {int(row["occurrence_id"]) for row in rows} <= occurrence_ids
