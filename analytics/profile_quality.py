"""Fine-grained PC Quality detection and v2-anchored profile scoring.

This module never changes the calibrated workload label or baseline statistics.
It creates additive observations from executable names and completed telemetry
windows, then produces read-only suitability evidence for the dashboard.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from agent.config import get_database_path
from analytics.profile_quality_catalogue import (
    BASE_METRICS,
    BROAD_PROFILES,
    DETECTION_RULE_VERSION,
    FINE_PROFILES,
    INTERPRETATION,
    FULL_SCORE_EXPLANATION,
    GUIDE_EXPLANATION,
    MEASURE_NAME,
    RESEARCH_REFERENCES,
    SCORING_METHOD_VERSION,
    SEMANTIC_VERSION,
    TAXONOMY_VERSION,
    public_catalogue,
)
from backend.database import (
    database_connection,
    initialize_database,
    read_only_database_connection,
    run_write_transaction,
)
from backend.postcalibration_repository import get_profile_quality_assessments


MINIMUM_FOREGROUND_SAMPLES = 2
MINIMUM_FOREGROUND_PROPORTION = 0.20


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalise_process(value: str | None) -> str:
    return (value or "").strip().casefold()


def _history_depth_description(
    qualifying_count: int,
    observed_count: int,
    distinct_days: int,
) -> tuple[str, str]:
    """Return factual history wording; this is not a confidence estimate."""
    if qualifying_count == 0:
        return ("none", "Not observed" if observed_count == 0 else "Not evaluated")
    if qualifying_count == 1:
        return ("one", "Limited history — based on one five-minute period")
    category = "few" if qualifying_count < 5 else "multiple"
    day_word = "day" if distinct_days == 1 else "days"
    return (
        category,
        f"{qualifying_count} qualifying observations across {distinct_days} distinct {day_word}",
    )


def detect_profile(
    foreground_processes: list[str | None],
    supporting_processes: list[str] | None,
    *,
    primary_workload: str | None,
    secondary_workload: str | None = None,
) -> dict[str, Any]:
    """Return one deterministic profile without inspecting user content."""
    foreground = [_normalise_process(item) for item in foreground_processes]
    foreground = [item for item in foreground if item]
    counts = Counter(foreground)
    sample_count = len(foreground_processes)
    matches: list[tuple[int, float, int, str, dict[str, Any], list[str]]] = []
    for order, (key, profile) in enumerate(FINE_PROFILES.items()):
        recognised = set(profile["foreground_executables"])
        matched = sorted(name for name in counts if name in recognised)
        count = sum(counts[name] for name in matched)
        proportion = count / max(1, sample_count)
        if (
            count >= MINIMUM_FOREGROUND_SAMPLES
            and proportion >= MINIMUM_FOREGROUND_PROPORTION
        ):
            matches.append((count, proportion, -order, key, profile, matched))
    if matches:
        count, proportion, _, key, profile, matched = max(matches)
        return {
            "profile_key": key,
            "parent_workload_profile": profile["parent_workload_profile"],
            "confidence": round(min(100.0, 60.0 + proportion * 40.0), 4),
            "reason": "recognised_foreground_executable_composition",
            "evidence": {
                "foreground_executables": matched,
                "matched_sample_count": count,
                "window_sample_count": sample_count,
                "matched_proportion": round(proportion, 6),
                "supporting_processes": sorted(set(
                    _normalise_process(item) for item in (supporting_processes or [])
                    if _normalise_process(item) in set(profile["supporting_executables"])
                )),
                "foreground_priority_applied": True,
            },
        }

    # Supporting/background processes cannot prove a fine-grained workload.
    fallback = (
        "guided_development" if secondary_workload == "guided_development"
        else primary_workload if primary_workload in BROAD_PROFILES
        else "interactive_light"
    )
    return {
        "profile_key": fallback,
        "parent_workload_profile": fallback,
        "confidence": 50.0 if foreground else 35.0,
        "reason": "broad_v2_profile_fallback_no_unambiguous_fine_evidence",
        "evidence": {
            "foreground_executables": sorted(counts),
            "window_sample_count": sample_count,
            "supporting_processes_ignored_as_proof": sorted(set(
                _normalise_process(item) for item in (supporting_processes or [])
                if _normalise_process(item)
            )),
            "privacy_boundary": "no_urls_titles_history_filenames_or_content",
        },
    }


def _supporting_processes(connection, first_id: int | None, last_id: int | None) -> list[str]:
    if first_id is None or last_id is None:
        return []
    return [
        str(row[0]) for row in connection.execute(
            """SELECT DISTINCT process_name FROM process_snapshots
            WHERE metric_id BETWEEN ? AND ? ORDER BY process_name""",
            (first_id, last_id),
        )
    ]


def derive_observations(
    database_path: Path | None = None,
    *,
    limit: int | None = None,
) -> dict[str, int]:
    path = initialize_database(database_path or get_database_path())
    with database_connection(path) as connection:
        sql = """SELECT * FROM feature_windows window
        WHERE finalization_state IN ('finalized','audited_correction')
          AND is_complete = 1
          AND NOT EXISTS (
            SELECT 1 FROM fine_quality_observations observation
            WHERE observation.feature_window_id = window.id
              AND observation.detection_rule_version = ?
          )
        ORDER BY window_start_utc"""
        parameters: list[Any] = [DETECTION_RULE_VERSION]
        if limit is not None:
            sql += " LIMIT ?"
            parameters.append(limit)
        windows = [dict(row) for row in connection.execute(sql, parameters)]

    observations: list[dict[str, Any]] = []
    with database_connection(path) as connection:
        for window in windows:
            foreground = [
                row[0] for row in connection.execute(
                    """SELECT foreground_process_name FROM metrics
                    WHERE device_id = ? AND timestamp_utc >= ? AND timestamp_utc < ?
                    ORDER BY timestamp_utc, id""",
                    (
                        window["device_id"], window["window_start_utc"],
                        window["window_end_utc"],
                    ),
                )
            ]
            detected = detect_profile(
                foreground,
                _supporting_processes(
                    connection, window.get("source_first_metric_id"),
                    window.get("source_last_metric_id"),
                ),
                primary_workload=window.get("dominant_workload_class"),
                secondary_workload=window.get("secondary_workload_context"),
            )
            observations.append({"window": window, "detected": detected})

    created = 0
    timestamp = _now()
    def persist(connection):
        nonlocal created
        for value in observations:
            window, detected = value["window"], value["detected"]
            cursor = connection.execute(
                """INSERT OR IGNORE INTO fine_quality_observations (
                feature_window_id, device_id, observed_at_utc,
                detected_profile_key, parent_workload_profile,
                detection_confidence, detection_reason,
                evidence_identifiers_json, taxonomy_version,
                detection_rule_version, derivation_mode, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    window["id"], window["device_id"], window["window_end_utc"],
                    detected["profile_key"], detected["parent_workload_profile"],
                    detected["confidence"], detected["reason"],
                    json.dumps(detected["evidence"], sort_keys=True),
                    TAXONOMY_VERSION, DETECTION_RULE_VERSION,
                    "historical_additive_derivation", timestamp,
                ),
            )
            created += int(cursor.rowcount > 0)
    if observations:
        run_write_transaction(path, persist, priority="maintenance")
    return {"considered": len(windows), "created": created}


def _broad_definition(profile_key: str) -> dict[str, Any]:
    name, parent = BROAD_PROFILES[profile_key]
    metrics = {
        key: {
            **BASE_METRICS[key], "weight": weight,
            "recommended_high": high, "limit_high": limit,
            "required": key in {"cpu_avg", "ram_avg", "disk_usage_avg"},
        }
        for key, weight, high, limit in (
            ("cpu_avg", 25, 82, 98), ("ram_avg", 25, 85, 96),
            ("swap_avg", 10, 10, 60), ("disk_usage_avg", 18, 85, 97),
            ("cpu_stddev", 12, 25, 55), ("critical_event_count", 6, 0, 1),
            ("error_event_count", 4, 0, 3),
        )
    }
    return {
        "key": profile_key, "name": name,
        "parent_workload_profile": parent, "metrics": metrics,
    }


def _metric_score(value: float, recommended: float, limit: float) -> float:
    if value <= recommended:
        return 100.0
    if limit <= recommended:
        return 0.0
    if value >= limit:
        return 0.0
    return max(0.0, 100.0 * (limit - value) / (limit - recommended))


def _baseline_stats(connection, version_id: int, scope: str) -> tuple[dict[str, Any], str]:
    profile = connection.execute(
        """SELECT * FROM baseline_version_profiles
        WHERE baseline_version_id = ? AND workload_scope = ?
          AND readiness_state = 'ready'""",
        (version_id, scope),
    ).fetchone()
    used_scope = scope
    if profile is None:
        profile = connection.execute(
            """SELECT * FROM baseline_version_profiles
            WHERE baseline_version_id = ? AND workload_scope = '__device__'
              AND readiness_state = 'ready'""",
            (version_id,),
        ).fetchone()
        used_scope = "__device__"
    if profile is None:
        return {}, "unavailable"
    return {
        row["feature_name"]: dict(row)
        for row in connection.execute(
            """SELECT * FROM baseline_version_feature_stats
            WHERE version_profile_id = ?""",
            (profile["id"],),
        )
    }, used_scope


def evaluate_profile_quality(database_path: Path | None = None) -> dict[str, int]:
    path = initialize_database(database_path or get_database_path())
    derive_observations(path)
    with database_connection(path) as connection:
        active = connection.execute(
            """SELECT * FROM baseline_versions WHERE lifecycle_state='active'
            ORDER BY version_number DESC LIMIT 1"""
        ).fetchone()
        if active is None:
            return {"evaluated": 0, "not_evaluated": 0}
        rows = connection.execute(
            """SELECT observation.*, window.*,
            observation.id observation_id, window.id window_id
            FROM fine_quality_observations observation
            JOIN feature_windows window ON window.id=observation.feature_window_id
            ORDER BY observation.observed_at_utc"""
        ).fetchall()
        existing = {
            (int(row["observation_id"]), str(row["profile_key"]))
            for row in connection.execute(
                """SELECT observation_id,profile_key FROM fine_quality_assessments
                WHERE scoring_method_version=? AND baseline_version_id=?""",
                (SCORING_METHOD_VERSION, active["id"]),
            )
        }
        plans: list[dict[str, Any]] = []
        for stored in rows:
            item = dict(stored)
            detected_key = item["detected_profile_key"]
            detected_definition = FINE_PROFILES.get(detected_key) or (
                _broad_definition(detected_key) if detected_key in BROAD_PROFILES else None
            )
            definitions = []
            if detected_definition is not None and detected_key != "device":
                definitions.append((detected_key, detected_definition))
            definitions.append(("device", _broad_definition("device")))
            for key, definition in definitions:
                if (int(item["observation_id"]), key) in existing:
                    continue
                _append_quality_plan(connection, active, item, key, definition, plans)

    counts = {"evaluated": 0, "not_evaluated": 0}
    def persist(connection):
        for plan in plans:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO fine_quality_assessments (
                observation_id, feature_window_id, device_id, profile_key, profile_name,
                parent_workload_profile, assessed_at_utc, evaluation_state,
                profile_quality_score, evidence_confidence, baseline_version_id,
                taxonomy_version, detection_rule_version, scoring_method_version,
                normalization_method, reason_codes_json, missing_evidence_json,
                explanation, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    plan["observation_id"], plan["window_id"], plan["device_id"],
                    plan["key"], plan["name"], plan["parent"], plan["assessed_at"],
                    plan["state"], plan["score"], plan["confidence"],
                    plan["baseline_version_id"], TAXONOMY_VERSION,
                    DETECTION_RULE_VERSION, SCORING_METHOD_VERSION,
                    "normalize_available_applicable_profile_metrics",
                    json.dumps(plan["reasons"]), json.dumps(plan["missing"]),
                    plan["explanation"], plan["assessed_at"],
                ),
            )
            if cursor.rowcount <= 0:
                continue
            assessment_id = int(cursor.lastrowid)
            for value in plan["contributions"]:
                connection.execute(
                    """INSERT INTO fine_quality_metric_contributions (
                    assessment_id, metric_key, metric_label, observed_value,
                    baseline_centre, expected_low, expected_high,
                    configured_threshold_json, direction, availability_status,
                    configured_weight, effective_weight, metric_score,
                    weighted_contribution, explanation
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        assessment_id, value["metric_key"], value["metric_label"],
                        value["observed_value"], value["baseline_centre"],
                        value["expected_low"], value["expected_high"],
                        json.dumps(value["threshold"]), value["direction"],
                        value["availability_status"], value["configured_weight"],
                        value["effective_weight"], value["metric_score"],
                        value["weighted_contribution"], value["explanation"],
                    ),
                )
            counts["evaluated" if plan["score"] is not None else "not_evaluated"] += 1
    if plans:
        run_write_transaction(path, persist, priority="maintenance")
    return counts


def _append_quality_plan(connection, active, item, key, definition, plans) -> None:
    """Build one independent profile plan without altering the source window."""
    stats, baseline_scope = _baseline_stats(
        connection, int(active["id"]),
        str(definition["parent_workload_profile"]),
    )
    contributions: list[dict[str, Any]] = []
    missing_required: list[str] = []
    available_weight = 0.0
    weighted = 0.0
    for metric_key, config in definition["metrics"].items():
        value = item.get(metric_key)
        weight = float(config["weight"])
        baseline = stats.get(metric_key, {})
        if value is None:
            if config["required"]:
                missing_required.append(metric_key)
            contributions.append({
                "metric_key": metric_key, "metric_label": config["label"],
                "observed_value": None, "baseline_centre": baseline.get("median"),
                "expected_low": baseline.get("percentile_05"),
                "expected_high": baseline.get("percentile_95"),
                "threshold": {"recommended_high": config["recommended_high"], "limit_high": config["limit_high"]},
                "direction": config["direction"], "availability_status": "unavailable",
                "configured_weight": weight, "effective_weight": 0.0,
                "metric_score": None, "weighted_contribution": None,
                "explanation": "Unavailable evidence was excluded, not replaced with zero.",
            })
            continue
        recommended = max(
            float(config["recommended_high"]),
            float(baseline.get("percentile_95") or config["recommended_high"]),
        )
        limit = max(float(config["limit_high"]), recommended + 1.0)
        score = _metric_score(float(value), recommended, limit)
        available_weight += weight
        weighted += score * weight
        contributions.append({
            "metric_key": metric_key, "metric_label": config["label"],
            "observed_value": float(value), "baseline_centre": baseline.get("median"),
            "expected_low": baseline.get("percentile_05"),
            "expected_high": baseline.get("percentile_95"),
            "threshold": {"recommended_high": recommended, "limit_high": limit},
            "direction": config["direction"], "availability_status": "available",
            "configured_weight": weight, "effective_weight": 0.0,
            "metric_score": round(score, 4), "weighted_contribution": 0.0,
            "explanation": (
                f"Observed {float(value):.2f}; the applicable v2-anchored "
                f"upper reference is {recommended:.2f}."
            ),
        })
    evaluated = not missing_required and available_weight > 0 and bool(stats)
    score = round(weighted / available_weight, 4) if evaluated else None
    for value in contributions:
        if value["metric_score"] is not None:
            effective = value["configured_weight"] / available_weight
            value["effective_weight"] = round(effective, 6)
            value["weighted_contribution"] = round(
                float(value["metric_score"]) * effective, 4
            )
    coverage = float(item.get("coverage_ratio") or 0)
    metric_availability = available_weight / max(
        1.0, sum(float(v["weight"]) for v in definition["metrics"].values())
    )
    detection_confidence = 100.0 if key == "device" else float(item["detection_confidence"])
    confidence = round(min(100.0, (
        coverage * 45
        + detection_confidence * 0.25
        + metric_availability * 20
        + (10 if baseline_scope != "unavailable" else 0)
    )), 4)
    plans.append({
        "observation_id": item["observation_id"], "window_id": item["window_id"],
        "device_id": item["device_id"], "key": key,
        "name": definition["name"], "parent": definition["parent_workload_profile"],
        "assessed_at": _now(), "state": "assessed" if evaluated else "not_evaluated",
        "score": score, "confidence": confidence if evaluated else None,
        "baseline_version_id": active["id"], "missing": missing_required,
        "reasons": [
            "v2_anchored_profile_specific_quality_score" if evaluated
            else "insufficient_required_quality_evidence",
            f"baseline_scope_{baseline_scope}",
        ],
        "explanation": (
            f"{definition['name']} current workload headroom uses its qualifying "
            "observed metrics, profile-specific weights, and the applicable active "
            "personal-baseline reference."
        ),
        "contributions": contributions,
    })


def profile_quality_status(database_path: Path | None = None) -> dict[str, Any]:
    path = (database_path or get_database_path()).resolve()
    with read_only_database_connection(path) as connection:
        device = connection.execute(
            "SELECT device_id FROM metrics ORDER BY timestamp_utc DESC,id DESC LIMIT 1"
        ).fetchone()
        active = connection.execute(
            """SELECT id,version_number,activated_at_utc FROM baseline_versions
            WHERE lifecycle_state='active' ORDER BY version_number DESC LIMIT 1"""
        ).fetchone()
        assessments = {
            item["profile_key"]: item
            for item in get_profile_quality_assessments(
                connection, device_id=device[0] if device else None
            )
        }
        observed = {
            row["detected_profile_key"]: int(row["count"])
            for row in connection.execute(
                """SELECT detected_profile_key,COUNT(*) count
                FROM fine_quality_observations
                WHERE (? IS NULL OR device_id = ?)
                GROUP BY detected_profile_key""",
                (device[0] if device else None, device[0] if device else None),
            )
        }
        observed["device"] = sum(observed.values())
        qualifying: dict[str, dict[str, Any]] = {}
        if active is not None:
            for row in connection.execute(
                """SELECT assessment.profile_key,
                          COUNT(*) qualifying_count,
                          COUNT(DISTINCT substr(observation.observed_at_utc,1,10)) distinct_days,
                          MAX(observation.observed_at_utc) latest_observation
                FROM fine_quality_assessments assessment
                JOIN fine_quality_observations observation
                  ON observation.id = assessment.observation_id
                WHERE assessment.baseline_version_id = ?
                  AND assessment.scoring_method_version = ?
                  AND assessment.profile_quality_score IS NOT NULL
                  AND (? IS NULL OR assessment.device_id = ?)
                GROUP BY assessment.profile_key""",
                (
                    active["id"], SCORING_METHOD_VERSION,
                    device[0] if device else None, device[0] if device else None,
                ),
            ):
                qualifying[row["profile_key"]] = dict(row)
            membership = {
                (row["workload_scope"], int(row["feature_window_id"]))
                for row in connection.execute(
                    """SELECT profile.workload_scope,training.feature_window_id
                    FROM baseline_version_training_windows training
                    JOIN baseline_version_profiles profile
                      ON profile.id=training.version_profile_id
                    WHERE profile.baseline_version_id=?""",
                    (active["id"],),
                )
            }
            for row in connection.execute(
                """SELECT assessment.profile_key,assessment.feature_window_id,
                          assessment.reason_codes_json,observation.observed_at_utc
                FROM fine_quality_assessments assessment
                JOIN fine_quality_observations observation
                  ON observation.id=assessment.observation_id
                WHERE assessment.baseline_version_id=?
                  AND assessment.scoring_method_version=?
                  AND assessment.profile_quality_score IS NOT NULL
                  AND (? IS NULL OR assessment.device_id=?)""",
                (
                    active["id"], SCORING_METHOD_VERSION,
                    device[0] if device else None, device[0] if device else None,
                ),
            ):
                profile_history = qualifying[row["profile_key"]]
                reasons = json.loads(row["reason_codes_json"] or "[]")
                scope = next(
                    (reason.removeprefix("baseline_scope_") for reason in reasons
                     if reason.startswith("baseline_scope_")),
                    "unavailable",
                )
                is_member = (scope, int(row["feature_window_id"])) in membership
                post_activation = bool(
                    active["activated_at_utc"]
                    and row["observed_at_utc"] > active["activated_at_utc"]
                )
                if is_member:
                    field = "baseline_training_observation_count"
                elif post_activation:
                    field = "independent_post_activation_observation_count"
                    previous = profile_history.get(
                        "latest_independent_post_activation_observation_utc"
                    )
                    if previous is None or row["observed_at_utc"] > previous:
                        profile_history[
                            "latest_independent_post_activation_observation_utc"
                        ] = row["observed_at_utc"]
                else:
                    field = "historical_independence_unavailable_count"
                profile_history[field] = int(profile_history.get(field, 0)) + 1
    profiles = []
    for profile in public_catalogue():
        key = str(profile["key"])
        assessment = assessments.get(key)
        count = observed.get(key, 0)
        history = qualifying.get(key, {})
        qualifying_count = int(history.get("qualifying_count", 0))
        distinct_days = int(history.get("distinct_days", 0))
        history_category, history_label = _history_depth_description(
            qualifying_count, count, distinct_days,
        )
        state = (
            assessment["evaluation_state"] if assessment else
            "not_observed" if count == 0 else
            "not_evaluated"
        )
        profiles.append({
            **profile,
            "observed_window_count": count,
            "history_depth": {
                "qualifying_observation_count": qualifying_count,
                "distinct_observation_days": distinct_days,
                "latest_qualifying_observation_utc": history.get("latest_observation"),
                "history_category": history_category,
                "label": history_label,
                "baseline_training_observation_count": int(
                    history.get("baseline_training_observation_count", 0)
                ),
                "independent_post_activation_observation_count": int(
                    history.get("independent_post_activation_observation_count", 0)
                ),
                "latest_independent_post_activation_observation_utc": history.get(
                    "latest_independent_post_activation_observation_utc"
                ),
                "historical_independence_unavailable_count": int(
                    history.get("historical_independence_unavailable_count", 0)
                ),
            },
            "evaluation_state": state,
            "assessment": assessment,
            "status_explanation": (
                "Not observed" if count == 0 else
                "Not evaluated — insufficient evidence" if assessment is None or assessment.get("profile_quality_score") is None else
                "Current workload headroom evaluated from genuine local evidence"
            ),
            "calibration_required": False,
        })
    return {
        "device_id": device[0] if device else None,
        "active_baseline_version": int(active["version_number"]) if active else None,
        "taxonomy_version": TAXONOMY_VERSION,
        "detection_rule_version": DETECTION_RULE_VERSION,
        "scoring_method_version": SCORING_METHOD_VERSION,
        "semantic_version": SEMANTIC_VERSION,
        "measure_name": MEASURE_NAME,
        "interpretation": INTERPRETATION,
        "guide_explanation": GUIDE_EXPLANATION,
        "full_score_explanation": FULL_SCORE_EXPLANATION,
        "profiles": profiles,
        "research_references": RESEARCH_REFERENCES,
        "profile_count": len(profiles),
        "fine_profile_count": len(FINE_PROFILES),
    }
