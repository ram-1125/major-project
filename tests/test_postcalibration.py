from __future__ import annotations

import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import backend.database as database_module
from agent.enhanced import _run_with_bounded_timeout_retry
from analytics.profile_quality import (
    _append_quality_plan,
    _broad_definition,
    _history_depth_description,
    detect_profile,
    profile_quality_status,
)
from analytics.profile_quality_catalogue import FINE_PROFILES, public_catalogue
from analytics.recalibration import baseline_management_status, start_recalibration
from backend.database import database_connection, initialize_database
from backend.postcalibration_repository import (
    get_pipeline_status,
    list_validations,
    register_validation,
    update_pipeline_stage,
    validation_metrics_from_counts,
)


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _active_v2(connection: sqlite3.Connection) -> int:
    return int(connection.execute(
        """INSERT INTO baseline_versions (
        device_id,version_number,version_label,lifecycle_state,
        algorithm_version,configuration_version,created_at_utc,
        activated_at_utc,reason_codes_json,learning_state
        ) VALUES ('device',2,'Permanent baseline v2','active',
        'test-algorithm','test-configuration',?,?, '[]','inactive')""",
        (NOW.isoformat(), NOW.isoformat()),
    ).lastrowid)


def test_schema17_migrates_additively_to_18_without_data_loss(tmp_path: Path):
    path = tmp_path / "schema17.db"
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO metrics(timestamp_utc,device_id) VALUES(?, 'preserved')",
            (NOW.isoformat(),),
        )
        for table in (
            "fine_quality_metric_contributions", "fine_quality_assessments",
            "fine_quality_observations", "validation_registry",
            "alert_confidence_components", "alert_explanation_snapshots",
            "alert_outcome_events", "pipeline_stage_status",
        ):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("PRAGMA user_version=17")

    initialize_database(path)
    with database_connection(path) as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 18
        assert connection.execute("SELECT COUNT(*) FROM metrics").fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert {
        "fine_quality_observations", "fine_quality_assessments",
        "validation_registry", "alert_explanation_snapshots",
        "alert_outcome_events", "pipeline_stage_status",
    } <= tables


def test_schema18_migration_rolls_back_completely_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    path = tmp_path / "rollback.db"
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        for table in (
            "fine_quality_metric_contributions", "fine_quality_assessments",
            "fine_quality_observations", "validation_registry",
            "alert_confidence_components", "alert_explanation_snapshots",
            "alert_outcome_events", "pipeline_stage_status",
        ):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("PRAGMA user_version=17")
    monkeypatch.setattr(
        database_module,
        "POSTCALIBRATION_SCHEMA_STATEMENTS",
        (*database_module.POSTCALIBRATION_SCHEMA_STATEMENTS, "CREATE TABLE invalid("),
    )
    with pytest.raises(sqlite3.OperationalError):
        initialize_database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 17
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='fine_quality_observations'"
        ).fetchone() is None


def test_taxonomy_has_twenty_fine_profiles_and_deterministic_foreground_priority():
    assert len(FINE_PROFILES) == 20
    assert len(public_catalogue()) == 30
    detected = detect_profile(
        ["EXCEL.EXE", "excel.exe", "Code.exe", None, None,
         None, None, None, None, None],
        ["python.exe"],
        primary_workload="office_productivity",
    )
    assert detected["profile_key"] == "spreadsheet_analysis"
    assert detected["reason"] == "recognised_foreground_executable_composition"
    assert detected["evidence"]["foreground_priority_applied"] is True


def test_supporting_or_launcher_process_alone_cannot_prove_fine_profile():
    detected = detect_profile(
        ["explorer.exe"] * 10,
        ["python.exe", "node.exe", "RiotClientServices.exe"],
        primary_workload="interactive_light",
    )
    assert detected["profile_key"] == "interactive_light"
    assert detected["reason"] == "broad_v2_profile_fallback_no_unambiguous_fine_evidence"


def test_profile_metric_weights_are_profile_specific_and_optional_missing_is_not_zero():
    conference = FINE_PROFILES["video_conferencing_online_classes"]["metrics"]
    compilation = FINE_PROFILES["software_build_compilation_testing"]["metrics"]
    assert conference["cpu_avg"]["weight"] != compilation["cpu_avg"]["weight"]
    assert conference["cpu_stddev"]["weight"] != compilation["cpu_stddev"]["weight"]
    assert all(config["direction"] == "lower_is_better" for config in conference.values())


def test_online_learning_is_catalogued_but_not_independently_detectable():
    profiles = {item["key"]: item for item in public_catalogue()}
    online = profiles["online_learning_research"]
    assert online["catalogue_state"] == "catalogued"
    assert online["detectability_state"] == "not_independently_detectable"
    assert online["foreground_executables"] == []
    assert "without inspecting content" in online["detectability_explanation"]

    browser_only = detect_profile(
        ["brave.exe"] * 10,
        [],
        primary_workload="browser_or_media",
    )
    assert browser_only["profile_key"] == "browser_or_media"
    assert browser_only["profile_key"] != "online_learning_research"


def test_one_observation_is_truthfully_labelled_limited_history():
    assert _history_depth_description(0, 0, 0) == ("none", "Not observed")
    assert _history_depth_description(0, 2, 1) == ("none", "Not evaluated")
    assert _history_depth_description(1, 1, 1) == (
        "one", "Limited history — based on one five-minute period",
    )
    assert _history_depth_description(2, 2, 1) == (
        "few", "2 qualifying observations across 1 distinct day",
    )


def test_profile_scores_are_separate_and_required_missing_data_is_not_fabricated(tmp_path: Path):
    path = tmp_path / "profile-score.db"
    initialize_database(path)
    with database_connection(path) as connection:
        with connection:
            version_id = _active_v2(connection)
            profile_ids = {}
            for scope in ("office_productivity", "__device__"):
                profile_ids[scope] = int(connection.execute(
                    """INSERT INTO baseline_version_profiles (
                    baseline_version_id,workload_scope,readiness_state,
                    reason_codes_json,feature_names_json,missing_features_json,
                    created_at_utc,updated_at_utc
                    ) VALUES (?,?,'ready','[]','[]','[]',?,?)""",
                    (version_id, scope, NOW.isoformat(), NOW.isoformat()),
                ).lastrowid)
                for metric, centre, upper in (
                    ("cpu_avg", 40, 70), ("ram_avg", 55, 75),
                    ("swap_avg", 2, 8), ("disk_usage_avg", 50, 70),
                    ("cpu_stddev", 8, 20), ("critical_event_count", 0, 0),
                    ("error_event_count", 0, 0),
                ):
                    connection.execute(
                        """INSERT INTO baseline_version_feature_stats (
                        version_profile_id,feature_name,direction,valid_count,
                        missing_count,missing_ratio,mean,median,percentile_05,
                        percentile_95
                        ) VALUES (?,?,'higher_is_unusual',30,0,0,?,?,?,?)""",
                        (profile_ids[scope], metric, centre, centre, centre, upper),
                    )
        active = connection.execute(
            "SELECT * FROM baseline_versions WHERE id=?", (version_id,)
        ).fetchone()
        item = {
            "observation_id": 1, "window_id": 1, "device_id": "device",
            "coverage_ratio": 1.0, "detection_confidence": 90,
            "cpu_avg": 90, "ram_avg": 70, "swap_avg": 2,
            "disk_usage_avg": 50, "cpu_stddev": 10,
            "critical_event_count": 0, "error_event_count": 0,
        }
        plans = []
        _append_quality_plan(
            connection, active, item, "spreadsheet_analysis",
            FINE_PROFILES["spreadsheet_analysis"], plans,
        )
        _append_quality_plan(
            connection, active, item, "device", _broad_definition("device"), plans,
        )
        missing_item = {**item, "observation_id": 2, "ram_avg": None}
        _append_quality_plan(
            connection, active, missing_item, "spreadsheet_analysis",
            FINE_PROFILES["spreadsheet_analysis"], plans,
        )
    assert plans[0]["score"] != plans[1]["score"]
    assert plans[0]["score"] < 100  # A ready baseline does not assign 100.
    assert plans[2]["score"] is None
    missing_ram = next(
        value for value in plans[2]["contributions"] if value["metric_key"] == "ram_avg"
    )
    assert missing_ram["observed_value"] is None
    assert missing_ram["metric_score"] is None


def test_headroom_status_prefers_post_activation_and_reports_factual_history(
    tmp_path: Path,
):
    path = tmp_path / "headroom-history.db"
    initialize_database(path)
    with database_connection(path) as connection:
        with connection:
            version_id = _active_v2(connection)
            version_profile_id = int(connection.execute(
                """INSERT INTO baseline_version_profiles (
                baseline_version_id,workload_scope,readiness_state,
                reason_codes_json,feature_names_json,missing_features_json,
                created_at_utc,updated_at_utc
                ) VALUES (?,'office_productivity','ready','[]','[]','[]',?,?)""",
                (version_id, NOW.isoformat(), NOW.isoformat()),
            ).lastrowid)
            connection.execute(
                "INSERT INTO metrics(timestamp_utc,device_id) VALUES(?, 'device')",
                ((NOW + timedelta(days=1, minutes=6)).isoformat(),),
            )

            def observation(at: datetime, score: float, training: bool) -> int:
                start = at - timedelta(minutes=5)
                window_id = int(connection.execute(
                    """INSERT INTO feature_windows (
                    device_id,window_start_utc,window_end_utc,sample_count,
                    expected_sample_count,coverage_ratio,is_complete,
                    missing_indicators_json,updated_at_utc,finalization_state
                    ) VALUES ('device',?,?,10,10,1,1,'{}',?,'finalized')""",
                    (start.isoformat(), at.isoformat(), at.isoformat()),
                ).lastrowid)
                observation_id = int(connection.execute(
                    """INSERT INTO fine_quality_observations (
                    feature_window_id,device_id,observed_at_utc,detected_profile_key,
                    parent_workload_profile,detection_confidence,detection_reason,
                    evidence_identifiers_json,taxonomy_version,detection_rule_version,
                    derivation_mode,created_at_utc
                    ) VALUES (?,'device',?,'word_processing','office_productivity',
                    80,'recognised_foreground_executable_composition','{}',
                    'smartops-pc-quality-taxonomy-v2',
                    'postcalibration-profile-detection-v1','test',?)""",
                    (window_id, at.isoformat(), at.isoformat()),
                ).lastrowid)
                connection.execute(
                    """INSERT INTO fine_quality_assessments (
                    observation_id,feature_window_id,device_id,profile_key,profile_name,
                    parent_workload_profile,assessed_at_utc,evaluation_state,
                    profile_quality_score,evidence_confidence,baseline_version_id,
                    taxonomy_version,detection_rule_version,scoring_method_version,
                    normalization_method,reason_codes_json,missing_evidence_json,
                    explanation,created_at_utc
                    ) VALUES (?,?,'device','word_processing','Word processing',
                    'office_productivity',?,'assessed',?,90,?,
                    'smartops-pc-quality-taxonomy-v2',
                    'postcalibration-profile-detection-v1',
                    'v2-anchored-profile-quality-v1','available','[\"baseline_scope_office_productivity\"]',
                    '[]','Current workload headroom test.',?)""",
                    (observation_id, window_id, at.isoformat(), score,
                     version_id, at.isoformat()),
                )
                if training:
                    connection.execute(
                        """INSERT INTO baseline_version_training_windows (
                        version_profile_id,feature_window_id) VALUES (?,?)""",
                        (version_profile_id, window_id),
                    )
                return window_id

            independent_window = observation(
                NOW + timedelta(days=1, minutes=5), 94.2, False,
            )
            # Insert the older calibration-period result second to prove selection
            # is based on evidence chronology/independence, not MAX(id).
            observation(NOW - timedelta(minutes=5), 100.0, True)

    before = path.stat().st_size
    status = profile_quality_status(path)
    after = path.stat().st_size
    word = next(item for item in status["profiles"] if item["key"] == "word_processing")
    assert status["profile_count"] == 30
    assert word["assessment"]["feature_window_id"] == independent_window
    assert word["assessment"]["profile_quality_score"] == pytest.approx(94.2)
    assert word["assessment"]["baseline_version_number"] == 2
    assert word["assessment"]["evidence_independence_state"] == (
        "independent_post_calibration_evidence"
    )
    assert word["history_depth"]["qualifying_observation_count"] == 2
    assert word["history_depth"]["distinct_observation_days"] == 2
    assert word["history_depth"]["baseline_training_observation_count"] == 1
    assert word["history_depth"]["independent_post_activation_observation_count"] == 1
    assert before == after


def test_active_v2_allows_only_explicit_single_optional_candidate(tmp_path: Path):
    path = tmp_path / "optional-recalibration.db"
    initialize_database(path)
    with database_connection(path) as connection:
        with connection:
            _active_v2(connection)
    status = baseline_management_status(path, device_id="device")
    assert status["calibration_available"] is True
    assert status["candidate_version"] is None
    started = start_recalibration(path, device_id="device")
    assert started["active_version"]["version_number"] == 2
    assert started["candidate_version"]["version_number"] == 3
    with pytest.raises(ValueError, match="already in progress"):
        start_recalibration(path, device_id="device")
    with database_connection(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM baseline_versions WHERE version_number=3"
        ).fetchone()[0] == 1


def test_validation_registry_metrics_and_labels_are_method_level(tmp_path: Path):
    path = tmp_path / "validation.db"
    initialize_database(path)
    metrics = validation_metrics_from_counts(8, 80, 4, 2)
    assert metrics["sample_size"] == 94
    assert metrics["accuracy"] == pytest.approx(88 / 94)
    assert metrics["precision"] == pytest.approx(8 / 12)
    assert metrics["recall"] == pytest.approx(0.8)
    with database_connection(path) as connection:
        with connection:
            register_validation(
                connection, rule_identifier="smartops-alert:test",
                rule_version="v1", workload_scope="*",
                validation_type="synthetic", counts=(8, 80, 4, 2),
                dataset_description="Isolated controlled records.",
                validation_procedure="Deterministic unit procedure.",
                validation_period=None, validation_date_utc=NOW.isoformat(),
                limitations=["Synthetic evidence is not real-world accuracy."],
                supporting_research=[], code_configuration_version="test",
            )
            register_validation(
                connection, rule_identifier="smartops-alert:unvalidated",
                rule_version="v1", workload_scope="*",
                validation_type="not_yet_validated", counts=(9, 9, 9, 9),
                dataset_description="No labelled dataset.",
                validation_procedure="Not run.", validation_period=None,
                validation_date_utc=None, limitations=["Not yet validated."],
                supporting_research=[], code_configuration_version="test",
            )
        rows = list_validations(connection)
    synthetic = next(row for row in rows if row["validation_type"] == "synthetic")
    unvalidated = next(row for row in rows if row["validation_type"] == "not_yet_validated")
    assert synthetic["display_label"] == "Synthetic validation"
    assert synthetic["sample_size"] == 94
    assert unvalidated["display_label"] == "Not yet validated"
    assert unvalidated["accuracy"] is None and unvalidated["sample_size"] == 0


def test_pipeline_transitions_processing_success_overdue_failure_and_active_v2(tmp_path: Path):
    path = tmp_path / "pipeline.db"
    initialize_database(path)
    with database_connection(path) as connection:
        with connection:
            _active_v2(connection)
            update_pipeline_stage(
                connection, "telemetry_collection", "processing",
                attempted_at_utc=NOW.isoformat(),
            )
        processing = get_pipeline_status(connection, now=NOW + timedelta(seconds=5))
        assert next(stage for stage in processing["stages"] if stage["stage_key"] == "telemetry_collection")["current_state"] == "processing"
        with connection:
            update_pipeline_stage(
                connection, "telemetry_collection", "successfully_waiting",
                successful_at_utc=(NOW + timedelta(seconds=6)).isoformat(),
                duration_ms=100,
            )
        waiting = get_pipeline_status(connection, now=NOW + timedelta(seconds=20))
        assert next(stage for stage in waiting["stages"] if stage["stage_key"] == "telemetry_collection")["current_state"] == "successfully_waiting"
        overdue = get_pipeline_status(connection, now=NOW + timedelta(seconds=200))
        assert next(stage for stage in overdue["stages"] if stage["stage_key"] == "telemetry_collection")["current_state"] == "overdue"
        baseline = next(stage for stage in overdue["stages"] if stage["stage_key"] == "active_baseline")
        assert baseline["current_state"] == "successfully_waiting"
        assert baseline["active_baseline_version"] == 2
        with connection:
            update_pipeline_stage(connection, "health_evaluation", "failed", reason="controlled_failure")
        failed = get_pipeline_status(connection, now=NOW)
        assert next(stage for stage in failed["stages"] if stage["stage_key"] == "health_evaluation")["current_state"] == "failed"


def test_optional_windows_timeout_retries_once_then_succeeds():
    calls = 0

    def runner(script: str, timeout: int):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise subprocess.TimeoutExpired("powershell", timeout)
        return {"ok": True}

    assert _run_with_bounded_timeout_retry(runner, "query", 3) == {"ok": True}
    assert calls == 2
