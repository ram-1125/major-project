"""Deterministic SQLite contention checks for the local SmartOps writers."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.notifications import NotificationDispatcher
from analytics.alerts import evaluate_database as evaluate_alerts
from analytics.recalibration import refresh_candidate, start_recalibration
from backend.database import (
    SQLITE_BUSY_TIMEOUT_MS,
    database_connection,
    initialize_database,
    is_transient_sqlite_lock,
    run_write_transaction,
)
from backend.main import create_app
from backend.phase7b1_repository import claim_agent_session
from backend.repository import insert_metric
from tests.test_database import sample
from tests.test_phase5a import make_database as make_alert_database, prepare
from tests.test_phase7a import RecordingProvider, make_database as make_notification_database, seed_alert
from tests.test_phase7b1 import prepared_baseline


def test_canonical_connection_policy_uses_wal_normal_and_foreign_keys(tmp_path: Path):
    path = tmp_path / "policy.db"
    initialize_database(path)
    with database_connection(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == SQLITE_BUSY_TIMEOUT_MS
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 19


def test_busy_retry_rolls_back_partial_attempt_and_succeeds_once(tmp_path: Path):
    path = tmp_path / "retry.db"
    initialize_database(path)
    with database_connection(path) as connection, connection:
        connection.execute("CREATE TABLE retry_probe (value TEXT UNIQUE)")
    calls = 0
    delays: list[float] = []

    def operation(connection: sqlite3.Connection) -> str:
        nonlocal calls
        calls += 1
        connection.execute("INSERT INTO retry_probe VALUES ('one')")
        if calls < 3:
            raise sqlite3.OperationalError("database is locked")
        return "stored"

    result = run_write_transaction(
        path,
        operation,
        attempts=3,
        base_delay_seconds=0.001,
        jitter_seconds=0,
        sleep=delays.append,
    )
    with database_connection(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM retry_probe").fetchone()[0] == 1
    assert result == "stored"
    assert calls == 3
    assert delays == [0.001, 0.002]


def test_busy_retry_exhaustion_rolls_back_closes_and_non_lock_fails_fast(tmp_path: Path):
    path = tmp_path / "exhaustion.db"
    initialize_database(path)
    with database_connection(path) as connection, connection:
        connection.execute("CREATE TABLE retry_probe (value TEXT)")
    lock_calls = 0

    def locked(connection: sqlite3.Connection) -> None:
        nonlocal lock_calls
        lock_calls += 1
        connection.execute("INSERT INTO retry_probe VALUES ('rolled-back')")
        raise sqlite3.OperationalError("database table is locked")

    with pytest.raises(sqlite3.OperationalError, match="locked"):
        run_write_transaction(
            path, locked, attempts=2, base_delay_seconds=0,
            jitter_seconds=0, sleep=lambda _delay: None,
        )
    assert lock_calls == 2
    with database_connection(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM retry_probe").fetchone()[0] == 0

    other_calls = 0

    def corrupt(_connection: sqlite3.Connection) -> None:
        nonlocal other_calls
        other_calls += 1
        raise sqlite3.OperationalError("no such table: not_a_lock")

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        run_write_transaction(path, corrupt, attempts=4)
    assert other_calls == 1
    assert is_transient_sqlite_lock(sqlite3.OperationalError("database is busy"))
    assert not is_transient_sqlite_lock(sqlite3.OperationalError("disk I/O error"))


def test_raw_priority_writer_waits_safely_and_fastapi_reads_during_write(tmp_path: Path):
    path = tmp_path / "contention.db"
    initialize_database(path)
    with database_connection(path) as connection, connection:
        connection.execute("CREATE TABLE writer_probe (owner TEXT)")

    writer_started = threading.Event()
    release_writer = threading.Event()
    errors: list[BaseException] = []

    def maintenance_operation(connection: sqlite3.Connection) -> None:
        connection.execute("INSERT INTO writer_probe VALUES ('maintenance')")
        writer_started.set()
        assert release_writer.wait(5)

    def maintenance() -> None:
        try:
            run_write_transaction(path, maintenance_operation, priority="maintenance")
        except BaseException as error:  # reported by the assertion below
            errors.append(error)

    def raw() -> None:
        try:
            assert writer_started.wait(5)
            run_write_transaction(
                path,
                lambda connection: connection.execute(
                    "INSERT INTO writer_probe VALUES ('raw')"
                ),
                priority="raw",
            )
        except BaseException as error:
            errors.append(error)

    with TestClient(create_app(path)) as client:
        maintenance_thread = threading.Thread(target=maintenance)
        raw_thread = threading.Thread(target=raw)
        maintenance_thread.start()
        raw_thread.start()
        assert writer_started.wait(5)
        started = time.monotonic()
        response = client.get("/api/status")
        assert time.monotonic() - started < 2
        assert response.status_code == 200
        release_writer.set()
        maintenance_thread.join(5)
        raw_thread.join(5)
    assert not maintenance_thread.is_alive()
    assert not raw_thread.is_alive()
    assert errors == []
    with database_connection(path) as connection:
        assert [
            row[0] for row in connection.execute(
                "SELECT owner FROM writer_probe ORDER BY rowid"
            )
        ] == ["maintenance", "raw"]


def test_metric_retry_identity_prevents_duplicate_metric_and_process_rows(tmp_path: Path):
    path = tmp_path / "metric-idempotency.db"
    initialize_database(path)
    payload = sample()
    first = run_write_transaction(
        path,
        lambda connection: insert_metric(
            connection, payload, manage_transaction=False
        ),
        priority="raw",
    )
    second = run_write_transaction(
        path,
        lambda connection: insert_metric(
            connection, payload, manage_transaction=False
        ),
        priority="raw",
    )
    assert first == second
    with database_connection(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM metrics").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM process_snapshots").fetchone()[0] == 2


def test_cross_connection_runtime_claim_is_single_owner(tmp_path: Path):
    path = tmp_path / "runtime-owner.db"
    initialize_database(path)
    now = datetime.now(timezone.utc)
    with database_connection(path) as connection, connection:
        claim_agent_session(
            connection,
            session_id="first",
            process_id=os.getpid(),
            started_at_utc=now,
        )
    with database_connection(path) as connection, connection:
        with pytest.raises(RuntimeError, match="already active"):
            claim_agent_session(
                connection,
                session_id="second",
                process_id=os.getpid(),
                started_at_utc=now + timedelta(seconds=1),
            )
    with database_connection(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM agent_runtime_sessions WHERE status='running'"
        ).fetchone()[0] == 1


def test_schema15_to_16_adds_ownership_guards_without_data_loss(tmp_path: Path):
    path = tmp_path / "schema15.db"
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER prevent_multiple_running_agent_inserts")
        connection.execute("DROP TRIGGER prevent_multiple_running_agent_updates")
        connection.execute(
            "INSERT INTO metrics (timestamp_utc, device_id) VALUES (?, ?)",
            ("2026-01-01T00:00:00+00:00", "preserved"),
        )
        connection.execute("PRAGMA user_version = 15")
    initialize_database(path)
    with database_connection(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 19
        assert connection.execute("SELECT COUNT(*) FROM metrics").fetchone()[0] == 1
        triggers = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )
        }
    assert {
        "prevent_multiple_running_agent_inserts",
        "prevent_multiple_running_agent_updates",
    } <= triggers


def test_wal_passive_checkpoint_is_safe_after_bounded_writes(tmp_path: Path):
    path = tmp_path / "checkpoint.db"
    initialize_database(path)
    run_write_transaction(
        path,
        lambda connection: connection.execute(
            "INSERT INTO metrics (timestamp_utc, device_id) VALUES (?, ?)",
            ("2026-01-01T00:00:00+00:00", "checkpoint-device"),
        ),
    )
    with database_connection(path) as connection:
        busy, log_pages, checkpointed = connection.execute(
            "PRAGMA wal_checkpoint(PASSIVE)"
        ).fetchone()
    assert busy == 0
    assert log_pages >= 0
    assert checkpointed >= 0


def test_alert_evaluation_and_raw_insert_contention_remain_idempotent(tmp_path: Path):
    path = make_alert_database(tmp_path)
    prepare(path, [{"ram": 97}, {"ram": 98}])
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def evaluate() -> None:
        try:
            barrier.wait()
            evaluate_alerts(path)
        except BaseException as error:
            errors.append(error)

    def write_raw() -> None:
        try:
            barrier.wait()
            run_write_transaction(
                path,
                lambda connection: insert_metric(
                    connection, sample(), manage_transaction=False
                ),
                priority="raw",
            )
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=evaluate), threading.Thread(target=write_raw)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(20)
    assert errors == []
    with database_connection(path) as connection:
        before = tuple(connection.execute(
            "SELECT (SELECT COUNT(*) FROM alert_occurrences),"
            " (SELECT COUNT(*) FROM alert_evidence),"
            " (SELECT COUNT(*) FROM alert_state_transitions)"
        ).fetchone())
    evaluate_alerts(path)
    with database_connection(path) as connection:
        after = tuple(connection.execute(
            "SELECT (SELECT COUNT(*) FROM alert_occurrences),"
            " (SELECT COUNT(*) FROM alert_evidence),"
            " (SELECT COUNT(*) FROM alert_state_transitions)"
        ).fetchone())
    assert before == after


def test_notification_audit_and_raw_insert_contention_send_once(tmp_path: Path):
    path = make_notification_database(tmp_path)
    seed_alert(path, severity="warning", suffix="concurrent")
    provider = RecordingProvider()
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def dispatch() -> None:
        try:
            barrier.wait()
            NotificationDispatcher(path, provider=provider).dispatch_pending()
        except BaseException as error:
            errors.append(error)

    def write_raw() -> None:
        try:
            barrier.wait()
            run_write_transaction(
                path,
                lambda connection: insert_metric(
                    connection, sample(), manage_transaction=False
                ),
                priority="raw",
            )
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=dispatch), threading.Thread(target=write_raw)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(20)
    assert errors == []
    assert len(provider.messages) == 1
    assert NotificationDispatcher(path, provider=provider).dispatch_pending().delivered == 0
    with database_connection(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM notification_deliveries"
        ).fetchone()[0] == 1


def test_candidate_learning_and_raw_insert_contention_keep_same_candidate(tmp_path: Path):
    path = prepared_baseline(tmp_path)
    status = start_recalibration(path, device_id="test-device")
    candidate_id = status["candidate_version"]["id"]
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def refresh() -> None:
        try:
            barrier.wait()
            refresh_candidate(path, device_id="test-device", force=True)
        except BaseException as error:
            errors.append(error)

    def write_raw() -> None:
        try:
            barrier.wait()
            run_write_transaction(
                path,
                lambda connection: insert_metric(
                    connection, sample(), manage_transaction=False
                ),
                priority="raw",
            )
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=refresh), threading.Thread(target=write_raw)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(20)
    assert errors == []
    with database_connection(path) as connection:
        candidates = connection.execute(
            """SELECT id FROM baseline_versions
            WHERE lifecycle_state IN ('candidate','paused','ready')"""
        ).fetchall()
        duplicate_training = connection.execute(
            """SELECT COUNT(*) FROM (
            SELECT version_profile_id, feature_window_id, COUNT(*) count
            FROM baseline_version_training_windows GROUP BY 1, 2
            HAVING count > 1)"""
        ).fetchone()[0]
    assert [row[0] for row in candidates] == [candidate_id]
    assert duplicate_training == 0
