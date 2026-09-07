from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import analytics.health as health
from analytics.health import evaluate_database, health_band_for_score
from analytics.health_config import INTERPRETATION
from backend.database import database_connection, initialize_database
from backend.main import create_app
from backend.phase4a_repository import (
    get_health_assessments,
    get_health_for_window,
)


DEVICE = "health-test-device"
BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_database(tmp_path: Path) -> Path:
    path = tmp_path / "health.db"
    initialize_database(path)
    return path


def seed_window(
    connection: sqlite3.Connection,
    index: int,
    *,
    complete: bool = True,
    coverage: float = 1.0,
    workload: str = "idle",
    confidence: float = 0.9,
    cpu: float | None = 25.0,
    ram: float | None = 50.0,
    swap: float | None = 2.0,
    disk: float | None = 55.0,
    disk_read: float | None = 1_000_000.0,
    disk_write: float | None = 500_000.0,
    ram_slope: float | None = 0.0,
    cpu_high_ratio: float | None = 0.0,
    idle_ratio: float | None = 0.8,
    temperature: float | None = None,
    gpu: float | None = None,
    warnings: int = 0,
    errors: int = 0,
    critical: int = 0,
    storage_events: int = 0,
) -> int:
    start = BASE + timedelta(minutes=index * 5)
    cursor = connection.execute(
        """INSERT INTO feature_windows (
            device_id, window_start_utc, window_end_utc, sample_count,
            expected_sample_count, coverage_ratio, is_complete,
            dominant_workload_class, workload_confidence,
            missing_indicators_json, cpu_avg, cpu_min, cpu_max, cpu_p95,
            cpu_slope, cpu_high_ratio, ram_avg, ram_min, ram_max,
            ram_slope, swap_avg, swap_max, memory_high_ratio,
            disk_usage_avg, disk_usage_max, disk_read_avg, disk_read_max,
            disk_write_avg, disk_write_max, disk_usage_slope,
            network_upload_avg, network_download_avg, active_ratio,
            idle_ratio, process_count_avg, cpu_temperature_avg,
            cpu_temperature_max, gpu_utilization_avg, gpu_utilization_max,
            cpu_temperature_missing_ratio, gpu_utilization_missing_ratio,
            gpu_memory_missing_ratio, gpu_temperature_missing_ratio,
            critical_event_count, error_event_count, warning_event_count,
            storage_event_count, updated_at_utc
        ) VALUES (?, ?, ?, 10, 10, ?, ?, ?, ?, '{}', ?, 5, ?, ?, 0, ?,
                  ?, 20, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, 0,
                  1000, 2000, ?, ?, 150, ?, ?, ?, ?, ?, ?, 1, 1,
                  ?, ?, ?, ?, ?)""",
        (
            DEVICE,
            start.isoformat(),
            (start + timedelta(minutes=5)).isoformat(),
            coverage,
            int(complete),
            workload,
            confidence,
            cpu,
            cpu,
            cpu,
            cpu_high_ratio,
            ram,
            ram,
            ram_slope,
            swap,
            swap,
            disk,
            disk,
            disk_read,
            disk_read,
            disk_write,
            disk_write,
            None if idle_ratio is None else 1.0 - idle_ratio,
            idle_ratio,
            temperature,
            temperature,
            gpu,
            gpu,
            1.0 if temperature is None else 0.0,
            1.0 if gpu is None else 0.0,
            critical,
            errors,
            warnings,
            storage_events,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    return int(cursor.lastrowid)


def seed_event(
    connection: sqlite3.Connection,
    window_index: int,
    record_id: int,
    *,
    level: str = "Warning",
    category: str = "other",
) -> None:
    timestamp = BASE + timedelta(minutes=window_index * 5 + 1)
    connection.execute(
        """INSERT INTO windows_events (
            device_id, event_timestamp_utc, channel, provider_name, event_id,
            record_id, event_level, smartops_category, safe_summary,
            collected_at_utc
        ) VALUES (?, ?, 'System', 'Test Provider', 1, ?, ?, ?,
                  'Structured test summary.', ?)""",
        (
            DEVICE,
            timestamp.isoformat(),
            record_id,
            level,
            category,
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def seed_established_upstream(
    connection: sqlite3.Connection,
    window_id: int,
    *,
    deviation_index: float = 20.0,
    risk_index: float = 30.0,
    event_contribution: float = 0.0,
) -> tuple[int, int, int]:
    now = datetime.now(timezone.utc).isoformat()
    baseline_id = int(
        connection.execute(
            """INSERT INTO baseline_profiles (
                device_id, workload_scope, training_start_utc,
                training_end_utc, eligible_window_count,
                excluded_window_count, distinct_day_count, readiness_state,
                algorithm_version, configuration_version, feature_names_json,
                missing_features_json, created_at_utc, updated_at_utc
            ) VALUES (?, '__device__', ?, ?, 100, 0, 7, 'established',
                      'baseline-test', 'baseline-config', '[]', '[]', ?, ?)""",
            (
                DEVICE,
                (BASE - timedelta(days=7)).isoformat(),
                BASE.isoformat(),
                now,
                now,
            ),
        ).lastrowid
    )
    deviation_id = int(
        connection.execute(
            """INSERT INTO deviation_assessments (
                feature_window_id, evaluation_timestamp_utc, device_id,
                baseline_id, baseline_scope, baseline_readiness,
                statistical_summary_json, isolation_forest_score,
                isolation_forest_result, deviation_index, overall_level,
                top_contributing_metrics_json, reason_codes_json,
                workload_context, relevant_event_context_json,
                data_quality_status
            ) VALUES (?, ?, ?, ?, 'device', 'established', '{}', -0.1,
                      'typical', ?, 'moderate', '[]', '[]', 'idle', '{}',
                      'sufficient')""",
            (window_id, now, DEVICE, baseline_id, deviation_index),
        ).lastrowid
    )
    window = connection.execute(
        "SELECT * FROM feature_windows WHERE id = ?", (window_id,)
    ).fetchone()
    risk_id = int(
        connection.execute(
            """INSERT INTO risk_assessments (
                feature_window_id, deviation_assessment_id, baseline_id,
                device_id, window_start_utc, window_end_utc,
                workload_context, workload_confidence, evaluated_at_utc,
                algorithm_version, configuration_version, catalogue_version,
                baseline_updated_at_utc, deviation_evaluated_at_utc,
                risk_evidence_index, evidence_level, data_quality_status,
                temporal_pattern, persistence_window_count, reason_codes_json
            ) VALUES (?, ?, ?, ?, ?, ?, 'idle', 0.9, ?, 'risk-test',
                      'risk-config', 'catalogue-test', ?, ?, ?, 'guarded',
                      'sufficient', 'isolated', 1, '[]')""",
            (
                window_id,
                deviation_id,
                baseline_id,
                DEVICE,
                window["window_start_utc"],
                window["window_end_utc"],
                now,
                now,
                now,
                risk_index,
            ),
        ).lastrowid
    )
    if event_contribution:
        connection.execute(
            """INSERT INTO risk_evidence_components (
                risk_assessment_id, component_name, correlation_group,
                raw_value, normalized_value, weight, contribution,
                evidence_json, reason_code
            ) VALUES (?, 'serious_events', 'events', 1, 1, 1, ?, '{}',
                      'test_event_evidence')""",
            (risk_id, event_contribution),
        )
    return baseline_id, deviation_id, risk_id


def assessment(path: Path, window_id: int) -> dict[str, object]:
    evaluate_database(path, window_id=window_id)
    with database_connection(path) as connection:
        result = get_health_for_window(connection, window_id)
    assert result is not None
    return result


@pytest.mark.parametrize(
    ("complete", "coverage", "cpu", "expected_reason"),
    [
        (False, 1.0, 20.0, "incomplete_feature_window"),
        (True, 0.79, 20.0, "coverage_below_minimum"),
        (True, 0.80, None, "cpu_avg_unavailable"),
    ],
)
def test_ineligible_windows_are_explicit_not_evaluated(
    tmp_path: Path,
    complete: bool,
    coverage: float,
    cpu: float | None,
    expected_reason: str,
):
    path = make_database(tmp_path)
    with database_connection(path) as connection:
        with connection:
            window_id = seed_window(
                connection, 0, complete=complete, coverage=coverage, cpu=cpu
            )
    item = assessment(path, window_id)
    assert item["evaluation_state"] == "not_evaluated"
    assert item["system_health_score"] is None
    assert item["health_band"] == "not_evaluated"
    assert expected_reason in item["reason_codes"]


def test_provisional_score_normalizes_missing_optional_and_non_battery_inputs(
    tmp_path: Path,
):
    path = make_database(tmp_path)
    with database_connection(path) as connection:
        with connection:
            window_id = seed_window(connection, 0)
    item = assessment(path, window_id)
    assert item["evaluation_state"] == "provisional"
    assert item["system_health_score"] is not None
    assert item["available_component_weight"] == 90.0
    assert item["excluded_component_weight"] == 10.0
    assert sum(c["effective_weight"] for c in item["components"]) == pytest.approx(1)
    assert item["score_reconstruction"] == pytest.approx(
        item["system_health_score"], abs=0.001
    )
    excluded = {row["input_name"]: row for row in item["excluded_inputs"]}
    assert excluded["cpu_temperature_avg"]["availability_status"] == "unavailable_optional"
    assert excluded["gpu_utilization_avg"]["availability_status"] == "unavailable_optional"
    assert excluded["battery_power"]["availability_status"] == "not_applicable"
    assert all(
        row["observed_value"] is None
        for row in item["excluded_inputs"]
        if row["input_name"] in {"cpu_temperature_avg", "gpu_utilization_avg"}
    )


def test_established_requires_eligible_phase3a_and_phase3b_evidence(tmp_path: Path):
    path = make_database(tmp_path)
    with database_connection(path) as connection:
        with connection:
            window_id = seed_window(connection, 0)
            seed_established_upstream(connection, window_id)
    item = assessment(path, window_id)
    assert item["evaluation_state"] == "established"
    assert item["baseline_reference"] is not None
    assert item["deviation_reference"] is not None
    assert item["risk_reference"] is not None
    assert any(c["component_name"] == "learned_evidence" for c in item["components"])


def test_independently_corroborated_severe_condition_can_reach_critical_band(
    tmp_path: Path,
):
    path = make_database(tmp_path)
    with database_connection(path) as connection:
        with connection:
            for index in range(5):
                seed_window(
                    connection,
                    index,
                    cpu=99,
                    ram=99,
                    swap=90,
                    disk=99,
                    disk_read=200_000_000,
                    disk_write=200_000_000,
                    ram_slope=float(index),
                    cpu_high_ratio=1,
                    temperature=99,
                    gpu=99,
                )
            window_id = seed_window(
                connection,
                5,
                cpu=99,
                ram=99,
                swap=90,
                disk=99,
                disk_read=200_000_000,
                disk_write=200_000_000,
                ram_slope=5,
                cpu_high_ratio=1,
                temperature=99,
                gpu=99,
                critical=3,
                errors=8,
                storage_events=4,
            )
            for record_id in range(1, 5):
                seed_event(
                    connection,
                    5,
                    record_id,
                    level="Critical" if record_id <= 3 else "Error",
                    category="storage",
                )
            seed_established_upstream(
                connection,
                window_id,
                deviation_index=100,
                risk_index=100,
            )
    item = assessment(path, window_id)
    assert item["evaluation_state"] == "established"
    assert item["health_band"] == "critical_condition"
    assert item["system_health_score"] is not None
    assert item["system_health_score"] < 30
    assert item["score_reconstruction"] == pytest.approx(
        item["system_health_score"], abs=0.001
    )


def test_workload_aware_cpu_and_disk_io_exceptions(tmp_path: Path):
    path = make_database(tmp_path)
    with database_connection(path) as connection:
        with connection:
            idle_id = seed_window(
                connection, 0, workload="idle", cpu=92, cpu_high_ratio=0.8,
                disk_read=200_000_000, disk_write=150_000_000,
            )
            dev_id = seed_window(
                connection, 1, workload="development", cpu=92,
                cpu_high_ratio=0.8, idle_ratio=0.0,
                disk_read=200_000_000, disk_write=150_000_000,
            )
    idle = assessment(path, idle_id)
    development = assessment(path, dev_id)
    idle_cpu = next(d for d in idle["deductions"] if d["contribution_group"] == "cpu")
    dev_cpu = next(d for d in development["deductions"] if d["contribution_group"] == "cpu")
    dev_io = next(d for d in development["deductions"] if d["contribution_group"] == "disk_io")
    assert idle_cpu["effective_deduction"] > dev_cpu["effective_deduction"]
    assert dev_cpu["reason_code"] == "cpu_pressure_reduced_for_expected_workload"
    assert "workload_exception" in dev_io["correlation_or_cap_reason"]


def test_memory_swap_and_cpu_correlations_are_capped(tmp_path: Path):
    path = make_database(tmp_path)
    with database_connection(path) as connection:
        with connection:
            window_id = seed_window(
                connection, 0, cpu=99, cpu_high_ratio=1,
                ram=99, swap=90, ram_slope=1,
            )
    item = assessment(path, window_id)
    cpu = next(d for d in item["deductions"] if d["contribution_group"] == "cpu")
    memory = [
        d for d in item["deductions"]
        if d["contribution_group"] == "memory_swap"
    ]
    assert cpu["effective_deduction"] <= cpu["maximum_deduction"]
    assert sum(d["effective_deduction"] for d in memory) <= 70.0


def test_disk_capacity_pressure_and_single_warning_protection(tmp_path: Path):
    path = make_database(tmp_path)
    with database_connection(path) as connection:
        with connection:
            window_id = seed_window(connection, 0, disk=96, warnings=1)
            seed_event(connection, 0, 1)
    item = assessment(path, window_id)
    capacity = next(
        d for d in item["deductions"]
        if d["contribution_group"] == "disk_capacity"
    )
    warning = next(
        d for d in item["deductions"]
        if d["signal_name"] == "warning_events"
    )
    assert capacity["effective_deduction"] > 0
    assert warning["effective_deduction"] == 0
    assert warning["reason_code"] == "single_warning_no_deduction"


def test_serious_repeated_events_and_phase3b_overlap_are_not_double_counted(
    tmp_path: Path,
):
    path = make_database(tmp_path)
    with database_connection(path) as connection:
        with connection:
            window_id = seed_window(
                connection, 0, errors=2, storage_events=2
            )
            seed_event(connection, 0, 1, level="Error", category="storage")
            seed_event(connection, 0, 2, level="Error", category="storage")
            seed_established_upstream(
                connection,
                window_id,
                risk_index=60,
                event_contribution=30,
            )
    item = assessment(path, window_id)
    risk = next(
        d for d in item["deductions"]
        if d["contribution_group"] == "phase3b_risk"
    )
    assert risk["supporting_value"]["event_contribution_removed"] == 30
    assert risk["effective_deduction"] < risk["raw_deduction"]
    events = next(
        c for c in item["components"]
        if c["component_name"] == "operational_events"
    )
    assert events["effective_deduction_total"] <= 50


def test_persistence_increasing_and_recovery_metadata(tmp_path: Path):
    path = make_database(tmp_path)
    with database_connection(path) as connection:
        with connection:
            seed_window(connection, 0, ram=86, swap=10)
            seed_window(connection, 1, ram=85, swap=20)
            increasing_id = seed_window(connection, 2, ram=94, swap=35)
            recovery_id = seed_window(connection, 3, ram=50, swap=2)
    increasing = assessment(path, increasing_id)
    recovery = assessment(path, recovery_id)
    assert increasing["consecutive_window_count"] >= 3
    assert increasing["trend_direction"] in {"increasing_deterioration", "stable"}
    assert recovery["recovery_state"] in {"recovering", "not_recovering"}
    if recovery["recovery_state"] == "recovering":
        assert recovery["guidance"]["improvements"]


@pytest.mark.parametrize(
    ("score", "band"),
    [
        (100, "good"), (85, "good"), (84.999, "stable"), (70, "stable"),
        (69.999, "attention"), (50, "attention"), (49.999, "degraded"),
        (30, "degraded"), (29.999, "critical_condition"), (0, "critical_condition"),
    ],
)
def test_health_band_boundaries(score: float, band: str):
    assert health_band_for_score(score) == band


def test_evaluation_and_backfill_are_idempotent(tmp_path: Path):
    path = make_database(tmp_path)
    with database_connection(path) as connection:
        with connection:
            seed_window(connection, 0)
            seed_window(connection, 1, complete=False)
    first = evaluate_database(path, command="backfill")
    second = evaluate_database(path, command="backfill")
    with database_connection(path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM health_assessments"
        ).fetchone()[0]
    assert first == {"evaluated": 1, "not_evaluated": 1, "skipped": 0}
    assert second == {"evaluated": 0, "not_evaluated": 0, "skipped": 2}
    assert count == 2


def test_bookkeeping_timestamp_refresh_does_not_repeat_health_evaluation(
    tmp_path: Path,
):
    path = make_database(tmp_path)
    with database_connection(path) as connection:
        with connection:
            seed_window(connection, 0)
    evaluate_database(path, command="agent")
    with database_connection(path) as connection:
        with connection:
            connection.execute(
                """UPDATE feature_windows SET updated_at_utc = ?
                WHERE device_id = ?""",
                ((datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(), DEVICE),
            )
        before = connection.execute(
            "SELECT COUNT(*) FROM health_evaluation_runs"
        ).fetchone()[0]
    health.maybe_evaluate_health(path)
    with database_connection(path) as connection:
        after = connection.execute(
            "SELECT COUNT(*) FROM health_evaluation_runs"
        ).fetchone()[0]
    assert after == before


def test_failed_assessment_write_rolls_back_children(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    path = make_database(tmp_path)
    with database_connection(path) as connection:
        with connection:
            window_id = seed_window(connection, 0)
    original = health._persist_assessment

    def fail_after_write(connection, item):
        original(connection, item)
        raise RuntimeError("synthetic transactional failure")

    monkeypatch.setattr(health, "_persist_assessment", fail_after_write)
    result = evaluate_database(path, window_id=window_id)
    with database_connection(path) as connection:
        assessment_count = connection.execute(
            "SELECT COUNT(*) FROM health_assessments"
        ).fetchone()[0]
        component_count = connection.execute(
            "SELECT COUNT(*) FROM health_component_scores"
        ).fetchone()[0]
    assert result["skipped"] == 1
    assert assessment_count == 0
    assert component_count == 0


def test_schema_five_migrates_to_six_without_data_loss(tmp_path: Path):
    path = make_database(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """INSERT INTO metrics (
                timestamp_utc, device_id, cpu_percent
            ) VALUES ('2026-01-01T00:00:00+00:00', 'legacy', 10)"""
        )
        connection.execute("PRAGMA user_version = 5")
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        count = connection.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
        health_tables = {
            row[0]
            for row in connection.execute(
                """SELECT name FROM sqlite_master
                WHERE type='table' AND name LIKE 'health_%'"""
            )
        }
    assert version == 18
    assert count == 1
    assert {
        "health_evaluation_runs",
        "health_assessments",
        "health_component_scores",
        "health_deductions",
        "health_input_status",
        "health_guidance",
    } <= health_tables


def test_health_api_filters_paginates_reconstructs_and_is_read_only(tmp_path: Path):
    path = make_database(tmp_path)
    with database_connection(path) as connection:
        with connection:
            first_id = seed_window(connection, 0, workload="idle")
            second_id = seed_window(connection, 1, workload="development")
    evaluate_database(path)
    with database_connection(path) as connection:
        runs_before = connection.execute(
            "SELECT COUNT(*) FROM health_evaluation_runs"
        ).fetchone()[0]
    with TestClient(create_app(path)) as client:
        status = client.get("/api/health/status")
        latest = client.get("/api/health/latest")
        history = client.get(
            "/api/health/history",
            params={
                "limit": 1,
                "offset": 0,
                "sort": "oldest",
                "workload": "idle",
                "evaluation_state": "provisional",
            },
        )
        summary_history = client.get(
            "/api/health/history",
            params={"limit": 1, "summary": True},
        )
        count = client.get("/api/health/count")
        by_window = client.get(f"/api/health/{second_id}")
        old_api = client.get("/api/status")
        docs = client.get("/openapi.json")
    assert status.status_code == 200
    assert latest.json()["assessment"]["feature_window_id"] == second_id
    assert latest.json()["assessment"]["score_reconstruction"] == pytest.approx(
        latest.json()["assessment"]["system_health_score"], abs=0.001
    )
    assert history.json()["total"] == 1
    assert history.json()["items"][0]["feature_window_id"] == first_id
    assert "components" in history.json()["items"][0]
    assert summary_history.status_code == 200
    assert "components" not in summary_history.json()["items"][0]
    assert "deductions" not in summary_history.json()["items"][0]
    assert count.json()["count"] == 2
    assert by_window.json()["status"] == "provisional"
    assert old_api.status_code == 200
    assert INTERPRETATION in docs.json()["info"]["description"]
    with database_connection(path) as connection:
        runs_after = connection.execute(
            "SELECT COUNT(*) FROM health_evaluation_runs"
        ).fetchone()[0]
    assert runs_after == runs_before


def test_repository_filters_health_band_state_and_date(tmp_path: Path):
    path = make_database(tmp_path)
    with database_connection(path) as connection:
        with connection:
            seed_window(connection, 0)
            seed_window(connection, 1, disk=99, ram=99, swap=90, cpu=99)
    evaluate_database(path)
    with database_connection(path) as connection:
        items, total = get_health_assessments(
            connection,
            10,
            0,
            start=BASE.isoformat(),
            end=(BASE + timedelta(minutes=10)).isoformat(),
            sort="oldest",
            device_id=DEVICE,
            evaluation_state="provisional",
        )
    assert total == 2
    assert [row["window_start_utc"] for row in items] == sorted(
        row["window_start_utc"] for row in items
    )
