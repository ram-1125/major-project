from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from analytics.validation import evaluate_database, validation_status
from analytics.validation_config import INTERPRETATION, ValidationPolicy
from analytics.evaluation_report import build_evaluation_report
from backend.database import database_connection, initialize_database
from backend.main import create_app
from backend.phase5b_repository import (
    close_observation_period,
    create_feedback,
    create_incident,
    create_observation_period,
    dumps,
    get_feedback,
    get_incident,
    revise_feedback,
    revise_incident,
    upsert_alert_incident_link,
)


DEVICE = "validation-test-device"
BASE = datetime(2026, 5, 1, tzinfo=timezone.utc)
TEST_POLICY = ValidationPolicy(
    minimum_precision_alerts=1,
    minimum_recall_incidents=1,
    minimum_accuracy_windows=1,
    minimum_accuracy_distinct_days=1,
    minimum_accuracy_period_seconds=0,
    minimum_lead_time_matches=1,
)


def make_database(tmp_path: Path) -> Path:
    path = tmp_path / "phase5b.db"
    initialize_database(path)
    return path


def seed_window(connection: sqlite3.Connection, index: int) -> tuple[int, int]:
    start = BASE + timedelta(minutes=index * 5)
    cursor = connection.execute(
        """INSERT INTO feature_windows (
        device_id, window_start_utc, window_end_utc, sample_count,
        expected_sample_count, coverage_ratio, is_complete,
        dominant_workload_class, workload_confidence,
        missing_indicators_json, cpu_avg, ram_avg, swap_avg, disk_usage_avg,
        updated_at_utc
        ) VALUES (?, ?, ?, 10, 10, 1, 1, 'idle', 0.9, '[]',
        20, 40, 0, 50, ?)""",
        (
            DEVICE,
            start.isoformat(),
            (start + timedelta(minutes=5)).isoformat(),
            start.isoformat(),
        ),
    )
    window_id = int(cursor.lastrowid)
    health_cursor = connection.execute(
        """INSERT INTO health_assessments (
        feature_window_id, device_id, window_start_utc, window_end_utc,
        assessed_at_utc, system_health_score, health_band, evaluation_state,
        data_confidence, coverage_ratio, workload_context, workload_confidence,
        available_component_weight, excluded_component_weight,
        normalization_method, algorithm_version, configuration_version,
        reason_codes_json, consecutive_window_count,
        persistence_duration_seconds, trend_direction, recovery_state,
        feature_signature, feature_updated_at_utc, created_at_utc, updated_at_utc
        ) VALUES (?, ?, ?, ?, ?, 90, 'good', 'provisional', 0.8, 1,
        'idle', 0.9, 1, 0, 'available_weight_normalization',
        'health-v1', 'phase4a-v1', '[]', 1, 300, 'stable',
        'not_recovering', ?, ?, ?, ?)""",
        (
            window_id,
            DEVICE,
            start.isoformat(),
            (start + timedelta(minutes=5)).isoformat(),
            start.isoformat(),
            f"window-{window_id}",
            start.isoformat(),
            start.isoformat(),
            start.isoformat(),
        ),
    )
    return window_id, int(health_cursor.lastrowid)


def seed_alert(
    connection: sqlite3.Connection,
    window_id: int,
    health_id: int,
    *,
    minutes: int,
    category: str = "system_stability",
) -> int:
    observed = BASE + timedelta(minutes=minutes)
    cursor = connection.execute(
        """INSERT INTO alerts (
        device_id, alert_fingerprint, lifecycle_number, alert_code, category,
        title, description, evidence_domain, probable_factor,
        current_severity, peak_severity, state, evaluation_state,
        data_confidence, workload_context, workload_confidence,
        first_observed_utc, latest_observed_utc, last_evidence_utc,
        occurrence_count, consecutive_window_count, duration_seconds,
        trend_direction, recovery_window_count, recovery_state,
        health_assessment_id, source_feature_window_id,
        probable_factors_json, contradictory_evidence_json,
        excluded_inputs_json, reason_codes_json, algorithm_version,
        configuration_version, catalogue_version, created_at_utc, updated_at_utc
        ) VALUES (?, ?, 1, 'TEST_ALERT', ?, 'Test alert', 'Test alert.',
        'stability', 'test factor', 'warning', 'warning', 'open', 'evaluated',
        0.9, 'idle', 0.9, ?, ?, ?, 1, 1, 300, 'stable', 0,
        'not_recovering', ?, ?, '[]', '[]', '[]', '[]',
        'predictive-alert-v1', 'phase5a-v1', 'alert-catalogue-v1', ?, ?)""",
        (
            DEVICE,
            f"fingerprint-{window_id}-{minutes}-{category}",
            category,
            observed.isoformat(),
            observed.isoformat(),
            observed.isoformat(),
            health_id,
            window_id,
            observed.isoformat(),
            observed.isoformat(),
        ),
    )
    return int(cursor.lastrowid)


def incident_values(
    *,
    start: datetime,
    category: str = "unexpected_restart",
    status: str = "active",
    verification: str = "user_reported",
) -> dict[str, object]:
    return {
        "category": category,
        "severity": "serious",
        "start_utc": start.isoformat(),
        "end_utc": None,
        "timestamp_precision": "exact",
        "verification_status": verification,
        "verification_source": None,
        "workload_context": "idle",
        "symptoms_text": "The test system restarted unexpectedly.",
        "windows_event_ids_json": "[]",
        "action_taken": None,
        "observed_outcome": "Recovered after restart.",
        "status": status,
        "data_confidence": 0.9,
        "reason_codes_json": "[]",
        "limitations_json": "[]",
    }


def feedback_values(
    outcome: str,
    *,
    horizon: float | None = 90_000,
    preventive: bool = False,
    verification: str = "user_reported",
) -> dict[str, object]:
    return {
        "outcome": outcome,
        "observation_horizon_seconds": horizon,
        "verification_status": verification,
        "verification_timestamp_utc": BASE.isoformat(),
        "verification_source": None,
        "notes": "Synthetic test feedback.",
        "user_reason_codes_json": "[]",
        "structured_action_taken": None,
        "condition_state": "unclear",
        "preventive_action_taken": int(preventive),
        "status": "active",
        "data_confidence": 0.9,
        "reason_codes_json": "[]",
        "limitations_json": "[]",
    }


def completed_period(
    connection: sqlite3.Connection,
    *,
    end: datetime,
    complete_reporting: bool = True,
) -> int:
    item = create_observation_period(
        connection, device_id=DEVICE, start_utc=BASE.isoformat()
    )
    result = close_observation_period(
        connection,
        item["id"],
        end_utc=end.isoformat(),
        incident_reporting_complete=complete_reporting,
        state="completed",
        missing_intervals=[],
        interruption_notes=None,
    )
    assert result is not None
    return int(item["id"])


def test_schema8_migrates_additively_to_current_schema(tmp_path: Path):
    path = make_database(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO metrics (timestamp_utc, device_id) VALUES (?, ?)",
            (BASE.isoformat(), DEVICE),
        )
        connection.execute("PRAGMA user_version = 8")
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        count = connection.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert version == 18
    assert count == 1
    assert {
        "validation_observation_periods",
        "incident_reports",
        "incident_report_revisions",
        "alert_feedback",
        "alert_feedback_revisions",
        "alert_incident_links",
        "validation_evaluation_runs",
        "validation_metric_results",
        "warning_lead_time_results",
        "validation_inclusion_exclusion",
    } <= tables


def test_incident_revisions_are_append_only_and_withdrawal_is_preserved(tmp_path: Path):
    path = make_database(tmp_path)
    with database_connection(path) as connection:
        with connection:
            item = create_incident(
                connection,
                device_id=DEVICE,
                values=incident_values(start=BASE),
            )
            revised = revise_incident(
                connection,
                item["id"],
                updates={"severity": "critical"},
                revision_reason="Severity confirmed.",
            )
            withdrawn = revise_incident(
                connection,
                item["id"],
                updates={"status": "withdrawn"},
                revision_reason="Duplicate report.",
            )
        stored = get_incident(connection, item["id"])
    assert revised and withdrawn and stored
    assert stored["status"] == "withdrawn"
    assert stored["current_revision_number"] == 3
    assert [row["severity"] for row in stored["revisions"]] == [
        "serious", "critical", "critical"
    ]


def test_feedback_revisions_do_not_overwrite_history(tmp_path: Path):
    path = make_database(tmp_path)
    with database_connection(path) as connection:
        with connection:
            window, health = seed_window(connection, 0)
            alert = seed_alert(connection, window, health, minutes=1)
            created = create_feedback(
                connection,
                alert_id=alert,
                values=feedback_values("uncertain", horizon=None),
            )
            revise_feedback(
                connection,
                alert,
                updates=feedback_values("no_issue_observed"),
                revision_reason="Observation period completed.",
            )
        item = get_feedback(connection, alert)
        lifecycle = connection.execute(
            "SELECT state FROM alerts WHERE id = ?", (alert,)
        ).fetchone()["state"]
    assert created["outcome"] == "uncertain"
    assert item and item["outcome"] == "no_issue_observed"
    assert [row["outcome"] for row in item["revisions"]] == [
        "uncertain", "no_issue_observed"
    ]
    assert lifecycle == "open"


def test_missing_feedback_and_incomplete_period_never_become_negatives(tmp_path: Path):
    path = make_database(tmp_path)
    with database_connection(path) as connection:
        with connection:
            window, health = seed_window(connection, 0)
            seed_alert(connection, window, health, minutes=1)
            item = create_observation_period(
                connection, device_id=DEVICE, start_utc=BASE.isoformat()
            )
            close_observation_period(
                connection,
                item["id"],
                end_utc=(BASE + timedelta(minutes=5)).isoformat(),
                incident_reporting_complete=False,
                state="completed",
                missing_intervals=[],
                interruption_notes="Reporting was not complete.",
            )
    result = evaluate_database(path, policy=TEST_POLICY)
    metrics = {
        row["metric_name"]: row for row in result["metrics"]
        if row["scope_type"] == "overall"
    }
    assert result["status"] == "insufficient_labeled_evidence"
    assert metrics["precision"]["metric_value"] is None
    assert metrics["accuracy"]["metric_value"] is None
    assert metrics["accuracy"]["denominator"] == 0


def test_baseline_training_window_is_excluded_from_independent_evaluation(
    tmp_path: Path,
):
    path = make_database(tmp_path)
    with database_connection(path) as connection:
        with connection:
            window, _ = seed_window(connection, 0)
            cursor = connection.execute(
                """INSERT INTO baseline_profiles (
                device_id, workload_scope, training_start_utc, training_end_utc,
                eligible_window_count, excluded_window_count, distinct_day_count,
                readiness_state, algorithm_version, configuration_version,
                feature_names_json, missing_features_json, created_at_utc,
                updated_at_utc
                ) VALUES (?, 'device', ?, ?, 1, 0, 1, 'ready',
                'robust-baseline-v1', 'test-baseline-v1', '[]', '[]', ?, ?)""",
                (DEVICE, BASE.isoformat(), BASE.isoformat(), BASE.isoformat(), BASE.isoformat()),
            )
            connection.execute(
                """INSERT INTO baseline_training_windows
                (baseline_id, feature_window_id) VALUES (?, ?)""",
                (int(cursor.lastrowid), window),
            )
            completed_period(connection, end=BASE + timedelta(minutes=5))
    result = evaluate_database(path, policy=TEST_POLICY)
    with database_connection(path) as connection:
        decision = connection.execute(
            """SELECT included, reason_codes_json, details_json
            FROM validation_inclusion_exclusion
            WHERE evaluation_run_id = ? AND evidence_type = 'feature_window'
            AND evidence_id = ?""",
            (result["id"], window),
        ).fetchone()
    assert decision["included"] == 0
    assert json.loads(decision["reason_codes_json"]) == [
        "feature_window_used_for_baseline_training"
    ]
    assert json.loads(decision["details_json"])["baseline_training_references"]
    assert result["eligible_window_count"] == 0


def test_true_positive_false_positive_false_negative_true_negative_and_lead_time(
    tmp_path: Path,
):
    path = make_database(tmp_path)
    with database_connection(path) as connection:
        with connection:
            seeded = [seed_window(connection, index) for index in range(4)]
            tp_alert = seed_alert(
                connection, *seeded[0], minutes=1, category="system_stability"
            )
            fp_alert = seed_alert(
                connection, *seeded[1], minutes=6, category="disk_io_pressure"
            )
            tp_incident = create_incident(
                connection,
                device_id=DEVICE,
                values=incident_values(start=BASE + timedelta(minutes=3)),
            )
            create_incident(
                connection,
                device_id=DEVICE,
                values=incident_values(
                    start=BASE + timedelta(minutes=12),
                    category="memory_exhaustion",
                ),
            )
            create_feedback(
                connection,
                alert_id=tp_alert,
                values=feedback_values("confirmed_related_issue", horizon=None),
            )
            create_feedback(
                connection,
                alert_id=fp_alert,
                values=feedback_values("no_issue_observed"),
            )
            upsert_alert_incident_link(
                connection,
                alert_id=tp_alert,
                incident_id=tp_incident["id"],
                match_type="confirmed_match",
                origin="manual",
                confirmed_by_user=True,
                matching_score=1,
                time_difference_seconds=120,
                category_compatible=True,
                matching_rule="explicit_user_link",
                supporting_evidence=["test_confirmation"],
                contradictory_evidence=[],
                reason_codes=["explicit_user_classification"],
            )
            completed_period(connection, end=BASE + timedelta(minutes=20))
    result = evaluate_database(path, policy=TEST_POLICY)
    metrics = {
        row["metric_name"]: row for row in result["metrics"]
        if row["scope_type"] == "overall"
    }
    assert metrics["precision"]["metric_value"] == pytest.approx(0.5)
    assert metrics["recall"]["metric_value"] == pytest.approx(0.5)
    assert metrics["accuracy"]["metric_value"] == pytest.approx(0.5)
    assert metrics["balanced_accuracy"]["metric_value"] == pytest.approx(0.5)
    assert metrics["mean_warning_lead_time_seconds"]["metric_value"] == 120
    assert len(result["lead_times"]) == 1
    report = build_evaluation_report(path)
    assert report["scientifically_publishable"] is True
    assert report["confusion_matrix_counts"] == {
        "TP": 1, "TN": 1, "FP": 1, "FN": 1
    }
    assert report["sample_counts"] == {
        "evaluated_windows": 4,
        "normal_samples": 2,
        "failure_risk_samples": 2,
    }
    assert report["metrics"]["accuracy"] == pytest.approx(0.5)
    assert report["metrics"]["balanced_accuracy"] == pytest.approx(0.5)
    assert report["metrics"]["false_positive_rate"] == pytest.approx(0.5)
    assert report["metrics"]["false_negative_rate"] == pytest.approx(0.5)
    assert report["metrics"]["f1_score"] == pytest.approx(0.5)
    assert report["average_prediction_confidence"] is None

    with database_connection(path) as connection:
        decisions = list(
            connection.execute(
                """SELECT classification, details_json
                FROM validation_inclusion_exclusion
                WHERE evaluation_run_id = ? AND evidence_type = 'feature_window'
                AND included = 1 ORDER BY evidence_id""",
                (result["id"],),
            )
        )
    assert len(decisions) == 4
    expected_labels = {
        "true_positive": (1, 1),
        "true_negative": (0, 0),
        "false_positive": (1, 0),
        "false_negative": (0, 1),
    }
    for decision in decisions:
        details = json.loads(decision["details_json"])
        assert (details["predicted_class"], details["true_class"]) == expected_labels[
            decision["classification"]
        ]
        assert details["prediction_confidence"] is None
        assert details["timestamp_utc"]
        assert details["workload_context"] == "idle"
        assert "baseline_version_id" in details
        assert details["validation_algorithm_version"] == "validation-v1"


def test_preventive_action_and_short_no_issue_horizon_are_excluded(tmp_path: Path):
    path = make_database(tmp_path)
    with database_connection(path) as connection:
        with connection:
            first = seed_window(connection, 0)
            second = seed_window(connection, 1)
            preventive = seed_alert(connection, *first, minutes=1)
            short = seed_alert(connection, *second, minutes=6)
            create_feedback(
                connection,
                alert_id=preventive,
                values=feedback_values(
                    "preventive_action_taken", preventive=True
                ),
            )
            create_feedback(
                connection,
                alert_id=short,
                values=feedback_values("no_issue_observed", horizon=60),
            )
            completed_period(connection, end=BASE + timedelta(minutes=10))
    result = evaluate_database(path, policy=TEST_POLICY)
    precision = next(
        row for row in result["metrics"]
        if row["scope_type"] == "overall" and row["metric_name"] == "precision"
    )
    assert precision["metric_value"] is None
    assert result["eligible_alert_count"] == 0


def test_automatic_matching_never_confirms_causality(tmp_path: Path):
    path = make_database(tmp_path)
    with database_connection(path) as connection:
        with connection:
            seeded = seed_window(connection, 0)
            seed_alert(connection, *seeded, minutes=1)
            create_incident(
                connection,
                device_id=DEVICE,
                values=incident_values(start=BASE + timedelta(minutes=3)),
            )
            completed_period(connection, end=BASE + timedelta(minutes=5))
    evaluate_database(path, policy=TEST_POLICY)
    with database_connection(path) as connection:
        link = connection.execute(
            "SELECT * FROM alert_incident_links"
        ).fetchone()
    assert link is not None
    assert link["origin"] == "automatic"
    assert link["match_type"] in {
        "probable_match", "possible_match", "rejected_match"
    }
    assert link["confirmed_by_user"] == 0


def test_uncertain_incident_is_excluded_and_late_detection_is_labelled(
    tmp_path: Path,
):
    path = make_database(tmp_path)
    with database_connection(path) as connection:
        with connection:
            seeded = seed_window(connection, 0)
            alert = seed_alert(connection, *seeded, minutes=4)
            uncertain = create_incident(
                connection,
                device_id=DEVICE,
                values=incident_values(
                    start=BASE + timedelta(minutes=1),
                    verification="uncertain",
                ),
            )
            confirmed = create_incident(
                connection,
                device_id=DEVICE,
                values=incident_values(start=BASE + timedelta(minutes=2)),
            )
            create_feedback(
                connection,
                alert_id=alert,
                values=feedback_values("confirmed_related_issue", horizon=None),
            )
            upsert_alert_incident_link(
                connection,
                alert_id=alert,
                incident_id=confirmed["id"],
                match_type="confirmed_match",
                origin="manual",
                confirmed_by_user=True,
                matching_score=1,
                time_difference_seconds=-120,
                category_compatible=True,
                matching_rule="explicit_user_link",
                supporting_evidence=["User confirmed timing."],
                contradictory_evidence=[],
                reason_codes=["explicit_user_classification"],
            )
            completed_period(connection, end=BASE + timedelta(minutes=5))
    result = evaluate_database(path, policy=TEST_POLICY)
    assert result["lead_times"][0]["lead_time_seconds"] == -120
    assert result["lead_times"][0]["timing_state"] == "late_detection"
    with database_connection(path) as connection:
        exclusion = connection.execute(
            """SELECT included, reason_codes_json
            FROM validation_inclusion_exclusion
            WHERE evaluation_run_id = ? AND evidence_type = 'incident'
            AND evidence_id = ?""",
            (result["id"], uncertain["id"]),
        ).fetchone()
    assert exclusion["included"] == 0
    assert "incident_not_verified" in exclusion["reason_codes_json"]


def test_evaluation_is_idempotent_and_reconstructable(tmp_path: Path):
    path = make_database(tmp_path)
    with database_connection(path) as connection:
        with connection:
            seed_window(connection, 0)
            completed_period(connection, end=BASE + timedelta(minutes=5))
    first = evaluate_database(path, policy=TEST_POLICY)
    second = evaluate_database(path, policy=TEST_POLICY)
    with database_connection(path) as connection:
        run_count = connection.execute(
            "SELECT COUNT(*) FROM validation_evaluation_runs"
        ).fetchone()[0]
        accuracy = connection.execute(
            """SELECT * FROM validation_metric_results
            WHERE evaluation_run_id = ? AND scope_type = 'overall'
            AND metric_name = 'accuracy'""",
            (first["id"],),
        ).fetchone()
    assert first["id"] == second["id"]
    assert run_count == 1
    reconstructed = accuracy["numerator"] / accuracy["denominator"]
    assert reconstructed == accuracy["metric_value"] == 1


def test_validation_transaction_rolls_back_on_metric_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = make_database(tmp_path)
    with database_connection(path) as connection:
        with connection:
            seed_window(connection, 0)
            completed_period(connection, end=BASE + timedelta(minutes=5))
    import analytics.validation as module

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic insertion failure")

    monkeypatch.setattr(module, "_insert_metric", fail)
    with pytest.raises(RuntimeError):
        evaluate_database(path, policy=TEST_POLICY)
    with database_connection(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM validation_evaluation_runs"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM validation_inclusion_exclusion"
        ).fetchone()[0] == 0


def test_api_gets_are_read_only_and_writes_require_confirmation(tmp_path: Path):
    path = make_database(tmp_path)
    app = create_app(path)
    with TestClient(app) as client:
        before = sqlite3.connect(path).execute(
            "SELECT COUNT(*) FROM validation_evaluation_runs"
        ).fetchone()[0]
        assert client.get("/api/validation/status").status_code == 200
        assert client.get("/api/validation/summary").status_code == 200
        assert client.get("/api/validation/history").status_code == 200
        assert client.get("/api/validation/metrics").status_code == 200
        assert client.get("/api/validation/lead-times").status_code == 200
        assert client.get("/api/validation/feedback").status_code == 200
        assert client.get("/api/incidents/history").status_code == 200
        assert client.get("/api/incidents").status_code == 200
        assert client.get("/api/validation/observation-periods").status_code == 200
        assert client.get("/api/validation/periods").status_code == 200
        after = sqlite3.connect(path).execute(
            "SELECT COUNT(*) FROM validation_evaluation_runs"
        ).fetchone()[0]
        assert before == after == 0
        payload = {
            "device_id": DEVICE,
            "category": "application_failure",
            "severity": "moderate",
            "start_utc": BASE.isoformat(),
            "timestamp_precision": "exact",
            "verification_status": "user_reported",
            "symptoms_text": "The application closed unexpectedly.",
            "confirmation": False,
        }
        assert client.post("/api/incidents", json=payload).status_code == 422
        payload["confirmation"] = True
        response = client.post("/api/incidents", json=payload)
        assert response.status_code == 200
        incident_id = response.json()["incident"]["id"]
        assert client.get(f"/api/incidents/{incident_id}").status_code == 200
        assert client.get("/api/status").status_code == 200
        assert client.get("/api/metrics/latest").status_code == 200
        assert client.get("/api/alerts/status").status_code == 200
        assert INTERPRETATION in client.get("/openapi.json").json()["info"]["description"]


def test_status_reports_insufficient_evidence_without_fake_zero(tmp_path: Path):
    path = make_database(tmp_path)
    result = evaluate_database(path)
    status = validation_status(path)
    assert result["status"] == "insufficient_labeled_evidence"
    assert status["status"] == "insufficient_labeled_evidence"
    overall = [
        item for item in result["metrics"] if item["scope_type"] == "overall"
    ]
    headline = [
        item for item in overall if item["metric_name"] in {
            "precision", "recall", "accuracy", "balanced_accuracy",
            "mean_warning_lead_time_seconds",
        }
    ]
    assert all(item["metric_value"] is None for item in headline)
    assert all(
        item["evaluation_state"] == "insufficient_labeled_evidence"
        for item in headline
    )


def test_incident_api_filters_sorting_pagination_and_timestamp_validation(
    tmp_path: Path,
):
    path = make_database(tmp_path)
    with TestClient(create_app(path)) as client:
        for index, category in enumerate(
            ("application_failure", "system_crash")
        ):
            response = client.post(
                "/api/incidents",
                json={
                    "device_id": DEVICE,
                    "category": category,
                    "severity": "minor" if index == 0 else "critical",
                    "start_utc": (BASE + timedelta(hours=index)).isoformat(),
                    "timestamp_precision": "exact",
                    "verification_status": "user_reported",
                    "symptoms_text": "A deliberately entered synthetic API test.",
                    "data_confidence": 0.9,
                    "confirmation": True,
                },
            )
            assert response.status_code == 200
        filtered = client.get(
            "/api/incidents",
            params={
                "limit": 1,
                "offset": 0,
                "sort": "oldest",
                "category": "application_failure",
                "severity": "minor",
                "minimum_confidence": 0.8,
            },
        )
        assert filtered.status_code == 200
        assert filtered.json()["total"] == 1
        assert filtered.json()["items"][0]["category"] == "application_failure"
        invalid = client.post(
            "/api/incidents",
            json={
                "device_id": DEVICE,
                "category": "system_freeze",
                "severity": "serious",
                "start_utc": BASE.isoformat(),
                "end_utc": (BASE - timedelta(minutes=1)).isoformat(),
                "timestamp_precision": "exact",
                "verification_status": "user_reported",
                "symptoms_text": "Invalid timestamp test.",
                "confirmation": True,
            },
        )
        assert invalid.status_code == 422


def test_feedback_api_revision_preserves_alert_lifecycle(tmp_path: Path):
    path = make_database(tmp_path)
    with database_connection(path) as connection:
        with connection:
            seeded = seed_window(connection, 0)
            alert_id = seed_alert(connection, *seeded, minutes=1)
    with TestClient(create_app(path)) as client:
        payload = {
            "outcome": "not_yet_verified",
            "verification_status": "uncertain",
            "notes": "User-entered synthetic test feedback.",
            "condition_state": "unclear",
            "confirmation": True,
        }
        created = client.post(
            f"/api/alerts/{alert_id}/feedback", json=payload
        )
        assert created.status_code == 200
        revised = client.post(
            f"/api/alerts/{alert_id}/feedback/revise",
            json={
                **payload,
                "outcome": "no_issue_observed",
                "verification_status": "user_reported",
                "observation_horizon_seconds": 90_000,
                "condition_state": "recovered",
                "revision_reason": "Observation horizon completed.",
            },
        )
        assert revised.status_code == 200
        assert revised.json()["feedback"]["current_revision_number"] == 2
        current = client.get(f"/api/alerts/{alert_id}/feedback").json()
        assert current["feedback"]["outcome"] == "no_issue_observed"
    with database_connection(path) as connection:
        alert_state = connection.execute(
            "SELECT state FROM alerts WHERE id = ?", (alert_id,)
        ).fetchone()["state"]
        revisions = connection.execute(
            "SELECT COUNT(*) FROM alert_feedback_revisions"
        ).fetchone()[0]
    assert alert_state == "open"
    assert revisions == 2
