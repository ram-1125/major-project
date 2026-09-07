"""Idempotent five-minute UTC feature aggregation for Phase 2B."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agent.config import get_database_path
from backend.database import (
    database_connection,
    initialize_database,
    run_write_transaction,
)
from analytics.workload import (
    WORKLOAD_RULE_VERSION,
    classify_window_composition,
)
from backend.phase7b2_repository import AGGREGATION_RULE_VERSION


WINDOW_MINUTES = 5
EXPECTED_SAMPLE_COUNT = 10
FINALIZATION_LATENESS_SECONDS = 60
HIGH_CPU_PERCENT = 80.0
HIGH_MEMORY_PERCENT = 85.0


def window_bounds(timestamp: datetime) -> tuple[datetime, datetime]:
    timestamp = timestamp.astimezone(timezone.utc)
    start = timestamp.replace(
        minute=(timestamp.minute // WINDOW_MINUTES) * WINDOW_MINUTES,
        second=0,
        microsecond=0,
    )
    return start, start + timedelta(minutes=WINDOW_MINUTES)


def percentile(values: list[float], percentile_value: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile_value
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _values(rows: list[dict[str, Any]], name: str) -> list[float]:
    return [
        float(row[name])
        for row in rows
        if isinstance(row.get(name), (int, float))
    ]


def _stats(values: list[float]) -> tuple[Any, Any, Any, Any]:
    if not values:
        return None, None, None, None
    return (
        statistics.fmean(values),
        min(values),
        max(values),
        statistics.pstdev(values),
    )


def _slope(rows: list[dict[str, Any]], name: str) -> float | None:
    available = [
        row for row in rows if isinstance(row.get(name), (int, float))
    ]
    if len(available) < 2:
        return None
    first, last = available[0], available[-1]
    elapsed = (
        datetime.fromisoformat(last["timestamp_utc"])
        - datetime.fromisoformat(first["timestamp_utc"])
    ).total_seconds()
    return (float(last[name]) - float(first[name])) / elapsed if elapsed > 0 else None


def _ratio(values: list[float], predicate) -> float | None:
    return (
        sum(1 for value in values if predicate(value)) / len(values)
        if values
        else None
    )


def _dominant_value(rows: list[dict[str, Any]], name: str) -> tuple[str | None, Counter]:
    """Return the majority value, preserving first-observed tie behaviour."""
    counts = Counter(row.get(name) for row in rows if row.get(name))
    if not counts:
        return None, counts
    highest = max(counts.values())
    tied = {value for value, count in counts.items() if count == highest}
    dominant = next(
        str(row[name]) for row in rows if row.get(name) in tied
    )
    return dominant, counts


def _workload_explanation(
    dominant: str | None,
    counts: Counter,
    sample_count: int,
) -> str | None:
    if dominant is None:
        return None
    highest = counts[dominant]
    tied = sorted(value for value, count in counts.items() if count == highest)
    tie_text = (
        f" A tie between {', '.join(tied)} was resolved by first observation."
        if len(tied) > 1 else ""
    )
    if counts.get("development", 0) and dominant != "development":
        return (
            f"Development was observed in {counts['development']} of {sample_count} "
            f"samples; {dominant.replace('_', ' ')} held the foreground majority "
            f"with {highest} samples.{tie_text}"
        )
    if counts.get("gaming_or_3d", 0) and dominant != "gaming_or_3d":
        return (
            f"Gaming/3D was observed in {counts['gaming_or_3d']} of {sample_count} "
            f"samples; {dominant.replace('_', ' ')} held the foreground majority "
            f"with {highest} samples.{tie_text}"
        )
    return (
        f"{dominant.replace('_', ' ')} was observed most often "
        f"({highest} of {sample_count} samples).{tie_text}"
    )


def _event_counts(
    connection,
    device_id: str,
    start: str,
    end: str,
) -> dict[str, int]:
    rows = connection.execute(
        """SELECT event_level, smartops_category, COUNT(*) count
        FROM windows_events
        WHERE device_id = ? AND event_timestamp_utc >= ? AND event_timestamp_utc < ?
        GROUP BY event_level, smartops_category""",
        (device_id, start, end),
    ).fetchall()
    counts = {
        "critical_event_count": 0, "error_event_count": 0,
        "warning_event_count": 0, "hardware_event_count": 0,
        "storage_event_count": 0, "power_event_count": 0,
        "application_crash_count": 0, "service_failure_count": 0,
        "resource_exhaustion_count": 0,
    }
    severity_keys = {
        "Critical": "critical_event_count", "Error": "error_event_count",
        "Warning": "warning_event_count",
    }
    category_keys = {
        "hardware": "hardware_event_count", "storage": "storage_event_count",
        "power": "power_event_count", "application_crash": "application_crash_count",
        "service_failure": "service_failure_count",
        "resource_exhaustion": "resource_exhaustion_count",
    }
    for row in rows:
        if row["event_level"] in severity_keys:
            counts[severity_keys[row["event_level"]]] += row["count"]
        if row["smartops_category"] in category_keys:
            counts[category_keys[row["smartops_category"]]] += row["count"]
    return counts


def build_feature(
    connection,
    rows: list[dict[str, Any]],
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    sample_count = len(rows)
    cpu = _values(rows, "cpu_percent")
    ram = _values(rows, "ram_percent")
    swap = _values(rows, "swap_percent")
    disk = _values(rows, "disk_percent")
    read_rate = _values(rows, "disk_read_bytes_per_second")
    write_rate = _values(rows, "disk_write_bytes_per_second")
    upload = _values(rows, "network_upload_bytes_per_second")
    download = _values(rows, "network_download_bytes_per_second")
    processes = _values(rows, "process_count")
    temperature = _values(rows, "cpu_temperature_celsius")
    gpu_utilization = _values(rows, "gpu_utilization_percent")
    gpu_memory = _values(rows, "gpu_memory_percent")
    gpu_temperature = _values(rows, "gpu_temperature_celsius")
    cpu_avg, cpu_min, cpu_max, cpu_stddev = _stats(cpu)
    ram_avg, ram_min, ram_max, ram_stddev = _stats(ram)
    dominant, workload_counts = _dominant_value(rows, "workload_class")
    confidences = [
        float(row["workload_confidence"])
        for row in rows
        if row.get("workload_class") == dominant
        and isinstance(row.get("workload_confidence"), (int, float))
    ]
    missing = {
        "cpu_ratio": 1 - len(cpu) / sample_count,
        "ram_ratio": 1 - len(ram) / sample_count,
        "temperature_ratio": 1 - len(temperature) / sample_count,
        "gpu_utilization_ratio": 1 - len(gpu_utilization) / sample_count,
        "gpu_memory_ratio": 1 - len(gpu_memory) / sample_count,
        "gpu_temperature_ratio": 1 - len(gpu_temperature) / sample_count,
    }
    versioned_rows = [row for row in rows if row.get("workload_rule_version")]
    dominant_user_activity, _ = _dominant_value(
        versioned_rows, "user_activity_state"
    )
    dominant_system_activity, _ = _dominant_value(
        versioned_rows, "system_activity_state"
    )
    rule_versions = sorted({
        str(row["workload_rule_version"])
        for row in versioned_rows
        if row.get("workload_rule_version")
    })
    rule_version = (
        rule_versions[0]
        if len(rule_versions) == 1
        else f"mixed:{','.join(rule_versions)}" if rule_versions else None
    )
    is_complete = sample_count >= EXPECTED_SAMPLE_COUNT
    new_rule_window = bool(
        is_complete
        and len(versioned_rows) == sample_count
        and all(
            row.get("workload_rule_version") == WORKLOAD_RULE_VERSION
            for row in rows
        )
    )
    composition = (
        classify_window_composition(
            [str(row.get("workload_class") or "unknown") for row in rows],
            expected_sample_count=EXPECTED_SAMPLE_COUNT,
            is_complete=is_complete,
        )
        if new_rule_window else None
    )
    start_text, end_text = start.isoformat(), end.isoformat()
    source_timestamps = [datetime.fromisoformat(row["timestamp_utc"]) for row in rows]
    source_gaps = [
        (source_timestamps[index] - source_timestamps[index - 1]).total_seconds()
        for index in range(1, len(source_timestamps))
    ]
    feature = {
        "device_id": rows[0]["device_id"],
        "window_start_utc": start_text, "window_end_utc": end_text,
        "sample_count": sample_count, "expected_sample_count": EXPECTED_SAMPLE_COUNT,
        "coverage_ratio": min(sample_count / EXPECTED_SAMPLE_COUNT, 1.0),
        "is_complete": is_complete,
        "dominant_workload_class": dominant,
        "workload_confidence": statistics.fmean(confidences) if confidences else None,
        "dominant_user_activity_state": dominant_user_activity,
        "dominant_system_activity_state": dominant_system_activity,
        "workload_rule_version": rule_version,
        "workload_distribution_json": (
            json.dumps(dict(sorted(workload_counts.items())), sort_keys=True)
            if versioned_rows else None
        ),
        "workload_majority_explanation": (
            _workload_explanation(dominant, workload_counts, sample_count)
            if versioned_rows else None
        ),
        "workload_composition_json": (
            json.dumps(composition, sort_keys=True) if composition else None
        ),
        "secondary_workload_context": (
            composition["secondary_context"] if composition else None
        ),
        "secondary_workload_rule_version": (
            composition["secondary_rule_version"] if composition else None
        ),
        "secondary_workload_reason_codes_json": (
            json.dumps(composition["reason_codes"], sort_keys=True)
            if composition else None
        ),
        "source_sample_count": sample_count,
        "source_first_metric_id": int(rows[0]["id"]),
        "source_last_metric_id": int(rows[-1]["id"]),
        "source_first_timestamp_utc": rows[0]["timestamp_utc"],
        "source_last_timestamp_utc": rows[-1]["timestamp_utc"],
        "source_max_internal_gap_seconds": max(source_gaps) if source_gaps else None,
        "aggregation_rule_version": AGGREGATION_RULE_VERSION,
        "finalization_state": "finalized",
        "finalized_at_utc": datetime.now(timezone.utc).isoformat(),
        "corrected_at_utc": None,
        "missing_indicators_json": json.dumps(missing),
        "cpu_avg": cpu_avg, "cpu_min": cpu_min, "cpu_max": cpu_max,
        "cpu_stddev": cpu_stddev, "cpu_p95": percentile(cpu, 0.95),
        "cpu_slope": _slope(rows, "cpu_percent"),
        "cpu_high_ratio": _ratio(cpu, lambda value: value >= HIGH_CPU_PERCENT),
        "ram_avg": ram_avg, "ram_min": ram_min, "ram_max": ram_max,
        "ram_stddev": ram_stddev, "ram_slope": _slope(rows, "ram_percent"),
        "swap_avg": statistics.fmean(swap) if swap else None,
        "swap_max": max(swap) if swap else None,
        "memory_high_ratio": _ratio(ram, lambda value: value >= HIGH_MEMORY_PERCENT),
        "disk_usage_avg": statistics.fmean(disk) if disk else None,
        "disk_usage_max": max(disk) if disk else None,
        "disk_read_avg": statistics.fmean(read_rate) if read_rate else None,
        "disk_read_max": max(read_rate) if read_rate else None,
        "disk_write_avg": statistics.fmean(write_rate) if write_rate else None,
        "disk_write_max": max(write_rate) if write_rate else None,
        "disk_usage_slope": _slope(rows, "disk_percent"),
        "network_upload_avg": statistics.fmean(upload) if upload else None,
        "network_upload_max": max(upload) if upload else None,
        "network_download_avg": statistics.fmean(download) if download else None,
        "network_download_max": max(download) if download else None,
        "active_ratio": sum(row.get("user_state") == "active" for row in rows) / sample_count,
        "idle_ratio": sum(row.get("user_state") == "idle" for row in rows) / sample_count,
        "process_count_avg": statistics.fmean(processes) if processes else None,
        "process_count_max": max(processes) if processes else None,
        "cpu_temperature_avg": statistics.fmean(temperature) if temperature else None,
        "cpu_temperature_max": max(temperature) if temperature else None,
        "gpu_utilization_avg": (
            statistics.fmean(gpu_utilization) if gpu_utilization else None
        ),
        "gpu_utilization_max": max(gpu_utilization) if gpu_utilization else None,
        "gpu_memory_avg": statistics.fmean(gpu_memory) if gpu_memory else None,
        "gpu_memory_max": max(gpu_memory) if gpu_memory else None,
        "gpu_temperature_avg": (
            statistics.fmean(gpu_temperature) if gpu_temperature else None
        ),
        "gpu_temperature_max": max(gpu_temperature) if gpu_temperature else None,
        "cpu_temperature_missing_ratio": missing["temperature_ratio"],
        "gpu_utilization_missing_ratio": missing["gpu_utilization_ratio"],
        "gpu_memory_missing_ratio": missing["gpu_memory_ratio"],
        "gpu_temperature_missing_ratio": missing["gpu_temperature_ratio"],
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    feature.update(_event_counts(connection, feature["device_id"], start_text, end_text))
    feature["semantic_hash"] = semantic_feature_hash(feature)
    return feature


def semantic_feature_values(feature: dict[str, Any]) -> dict[str, Any]:
    """Return stable values that reconstruct the meaning of one feature row."""
    excluded = {
        "id", "updated_at_utc", "finalized_at_utc", "corrected_at_utc",
        "semantic_hash", "finalization_state",
    }
    def stable(value: Any) -> Any:
        # sqlite3 materializes booleans as 0/1. Normalize them before hashing
        # so an immediate authoritative re-read is byte-for-byte idempotent.
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, dict):
            return {key: stable(item) for key, item in sorted(value.items())}
        if isinstance(value, (list, tuple)):
            return [stable(item) for item in value]
        return value

    return {
        key: stable(feature[key])
        for key in sorted(feature)
        if key not in excluded
    }


def semantic_feature_hash(feature: dict[str, Any]) -> str:
    payload = json.dumps(
        semantic_feature_values(feature), sort_keys=True, separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def upsert_feature(
    connection,
    feature: dict[str, Any],
    *,
    correction_reason: str | None = None,
) -> bool:
    """Insert or update only when semantic feature content changed."""
    existing = connection.execute(
        """SELECT * FROM feature_windows
        WHERE device_id = ? AND window_start_utc = ?""",
        (feature["device_id"], feature["window_start_utc"]),
    ).fetchone()
    if existing is not None:
        existing_dict = dict(existing)
        # Recompute from stored columns instead of trusting a cached hash. This
        # also lets the repair tool detect a legacy or externally interrupted
        # row whose semantic columns and cached hash disagree.
        if semantic_feature_hash(existing_dict) == feature["semantic_hash"]:
            return False
    if existing is not None:
        old_source_count = existing_dict.get("source_sample_count")
        new_source_count = feature.get("source_sample_count")
        if (
            existing_dict.get("finalization_state") in {"finalized", "audited_correction"}
            and isinstance(old_source_count, int)
            and isinstance(new_source_count, int)
            and new_source_count < old_source_count
        ):
            # Authoritative raw telemetry is append-only. A smaller re-read is
            # necessarily a partial query and must never downgrade the window.
            return False
        if correction_reason or existing_dict.get("finalization_state") in {
            "finalized", "audited_correction"
        }:
            reason = correction_reason or "authoritative_late_source_correction"
            feature["finalization_state"] = "audited_correction"
            feature["corrected_at_utc"] = datetime.now(timezone.utc).isoformat()
            connection.execute(
                """INSERT OR IGNORE INTO feature_window_repair_events (
                feature_window_id, repaired_at_utc, repair_reason,
                aggregation_rule_version, old_semantic_hash, new_semantic_hash,
                old_values_json, new_values_json, source_sample_count,
                source_first_metric_id, source_last_metric_id,
                source_first_timestamp_utc, source_last_timestamp_utc, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}')""",
                (
                    existing_dict["id"], feature["corrected_at_utc"], reason,
                    AGGREGATION_RULE_VERSION,
                    semantic_feature_hash(existing_dict),
                    feature["semantic_hash"],
                    json.dumps(semantic_feature_values(existing_dict), sort_keys=True),
                    json.dumps(semantic_feature_values(feature), sort_keys=True),
                    feature["source_sample_count"], feature["source_first_metric_id"],
                    feature["source_last_metric_id"],
                    feature["source_first_timestamp_utc"],
                    feature["source_last_timestamp_utc"],
                ),
            )
    columns = tuple(feature)
    updates = ", ".join(
        f"{column}=excluded.{column}"
        for column in columns
        if column not in {"device_id", "window_start_utc"}
    )
    connection.execute(
        f"""INSERT INTO feature_windows ({", ".join(columns)})
        VALUES ({", ".join("?" for _ in columns)})
        ON CONFLICT(device_id, window_start_utc) DO UPDATE SET {updates}""",
        tuple(feature[column] for column in columns),
    )
    return True


def aggregate_database(
    database_path: Path | None = None,
    include_partial: bool = False,
    recent_minutes: int | None = None,
    now_utc: datetime | None = None,
) -> int:
    """Upsert feature windows.

    CLI/backfill callers keep the historical all-row behavior.  The running
    agent supplies a bounded lookback so its routine maintenance does not scan
    the complete telemetry history every 30 seconds.
    """
    path = initialize_database(database_path or get_database_path())
    now = now_utc or datetime.now(timezone.utc)
    finalization_cutoff = now - timedelta(seconds=FINALIZATION_LATENESS_SECONDS)
    with database_connection(path) as connection:
        if recent_minutes is None:
            raw_query = "SELECT * FROM metrics ORDER BY timestamp_utc, id"
            parameters: tuple[Any, ...] = ()
        else:
            lookback = max(WINDOW_MINUTES * 2, int(recent_minutes))
            threshold = (now - timedelta(minutes=lookback)).isoformat()
            latest_feature = connection.execute(
                "SELECT MAX(window_start_utc) FROM feature_windows"
            ).fetchone()[0]
            if latest_feature:
                # Revisit the newest stored window and its predecessor.  This
                # also closes a partial final window after a long offline gap
                # without loading unrelated history into memory.
                latest_start = datetime.fromisoformat(latest_feature)
                threshold = min(
                    threshold,
                    (latest_start - timedelta(minutes=WINDOW_MINUTES)).isoformat(),
                )
            raw_query = (
                "SELECT * FROM metrics WHERE timestamp_utc >= ? "
                "ORDER BY timestamp_utc, id"
            )
            parameters = (threshold,)
        bounded_rows = [dict(row) for row in connection.execute(raw_query, parameters)]
        keys: set[tuple[str, datetime]] = set()
        for row in bounded_rows:
            start, end = window_bounds(datetime.fromisoformat(row["timestamp_utc"]))
            # `include_partial` permits closed historical windows with fewer
            # than ten samples; it never permits the currently open/late
            # window to become a final training feature.
            if end > finalization_cutoff:
                continue
            keys.add((row["device_id"], start))
        # The bounded query identifies affected keys only. Every key is then
        # reconstructed from its complete authoritative half-open raw range.
        authoritative_groups: list[tuple[datetime, list[dict[str, Any]]]] = []
        for device_id, start in sorted(keys, key=lambda item: (item[1], item[0])):
            end = start + timedelta(minutes=WINDOW_MINUTES)
            rows = [dict(row) for row in connection.execute(
                """SELECT * FROM metrics WHERE device_id = ?
                AND timestamp_utc >= ? AND timestamp_utc < ?
                ORDER BY timestamp_utc, id""",
                (device_id, start.isoformat(), end.isoformat()),
            )]
            if rows:
                authoritative_groups.append((start, rows))
        features = [
            build_feature(connection, rows, start, start + timedelta(minutes=WINDOW_MINUTES))
            for start, rows in authoritative_groups
        ]
    if features:
        def persist(connection):
            return sum(upsert_feature(connection, feature) for feature in features)

        run_write_transaction(path, persist, priority="maintenance")
    return len(authoritative_groups)


def main() -> None:
    parser = argparse.ArgumentParser(description="SmartOps five-minute aggregation")
    parser.add_argument(
        "--backfill", action="store_true",
        help="Upsert all closed historical windows, including incomplete ones.",
    )
    args = parser.parse_args()
    count = aggregate_database(include_partial=args.backfill)
    print(f"Upserted {count} five-minute feature windows.")


if __name__ == "__main__":
    main()
