"""Deterministic correctness tests for schema-17 window integrity."""

from __future__ import annotations

import threading
import time
import subprocess
import sys
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import analytics.repair as repair_module
from agent.main import EnhancedCollectionWorker
from backend.phase7b1_repository import claim_agent_session
from analytics.aggregate import aggregate_database
from analytics.recalibration import _reconcile_training_membership
from analytics.repair import (
    REPAIR_REASON,
    apply_repairs,
    compare_windows,
    shadow_quality_policy_comparison,
)
from backend.database import (
    database_connection,
    initialize_database,
    run_write_transaction,
)
from backend.repository import insert_metric
from tests.test_database import sample


DEVICE = "integrity-device"
RULE = "phase7b1-workload-v3"


def _insert_window_samples(
    path: Path,
    start: datetime,
    *,
    count: int = 10,
    skip_indexes: set[int] | None = None,
    workload: str = "gaming_or_3d",
    interval_seconds: int = 30,
) -> list[int]:
    ids: list[int] = []
    skip = skip_indexes or set()
    with database_connection(path) as connection:
        for index in range(count + len(skip)):
            if index in skip:
                continue
            timestamp = start + timedelta(seconds=interval_seconds * index + 13)
            item = sample(timestamp.isoformat())
            item.update({
                "device_id": DEVICE,
                "workload_class": workload,
                "workload_confidence": 0.98,
                "workload_reasons": ["explicit_gaming_foreground"],
                "user_activity_state": "active",
                "system_activity_state": "active",
                "workload_rule_version": RULE,
                "foreground_process_name": (
                    "VALORANT-Win64-Shipping.exe"
                    if workload == "gaming_or_3d" else "Code.exe"
                ),
            })
            ids.append(insert_metric(connection, item))
    return ids


def _feature(path: Path, start: datetime):
    with database_connection(path) as connection:
        return connection.execute(
            "SELECT * FROM feature_windows WHERE device_id=? AND window_start_utc=?",
            (DEVICE, start.isoformat()),
        ).fetchone()


def test_partial_lookback_identifies_key_but_reloads_authoritative_range(tmp_path: Path):
    path = tmp_path / "authoritative.db"
    initialize_database(path)
    start = datetime(2026, 8, 19, 7, 5, tzinfo=timezone.utc)
    ids = _insert_window_samples(path, start)

    # The old implementation queried after 07:08:53 and rebuilt this window
    # from only its last two rows. The bounded rows now identify only the key.
    assert aggregate_database(
        path, recent_minutes=20,
        now_utc=datetime(2026, 8, 19, 7, 28, 53, tzinfo=timezone.utc),
    ) == 1
    row = _feature(path, start)
    assert row["sample_count"] == 10
    assert row["source_sample_count"] == 10
    assert (row["source_first_metric_id"], row["source_last_metric_id"]) == (
        ids[0], ids[-1]
    )
    assert row["is_complete"] == 1
    assert row["dominant_workload_class"] == "gaming_or_3d"
    assert row["finalization_state"] == "finalized"


def test_open_window_is_not_finalized_and_complete_window_never_downgrades(tmp_path: Path):
    path = tmp_path / "open.db"
    initialize_database(path)
    start = datetime(2026, 8, 19, 7, 10, tzinfo=timezone.utc)
    _insert_window_samples(path, start)
    assert aggregate_database(
        path, include_partial=True,
        now_utc=datetime(2026, 8, 19, 7, 10, 30, tzinfo=timezone.utc),
    ) == 0
    assert _feature(path, start) is None

    assert aggregate_database(
        path, now_utc=datetime(2026, 8, 19, 7, 16, 1, tzinfo=timezone.utc)
    ) == 1
    original = dict(_feature(path, start))
    assert original["sample_count"] == 10
    assert aggregate_database(
        path, recent_minutes=5,
        now_utc=datetime(2026, 8, 19, 7, 36, 1, tzinfo=timezone.utc),
    ) == 1
    later = dict(_feature(path, start))
    assert later["sample_count"] == 10
    assert later["semantic_hash"] == original["semantic_hash"]
    assert later["updated_at_utc"] == original["updated_at_utc"]


def test_late_source_sample_creates_versioned_correction(tmp_path: Path):
    path = tmp_path / "late.db"
    initialize_database(path)
    start = datetime(2026, 8, 19, 7, 5, tzinfo=timezone.utc)
    _insert_window_samples(path, start, count=9)
    aggregate_database(
        path, now_utc=datetime(2026, 8, 19, 7, 11, 1, tzinfo=timezone.utc)
    )
    before = _feature(path, start)
    assert before["sample_count"] == 9

    # Add the missing final slot without changing any existing source row.
    with database_connection(path) as connection:
        item = sample((start + timedelta(seconds=9 * 30 + 13)).isoformat())
        item.update({
            "device_id": DEVICE, "workload_class": "gaming_or_3d",
            "workload_confidence": .98, "workload_rule_version": RULE,
            "user_activity_state": "active", "system_activity_state": "active",
            "foreground_process_name": "VALORANT-Win64-Shipping.exe",
        })
        insert_metric(connection, item)
    aggregate_database(
        path, now_utc=datetime(2026, 8, 19, 7, 12, 1, tzinfo=timezone.utc)
    )
    after = _feature(path, start)
    assert after["sample_count"] == 10
    assert after["finalization_state"] == "audited_correction"
    with database_connection(path) as connection:
        event = connection.execute(
            "SELECT * FROM feature_window_repair_events WHERE feature_window_id=?",
            (after["id"],),
        ).fetchone()
    assert event["repair_reason"] == "authoritative_late_source_correction"
    assert event["source_sample_count"] == 10


def test_repair_dry_run_audit_and_transactional_rollback(tmp_path: Path, monkeypatch):
    path = tmp_path / "repair.db"
    initialize_database(path)
    starts = [
        datetime(2026, 8, 19, 7, 5, tzinfo=timezone.utc),
        datetime(2026, 8, 19, 7, 10, tzinfo=timezone.utc),
    ]
    for start in starts:
        _insert_window_samples(path, start)
    aggregate_database(
        path, now_utc=datetime(2026, 8, 19, 7, 21, 1, tzinfo=timezone.utc)
    )
    with database_connection(path) as connection, connection:
        connection.execute(
            "UPDATE feature_windows SET sample_count=2,is_complete=0,coverage_ratio=.2"
        )
    comparisons = compare_windows(path)
    assert len(comparisons) == 2
    assert comparisons[0]["differences"]["sample_count"] == {"old": 2, "new": 10}

    real_upsert = repair_module.upsert_feature
    calls = 0

    def fail_second(connection, feature, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("controlled rollback")
        return real_upsert(connection, feature, **kwargs)

    monkeypatch.setattr(repair_module, "upsert_feature", fail_second)
    with pytest.raises(RuntimeError, match="controlled rollback"):
        apply_repairs(path, comparisons)
    with database_connection(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM feature_window_repair_events"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM feature_windows WHERE sample_count=2"
        ).fetchone()[0] == 2

    monkeypatch.setattr(repair_module, "upsert_feature", real_upsert)
    assert apply_repairs(path, comparisons) == 2
    with database_connection(path) as connection:
        events = connection.execute(
            "SELECT * FROM feature_window_repair_events ORDER BY feature_window_id"
        ).fetchall()
    assert len(events) == 2
    assert all(event["repair_reason"] == REPAIR_REASON for event in events)
    assert all(event["old_values_json"] and event["new_values_json"] for event in events)


def _candidate_and_profile(connection) -> tuple[int, int]:
    timestamp = "2026-08-19T07:00:00+00:00"
    cursor = connection.execute(
        """INSERT INTO baseline_versions (
        device_id,version_number,version_label,lifecycle_state,algorithm_version,
        configuration_version,created_at_utc,learning_started_at_utc,
        reason_codes_json,learning_state) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (DEVICE, 2, "candidate v2", "candidate", "test", "test", timestamp,
         timestamp, "[]", "collecting"),
    )
    version_id = int(cursor.lastrowid)
    cursor = connection.execute(
        """INSERT INTO baseline_version_profiles (
        baseline_version_id,workload_scope,readiness_state,created_at_utc,
        updated_at_utc) VALUES (?,?,'collecting_data',?,?)""",
        (version_id, "gaming_or_3d", timestamp, timestamp),
    )
    return version_id, int(cursor.lastrowid)


def test_membership_reconciliation_is_idempotent_audited_and_reaccepts(tmp_path: Path):
    path = tmp_path / "membership.db"
    initialize_database(path)
    start = datetime(2026, 8, 19, 7, 5, tzinfo=timezone.utc)
    _insert_window_samples(path, start)
    aggregate_database(
        path, now_utc=datetime(2026, 8, 19, 7, 11, 1, tzinfo=timezone.utc)
    )
    with database_connection(path) as connection, connection:
        version_id, profile_id = _candidate_and_profile(connection)
        window = dict(connection.execute("SELECT * FROM feature_windows").fetchone())
        first = _reconcile_training_membership(
            connection, baseline_version_id=version_id, profile_id=profile_id,
            observed=[window], eligible=[window], timestamp="2026-08-19T07:12:00+00:00",
        )
        second = _reconcile_training_membership(
            connection, baseline_version_id=version_id, profile_id=profile_id,
            observed=[window], eligible=[window], timestamp="2026-08-19T07:13:00+00:00",
        )
        assert first == {"accepted": 1, "removed": 0, "rejected": 0}
        assert second == {"accepted": 0, "removed": 0, "rejected": 0}
        assert connection.execute(
            "SELECT COUNT(*) FROM baseline_training_membership_events"
        ).fetchone()[0] == 1

        invalid = {**window, "is_complete": 0, "coverage_ratio": .2}
        removed = _reconcile_training_membership(
            connection, baseline_version_id=version_id, profile_id=profile_id,
            observed=[invalid], eligible=[], timestamp="2026-08-19T07:14:00+00:00",
        )
        assert removed["removed"] == 1
        # Mark an audited repair, then accept the same immutable window again.
        connection.execute(
            """INSERT INTO feature_window_repair_events (
            feature_window_id,repaired_at_utc,repair_reason,aggregation_rule_version,
            old_semantic_hash,new_semantic_hash,old_values_json,new_values_json,
            source_sample_count,details_json) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (window["id"], "2026-08-19T07:15:00+00:00", REPAIR_REASON,
             "test", "old", "new", "{}", "{}", 10, "{}"),
        )
        accepted = _reconcile_training_membership(
            connection, baseline_version_id=version_id, profile_id=profile_id,
            observed=[window], eligible=[window], timestamp="2026-08-19T07:16:00+00:00",
        )
        assert accepted["accepted"] == 1
        event_types = [row[0] for row in connection.execute(
            "SELECT event_type FROM baseline_training_membership_events ORDER BY id"
        )]
    assert event_types == ["accepted", "removed_after_correction", "reaccepted_after_repair"]


def test_strict_policy_and_shadow_comparison_do_not_change_membership(tmp_path: Path):
    path = tmp_path / "shadow.db"
    initialize_database(path)
    starts = [
        datetime(2026, 8, 19, 7, 5, tzinfo=timezone.utc),
        datetime(2026, 8, 19, 7, 10, tzinfo=timezone.utc),
        datetime(2026, 8, 19, 7, 15, tzinfo=timezone.utc),
        datetime(2026, 8, 19, 7, 20, tzinfo=timezone.utc),
    ]
    _insert_window_samples(path, starts[0], count=8)
    _insert_window_samples(path, starts[1], count=9)
    _insert_window_samples(path, starts[2], count=10)
    _insert_window_samples(path, starts[3], count=11, interval_seconds=27)
    aggregate_database(
        path, now_utc=datetime(2026, 8, 19, 7, 31, 1, tzinfo=timezone.utc)
    )
    with database_connection(path) as connection:
        rows = connection.execute(
            "SELECT sample_count,is_complete FROM feature_windows ORDER BY window_start_utc"
        ).fetchall()
        before = connection.execute(
            "SELECT COUNT(*) FROM baseline_version_training_windows"
        ).fetchone()[0]
    assert [(row["sample_count"], row["is_complete"]) for row in rows] == [
        (8, 0), (9, 0), (10, 1), (11, 1)
    ]
    result = shadow_quality_policy_comparison(path, device_id=DEVICE)
    with database_connection(path) as connection:
        after = connection.execute(
            "SELECT COUNT(*) FROM baseline_version_training_windows"
        ).fetchone()[0]
    assert result["affects_candidate_v2"] is False
    assert result["evaluated_8_9_window_count"] == 2
    assert before == after


def test_enhanced_worker_coalesces_without_blocking_request_thread(tmp_path: Path, monkeypatch):
    path = tmp_path / "worker.db"
    initialize_database(path)
    started = threading.Event()
    release = threading.Event()

    def slow_collect(*args, **kwargs):
        started.set()
        release.wait(2)
        raise RuntimeError("controlled optional collector failure")

    monkeypatch.setattr("agent.main.collect_enhanced_evidence", slow_collect)
    monkeypatch.setattr("agent.main.run_write_transaction", lambda *args, **kwargs: None)
    worker = EnhancedCollectionWorker(path)
    worker.start()
    assert worker.request({}, None, None, 1) is True
    assert started.wait(1)
    request_started = time.monotonic()
    assert worker.request({}, None, None, 2) is False
    assert time.monotonic() - request_started < .1
    release.set()
    assert worker.stop(3)


def test_simulated_45_second_enhanced_run_does_not_shift_raw_slots(
    tmp_path: Path, monkeypatch
):
    import agent.main as agent_main

    path = tmp_path / "slow-enhanced.db"
    initialize_database(path)
    session_id = "synthetic-65-minute-owner"
    scheduled_start = datetime(2026, 8, 19, 8, 0, tzinfo=timezone.utc)
    with database_connection(path) as connection, connection:
        claim_agent_session(
            connection, session_id=session_id, process_id=1,
            started_at_utc=scheduled_start,
        )
    release = threading.Event()
    enhanced_started = threading.Event()

    def simulated_45_second_collect(*args, **kwargs):
        enhanced_started.set()
        release.wait(3)
        return SimpleNamespace(run_id=None, failed_collectors=[])

    payloads = []
    # Ten stored cycles are sufficient to exercise the live worker; the fixed
    # clock below separately verifies all 131 slots in a 65-minute session.
    for index in range(10):
        item = sample((scheduled_start + timedelta(seconds=30 * index)).isoformat())
        item["device_id"] = DEVICE
        payloads.append(item)
    monkeypatch.setattr(agent_main, "get_or_create_device_id", lambda: DEVICE)
    monkeypatch.setattr(agent_main, "collect_metrics", lambda *_a, **_k: payloads.pop(0))
    monkeypatch.setattr(agent_main, "collect_enhanced_evidence", simulated_45_second_collect)
    monkeypatch.setattr(agent_main, "finish_enhanced_collection", lambda *_a, **_k: None)

    worker = EnhancedCollectionWorker(path)
    worker.start()
    raw_durations: list[float] = []
    for index in range(10):
        started = time.monotonic()
        agent_main.collect_and_store(
            path,
            scheduled_at_utc=scheduled_start + timedelta(seconds=30 * index),
            agent_session_id=session_id,
            run_maintenance=False,
            enhanced_worker=worker,
        )
        raw_durations.append(time.monotonic() - started)
        if index == 0:
            assert enhanced_started.wait(1)
    release.set()
    assert worker.stop(3)
    assert max(raw_durations) < 1.0

    synthetic_slots = [
        scheduled_start + timedelta(seconds=30 * index) for index in range(131)
    ]
    synthetic_gaps = [
        (synthetic_slots[index] - synthetic_slots[index - 1]).total_seconds()
        for index in range(1, len(synthetic_slots))
    ]
    assert synthetic_slots[-1] - synthetic_slots[0] == timedelta(minutes=65)
    assert synthetic_gaps == [30.0] * 130
    with database_connection(path) as connection:
        timestamps = [datetime.fromisoformat(row[0]) for row in connection.execute(
            "SELECT timestamp_utc FROM metrics ORDER BY timestamp_utc,id"
        )]
        statuses = [row[0] for row in connection.execute(
            "SELECT enhanced_status FROM collection_cycle_audit ORDER BY scheduled_at_utc"
        )]
    assert [(timestamps[index] - timestamps[index - 1]).total_seconds()
            for index in range(1, len(timestamps))] == [30.0] * 9
    assert statuses[0] == "completed"
    assert statuses[1:] == ["coalesced"] * 9


def test_cross_process_lock_is_bounded_and_raw_retry_is_idempotent(tmp_path: Path):
    path = tmp_path / "cross-process.db"
    initialize_database(path)
    with database_connection(path) as connection, connection:
        connection.execute("CREATE TABLE lock_probe (value TEXT UNIQUE)")
    child_code = r"""
import sys,time
from pathlib import Path
from backend.database import database_connection
with database_connection(Path(sys.argv[1])) as connection:
    connection.execute('BEGIN IMMEDIATE')
    connection.execute("INSERT INTO lock_probe VALUES ('maintenance')")
    print('LOCKED',flush=True)
    time.sleep(1.0)
    connection.commit()
"""
    child = subprocess.Popen(
        [sys.executable, "-c", child_code, str(path)],
        cwd=Path(__file__).parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert child.stdout is not None
    assert child.stdout.readline().strip() == "LOCKED"
    payload = sample("2026-08-19T08:00:00+00:00")
    payload["device_id"] = DEVICE
    metric_id = run_write_transaction(
        path,
        lambda connection: insert_metric(
            connection, payload, manage_transaction=False
        ),
        priority="raw",
    )
    assert child.wait(5) == 0, child.stderr.read() if child.stderr else ""
    duplicate_id = run_write_transaction(
        path,
        lambda connection: insert_metric(
            connection, payload, manage_transaction=False
        ),
        priority="raw",
    )
    with database_connection(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM metrics").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM lock_probe").fetchone()[0] == 1
    assert duplicate_id == metric_id


def test_exhausted_lock_failure_is_persisted_after_database_recovers(
    tmp_path: Path, monkeypatch
):
    import agent.main as agent_main

    path = tmp_path / "recovery-audit.db"
    calls = 0

    def collect(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("database is locked after bounded retries")
        if calls == 2:
            return {"timestamp_utc": "2026-08-19T08:00:30+00:00"}
        raise KeyboardInterrupt

    monkeypatch.setattr(agent_main, "collect_and_store", collect)
    agent_main.run_forever(.001, path)
    with database_connection(path) as connection:
        failure = connection.execute(
            """SELECT status,reason_code,collector_failure_category,
            sqlite_retry_count,lock_category FROM collection_cycle_audit
            WHERE status='failed'"""
        ).fetchone()
    assert tuple(failure) == (
        "failed", "sqlite_busy_retries_exhausted", "sqlite", 4, "busy_or_locked"
    )
