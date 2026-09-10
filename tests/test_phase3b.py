from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

import analytics.risk as risk
from analytics.risk import (
    evaluate_database,
    evaluation_eligibility,
    evidence_level_for_score,
)
from backend.database import database_connection, initialize_database
from backend.main import create_app
from backend.phase3b_repository import get_risk_assessments


DEVICE = "risk-test-device"
BASE_TIME = datetime(2025, 1, 1, tzinfo=timezone.utc)


def seed_profile(connection: sqlite3.Connection) -> int:
    now = datetime.now(timezone.utc).isoformat()
    cursor = connection.execute(
        """INSERT INTO baseline_profiles (
            device_id, workload_scope, training_start_utc, training_end_utc,
            eligible_window_count, excluded_window_count, distinct_day_count,
            readiness_state, algorithm_version, configuration_version,
            feature_names_json, missing_features_json, created_at_utc,
            updated_at_utc
        ) VALUES (?, '__device__', ?, ?, 100, 0, 7, 'established',
                  'test-baseline-v1', 'test-config-v1', '[]', '[]', ?, ?)""",
        (
            DEVICE,
            (BASE_TIME - timedelta(days=7)).isoformat(),
            (BASE_TIME - timedelta(minutes=5)).isoformat(),
            now,
            now,
        ),
    )
    return int(cursor.lastrowid)


def seed_window(
    connection: sqlite3.Connection,
    baseline_id: int,
    index: int,
    *,
    workload: str = "idle",
    confidence: float = 0.9,
    coverage: float = 1.0,
    complete: bool = True,
    scores: dict[str, float] | None = None,
    isolation: str = "typical",
    idle_ratio: float | None = None,
    missing_optional: bool = True,
) -> int:
    start = BASE_TIME + timedelta(minutes=index * 5)
    cursor = connection.execute(
        """INSERT INTO feature_windows (
            device_id, window_start_utc, window_end_utc, sample_count,
            expected_sample_count, coverage_ratio, is_complete,
            dominant_workload_class, workload_confidence,
            missing_indicators_json, cpu_avg, cpu_max, cpu_p95, cpu_slope,
            ram_avg, ram_max, ram_slope, swap_avg, swap_max,
            disk_usage_avg, disk_usage_max, disk_read_avg, disk_write_avg,
            disk_usage_slope, network_upload_avg, network_download_avg,
            active_ratio, idle_ratio, process_count_avg,
            cpu_temperature_avg, gpu_utilization_avg,
            cpu_temperature_missing_ratio, gpu_utilization_missing_ratio,
            updated_at_utc
        ) VALUES (?, ?, ?, 10, 10, ?, ?, ?, ?, ?, 85, 95, 92, 0.05,
                  80, 85, 0.02, 15, 20, 75, 78, 1000, 1200, 0.01,
                  500, 700, ?, ?, 150, ?, ?, ?, ?, ?)""",
        (
            DEVICE,
            start.isoformat(),
            (start + timedelta(minutes=5)).isoformat(),
            coverage,
            int(complete),
            workload,
            confidence,
            json.dumps({
                "temperature_ratio": 1.0 if missing_optional else 0.0,
                "gpu_utilization_ratio": 1.0 if missing_optional else 0.0,
            }),
            0.0 if idle_ratio is None else 1.0 - idle_ratio,
            1.0 if idle_ratio is None else idle_ratio,
            None if missing_optional else 75.0,
            None if missing_optional else 60.0,
            1.0 if missing_optional else 0.0,
            1.0 if missing_optional else 0.0,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    window_id = int(cursor.lastrowid)
    deviation_cursor = connection.execute(
        """INSERT INTO deviation_assessments (
            feature_window_id, evaluation_timestamp_utc, device_id,
            baseline_id, baseline_scope, baseline_readiness,
            statistical_summary_json, isolation_forest_score,
            isolation_forest_result, deviation_index, overall_level,
            top_contributing_metrics_json, reason_codes_json,
            workload_context, relevant_event_context_json,
            data_quality_status
        ) VALUES (?, ?, ?, ?, 'device', 'established', '{}', ?, ?, 70,
                  'high', '[]', '[]', ?, '{}', 'sufficient')""",
        (
            window_id,
            datetime.now(timezone.utc).isoformat(),
            DEVICE,
            baseline_id,
            0.4 if isolation == "unusual" else -0.1,
            isolation,
            workload,
        ),
    )
    deviation_id = int(deviation_cursor.lastrowid)
    for feature, score in (scores or {"cpu_avg": 4.0}).items():
        connection.execute(
            """INSERT INTO deviation_feature_results (
                assessment_id, feature_name, observed_value, baseline_centre,
                expected_low, expected_high, deviation_direction,
                deviation_magnitude, normalized_deviation_score,
                severity_band, reason_code, baseline_scope
            ) VALUES (?, ?, 90, 30, 20, 45, 'above', 60, ?, ?, ?, 'device')""",
            (
                deviation_id,
                feature,
                score,
                "high" if score >= 4 else "elevated",
                f"{feature}_above_device_baseline",
            ),
        )
    return window_id


def make_database(tmp_path: Path) -> tuple[Path, int]:
    path = tmp_path / "risk.db"
    initialize_database(path)
    with database_connection(path) as connection:
        with connection:
            baseline_id = seed_profile(connection)
    return path, baseline_id


def insert_event(
    connection: sqlite3.Connection,
    event_time: datetime,
    record_id: int,
    category: str = "hardware",
    level: str = "Error",
) -> None:
    connection.execute(
        """INSERT INTO windows_events (
            device_id, event_timestamp_utc, channel, provider_name, event_id,
            record_id, event_level, smartops_category, safe_summary,
            collected_at_utc
        ) VALUES (?, ?, 'System', 'Synthetic Test Provider', 99, ?, ?, ?,
                  'Mapped test event.', ?)""",
        (
            DEVICE,
            event_time.isoformat(),
            record_id,
            level,
            category,
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def test_eligibility_refuses_incomplete_low_quality_and_missing_deviation():
    window = {
        "is_complete": 1,
        "coverage_ratio": 1.0,
        "dominant_workload_class": "idle",
        "workload_confidence": 0.9,
        "cpu_avg": 1.0,
        "ram_avg": 2.0,
        "disk_usage_avg": 3.0,
        "process_count_avg": 4.0,
    }
    baseline = {"readiness_state": "established"}
    deviation = {
        "deviation_index": 20.0,
        "data_quality_status": "sufficient",
        "overall_level": "mild",
    }
    assert evaluation_eligibility(window, deviation, baseline)[0] is True
    eligible, reasons, quality = evaluation_eligibility(
        {
            **window,
            "is_complete": 0,
            "coverage_ratio": 0.5,
            "dominant_workload_class": "unknown",
        },
        None,
        {"readiness_state": "collecting_data"},
    )
    assert eligible is False and quality == "insufficient"
    assert {
        "incomplete_feature_window",
        "coverage_below_minimum",
        "invalid_workload_context",
        "baseline_not_ready",
        "deviation_assessment_missing",
    }.issubset(reasons)


def test_evidence_level_boundaries():
    assert evidence_level_for_score(0) == "low"
    assert evidence_level_for_score(25) == "guarded"
    assert evidence_level_for_score(50) == "elevated"
    assert evidence_level_for_score(70) == "high"
    assert evidence_level_for_score(85) == "critical_evidence"


def test_workload_aware_cpu_and_idle_background_candidate(tmp_path: Path):
    path, baseline_id = make_database(tmp_path)
    with database_connection(path) as connection:
        with connection:
            development = seed_window(
                connection,
                baseline_id,
                0,
                workload="development",
                scores={"cpu_avg": 5, "cpu_max": 5, "cpu_p95": 5},
                idle_ratio=0.0,
            )
            idle = seed_window(
                connection,
                baseline_id,
                1,
                workload="idle",
                scores={"cpu_avg": 5, "cpu_max": 5, "cpu_p95": 5},
                idle_ratio=1.0,
            )
    assert evaluate_database(path) == 2
    with database_connection(path) as connection:
        development_row = connection.execute(
            "SELECT * FROM risk_assessments WHERE feature_window_id = ?",
            (development,),
        ).fetchone()
        idle_row = connection.execute(
            "SELECT * FROM risk_assessments WHERE feature_window_id = ?",
            (idle,),
        ).fetchone()
        development_penalty = connection.execute(
            """SELECT contribution FROM risk_evidence_components
            WHERE risk_assessment_id = ?
            AND component_name = 'workload_compatibility'""",
            (development_row["id"],),
        ).fetchone()[0]
        idle_domains = {
            row[0] for row in connection.execute(
                """SELECT candidate_domain FROM root_cause_candidates
                WHERE risk_assessment_id = ?""",
                (idle_row["id"],),
            )
        }
        cpu_group_count = connection.execute(
            """SELECT COUNT(*) FROM risk_evidence_components
            WHERE risk_assessment_id = ?
            AND component_name = 'statistical_deviation'
            AND correlation_group = 'cpu'""",
            (development_row["id"],),
        ).fetchone()[0]
        contradictory_context = connection.execute(
            """SELECT COUNT(*)
            FROM root_cause_candidate_evidence e
            JOIN root_cause_candidates c ON c.id = e.candidate_id
            WHERE c.risk_assessment_id = ?
            AND e.supports_candidate = 0
            AND e.reason_code LIKE '%expected_for_development%'""",
            (development_row["id"],),
        ).fetchone()[0]
    assert development_penalty == -10.0
    assert idle_row["risk_evidence_index"] > development_row["risk_evidence_index"]
    assert "unusual_background_activity" in idle_domains
    assert cpu_group_count == 1  # avg/max/p95 are one correlated signal
    assert contradictory_context == 1


def test_persistence_increasing_trend_and_recovery(tmp_path: Path):
    path, baseline_id = make_database(tmp_path)
    with database_connection(path) as connection:
        with connection:
            for index, score in enumerate((2.0, 3.0, 4.0)):
                seed_window(
                    connection,
                    baseline_id,
                    index,
                    scores={"ram_avg": score},
                )
    evaluate_database(path)
    with database_connection(path) as connection:
        newest = connection.execute(
            "SELECT * FROM risk_assessments ORDER BY window_start_utc DESC LIMIT 1"
        ).fetchone()
    assert newest["persistence_window_count"] == 3
    assert newest["temporal_pattern"] == "increasing_trend"

    recovery_path, recovery_baseline = make_database(tmp_path / "recovery")
    with database_connection(recovery_path) as connection:
        with connection:
            for index, score in enumerate((5.0, 4.0, 2.0)):
                seed_window(
                    connection,
                    recovery_baseline,
                    index,
                    scores={"ram_avg": score},
                )
    evaluate_database(recovery_path)
    with database_connection(recovery_path) as connection:
        newest = connection.execute(
            "SELECT temporal_pattern FROM risk_assessments "
            "ORDER BY window_start_utc DESC LIMIT 1"
        ).fetchone()
    assert newest["temporal_pattern"] == "recovery_toward_baseline"


def test_event_timing_candidate_ranking_and_missing_sensors(tmp_path: Path):
    path, baseline_id = make_database(tmp_path)
    with database_connection(path) as connection:
        with connection:
            window_id = seed_window(
                connection,
                baseline_id,
                2,
                scores={"ram_avg": 5, "swap_avg": 4, "disk_usage_avg": 4},
                isolation="unusual",
                missing_optional=True,
            )
            start = BASE_TIME + timedelta(minutes=10)
            insert_event(connection, start - timedelta(minutes=5), 1, "hardware")
            insert_event(connection, start + timedelta(minutes=2), 2, "hardware")
            insert_event(connection, start + timedelta(minutes=6), 3, "hardware")
            insert_event(connection, start + timedelta(minutes=10), 4, "hardware")
    assert evaluate_database(path) == 1
    with database_connection(path) as connection:
        items, total = get_risk_assessments(connection, 10, 0)
    assessment = items[0]
    assert total == 1
    assert assessment["risk_evidence_index"] >= 70
    assert assessment["score_reconstruction"] == assessment["risk_evidence_index"]
    event_component = next(
        item for item in assessment["components"]
        if item["component_name"] == "serious_events"
    )
    assert [event["timing"] for event in event_component["evidence"]["events"]] == [
        "before",
        "during",
        "after",
    ]
    domains = [candidate["candidate_domain"] for candidate in assessment["candidates"]]
    assert "hardware_error_evidence" in domains
    assert "thermal_stress" not in domains
    hardware = next(
        candidate for candidate in assessment["candidates"]
        if candidate["candidate_domain"] == "hardware_error_evidence"
    )
    assert hardware["supporting_events"]
    assert hardware["recommended_verification_steps"]
    assert hardware["limitations"]
    assert [candidate["rank"] for candidate in assessment["candidates"]] == list(
        range(1, len(assessment["candidates"]) + 1)
    )


def test_evaluation_backfill_idempotence_and_transaction_rollback(
    tmp_path: Path,
    monkeypatch,
):
    path, baseline_id = make_database(tmp_path)
    with database_connection(path) as connection:
        with connection:
            seed_window(connection, baseline_id, 0, scores={"memory": 4})
    assert evaluate_database(path, command="backfill") == 1
    assert evaluate_database(path, command="backfill") == 0
    assert evaluate_database(path, command="backfill", force=True) == 1
    with database_connection(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM risk_assessments"
        ).fetchone()[0] == 1

    rollback_path, rollback_baseline = make_database(tmp_path / "rollback")
    with database_connection(rollback_path) as connection:
        with connection:
            seed_window(
                connection,
                rollback_baseline,
                0,
                scores={"ram_avg": 4},
            )
    monkeypatch.setattr(
        risk,
        "_persist_assessment",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("test rollback")),
    )
    assert evaluate_database(rollback_path) == 0
    with database_connection(rollback_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM risk_assessments"
        ).fetchone()[0] == 0
        run = connection.execute(
            "SELECT * FROM risk_evaluation_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert run["status"] == "error"
    assert run["error_code"] == "RuntimeError"


def test_schema_four_migrates_without_data_loss(tmp_path: Path):
    path = tmp_path / "schema4.db"
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """INSERT INTO metrics (
                timestamp_utc, device_id, cpu_percent
            ) VALUES ('2025-01-01T00:00:00+00:00', 'legacy', 10)"""
        )
        connection.execute("PRAGMA user_version = 4")
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        count = connection.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
        risk_tables = {
            row[0] for row in connection.execute(
                """SELECT name FROM sqlite_master WHERE type = 'table'
                AND name LIKE 'risk_%'"""
            )
        }
    assert version == 19 and count == 1
    assert {"risk_assessments", "risk_evidence_components"}.issubset(risk_tables)


def test_phase3b_api_filters_pagination_and_old_endpoint_compatibility(
    tmp_path: Path,
):
    path, baseline_id = make_database(tmp_path)
    with database_connection(path) as connection:
        with connection:
            first = seed_window(
                connection,
                baseline_id,
                0,
                workload="idle",
                scores={"cpu_avg": 5},
                idle_ratio=1.0,
            )
            second = seed_window(
                connection,
                baseline_id,
                1,
                workload="development",
                scores={"ram_avg": 4},
            )
    evaluate_database(path)
    with TestClient(create_app(path)) as client:
        assert client.get("/api/status").status_code == 200
        assert client.get("/api/metrics/latest").status_code == 200
        assert client.get("/api/baseline/status").status_code == 200
        assert client.get("/api/deviations/latest").status_code == 200
        assert client.get("/api/risk/status").json()["status"] == "evaluated"
        latest = client.get("/api/risk/latest").json()
        assert latest["status"] == "evaluated"
        level = latest["assessment"]["evidence_level"]
        assert client.get(
            "/api/risk/history",
            params={"evidence_level": level},
        ).json()["total"] >= 1
        page = client.get(
            "/api/risk/history",
            params={
                "limit": 1,
                "offset": 1,
                "workload": "idle",
                "sort": "newest",
            },
        ).json()
        assert page["total"] == 1 and page["items"] == []
        assert client.get("/api/risk/count").json() == {"count": 2}
        assert client.get(f"/api/risk/{first}").json()["status"] == "evaluated"
        assert client.get("/api/risk/9999").json()["status"] == "not_evaluated"
        root_latest = client.get("/api/root-causes/latest").json()
        assert root_latest["status"] == "evaluated"
        root_page = client.get(
            "/api/root-causes/history",
            params={"limit": 1, "offset": 0, "workload": "idle"},
        ).json()
        assert root_page["total"] >= 1 and len(root_page["items"]) == 1
        assert client.get(
            f"/api/root-causes/{second}"
        ).json()["status"] == "evaluated"
        assert client.get("/api/risk/history?limit=1001").status_code == 422
        assert client.get(
            "/api/risk/history?evidence_level=probability"
        ).status_code == 422
        assert client.get(
            "/api/risk/history",
            params={
                "start": "2026-02-01T00:00:00Z",
                "end": "2026-01-01T00:00:00Z",
            },
        ).status_code == 422
