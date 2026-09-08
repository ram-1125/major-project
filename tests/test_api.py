from pathlib import Path

from fastapi.testclient import TestClient

from backend.database import database_connection, initialize_database
from backend.main import create_app
from backend.repository import insert_metric
from tests.test_database import sample


def test_phase_one_endpoints_remain_available_with_empty_database(tmp_path: Path):
    app = create_app(tmp_path / "smartops.db")

    with TestClient(app) as client:
        status_response = client.get("/api/status")
        latest_response = client.get("/api/metrics/latest")
        history_response = client.get("/api/metrics/history")

    assert status_response.status_code == 200
    assert status_response.json() == {"status": "ok", "database": "connected"}
    assert latest_response.status_code == 200
    assert latest_response.json() is None
    assert history_response.status_code == 200
    assert history_response.json()["items"] == []
    assert history_response.json()["total"] == 0


def test_technical_evidence_catalogue_is_read_only_bounded_and_keeps_audit_records_in_settings(tmp_path: Path):
    database_path = tmp_path / "technical.db"
    with TestClient(create_app(database_path)) as client:
        catalogue = client.get("/api/technical-evidence")
        page = client.get("/api/technical-evidence/analytical-records", params={"limit": 25, "offset": 0})
        excessive = client.get("/api/technical-evidence/monitoring", params={"limit": 101})
    assert catalogue.status_code == 200
    assert catalogue.json()["read_only"] is True
    assert catalogue.json()["audit_records_location"] == "settings"
    assert page.status_code == 200
    assert page.json()["items"] == []
    assert page.json()["read_only"] is True
    assert excessive.status_code == 422


def test_pc_quality_taxonomy_and_score_apis_return_all_supported_profiles(
    tmp_path: Path,
):
    database_path = tmp_path / "smartops.db"
    app = create_app(database_path)

    with TestClient(app) as client:
        with database_connection(database_path) as connection:
            before = tuple(connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0] for table in (
                "fine_quality_observations", "fine_quality_assessments",
                "fine_quality_metric_contributions",
            ))
        taxonomy_response = client.get("/api/quality/profile-taxonomy")
        scores_response = client.get("/api/quality/profile-scores")
        with database_connection(database_path) as connection:
            after = tuple(connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0] for table in (
                "fine_quality_observations", "fine_quality_assessments",
                "fine_quality_metric_contributions",
            ))

    assert taxonomy_response.status_code == 200
    assert scores_response.status_code == 200
    assert taxonomy_response.json()["profile_count"] == 30
    assert scores_response.json()["profile_count"] == 30
    assert len(taxonomy_response.json()["profiles"]) == 30
    assert len(scores_response.json()["profiles"]) == 30
    assert scores_response.json()["measure_name"] == "Current Workload Headroom"
    online = next(
        item for item in scores_response.json()["profiles"]
        if item["key"] == "online_learning_research"
    )
    assert online["detectability_state"] == "not_independently_detectable"
    assert online["assessment"] is None
    assert before == after


def test_count_processes_pagination_and_date_filters(tmp_path: Path):
    database_path = tmp_path / "smartops.db"
    initialize_database(database_path)
    with database_connection(database_path) as connection:
        insert_metric(connection, sample("2026-01-01T00:00:00+00:00"))
        insert_metric(connection, sample("2026-01-01T01:00:00+00:00"))
        insert_metric(connection, sample("2026-01-01T02:00:00+00:00"))

    with TestClient(create_app(database_path)) as client:
        latest_response = client.get("/api/metrics/latest")
        count_response = client.get("/api/metrics/count")
        history_response = client.get(
            "/api/metrics/history",
            params={
                "limit": 1,
                "offset": 1,
                "start": "2026-01-01T00:30:00Z",
                "end": "2026-01-01T03:00:00Z",
                "sort": "oldest",
            },
        )
        processes_response = client.get("/api/processes/latest")
        invalid_limit = client.get("/api/metrics/history?limit=5001")
        invalid_range = client.get(
            "/api/metrics/history",
            params={
                "start": "2026-01-02T00:00:00Z",
                "end": "2026-01-01T00:00:00Z",
            },
        )

    assert latest_response.status_code == 200
    assert latest_response.json()["cpu_per_core_percent"] == [5.0, 15.0]
    assert count_response.json() == {"count": 3}
    assert history_response.status_code == 200
    assert history_response.json()["total"] == 2
    assert history_response.json()["items"][0]["timestamp_utc"].startswith(
        "2026-01-01T02:00:00"
    )
    assert processes_response.status_code == 200
    assert processes_response.json()["cpu"][0]["process_name"] == "cpu.exe"
    assert invalid_limit.status_code == 422
    assert invalid_range.status_code == 422
