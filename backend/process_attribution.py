"""Timestamp-matched process attribution for one five-minute window."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from statistics import median
from typing import Any


def process_attribution_for_window(
    connection: sqlite3.Connection, window_id: int
) -> dict[str, Any]:
    window = connection.execute(
        """SELECT id, window_start_utc, window_end_utc, source_sample_count,
        sample_count, dominant_workload_class, coverage_ratio
        FROM feature_windows WHERE id = ?""",
        (window_id,),
    ).fetchone()
    if window is None:
        return {"state": "unavailable", "reason": "analysis_period_not_found"}
    domains = [
        str(row["candidate_domain"]).lower()
        for row in connection.execute(
            """SELECT candidate_domain FROM root_cause_candidates candidate
            JOIN risk_assessments risk ON risk.id = candidate.risk_assessment_id
            WHERE risk.feature_window_id = ? ORDER BY candidate.rank""",
            (window_id,),
        )
    ]
    resource = "memory" if any("memory" in value or "ram" in value or "swap" in value for value in domains) else "cpu"
    rows = list(connection.execute(
        """SELECT metric.id metric_id, metric.timestamp_utc,
        metric.foreground_process_name, metric.ram_total_bytes,
        process.process_name, process.cpu_percent, process.memory_percent
        FROM metrics metric JOIN process_snapshots process
          ON process.metric_id = metric.id AND process.category = ?
        WHERE metric.timestamp_utc >= ? AND metric.timestamp_utc < ?
        ORDER BY metric.timestamp_utc, process.rank""",
        (resource, window["window_start_utc"], window["window_end_utc"]),
    ))
    if not rows:
        return {
            "state": "unavailable", "reason": "no_timestamp_matched_process_snapshots",
            "window_id": window_id, "window_start_utc": window["window_start_utc"],
            "window_end_utc": window["window_end_utc"], "resource": resource,
        }
    by_process: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        by_process[str(row["process_name"])].append(row)
    expected = int(window["source_sample_count"] or window["sample_count"] or 0)
    contributors: list[dict[str, Any]] = []
    for name, samples in by_process.items():
        values = [
            float(row["memory_percent"] if resource == "memory" else row["cpu_percent"])
            for row in samples
            if row["memory_percent" if resource == "memory" else "cpu_percent"] is not None
        ]
        if not values:
            continue
        bytes_values = [
            float(row["memory_percent"]) / 100 * float(row["ram_total_bytes"])
            for row in samples
            if resource == "memory" and row["memory_percent"] is not None and row["ram_total_bytes"]
        ]
        contributors.append({
            "process_name": name,
            "median_value": median(values), "maximum_value": max(values),
            "unit": "% of system memory" if resource == "memory" else "% of whole-system CPU",
            "median_bytes": median(bytes_values) if bytes_values else None,
            "maximum_bytes": max(bytes_values) if bytes_values else None,
            "sample_count": len({int(row["metric_id"]) for row in samples}),
            "coverage_ratio": len({int(row["metric_id"]) for row in samples}) / expected if expected else None,
            "foreground_sample_count": sum(
                1 for row in samples
                if str(row["foreground_process_name"] or "").casefold() == name.casefold()
            ),
        })
    contributors.sort(key=lambda item: (item["median_value"], item["maximum_value"]), reverse=True)
    if not contributors:
        return {"state": "unavailable", "reason": "process_values_unavailable", "window_id": window_id}
    first = contributors[0]
    second_value = contributors[1]["median_value"] if len(contributors) > 1 else 0
    coverage = first["coverage_ratio"] or 0
    if coverage < 0.5:
        attribution = "process_attribution_unavailable"
        reason = "incomplete_process_coverage"
    elif second_value and first["median_value"] < second_value * 1.20:
        attribution = "no_single_dominant_application_identified"
        reason = "multiple_similar_recorded_contributors"
    elif first["foreground_sample_count"] > 0:
        attribution = "primary_recorded_contributor"
        reason = "largest_recorded_contributor_with_foreground_support"
    else:
        attribution = "likely_contributor"
        reason = "largest_recorded_contributor_without_foreground_causality"
    return {
        "state": "evaluated", "window_id": window_id,
        "window_start_utc": window["window_start_utc"], "window_end_utc": window["window_end_utc"],
        "workload_context": window["dominant_workload_class"],
        "window_coverage_ratio": window["coverage_ratio"], "resource": resource,
        "attribution": attribution, "reason": reason,
        "primary_contributor": first if attribution != "no_single_dominant_application_identified" else None,
        "contributors": contributors[:5],
        "limitation": "Process snapshots contain bounded top-process samples; foreground activity alone is not treated as causation.",
    }
