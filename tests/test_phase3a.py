from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import analytics.baseline as baseline
from analytics.baseline import (
    baseline_status,
    evaluate_database,
    readiness_state,
    robust_deviation,
    robust_statistics,
    severity_for_score,
    train_database,
    window_eligibility,
)
from analytics.baseline_config import BaselinePolicy, DEVICE_SCOPE
from backend.database import database_connection, initialize_database
from backend.main import create_app


TEST_POLICY = BaselinePolicy(
    minimum_device_windows=6,
    minimum_workload_windows=3,
    minimum_distinct_days=3,
    isolation_minimum_windows=6,
    stale_after_days=7,
)


def insert_feature(
    connection: sqlite3.Connection,
    index: int,
    workload: str = "development",
    cpu: float | None = 30.0,
    ram: float | None = 45.0,
    complete: bool = True,
    coverage: float = 1.0,
    serious_event: bool = False,
) -> int:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(
        days=index // 2,
        minutes=(index % 2) * 5,
    )
    cursor = connection.execute(
        """INSERT INTO feature_windows (
            device_id, window_start_utc, window_end_utc, sample_count,
            expected_sample_count, coverage_ratio, is_complete,
            dominant_workload_class, workload_confidence,
            missing_indicators_json, cpu_avg, cpu_max, cpu_p95, ram_avg,
            ram_max, swap_avg, swap_max, disk_usage_avg, disk_usage_max,
            disk_read_avg, disk_write_avg, network_upload_avg,
            network_download_avg, active_ratio, idle_ratio,
            process_count_avg, hardware_event_count, updated_at_utc
        ) VALUES (?, ?, ?, 10, 10, ?, ?, ?, 0.9, '{}', ?, ?, ?, ?, ?,
                  2, 4, 50, 52, 1000, 800, 500, 700, 1, 0, 100, ?, ?)""",
        (
            "test-device",
            start.isoformat(),
            (start + timedelta(minutes=5)).isoformat(),
            coverage,
            complete,
            workload,
            cpu,
            None if cpu is None else cpu + 5,
            None if cpu is None else cpu + 3,
            ram,
            None if ram is None else ram + 2,
            1 if serious_event else 0,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    return int(cursor.lastrowid)


def seed_training_history(
    database_path: Path,
    workloads: tuple[str, ...] = ("development",) * 6,
) -> None:
    initialize_database(database_path)
    with database_connection(database_path) as connection:
        with connection:
            for index, workload in enumerate(workloads):
                insert_feature(
                    connection,
                    index,
                    workload=workload,
                    cpu=25.0 + index,
                    ram=40.0 + index,
                )


def test_window_eligibility_coverage_events_and_missing_values():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    base = {
        "is_complete": 1,
        "coverage_ratio": 1.0,
        "window_end_utc": "2025-01-01T00:05:00+00:00",
        "cpu_avg": 10.0,
        "ram_avg": 20.0,
        "disk_usage_avg": 30.0,
        "process_count_avg": 40.0,
        "active_ratio": 1.0,
        "critical_event_count": 0,
        "hardware_event_count": 0,
        "storage_event_count": 0,
        "power_event_count": 0,
    }
    assert window_eligibility(base, now, TEST_POLICY)[0] is True
    assert "incomplete_window" in window_eligibility(
        {**base, "is_complete": 0}, now, TEST_POLICY
    )[1]
    assert "coverage_below_minimum" in window_eligibility(
        {**base, "coverage_ratio": 0.79}, now, TEST_POLICY
    )[1]
    assert "critical_event_excluded" in window_eligibility(
        {**base, "critical_event_count": 1}, now, TEST_POLICY
    )[1]
    assert "serious_event_excluded" in window_eligibility(
        {**base, "storage_event_count": 1}, now, TEST_POLICY
    )[1]


def test_robust_statistics_percentiles_zero_variance_and_severity():
    stats = robust_statistics([1.0, 2.0, 100.0], 4, "two_sided")
    assert stats["valid_count"] == 3
    assert stats["missing_count"] == 1
    assert stats["median"] == 2.0
    assert stats["median_absolute_deviation"] == 1.0
    assert stats["interquartile_range"] == 49.5
    flat = robust_statistics([5.0, 5.0, 5.0], 3, "higher")
    assert flat["population_stddev"] == 0.0
    score, direction, _ = robust_deviation(5.0, flat)
    assert score == 0 and direction == "within"
    score, direction, _ = robust_deviation(8.0, flat)
    assert score == 5 and direction == "above"
    assert severity_for_score(score) == "high"
    score, _, _ = robust_deviation(2.0, flat)
    assert score == 0  # lower is not concerning for a "higher" metric
    two_sided_flat = {**flat, "direction": "two_sided"}
    score, direction, _ = robust_deviation(2.0, two_sided_flat)
    assert score == 5 and direction == "below"


def test_device_and_workload_training_are_idempotent_and_versioned(tmp_path: Path):
    database_path = tmp_path / "baseline.db"
    seed_training_history(database_path)
    first = train_database(database_path, policy=TEST_POLICY)
    second = train_database(database_path, policy=TEST_POLICY)
    with database_connection(database_path) as connection:
        profile_count = connection.execute(
            "SELECT COUNT(*) FROM baseline_profiles"
        ).fetchone()[0]
        stats_count = connection.execute(
            "SELECT COUNT(*) FROM baseline_feature_stats"
        ).fetchone()[0]
        device = connection.execute(
            "SELECT * FROM baseline_profiles WHERE workload_scope = ?",
            (DEVICE_SCOPE,),
        ).fetchone()
    assert len(first) == len(second) == 2
    assert profile_count == 2
    assert stats_count > 0
    assert device["readiness_state"] == "established"
    assert device["distinct_day_count"] == 3
    assert device["algorithm_version"] == "robust-baseline-v1"
    assert readiness_state(5, 3, DEVICE_SCOPE, TEST_POLICY) == "collecting_data"


def test_workload_fallback_context_and_isolation_are_deterministic(tmp_path: Path):
    database_path = tmp_path / "fallback.db"
    seed_training_history(
        database_path,
        ("idle", "idle", "development", "development", "gaming_or_3d", "gaming_or_3d"),
    )
    train_database(database_path, policy=TEST_POLICY)
    with database_connection(database_path) as connection:
        with connection:
            target_id = insert_feature(
                connection,
                8,
                workload="gaming_or_3d",
                cpu=95.0,
                ram=50.0,
            )
    assert evaluate_database(database_path, policy=TEST_POLICY) == 1
    with database_connection(database_path) as connection:
        assessment = connection.execute(
            "SELECT * FROM deviation_assessments WHERE feature_window_id = ?",
            (target_id,),
        ).fetchone()
        cpu_result = connection.execute(
            """SELECT r.* FROM deviation_feature_results r
            JOIN deviation_assessments a ON a.id = r.assessment_id
            WHERE a.feature_window_id = ? AND r.feature_name = 'cpu_avg'""",
            (target_id,),
        ).fetchone()
        leaked = connection.execute(
            """SELECT COUNT(*) FROM baseline_training_windows
            WHERE feature_window_id = ?""",
            (target_id,),
        ).fetchone()[0]
    assert assessment["baseline_scope"] == "device"
    assert assessment["isolation_forest_result"] in {"typical", "unusual"}
    first_score = assessment["isolation_forest_score"]
    assert leaked == 0
    assert cpu_result["severity_band"] != "high"
    assert "expected_for_gaming" in cpu_result["reason_code"]
    assert evaluate_database(
        database_path, force=True, policy=TEST_POLICY
    ) == 1
    with database_connection(database_path) as connection:
        second_score = connection.execute(
            """SELECT isolation_forest_score FROM deviation_assessments
            WHERE feature_window_id = ?""",
            (target_id,),
        ).fetchone()[0]
        assert connection.execute(
            "SELECT COUNT(*) FROM deviation_assessments"
        ).fetchone()[0] == 1
    assert second_score == pytest.approx(first_score)


def test_missing_optional_metrics_do_not_become_zero(tmp_path: Path):
    database_path = tmp_path / "missing.db"
    seed_training_history(database_path)
    profiles = train_database(database_path, policy=TEST_POLICY)
    device_profile = next(
        profile for profile in profiles if profile["workload_scope"] == DEVICE_SCOPE
    )
    assert "cpu_temperature_avg" in device_profile["missing_features"]
    with database_connection(database_path) as connection:
        value = connection.execute(
            """SELECT mean FROM baseline_feature_stats
            WHERE baseline_id = ? AND feature_name = 'cpu_temperature_avg'""",
            (device_profile["id"],),
        ).fetchone()[0]
    assert value is None


def test_failed_retraining_preserves_last_valid_profile(
    tmp_path: Path,
    monkeypatch,
):
    database_path = tmp_path / "rollback.db"
    seed_training_history(database_path)
    train_database(database_path, workload=DEVICE_SCOPE, policy=TEST_POLICY)
    with database_connection(database_path) as connection:
        before = dict(connection.execute(
            "SELECT * FROM baseline_profiles WHERE workload_scope = ?",
            (DEVICE_SCOPE,),
        ).fetchone())

    monkeypatch.setattr(
        baseline,
        "_write_profile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("test")),
    )
    with pytest.raises(RuntimeError):
        train_database(database_path, workload=DEVICE_SCOPE, policy=TEST_POLICY)
    with database_connection(database_path) as connection:
        after = dict(connection.execute(
            "SELECT * FROM baseline_profiles WHERE workload_scope = ?",
            (DEVICE_SCOPE,),
        ).fetchone())
        assert connection.execute(
            """SELECT COUNT(*) FROM baseline_training_runs
            WHERE status = 'error'"""
        ).fetchone()[0] == 1
    assert after == before


def test_stale_status_and_phase3_api_filters(tmp_path: Path):
    database_path = tmp_path / "api.db"
    seed_training_history(database_path)
    train_database(database_path, policy=TEST_POLICY)
    with database_connection(database_path) as connection:
        with connection:
            target_id = insert_feature(
                connection, 8, workload="development", cpu=90.0
            )
    evaluate_database(database_path, policy=TEST_POLICY)
    stale_policy = BaselinePolicy(
        minimum_device_windows=6,
        minimum_workload_windows=3,
        minimum_distinct_days=3,
        isolation_minimum_windows=6,
        stale_after_days=0,
    )
    assert baseline_status(database_path, policy=stale_policy)["state"] == "stale"

    with TestClient(create_app(database_path)) as client:
        assert client.get("/api/status").status_code == 200
        status = client.get("/api/baseline/status").json()
        assert status["state"] == "established"
        assert client.get(
            "/api/baseline/profiles",
            params={"workload": "development"},
        ).json()["total"] == 1
        latest = client.get("/api/deviations/latest").json()
        assert latest["status"] == "evaluated"
        page = client.get(
            "/api/deviations/history",
            params={
                "limit": 1,
                "offset": 0,
                "workload": "development",
                "sort": "newest",
            },
        ).json()
        assert page["total"] == 1 and len(page["items"]) == 1
        assert client.get(f"/api/deviations/{target_id}").status_code == 200
        assert client.get("/api/deviations/count").json() == {"count": 1}
        assert client.get("/api/deviations/history?limit=1001").status_code == 422
