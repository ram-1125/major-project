"""Device-specific robust baselines and transparent deviation evaluation."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.config import get_database_path
from analytics.aggregate import percentile
from analytics.workload import GUIDED_DEVELOPMENT_CONTEXT
from analytics.baseline_config import (
    ALGORITHM_VERSION,
    CONFIGURATION_VERSION,
    CONTEXTUAL_HIGH_CPU_WORKLOADS,
    DEFAULT_POLICY,
    DEVICE_SCOPE,
    FEATURE_DEFINITIONS,
    ISOLATION_VERSION,
    SEVERITY_THRESHOLDS,
    BaselinePolicy,
)
from backend.database import database_connection, initialize_database


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def window_eligibility(
    window: dict[str, Any],
    now: datetime | None = None,
    policy: BaselinePolicy = DEFAULT_POLICY,
) -> tuple[bool, list[str]]:
    """Return eligibility and structured exclusion reasons for one window."""
    reasons: list[str] = []
    current_time = now or _utc_now()
    if not bool(window.get("is_complete")):
        reasons.append("incomplete_window")
    coverage = _number(window.get("coverage_ratio"))
    if coverage is None or not 0 <= coverage <= 1:
        reasons.append("invalid_coverage")
    elif coverage < policy.minimum_coverage:
        reasons.append("coverage_below_minimum")
    try:
        end = datetime.fromisoformat(str(window["window_end_utc"]))
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        if end > current_time:
            reasons.append("currently_open_window")
    except (KeyError, TypeError, ValueError):
        reasons.append("invalid_window_timestamp")

    valid_features = sum(
        _number(window.get(name)) is not None for name in FEATURE_DEFINITIONS
    )
    if valid_features < policy.minimum_valid_features:
        reasons.append("insufficient_valid_values")
    if policy.exclude_critical_events and (window.get("critical_event_count") or 0) > 0:
        reasons.append("critical_event_excluded")
    if policy.exclude_serious_events and any(
        (window.get(name) or 0) > 0
        for name in (
            "hardware_event_count",
            "storage_event_count",
            "power_event_count",
        )
    ):
        reasons.append("serious_event_excluded")
    return not reasons, reasons


def robust_statistics(
    values: list[float],
    total_count: int,
    direction: str,
) -> dict[str, Any]:
    """Calculate deterministic population and robust distribution statistics."""
    if not values:
        return {
            "direction": direction,
            "valid_count": 0,
            "missing_count": total_count,
            "missing_ratio": 1.0 if total_count else 0.0,
            "mean": None,
            "population_stddev": None,
            "median": None,
            "median_absolute_deviation": None,
            "minimum": None,
            "maximum": None,
            "percentile_05": None,
            "percentile_25": None,
            "percentile_75": None,
            "percentile_95": None,
            "interquartile_range": None,
        }
    centre = statistics.median(values)
    absolute_deviations = [abs(value - centre) for value in values]
    p25 = percentile(values, 0.25)
    p75 = percentile(values, 0.75)
    return {
        "direction": direction,
        "valid_count": len(values),
        "missing_count": total_count - len(values),
        "missing_ratio": (total_count - len(values)) / total_count,
        "mean": statistics.fmean(values),
        "population_stddev": statistics.pstdev(values),
        "median": centre,
        "median_absolute_deviation": statistics.median(absolute_deviations),
        "minimum": min(values),
        "maximum": max(values),
        "percentile_05": percentile(values, 0.05),
        "percentile_25": p25,
        "percentile_75": p75,
        "percentile_95": percentile(values, 0.95),
        "interquartile_range": (
            p75 - p25 if p25 is not None and p75 is not None else None
        ),
    }


def readiness_state(
    eligible_count: int,
    distinct_days: int,
    workload_scope: str,
    policy: BaselinePolicy = DEFAULT_POLICY,
) -> str:
    minimum = (
        policy.minimum_device_windows
        if workload_scope == DEVICE_SCOPE
        else policy.minimum_workload_windows
    )
    if eligible_count < minimum:
        return "collecting_data"
    if distinct_days < policy.minimum_distinct_days:
        return "provisional"
    return "established"


def _candidate_windows(
    connection,
    device_id: str,
    workload_scope: str,
) -> list[dict[str, Any]]:
    where = ["device_id = ?"]
    parameters: list[Any] = [device_id]
    if workload_scope != DEVICE_SCOPE:
        where.append(
            "secondary_workload_context = ?"
            if workload_scope == GUIDED_DEVELOPMENT_CONTEXT
            else "dominant_workload_class = ?"
        )
        parameters.append(workload_scope)
    return [
        dict(row)
        for row in connection.execute(
            f"""SELECT * FROM feature_windows
            WHERE {" AND ".join(where)}
            ORDER BY window_start_utc, id""",
            parameters,
        )
    ]


def _eligible_windows(
    connection,
    device_id: str,
    workload_scope: str,
    policy: BaselinePolicy,
    now: datetime,
) -> tuple[list[dict[str, Any]], int]:
    candidates = _candidate_windows(connection, device_id, workload_scope)
    previously_high = {
        row[0]
        for row in connection.execute(
            """SELECT feature_window_id FROM deviation_assessments
            WHERE device_id = ? AND overall_level = 'high'""",
            (device_id,),
        )
    }
    eligible = []
    for window in candidates:
        allowed, _ = window_eligibility(window, now, policy)
        if allowed and window["id"] not in previously_high:
            eligible.append(window)
    return eligible, len(candidates) - len(eligible)


def _model_metadata(
    rows: list[dict[str, Any]],
    stats: dict[str, dict[str, Any]],
    policy: BaselinePolicy,
) -> dict[str, Any]:
    feature_names = [
        name
        for name, definition in FEATURE_DEFINITIONS.items()
        if definition.isolation_forest
        and stats[name]["valid_count"] >= max(3, math.ceil(len(rows) * 0.8))
    ]
    try:
        import sklearn  # noqa: F401

        sklearn_available = True
    except ImportError:
        sklearn_available = False
    ready = (
        sklearn_available
        and len(rows) >= policy.isolation_minimum_windows
        and len(feature_names) >= 3
    )
    medians = {name: stats[name]["median"] for name in feature_names}
    return {
        "readiness_state": "ready" if ready else (
            "unavailable" if not sklearn_available else "collecting_data"
        ),
        "feature_names": feature_names,
        "preprocessing": {
            "strategy": "training_median",
            "medians": medians,
            "missing_values_are_not_zero": True,
        },
        "training_count": len(rows),
        "random_seed": policy.isolation_random_seed,
        "contamination": policy.isolation_contamination,
    }


def _write_profile(
    connection,
    device_id: str,
    workload_scope: str,
    rows: list[dict[str, Any]],
    excluded_count: int,
    policy: BaselinePolicy,
    now_text: str,
) -> int:
    distinct_days = len({row["window_start_utc"][:10] for row in rows})
    state = readiness_state(len(rows), distinct_days, workload_scope, policy)
    stats = {
        name: robust_statistics(
            [
                value
                for row in rows
                if (value := _number(row.get(name))) is not None
            ],
            len(rows),
            definition.direction,
        )
        for name, definition in FEATURE_DEFINITIONS.items()
    }
    missing_features = [
        name for name, feature_stats in stats.items()
        if feature_stats["valid_count"] == 0
    ]
    connection.execute(
        """INSERT INTO baseline_profiles (
            device_id, workload_scope, training_start_utc, training_end_utc,
            eligible_window_count, excluded_window_count, distinct_day_count,
            readiness_state, algorithm_version, configuration_version,
            feature_names_json, missing_features_json, created_at_utc, updated_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            device_id,
            workload_scope,
            rows[0]["window_start_utc"] if rows else None,
            rows[-1]["window_end_utc"] if rows else None,
            len(rows),
            excluded_count,
            distinct_days,
            state,
            ALGORITHM_VERSION,
            CONFIGURATION_VERSION,
            json.dumps(list(FEATURE_DEFINITIONS)),
            json.dumps(missing_features),
            now_text,
            now_text,
        ),
    )
    baseline_id = connection.execute(
        """SELECT id FROM baseline_profiles
        WHERE device_id = ? AND workload_scope = ?""",
        (device_id, workload_scope),
    ).fetchone()[0]
    connection.execute(
        "DELETE FROM baseline_feature_stats WHERE baseline_id = ?",
        (baseline_id,),
    )
    for name, feature_stats in stats.items():
        columns = ("baseline_id", "feature_name", *feature_stats.keys())
        connection.execute(
            f"""INSERT INTO baseline_feature_stats ({", ".join(columns)})
            VALUES ({", ".join("?" for _ in columns)})""",
            (baseline_id, name, *feature_stats.values()),
        )
    connection.execute(
        "DELETE FROM baseline_training_windows WHERE baseline_id = ?",
        (baseline_id,),
    )
    connection.executemany(
        """INSERT INTO baseline_training_windows (baseline_id, feature_window_id)
        VALUES (?, ?)""",
        [(baseline_id, row["id"]) for row in rows],
    )
    model = _model_metadata(rows, stats, policy)
    connection.execute(
        """INSERT INTO isolation_model_metadata (
            baseline_id, readiness_state, algorithm_version, feature_names_json,
            preprocessing_json, training_count, random_seed, contamination,
            created_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(baseline_id) DO UPDATE SET
            readiness_state=excluded.readiness_state,
            algorithm_version=excluded.algorithm_version,
            feature_names_json=excluded.feature_names_json,
            preprocessing_json=excluded.preprocessing_json,
            training_count=excluded.training_count,
            random_seed=excluded.random_seed,
            contamination=excluded.contamination,
            created_at_utc=excluded.created_at_utc""",
        (
            baseline_id,
            model["readiness_state"],
            ISOLATION_VERSION,
            json.dumps(model["feature_names"]),
            json.dumps(model["preprocessing"]),
            model["training_count"],
            model["random_seed"],
            model["contamination"],
            now_text,
        ),
    )
    return baseline_id


def train_database(
    database_path: Path | None = None,
    device_id: str | None = None,
    workload: str | None = None,
    policy: BaselinePolicy = DEFAULT_POLICY,
) -> list[dict[str, Any]]:
    """Transactionally rebuild requested profiles without touching raw history."""
    path = initialize_database(database_path or get_database_path())
    now = _utc_now()
    now_text = now.isoformat()
    with database_connection(path) as connection:
        devices = (
            [device_id]
            if device_id
            else [
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT device_id FROM feature_windows ORDER BY device_id"
                )
            ]
        )
        results: list[dict[str, Any]] = []
        for current_device in devices:
            if workload:
                scopes = [workload]
            else:
                scopes = [DEVICE_SCOPE]
                scopes.extend(
                    row[0]
                    for row in connection.execute(
                        """SELECT DISTINCT dominant_workload_class
                        FROM feature_windows
                        WHERE device_id = ? AND dominant_workload_class IS NOT NULL
                        ORDER BY dominant_workload_class""",
                        (current_device,),
                    )
                )
                if connection.execute(
                    """SELECT 1 FROM feature_windows
                    WHERE device_id = ? AND secondary_workload_context = ?
                    LIMIT 1""",
                    (current_device, GUIDED_DEVELOPMENT_CONTEXT),
                ).fetchone():
                    scopes.append(GUIDED_DEVELOPMENT_CONTEXT)
                scopes = list(dict.fromkeys(scopes))
            for scope in scopes:
                started = _utc_now().isoformat()
                rows, excluded = _eligible_windows(
                    connection, current_device, scope, policy, now
                )
                try:
                    with connection:
                        baseline_id = _write_profile(
                            connection,
                            current_device,
                            scope,
                            rows,
                            excluded,
                            policy,
                            now_text,
                        )
                        connection.execute(
                            """INSERT INTO baseline_training_runs (
                                device_id, workload_scope, started_at_utc,
                                finished_at_utc, status, eligible_window_count,
                                excluded_window_count
                            ) VALUES (?, ?, ?, ?, 'success', ?, ?)""",
                            (
                                current_device,
                                scope,
                                started,
                                _utc_now().isoformat(),
                                len(rows),
                                excluded,
                            ),
                        )
                    profile = get_profile(connection, baseline_id)
                    results.append(profile)
                except Exception as error:
                    connection.rollback()
                    with connection:
                        connection.execute(
                            """INSERT INTO baseline_training_runs (
                                device_id, workload_scope, started_at_utc,
                                finished_at_utc, status, eligible_window_count,
                                excluded_window_count, error_code
                            ) VALUES (?, ?, ?, ?, 'error', ?, ?, ?)""",
                            (
                                current_device,
                                scope,
                                started,
                                _utc_now().isoformat(),
                                len(rows),
                                excluded,
                                type(error).__name__,
                            ),
                        )
                    raise
        return results


def get_profile(connection, baseline_id: int) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM baseline_profiles WHERE id = ?", (baseline_id,)
    ).fetchone()
    if row is None:
        raise ValueError("Baseline profile does not exist.")
    profile = dict(row)
    profile["feature_names"] = json.loads(profile.pop("feature_names_json"))
    profile["missing_features"] = json.loads(profile.pop("missing_features_json"))
    model = connection.execute(
        "SELECT * FROM isolation_model_metadata WHERE baseline_id = ?",
        (baseline_id,),
    ).fetchone()
    profile["isolation_forest"] = (
        {
            **dict(model),
            "feature_names": json.loads(model["feature_names_json"]),
            "preprocessing": json.loads(model["preprocessing_json"]),
        }
        if model
        else {"readiness_state": "unavailable"}
    )
    if model:
        profile["isolation_forest"].pop("feature_names_json")
        profile["isolation_forest"].pop("preprocessing_json")
    return profile


def baseline_status(
    database_path: Path | None = None,
    device_id: str | None = None,
    policy: BaselinePolicy = DEFAULT_POLICY,
) -> dict[str, Any]:
    path = initialize_database(database_path or get_database_path())
    with database_connection(path) as connection:
        resolved_device = device_id
        if resolved_device is None:
            row = connection.execute(
                """SELECT device_id FROM feature_windows
                ORDER BY window_start_utc DESC LIMIT 1"""
            ).fetchone()
            resolved_device = row[0] if row else None
        if resolved_device is None:
            return {
                "state": "unavailable",
                "device_id": None,
                "eligible_window_count": 0,
                "distinct_collection_days": 0,
                "minimum_device_windows": policy.minimum_device_windows,
                "minimum_workload_windows": policy.minimum_workload_windows,
                "minimum_distinct_days": policy.minimum_distinct_days,
                "recommended_history_days": policy.recommended_days,
                "preferred_history_days": policy.preferred_days,
                "device_profile": None,
                "workload_profiles": [],
                "last_successful_training_utc": None,
                "last_training_status": None,
                "last_training_error_code": None,
            }
        rows, _ = _eligible_windows(
            connection, resolved_device, DEVICE_SCOPE, policy, _utc_now()
        )
        profiles = [
            get_profile(connection, row["id"])
            for row in connection.execute(
                """SELECT id FROM baseline_profiles
                WHERE device_id = ? ORDER BY workload_scope""",
                (resolved_device,),
            )
        ]
        device_profile = next(
            (item for item in profiles if item["workload_scope"] == DEVICE_SCOPE),
            None,
        )
        last_run = connection.execute(
            """SELECT MAX(finished_at_utc) FROM baseline_training_runs
            WHERE device_id = ? AND status = 'success'""",
            (resolved_device,),
        ).fetchone()[0]
        latest_run = connection.execute(
            """SELECT status, error_code FROM baseline_training_runs
            WHERE device_id = ? ORDER BY id DESC LIMIT 1""",
            (resolved_device,),
        ).fetchone()
        active_version = connection.execute(
            """SELECT id,version_number FROM baseline_versions
            WHERE device_id=? AND lifecycle_state='active'
            ORDER BY version_number DESC LIMIT 1""",
            (resolved_device,),
        ).fetchone()
        candidate_version = connection.execute(
            """SELECT id FROM baseline_versions WHERE device_id=?
            AND lifecycle_state IN ('candidate','paused','ready') LIMIT 1""",
            (resolved_device,),
        ).fetchone()
        state = (
            device_profile["readiness_state"]
            if device_profile
            else readiness_state(
                len(rows),
                len({row["window_start_utc"][:10] for row in rows}),
                DEVICE_SCOPE,
                policy,
            )
        )
        if active_version and int(active_version["version_number"]) >= 2 and candidate_version is None:
            # A completed permanent calibration does not become stale merely
            # because no recurring candidate is running.
            state = "established"
        elif device_profile and last_run:
            last_time = datetime.fromisoformat(last_run)
            if (_utc_now() - last_time).days >= policy.stale_after_days:
                state = "stale"
        elif device_profile is None and latest_run and latest_run["status"] == "error":
            state = "error"
        return {
            "state": state,
            "device_id": resolved_device,
            "eligible_window_count": len(rows),
            "distinct_collection_days": len(
                {row["window_start_utc"][:10] for row in rows}
            ),
            "minimum_device_windows": policy.minimum_device_windows,
            "minimum_workload_windows": policy.minimum_workload_windows,
            "minimum_distinct_days": policy.minimum_distinct_days,
            "recommended_history_days": policy.recommended_days,
            "preferred_history_days": policy.preferred_days,
            "device_profile": device_profile,
            "workload_profiles": [
                item for item in profiles if item["workload_scope"] != DEVICE_SCOPE
            ],
            "last_successful_training_utc": last_run,
            "last_training_status": latest_run["status"] if latest_run else None,
            "last_training_error_code": latest_run["error_code"] if latest_run else None,
            "calibration_state": (
                "not_applicable_no_calibration_running"
                if active_version and int(active_version["version_number"]) >= 2
                and candidate_version is None else "candidate_or_initial_calibration"
            ),
        }


def _feature_stats(connection, baseline_id: int) -> dict[str, dict[str, Any]]:
    return {
        row["feature_name"]: dict(row)
        for row in connection.execute(
            "SELECT * FROM baseline_feature_stats WHERE baseline_id = ?",
            (baseline_id,),
        )
    }


def robust_deviation(
    observed: float,
    feature_stats: dict[str, Any],
) -> tuple[float, str, float]:
    """Return normalized score, direction, and absolute magnitude."""
    centre = feature_stats["median"]
    if centre is None:
        return 0.0, "not_evaluated", 0.0
    magnitude = abs(observed - centre)
    direction = "above" if observed > centre else "below" if observed < centre else "within"
    mad = feature_stats["median_absolute_deviation"] or 0.0
    iqr = feature_stats["interquartile_range"] or 0.0
    stddev = feature_stats["population_stddev"] or 0.0
    if mad > 1e-9:
        score = magnitude / (1.4826 * mad)
    elif iqr > 1e-9:
        q25, q75 = feature_stats["percentile_25"], feature_stats["percentile_75"]
        outside = max(q25 - observed, observed - q75, 0.0)
        score = outside / iqr
    elif stddev > 1e-9:
        score = magnitude / stddev
    else:
        score = 0.0 if magnitude <= 1e-9 else 5.0
    if feature_stats["direction"] == "higher" and direction != "above":
        score = 0.0
    return score, direction, magnitude


def severity_for_score(score: float) -> str:
    if score >= SEVERITY_THRESHOLDS["high"]:
        return "high"
    if score >= SEVERITY_THRESHOLDS["elevated"]:
        return "elevated"
    if score >= SEVERITY_THRESHOLDS["mild"]:
        return "mild"
    return "normal"


def _reason_code(
    feature_name: str,
    direction: str,
    workload: str | None,
    scope: str,
    severity: str,
) -> str:
    if (
        feature_name.startswith("cpu_")
        and workload in CONTEXTUAL_HIGH_CPU_WORKLOADS
        and scope == "device"
        and direction == "above"
    ):
        return f"cpu_peak_expected_for_{workload}_workload"
    suffix = "above" if direction == "above" else "below"
    readable_scope = workload if scope == "workload" and workload else "device"
    return f"{feature_name}_{suffix}_{readable_scope}_baseline" if severity != "normal" else "within_expected_range"


def _isolation_result(
    connection,
    baseline_id: int,
    target: dict[str, Any],
) -> tuple[float | None, str]:
    metadata = connection.execute(
        "SELECT * FROM isolation_model_metadata WHERE baseline_id = ?",
        (baseline_id,),
    ).fetchone()
    if metadata is None or metadata["readiness_state"] != "ready":
        return None, "not_evaluated"
    try:
        from sklearn.ensemble import IsolationForest
    except ImportError:
        return None, "not_evaluated"
    feature_names = json.loads(metadata["feature_names_json"])
    preprocessing = json.loads(metadata["preprocessing_json"])
    medians = preprocessing["medians"]
    training_rows = [
        dict(row)
        for row in connection.execute(
            """SELECT f.* FROM feature_windows f
            JOIN baseline_training_windows t ON t.feature_window_id = f.id
            WHERE t.baseline_id = ? ORDER BY f.id""",
            (baseline_id,),
        )
    ]
    if target["id"] in {row["id"] for row in training_rows}:
        return None, "not_evaluated"
    matrix = [
        [
            _number(row.get(name))
            if _number(row.get(name)) is not None
            else medians[name]
            for name in feature_names
        ]
        for row in training_rows
    ]
    target_values = [[
        _number(target.get(name))
        if _number(target.get(name)) is not None
        else medians[name]
        for name in feature_names
    ]]
    model = IsolationForest(
        contamination=metadata["contamination"],
        random_state=metadata["random_seed"],
        n_estimators=100,
    )
    model.fit(matrix)
    score = float(-model.decision_function(target_values)[0])
    result = "unusual" if int(model.predict(target_values)[0]) == -1 else "typical"
    return score, result


def _choose_profile(connection, window: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    secondary_context = window.get("secondary_workload_context")
    if secondary_context:
        row = connection.execute(
            """SELECT id FROM baseline_profiles
            WHERE device_id = ? AND workload_scope = ?
            AND readiness_state IN ('provisional', 'established')""",
            (window["device_id"], secondary_context),
        ).fetchone()
        if row:
            return get_profile(connection, row[0]), "guided_workload"
    workload = window.get("dominant_workload_class")
    if workload:
        row = connection.execute(
            """SELECT id FROM baseline_profiles
            WHERE device_id = ? AND workload_scope = ?
            AND readiness_state IN ('provisional', 'established')""",
            (window["device_id"], workload),
        ).fetchone()
        if row:
            return get_profile(connection, row[0]), "workload"
    row = connection.execute(
        """SELECT id FROM baseline_profiles
        WHERE device_id = ? AND workload_scope = ?
        AND readiness_state IN ('provisional', 'established')""",
        (window["device_id"], DEVICE_SCOPE),
    ).fetchone()
    return (get_profile(connection, row[0]), "device") if row else (None, "none")


def _evaluate_window(
    connection,
    window: dict[str, Any],
    profile: dict[str, Any],
    scope: str,
) -> int:
    version = connection.execute(
        """SELECT id, configuration_version FROM baseline_versions
        WHERE device_id = ? AND lifecycle_state = 'active'
        ORDER BY version_number DESC LIMIT 1""",
        (window["device_id"],),
    ).fetchone()
    stats = _feature_stats(connection, profile["id"])
    workload = window.get("dominant_workload_class")
    results: list[dict[str, Any]] = []
    weighted_scores: list[tuple[float, float]] = []
    for name, definition in FEATURE_DEFINITIONS.items():
        observed = _number(window.get(name))
        feature_stats = stats.get(name)
        if observed is None or not feature_stats or feature_stats["valid_count"] == 0:
            results.append({
                "feature_name": name,
                "observed_value": observed,
                "baseline_centre": feature_stats["median"] if feature_stats else None,
                "expected_low": feature_stats["percentile_05"] if feature_stats else None,
                "expected_high": feature_stats["percentile_95"] if feature_stats else None,
                "deviation_direction": "not_evaluated",
                "deviation_magnitude": None,
                "normalized_deviation_score": None,
                "severity_band": "not_evaluated",
                "reason_code": "optional_sensor_not_evaluated" if definition.optional else "metric_not_evaluated",
                "baseline_scope": scope,
            })
            continue
        score, direction, magnitude = robust_deviation(observed, feature_stats)
        if (
            name.startswith("cpu_")
            and workload in CONTEXTUAL_HIGH_CPU_WORKLOADS
            and scope == "device"
        ):
            score = min(score, SEVERITY_THRESHOLDS["elevated"] - 0.01)
        severity = severity_for_score(score)
        reason = _reason_code(name, direction, workload, scope, severity)
        results.append({
            "feature_name": name,
            "observed_value": observed,
            "baseline_centre": feature_stats["median"],
            "expected_low": feature_stats["percentile_05"],
            "expected_high": feature_stats["percentile_95"],
            "deviation_direction": direction,
            "deviation_magnitude": magnitude,
            "normalized_deviation_score": score,
            "severity_band": severity,
            "reason_code": reason,
            "baseline_scope": scope,
        })
        weighted_scores.append((score, definition.weight))

    evaluated_count = len(weighted_scores)
    data_quality = "sufficient" if evaluated_count >= 3 else "insufficient"
    statistical_index = (
        min(
            100.0,
            sum(min(score, 5.0) * weight for score, weight in weighted_scores)
            / sum(weight for _, weight in weighted_scores)
            / 5.0
            * 100.0,
        )
        if data_quality == "sufficient"
        else None
    )
    isolation_score, isolation_result = _isolation_result(
        connection, profile["id"], window
    )
    deviation_index = statistical_index
    if deviation_index is not None and isolation_result == "unusual":
        deviation_index = min(100.0, deviation_index * 0.8 + 20.0)
    overall = (
        "not_evaluated"
        if deviation_index is None
        else "high" if deviation_index >= 70
        else "elevated" if deviation_index >= 40
        else "mild" if deviation_index >= 20
        else "normal"
    )
    ranked = sorted(
        (
            result for result in results
            if result["normalized_deviation_score"] is not None
        ),
        key=lambda result: result["normalized_deviation_score"],
        reverse=True,
    )
    top = [
        {
            "feature_name": result["feature_name"],
            "score": result["normalized_deviation_score"],
            "severity": result["severity_band"],
            "reason_code": result["reason_code"],
        }
        for result in ranked[:5]
    ]
    reason_codes = [
        item["reason_code"] for item in top
        if item["reason_code"] != "within_expected_range"
    ]
    event_context = {
        name: int(window.get(name) or 0)
        for name in (
            "critical_event_count",
            "hardware_event_count",
            "storage_event_count",
            "power_event_count",
            "application_crash_count",
            "service_failure_count",
            "resource_exhaustion_count",
        )
        if (window.get(name) or 0) > 0
    }
    if event_context.get("critical_event_count"):
        reason_codes.append("recent_critical_event_observed")
    statistical_summary = {
        "evaluated_feature_count": evaluated_count,
        "not_evaluated_feature_count": len(results) - evaluated_count,
        "statistical_deviation_index": statistical_index,
        "primary_workload_context": window.get("dominant_workload_class"),
        "secondary_workload_context": window.get("secondary_workload_context"),
        "workload_rule_version": window.get("workload_rule_version"),
        "baseline_profile_scope": profile.get("workload_scope"),
    }
    now_text = _utc_now().isoformat()
    connection.execute(
        """INSERT INTO deviation_assessments (
            feature_window_id, evaluation_timestamp_utc, device_id, baseline_id,
            baseline_scope, baseline_readiness, statistical_summary_json,
            isolation_forest_score, isolation_forest_result, deviation_index,
            overall_level, top_contributing_metrics_json, reason_codes_json,
            workload_context, relevant_event_context_json, data_quality_status,
            baseline_version_id, baseline_rule_version, workload_rule_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(feature_window_id) DO UPDATE SET
            evaluation_timestamp_utc=excluded.evaluation_timestamp_utc,
            baseline_id=excluded.baseline_id,
            baseline_scope=excluded.baseline_scope,
            baseline_readiness=excluded.baseline_readiness,
            statistical_summary_json=excluded.statistical_summary_json,
            isolation_forest_score=excluded.isolation_forest_score,
            isolation_forest_result=excluded.isolation_forest_result,
            deviation_index=excluded.deviation_index,
            overall_level=excluded.overall_level,
            top_contributing_metrics_json=excluded.top_contributing_metrics_json,
            reason_codes_json=excluded.reason_codes_json,
            workload_context=excluded.workload_context,
            relevant_event_context_json=excluded.relevant_event_context_json,
            data_quality_status=excluded.data_quality_status,
            baseline_version_id=excluded.baseline_version_id,
            baseline_rule_version=excluded.baseline_rule_version,
            workload_rule_version=excluded.workload_rule_version""",
        (
            window["id"], now_text, window["device_id"], profile["id"], scope,
            profile["readiness_state"], json.dumps(statistical_summary),
            isolation_score, isolation_result, deviation_index, overall,
            json.dumps(top), json.dumps(sorted(set(reason_codes))), workload,
            json.dumps(event_context), data_quality,
            version["id"] if version else None,
            version["configuration_version"] if version else "phase3a-v1",
            window.get("workload_rule_version"),
        ),
    )
    assessment_id = connection.execute(
        "SELECT id FROM deviation_assessments WHERE feature_window_id = ?",
        (window["id"],),
    ).fetchone()[0]
    connection.execute(
        "DELETE FROM deviation_feature_results WHERE assessment_id = ?",
        (assessment_id,),
    )
    for result in results:
        columns = ("assessment_id", *result.keys())
        connection.execute(
            f"""INSERT INTO deviation_feature_results ({", ".join(columns)})
            VALUES ({", ".join("?" for _ in columns)})""",
            (assessment_id, *result.values()),
        )
    return assessment_id


def evaluate_database(
    database_path: Path | None = None,
    device_id: str | None = None,
    force: bool = False,
    policy: BaselinePolicy = DEFAULT_POLICY,
) -> int:
    path = initialize_database(database_path or get_database_path())
    now = _utc_now()
    evaluated = 0
    with database_connection(path) as connection:
        where = ["1 = 1"]
        parameters: list[Any] = []
        if device_id:
            where.append("device_id = ?")
            parameters.append(device_id)
        windows = [
            dict(row)
            for row in connection.execute(
                f"""SELECT * FROM feature_windows
                WHERE {" AND ".join(where)}
                ORDER BY window_start_utc, id""",
                parameters,
            )
        ]
        for window in windows:
            eligible, _ = window_eligibility(window, now, policy)
            if not eligible:
                continue
            if not force and connection.execute(
                "SELECT 1 FROM deviation_assessments WHERE feature_window_id = ?",
                (window["id"],),
            ).fetchone():
                continue
            profile, scope = _choose_profile(connection, window)
            if profile is None:
                continue
            if connection.execute(
                """SELECT 1 FROM baseline_training_windows
                WHERE baseline_id = ? AND feature_window_id = ?""",
                (profile["id"], window["id"]),
            ).fetchone():
                continue
            with connection:
                _evaluate_window(connection, window, profile, scope)
            evaluated += 1
    return evaluated


def maybe_maintain_baselines(
    database_path: Path | None = None,
    policy: BaselinePolicy = DEFAULT_POLICY,
) -> None:
    """Cheap periodic gate used by the agent; training remains batch-controlled."""
    path = initialize_database(database_path or get_database_path())
    should_train_initial = False
    with database_connection(path) as connection:
        devices = [
            row[0] for row in connection.execute(
                "SELECT DISTINCT device_id FROM feature_windows"
            )
        ]
        for device in devices:
            profile = connection.execute(
                """SELECT id, updated_at_utc, eligible_window_count
                FROM baseline_profiles
                WHERE device_id = ? AND workload_scope = ?""",
                (device, DEVICE_SCOPE),
            ).fetchone()
            eligible, _ = _eligible_windows(
                connection, device, DEVICE_SCOPE, policy, _utc_now()
            )
            if profile is None:
                should_train_initial = True
                break
    if should_train_initial:
        train_database(path, policy=policy)
        from backend.phase7b1_repository import ensure_versioned_baseline_snapshot

        with database_connection(path) as connection, connection:
            ensure_versioned_baseline_snapshot(connection)
    # Existing active profiles are immutable in Phase 7B.1. Only a candidate
    # explicitly started in Settings is refreshed, and it cannot affect scoring
    # until the user activates it.
    from analytics.recalibration import maybe_refresh_candidate

    maybe_refresh_candidate(path)
    evaluate_database(path, policy=policy)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SmartOps local device baseline and deviation tools"
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--train", action="store_true", help="Rebuild baselines.")
    action.add_argument("--status", action="store_true", help="Show readiness.")
    action.add_argument(
        "--evaluate",
        action="store_true",
        help="Evaluate eligible windows not used for training.",
    )
    parser.add_argument("--device", help="Limit work to one local device ID.")
    parser.add_argument("--workload", help="Train only one workload scope.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-evaluate existing derived assessments.",
    )
    args = parser.parse_args()
    if args.train:
        profiles = train_database(
            device_id=args.device,
            workload=args.workload,
        )
        evaluated = evaluate_database(device_id=args.device)
        print(json.dumps({"profiles": profiles, "evaluated_windows": evaluated}, indent=2))
    elif args.evaluate:
        count = evaluate_database(device_id=args.device, force=args.force)
        print(json.dumps({"evaluated_windows": count}, indent=2))
    else:
        print(json.dumps(baseline_status(device_id=args.device), indent=2))


if __name__ == "__main__":
    main()
