import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

import agent.events as events
from agent.events import (
    ChannelPollResult,
    categorize_event,
    is_relevant_event,
    parse_event_xml,
)
from analytics.aggregate import aggregate_database, percentile, window_bounds
from analytics.workload import (
    WORKLOAD_RULE_VERSION,
    classify_window_composition,
    classify_workload,
)
from backend.database import database_connection, initialize_database
from backend.main import create_app
from backend.phase2b_repository import (
    get_event_checkpoint,
    get_events,
    store_event_poll,
)
from backend.repository import insert_metric
from tests.test_database import sample


def test_workload_rules_distinguish_context_from_failure():
    idle_background = classify_workload({
        "user_state": "idle", "user_idle_seconds": 600, "cpu_percent": 20,
        "disk_read_bytes_per_second": 200_000,
        "disk_write_bytes_per_second": 0,
        "network_upload_bytes_per_second": 0,
        "network_download_bytes_per_second": 0,
    })
    assert idle_background.workload_class == "idle"
    assert idle_background.user_activity_state == "idle"
    assert idle_background.system_activity_state == "background"
    assert idle_background.rule_version == WORKLOAD_RULE_VERSION
    quiescent = classify_workload({
        "user_state": "idle", "user_idle_seconds": 600, "cpu_percent": 2,
        "disk_read_bytes_per_second": 0,
        "disk_write_bytes_per_second": 0,
        "network_upload_bytes_per_second": 0,
        "network_download_bytes_per_second": 0,
    })
    assert quiescent.workload_class == "idle"
    assert quiescent.system_activity_state == "quiescent"
    assert classify_workload({
        "user_state": "active", "foreground_process_name": "Code.exe",
    }).workload_class == "development"
    assert classify_workload({
        "user_state": "active", "foreground_process_name": "Brave.exe",
    }).workload_class == "browser_or_media"
    explicit_game = classify_workload({
        "user_state": "active",
        "foreground_process_name": "VALORANT-Win64-Shipping.exe",
        "gpu_utilization_percent": None,
    })
    assert explicit_game.workload_class == "gaming_or_3d"
    assert "explicit_gaming_foreground" in explicit_game.reason_codes
    gaming = classify_workload({
        "user_state": "active", "cpu_percent": 70,
        "gpu_utilization_percent": 80,
    })
    assert gaming.workload_class == "gaming_or_3d"
    assert "failure" not in gaming.reason_codes
    monitoring_overhead = classify_workload({
        "user_state": "idle", "user_idle_seconds": 600, "cpu_percent": 70,
        "disk_read_bytes_per_second": 500_000,
        "disk_write_bytes_per_second": 500_000,
        "network_upload_bytes_per_second": 0,
        "network_download_bytes_per_second": 0,
    })
    assert monitoring_overhead.workload_class == "idle"
    assert monitoring_overhead.system_activity_state == "background"
    unknown_game = classify_workload({
        "user_state": "active", "foreground_process_name": "UnmappedGame.exe",
    })
    assert unknown_game.workload_class == "interactive_light"
    assert "foreground_not_in_catalogue" in unknown_game.reason_codes
    missing = classify_workload({
        "user_state": "active", "foreground_process_name": None,
        "gpu_utilization_percent": None,
    })
    assert missing.workload_class == "unknown"
    assert missing.confidence < 0.5


def test_guided_development_rules_preserve_pure_and_insufficient_contexts():
    pure_development = classify_window_composition(
        ["development"] * 10,
        expected_sample_count=10,
        is_complete=True,
    )
    assert pure_development["secondary_context"] is None
    assert pure_development["profiles"]["development"]["proportion"] == 1.0

    browser_only = classify_window_composition(
        ["browser_or_media"] * 10,
        expected_sample_count=10,
        is_complete=True,
    )
    assert browser_only["secondary_context"] is None

    guided = classify_window_composition(
        ["development", "browser_or_media"] * 5,
        expected_sample_count=10,
        is_complete=True,
    )
    assert guided["secondary_context"] == "guided_development"
    assert guided["profiles"]["development"] == {
        "count": 5,
        "proportion": 0.5,
    }

    insufficient = classify_window_composition(
        ["development"] + ["browser_or_media"] * 9,
        expected_sample_count=10,
        is_complete=True,
    )
    assert insufficient["secondary_context"] is None


def test_background_code_snapshot_does_not_override_brave_foreground():
    result = classify_workload({
        "user_state": "active",
        "foreground_process_name": "Brave.exe",
        "process_snapshots": {
            "cpu": [{"pid": 10, "process_name": "Code.exe"}],
        },
    })
    assert result.workload_class == "browser_or_media"


def test_event_mapping_xml_safety_and_permission_failure(monkeypatch):
    expected_categories = (
        ("System", "Microsoft-Windows-Kernel-Power", 41, "power"),
        ("System", "Microsoft-Windows-WHEA-Logger", 18, "hardware"),
        ("System", "Disk", 7, "storage"),
        ("Application", "Application Error", 1000, "application_crash"),
        ("System", "Service Control Manager", 7031, "service_failure"),
        (
            "System",
            "Microsoft-Windows-Resource-Exhaustion-Detector",
            2004,
            "resource_exhaustion",
        ),
    )
    for channel, provider, event_id, expected in expected_categories:
        category, summary = categorize_event(channel, provider, event_id)
        assert category == expected
        assert "private-path" not in summary

    xml = """<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
    <System><Provider Name="Microsoft-Windows-WHEA-Logger"/><EventID>18</EventID>
    <Level>2</Level><TimeCreated SystemTime="2026-01-01T00:00:00.000Z"/>
    <EventRecordID>42</EventRecordID><Channel>System</Channel></System>
    <EventData><Data>C:\\Users\\private-path\\secret.docx</Data></EventData></Event>"""
    parsed = parse_event_xml(xml, "device", "2026-01-01T00:01:00+00:00")
    assert parsed["smartops_category"] == "hardware"
    assert parsed["event_timestamp_utc"] == "2026-01-01T00:00:00+00:00"
    assert "secret" not in parsed["safe_summary"]
    assert set(parsed).isdisjoint({"raw_xml", "message"})

    monkeypatch.setattr(events.sys, "platform", "win32")
    monkeypatch.setattr(events.subprocess, "run", lambda *_a, **_k: (_ for _ in ()).throw(PermissionError()))
    result = events.poll_channel("System", "device", 10)
    assert result.available is False
    assert result.status == "unavailable_or_permission_restricted"

    assert is_relevant_event({
        "event_level": "Warning",
        "smartops_category": "system_warning",
        "provider_name": "Unrelated Application Provider",
    }) is False
    assert is_relevant_event({
        "event_level": "Warning",
        "smartops_category": "system_warning",
        "provider_name": "Microsoft-Windows-Kernel-General",
    }) is True
    assert is_relevant_event({
        "event_level": "Error",
        "smartops_category": "application_warning",
        "provider_name": "Any Provider",
    }) is True


def test_event_poll_resets_checkpoint_after_log_rollover(monkeypatch):
    event_xml = """<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
    <System><Provider Name="Disk"/><EventID>7</EventID><Level>3</Level>
    <TimeCreated SystemTime="2026-01-01T00:00:00Z"/>
    <EventRecordID>2</EventRecordID><Channel>System</Channel></System></Event>"""
    monkeypatch.setattr(events.sys, "platform", "win32")
    monkeypatch.setattr(
        events.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=event_xml),
    )

    result = events.poll_channel("System", "device", last_record_id=100)

    assert result.available is True
    assert result.newest_record_id == 2
    assert [event["record_id"] for event in result.events] == [2]


def test_event_deduplication_and_checkpoint(tmp_path: Path):
    database_path = tmp_path / "events.db"
    initialize_database(database_path)
    event = {
        "device_id": "device", "event_timestamp_utc": "2026-01-01T00:01:00+00:00",
        "channel": "System", "provider_name": "Disk", "event_id": 7,
        "record_id": 100, "event_level": "Error",
        "smartops_category": "storage", "safe_summary": "Safe mapped summary.",
        "collected_at_utc": "2026-01-01T00:02:00+00:00",
    }
    result = ChannelPollResult("System", True, "available", [event], 100)
    with database_connection(database_path) as connection:
        assert store_event_poll(connection, result) == 1
        assert store_event_poll(connection, result) == 0
        assert get_event_checkpoint(connection, "System") == 100
        stored, total = get_events(connection, 10, 0, category="storage")
    assert total == 1
    assert stored[0]["safe_summary"] == "Safe mapped summary."


def test_five_minute_features_are_statistical_partial_and_idempotent(tmp_path: Path):
    database_path = tmp_path / "features.db"
    initialize_database(database_path)
    start, end = window_bounds(datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc))
    assert start.minute == 0 and end.minute == 5
    assert percentile([0.0, 100.0], 0.95) == 95.0

    with database_connection(database_path) as connection:
        for index, cpu in enumerate((0.0, 100.0)):
            row = sample(f"2026-01-01T00:0{index}:00+00:00")
            row.update({
                "cpu_percent": cpu, "ram_percent": 50.0 + index,
                "cpu_temperature_celsius": None,
                "gpu_utilization_percent": None,
                "workload_class": "development",
                "workload_confidence": 0.9,
                "workload_reasons": ["development_foreground"],
            })
            insert_metric(connection, row)
        store_event_poll(connection, ChannelPollResult(
            "System", True, "available", [{
                "device_id": "test-device",
                "event_timestamp_utc": "2026-01-01T00:02:00+00:00",
                "channel": "System", "provider_name": "WHEA", "event_id": 18,
                "record_id": 1, "event_level": "Error",
                "smartops_category": "hardware", "safe_summary": "Mapped hardware event.",
                "collected_at_utc": "2026-01-01T00:03:00+00:00",
            }], 1))

    assert aggregate_database(database_path, include_partial=True) == 1
    assert aggregate_database(database_path, include_partial=True) == 1
    with database_connection(database_path) as connection:
        feature = connection.execute("SELECT * FROM feature_windows").fetchone()
        count = connection.execute("SELECT COUNT(*) FROM feature_windows").fetchone()[0]
    assert count == 1
    assert feature["sample_count"] == 2
    assert feature["is_complete"] == 0
    assert feature["coverage_ratio"] == 0.2
    assert feature["cpu_avg"] == 50.0
    assert feature["cpu_stddev"] == 50.0
    assert feature["cpu_p95"] == 95.0
    assert feature["cpu_temperature_missing_ratio"] == 1.0
    assert feature["gpu_utilization_missing_ratio"] == 1.0
    assert feature["gpu_memory_missing_ratio"] == 1.0
    assert feature["gpu_temperature_missing_ratio"] == 1.0
    assert feature["hardware_event_count"] == 1


def test_workload_majority_is_transparent_when_development_loses(tmp_path: Path):
    database_path = tmp_path / "workload-majority.db"
    initialize_database(database_path)
    with database_connection(database_path) as connection:
        for index in range(10):
            row = sample(
                (datetime(2026, 1, 1, tzinfo=timezone.utc)
                 + timedelta(seconds=30 * index)).isoformat()
            )
            workload = "development" if index < 4 else "browser_or_media"
            row.update({
                "workload_class": workload,
                "workload_confidence": 0.9,
                "workload_reasons": [f"{workload}_foreground"],
                "user_activity_state": "active",
                "system_activity_state": "background",
                "workload_rule_version": WORKLOAD_RULE_VERSION,
                "workload_provenance": {"rule_version": WORKLOAD_RULE_VERSION},
            })
            insert_metric(connection, row)
    aggregate_database(database_path, include_partial=True)
    with database_connection(database_path) as connection:
        feature = connection.execute("SELECT * FROM feature_windows").fetchone()
    assert feature["dominant_workload_class"] == "browser_or_media"
    assert feature["dominant_user_activity_state"] == "active"
    assert feature["dominant_system_activity_state"] == "background"
    assert feature["secondary_workload_context"] == "guided_development"
    composition = json.loads(feature["workload_composition_json"])
    assert composition["profiles"]["development"]["count"] == 4
    assert composition["profiles"]["browser_or_media"]["proportion"] == 0.6
    assert "Development was observed in 4 of 10 samples" in (
        feature["workload_majority_explanation"] or ""
    )
    with TestClient(create_app(database_path)) as client:
        api_feature = client.get("/api/features/latest").json()
    assert api_feature["secondary_workload_context"] == "guided_development"
    assert api_feature["workload_composition"]["profiles"]["development"][
        "count"
    ] == 4


def test_historical_rule_windows_are_not_reinterpreted(tmp_path: Path):
    database_path = tmp_path / "historical-workload.db"
    initialize_database(database_path)
    with database_connection(database_path) as connection:
        for index, workload in enumerate(
            ["development"] * 5 + ["browser_or_media"] * 5
        ):
            row = sample(
                (datetime(2026, 1, 1, tzinfo=timezone.utc)
                 + timedelta(seconds=30 * index)).isoformat()
            )
            row.update({
                "workload_class": workload,
                "workload_confidence": 0.9,
                "workload_reasons": [],
                "user_activity_state": "active",
                "system_activity_state": "background",
                "workload_rule_version": "phase7b1-workload-v2",
                "workload_provenance": {"rule_version": "phase7b1-workload-v2"},
            })
            insert_metric(connection, row)
    aggregate_database(database_path, include_partial=True)
    with database_connection(database_path) as connection:
        feature = connection.execute("SELECT * FROM feature_windows").fetchone()
    assert feature["dominant_workload_class"] == "development"
    assert feature["workload_composition_json"] is None
    assert feature["secondary_workload_context"] is None


def test_phase2b_api_filters_paginates_and_preserves_old_endpoints(tmp_path: Path):
    database_path = tmp_path / "api.db"
    initialize_database(database_path)
    row = sample("2026-01-01T00:01:00+00:00")
    row.update({
        "workload_class": "development", "workload_confidence": 0.9,
        "workload_reasons": ["development_foreground"],
    })
    with database_connection(database_path) as connection:
        insert_metric(connection, row)
        for record_id, level in ((1, "Error"), (2, "Warning")):
            store_event_poll(connection, ChannelPollResult(
                "Application", True, "available", [{
                    "device_id": "test-device",
                    "event_timestamp_utc": f"2026-01-01T00:0{record_id}:00+00:00",
                    "channel": "Application", "provider_name": "Application Error",
                    "event_id": 1000, "record_id": record_id, "event_level": level,
                    "smartops_category": "application_crash",
                    "safe_summary": "Mapped application crash.",
                    "collected_at_utc": "2026-01-01T00:03:00+00:00",
                }], record_id))
    aggregate_database(database_path, include_partial=True)

    with TestClient(create_app(database_path)) as client:
        assert client.get("/api/status").status_code == 200
        assert client.get("/api/metrics/latest").status_code == 200
        event_page = client.get("/api/events", params={
            "limit": 1, "offset": 1, "category": "application_crash",
        }).json()
        assert event_page["total"] == 2 and len(event_page["items"]) == 1
        assert client.get("/api/events/summary").status_code == 200
        assert client.get("/api/workload/latest").json()["workload_class"] == "development"
        assert client.get("/api/workload/history?limit=1").json()["total"] == 1
        assert client.get("/api/features/latest").status_code == 200
        assert client.get("/api/features/history?limit=1").json()["total"] == 1
        assert client.get("/api/features/count").json() == {"count": 1}
        assert client.get(
            "/api/features/count",
            params={
                "start": "2026-02-01T00:00:00Z",
                "end": "2026-03-01T00:00:00Z",
            },
        ).json() == {"count": 0}
