from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import analytics.quality as quality
from analytics.quality import (
    _load_inventory,
    _persist_assessment,
    build_assessment,
    collect_inventory,
    evaluate_profiles,
    store_inventory,
    suitability_result_for_score,
)
from analytics.quality_catalogue import INTERPRETATION, WORKLOAD_PROFILES
from backend.database import database_connection, initialize_database
from backend.main import create_app
from backend.phase4b_repository import (
    get_profile_observation,
    get_latest_inventory,
    get_quality_assessment,
    suitability_presentation,
)
from tests.test_phase4a import seed_established_upstream


DEVICE = "quality-test-device"
NOW = "2026-07-01T12:00:00+00:00"


def _inventory(
    *,
    signature: str = "inventory-a",
    physical_cores: int | None = 8,
    ram_gb: int | None = 32,
    storage_gb: int | None = 1024,
    gpu_classification: str | None = "dedicated",
    gpu_memory_gb: int | None = 12,
    gpu_memory_status: str = "available",
    os_architecture: str | None = "64-bit",
    media_type: str = "unknown",
    media_status: str = "unreliable",
) -> dict[str, object]:
    values: dict[str, tuple[object, str]] = {
        "cpu_name": ("Synthetic CPU", "available"),
        "system_architecture": ("x86_64", "available"),
        "physical_cores": (
            physical_cores,
            "available" if physical_cores is not None else "unavailable_required",
        ),
        "logical_processors": (16, "available"),
        "cpu_max_clock_mhz": (4000, "available"),
        "virtualization_capable": (True, "available"),
        "ram_installed_bytes": (
            ram_gb * 1024**3 if ram_gb is not None else None,
            "available" if ram_gb is not None else "unavailable_required",
        ),
        "ram_usable_bytes": (
            ram_gb * 1024**3 if ram_gb is not None else None,
            "available" if ram_gb is not None else "unavailable_required",
        ),
        "memory_module_count": (2, "available"),
        "system_drive_total_bytes": (
            storage_gb * 1024**3 if storage_gb is not None else None,
            "available" if storage_gb is not None else "unavailable_required",
        ),
        "system_drive_free_bytes": (256 * 1024**3, "available"),
        "storage_media_type": (media_type, media_status),
        "local_drive_count": (1, "available"),
        "gpu_name": (
            "Synthetic GPU" if gpu_classification is not None else None,
            "available" if gpu_classification is not None else "unavailable_optional",
        ),
        "gpu_classification": (
            gpu_classification,
            "available" if gpu_classification is not None else "unavailable_optional",
        ),
        "gpu_memory_bytes": (
            gpu_memory_gb * 1024**3 if gpu_memory_gb is not None else None,
            gpu_memory_status,
        ),
        "gpu_driver_version": ("1.0", "available"),
        "graphics_available": (
            gpu_classification is not None,
            "available" if gpu_classification is not None else "unavailable_optional",
        ),
        "windows_edition": ("Windows 11", "available"),
        "windows_version": ("10.0", "available"),
        "windows_build": ("26200", "available"),
        "os_architecture": (
            os_architecture,
            "available" if os_architecture is not None else "unavailable_required",
        ),
    }
    fields = [
        {
            "field_name": name,
            "component_group": quality.INVENTORY_FIELDS[name][0],
            "value": value,
            "availability_status": status,
            "reliability_note": (
                "Synthetic unreliable test value." if status == "unreliable" else None
            ),
            "source_name": "synthetic-test",
        }
        for name, (value, status) in values.items()
    ]
    required_missing = [
        field["field_name"]
        for field in fields
        if field["availability_status"] == "unavailable_required"
    ]
    return {
        "device_id": DEVICE,
        "captured_at_utc": NOW,
        "inventory_signature": hashlib.sha256(signature.encode()).hexdigest(),
        "provider_version": "test-provider",
        "detection_confidence": 95.0,
        "inventory_state": "inadequate" if required_missing else "available",
        "reason_codes": [
            f"{field}_unavailable" for field in required_missing
        ],
        "fields": fields,
        "privacy_excluded_fields": list(quality.PRIVACY_EXCLUDED_FIELDS),
    }


def _stored(path: Path, inventory: dict[str, object]) -> dict[str, object]:
    initialize_database(path)
    with database_connection(path) as connection:
        snapshot_id, _ = store_inventory(connection, inventory)
        return _load_inventory(connection, snapshot_id)


def test_safe_inventory_uses_allowlist_and_excludes_private_identifiers(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(quality, "_nvidia_inventory", lambda: None)
    monkeypatch.setattr(
        quality,
        "_run_powershell_json",
        lambda: {
            "Cpu": {
                "Name": "Allowed CPU",
                "Architecture": 9,
                "NumberOfCores": 4,
                "NumberOfLogicalProcessors": 8,
            },
            "Computer": {
                "TotalPhysicalMemory": 16 * 1024**3,
                "UserName": "must-not-be-collected",
            },
            "OperatingSystem": {
                "Caption": "Windows 11",
                "Version": "10.0",
                "BuildNumber": "26200",
                "OSArchitecture": "64-bit",
            },
            "Gpus": [{"Name": "Intel Iris Xe", "AdapterRAM": 2 * 1024**3}],
            "PhysicalDisks": [{"MediaType": "Unspecified"}],
            "SerialNumber": "must-not-be-collected",
        },
    )
    result = collect_inventory(DEVICE)
    names = {field["field_name"] for field in result["fields"]}
    serialized = json.dumps(result).casefold()

    assert names == set(quality.INVENTORY_FIELDS)
    assert "must-not-be-collected" not in serialized
    assert "serialnumber" not in serialized
    assert result["privacy_excluded_fields"] == list(
        quality.PRIVACY_EXCLUDED_FIELDS
    )
    media = next(
        field for field in result["fields"]
        if field["field_name"] == "storage_media_type"
    )
    assert media["value"] == "unknown"
    assert media["availability_status"] == "unreliable"


def test_graphics_cim_fallback_recovers_integrated_gpu_after_batch_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(quality, "_run_powershell_json", lambda: None)
    monkeypatch.setattr(quality, "_nvidia_inventory", lambda: None)
    monkeypatch.setattr(quality, "_run_video_controller_json", lambda: [{
        "Name": "Intel(R) Iris(R) Xe Graphics",
        "AdapterRAM": 2 * 1024**3,
        "DriverVersion": "test-driver",
    }])
    result = collect_inventory(DEVICE)
    fields = {item["field_name"]: item for item in result["fields"]}
    assert fields["gpu_name"]["value"] == "Intel(R) Iris(R) Xe Graphics"
    assert fields["gpu_classification"]["value"] == "integrated"
    assert fields["gpu_memory_bytes"]["availability_status"] == "unreliable"


def test_inventory_storage_reuses_unchanged_hardware_and_detects_change(
    tmp_path: Path,
):
    path = tmp_path / "quality.db"
    initialize_database(path)
    first = _inventory()
    second = copy.deepcopy(first)
    second["captured_at_utc"] = "2026-07-02T12:00:00+00:00"
    next(
        field for field in second["fields"]
        if field["field_name"] == "system_drive_free_bytes"
    )["value"] = 128 * 1024**3
    changed = _inventory(signature="inventory-b", ram_gb=64)
    with database_connection(path) as connection:
        first_id, first_reused = store_inventory(connection, first)
        second_id, second_reused = store_inventory(connection, second)
        changed_id, changed_reused = store_inventory(connection, changed)
        rows = connection.execute(
            """SELECT id, captured_at_utc, last_checked_at_utc
            FROM device_inventory_snapshots ORDER BY id"""
        ).fetchall()

    assert (first_reused, second_reused, changed_reused) == (False, True, False)
    assert first_id == second_id
    assert changed_id != first_id
    assert len(rows) == 2
    assert rows[0]["captured_at_utc"] == NOW
    assert rows[0]["last_checked_at_utc"] == second["captured_at_utc"]


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (100, "well_suited"),
        (90, "well_suited"),
        (89.999, "suitable"),
        (75, "suitable"),
        (60, "suitable_with_limits"),
        (40, "upgrade_recommended"),
        (0, "insufficient"),
    ],
)
def test_suitability_band_boundaries(score: float, expected: str):
    assert suitability_result_for_score(score) == expected


def test_every_result_band_is_reconstructed_in_a_synthetic_database(
    tmp_path: Path,
):
    path = tmp_path / "all-bands.db"
    initialize_database(path)
    profile = copy.deepcopy(WORKLOAD_PROFILES["software_development"])
    profile["key"] = "controlled_band_profile"
    profile["name"] = "Controlled band profile"
    profile["components"] = {
        "cpu_capability": {
            "required": True,
            "weight": 100,
            "metric": "physical_cores",
            "minimum": 100,
            "recommended": 200,
            "unit": "controlled units",
            "hard_gate": False,
        }
    }
    cases = (
        (0, "insufficient"),
        (66.6666667, "upgrade_recommended"),
        (100, "suitable_with_limits"),
        (137.5, "suitable"),
        (175, "well_suited"),
    )
    stored_results = []
    with database_connection(path) as connection:
        for index, (value, expected) in enumerate(cases):
            snapshot_id, _ = store_inventory(
                connection,
                _inventory(
                    signature=f"band-{index}",
                    physical_cores=value,
                ),
            )
            inventory = _load_inventory(connection, snapshot_id)
            assessment = build_assessment(connection, inventory, profile)
            with connection:
                assessment_id = _persist_assessment(connection, assessment)
            stored = get_quality_assessment(connection, assessment_id)
            assert stored is not None
            assert stored["suitability_result"] == expected
            assert stored["score_reconstruction"]["final_score_after_caps"] == (
                pytest.approx(stored["suitability_index"])
            )
            stored_results.append(expected)

    assert stored_results == [expected for _, expected in cases]


def test_missing_required_capability_is_not_evaluated_without_fake_zero(
    tmp_path: Path,
):
    path = tmp_path / "missing.db"
    inventory = _stored(path, _inventory(physical_cores=None))
    with database_connection(path) as connection:
        result = build_assessment(
            connection, inventory, WORKLOAD_PROFILES["software_development"]
        )

    assert result["evaluation_state"] == "not_evaluated"
    assert result["suitability_index"] is None
    assert result["suitability_result"] == "not_evaluated"
    assert "cpu_capability_unavailable_required" in result["reason_codes"]


def test_optional_missing_and_unreliable_values_are_excluded_or_provisional(
    tmp_path: Path,
):
    path = tmp_path / "optional.db"
    inventory = _stored(
        path,
        _inventory(
            gpu_classification=None,
            gpu_memory_gb=None,
            gpu_memory_status="unavailable_optional",
        ),
    )
    with database_connection(path) as connection:
        result = build_assessment(
            connection, inventory, WORKLOAD_PROFILES["everyday_productivity"]
        )

    assert result["evaluation_state"] == "provisional"
    assert result["suitability_index"] is not None
    graphics = next(
        item for item in result["components"]
        if item["component_name"] == "graphics_capability"
    )
    assert graphics["raw_component_score"] is None
    assert result["excluded_component_weight"] == 10
    assert sum(
        item["effective_weight"] for item in result["components"]
    ) == pytest.approx(1.0)


def test_integrated_gpu_is_valid_for_productivity_but_hard_limits_gpu_compute(
    tmp_path: Path,
):
    path = tmp_path / "integrated.db"
    inventory = _stored(
        path,
        _inventory(gpu_classification="integrated", gpu_memory_gb=2),
    )
    with database_connection(path) as connection:
        everyday = build_assessment(
            connection, inventory, WORKLOAD_PROFILES["everyday_productivity"]
        )
        gpu_compute = build_assessment(
            connection, inventory, WORKLOAD_PROFILES["local_ai_and_gpu_compute"]
        )

    assert everyday["suitability_index"] >= 90
    assert gpu_compute["suitability_index"] <= 39
    assert gpu_compute["suitability_result"] == "insufficient"
    assert any(
        gate["rule_type"] == "hard_requirement_gate"
        for gate in gpu_compute["gates"]
    )


def test_hardware_suitability_observation_and_evaluation_states_are_separate(
    tmp_path: Path,
):
    path = tmp_path / "suitability-states.db"
    integrated = _stored(
        path, _inventory(gpu_classification="integrated", gpu_memory_gb=2)
    )
    with database_connection(path) as connection, connection:
        connection.execute(
            """INSERT INTO feature_windows (
            device_id,window_start_utc,window_end_utc,sample_count,
            expected_sample_count,coverage_ratio,is_complete,
            dominant_workload_class,missing_indicators_json,updated_at_utc
            ) VALUES (?,?,?,10,10,1,1,'gaming_or_3d','[]',?)""",
            (DEVICE, NOW, "2026-07-01T12:05:00+00:00", NOW),
        )
        gaming = get_profile_observation(connection, "modern_3d_gaming", DEVICE)
        assessment = build_assessment(
            connection, integrated, WORKLOAD_PROFILES["modern_3d_gaming"]
        )
        presentation = suitability_presentation(
            assessment, gaming, profile_known=True
        )
    assert gaming["state"] == "observed"
    assert presentation["state"] == "evaluated"
    assert assessment["suitability_result"] == "insufficient"
    assert assessment["suitability_index"] is not None
    assert presentation["limiting_factor"]["component_name"] == "graphics_capability"


def test_workload_use_never_creates_a_score_when_required_gpu_is_missing(
    tmp_path: Path,
):
    path = tmp_path / "missing-gpu-observed.db"
    missing = _stored(
        path, _inventory(gpu_classification=None, gpu_memory_gb=None)
    )
    with database_connection(path) as connection, connection:
        connection.execute(
            """INSERT INTO feature_windows (
            device_id,window_start_utc,window_end_utc,sample_count,
            expected_sample_count,coverage_ratio,is_complete,
            dominant_workload_class,missing_indicators_json,updated_at_utc
            ) VALUES (?,?,?,10,10,1,1,'gaming_or_3d','[]',?)""",
            (DEVICE, NOW, "2026-07-01T12:05:00+00:00", NOW),
        )
        observation = get_profile_observation(
            connection, "modern_3d_gaming", DEVICE
        )
        assessment = build_assessment(
            connection, missing, WORKLOAD_PROFILES["modern_3d_gaming"]
        )
        presentation = suitability_presentation(
            assessment, observation, profile_known=True
        )
    assert observation["state"] == "observed"
    assert assessment["suitability_index"] is None
    assert presentation["state"] == "cannot_evaluate_required_hardware_missing"
    assert "graphics capability" in presentation["missing_requirements"]


def test_local_ai_cpu_only_observation_is_relevant_but_does_not_prove_gpu(
    tmp_path: Path,
):
    path = tmp_path / "cpu-local-ai.db"
    missing = _stored(
        path, _inventory(gpu_classification=None, gpu_memory_gb=None)
    )
    with database_connection(path) as connection, connection:
        connection.execute(
            "INSERT INTO metrics (timestamp_utc,device_id,foreground_process_name) VALUES (?,?,?)",
            (NOW, DEVICE, "Ollama.exe"),
        )
        observation = get_profile_observation(
            connection, "local_ai_and_gpu_compute", DEVICE
        )
        assessment = build_assessment(
            connection, missing, WORKLOAD_PROFILES["local_ai_and_gpu_compute"]
        )
        presentation = suitability_presentation(
            assessment, observation, profile_known=True
        )
    assert observation["state"] == "observed"
    assert presentation["state"] == "cannot_evaluate_required_hardware_missing"
    assert assessment["suitability_index"] is None


def test_unobserved_gpu_profile_can_still_receive_hardware_only_evaluation(
    tmp_path: Path,
):
    path = tmp_path / "unobserved-gpu.db"
    dedicated = _stored(path, _inventory())
    with database_connection(path) as connection:
        observation = get_profile_observation(
            connection, "local_ai_and_gpu_compute", DEVICE
        )
        assessment = build_assessment(
            connection, dedicated, WORKLOAD_PROFILES["local_ai_and_gpu_compute"]
        )
        presentation = suitability_presentation(
            assessment, observation, profile_known=True
        )
    assert observation["state"] == "not_observed"
    assert presentation["state"] == "evaluated"
    assert assessment["suitability_index"] is not None


def test_dedicated_gpu_without_vram_is_precisely_not_evaluable(tmp_path: Path):
    path = tmp_path / "missing-vram.db"
    inventory = _stored(
        path,
        _inventory(
            gpu_classification="dedicated",
            gpu_memory_gb=None,
            gpu_memory_status="unavailable_optional",
        ),
    )
    with database_connection(path) as connection:
        assessment = build_assessment(
            connection, inventory, WORKLOAD_PROFILES["modern_3d_gaming"]
        )
        presentation = suitability_presentation(
            assessment,
            get_profile_observation(connection, "modern_3d_gaming", DEVICE),
            profile_known=True,
        )
    assert assessment["evaluation_state"] == "not_evaluated"
    assert assessment["suitability_index"] is None
    assert presentation["state"] == "cannot_evaluate_required_hardware_missing"
    assert "gpu memory" in presentation["missing_requirements"]


@pytest.mark.parametrize("profile_key", [
    "modern_3d_gaming",
    "local_ai_and_gpu_compute",
])
def test_complete_dedicated_gpu_evidence_evaluates_stable_profile_ids(
    tmp_path: Path,
    profile_key: str,
):
    path = tmp_path / f"{profile_key}.db"
    inventory = _stored(path, _inventory())
    with database_connection(path) as connection:
        assessment = build_assessment(
            connection, inventory, WORKLOAD_PROFILES[profile_key]
        )
    assert assessment["profile_key"] == profile_key
    assert assessment["evaluation_state"] == "assessed"
    assert assessment["suitability_index"] is not None


def test_unreliable_dedicated_gpu_memory_makes_assessment_provisional(
    tmp_path: Path,
):
    path = tmp_path / "gpu-unreliable.db"
    inventory = _stored(
        path,
        _inventory(gpu_memory_status="unreliable"),
    )
    with database_connection(path) as connection:
        result = build_assessment(
            connection, inventory, WORKLOAD_PROFILES["modern_3d_gaming"]
        )

    assert result["evaluation_state"] == "provisional"
    assert "important_capability_uncertain" in result["reason_codes"]


def test_profile_weighting_changes_result_and_health_is_informational(
    tmp_path: Path,
):
    path = tmp_path / "separation.db"
    inventory = _stored(
        path,
        _inventory(physical_cores=4, ram_gb=16, storage_gb=256),
    )
    with database_connection(path) as connection:
        productivity = build_assessment(
            connection, inventory, WORKLOAD_PROFILES["everyday_productivity"]
        )
        gaming = build_assessment(
            connection, inventory, WORKLOAD_PROFILES["modern_3d_gaming"]
        )
        before = build_assessment(
            connection, inventory, WORKLOAD_PROFILES["software_development"]
        )
        # A deliberately poor current Health Score is linked for context only.
        window_id = connection.execute(
            """INSERT INTO feature_windows (
                device_id, window_start_utc, window_end_utc, sample_count,
                expected_sample_count, coverage_ratio, is_complete,
                missing_indicators_json, updated_at_utc
            ) VALUES (?, ?, ?, 10, 10, 1, 1, '{}', ?)""",
            (DEVICE, NOW, "2026-07-01T12:05:00+00:00", NOW),
        ).lastrowid
        connection.execute(
            """INSERT INTO health_assessments (
                feature_window_id, device_id, window_start_utc, window_end_utc,
                assessed_at_utc, system_health_score, health_band,
                evaluation_state, data_confidence, coverage_ratio,
                workload_context, workload_confidence, algorithm_version,
                configuration_version, normalization_method,
                available_component_weight, excluded_component_weight,
                reason_codes_json, consecutive_window_count,
                persistence_duration_seconds, trend_direction, recovery_state,
                feature_signature, feature_updated_at_utc, created_at_utc,
                updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, 10, 'critical_condition', 'provisional',
                      90, 1, 'idle', 0.9, 'test', 'test', 'test', 100, 0,
                      '[]', 1, 300, 'stable', 'not_recovering', 'signature',
                      ?, ?, ?)""",
            (
                window_id,
                DEVICE,
                NOW,
                "2026-07-01T12:05:00+00:00",
                    NOW,
                    NOW,
                    NOW,
                    NOW,
                ),
            )
        after = build_assessment(
            connection, inventory, WORKLOAD_PROFILES["software_development"]
        )
        _, _, risk_id = seed_established_upstream(
            connection,
            int(window_id),
            deviation_index=95,
            risk_index=95,
        )
        connection.execute(
            "UPDATE risk_assessments SET device_id = ? WHERE id = ?",
            (DEVICE, risk_id),
        )
        after_health_and_risk = build_assessment(
            connection, inventory, WORKLOAD_PROFILES["software_development"]
        )

    assert productivity["suitability_index"] != gaming["suitability_index"]
    assert before["suitability_index"] == after["suitability_index"]
    assert before["suitability_result"] == after["suitability_result"]
    assert before["suitability_index"] == after_health_and_risk["suitability_index"]
    assert after["current_readiness"]["health"]["system_health_score"] == 10
    assert after_health_and_risk["current_readiness"]["risk"][
        "risk_evidence_index"
    ] == 95
    assert "does not alter" in after["current_readiness"]["separation"]


def test_evaluation_persistence_reconstruction_idempotence_and_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    path = tmp_path / "evaluate.db"
    _stored(path, _inventory())
    first = evaluate_profiles(path, profile_key="software_development")
    second = evaluate_profiles(path, profile_key="software_development")
    with database_connection(path) as connection:
        assessment = get_quality_assessment(connection, 1)
        assessment_count = connection.execute(
            "SELECT COUNT(*) FROM workload_suitability_assessments"
        ).fetchone()[0]

    assert first["assessed"] == 1
    assert second["skipped"] == 1
    assert assessment_count == 1
    assert assessment is not None
    assert assessment["score_reconstruction"]["final_score_after_caps"] == (
        pytest.approx(assessment["suitability_index"])
    )

    original = quality._persist_assessment
    monkeypatch.setattr(
        quality, "_persist_assessment", lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("controlled persistence failure")
        )
    )
    failed = evaluate_profiles(
        path, profile_key="everyday_productivity", force=True
    )
    monkeypatch.setattr(quality, "_persist_assessment", original)
    with database_connection(path) as connection:
        partial = connection.execute(
            """SELECT COUNT(*) FROM workload_suitability_assessments
            WHERE profile_key = 'everyday_productivity'"""
        ).fetchone()[0]
    assert failed["assessed"] == 0
    assert partial == 0


def test_schema_six_migrates_additively_without_data_loss(tmp_path: Path):
    path = tmp_path / "schema-six.db"
    initialize_database(path)
    phase4b_tables = (
        "suitability_recommendations",
        "suitability_limiting_components",
        "suitability_gates_caps",
        "suitability_component_results",
        "workload_suitability_assessments",
        "quality_evaluation_runs",
        "inventory_component_values",
        "device_inventory_snapshots",
    )
    with sqlite3.connect(path) as connection:
        for table in phase4b_tables:
            connection.execute(f"DROP TABLE {table}")
        connection.execute(
            """INSERT INTO metrics (
                timestamp_utc, device_id, cpu_percent, ram_percent,
                ram_used_bytes, ram_total_bytes, disk_percent,
                disk_used_bytes, disk_total_bytes, uptime_seconds
            ) VALUES (?, ?, 10, 20, 200, 1000, 30, 300, 1000, 60)""",
            (NOW, DEVICE),
        )
        connection.execute("PRAGMA user_version = 6")

    initialize_database(path)
    with database_connection(path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        metric_count = connection.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
        created = {
            row["name"]
            for row in connection.execute(
                """SELECT name FROM sqlite_master
                WHERE type = 'table' AND name LIKE '%suitability%'"""
            )
        }
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()

    assert version == 19
    assert metric_count == 1
    assert "workload_suitability_assessments" in created
    assert integrity == "ok"
    assert foreign_keys == []


def test_quality_api_filters_pagination_and_get_requests_do_not_evaluate(
    tmp_path: Path,
):
    path = tmp_path / "api.db"
    _stored(path, _inventory())
    evaluate_profiles(path)
    with database_connection(path) as connection:
        before = connection.execute(
            "SELECT COUNT(*) FROM workload_suitability_assessments"
        ).fetchone()[0]

    with TestClient(create_app(path)) as client:
        responses = {
            "status": client.get("/api/quality/status"),
            "profiles": client.get("/api/quality/profiles"),
            "inventory": client.get("/api/quality/inventory/latest"),
            "latest": client.get(
                "/api/quality/latest?profile=software_development"
            ),
            "history": client.get(
                "/api/quality/history",
                params={
                    "limit": 2,
                    "offset": 1,
                    "sort": "oldest",
                    "evaluation_state": "assessed",
                },
            ),
            "count": client.get(
                "/api/quality/count?evaluation_state=assessed"
            ),
            "detail": client.get("/api/quality/1"),
            "missing": client.get("/api/quality/99999"),
            "legacy": client.get("/api/status"),
        }
        too_large = client.get("/api/quality/history?limit=5001")
    with database_connection(path) as connection:
        after = connection.execute(
            "SELECT COUNT(*) FROM workload_suitability_assessments"
        ).fetchone()[0]

    assert all(response.status_code == 200 for response in responses.values())
    assert responses["profiles"].json()["interpretation"] == INTERPRETATION
    assert len(responses["profiles"].json()["profiles"]) == 6
    assert responses["status"].json()["privacy_excluded_inputs"][0][
        "availability_status"
    ] == "excluded_for_privacy"
    assert responses["inventory"].json()["inventory"]["by_field"]["cpu_name"][
        "value"
    ] == "Synthetic CPU"
    assert responses["latest"].json()["assessment"]["profile_key"] == (
        "software_development"
    )
    assert responses["latest"].json()["presentation"]["state"] == "evaluated"
    assert responses["history"].json()["total"] == 6
    assert len(responses["history"].json()["items"]) == 2
    assert responses["count"].json() == {"count": 6}
    assert responses["missing"].json()["status"] == "not_evaluated"
    assert responses["legacy"].json()["status"] == "ok"
    assert too_large.status_code == 422
    assert before == after


def test_latest_quality_follows_last_verified_inventory_not_original_capture_time(
    tmp_path: Path,
):
    path = tmp_path / "inventory-recency.db"
    initialize_database(path)
    valid = _inventory(signature="valid", gpu_classification="integrated", gpu_memory_gb=2)
    valid["captured_at_utc"] = "2026-07-01T00:00:00+00:00"
    with database_connection(path) as connection:
        valid_id, _ = store_inventory(connection, valid)
    evaluate_profiles(path, force=True)

    unavailable = _inventory(
        signature="temporary-cim-failure",
        gpu_classification=None,
        gpu_memory_gb=None,
    )
    unavailable["captured_at_utc"] = "2026-07-02T00:00:00+00:00"
    with database_connection(path) as connection:
        unavailable_id, _ = store_inventory(connection, unavailable)
    evaluate_profiles(path, force=True)

    verified_again = copy.deepcopy(valid)
    verified_again["captured_at_utc"] = "2026-07-03T00:00:00+00:00"
    with database_connection(path) as connection:
        reused_id, reused = store_inventory(connection, verified_again)
        latest = get_latest_inventory(connection, DEVICE)

    assert reused is True
    assert reused_id == valid_id
    assert latest["id"] == valid_id
    assert latest["id"] != unavailable_id
    with TestClient(create_app(path)) as client:
        for profile in ("modern_3d_gaming", "local_ai_and_gpu_compute"):
            response = client.get("/api/quality/latest", params={"profile": profile})
            assert response.status_code == 200
            body = response.json()
            assert body["assessment"]["inventory_snapshot_id"] == valid_id
            assert body["assessment"]["evaluation_state"] == "assessed"
            assert body["assessment"]["suitability_index"] is not None
            assert body["presentation"]["state"] == "evaluated"


def test_recommendations_are_generic_and_not_commercial_products(tmp_path: Path):
    path = tmp_path / "guidance.db"
    inventory = _stored(
        path, _inventory(physical_cores=2, ram_gb=4, storage_gb=64)
    )
    with database_connection(path) as connection:
        result = build_assessment(
            connection, inventory, WORKLOAD_PROFILES["software_development"]
        )
    serialized = json.dumps(result["recommendations"]).casefold()

    assert result["recommendations"]
    assert "no performance gain is guaranteed" in serialized
    assert "commercial product recommendation" in serialized
    for commercial_name in ("amazon", "best buy", "newegg", "dell", "lenovo"):
        assert commercial_name not in serialized
