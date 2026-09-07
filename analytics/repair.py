"""Audited schema-17 feature-window integrity comparison and repair."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agent.config import get_database_path
from analytics.aggregate import (
    WINDOW_MINUTES,
    build_feature,
    semantic_feature_hash,
    semantic_feature_values,
    upsert_feature,
)
from analytics.recalibration import refresh_candidate
from backend.database import (
    database_connection,
    initialize_database,
    read_only_database_connection,
    run_write_transaction,
)


REPAIR_REASON = "partial_lookback_aggregation_repair"
COMPARISON_FIELDS = (
    "sample_count", "expected_sample_count", "coverage_ratio", "is_complete",
    "dominant_workload_class", "workload_confidence", "workload_distribution_json",
    "workload_composition_json", "secondary_workload_context",
    "missing_indicators_json",
)


def shadow_quality_policy_comparison(
    database_path: Path,
    *,
    device_id: str | None = None,
    candidate_version_id: int | None = None,
) -> dict[str, Any]:
    """Compare 8/9-row windows without changing production eligibility.

    This is deliberately diagnostic only.  Historical raw rows predate the
    schema-17 cycle link, so runtime continuity is conservatively inferred
    only when source positions and the maximum internal gap are all healthy.
    """
    rows_out: list[dict[str, Any]] = []
    with read_only_database_connection(database_path) as connection:
        clauses = ["sample_count IN (8, 9)"]
        parameters: list[Any] = []
        if device_id:
            clauses.append("device_id = ?")
            parameters.append(device_id)
        paused_intervals: list[tuple[datetime, datetime]] = []
        if candidate_version_id is not None:
            paused_at: datetime | None = None
            for event in connection.execute(
                """SELECT event_type,event_timestamp_utc
                FROM baseline_recalibration_events
                WHERE baseline_version_id=? AND event_type IN ('paused','resumed')
                ORDER BY event_timestamp_utc,id""",
                (candidate_version_id,),
            ):
                event_time = datetime.fromisoformat(event["event_timestamp_utc"])
                if event["event_type"] == "paused":
                    paused_at = event_time
                elif paused_at is not None:
                    paused_intervals.append((paused_at, event_time))
                    paused_at = None
        windows = connection.execute(
            f"SELECT * FROM feature_windows WHERE {' AND '.join(clauses)} "
            "ORDER BY window_start_utc,id",
            parameters,
        ).fetchall()
        for row in windows:
            window = dict(row)
            source = connection.execute(
                """SELECT id,timestamp_utc,cpu_percent,ram_percent,disk_percent
                FROM metrics WHERE device_id=? AND timestamp_utc>=? AND timestamp_utc<?
                ORDER BY timestamp_utc,id""",
                (window["device_id"], window["window_start_utc"], window["window_end_utc"]),
            ).fetchall()
            if len(source) not in (8, 9):
                continue
            start_dt = datetime.fromisoformat(window["window_start_utc"])
            end_dt = datetime.fromisoformat(window["window_end_utc"])
            times = [datetime.fromisoformat(item["timestamp_utc"]) for item in source]
            gaps = [(times[index] - times[index - 1]).total_seconds()
                    for index in range(1, len(times))]
            sample_ratio = len(source) / 10.0
            span_ratio = (
                (times[-1] - times[0]).total_seconds() / 270.0 if len(times) > 1 else 0.0
            )
            boundary_ok = (
                (times[0] - start_dt).total_seconds() <= 45.0
                and (end_dt - times[-1]).total_seconds() <= 45.0
            )
            gap_ok = bool(gaps) and max(gaps) <= 45.0
            core_ok = all(
                item[name] is not None
                for item in source
                for name in ("cpu_percent", "ram_percent", "disk_percent")
            )
            safety_ok = not any(
                (window.get(name) or 0) > 0
                for name in (
                    "critical_event_count", "hardware_event_count",
                    "storage_event_count", "power_event_count",
                )
            )
            pause_ok = not any(
                pause_start <= end_dt <= pause_end
                for pause_start, pause_end in paused_intervals
            )
            qualifies = all((sample_ratio >= 0.8, span_ratio >= 0.8,
                             boundary_ok, gap_ok, core_ok, safety_ok, pause_ok))
            rows_out.append({
                "window_id": int(window["id"]),
                "sample_count": len(source),
                "sample_coverage": sample_ratio,
                "temporal_span_coverage": span_ratio,
                "boundary_positions_ok": boundary_ok,
                "maximum_internal_gap_seconds": max(gaps) if gaps else None,
                "required_core_indicators_available": core_ok,
                "runtime_session_continuity": (
                    "inferred_from_source_positions_and_gap"
                    if boundary_ok and gap_ok else "not_established"
                ),
                "candidate_pause_overlap": not pause_ok,
                "safety_policy_ok": safety_ok,
                "would_qualify_shadow_policy": qualifies,
            })
    return {
        "policy": "shadow-temporal-quality-v1",
        "affects_candidate_v2": False,
        "evaluated_8_9_window_count": len(rows_out),
        "would_qualify_count": sum(
            bool(item["would_qualify_shadow_policy"]) for item in rows_out
        ),
        "windows": rows_out,
        "limitation": (
            "Historical rows lack immutable runtime-cycle links; continuity is "
            "inferred from boundary position and internal gap and remains shadow-only."
        ),
    }


def compare_windows(
    database_path: Path,
    *,
    device_id: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> list[dict[str, Any]]:
    """Read authoritative raw ranges and return semantic mismatches only."""
    mismatches: list[dict[str, Any]] = []
    with read_only_database_connection(database_path) as connection:
        clauses: list[str] = []
        parameters: list[Any] = []
        if device_id:
            clauses.append("device_id = ?")
            parameters.append(device_id)
        if start:
            clauses.append("window_end_utc >= ?")
            parameters.append(start)
        if end:
            clauses.append("window_start_utc < ?")
            parameters.append(end)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        windows = connection.execute(
            f"SELECT * FROM feature_windows {where} ORDER BY window_start_utc, id",
            parameters,
        ).fetchall()
        for stored_row in windows:
            stored = dict(stored_row)
            rows = [dict(row) for row in connection.execute(
                """SELECT * FROM metrics WHERE device_id=? AND timestamp_utc>=?
                AND timestamp_utc<? ORDER BY timestamp_utc,id""",
                (stored["device_id"], stored["window_start_utc"], stored["window_end_utc"]),
            )]
            if not rows:
                continue
            start_dt = datetime.fromisoformat(stored["window_start_utc"])
            authoritative = build_feature(
                connection, rows, start_dt,
                start_dt + timedelta(minutes=WINDOW_MINUTES),
            )
            differences = {
                field: {"old": stored.get(field), "new": authoritative.get(field)}
                for field in COMPARISON_FIELDS
                if stored.get(field) != authoritative.get(field)
            }
            if not differences:
                continue
            mismatches.append({
                "window_id": int(stored["id"]),
                "window_start_utc": stored["window_start_utc"],
                "window_end_utc": stored["window_end_utc"],
                "differences": differences,
                "old_semantic_hash": semantic_feature_hash(stored),
                "old_semantic_values": semantic_feature_values(stored),
                "new_semantic_hash": authoritative["semantic_hash"],
                "source_sample_count": len(rows),
                "source_metric_ids": [int(rows[0]["id"]), int(rows[-1]["id"])],
                "source_timestamps": [rows[0]["timestamp_utc"], rows[-1]["timestamp_utc"]],
                "authoritative_feature": authoritative,
            })
    return mismatches


def apply_repairs(database_path: Path, comparisons: list[dict[str, Any]]) -> int:
    """Apply exactly the supplied dry-run mismatches in one transaction."""
    initialize_database(database_path)

    def persist(connection) -> int:
        changed = 0
        for item in comparisons:
            current = connection.execute(
                "SELECT * FROM feature_windows WHERE id=?", (item["window_id"],)
            ).fetchone()
            if current is None:
                raise RuntimeError(f"Feature window {item['window_id']} disappeared.")
            current_values = semantic_feature_values(dict(current))
            if semantic_feature_hash(dict(current)) != item["old_semantic_hash"]:
                expected_values = item.get("old_semantic_values", {})
                existing_changed = any(
                    current_values.get(key) != value
                    for key, value in expected_values.items()
                )
                additive_non_null = any(
                    key not in expected_values and value is not None
                    for key, value in current_values.items()
                )
                if existing_changed or additive_non_null:
                    raise RuntimeError(
                        f"Feature window {item['window_id']} changed after the dry run."
                    )
            feature = dict(item["authoritative_feature"])
            feature["finalization_state"] = "audited_correction"
            feature["corrected_at_utc"] = datetime.now(timezone.utc).isoformat()
            changed += int(upsert_feature(
                connection, feature, correction_reason=REPAIR_REASON
            ))
        return changed

    return run_write_transaction(database_path, persist, priority="maintenance")


def certify_consistent_legacy_windows(database_path: Path) -> int:
    """Populate schema-17 provenance without changing legacy feature semantics."""
    initialize_database(database_path)

    def persist(connection) -> int:
        rows = connection.execute(
            "SELECT * FROM feature_windows WHERE finalization_state IS NULL"
        ).fetchall()
        changed = 0
        for row in rows:
            stored = dict(row)
            source = connection.execute(
                """SELECT id,timestamp_utc FROM metrics WHERE device_id=?
                AND timestamp_utc>=? AND timestamp_utc<? ORDER BY timestamp_utc,id""",
                (stored["device_id"], stored["window_start_utc"], stored["window_end_utc"]),
            ).fetchall()
            if not source:
                continue
            timestamps = [datetime.fromisoformat(item["timestamp_utc"]) for item in source]
            gaps = [(timestamps[i] - timestamps[i-1]).total_seconds()
                    for i in range(1, len(timestamps))]
            provenance = {
                "source_sample_count": len(source),
                "source_first_metric_id": source[0]["id"],
                "source_last_metric_id": source[-1]["id"],
                "source_first_timestamp_utc": source[0]["timestamp_utc"],
                "source_last_timestamp_utc": source[-1]["timestamp_utc"],
                "source_max_internal_gap_seconds": max(gaps) if gaps else None,
                "aggregation_rule_version": "authoritative-window-v1",
            }
            certified = {**stored, **provenance}
            connection.execute(
                """UPDATE feature_windows SET source_sample_count=?,
                source_first_metric_id=?,source_last_metric_id=?,
                source_first_timestamp_utc=?,source_last_timestamp_utc=?,
                source_max_internal_gap_seconds=?,aggregation_rule_version=?,
                finalization_state='finalized',finalized_at_utc=COALESCE(finalized_at_utc,?),
                semantic_hash=COALESCE(semantic_hash,?) WHERE id=?""",
                (len(source), source[0]["id"], source[-1]["id"],
                 source[0]["timestamp_utc"], source[-1]["timestamp_utc"],
                 max(gaps) if gaps else None, "authoritative-window-v1",
                 stored["updated_at_utc"], semantic_feature_hash(certified), stored["id"]),
            )
            changed += 1
        return changed

    return run_write_transaction(database_path, persist, priority="maintenance")


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit authoritative feature windows")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--device")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--database", type=Path, default=get_database_path())
    args = parser.parse_args()
    comparison = compare_windows(
        args.database, device_id=args.device, start=args.start, end=args.end
    )
    output: dict[str, Any] = {
        "mode": "dry_run" if args.dry_run else "apply",
        "mismatch_count": len(comparison),
        "windows": [{
            k: v for k, v in item.items()
            if k not in {"authoritative_feature", "old_semantic_values"}
        }
                    for item in comparison],
        "shadow_quality_comparison": shadow_quality_policy_comparison(
            args.database, device_id=args.device
        ),
    }
    if args.apply:
        output["repaired_count"] = apply_repairs(args.database, comparison)
        output["certified_legacy_count"] = certify_consistent_legacy_windows(args.database)
        output["candidate"] = refresh_candidate(
            args.database, device_id=args.device, force=True
        ) if args.device else None
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
