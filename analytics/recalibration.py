"""Opt-in, versioned workload-aware baseline recalibration.

Candidate versions learn only from complete windows observed after the user
starts recalibration. They never affect genuine assessments until an explicit
activation action copies the ready snapshot into the active runtime projection.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agent.config import get_database_path
from analytics.baseline import robust_statistics, window_eligibility
from analytics.baseline_config import (
    ALGORITHM_VERSION,
    DEFAULT_POLICY,
    DEVICE_SCOPE,
    FEATURE_DEFINITIONS,
)
from backend.database import database_connection, initialize_database
from backend.phase7b2_repository import ELIGIBILITY_RULE_VERSION
from backend.phase7b1_repository import ensure_versioned_baseline_snapshot


RULE_VERSION = "phase7b1-recalibration-v2"
CONFIGURATION_VERSION = "phase7b1-baseline-candidate-v2"
GUIDED_DEVELOPMENT_SCOPE = "guided_development"
WORKLOAD_SCOPES = (
    "idle",
    "interactive_light",
    "background_activity",
    "development",
    GUIDED_DEVELOPMENT_SCOPE,
    "browser_or_media",
    "office_productivity",
    "compute_intensive",
    "gaming_or_3d",
)
FOREGROUND_DEPENDENT_SCOPES = {
    "development", GUIDED_DEVELOPMENT_SCOPE, "browser_or_media",
    "office_productivity", "gaming_or_3d",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _event(
    connection: sqlite3.Connection,
    version_id: int,
    event_type: str,
    previous_state: str | None,
    new_state: str,
    reason: str,
    details: dict[str, Any] | None = None,
) -> None:
    connection.execute(
        """INSERT INTO baseline_recalibration_events (
        baseline_version_id, event_type, event_timestamp_utc, previous_state,
        new_state, reason_code, details_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            version_id, event_type, _now().isoformat(), previous_state,
            new_state, reason, _json(details or {}),
        ),
    )


def _candidate_rows(
    connection: sqlite3.Connection,
    version_id: int,
    device_id: str,
    scope: str,
    started_at_utc: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Counter[str]]:
    clauses = [
        "device_id = ?", "window_end_utc >= ?",
        "COALESCE(finalization_state, 'legacy_finalized') IN "
        "('legacy_finalized', 'finalized', 'audited_correction')",
    ]
    parameters: list[Any] = [device_id, started_at_utc]
    if scope != DEVICE_SCOPE:
        clauses.append(
            "secondary_workload_context = ?"
            if scope == GUIDED_DEVELOPMENT_SCOPE
            else "dominant_workload_class = ?"
        )
        parameters.append(scope)
    observed = [
        dict(row)
        for row in connection.execute(
            f"""SELECT * FROM feature_windows
            WHERE {' AND '.join(clauses)} ORDER BY window_start_utc, id""",
            parameters,
        )
    ]
    paused_at: datetime | None = None
    paused_intervals: list[tuple[datetime, datetime]] = []
    for event in connection.execute(
        """SELECT event_type, event_timestamp_utc
        FROM baseline_recalibration_events
        WHERE baseline_version_id = ? AND event_type IN ('paused', 'resumed')
        ORDER BY event_timestamp_utc, id""",
        (version_id,),
    ):
        if event["event_type"] == "paused":
            paused_at = datetime.fromisoformat(event["event_timestamp_utc"])
        elif event["event_type"] == "resumed" and paused_at is not None:
            paused_intervals.append(
                (paused_at, datetime.fromisoformat(event["event_timestamp_utc"]))
            )
            paused_at = None
    if paused_intervals:
        observed = [
            row
            for row in observed
            if not any(
                start <= datetime.fromisoformat(row["window_end_utc"]) <= end
                for start, end in paused_intervals
            )
        ]
    eligible: list[dict[str, Any]] = []
    exclusion_reasons: Counter[str] = Counter()
    current_time = _now()
    for row in observed:
        is_eligible, reasons = window_eligibility(row, current_time, DEFAULT_POLICY)
        if is_eligible:
            eligible.append(row)
        else:
            exclusion_reasons.update(reasons)
    return observed, eligible, exclusion_reasons


def _profile_readiness(
    scope: str,
    observed_count: int,
    eligible_count: int,
    distinct_days: int,
) -> tuple[str, list[str], str]:
    reasons: list[str] = []
    if observed_count == 0:
        return (
            "not_observed",
            ["workload_not_observed_since_recalibration_started"],
            "not_observed",
        )
    minimum = (
        DEFAULT_POLICY.minimum_device_windows
        if scope == DEVICE_SCOPE
        else DEFAULT_POLICY.minimum_workload_windows
    )
    if eligible_count < minimum:
        reasons.append("eligible_window_count_below_minimum")
    if distinct_days < DEFAULT_POLICY.minimum_distinct_days:
        reasons.append("distinct_collection_days_below_minimum")
    if eligible_count < observed_count:
        # This is audit information, not a permanent readiness blocker.
        reasons.append("some_windows_excluded_by_quality_or_event_policy")
    blocking_reasons = {
        "eligible_window_count_below_minimum",
        "distinct_collection_days_below_minimum",
    }
    ready = not any(reason in blocking_reasons for reason in reasons)
    return (
        "ready" if ready else "collecting_data",
        reasons,
        "applicable" if ready else "observed_insufficient",
    )


def _refresh_profile(
    connection: sqlite3.Connection,
    version: sqlite3.Row,
    scope: str,
    timestamp: str,
) -> dict[str, Any]:
    observed, eligible, exclusion_reasons = _candidate_rows(
        connection,
        int(version["id"]),
        version["device_id"],
        scope,
        version["learning_started_at_utc"],
    )
    distinct_days = len({row["window_start_utc"][:10] for row in eligible})
    readiness, reasons, applicability = _profile_readiness(
        scope, len(observed), len(eligible), distinct_days
    )
    if applicability == "not_observed" and scope in FOREGROUND_DEPENDENT_SCOPES:
        visibility = connection.execute(
            """SELECT
            SUM(CASE WHEN COALESCE(user_activity_state, user_state) = 'active'
                THEN 1 ELSE 0 END) AS active_count,
            SUM(CASE WHEN COALESCE(user_activity_state, user_state) = 'active'
                AND foreground_process_name IS NOT NULL THEN 1 ELSE 0 END)
                AS visible_count
            FROM metrics WHERE device_id = ? AND timestamp_utc >= ?""",
            (version["device_id"], version["learning_started_at_utc"]),
        ).fetchone()
        if visibility["active_count"] and not visibility["visible_count"]:
            readiness = "collector_limited"
            applicability = "collector_or_permission_limited"
            reasons = ["foreground_process_unavailable_for_active_samples"]
    completeness = (
        sum(float(row["coverage_ratio"]) for row in observed) / len(observed)
        if observed else None
    )
    stats = {
        name: robust_statistics(
            [
                float(row[name]) for row in eligible
                if isinstance(row.get(name), (int, float))
                and math.isfinite(float(row[name]))
            ],
            len(eligible),
            definition.direction,
        )
        for name, definition in FEATURE_DEFINITIONS.items()
    }
    missing = [name for name, item in stats.items() if item["valid_count"] == 0]
    connection.execute(
        """INSERT INTO baseline_version_profiles (
        baseline_version_id, workload_scope, observed_window_count,
        eligible_window_count, excluded_window_count, distinct_day_count,
        sampling_completeness, readiness_state, training_start_utc,
        training_end_utc, last_learning_at_utc, reason_codes_json,
        feature_names_json, missing_features_json, created_at_utc, updated_at_utc,
        applicability_state, applicability_reasons_json,
        exclusion_reason_counts_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(baseline_version_id, workload_scope) DO UPDATE SET
        observed_window_count=excluded.observed_window_count,
        eligible_window_count=excluded.eligible_window_count,
        excluded_window_count=excluded.excluded_window_count,
        distinct_day_count=excluded.distinct_day_count,
        sampling_completeness=excluded.sampling_completeness,
        readiness_state=excluded.readiness_state,
        training_start_utc=excluded.training_start_utc,
        training_end_utc=excluded.training_end_utc,
        last_learning_at_utc=excluded.last_learning_at_utc,
        reason_codes_json=excluded.reason_codes_json,
        feature_names_json=excluded.feature_names_json,
        missing_features_json=excluded.missing_features_json,
        updated_at_utc=excluded.updated_at_utc,
        applicability_state=excluded.applicability_state,
        applicability_reasons_json=excluded.applicability_reasons_json,
        exclusion_reason_counts_json=excluded.exclusion_reason_counts_json""",
        (
            version["id"], scope, len(observed), len(eligible),
            len(observed) - len(eligible), distinct_days, completeness, readiness,
            eligible[0]["window_start_utc"] if eligible else None,
            eligible[-1]["window_end_utc"] if eligible else None,
            timestamp, _json(reasons), _json(list(FEATURE_DEFINITIONS)),
            _json(missing), timestamp, timestamp, applicability,
            _json([
                "not_required_for_activation_until_observed"
                if applicability == "not_observed"
                else "profile_has_sufficient_eligible_evidence"
                if applicability == "applicable"
                else "foreground_collection_unavailable_or_permission_limited"
                if applicability == "collector_or_permission_limited"
                else "profile_observed_but_requirements_not_met"
            ]),
            _json(dict(sorted(exclusion_reasons.items()))),
        ),
    )
    profile_id = int(connection.execute(
        """SELECT id FROM baseline_version_profiles
        WHERE baseline_version_id = ? AND workload_scope = ?""",
        (version["id"], scope),
    ).fetchone()[0])
    connection.execute(
        "DELETE FROM baseline_version_feature_stats WHERE version_profile_id = ?",
        (profile_id,),
    )
    for name, feature_stats in stats.items():
        columns = ("version_profile_id", "feature_name", *feature_stats.keys())
        connection.execute(
            f"""INSERT INTO baseline_version_feature_stats ({', '.join(columns)})
            VALUES ({', '.join('?' for _ in columns)})""",
            (profile_id, name, *feature_stats.values()),
        )
    _reconcile_training_membership(
        connection,
        baseline_version_id=int(version["id"]),
        profile_id=profile_id,
        observed=observed,
        eligible=eligible,
        timestamp=timestamp,
    )
    return {
        "workload_scope": scope,
        "readiness_state": readiness,
        "eligible_window_count": len(eligible),
        "applicability_state": applicability,
    }


def _reconcile_training_membership(
    connection: sqlite3.Connection,
    *,
    baseline_version_id: int,
    profile_id: int,
    observed: list[dict[str, Any]],
    eligible: list[dict[str, Any]],
    timestamp: str,
) -> dict[str, int]:
    """Reconcile accepted links and append one audit event per real transition."""
    current = {
        int(row[0]) for row in connection.execute(
            "SELECT feature_window_id FROM baseline_version_training_windows "
            "WHERE version_profile_id = ?", (profile_id,),
        )
    }
    eligible_by_id = {int(row["id"]): row for row in eligible}
    observed_by_id = {int(row["id"]): row for row in observed}
    desired = set(eligible_by_id)
    accepted = removed = rejected = 0

    for window_id in sorted(desired - current):
        prior = connection.execute(
            """SELECT new_state FROM baseline_training_membership_events
            WHERE version_profile_id=? AND feature_window_id=?
            ORDER BY id DESC LIMIT 1""", (profile_id, window_id),
        ).fetchone()
        previous_state = str(prior[0]) if prior else "unknown"
        repaired = connection.execute(
            "SELECT 1 FROM feature_window_repair_events WHERE feature_window_id=? LIMIT 1",
            (window_id,),
        ).fetchone() is not None
        event_type = (
            "reaccepted_after_repair"
            if repaired or previous_state in {"rejected", "removed"}
            else "accepted"
        )
        connection.execute(
            "INSERT OR IGNORE INTO baseline_version_training_windows "
            "(version_profile_id, feature_window_id) VALUES (?, ?)",
            (profile_id, window_id),
        )
        connection.execute(
            """INSERT OR IGNORE INTO baseline_training_membership_events (
            baseline_version_id, version_profile_id, feature_window_id,
            previous_state, new_state, event_type, eligibility_rule_version,
            reason_codes_json, event_timestamp_utc, details_json
            ) VALUES (?, ?, ?, ?, 'accepted', ?, ?, ?, ?, '{}')""",
            (baseline_version_id, profile_id, window_id, previous_state, event_type,
             ELIGIBILITY_RULE_VERSION, _json(["eligible_finalized_window"]), timestamp),
        )
        accepted += 1

    for window_id in sorted(current - desired):
        row = observed_by_id.get(window_id)
        allowed, reasons = window_eligibility(row, _now(), DEFAULT_POLICY) if row else (
            False, ["window_no_longer_in_profile_scope"]
        )
        assert not allowed
        connection.execute(
            "DELETE FROM baseline_version_training_windows "
            "WHERE version_profile_id=? AND feature_window_id=?",
            (profile_id, window_id),
        )
        connection.execute(
            """INSERT OR IGNORE INTO baseline_training_membership_events (
            baseline_version_id, version_profile_id, feature_window_id,
            previous_state, new_state, event_type, eligibility_rule_version,
            reason_codes_json, event_timestamp_utc, details_json
            ) VALUES (?, ?, ?, 'accepted', 'removed', 'removed_after_correction',
            ?, ?, ?, '{}')""",
            (baseline_version_id, profile_id, window_id, ELIGIBILITY_RULE_VERSION,
             _json(reasons), timestamp),
        )
        removed += 1

    for window_id in sorted(set(observed_by_id) - desired - current):
        prior = connection.execute(
            """SELECT 1 FROM baseline_training_membership_events
            WHERE version_profile_id=? AND feature_window_id=? LIMIT 1""",
            (profile_id, window_id),
        ).fetchone()
        if prior:
            continue
        _, reasons = window_eligibility(observed_by_id[window_id], _now(), DEFAULT_POLICY)
        connection.execute(
            """INSERT OR IGNORE INTO baseline_training_membership_events (
            baseline_version_id, version_profile_id, feature_window_id,
            previous_state, new_state, event_type, eligibility_rule_version,
            reason_codes_json, event_timestamp_utc, details_json
            ) VALUES (?, ?, ?, 'unknown', 'rejected', 'rejected', ?, ?, ?, '{}')""",
            (baseline_version_id, profile_id, window_id, ELIGIBILITY_RULE_VERSION,
             _json(reasons), timestamp),
        )
        rejected += 1
    return {"accepted": accepted, "removed": removed, "rejected": rejected}


def refresh_candidate(
    database_path: Path | None = None,
    *,
    device_id: str | None = None,
    force: bool = False,
) -> dict[str, Any] | None:
    path = initialize_database(database_path or get_database_path())
    with database_connection(path) as connection:
        version = connection.execute(
            """SELECT * FROM baseline_versions
            WHERE lifecycle_state IN ('candidate', 'ready')
              AND (? IS NULL OR device_id = ?)
            ORDER BY version_number DESC LIMIT 1""",
            (device_id, device_id),
        ).fetchone()
        if version is None:
            return None
        newest = connection.execute(
            """SELECT MAX(window_end_utc) FROM feature_windows
            WHERE device_id = ?""",
            (version["device_id"],),
        ).fetchone()[0]
        if (
            not force and version["last_learning_at_utc"]
            and newest and newest <= version["last_learning_at_utc"]
        ):
            return baseline_management_status(path, device_id=version["device_id"])[
                "candidate_version"
            ]
        timestamp = _now().isoformat()
        with connection:
            profiles = [
                _refresh_profile(connection, version, scope, timestamp)
                for scope in (DEVICE_SCOPE, *WORKLOAD_SCOPES)
            ]
            device_ready = any(
                item["workload_scope"] == DEVICE_SCOPE
                and item["readiness_state"] == "ready"
                for item in profiles
            )
            workload_ready = any(
                item["workload_scope"] != DEVICE_SCOPE
                and item["readiness_state"] == "ready"
                for item in profiles
            )
            new_state = "ready" if device_ready and workload_ready else "candidate"
            current_learning_state = version["learning_state"] or (
                "collecting" if version["lifecycle_state"] == "candidate"
                else "ready_for_validation"
            )
            learning_state = current_learning_state
            if new_state == "ready" and version["lifecycle_state"] != "ready":
                learning_state = "ready_for_validation"
            elif new_state == "candidate":
                learning_state = "collecting"
            if device_ready:
                connection.execute(
                    """UPDATE baseline_version_profiles
                    SET applicability_state = 'not_applicable',
                        applicability_reasons_json = ?
                    WHERE baseline_version_id = ?
                      AND workload_scope != ?
                      AND observed_window_count = 0
                      AND applicability_state = 'not_observed'""",
                    (
                        _json([
                            "not_observed_during_sufficient_device_history",
                            "not_required_for_candidate_activation",
                        ]),
                        version["id"],
                        DEVICE_SCOPE,
                    ),
                )
            connection.execute(
                """UPDATE baseline_versions SET lifecycle_state = ?,
                learning_state = ?, last_learning_at_utc = ?,
                completed_at_utc = ? WHERE id = ?""",
                (
                    new_state, learning_state, timestamp,
                    timestamp if new_state == "ready" else None,
                    version["id"],
                ),
            )
            if new_state != version["lifecycle_state"]:
                _event(
                    connection, int(version["id"]), "candidate_ready",
                    version["lifecycle_state"], new_state,
                    "minimum_recalibration_history_satisfied",
                )
    return baseline_management_status(path, device_id=version["device_id"])[
        "candidate_version"
    ]


def start_recalibration(
    database_path: Path | None = None,
    *,
    device_id: str,
) -> dict[str, Any]:
    path = initialize_database(database_path or get_database_path())
    with database_connection(path) as connection:
        with connection:
            ensure_versioned_baseline_snapshot(connection, device_id)
        existing = connection.execute(
            """SELECT id FROM baseline_versions WHERE device_id = ?
            AND lifecycle_state IN ('candidate', 'paused', 'ready')""",
            (device_id,),
        ).fetchone()
        if existing:
            raise ValueError("A baseline recalibration is already in progress.")
        active = connection.execute(
            """SELECT * FROM baseline_versions WHERE device_id = ?
            AND lifecycle_state = 'active'""",
            (device_id,),
        ).fetchone()
        if active is None:
            raise ValueError("An active baseline must exist before recalibration.")
        version_number = int(connection.execute(
            "SELECT COALESCE(MAX(version_number), 0) + 1 FROM baseline_versions WHERE device_id = ?",
            (device_id,),
        ).fetchone()[0])
        timestamp = _now().isoformat()
        with connection:
            cursor = connection.execute(
                """INSERT INTO baseline_versions (
                device_id, version_number, version_label, lifecycle_state,
                algorithm_version, configuration_version, previous_version_id,
                created_at_utc, learning_started_at_utc, last_learning_at_utc,
                reason_codes_json, learning_state
                ) VALUES (?, ?, ?, 'candidate', ?, ?, ?, ?, ?, NULL, ?, 'collecting')""",
                (
                    device_id, version_number,
                    f"Representative workload candidate v{version_number}",
                    ALGORITHM_VERSION, CONFIGURATION_VERSION, active["id"],
                    timestamp, timestamp,
                    _json(["collecting_representative_post_start_windows"]),
                ),
            )
            version_id = int(cursor.lastrowid)
            _event(
                connection, version_id, "started", None, "candidate",
                "user_started_recalibration",
            )
    refresh_candidate(path, device_id=device_id, force=True)
    return baseline_management_status(path, device_id=device_id)


def _change_state(
    database_path: Path | None,
    *,
    device_id: str,
    expected: tuple[str, ...],
    new_state: str,
    event_type: str,
    reason: str,
) -> dict[str, Any]:
    path = initialize_database(database_path or get_database_path())
    with database_connection(path) as connection:
        placeholders = ",".join("?" for _ in expected)
        version = connection.execute(
            f"""SELECT * FROM baseline_versions WHERE device_id = ?
            AND lifecycle_state IN ({placeholders}) ORDER BY version_number DESC LIMIT 1""",
            (device_id, *expected),
        ).fetchone()
        if version is None:
            raise ValueError("No compatible baseline recalibration is available.")
        timestamp = _now().isoformat()
        with connection:
            fields = {
                "paused": "paused_at_utc",
                "cancelled": "cancelled_at_utc",
            }
            column = fields.get(new_state)
            if column:
                connection.execute(
                    f"""UPDATE baseline_versions SET lifecycle_state = ?,
                    learning_state = 'inactive', {column} = ? WHERE id = ?""",
                    (new_state, timestamp, version["id"]),
                )
            else:
                connection.execute(
                    "UPDATE baseline_versions SET lifecycle_state = ?, paused_at_utc = NULL WHERE id = ?",
                    (new_state, version["id"]),
                )
            _event(
                connection, int(version["id"]), event_type,
                version["lifecycle_state"], new_state, reason,
            )
    if new_state == "candidate":
        refresh_candidate(path, device_id=device_id, force=True)
    return baseline_management_status(path, device_id=device_id)


def pause_recalibration(path: Path | None, *, device_id: str) -> dict[str, Any]:
    database_path = initialize_database(path or get_database_path())
    with database_connection(database_path) as connection:
        version = connection.execute(
            """SELECT * FROM baseline_versions WHERE device_id = ?
            AND lifecycle_state IN ('candidate', 'ready')
            AND learning_state = 'collecting'
            ORDER BY version_number DESC LIMIT 1""",
            (device_id,),
        ).fetchone()
        if version is None:
            raise ValueError("No actively collecting recalibration is available.")
        new_lifecycle = (
            "paused" if version["lifecycle_state"] == "candidate" else "ready"
        )
        timestamp = _now().isoformat()
        with connection:
            connection.execute(
                """UPDATE baseline_versions SET lifecycle_state = ?,
                learning_state = 'paused', paused_at_utc = ? WHERE id = ?""",
                (new_lifecycle, timestamp, version["id"]),
            )
            _event(
                connection, int(version["id"]), "paused",
                version["lifecycle_state"], new_lifecycle,
                "user_paused_recalibration",
                {"previous_learning_state": "collecting", "learning_state": "paused"},
            )
    return baseline_management_status(database_path, device_id=device_id)


def resume_recalibration(path: Path | None, *, device_id: str) -> dict[str, Any]:
    database_path = initialize_database(path or get_database_path())
    with database_connection(database_path) as connection:
        version = connection.execute(
            """SELECT * FROM baseline_versions WHERE device_id = ?
            AND learning_state = 'paused'
            AND lifecycle_state IN ('paused', 'ready')
            ORDER BY version_number DESC LIMIT 1""",
            (device_id,),
        ).fetchone()
        if version is None:
            raise ValueError("No paused recalibration is available.")
        new_lifecycle = (
            "candidate" if version["lifecycle_state"] == "paused" else "ready"
        )
        with connection:
            connection.execute(
                """UPDATE baseline_versions SET lifecycle_state = ?,
                learning_state = 'collecting', paused_at_utc = NULL WHERE id = ?""",
                (new_lifecycle, version["id"]),
            )
            _event(
                connection, int(version["id"]), "resumed",
                version["lifecycle_state"], new_lifecycle,
                "user_resumed_recalibration",
                {"previous_learning_state": "paused", "learning_state": "collecting"},
            )
    return baseline_management_status(database_path, device_id=device_id)


def continue_recalibration(path: Path | None, *, device_id: str) -> dict[str, Any]:
    """Continue a ready candidate without creating or activating a version."""
    database_path = initialize_database(path or get_database_path())
    with database_connection(database_path) as connection:
        version = connection.execute(
            """SELECT * FROM baseline_versions WHERE device_id = ?
            AND lifecycle_state = 'ready'
            AND learning_state = 'ready_for_validation'
            ORDER BY version_number DESC LIMIT 1""",
            (device_id,),
        ).fetchone()
        if version is None:
            raise ValueError("No ready candidate is available to continue.")
        with connection:
            connection.execute(
                """UPDATE baseline_versions SET learning_state = 'collecting',
                paused_at_utc = NULL WHERE id = ?""",
                (version["id"],),
            )
            _event(
                connection, int(version["id"]), "continued",
                "ready", "ready", "user_continued_ready_candidate",
                {
                    "candidate_version": int(version["version_number"]),
                    "original_learning_started_at_utc": version[
                        "learning_started_at_utc"
                    ],
                    "learning_state": "collecting",
                },
            )
    return baseline_management_status(database_path, device_id=device_id)


def cancel_recalibration(path: Path | None, *, device_id: str) -> dict[str, Any]:
    return _change_state(
        path, device_id=device_id, expected=("candidate", "paused", "ready"),
        new_state="cancelled", event_type="cancelled",
        reason="user_cancelled_recalibration",
    )


def _copy_profile_to_runtime_projection(
    connection: sqlite3.Connection,
    version_profile: sqlite3.Row,
    version: sqlite3.Row,
    timestamp: str,
) -> int:
    scope = version_profile["workload_scope"]
    connection.execute(
        """INSERT INTO baseline_profiles (
        device_id, workload_scope, training_start_utc, training_end_utc,
        eligible_window_count, excluded_window_count, distinct_day_count,
        readiness_state, algorithm_version, configuration_version,
        feature_names_json, missing_features_json, created_at_utc, updated_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'established', ?, ?, ?, ?, ?, ?)
        ON CONFLICT(device_id, workload_scope) DO UPDATE SET
        training_start_utc=excluded.training_start_utc,
        training_end_utc=excluded.training_end_utc,
        eligible_window_count=excluded.eligible_window_count,
        excluded_window_count=excluded.excluded_window_count,
        distinct_day_count=excluded.distinct_day_count,
        readiness_state=excluded.readiness_state,
        algorithm_version=excluded.algorithm_version,
        configuration_version=excluded.configuration_version,
        feature_names_json=excluded.feature_names_json,
        missing_features_json=excluded.missing_features_json,
        updated_at_utc=excluded.updated_at_utc""",
        (
            version["device_id"], scope, version_profile["training_start_utc"],
            version_profile["training_end_utc"],
            version_profile["eligible_window_count"],
            version_profile["excluded_window_count"],
            version_profile["distinct_day_count"], ALGORITHM_VERSION,
            CONFIGURATION_VERSION, version_profile["feature_names_json"],
            version_profile["missing_features_json"], timestamp, timestamp,
        ),
    )
    baseline_id = int(connection.execute(
        "SELECT id FROM baseline_profiles WHERE device_id = ? AND workload_scope = ?",
        (version["device_id"], scope),
    ).fetchone()[0])
    connection.execute("DELETE FROM baseline_feature_stats WHERE baseline_id = ?", (baseline_id,))
    stats = connection.execute(
        "SELECT * FROM baseline_version_feature_stats WHERE version_profile_id = ?",
        (version_profile["id"],),
    ).fetchall()
    columns = [
        "feature_name", "direction", "valid_count", "missing_count",
        "missing_ratio", "mean", "population_stddev", "median",
        "median_absolute_deviation", "minimum", "maximum", "percentile_05",
        "percentile_25", "percentile_75", "percentile_95", "interquartile_range",
    ]
    for stat in stats:
        connection.execute(
            f"""INSERT INTO baseline_feature_stats
            (baseline_id, {', '.join(columns)})
            VALUES (?, {', '.join('?' for _ in columns)})""",
            (baseline_id, *(stat[column] for column in columns)),
        )
    connection.execute("DELETE FROM baseline_training_windows WHERE baseline_id = ?", (baseline_id,))
    connection.execute(
        """INSERT INTO baseline_training_windows (baseline_id, feature_window_id)
        SELECT ?, feature_window_id FROM baseline_version_training_windows
        WHERE version_profile_id = ?""",
        (baseline_id, version_profile["id"]),
    )
    connection.execute(
        "UPDATE baseline_version_profiles SET legacy_baseline_id = ? WHERE id = ?",
        (baseline_id, version_profile["id"]),
    )
    return baseline_id


def activate_candidate(
    database_path: Path | None = None,
    *,
    device_id: str,
) -> dict[str, Any]:
    path = initialize_database(database_path or get_database_path())
    with database_connection(path) as connection:
        version = connection.execute(
            """SELECT * FROM baseline_versions WHERE device_id = ?
            AND lifecycle_state = 'ready' ORDER BY version_number DESC LIMIT 1""",
            (device_id,),
        ).fetchone()
        if version is None:
            raise ValueError("The candidate baseline is not ready for activation.")
        profiles = connection.execute(
            """SELECT * FROM baseline_version_profiles
            WHERE baseline_version_id = ? ORDER BY workload_scope""",
            (version["id"],),
        ).fetchall()
        if not any(
            row["workload_scope"] == DEVICE_SCOPE and row["readiness_state"] == "ready"
            for row in profiles
        ):
            raise ValueError("The device-wide candidate profile is not ready.")
        timestamp = _now().isoformat()
        with connection:
            previous = connection.execute(
                "SELECT * FROM baseline_versions WHERE device_id = ? AND lifecycle_state = 'active'",
                (device_id,),
            ).fetchone()
            # Remove stale workload profiles from the active runtime projection
            # without deleting them. Their immutable data remains in the prior
            # version snapshot and rollback can restore it.
            ready_scopes = {
                row["workload_scope"]
                for row in profiles
                if row["readiness_state"] == "ready"
            }
            for row in connection.execute(
                "SELECT id, workload_scope FROM baseline_profiles WHERE device_id = ?",
                (device_id,),
            ).fetchall():
                if row["workload_scope"] not in ready_scopes:
                    connection.execute(
                        """UPDATE baseline_profiles
                        SET readiness_state = 'collecting_data', updated_at_utc = ?
                        WHERE id = ?""",
                        (timestamp, row["id"]),
                    )
            for profile in profiles:
                if profile["readiness_state"] == "ready":
                    _copy_profile_to_runtime_projection(connection, profile, version, timestamp)
            connection.execute(
                """UPDATE baseline_versions SET lifecycle_state = 'archived',
                learning_state = 'inactive'
                WHERE device_id = ? AND lifecycle_state = 'active'""",
                (device_id,),
            )
            connection.execute(
                """UPDATE baseline_versions SET lifecycle_state = 'active',
                learning_state = 'inactive', activated_at_utc = ? WHERE id = ?""",
                (timestamp, version["id"]),
            )
            _event(
                connection, int(version["id"]), "activated", "ready", "active",
                "user_activated_ready_candidate",
                {"previous_version_id": previous["id"] if previous else None},
            )
    return baseline_management_status(path, device_id=device_id)


def rollback_baseline(
    database_path: Path | None = None,
    *,
    device_id: str,
) -> dict[str, Any]:
    path = initialize_database(database_path or get_database_path())
    with database_connection(path) as connection:
        active = connection.execute(
            "SELECT * FROM baseline_versions WHERE device_id = ? AND lifecycle_state = 'active'",
            (device_id,),
        ).fetchone()
        if active is None or active["previous_version_id"] is None:
            raise ValueError("No previous baseline version is available for rollback.")
        previous = connection.execute(
            "SELECT * FROM baseline_versions WHERE id = ?",
            (active["previous_version_id"],),
        ).fetchone()
        profiles = connection.execute(
            """SELECT * FROM baseline_version_profiles
            WHERE baseline_version_id = ? AND readiness_state IN ('ready', 'established')""",
            (previous["id"],),
        ).fetchall()
        timestamp = _now().isoformat()
        with connection:
            restored_scopes = {row["workload_scope"] for row in profiles}
            for row in connection.execute(
                "SELECT id, workload_scope FROM baseline_profiles WHERE device_id = ?",
                (device_id,),
            ).fetchall():
                if row["workload_scope"] not in restored_scopes:
                    connection.execute(
                        """UPDATE baseline_profiles
                        SET readiness_state = 'collecting_data', updated_at_utc = ?
                        WHERE id = ?""",
                        (timestamp, row["id"]),
                    )
            for profile in profiles:
                _copy_profile_to_runtime_projection(connection, profile, previous, timestamp)
            connection.execute(
                """UPDATE baseline_versions SET lifecycle_state = 'archived',
                learning_state = 'inactive' WHERE id = ?""",
                (active["id"],),
            )
            connection.execute(
                """UPDATE baseline_versions SET lifecycle_state = 'active',
                learning_state = 'inactive', activated_at_utc = ? WHERE id = ?""",
                (timestamp, previous["id"]),
            )
            _event(
                connection, int(previous["id"]), "rollback_activated",
                previous["lifecycle_state"], "active", "user_requested_safe_rollback",
                {"replaced_version_id": active["id"]},
            )
    return baseline_management_status(path, device_id=device_id)


def _decode_version(connection: sqlite3.Connection, row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    item["reason_codes"] = json.loads(item.pop("reason_codes_json") or "[]")
    profiles = []
    for profile in connection.execute(
        """SELECT * FROM baseline_version_profiles
        WHERE baseline_version_id = ? ORDER BY workload_scope""",
        (item["id"],),
    ):
        decoded = dict(profile)
        decoded["reason_codes"] = json.loads(decoded.pop("reason_codes_json") or "[]")
        decoded["feature_names"] = json.loads(decoded.pop("feature_names_json") or "[]")
        decoded["missing_features"] = json.loads(decoded.pop("missing_features_json") or "[]")
        decoded["applicability_reasons"] = json.loads(
            decoded.pop("applicability_reasons_json", None) or "[]"
        )
        decoded["exclusion_reason_counts"] = json.loads(
            decoded.pop("exclusion_reason_counts_json", None) or "{}"
        )
        decoded["informational_reason_codes"] = [
            reason for reason in decoded["reason_codes"]
            if reason == "some_windows_excluded_by_quality_or_event_policy"
        ]
        decoded["blocking_reason_codes"] = [
            reason for reason in decoded["reason_codes"]
            if reason not in {
                "some_windows_excluded_by_quality_or_event_policy",
                "workload_not_observed_since_recalibration_started",
            }
        ]
        blockers = set(decoded["blocking_reason_codes"])
        if {
            "eligible_window_count_below_minimum",
            "distinct_collection_days_below_minimum",
        } <= blockers:
            decoded["blocking_reason"] = "both_requirements_pending"
        elif "eligible_window_count_below_minimum" in blockers:
            decoded["blocking_reason"] = "eligible_window_count_below_minimum"
        elif "distinct_collection_days_below_minimum" in blockers:
            decoded["blocking_reason"] = "distinct_collection_days_below_minimum"
        elif decoded["readiness_state"] == "not_observed":
            decoded["blocking_reason"] = "none_not_required"
        else:
            decoded["blocking_reason"] = "none"
        audit = connection.execute(
            """SELECT
            SUM(event_type IN ('accepted','reaccepted_after_repair')) AS accepted_events,
            SUM(event_type='removed_after_correction') AS removed_events,
            SUM(event_type='reaccepted_after_repair') AS reaccepted_events
            FROM baseline_training_membership_events WHERE version_profile_id=?""",
            (decoded["id"],),
        ).fetchone()
        decoded["membership_audit"] = {
            "historical_acceptance_events": int(audit["accepted_events"] or 0),
            "audited_removal_events": int(audit["removed_events"] or 0),
            "reacceptance_events": int(audit["reaccepted_events"] or 0),
        }
        profiles.append(decoded)
    item["profiles"] = profiles
    item["last_new_eligible_window_utc"] = connection.execute(
        """SELECT MAX(training_end_utc) FROM baseline_version_profiles
        WHERE baseline_version_id = ?""",
        (item["id"],),
    ).fetchone()[0]
    learning_state = item.get("learning_state") or "inactive"
    if learning_state == "collecting":
        item["learning_state_explanation"] = (
            f"Calibration is continuing. Candidate v{item['version_number']} "
            "is not yet active."
        )
    elif learning_state == "paused":
        item["learning_state_explanation"] = (
            "Calibration is paused. Existing candidate data is preserved."
        )
    elif item["lifecycle_state"] == "ready":
        item["learning_state_explanation"] = (
            f"Candidate v{item['version_number']} is ready for validation but "
            "has not replaced the active baseline."
        )
    else:
        item["learning_state_explanation"] = "Baseline learning is not active."
    return item


def baseline_management_status(
    database_path: Path | None = None,
    *,
    device_id: str | None = None,
) -> dict[str, Any]:
    path = initialize_database(database_path or get_database_path())
    with database_connection(path) as connection:
        resolved = device_id
        if resolved is None:
            row = connection.execute(
                "SELECT device_id FROM metrics ORDER BY timestamp_utc DESC, id DESC LIMIT 1"
            ).fetchone()
            if row is None:
                row = connection.execute(
                    """SELECT device_id FROM feature_windows
                    ORDER BY window_end_utc DESC, id DESC LIMIT 1"""
                ).fetchone()
            resolved = row[0] if row else None
        active = connection.execute(
            """SELECT * FROM baseline_versions WHERE device_id = ?
            AND lifecycle_state = 'active'""",
            (resolved,),
        ).fetchone() if resolved else None
        candidate = connection.execute(
            """SELECT * FROM baseline_versions WHERE device_id = ?
            AND lifecycle_state IN ('candidate', 'paused', 'ready')
            ORDER BY version_number DESC LIMIT 1""",
            (resolved,),
        ).fetchone() if resolved else None
        history = [
            _decode_version(connection, row)
            for row in connection.execute(
                """SELECT * FROM baseline_versions WHERE device_id = ?
                ORDER BY version_number DESC""",
                (resolved,),
            )
        ] if resolved else []
        decoded_active = _decode_version(connection, active)
        decoded_candidate = _decode_version(connection, candidate)
        if (
            decoded_candidate
            and decoded_candidate["lifecycle_state"] == "ready"
            and decoded_candidate["learning_state"] == "ready_for_validation"
        ):
            active_label = (
                f"active baseline v{decoded_active['version_number']}"
                if decoded_active else "the active baseline"
            )
            decoded_candidate["learning_state_explanation"] = (
                f"Candidate v{decoded_candidate['version_number']} is ready for "
                f"validation but has not replaced {active_label}."
            )
        return {
            "device_id": resolved,
            "active_version": decoded_active,
            "candidate_version": decoded_candidate,
            "version_history": history,
            "minimum_device_windows": DEFAULT_POLICY.minimum_device_windows,
            "minimum_workload_windows": DEFAULT_POLICY.minimum_workload_windows,
            "minimum_distinct_days": DEFAULT_POLICY.minimum_distinct_days,
            "rule_version": RULE_VERSION,
            "automatic_start": False,
            # One healthy baseline is normally sufficient, but the owner may
            # explicitly start a new, isolated candidate after a material
            # hardware/system/usage change. GET requests never start it.
            "calibration_available": decoded_active is not None and decoded_candidate is None,
            "calibration_state": (
                "not_applicable_no_calibration_running"
                if decoded_active
                and int(decoded_active["version_number"]) >= 2
                and decoded_candidate is None
                else "candidate_or_initial_calibration"
            ),
            "guidance": (
                "Your personal baseline is healthy. No recalibration is required. "
                "A new calibration remains an optional user-controlled action."
                if decoded_active and int(decoded_active["version_number"]) >= 2
                and decoded_candidate is None else
                "Use the computer normally and safely while the existing candidate collects."
            ),
            "accuracy_limitation": (
                "Recalibration adapts the learned operating pattern; it does not by "
                "itself establish improved predictive accuracy."
            ),
        }


def maybe_refresh_candidate(database_path: Path | None = None) -> None:
    """Refresh only a user-started candidate, at most once per five minutes."""
    path = initialize_database(database_path or get_database_path())
    with database_connection(path) as connection:
        row = connection.execute(
            """SELECT device_id, last_learning_at_utc FROM baseline_versions
            WHERE lifecycle_state IN ('candidate', 'ready')
              AND learning_state = 'collecting'
            ORDER BY id DESC LIMIT 1"""
        ).fetchone()
    if row is None:
        return
    if row["last_learning_at_utc"]:
        last = datetime.fromisoformat(row["last_learning_at_utc"])
        if _now() - last < timedelta(minutes=5):
            return
    refresh_candidate(path, device_id=row["device_id"])
