"""Deterministic Phase 7B.1 correctness tests use isolated databases only."""

from __future__ import annotations

import sqlite3
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import agent.enhanced as enhanced
from agent.notification_policy import notification_category_for_alert_severity
from analytics.baseline import _choose_profile, evaluate_database, train_database
from analytics.health import evaluate_database as evaluate_health
from analytics.risk import evaluate_database as evaluate_risk
from analytics.recalibration import (
    _profile_readiness,
    activate_candidate,
    baseline_management_status,
    cancel_recalibration,
    continue_recalibration,
    pause_recalibration,
    resume_recalibration,
    refresh_candidate,
    rollback_baseline,
    start_recalibration,
)
from analytics.baseline_config import DEFAULT_POLICY, DEVICE_SCOPE
from agent.main import MaintenanceWorker
from backend.database import database_connection, initialize_database
from backend.enhanced_repository import update_collector_state
from backend.main import create_app
from backend.phase7b1_repository import (
    claim_agent_session,
    close_orphaned_agent_sessions,
    ensure_versioned_baseline_snapshot,
)
from tests.test_phase3a import TEST_POLICY, insert_feature, seed_training_history


def prepared_baseline(tmp_path: Path) -> Path:
    path = tmp_path / "phase7b1.db"
    seed_training_history(path)
    train_database(path, policy=TEST_POLICY)
    with database_connection(path) as connection, connection:
        ensure_versioned_baseline_snapshot(connection, "test-device")
    return path


def test_alert_severity_to_notification_category_is_exact():
    assert notification_category_for_alert_severity("informational") == "advisory"
    assert notification_category_for_alert_severity("advisory") == "advisory"
    assert notification_category_for_alert_severity("warning") == "warning"
    assert notification_category_for_alert_severity("urgent") == "urgent"
    assert notification_category_for_alert_severity("high") is None
    assert notification_category_for_alert_severity("URGENT") is None
    assert notification_category_for_alert_severity(None) is None


def test_schema11_to_14_is_additive_and_preserves_history(tmp_path: Path):
    path = tmp_path / "schema11.db"
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO metrics (timestamp_utc, device_id) VALUES (?, ?)",
            ("2026-01-01T00:00:00+00:00", "preserved"),
        )
        connection.execute("PRAGMA user_version = 11")
    initialize_database(path)
    with database_connection(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 18
        assert connection.execute("SELECT COUNT(*) FROM metrics").fetchone()[0] == 1
        feature_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(feature_windows)")
        }
        assert {
            "workload_composition_json",
            "secondary_workload_context",
            "secondary_workload_rule_version",
        } <= feature_columns
        for table in (
            "deviation_assessments",
            "risk_assessments",
            "health_assessments",
            "alerts",
        ):
            assert "workload_rule_version" in {
                row[1] for row in connection.execute(f"PRAGMA table_info({table})")
            }
        tables = {
            row[0]
            for row in connection.execute(
                """SELECT name FROM sqlite_master WHERE type='table'
                AND name IN ('notification_decisions', 'agent_runtime_sessions',
                'baseline_versions', 'baseline_version_profiles')"""
            )
        }
        assert len(tables) == 4
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_readiness_ignores_ordinary_exclusions_after_requirements_are_met():
    state, reasons, applicability = _profile_readiness(
        DEVICE_SCOPE,
        DEFAULT_POLICY.minimum_device_windows + 7,
        DEFAULT_POLICY.minimum_device_windows,
        DEFAULT_POLICY.minimum_distinct_days,
    )
    assert state == "ready"
    assert applicability == "applicable"
    assert "some_windows_excluded_by_quality_or_event_policy" in reasons

    insufficient_count = _profile_readiness(
        "development",
        DEFAULT_POLICY.minimum_workload_windows,
        DEFAULT_POLICY.minimum_workload_windows - 1,
        DEFAULT_POLICY.minimum_distinct_days,
    )
    assert insufficient_count[0] == "collecting_data"
    assert "eligible_window_count_below_minimum" in insufficient_count[1]

    insufficient_days = _profile_readiness(
        "development",
        DEFAULT_POLICY.minimum_workload_windows,
        DEFAULT_POLICY.minimum_workload_windows,
        DEFAULT_POLICY.minimum_distinct_days - 1,
    )
    assert insufficient_days[0] == "collecting_data"
    assert "distinct_collection_days_below_minimum" in insufficient_days[1]


def test_maintenance_worker_drops_slow_cycle_backlog_and_stops(tmp_path: Path):
    calls: list[int] = []
    first_started = threading.Event()
    release_first = threading.Event()

    def slow_callback(_path: Path) -> None:
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            first_started.set()
            assert release_first.wait(2)

    worker = MaintenanceWorker(tmp_path / "unused.db", callback=slow_callback)
    worker.start()
    worker.request()
    assert first_started.wait(1)
    for _ in range(20):
        worker.request()
    release_first.set()
    time.sleep(0.05)
    assert calls == [1]
    deadline = time.monotonic() + 2
    while len(calls) < 2 and time.monotonic() < deadline:
        worker.request()
        time.sleep(0.01)
    assert worker.stop(2)
    assert calls == [1, 2]


def test_ctrl_c_path_stops_worker_and_closes_agent_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import agent.main as agent_main

    path = tmp_path / "ctrl-c.db"

    def interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(agent_main, "collect_and_store", interrupt)
    agent_main.run_forever(1, path)

    with database_connection(path) as connection:
        session = connection.execute(
            """SELECT status, stop_reason FROM agent_runtime_sessions
            ORDER BY started_at_utc DESC LIMIT 1"""
        ).fetchone()
    assert tuple(session) == ("stopped", "ctrl_c_or_normal_exit")


def test_single_agent_session_owner_rejects_competing_worker(tmp_path: Path):
    path = tmp_path / "owner.db"
    initialize_database(path)
    now = datetime.now(timezone.utc)
    with database_connection(path) as connection, connection:
        claim_agent_session(
            connection,
            session_id="owner-one",
            process_id=os.getpid(),
            started_at_utc=now,
        )
        with pytest.raises(RuntimeError, match="already active"):
            claim_agent_session(
                connection,
                session_id="owner-two",
                process_id=200,
                started_at_utc=now + timedelta(seconds=5),
            )


def test_supervisor_cleanup_closes_only_orphaned_owner(tmp_path: Path):
    path = tmp_path / "cleanup.db"
    initialize_database(path)
    now = datetime.now(timezone.utc)
    with database_connection(path) as connection, connection:
        connection.execute(
            """INSERT INTO agent_runtime_sessions (
            session_id, process_id, started_at_utc, heartbeat_at_utc, status
            ) VALUES ('orphan', 999999, ?, ?, 'running')""",
            (now.isoformat(), now.isoformat()),
        )
        assert close_orphaned_agent_sessions(connection, now) == 1
        row = connection.execute(
            "SELECT status, stop_reason FROM agent_runtime_sessions WHERE session_id='orphan'"
        ).fetchone()
    assert tuple(row) == ("stopped", "supervisor_shutdown")


def test_fixed_collector_schedule_records_delay_and_gap_classification(tmp_path: Path):
    path = tmp_path / "schedule.db"
    initialize_database(path)
    scheduled = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with database_connection(path) as connection:
        update_collector_state(
            connection,
            collector_key="performance_counters",
            attempted_at_utc=scheduled + timedelta(seconds=1),
            scheduled_at_utc=scheduled,
            frequency_seconds=60,
            availability_status="available",
            reason_code=None,
            source_name="Windows Performance Counters",
            successful=True,
            collection_duration_ms=1250,
            gap_classification="on_schedule",
            agent_session_id="session-a",
        )
        first = connection.execute(
            "SELECT * FROM enhanced_collector_state WHERE collector_key='performance_counters'"
        ).fetchone()
        update_collector_state(
            connection,
            collector_key="performance_counters",
            attempted_at_utc=scheduled + timedelta(seconds=125),
            scheduled_at_utc=scheduled + timedelta(seconds=60),
            frequency_seconds=60,
            availability_status="timeout",
            reason_code="query_timeout",
            source_name="Windows Performance Counters",
            successful=False,
            collection_duration_ms=5000,
            gap_classification="missed_while_running",
            agent_session_id="session-a",
        )
        second = connection.execute(
            "SELECT * FROM enhanced_collector_state WHERE collector_key='performance_counters'"
        ).fetchone()
    assert first["next_due_utc"] == (scheduled + timedelta(seconds=60)).isoformat()
    assert first["last_schedule_delay_seconds"] == pytest.approx(1)
    assert second["next_due_utc"] == (scheduled + timedelta(seconds=180)).isoformat()
    assert second["last_gap_classification"] == "missed_while_running"
    assert second["last_success_utc"] == first["last_success_utc"]


def test_offline_gap_is_distinct_from_an_in_session_miss():
    assert enhanced.classify_schedule_gap(5, 60, "a", "a") == "on_schedule"
    assert enhanced.classify_schedule_gap(65, 60, "a", "a") == "missed_while_running"
    assert enhanced.classify_schedule_gap(300, 60, "old", "new") == "offline_or_shutdown_gap"
    assert enhanced.classify_schedule_gap(300, 60, None, "new") == "offline_or_shutdown_gap"


def test_storage_active_semantics_are_bounded_per_physical_disk(monkeypatch):
    monkeypatch.setattr(enhanced.sys, "platform", "win32")

    def runner(_script: str, _timeout: int):
        return [
            {
                "path": r"\\host\physicaldisk(0 c:)\% idle time",
                "cooked_value": 20.0,
                "status": 0,
            },
            {
                "path": r"\\host\physicaldisk(1 d:)\% idle time",
                "cooked_value": 85.0,
                "status": 0,
            },
        ]

    signals, succeeded, _ = enhanced.collect_performance_signals(runner)
    storage = next(
        item for item in signals if item["signal_key"] == "storage_io_active_percent"
    )
    assert succeeded is True
    assert storage["numeric_value"] == pytest.approx(80.0)
    assert 0 <= storage["numeric_value"] <= 100
    assert storage["details"]["semantics"] == "maximum_active_time_across_physical_disks"
    assert storage["details"]["bounded_percent"] is True


def test_storage_permission_limit_is_not_reported_as_unsupported(monkeypatch):
    monkeypatch.setattr(enhanced.sys, "platform", "win32")

    def runner(_script: str, _timeout: int):
        return {
            "storage_status": "permission_limited",
            "storage_reason": "storage_query_permission_limited",
            "reliability": [],
            "battery_present": False,
            "full_capacity": [],
            "design_capacity": [],
            "battery_status": [],
        }

    signals, succeeded, _ = enhanced.collect_hardware_signals(runner)
    storage = [item for item in signals if item["signal_group"] == "storage"]
    assert succeeded is True
    assert storage
    assert all(item["numeric_value"] is None for item in storage)
    assert all(item["availability_status"] == "permission_limited" for item in storage)
    assert all(item["reason_code"] == "storage_query_permission_limited" for item in storage)


def test_recalibration_is_opt_in_resumable_and_preserves_original(tmp_path: Path):
    path = prepared_baseline(tmp_path)
    initial = baseline_management_status(path)
    assert initial["active_version"]["version_number"] == 1
    assert initial["candidate_version"] is None

    started = start_recalibration(path, device_id="test-device")
    assert started["active_version"]["version_number"] == 1
    assert started["candidate_version"]["version_number"] == 2
    profiles = started["candidate_version"]["profiles"]
    gaming = next(row for row in profiles if row["workload_scope"] == "gaming_or_3d")
    assert gaming["readiness_state"] == "not_observed"
    assert "workload_not_observed" in gaming["reason_codes"][0]

    assert pause_recalibration(path, device_id="test-device")["candidate_version"]["lifecycle_state"] == "paused"
    assert resume_recalibration(path, device_id="test-device")["candidate_version"]["lifecycle_state"] == "candidate"
    cancelled = cancel_recalibration(path, device_id="test-device")
    assert cancelled["candidate_version"] is None
    assert cancelled["active_version"]["version_number"] == 1
    with database_connection(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM baseline_versions").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM feature_windows").fetchone()[0] == 6


def test_ready_candidate_continues_same_version_and_persists_state(tmp_path: Path):
    path = prepared_baseline(tmp_path)
    started = start_recalibration(path, device_id="test-device")
    original = started["candidate_version"]
    with database_connection(path) as connection, connection:
        connection.execute(
            """UPDATE baseline_versions SET lifecycle_state='ready',
            learning_state='ready_for_validation' WHERE id=?""",
            (original["id"],),
        )

    continued = continue_recalibration(path, device_id="test-device")
    candidate = continued["candidate_version"]
    assert candidate["id"] == original["id"]
    assert candidate["version_number"] == 2
    assert candidate["learning_started_at_utc"] == original["learning_started_at_utc"]
    assert candidate["lifecycle_state"] == "ready"
    assert candidate["learning_state"] == "collecting"
    assert continued["active_version"]["version_number"] == 1

    paused = pause_recalibration(path, device_id="test-device")
    assert paused["candidate_version"]["lifecycle_state"] == "ready"
    assert paused["candidate_version"]["learning_state"] == "paused"
    resumed = resume_recalibration(path, device_id="test-device")
    assert resumed["candidate_version"]["id"] == original["id"]
    assert resumed["candidate_version"]["learning_state"] == "collecting"

    after_restart = baseline_management_status(path, device_id="test-device")
    assert after_restart["candidate_version"]["id"] == original["id"]
    assert after_restart["candidate_version"]["learning_state"] == "collecting"
    with database_connection(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM baseline_versions").fetchone()[0] == 2
        event = connection.execute(
            """SELECT event_type, reason_code, details_json
            FROM baseline_recalibration_events
            WHERE baseline_version_id=? AND event_type='continued'""",
            (original["id"],),
        ).fetchone()
    assert event["reason_code"] == "user_continued_ready_candidate"
    assert original["learning_started_at_utc"] in event["details_json"]


def test_readiness_labels_separate_blockers_from_audit_information(tmp_path: Path):
    path = prepared_baseline(tmp_path)
    start_recalibration(path, device_id="test-device")
    status = baseline_management_status(path, device_id="test-device")
    profiles = status["candidate_version"]["profiles"]
    not_observed = next(item for item in profiles if item["workload_scope"] == "gaming_or_3d")
    assert not_observed["readiness_state"] == "not_observed"
    assert not_observed["blocking_reason"] == "none_not_required"

    with database_connection(path) as connection, connection:
        profile_id = connection.execute(
            """SELECT id FROM baseline_version_profiles
            WHERE baseline_version_id=? AND workload_scope=?""",
            (status["candidate_version"]["id"], "development"),
        ).fetchone()[0]
        connection.execute(
            """UPDATE baseline_version_profiles SET readiness_state='ready',
            applicability_state='applicable', sampling_completeness=.91,
            reason_codes_json=? WHERE id=?""",
            ('["some_windows_excluded_by_quality_or_event_policy"]', profile_id),
        )
    ready = next(
        item for item in baseline_management_status(path)["candidate_version"]["profiles"]
        if item["workload_scope"] == "development"
    )
    assert ready["sampling_completeness"] == pytest.approx(.91)
    assert ready["blocking_reason"] == "none"
    assert ready["informational_reason_codes"] == [
        "some_windows_excluded_by_quality_or_event_policy"
    ]


def test_future_deviation_records_the_active_baseline_version(tmp_path: Path):
    path = prepared_baseline(tmp_path)
    status = baseline_management_status(path)
    with database_connection(path) as connection, connection:
        window_id = insert_feature(
            connection,
            20,
            workload="development",
            cpu=68.0,
            ram=48.0,
        )
        connection.execute(
            """UPDATE feature_windows SET workload_rule_version = ?
            WHERE id = ?""",
            ("phase7b1-workload-v3", window_id),
        )
    assert evaluate_database(path, policy=TEST_POLICY) == 1
    with database_connection(path) as connection:
        assessment = connection.execute(
            """SELECT baseline_version_id, baseline_rule_version,
            workload_rule_version
            FROM deviation_assessments WHERE feature_window_id=?""",
            (window_id,),
        ).fetchone()
    assert assessment["baseline_version_id"] == status["active_version"]["id"]
    assert assessment["baseline_rule_version"] == "phase3a-v1"
    assert assessment["workload_rule_version"] == "phase7b1-workload-v3"
    with database_connection(path) as connection:
        window_start = connection.execute(
            "SELECT window_start_utc FROM feature_windows WHERE id = ?",
            (window_id,),
        ).fetchone()[0]
    assert evaluate_risk(path, start=window_start, end=window_start) == 1
    assert evaluate_health(path, window_id=window_id)["evaluated"] == 1
    with database_connection(path) as connection:
        risk = connection.execute(
            "SELECT * FROM risk_assessments WHERE feature_window_id = ?",
            (window_id,),
        ).fetchone()
        health = connection.execute(
            "SELECT * FROM health_assessments WHERE feature_window_id = ?",
            (window_id,),
        ).fetchone()
    assert risk["baseline_version_id"] == status["active_version"]["id"]
    assert risk["workload_rule_version"] == "phase7b1-workload-v3"
    assert health["baseline_version_id"] == status["active_version"]["id"]
    assert health["workload_rule_version"] == "phase7b1-workload-v3"


def test_guided_profile_selection_and_conservative_primary_fallback(tmp_path: Path):
    path = prepared_baseline(tmp_path)
    with database_connection(path) as connection, connection:
        connection.execute(
            """INSERT INTO baseline_profiles (
            device_id, workload_scope, training_start_utc, training_end_utc,
            eligible_window_count, excluded_window_count, distinct_day_count,
            readiness_state, algorithm_version, configuration_version,
            feature_names_json, missing_features_json, created_at_utc, updated_at_utc
            ) SELECT device_id, 'guided_development', training_start_utc,
            training_end_utc, eligible_window_count, excluded_window_count,
            distinct_day_count, readiness_state, algorithm_version,
            configuration_version, feature_names_json, missing_features_json,
            created_at_utc, updated_at_utc FROM baseline_profiles
            WHERE device_id='test-device' AND workload_scope='development'"""
        )
        window = {
            "device_id": "test-device",
            "dominant_workload_class": "development",
            "secondary_workload_context": "guided_development",
        }
        profile, scope = _choose_profile(connection, window)
        assert profile["workload_scope"] == "guided_development"
        assert scope == "guided_workload"
        connection.execute(
            """UPDATE baseline_profiles SET readiness_state='collecting_data'
            WHERE workload_scope='guided_development'"""
        )
        fallback, fallback_scope = _choose_profile(connection, window)
    assert fallback["workload_scope"] == "development"
    assert fallback_scope == "workload"


def test_existing_candidate_continues_learning_guided_context(tmp_path: Path):
    path = prepared_baseline(tmp_path)
    started = start_recalibration(path, device_id="test-device")
    candidate_id = started["candidate_version"]["id"]
    with database_connection(path) as connection, connection:
        connection.execute(
            """UPDATE baseline_versions SET learning_started_at_utc = ?
            WHERE id = ?""",
            ("2024-12-31T00:00:00+00:00", candidate_id),
        )
        window_id = insert_feature(
            connection, 20, workload="browser_or_media", cpu=45, ram=50
        )
        connection.execute(
            """UPDATE feature_windows SET secondary_workload_context = ?,
            secondary_workload_rule_version = ?, workload_rule_version = ?
            WHERE id = ?""",
            (
                "guided_development",
                "guided-development-v1",
                "phase7b1-workload-v3",
                window_id,
            ),
        )
    refresh_candidate(path, device_id="test-device", force=True)
    status = baseline_management_status(path)
    guided = next(
        profile for profile in status["candidate_version"]["profiles"]
        if profile["workload_scope"] == "guided_development"
    )
    assert status["active_version"]["version_number"] == 1
    assert status["candidate_version"]["id"] == candidate_id
    assert status["candidate_version"]["version_number"] == 2
    assert guided["observed_window_count"] == 1
    assert guided["eligible_window_count"] == 1
    with database_connection(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM baseline_versions").fetchone()[0] == 2


def test_candidate_activation_and_safe_rollback_are_versioned(tmp_path: Path):
    path = prepared_baseline(tmp_path)
    start_recalibration(path, device_id="test-device")
    with database_connection(path) as connection, connection:
        candidate = connection.execute(
            "SELECT id FROM baseline_versions WHERE lifecycle_state='candidate'"
        ).fetchone()[0]
        connection.execute(
            "UPDATE baseline_versions SET lifecycle_state='ready' WHERE id=?",
            (candidate,),
        )
        connection.execute(
            """UPDATE baseline_version_profiles SET readiness_state='ready'
            WHERE baseline_version_id=? AND workload_scope IN ('__device__', 'development')""",
            (candidate,),
        )
    active = activate_candidate(path, device_id="test-device")
    assert active["active_version"]["version_number"] == 2
    rolled_back = rollback_baseline(path, device_id="test-device")
    assert rolled_back["active_version"]["version_number"] == 1
    with database_connection(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM baseline_versions").fetchone()[0] == 2


def test_baseline_management_api_does_not_start_recalibration_on_get(tmp_path: Path):
    path = prepared_baseline(tmp_path)
    with TestClient(create_app(path)) as client:
        status = client.get("/api/baseline-management/status")
        after_get = client.get("/api/baseline-management/status")
        started = client.post("/api/baseline-management/start")
    assert status.status_code == 200
    assert after_get.json()["candidate_version"] is None
    assert started.status_code == 200
    assert started.json()["candidate_version"]["lifecycle_state"] == "candidate"


def test_runtime_status_uses_persisted_agent_heartbeat(tmp_path: Path):
    path = tmp_path / "runtime-status.db"
    initialize_database(path)
    with TestClient(create_app(path)) as client:
        assert client.get("/api/runtime/status").json()["state"] == "offline"
        now = datetime.now(timezone.utc).isoformat()
        with database_connection(path) as connection, connection:
            connection.execute(
                """INSERT INTO agent_runtime_sessions (
                session_id, process_id, started_at_utc, heartbeat_at_utc, status
                ) VALUES ('runtime-test', ?, ?, ?, 'running')""",
                (os.getpid(), now, now),
            )
        response = client.get("/api/runtime/status")
    assert response.status_code == 200
    assert response.json()["state"] == "live"
    assert response.json()["agent_running"] is True
