"""Controlled Phase 7A/7B tests; no native toast is sent by this module."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.notifications import (
    NotificationDispatcher,
    NotificationMessage,
    NotificationResult,
    UnsupportedNotificationProvider,
    build_alert_deep_link,
    build_enabled_confirmation_message,
    build_test_message,
)
from backend.database import database_connection, initialize_database
from backend.main import create_app
from backend.phase7a_repository import ensure_notification_preferences
from backend.phase7a_repository import update_notification_preferences


class RecordingProvider:
    provider_name = "test-provider"

    def __init__(self, *, succeeds: bool = True) -> None:
        self.succeeds = succeeds
        self.messages: list[NotificationMessage] = []

    def support_status(self) -> dict[str, str | bool | None]:
        return {
            "supported": True,
            "status": "supported",
            "provider_name": self.provider_name,
            "reason": None,
        }

    def send(self, message: NotificationMessage) -> NotificationResult:
        self.messages.append(message)
        return NotificationResult(
            self.succeeds,
            self.provider_name,
            None if self.succeeds else "controlled provider failure",
        )


def make_database(tmp_path: Path) -> Path:
    path = tmp_path / "phase7a.db"
    initialize_database(path)
    with database_connection(path) as connection:
        with connection:
            ensure_notification_preferences(connection)
    return path


def seed_alert(
    path: Path,
    *,
    severity: str = "warning",
    state: str = "open",
    created_at: datetime | None = None,
    suffix: str = "one",
) -> int:
    now = created_at or (datetime.now(timezone.utc) + timedelta(seconds=1))
    start = now - timedelta(minutes=5)
    with database_connection(path) as connection:
        with connection:
            window_id = int(
                connection.execute(
                    """INSERT INTO feature_windows (
                    device_id, window_start_utc, window_end_utc, sample_count,
                    expected_sample_count, coverage_ratio, is_complete,
                    dominant_workload_class, workload_confidence,
                    missing_indicators_json, updated_at_utc
                    ) VALUES ('notification-device', ?, ?, 10, 10, 1, 1,
                    'idle', 0.9, '[]', ?)""",
                    (start.isoformat(), now.isoformat(), now.isoformat()),
                ).lastrowid
            )
            health_id = int(
                connection.execute(
                    """INSERT INTO health_assessments (
                    feature_window_id, device_id, window_start_utc, window_end_utc,
                    assessed_at_utc, system_health_score, health_band,
                    evaluation_state, data_confidence, coverage_ratio,
                    workload_context, workload_confidence,
                    available_component_weight, excluded_component_weight,
                    normalization_method, algorithm_version,
                    configuration_version, reason_codes_json,
                    consecutive_window_count, persistence_duration_seconds,
                    trend_direction, recovery_state, feature_signature,
                    feature_updated_at_utc, created_at_utc, updated_at_utc
                    ) VALUES (?, 'notification-device', ?, ?, ?, 60, 'attention',
                    'provisional', 0.8, 1, 'idle', 0.9, 1, 0,
                    'available_weight_normalization', 'health-v1', 'health-c1',
                    '[]', 2, 600, 'stable', 'not_recovering', ?, ?, ?, ?)""",
                    (
                        window_id,
                        start.isoformat(),
                        now.isoformat(),
                        now.isoformat(),
                        f"notification-{suffix}",
                        now.isoformat(),
                        now.isoformat(),
                        now.isoformat(),
                    ),
                ).lastrowid
            )
            return int(
                connection.execute(
                    """INSERT INTO alerts (
                    device_id, alert_fingerprint, lifecycle_number, alert_code,
                    category, title, description, evidence_domain,
                    probable_factor, current_severity, peak_severity, state,
                    evaluation_state, data_confidence, workload_context,
                    workload_confidence, first_observed_utc, latest_observed_utc,
                    last_evidence_utc, occurrence_count, consecutive_window_count,
                    duration_seconds, trend_direction, recovery_window_count,
                    recovery_state, health_assessment_id,
                    source_feature_window_id, probable_factors_json,
                    contradictory_evidence_json, excluded_inputs_json,
                    reason_codes_json, algorithm_version,
                    configuration_version, catalogue_version, created_at_utc,
                    updated_at_utc
                    ) VALUES (
                    'notification-device', ?, 1, 'NOTIFY_TEST',
                    'system_stability', 'Controlled alert', 'Controlled alert.',
                    'stability', 'repeated stability evidence', ?, ?, ?,
                    'evaluated', 0.9, 'idle', 0.9, ?, ?, ?, 1, 2, 600,
                    'stable', 0, 'not_recovering', ?, ?, '[]', '[]', '[]',
                    '[]', 'alerts-v1', 'alerts-c1', 'catalogue-v1', ?, ?
                    )""",
                    (
                        f"notify-{suffix}",
                        severity,
                        severity,
                        state,
                        now.isoformat(),
                        now.isoformat(),
                        now.isoformat(),
                        health_id,
                        window_id,
                        now.isoformat(),
                        now.isoformat(),
                    ),
                ).lastrowid
            )


def deliveries(path: Path) -> list[sqlite3.Row]:
    with database_connection(path) as connection:
        return connection.execute(
            "SELECT * FROM notification_deliveries ORDER BY id"
        ).fetchall()


@pytest.mark.parametrize("severity", ["warning", "urgent"])
def test_high_and_critical_semantic_severities_notify(
    tmp_path: Path,
    severity: str,
):
    path = make_database(tmp_path)
    alert_id = seed_alert(path, severity=severity, suffix=severity)
    provider = RecordingProvider()

    result = NotificationDispatcher(path, provider=provider).dispatch_pending()

    assert result.delivered == 1
    assert len(provider.messages) == 1
    assert f"alertId={alert_id}" in provider.messages[0].deep_link_url


@pytest.mark.parametrize("severity", ["informational", "advisory"])
def test_lower_severities_are_excluded_by_default(tmp_path: Path, severity: str):
    path = make_database(tmp_path)
    seed_alert(path, severity=severity, suffix=severity)
    provider = RecordingProvider()
    result = NotificationDispatcher(path, provider=provider).dispatch_pending()
    assert result.considered == 0
    assert provider.messages == []
    assert deliveries(path) == []


def test_elevated_can_be_deliberately_enabled_in_local_preferences(tmp_path: Path):
    path = make_database(tmp_path)
    with database_connection(path) as connection:
        with connection:
            update_notification_preferences(
                connection,
                enabled=True,
                eligible_severities=["warning", "urgent", "advisory"],
            )
    seed_alert(path, severity="advisory")
    provider = RecordingProvider()
    result = NotificationDispatcher(path, provider=provider).dispatch_pending()
    assert result.delivered == 1


@pytest.mark.parametrize(
    ("categories", "severity", "should_deliver"),
    [
        (["advisory"], "informational", True),
        (["advisory"], "advisory", True),
        (["advisory"], "warning", False),
        (["warning"], "warning", True),
        (["warning"], "urgent", False),
        (["urgent"], "urgent", True),
        (["urgent"], "warning", False),
        (["warning", "urgent"], "warning", True),
        (["warning", "urgent"], "urgent", True),
        ([], "urgent", False),
        (["urgent"], "high", False),
        (["urgent"], "URGENT", False),
    ],
)
def test_notification_category_matrix_is_exact_and_fail_safe(
    tmp_path: Path,
    categories: list[str],
    severity: str,
    should_deliver: bool,
):
    path = make_database(tmp_path)
    with database_connection(path) as connection:
        with connection:
            update_notification_preferences(
                connection,
                enabled=True,
                eligible_categories=categories,
            )
    seed_alert(path, severity=severity, suffix=f"{severity}-{len(categories)}")
    provider = RecordingProvider()
    result = NotificationDispatcher(path, provider=provider).dispatch_pending()
    assert result.delivered == int(should_deliver)
    assert len(provider.messages) == int(should_deliver)
    assert len(deliveries(path)) == int(should_deliver)
    with database_connection(path) as connection:
        decisions = connection.execute(
            "SELECT decision_reason FROM notification_decisions"
        ).fetchall()
    if not should_deliver:
        assert decisions[0]["decision_reason"] in {
            "suppressed_by_preference",
            "unknown_alert_severity",
        }


def test_urgent_only_sends_once_after_meaningful_warning_escalation(
    tmp_path: Path,
):
    path = make_database(tmp_path)
    with database_connection(path) as connection:
        with connection:
            update_notification_preferences(
                connection, enabled=True, eligible_categories=["urgent"]
            )
    alert_id = seed_alert(path, severity="warning", suffix="urgent-only")
    provider = RecordingProvider()
    dispatcher = NotificationDispatcher(path, provider=provider)
    assert dispatcher.dispatch_pending().delivered == 0
    changed = datetime.now(timezone.utc) + timedelta(seconds=2)
    with database_connection(path) as connection:
        with connection:
            connection.execute(
                """UPDATE alerts SET current_severity = 'urgent',
                peak_severity = 'urgent', updated_at_utc = ? WHERE id = ?""",
                (changed.isoformat(), alert_id),
            )
            connection.execute(
                """INSERT INTO alert_state_transitions (
                alert_id, transition_timestamp_utc, previous_state, new_state,
                previous_severity, new_severity, transition_type, reason_code,
                metadata_json
                ) VALUES (?, ?, 'open', 'open', 'warning', 'urgent',
                'severity_escalated', 'controlled_escalation', '{}')""",
                (alert_id, changed.isoformat()),
            )
    assert dispatcher.dispatch_pending().delivered == 1
    assert dispatcher.dispatch_pending().delivered == 0
    assert [row["notification_type"] for row in deliveries(path)] == ["escalation"]


def test_disabled_notifications_send_nothing(tmp_path: Path):
    path = make_database(tmp_path)
    with database_connection(path) as connection:
        with connection:
            connection.execute(
                "UPDATE notification_preferences SET enabled = 0 WHERE id = 1"
            )
    seed_alert(path)
    provider = RecordingProvider()
    result = NotificationDispatcher(path, provider=provider).dispatch_pending()
    assert result.disabled is True
    assert provider.messages == []


def test_activation_is_delivered_once_and_unchanged_cycle_is_deduplicated(
    tmp_path: Path,
):
    path = make_database(tmp_path)
    seed_alert(path)
    provider = RecordingProvider()
    dispatcher = NotificationDispatcher(path, provider=provider)
    first = dispatcher.dispatch_pending()
    second = NotificationDispatcher(path, provider=provider).dispatch_pending()
    assert first.delivered == 1
    assert second.delivered == 0
    assert second.deduplicated == 1
    assert len(provider.messages) == 1
    assert len(deliveries(path)) == 1


def test_meaningful_escalation_creates_one_new_notification(tmp_path: Path):
    path = make_database(tmp_path)
    alert_id = seed_alert(path, severity="warning")
    provider = RecordingProvider()
    dispatcher = NotificationDispatcher(path, provider=provider)
    dispatcher.dispatch_pending()
    changed = datetime.now(timezone.utc) + timedelta(seconds=2)
    with database_connection(path) as connection:
        with connection:
            connection.execute(
                """UPDATE alerts SET current_severity = 'urgent',
                peak_severity = 'urgent', updated_at_utc = ? WHERE id = ?""",
                (changed.isoformat(), alert_id),
            )
            connection.execute(
                """INSERT INTO alert_state_transitions (
                alert_id, transition_timestamp_utc, previous_state, new_state,
                previous_severity, new_severity, transition_type, reason_code,
                metadata_json
                ) VALUES (?, ?, 'open', 'open', 'warning', 'urgent',
                'severity_escalated', 'controlled_escalation', '{}')""",
                (alert_id, changed.isoformat()),
            )
    result = dispatcher.dispatch_pending()
    dispatcher.dispatch_pending()
    assert result.delivered == 1
    assert len(provider.messages) == 2
    assert [row["notification_type"] for row in deliveries(path)] == [
        "activation",
        "escalation",
    ]


def test_resolved_and_historical_alerts_do_not_notify(tmp_path: Path):
    path = make_database(tmp_path)
    seed_alert(path, state="resolved", suffix="resolved")
    seed_alert(
        path,
        created_at=datetime.now(timezone.utc) - timedelta(days=1),
        suffix="historical",
    )
    provider = RecordingProvider()
    result = NotificationDispatcher(path, provider=provider).dispatch_pending()
    assert result.considered == 0
    assert provider.messages == []


def test_provider_failure_is_recorded_and_not_retried(tmp_path: Path):
    path = make_database(tmp_path)
    seed_alert(path)
    provider = RecordingProvider(succeeds=False)
    dispatcher = NotificationDispatcher(path, provider=provider)
    first = dispatcher.dispatch_pending()
    second = dispatcher.dispatch_pending()
    rows = deliveries(path)
    assert first.failed == 1
    assert second.delivered == 0
    assert len(provider.messages) == 1
    assert rows[0]["delivery_status"] == "failed"
    assert rows[0]["failure_reason"] == "controlled provider failure"


def test_deep_link_is_exact_and_rejects_injection_values():
    assert build_alert_deep_link(27) == (
        "http://localhost:5173/#/predictive-alerts?alertId=27"
    )
    for invalid in (0, -1, True, "1&route=evil"):
        with pytest.raises(ValueError):
            build_alert_deep_link(invalid)  # type: ignore[arg-type]


def test_notification_api_preferences_status_and_test_are_separate(
    tmp_path: Path,
):
    path = tmp_path / "api.db"
    provider = RecordingProvider()
    with TestClient(create_app(path, notification_provider=provider)) as client:
        status = client.get("/api/notifications/status")
        disabled = client.put(
            "/api/notifications/preferences",
            json={"enabled": False},
        )
        no_test = client.post("/api/notifications/test")
        enabled = client.put(
            "/api/notifications/preferences",
            json={"enabled": True},
        )
        submitted = client.post("/api/notifications/test")
    assert status.status_code == 200
    assert status.json()["eligible_severities"] == ["warning", "urgent"]
    assert disabled.json()["preferences"]["enabled"] is False
    assert no_test.json()["status"] == "disabled"
    assert enabled.json()["preferences"]["enabled"] is True
    assert enabled.json()["message"] == "Notifications have been enabled."
    assert enabled.json()["confirmation_notification"]["attempted"] is True
    assert submitted.json()["test_only"] is True
    assert len(provider.messages) == 2
    assert provider.messages[0] == build_enabled_confirmation_message()
    assert provider.messages[1] == build_test_message()
    with database_connection(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM notification_deliveries"
        ).fetchone()[0] == 0


def test_enable_confirmation_occurs_only_once_for_off_to_on(tmp_path: Path):
    path = make_database(tmp_path)
    provider = RecordingProvider()
    with TestClient(create_app(path, notification_provider=provider)) as client:
        client.put("/api/notifications/preferences", json={"enabled": False})
        enabled = client.put(
            "/api/notifications/preferences",
            json={"enabled": True},
        )
        unchanged = client.put(
            "/api/notifications/preferences",
            json={"enabled": True},
        )
        refreshed = client.get("/api/notifications/status")
        disabled = client.put(
            "/api/notifications/preferences",
            json={"enabled": False},
        )

    assert enabled.json()["transition"] == "enabled"
    assert enabled.json()["message"] == "Notifications have been enabled."
    assert unchanged.json()["transition"] == "unchanged"
    assert unchanged.json()["confirmation_notification"]["attempted"] is False
    assert refreshed.status_code == 200
    assert disabled.json()["message"] == "Notifications have been disabled."
    assert len(provider.messages) == 1
    assert provider.messages[0].title == "SmartOps"
    assert provider.messages[0].body == "Notifications have been enabled."
    assert provider.messages[0].deep_link_url.endswith("/#/settings")


def test_empty_notification_category_policy_is_preserved_without_a_toast(
    tmp_path: Path,
):
    path = make_database(tmp_path)
    provider = RecordingProvider()
    with TestClient(create_app(path, notification_provider=provider)) as client:
        response = client.put(
            "/api/notifications/preferences",
            json={"enabled": True, "eligible_categories": []},
        )
    assert response.status_code == 200
    assert response.json()["preferences"]["eligible_categories"] == []
    assert provider.messages == []


def test_enable_confirmation_failure_keeps_preference_and_api_healthy(
    tmp_path: Path,
):
    path = make_database(tmp_path)
    provider = RecordingProvider(succeeds=False)
    with TestClient(create_app(path, notification_provider=provider)) as client:
        client.put("/api/notifications/preferences", json={"enabled": False})
        response = client.put(
            "/api/notifications/preferences",
            json={"enabled": True},
        )
        status = client.get("/api/notifications/status")

    assert response.status_code == 200
    assert response.json()["preferences"]["enabled"] is True
    assert response.json()["confirmation_notification"] == {
        "attempted": True,
        "delivered": False,
        "status": "failed",
        "user_message": (
            "Notifications are enabled, but Windows could not accept the "
            "confirmation notification."
        ),
    }
    assert status.json()["enabled"] is True


def test_settings_status_is_read_only_and_reports_current_schema(tmp_path: Path):
    path = make_database(tmp_path)
    provider = RecordingProvider()
    with TestClient(create_app(path, notification_provider=provider)) as client:
        response = client.get("/api/settings/status")
    body = response.json()
    assert response.status_code == 200
    assert body["schema_version"] == 18
    assert body["production_sampling_seconds"] == 30
    assert body["feature_window_minutes"] == 5
    assert body["processing_mode"] == "local_only"
    assert body["foreign_keys_enabled"] is True
    assert provider.messages == []


def test_notification_preference_survives_api_restart(tmp_path: Path):
    path = tmp_path / "persistent.db"
    provider = RecordingProvider()
    with TestClient(create_app(path, notification_provider=provider)) as client:
        client.put("/api/notifications/preferences", json={"enabled": False})
    with TestClient(create_app(path, notification_provider=provider)) as client:
        status = client.get("/api/notifications/status").json()
    assert status["enabled"] is False


def test_unsupported_provider_fails_safely():
    provider = UnsupportedNotificationProvider("controlled unsupported session")
    result = provider.send(NotificationMessage("title", "body", "http://localhost"))
    assert result.delivered is False
    assert provider.support_status()["status"] == "unsupported"


def test_schema9_migrates_to_current_schema_without_data_loss(tmp_path: Path):
    path = tmp_path / "schema9.db"
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """INSERT INTO metrics (timestamp_utc, device_id)
            VALUES ('2026-01-01T00:00:00+00:00', 'preserved')"""
        )
        connection.execute("DROP TABLE notification_deliveries")
        connection.execute("DROP TABLE notification_preferences")
        connection.execute("PRAGMA user_version = 9")
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 18
        assert connection.execute("SELECT COUNT(*) FROM metrics").fetchone()[0] == 1
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {"notification_preferences", "notification_deliveries"} <= tables


def test_notification_exception_cannot_interrupt_sample_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import agent.main as agent_main
    from tests.test_database import sample as metric_sample

    path = tmp_path / "agent.db"
    sample = metric_sample("2026-07-28T12:00:00+00:00")

    class Workload:
        workload_class = "idle"
        confidence = 0.9
        reason_codes: list[str] = []

    monkeypatch.setattr(agent_main, "get_or_create_device_id", lambda: "test-device")
    monkeypatch.setattr(
        agent_main,
        "collect_metrics",
        lambda *_args, **_kwargs: dict(sample),
    )
    monkeypatch.setattr(agent_main, "classify_workload", lambda _sample: Workload())
    monkeypatch.setattr(
        agent_main,
        "poll_channel",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("event test")),
    )
    for name in (
        "aggregate_database",
        "maybe_maintain_baselines",
        "maybe_evaluate_risk",
        "maybe_evaluate_health",
        "maybe_maintain_quality",
        "maybe_evaluate_alerts",
    ):
        monkeypatch.setattr(agent_main, name, lambda *_args: None)
    monkeypatch.setattr(
        agent_main,
        "dispatch_pending_notifications",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("toast test")),
    )
    monkeypatch.setattr(
        agent_main,
        "collect_enhanced_evidence",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("enhanced collector test")
        ),
    )

    stored = agent_main.collect_and_store(path)
    assert stored["timestamp_utc"] == sample["timestamp_utc"]
    with database_connection(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM metrics").fetchone()[0] == 1
