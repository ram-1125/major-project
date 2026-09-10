from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import analytics.alerts as alerts_module
from analytics.alerts import (
    acknowledge_alert,
    evaluate_database,
)
from analytics.health import evaluate_database as evaluate_health
from backend.database import database_connection, initialize_database
from backend.main import create_app
from backend.phase5a_repository import get_alert


DEVICE = "alert-test-device"
BASE = datetime(2026, 2, 1, tzinfo=timezone.utc)


def make_database(tmp_path: Path) -> Path:
    path = tmp_path / "alerts.db"
    initialize_database(path)
    return path


def seed_window(
    connection: sqlite3.Connection,
    index: int,
    *,
    cpu: float = 20,
    ram: float = 45,
    swap: float = 2,
    disk: float = 50,
    disk_read: float = 1_000_000,
    disk_write: float = 500_000,
    workload: str = "idle",
    confidence: float = 0.9,
    complete: bool = True,
    coverage: float = 1.0,
    temperature: float | None = None,
) -> int:
    start = BASE + timedelta(minutes=index * 5)
    return int(
        connection.execute(
            """INSERT INTO feature_windows (
            device_id, window_start_utc, window_end_utc, sample_count,
            expected_sample_count, coverage_ratio, is_complete,
            dominant_workload_class, workload_confidence,
            missing_indicators_json, cpu_avg, cpu_p95, cpu_high_ratio,
            ram_avg, swap_avg, disk_usage_avg, disk_read_avg, disk_write_avg,
            cpu_temperature_avg, gpu_temperature_avg,
            cpu_temperature_missing_ratio, gpu_utilization_missing_ratio,
            gpu_memory_missing_ratio, gpu_temperature_missing_ratio,
            updated_at_utc
            ) VALUES (?, ?, ?, 10, 10, ?, ?, ?, ?, '{}', ?, ?, ?, ?, ?, ?,
            ?, ?, ?, NULL, ?, 1, 1, 1, ?)""",
            (
                DEVICE, start.isoformat(), (start + timedelta(minutes=5)).isoformat(),
                coverage, int(complete), workload, confidence, cpu, cpu,
                1.0 if cpu >= 85 else 0.0, ram, swap, disk, disk_read,
                disk_write, temperature, 0.0 if temperature is not None else 1.0,
                datetime.now(timezone.utc).isoformat(),
            ),
        ).lastrowid
    )


def seed_event(
    connection: sqlite3.Connection,
    index: int,
    record_id: int,
    level: str = "Critical",
    category: str = "storage",
) -> None:
    timestamp = BASE + timedelta(minutes=index * 5 + 1)
    connection.execute(
        """INSERT INTO windows_events (
        device_id, event_timestamp_utc, channel, provider_name, event_id,
        record_id, event_level, smartops_category, safe_summary, collected_at_utc
        ) VALUES (?, ?, 'System', 'Test Provider', 41, ?, ?, ?,
        'Mapped test evidence.', ?)""",
        (
            DEVICE, timestamp.isoformat(), record_id, level, category,
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def prepare(
    path: Path,
    windows: list[dict[str, object]],
    *,
    scores: list[float] | None = None,
    states: list[str] | None = None,
    confidences: list[float] | None = None,
) -> list[int]:
    ids: list[int] = []
    with database_connection(path) as connection:
        with connection:
            for index, values in enumerate(windows):
                ids.append(seed_window(connection, index, **values))
    evaluate_health(path)
    with database_connection(path) as connection:
        with connection:
            for index, window_id in enumerate(ids):
                if scores is not None or states is not None or confidences is not None:
                    connection.execute(
                        """UPDATE health_assessments SET
                        system_health_score = COALESCE(?, system_health_score),
                        health_band = CASE
                            WHEN ? IS NULL THEN health_band
                            WHEN ? >= 85 THEN 'good'
                            WHEN ? >= 70 THEN 'stable'
                            WHEN ? >= 50 THEN 'attention'
                            WHEN ? >= 30 THEN 'degraded'
                            ELSE 'critical_condition' END,
                        evaluation_state = COALESCE(?, evaluation_state),
                        data_confidence = COALESCE(?, data_confidence),
                        updated_at_utc = ?
                        WHERE feature_window_id = ?""",
                        (
                            scores[index] if scores else None,
                            scores[index] if scores else None,
                            scores[index] if scores else None,
                            scores[index] if scores else None,
                            scores[index] if scores else None,
                            scores[index] if scores else None,
                            states[index] if states else None,
                            confidences[index] if confidences else None,
                            (BASE + timedelta(days=1, seconds=index)).isoformat(),
                            window_id,
                        ),
                    )
    return ids


def test_schema7_migration_preserves_existing_records(tmp_path: Path):
    path = make_database(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """INSERT INTO metrics (timestamp_utc, device_id)
            VALUES (?, 'preserved-device')""",
            (BASE.isoformat(),),
        )
        connection.execute("PRAGMA user_version = 7")
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 19
        assert connection.execute("SELECT COUNT(*) FROM metrics").fetchone()[0] == 1
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {
        "alerts", "alert_occurrences", "alert_evidence",
        "alert_state_transitions", "alert_explanations",
        "alert_recommendations", "alert_evaluation_runs",
    } <= tables


def test_not_evaluated_and_incomplete_windows_never_alert(tmp_path: Path):
    path = make_database(tmp_path)
    prepare(
        path,
        [{"cpu": 99}, {"cpu": 99, "complete": False}],
        states=["not_evaluated", "not_evaluated"],
    )
    result = evaluate_database(path)
    with database_connection(path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    assert count == 0
    assert result["not_evaluated"] == 2


def test_isolated_cpu_spike_is_protected_but_repetition_opens_one_alert(
    tmp_path: Path,
):
    path = make_database(tmp_path)
    first = prepare(path, [{"cpu": 96}])
    result = evaluate_database(path, window_id=first[0])
    assert result["isolated_protected"] >= 1
    with database_connection(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 0
        with connection:
            second = seed_window(connection, 1, cpu=96)
    evaluate_health(path, window_id=second)
    evaluate_database(path, window_id=second)
    with database_connection(path) as connection:
        alert = connection.execute(
            "SELECT * FROM alerts WHERE category = 'resource_pressure'"
        ).fetchone()
        assert alert is not None
        assert alert["consecutive_window_count"] == 2
        assert alert["current_severity"] in {"advisory", "warning"}


def test_moderate_development_cpu_alone_does_not_create_warning(tmp_path: Path):
    path = make_database(tmp_path)
    prepare(
        path,
        [
            {"cpu": 68, "workload": "development", "confidence": 0.9},
            {"cpu": 69, "workload": "development", "confidence": 0.9},
            {"cpu": 67, "workload": "development", "confidence": 0.9},
        ],
    )
    evaluate_database(path)
    with database_connection(path) as connection:
        rows = connection.execute(
            "SELECT current_severity, alert_code FROM alerts"
        ).fetchall()
    assert all(row["current_severity"] not in {"warning", "urgent"} for row in rows)
    assert all(row["alert_code"] != "ALT-CPU-001" for row in rows)


def test_future_alert_snapshot_confidence_validation_and_outcome_are_separate(
    tmp_path: Path,
):
    """A future occurrence is immutable; an outcome labels evidence only."""
    path = make_database(tmp_path)
    ids = prepare(path, [{"cpu": 96}, {"cpu": 97}])
    evaluate_database(path, window_id=ids[0])
    evaluate_database(path, window_id=ids[1])
    with database_connection(path) as connection:
        alert_id = int(connection.execute(
            "SELECT id FROM alerts WHERE category='resource_pressure'"
        ).fetchone()[0])
        snapshot_count = connection.execute(
            "SELECT COUNT(*) FROM alert_explanation_snapshots WHERE alert_id=?",
            (alert_id,),
        ).fetchone()[0]
        before_baselines = connection.execute(
            "SELECT COUNT(*) FROM baseline_versions"
        ).fetchone()[0]
    assert snapshot_count == 1

    with TestClient(create_app(path)) as client:
        detail = client.get(f"/api/alerts/{alert_id}")
        assert detail.status_code == 200
        body = detail.json()["alert"]
        assert body["short_alert_basis"]
        assert body["explanation_snapshot"] is not None
        assert body["validation"]["display_label"] == "Not yet validated"
        assert body["validation"]["accuracy"] is None
        labelled = client.post(
            f"/api/alerts/{alert_id}/outcome",
            json={"outcome": "confirmed", "note": "Observed outcome only."},
        )
        assert labelled.status_code == 200
        assert labelled.json()["outcome"]["new_outcome"] == "confirmed"

    with database_connection(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM baseline_versions"
        ).fetchone()[0] == before_baselines
        assert connection.execute(
            "SELECT COUNT(*) FROM alert_outcome_events"
        ).fetchone()[0] == 1


def test_resolved_alert_detail_uses_material_trigger_not_recovery(tmp_path: Path):
    path = make_database(tmp_path)
    prepare(path, [{"cpu": 96}, {"cpu": 97}])
    evaluate_database(path)
    with database_connection(path) as connection:
        alert = connection.execute("SELECT * FROM alerts LIMIT 1").fetchone()
        trigger = connection.execute(
            "SELECT * FROM alert_occurrences WHERE alert_id=? AND condition_met=1 ORDER BY id DESC LIMIT 1",
            (alert["id"],),
        ).fetchone()
        assert trigger is not None
        recovery_source = connection.execute(
            """SELECT window.id feature_window_id, health.id health_assessment_id
            FROM feature_windows window JOIN health_assessments health
              ON health.feature_window_id=window.id
            WHERE window.id != ? ORDER BY window.id LIMIT 1""",
            (trigger["feature_window_id"],),
        ).fetchone()
        recovery_time = (BASE + timedelta(hours=2)).isoformat()
        with connection:
            recovery_id = connection.execute(
                """INSERT INTO alert_occurrences (
                alert_id, feature_window_id, health_assessment_id,
                risk_assessment_id, deviation_assessment_id, observed_at_utc,
                severity, condition_met, raw_evidence_strength,
                effective_evidence_strength, temporal_pattern, trend_direction,
                evidence_signature, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 'recovery', 'recovering', ?, ?)""",
                (
                    trigger["alert_id"], recovery_source["feature_window_id"],
                    recovery_source["health_assessment_id"], trigger["risk_assessment_id"],
                    trigger["deviation_assessment_id"], recovery_time,
                    trigger["severity"], "recovery-test", recovery_time,
                ),
            ).lastrowid
            connection.execute(
                "UPDATE alerts SET state='resolved', resolved_at_utc=?, latest_observed_utc=? WHERE id=?",
                (recovery_time, recovery_time, alert["id"]),
            )
        detail = get_alert(connection, int(alert["id"]))
    assert detail is not None
    assert detail["material_occurrence"]["id"] == trigger["id"]
    assert detail["material_occurrence"]["id"] != recovery_id
    assert any(item["id"] == recovery_id for item in detail["occurrences"])
    assert detail["evidence"]


def test_alert_outcome_revision_is_idempotent_and_updates_preliminary_precision(tmp_path: Path):
    path = make_database(tmp_path)
    prepare(path, [{"cpu": 96}, {"cpu": 97}])
    evaluate_database(path)
    with database_connection(path) as connection:
        alert_id = int(connection.execute("SELECT id FROM alerts LIMIT 1").fetchone()[0])
    with TestClient(create_app(path)) as client:
        first = client.post(f"/api/alerts/{alert_id}/outcome", json={"outcome": "confirmed"})
        duplicate = client.post(f"/api/alerts/{alert_id}/outcome", json={"outcome": "confirmed"})
        revised = client.post(f"/api/alerts/{alert_id}/outcome", json={"outcome": "false_positive"})
        summary = client.get("/api/validation/user-reviewed-summary").json()
    assert first.json()["outcome"]["changed"] is True
    assert duplicate.json()["outcome"]["changed"] is False
    assert revised.json()["outcome"]["previous_outcome"] == "confirmed"
    assert summary["confirmed_count"] == 0
    assert summary["false_positive_count"] == 1
    assert summary["precision"] == 0
    assert summary["full_accuracy"] is None
    with database_connection(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM alert_outcome_events").fetchone()[0] == 2


def test_high_confidence_workload_exception_and_low_confidence_conservatism(
    tmp_path: Path,
):
    expected = make_database(tmp_path / "expected")
    ids = prepare(expected, [
        {"cpu": 96, "workload": "development", "confidence": 0.9},
        {"cpu": 96, "workload": "development", "confidence": 0.9},
    ])
    evaluate_database(expected)
    with database_connection(expected) as connection:
        assert connection.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 0
    with database_connection(expected) as connection:
        with connection:
                third = seed_window(
                    connection, 2, cpu=96,
                    workload="development", confidence=0.9,
                )
                connection.execute(
                    """UPDATE feature_windows SET workload_rule_version = ?
                    WHERE id = ?""",
                    ("phase7b1-workload-v3", third),
                )
    evaluate_health(expected, window_id=third)
    with database_connection(expected) as connection:
        health_row = connection.execute(
            "SELECT evaluation_state, system_health_score, reason_codes_json "
            "FROM health_assessments WHERE feature_window_id = ?", (third,)
        ).fetchone()
    assert health_row is not None, third
    third_result = evaluate_database(expected, window_id=third)
    assert third_result["not_evaluated"] == 0, third_result
    with database_connection(expected) as connection:
        row = connection.execute("SELECT * FROM alerts").fetchone()
        assert row is not None, third_result
        assert "workload_exception_applied" in row["reason_codes_json"]
        assert row["workload_rule_version"] == "phase7b1-workload-v3"

    conservative = make_database(tmp_path / "conservative")
    prepare(
        conservative,
        [
            {"cpu": 96, "workload": "development", "confidence": 0.3},
            {"cpu": 96, "workload": "development", "confidence": 0.3},
        ],
        scores=[90, 90],
        states=["provisional", "provisional"],
    )
    evaluate_database(conservative)
    with database_connection(conservative) as connection:
        assert connection.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 1


def test_memory_and_swap_are_one_correlated_alert(tmp_path: Path):
    path = make_database(tmp_path)
    prepare(path, [
        {"ram": 96, "swap": 40},
        {"ram": 97, "swap": 45},
    ])
    evaluate_database(path)
    with database_connection(path) as connection:
        rows = connection.execute(
            "SELECT * FROM alerts WHERE category = 'memory_and_swap_pressure'"
        ).fetchall()
        assert len(rows) == 1
        evidence = connection.execute(
            """SELECT e.* FROM alert_evidence e JOIN alert_occurrences o
            ON o.id = e.occurrence_id WHERE o.alert_id = ?""",
            (rows[0]["id"],),
        ).fetchall()
    assert {row["correlation_group"] for row in evidence} == {"memory"}
    assert any(row["suppressed"] for row in evidence)


def test_sudden_mapped_critical_event_is_urgent_and_event_is_counted_once(
    tmp_path: Path,
):
    path = make_database(tmp_path)
    ids = prepare(path, [{}])
    with database_connection(path) as connection:
        with connection:
            seed_event(connection, 0, 1)
    evaluate_database(path, window_id=ids[0])
    with database_connection(path) as connection:
        alert = connection.execute(
            "SELECT * FROM alerts WHERE category = 'repeated_serious_event'"
        ).fetchone()
        assert alert is not None
        assert alert["current_severity"] == "urgent"
        event_evidence = connection.execute(
            """SELECT COUNT(*) FROM alert_evidence e JOIN alert_occurrences o
            ON o.id = e.occurrence_id WHERE o.alert_id = ?
            AND e.source_table = 'windows_events' AND e.source_id = 1""",
            (alert["id"],),
        ).fetchone()[0]
    assert event_evidence == 1


def test_provisional_health_is_disclosed_and_missing_sensors_do_not_alert(
    tmp_path: Path,
):
    path = make_database(tmp_path)
    prepare(path, [{}], scores=[40], states=["provisional"])
    evaluate_database(path)
    with database_connection(path) as connection:
        alerts = connection.execute("SELECT * FROM alerts").fetchall()
    assert len(alerts) == 1
    assert alerts[0]["category"] == "degraded_system_health"
    assert alerts[0]["evaluation_state"] == "provisional"
    assert alerts[0]["category"] != "thermal_evidence"
    assert "cpu_temperature_avg" in alerts[0]["excluded_inputs_json"]


def test_recovery_requires_two_windows_and_preserves_resolution_history(
    tmp_path: Path,
):
    path = make_database(tmp_path)
    prepare(path, [
        {"ram": 96, "swap": 30},
        {"ram": 96, "swap": 30},
        {},
        {},
    ])
    evaluate_database(path)
    with database_connection(path) as connection:
        alert = connection.execute(
            "SELECT * FROM alerts WHERE category = 'memory_and_swap_pressure'"
        ).fetchone()
        transitions = connection.execute(
            """SELECT transition_type FROM alert_state_transitions
            WHERE alert_id = ? ORDER BY id""",
            (alert["id"],),
        ).fetchall()
    assert alert["state"] == "resolved"
    assert alert["resolved_at_utc"] is not None
    assert [row["transition_type"] for row in transitions] == [
        "opened", "recovery_started", "resolved"
    ]


def test_acknowledgement_does_not_resolve_and_is_idempotent(tmp_path: Path):
    path = make_database(tmp_path)
    ids = prepare(path, [{"ram": 96}, {"ram": 97}])
    evaluate_database(path)
    with database_connection(path) as connection:
        alert_id = connection.execute("SELECT id FROM alerts").fetchone()["id"]
    first = acknowledge_alert(path, alert_id)
    second = acknowledge_alert(path, alert_id)
    assert first == {"id": alert_id, "state": "acknowledged", "changed": True}
    assert second == {"id": alert_id, "state": "acknowledged", "changed": False}
    with database_connection(path) as connection:
        row = connection.execute("SELECT * FROM alerts").fetchone()
    assert row["state"] == "acknowledged"
    assert row["resolved_at_utc"] is None


def test_evaluation_backfill_fingerprint_and_restart_are_idempotent(tmp_path: Path):
    path = make_database(tmp_path)
    prepare(path, [{"ram": 96}, {"ram": 97}, {"ram": 98}])
    first = evaluate_database(path, command="backfill")
    second = evaluate_database(path, command="backfill")
    with database_connection(path) as connection:
        alert_count = connection.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        occurrence_count = connection.execute(
            "SELECT COUNT(*) FROM alert_occurrences"
        ).fetchone()[0]
        fingerprints = connection.execute(
            "SELECT DISTINCT alert_fingerprint FROM alerts"
        ).fetchall()
    assert first["created"] == 1
    assert second["unchanged"] == 3
    assert alert_count == 1
    # The middle repeated observation is compacted into observation_count.
    assert occurrence_count == 2
    assert len(fingerprints) == 1


def test_api_filters_pagination_detail_acknowledgement_and_get_immutability(
    tmp_path: Path,
):
    path = make_database(tmp_path)
    prepare(path, [{"ram": 96}, {"ram": 97}])
    evaluate_database(path)
    with TestClient(create_app(path)) as client:
        with database_connection(path) as connection:
            before = {
                table: connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                for table in (
                    "alerts", "alert_occurrences", "alert_state_transitions",
                    "alert_evaluation_runs", "notification_deliveries",
                )
            }
        status = client.get("/api/alerts/status")
        latest = client.get("/api/alerts/latest")
        history = client.get(
            "/api/alerts/history",
            params={
                "limit": 1, "offset": 0, "severity": "warning",
                "state": "open", "sort": "oldest",
            },
        )
        summary_history = client.get(
            "/api/alerts/history",
            params={"limit": 1, "summary": "true"},
        )
        count = client.get("/api/alerts/count", params={"state": "open"})
        alert_id = latest.json()["items"][0]["id"]
        detail = client.get(f"/api/alerts/{alert_id}")
        with database_connection(path) as connection:
            after_get = {
                table: connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                for table in before
            }
        acknowledged = client.post(f"/api/alerts/{alert_id}/acknowledge")
        old_api = client.get("/api/status")
    assert status.status_code == latest.status_code == detail.status_code == 200
    assert history.status_code == count.status_code == 200
    assert summary_history.status_code == 200
    assert before == after_get
    assert count.json()["count"] == 1
    assert detail.json()["alert"]["evidence"]
    assert detail.json()["alert"]["diagnostic_recommendations"]
    assert detail.json()["alert"]["notification_deliveries"] == []
    assert summary_history.json()["summary"] is True
    assert summary_history.json()["items"][0]["summary_record"] is True
    assert summary_history.json()["items"][0]["evidence"] == []
    assert acknowledged.json()["state"] == "acknowledged"
    assert old_api.status_code == 200


def test_alert_history_searches_beyond_first_fifty_and_filters_canonical_workload(tmp_path: Path):
    path = make_database(tmp_path)
    prepare(path, [{"ram": 96}, {"ram": 97}])
    evaluate_database(path)
    with database_connection(path) as connection, connection:
        source = connection.execute("SELECT * FROM alerts LIMIT 1").fetchone()
        columns = [row["name"] for row in connection.execute("PRAGMA table_info(alerts)") if row["name"] != "id"]
        for index in range(60):
            values = {column: source[column] for column in columns}
            values.update({
                "alert_fingerprint": f"history-search-{index}",
                "alert_code": f"SEARCH-{index}",
                "title": "Target Beyond Fifty" if index == 0 else f"Routine stored alert {index}",
                "workload_context": "browser_or_media" if index == 0 else "interactive_light",
            })
            connection.execute(
                f"INSERT INTO alerts ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                tuple(values[column] for column in columns),
            )
    with TestClient(create_app(path)) as client:
        response = client.get("/api/alerts/history", params={
            "limit": 25, "offset": 0, "summary": "true",
            "search": "Target Beyond Fifty", "workload": "browser_or_media",
        })
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["title"] == "Target Beyond Fifty"


def test_every_severity_has_a_controlled_synthetic_path(tmp_path: Path):
    informational = make_database(tmp_path / "informational")
    prepare(
        informational,
        [
            {"cpu": 96, "workload": "development", "confidence": 0.9},
            {"cpu": 96, "workload": "development", "confidence": 0.9},
            {"cpu": 96, "workload": "development", "confidence": 0.9},
        ],
    )
    evaluate_database(informational)

    advisory = make_database(tmp_path / "advisory")
    prepare(advisory, [{"disk": 90}])
    evaluate_database(advisory)

    warning = make_database(tmp_path / "warning")
    prepare(warning, [{"ram": 97, "swap": 45}, {"ram": 98, "swap": 50}])
    evaluate_database(warning)

    urgent = make_database(tmp_path / "urgent")
    urgent_ids = prepare(urgent, [{}])
    with database_connection(urgent) as connection:
        with connection:
            seed_event(connection, 0, 88)
    evaluate_database(urgent, window_id=urgent_ids[0])

    severities = set()
    for path in (informational, advisory, warning, urgent):
        with database_connection(path) as connection:
            severities.update(
                row["current_severity"]
                for row in connection.execute("SELECT current_severity FROM alerts")
            )
    assert severities == {"informational", "advisory", "warning", "urgent"}

    with TestClient(create_app(informational)) as client:
        assert client.get(
            "/api/alerts/history", params={"view": "attention", "summary": "true"}
        ).json()["total"] == 0
        assert client.get(
            "/api/alerts/history", params={"view": "observations", "summary": "true"}
        ).json()["total"] == 1
    with TestClient(create_app(warning)) as client:
        assert client.get(
            "/api/alerts/history", params={"view": "attention", "summary": "true"}
        ).json()["total"] == 1


def test_failed_evaluation_rolls_back_alert_and_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    path = make_database(tmp_path)
    prepare(path, [{"ram": 97}, {"ram": 98}])

    def fail_occurrence(*args: object, **kwargs: object) -> int:
        raise RuntimeError("controlled occurrence failure")

    monkeypatch.setattr(alerts_module, "_insert_occurrence", fail_occurrence)
    with pytest.raises(RuntimeError, match="controlled occurrence failure"):
        evaluate_database(path)
    with database_connection(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM alert_evaluation_runs"
        ).fetchone()[0] == 0


def test_cooldown_prevents_immediate_duplicate_then_links_real_return(
    tmp_path: Path,
):
    path = make_database(tmp_path)
    prepare(path, [
        {"ram": 97}, {"ram": 98}, {}, {},
        {"ram": 97}, {"ram": 98}, {"ram": 97}, {"ram": 98},
    ])
    # Resolve the first lifecycle after two pressure and two recovery windows.
    evaluate_database(path, end=(BASE + timedelta(minutes=20)).isoformat())
    # Windows 5 and 6 are inside the configured two-window cooldown.
    evaluate_database(
        path,
        start=(BASE + timedelta(minutes=20)).isoformat(),
        end=(BASE + timedelta(minutes=30)).isoformat(),
    )
    with database_connection(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 1
    # A persistent return after cooldown opens a linked second lifecycle.
    evaluate_database(
        path,
        start=(BASE + timedelta(minutes=30)).isoformat(),
    )
    with database_connection(path) as connection:
        rows = connection.execute(
            """SELECT id, lifecycle_number, predecessor_alert_id, state
            FROM alerts ORDER BY lifecycle_number"""
        ).fetchall()
    assert len(rows) == 2
    assert rows[0]["state"] == "resolved"
    assert rows[1]["lifecycle_number"] == 2
    assert rows[1]["predecessor_alert_id"] == rows[0]["id"]
