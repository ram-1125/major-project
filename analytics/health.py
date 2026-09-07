"""Phase 4A explainable System Health Score.

The score summarizes current observed operating condition.  It is deterministic
and reconstructable, and it deliberately remains separate from Phase 3A
deviation and Phase 3B risk evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.config import get_database_path
from analytics.health_config import (
    ALGORITHM_VERSION,
    CONFIGURATION_VERSION,
    CORRELATION_CAPS,
    CPU_EXPECTED_WORKLOADS,
    DATA_CONFIDENCE_WEIGHTS,
    DEFAULT_POLICY,
    DIAGNOSTIC_STEPS,
    DISK_IO_EXPECTED_WORKLOADS,
    EVENT_DEDUCTIONS,
    HEALTH_BANDS,
    INTERPRETATION,
    LIMITATIONS,
    OPTIONAL_RESOURCE_GROUPS,
    REQUIRED_CORE_INPUTS,
    RESOURCE_GROUP_WEIGHTS,
    THRESHOLDS,
    TOP_LEVEL_WEIGHTS,
    VALID_WORKLOADS,
    HealthPolicy,
)
from backend.database import database_connection, initialize_database


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _feature_signature(window: dict[str, Any]) -> str:
    """Fingerprint source content while ignoring bookkeeping timestamps."""
    source = {
        key: value
        for key, value in window.items()
        if key not in {"updated_at_utc"}
    }
    encoded = json.dumps(
        source,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def health_band_for_score(score: float) -> str:
    for threshold, band in HEALTH_BANDS:
        if score >= threshold:
            return band
    return "critical_condition"


def _threshold_deduction(name: str, value: float | None) -> float:
    if value is None:
        return 0.0
    for threshold, deduction in THRESHOLDS[name]:
        if value >= threshold:
            return deduction
    return 0.0


def evaluation_eligibility(
    window: dict[str, Any],
    policy: HealthPolicy = DEFAULT_POLICY,
) -> tuple[bool, list[str], list[dict[str, Any]]]:
    reasons: list[str] = []
    inputs: list[dict[str, Any]] = []
    if not bool(window.get("is_complete")):
        reasons.append("incomplete_feature_window")
    coverage = _number(window.get("coverage_ratio"))
    if coverage is None or coverage < policy.minimum_coverage:
        reasons.append("coverage_below_minimum")
    workload = window.get("dominant_workload_class")
    if workload not in VALID_WORKLOADS:
        reasons.append("invalid_workload_context")
    confidence = _number(window.get("workload_confidence"))
    if confidence is None or confidence < policy.minimum_workload_confidence:
        reasons.append("workload_confidence_below_minimum")
    for name in REQUIRED_CORE_INPUTS:
        value = _number(window.get(name))
        status = "available" if value is not None else "unavailable_required"
        if value is None:
            reasons.append(f"{name}_unavailable")
        inputs.append({
            "input_name": name,
            "input_category": "required_core",
            "availability_status": status,
            "observed_value": value,
            "excluded_reason": None if value is not None else "required_metric_missing",
            "applicable_weight": 1.0,
        })
    return not reasons, sorted(set(reasons)), inputs


def _input(
    name: str,
    category: str,
    status: str,
    value: Any,
    reason: str | None,
    weight: float = 0.0,
) -> dict[str, Any]:
    return {
        "input_name": name,
        "input_category": category,
        "availability_status": status,
        "observed_value": value,
        "excluded_reason": reason,
        "applicable_weight": weight,
    }


def _deduction(
    component: str,
    group: str,
    signal: str,
    raw: float,
    effective: float,
    maximum: float,
    cap_reason: str,
    reason: str,
    explanation: str,
    value: Any,
    workload: str | None,
    event_ids: list[int] | None = None,
) -> dict[str, Any]:
    return {
        "component_name": component,
        "contribution_group": group,
        "signal_name": signal,
        "raw_deduction": round(raw, 4),
        "effective_deduction": round(effective, 4),
        "maximum_deduction": maximum,
        "correlation_or_cap_reason": cap_reason,
        "reason_code": reason,
        "explanation": explanation,
        "supporting_value": value,
        "supporting_event_ids": event_ids or [],
        "workload_context": workload,
    }


def _power_summary(connection, window: dict[str, Any]) -> dict[str, Any]:
    rows = connection.execute(
        """SELECT battery_percent, battery_charging, ac_power_connected
        FROM metrics WHERE device_id = ? AND timestamp_utc >= ?
        AND timestamp_utc < ? ORDER BY timestamp_utc DESC, id DESC""",
        (
            window["device_id"],
            window["window_start_utc"],
            window["window_end_utc"],
        ),
    ).fetchall()
    available = [
        row for row in rows
        if row["battery_percent"] is not None
        or row["ac_power_connected"] is not None
    ]
    if not available:
        return {"applicable": False, "reason": "battery_not_present_or_unavailable"}
    latest = available[0]
    return {
        "applicable": True,
        "battery_percent": latest["battery_percent"],
        "battery_charging": (
            bool(latest["battery_charging"])
            if latest["battery_charging"] is not None else None
        ),
        "ac_power_connected": (
            bool(latest["ac_power_connected"])
            if latest["ac_power_connected"] is not None else None
        ),
    }


def _resource_component(
    connection,
    window: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    workload = window.get("dominant_workload_class")
    workload_confidence = _number(window.get("workload_confidence")) or 0.0
    deductions: list[dict[str, Any]] = []
    inputs: list[dict[str, Any]] = []
    explanations: list[str] = []
    group_deductions: dict[str, float] = {}

    cpu_avg = _number(window.get("cpu_avg"))
    cpu_p95 = _number(window.get("cpu_p95"))
    cpu_high_ratio = _number(window.get("cpu_high_ratio"))
    cpu_raw = _threshold_deduction("cpu", cpu_avg)
    if cpu_high_ratio is not None and cpu_high_ratio >= 0.5:
        cpu_raw += 5.0
    cpu_raw = min(cpu_raw, CORRELATION_CAPS["cpu"])
    cpu_effective = cpu_raw
    cpu_reason = "cpu_within_operational_threshold"
    cpu_explanation = "CPU utilization remained within the configured current-condition range."
    cpu_cap_reason = "cpu_average_saturation_and_p95_share_one_group"
    if cpu_raw:
        cpu_reason = "cpu_pressure_observed"
        cpu_explanation = "CPU utilization and saturation reduced the resource-condition component."
    if (
        cpu_raw
        and workload in CPU_EXPECTED_WORKLOADS
        and workload_confidence >= 0.6
    ):
        cpu_effective = cpu_raw * 0.35
        cpu_reason = "cpu_pressure_reduced_for_expected_workload"
        cpu_explanation = (
            f"CPU utilization was high, but it was consistent with the current "
            f"{str(workload).replace('_', ' ')} workload; no strong CPU-health "
            "deduction was applied."
        )
        cpu_cap_reason += ";workload_exception_applied"
    elif cpu_raw and (
        (_number(window.get("idle_ratio")) or 0.0) >= 0.5
        or workload in {"idle", "background_activity"}
    ):
        cpu_effective = min(CORRELATION_CAPS["cpu"], cpu_raw * 1.2)
        cpu_reason = "cpu_pressure_while_idle"
        cpu_explanation = "CPU pressure occurred while the user context was idle."
    group_deductions["cpu"] = cpu_effective
    deductions.append(_deduction(
        "resource_condition", "cpu", "cpu_utilization", cpu_raw, cpu_effective,
        CORRELATION_CAPS["cpu"], cpu_cap_reason, cpu_reason, cpu_explanation,
        {"average": cpu_avg, "p95": cpu_p95, "high_ratio": cpu_high_ratio},
        workload,
    ))

    ram_avg = _number(window.get("ram_avg"))
    ram_slope = _number(window.get("ram_slope"))
    swap_avg = _number(window.get("swap_avg"))
    memory_raw = _threshold_deduction("memory", ram_avg)
    if (
        memory_raw
        and (swap_avg is None or swap_avg < 10.0)
        and (ram_slope is None or ram_slope <= 0.01)
    ):
        memory_effective = memory_raw * 0.5
        memory_reason = "ram_high_but_stable_without_swap_growth"
        memory_explanation = (
            "RAM use was elevated, but stable memory and low swap activity "
            "reduced the deduction."
        )
    else:
        memory_effective = memory_raw
        memory_reason = (
            "ram_pressure_observed" if memory_raw else "ram_within_operational_threshold"
        )
        memory_explanation = (
            "RAM pressure reduced the resource-condition component."
            if memory_raw else
            "RAM use remained within the configured current-condition range."
        )
    swap_raw = _threshold_deduction("swap", swap_avg)
    memory_swap_raw = memory_effective + swap_raw
    memory_effective_capped = min(
        memory_effective,
        CORRELATION_CAPS["memory_and_swap"],
    )
    swap_effective = min(
        swap_raw,
        max(0.0, CORRELATION_CAPS["memory_and_swap"] - memory_effective_capped),
    )
    group_deductions["memory"] = memory_effective_capped
    group_deductions["swap"] = swap_effective
    deductions.extend([
        _deduction(
            "resource_condition", "memory_and_swap", "ram_pressure",
            memory_raw, memory_effective_capped,
            CORRELATION_CAPS["memory_and_swap"],
            "ram_and_swap_share_a_combined_cap",
            memory_reason, memory_explanation,
            {"average": ram_avg, "slope": ram_slope}, workload,
        ),
        _deduction(
            "resource_condition", "memory_and_swap", "swap_pressure",
            swap_raw, swap_effective,
            CORRELATION_CAPS["memory_and_swap"],
            (
                "ram_and_swap_share_a_combined_cap"
                if memory_swap_raw <= CORRELATION_CAPS["memory_and_swap"]
                else "memory_swap_combined_cap_applied"
            ),
            "swap_pressure_observed" if swap_raw else "swap_within_operational_threshold",
            (
                "Swap activity reinforced the RAM-pressure deduction."
                if swap_raw else "Swap activity did not add a health deduction."
            ),
            {"average": swap_avg, "maximum": _number(window.get("swap_max"))},
            workload,
        ),
    ])

    disk_usage = _number(window.get("disk_usage_avg"))
    disk_capacity = _threshold_deduction("disk_capacity", disk_usage)
    group_deductions["disk_capacity"] = disk_capacity
    deductions.append(_deduction(
        "resource_condition", "disk_capacity", "disk_capacity_headroom",
        disk_capacity, disk_capacity, 35.0, "single_capacity_group",
        "disk_capacity_headroom_low" if disk_capacity else "disk_capacity_headroom_available",
        (
            "Disk-capacity headroom is low and reduced the resource-condition component."
            if disk_capacity else "Disk-capacity headroom did not require a deduction."
        ),
        {"usage_percent": disk_usage}, workload,
    ))

    read_rate = _number(window.get("disk_read_avg"))
    write_rate = _number(window.get("disk_write_avg"))
    disk_rate = max(value for value in (read_rate, write_rate) if value is not None) if any(
        value is not None for value in (read_rate, write_rate)
    ) else None
    disk_io_raw = _threshold_deduction("disk_io_bytes_per_second", disk_rate)
    disk_io_effective = disk_io_raw
    disk_reason = "disk_io_pressure_observed" if disk_io_raw else "disk_io_within_operational_threshold"
    disk_explanation = (
        "Disk I/O pressure reduced the resource-condition component."
        if disk_io_raw else "Disk transfer activity did not require a deduction."
    )
    disk_cap_reason = "read_and_write_rates_share_one_disk_io_group"
    if (
        disk_io_raw
        and workload in DISK_IO_EXPECTED_WORKLOADS
        and workload_confidence >= 0.6
    ):
        disk_io_effective = disk_io_raw * 0.4
        disk_reason = "disk_io_reduced_for_expected_workload"
        disk_explanation = (
            "Disk I/O was elevated but compatible with the development workload."
        )
        disk_cap_reason += ";workload_exception_applied"
    disk_io_effective = min(disk_io_effective, CORRELATION_CAPS["disk_io"])
    group_deductions["disk_io"] = disk_io_effective
    deductions.append(_deduction(
        "resource_condition", "disk_io", "disk_transfer_rate",
        disk_io_raw, disk_io_effective, CORRELATION_CAPS["disk_io"],
        disk_cap_reason, disk_reason, disk_explanation,
        {"read_bytes_per_second": read_rate, "write_bytes_per_second": write_rate},
        workload,
    ))

    for group, field, threshold_name, display_name in (
        ("cpu_thermal", "cpu_temperature_avg", "cpu_thermal", "CPU temperature"),
        ("gpu_thermal", "gpu_temperature_avg", "gpu_thermal", "GPU temperature"),
    ):
        value = _number(window.get(field))
        if value is None:
            inputs.append(_input(
                field, "optional_sensor", "unavailable_optional", None,
                "sensor_unavailable", RESOURCE_GROUP_WEIGHTS[group],
            ))
            explanations.append(
                f"{display_name} was excluded because the sensor is unavailable."
            )
            continue
        inputs.append(_input(
            field, "optional_sensor", "available", value, None,
            RESOURCE_GROUP_WEIGHTS[group],
        ))
        raw = _threshold_deduction(threshold_name, value)
        effective = min(raw, CORRELATION_CAPS["thermal"])
        group_deductions[group] = effective
        deductions.append(_deduction(
            "resource_condition", "thermal", field, raw, effective,
            CORRELATION_CAPS["thermal"], "available_thermal_sensor_group",
            f"{group}_elevated" if raw else f"{group}_within_range",
            (
                f"{display_name} reduced the resource-condition component."
                if raw else f"{display_name} did not require a deduction."
            ),
            {"celsius": value}, workload,
        ))

    for name in ("gpu_utilization_avg", "gpu_memory_avg"):
        value = _number(window.get(name))
        inputs.append(_input(
            name, "optional_sensor",
            "available" if value is not None else "unavailable_optional",
            value, None if value is not None else "sensor_unavailable", 0.0,
        ))

    power = _power_summary(connection, window)
    if not power["applicable"]:
        inputs.append(_input(
            "battery_power", "optional_hardware", "not_applicable", None,
            power["reason"], RESOURCE_GROUP_WEIGHTS["power"],
        ))
        explanations.append(
            "Battery condition was excluded because no applicable battery was available."
        )
    else:
        inputs.append(_input(
            "battery_power", "optional_hardware", "available", power, None,
            RESOURCE_GROUP_WEIGHTS["power"],
        ))
        battery = _number(power.get("battery_percent"))
        on_ac = power.get("ac_power_connected")
        raw = 15.0 if battery is not None and battery < 10 and on_ac is False else 0.0
        group_deductions["power"] = raw
        deductions.append(_deduction(
            "resource_condition", "power", "battery_condition",
            raw, raw, 15.0, "battery_applies_only_when_present",
            "low_battery_off_ac" if raw else "power_condition_available",
            (
                "Low battery while disconnected from AC reduced the component."
                if raw else "Available battery and power state required no deduction."
            ),
            power, workload,
        ))

    available_groups = {
        name: weight for name, weight in RESOURCE_GROUP_WEIGHTS.items()
        if name in group_deductions
    }
    available_weight = sum(available_groups.values())
    excluded_weight = sum(RESOURCE_GROUP_WEIGHTS.values()) - available_weight
    score = (
        sum(
            (100.0 - group_deductions[name]) * weight
            for name, weight in available_groups.items()
        ) / available_weight
        if available_weight else 0.0
    )
    component = {
        "component_name": "resource_condition",
        "component_score": round(max(0.0, min(100.0, score)), 4),
        "configured_weight": TOP_LEVEL_WEIGHTS["resource_condition"],
        "available_subcomponent_weight": available_weight,
        "excluded_subcomponent_weight": excluded_weight,
        "raw_deduction_total": sum(item["raw_deduction"] for item in deductions),
        "effective_deduction_total": sum(
            item["effective_deduction"] for item in deductions
        ),
        "data_quality_status": "sufficient",
        "reason_codes": sorted({
            item["reason_code"] for item in deductions
            if item["effective_deduction"] != 0
        }),
        "details": {
            "group_deductions": group_deductions,
            "available_groups": sorted(available_groups),
            "excluded_optional_groups": sorted(
                OPTIONAL_RESOURCE_GROUPS - set(available_groups)
            ),
            "normalization": "renormalized_across_available_resource_groups",
        },
    }
    return component, deductions, inputs, explanations


def _pressure_groups(window: dict[str, Any]) -> set[str]:
    groups: set[str] = set()
    workload = window.get("dominant_workload_class")
    confidence = _number(window.get("workload_confidence")) or 0.0
    if (
        (_number(window.get("cpu_avg")) or 0.0) >= 80.0
        and not (workload in CPU_EXPECTED_WORKLOADS and confidence >= 0.6)
    ):
        groups.add("cpu")
    if (_number(window.get("ram_avg")) or 0.0) >= 85.0:
        groups.add("memory")
    if (_number(window.get("swap_avg")) or 0.0) >= 25.0:
        groups.add("swap")
    if (_number(window.get("disk_usage_avg")) or 0.0) >= 90.0:
        groups.add("disk_capacity")
    return groups


def _temporal_context(
    connection,
    window: dict[str, Any],
    policy: HealthPolicy,
) -> dict[str, Any]:
    rows = [
        dict(row) for row in connection.execute(
            """SELECT * FROM feature_windows
            WHERE device_id = ? AND window_start_utc <= ?
            AND is_complete = 1 AND coverage_ratio >= ?
            ORDER BY window_start_utc DESC, id DESC LIMIT ?""",
            (
                window["device_id"],
                window["window_start_utc"],
                policy.minimum_coverage,
                policy.temporal_window_count,
            ),
        )
    ]
    if not rows or rows[0]["id"] != window["id"]:
        rows.insert(0, window)
    contiguous = [rows[0]]
    current_start = datetime.fromisoformat(rows[0]["window_start_utc"])
    for row in rows[1:]:
        prior_end = datetime.fromisoformat(row["window_end_utc"])
        gap = (current_start - prior_end).total_seconds()
        if gap < 0 or gap > 300:
            break
        contiguous.append(row)
        current_start = datetime.fromisoformat(row["window_start_utc"])
    current_groups = _pressure_groups(window)
    matching = set(current_groups)
    consecutive = 1 if matching else 0
    for row in contiguous[1:]:
        overlap = matching & _pressure_groups(row)
        if not overlap:
            break
        matching = overlap
        consecutive += 1

    chronological = list(reversed(contiguous))
    trend_metrics = {
        name: [
            float(row[name]) for row in chronological
            if _number(row.get(name)) is not None
        ]
        for name in ("ram_avg", "swap_avg", "disk_usage_avg", "cpu_avg")
    }
    increases = {
        "ram_avg": 5.0,
        "swap_avg": 5.0,
        "disk_usage_avg": 2.0,
        "cpu_avg": 20.0,
    }
    increasing = [
        name for name, values in trend_metrics.items()
        if len(values) >= 2 and values[-1] - values[0] >= increases[name]
    ]
    recovering = [
        name for name, values in trend_metrics.items()
        if len(values) >= 2 and values[0] - values[-1] >= increases[name]
    ]
    trend = (
        "increasing_deterioration" if increasing
        else "recovery" if recovering
        else "stable"
    )
    first = (
        contiguous[min(consecutive, len(contiguous)) - 1]["window_start_utc"]
        if consecutive else window["window_start_utc"]
    )
    duration = max(
        0.0,
        (
            datetime.fromisoformat(window["window_end_utc"])
            - datetime.fromisoformat(first)
        ).total_seconds(),
    )
    return {
        "pressure_groups": sorted(current_groups),
        "persistent_groups": sorted(matching),
        "consecutive_window_count": consecutive,
        "first_observed_utc": first,
        "most_recent_observed_utc": window["window_end_utc"],
        "persistence_duration_seconds": duration,
        "trend_direction": trend,
        "recovery_state": "recovering" if recovering else "not_recovering",
        "increasing_metrics": increasing,
        "recovering_metrics": recovering,
        "contiguous_window_count": len(contiguous),
    }


def _stability_component(
    temporal: dict[str, Any],
    workload: str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    deductions: list[dict[str, Any]] = []
    guidance: list[str] = []
    consecutive = temporal["consecutive_window_count"]
    persistence_raw = (
        0.0 if consecutive <= 1
        else 15.0 if consecutive == 2
        else 30.0 if consecutive < 6
        else 50.0
    )
    deductions.append(_deduction(
        "operating_stability", "temporal_pressure", "persistence",
        persistence_raw, persistence_raw, 50.0,
        "one_persistence_deduction_for_all_correlated_pressure_groups",
        (
            "sustained_resource_pressure" if consecutive >= 3
            else "repeated_resource_pressure" if consecutive == 2
            else "isolated_pressure_protected"
        ),
        (
            f"Resource pressure persisted across {consecutive} completed windows."
            if consecutive >= 2 else
            "An isolated pressure signal did not receive a persistence deduction."
        ),
        temporal["persistent_groups"], workload,
    ))
    trend_raw = 25.0 if temporal["trend_direction"] == "increasing_deterioration" else 0.0
    trend_effective = -8.0 if temporal["trend_direction"] == "recovery" else trend_raw
    deductions.append(_deduction(
        "operating_stability", "temporal_trend", "trend",
        trend_raw, trend_effective, 25.0,
        "one_directional_trend_contribution_across_correlated_metrics",
        temporal["trend_direction"],
        (
            "Pressure increased across recent completed windows."
            if trend_raw else
            "The score improved as pressure returned toward the operating range."
            if trend_effective < 0 else
            "Recent completed windows showed a stable condition."
        ),
        {
            "increasing": temporal["increasing_metrics"],
            "recovering": temporal["recovering_metrics"],
        },
        workload,
    ))
    cross_raw = min(
        25.0,
        max(0, len(temporal["pressure_groups"]) - 1) * 12.5,
    )
    deductions.append(_deduction(
        "operating_stability", "independent_pressure", "cross_group_pressure",
        cross_raw, cross_raw, 25.0,
        "only_independent_resource_groups_corroborate",
        "multiple_pressure_groups" if cross_raw else "no_cross_group_pressure",
        (
            "Multiple independent resource groups showed pressure."
            if cross_raw else "No independent cross-resource pressure was observed."
        ),
        temporal["pressure_groups"], workload,
    ))
    raw_total = sum(item["raw_deduction"] for item in deductions)
    effective_total = max(
        -10.0,
        min(
            CORRELATION_CAPS["stability"],
            sum(item["effective_deduction"] for item in deductions),
        ),
    )
    if temporal["recovery_state"] == "recovering":
        guidance.append(
            "Recovery evidence was observed in "
            + ", ".join(name.replace("_", " ") for name in temporal["recovering_metrics"])
            + "."
        )
    component = {
        "component_name": "operating_stability",
        "component_score": round(max(0.0, min(100.0, 100.0 - effective_total)), 4),
        "configured_weight": TOP_LEVEL_WEIGHTS["operating_stability"],
        "available_subcomponent_weight": 100.0,
        "excluded_subcomponent_weight": 0.0,
        "raw_deduction_total": raw_total,
        "effective_deduction_total": effective_total,
        "data_quality_status": "sufficient",
        "reason_codes": [
            item["reason_code"] for item in deductions
            if item["effective_deduction"] != 0
        ],
        "details": temporal,
    }
    return component, deductions, guidance


def _event_rows(connection, window: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(row) for row in connection.execute(
            """SELECT id, event_timestamp_utc, channel, provider_name, event_id,
            event_level, smartops_category, safe_summary
            FROM windows_events WHERE device_id = ? AND event_timestamp_utc >= ?
            AND event_timestamp_utc < ? ORDER BY event_timestamp_utc, id""",
            (
                window["device_id"],
                window["window_start_utc"],
                window["window_end_utc"],
            ),
        )
    ]


def _events_component(
    connection,
    window: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    workload = window.get("dominant_workload_class")
    events = _event_rows(connection, window)
    event_ids = [event["id"] for event in events]
    deductions: list[dict[str, Any]] = []
    category_fields = {
        "hardware": "hardware_event_count",
        "power": "power_event_count",
        "storage": "storage_event_count",
        "resource_exhaustion": "resource_exhaustion_count",
        "application_crash": "application_crash_count",
        "service_failure": "service_failure_count",
    }
    critical = int(window.get("critical_event_count") or 0)
    if critical:
        raw = min(70.0, critical * EVENT_DEDUCTIONS["critical_event"])
        deductions.append(_deduction(
            "operational_events", "serious_events", "critical_events",
            raw, raw, 70.0, "critical_events_capped_as_one_group",
            "critical_event_evidence", "Critical operational events reduced the event condition.",
            {"count": critical}, workload, event_ids,
        ))
    category_total = 0
    for category, field in category_fields.items():
        count = int(window.get(field) or 0)
        category_total += count
        if not count:
            continue
        raw = min(
            EVENT_DEDUCTIONS[category] * count,
            EVENT_DEDUCTIONS[category] + 12.0,
        )
        deductions.append(_deduction(
            "operational_events", "mapped_event_categories", category,
            raw, raw, EVENT_DEDUCTIONS[category] + 12.0,
            "repeated_related_events_receive_a_bounded_bonus",
            f"{category}_event_evidence",
            f"Mapped {category.replace('_', ' ')} evidence reduced the event condition.",
            {"count": count}, workload,
            [event["id"] for event in events if event["smartops_category"] == category],
        ))
    generic_errors = max(
        0,
        int(window.get("error_event_count") or 0) - category_total,
    )
    if generic_errors:
        raw = min(18.0, generic_errors * EVENT_DEDUCTIONS["error_event"])
        deductions.append(_deduction(
            "operational_events", "generic_errors", "error_events",
            raw, raw, 18.0, "mapped_categories_are_not_counted_again_as_generic_errors",
            "generic_error_evidence", "Unmapped Error events reduced the event condition.",
            {"count": generic_errors}, workload, event_ids,
        ))
    warnings = int(window.get("warning_event_count") or 0)
    warning_raw = (
        0.0 if warnings <= 1
        else min(10.0, (warnings - 1) * EVENT_DEDUCTIONS["warning_event"])
    )
    deductions.append(_deduction(
        "operational_events", "warnings", "warning_events",
        warning_raw, warning_raw, 10.0,
        "first_warning_is_protected_and_repetition_is_capped",
        "single_warning_no_deduction" if warnings <= 1 else "repeated_warning_evidence",
        (
            "A single Windows Warning did not lower health."
            if warnings == 1 else
            "Repeated selected Warning events created a bounded deduction."
            if warnings > 1 else
            "No selected Warning events were present."
        ),
        {"count": warnings}, workload, event_ids,
    ))
    raw_total = sum(item["raw_deduction"] for item in deductions)
    effective_total = min(CORRELATION_CAPS["operational_events"], raw_total)
    component = {
        "component_name": "operational_events",
        "component_score": round(max(0.0, 100.0 - effective_total), 4),
        "configured_weight": TOP_LEVEL_WEIGHTS["operational_events"],
        "available_subcomponent_weight": 100.0,
        "excluded_subcomponent_weight": 0.0,
        "raw_deduction_total": raw_total,
        "effective_deduction_total": effective_total,
        "data_quality_status": "sufficient",
        "reason_codes": [
            item["reason_code"] for item in deductions
            if item["effective_deduction"] != 0
        ],
        "details": {
            "events": events,
            "double_count_policy": "mapped categories are excluded from generic error count",
        },
    }
    return component, deductions, events


def _upstream_evidence(connection, window: dict[str, Any]) -> dict[str, Any]:
    deviation_row = connection.execute(
        "SELECT * FROM deviation_assessments WHERE feature_window_id = ?",
        (window["id"],),
    ).fetchone()
    deviation = dict(deviation_row) if deviation_row else None
    risk_row = connection.execute(
        """SELECT * FROM risk_assessments WHERE feature_window_id = ?
        ORDER BY evaluated_at_utc DESC, id DESC LIMIT 1""",
        (window["id"],),
    ).fetchone()
    risk = dict(risk_row) if risk_row else None
    baseline = None
    if deviation:
        baseline_row = connection.execute(
            "SELECT * FROM baseline_profiles WHERE id = ?",
            (deviation["baseline_id"],),
        ).fetchone()
        baseline = dict(baseline_row) if baseline_row else None
    if baseline is None:
        secondary = window.get("secondary_workload_context")
        primary = window.get("dominant_workload_class")
        baseline_row = connection.execute(
            """SELECT * FROM baseline_profiles
            WHERE device_id = ? AND workload_scope IN (?, ?, '__device__')
            ORDER BY CASE
                WHEN workload_scope = ? THEN 0
                WHEN workload_scope = ? THEN 1
                ELSE 2 END, id DESC
            LIMIT 1""",
            (
                window["device_id"],
                secondary,
                primary,
                secondary,
                primary,
            ),
        ).fetchone()
        baseline = dict(baseline_row) if baseline_row else None
    risk_event_contribution = 0.0
    candidates: list[str] = []
    if risk:
        component = connection.execute(
            """SELECT contribution FROM risk_evidence_components
            WHERE risk_assessment_id = ? AND component_name = 'serious_events'
            LIMIT 1""",
            (risk["id"],),
        ).fetchone()
        risk_event_contribution = float(component[0]) if component else 0.0
        candidates = [
            row[0] for row in connection.execute(
                """SELECT candidate_domain FROM root_cause_candidates
                WHERE risk_assessment_id = ? ORDER BY rank""",
                (risk["id"],),
            )
        ]
    return {
        "baseline": baseline,
        "deviation": deviation,
        "risk": risk,
        "risk_event_contribution": risk_event_contribution,
        "candidate_domains": candidates,
    }


def _learned_component(
    upstream: dict[str, Any],
    workload: str | None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]]]:
    deviation = upstream["deviation"]
    risk = upstream["risk"]
    deductions: list[dict[str, Any]] = []
    inputs: list[dict[str, Any]] = []
    if deviation and deviation.get("deviation_index") is not None:
        value = float(deviation["deviation_index"])
        raw = min(50.0, value * 0.5)
        deductions.append(_deduction(
            "learned_evidence", "phase3a_deviation", "deviation_index",
            raw, raw, 50.0, "phase3a_contributes_once",
            "phase3a_deviation_available",
            "Phase 3A deviation evidence contributed to learned condition.",
            {"deviation_index": value, "overall_level": deviation["overall_level"]},
            workload,
        ))
        inputs.append(_input(
            "phase3a_deviation", "learned_evidence", "available",
            {"id": deviation["id"], "deviation_index": value}, None, 0.5,
        ))
    else:
        inputs.append(_input(
            "phase3a_deviation", "learned_evidence", "not_applicable", None,
            "deviation_not_evaluated", 0.5,
        ))
    if risk:
        risk_value = float(risk["risk_evidence_index"])
        non_event_value = max(
            0.0,
            risk_value - float(upstream["risk_event_contribution"]),
        )
        raw = min(60.0, risk_value * 0.6)
        effective = min(40.0, non_event_value * 0.3)
        deductions.append(_deduction(
            "learned_evidence", "phase3b_risk", "risk_evidence_index",
            raw, effective, 40.0,
            "phase3b_event_overlap_removed_and_current_metric_overlap_reduced",
            "phase3b_risk_evidence_available",
            (
                "Phase 3B evidence contributed after removing event overlap and "
                "reducing duplication with current resource condition."
            ),
            {
                "risk_evidence_index": risk_value,
                "event_contribution_removed": upstream["risk_event_contribution"],
                "candidate_domains": upstream["candidate_domains"],
            },
            workload,
        ))
        inputs.append(_input(
            "phase3b_risk", "learned_evidence", "available",
            {"id": risk["id"], "risk_evidence_index": risk_value}, None, 0.5,
        ))
    else:
        inputs.append(_input(
            "phase3b_risk", "learned_evidence", "not_applicable", None,
            "risk_evidence_not_evaluated", 0.5,
        ))
    if not deductions:
        return None, deductions, inputs
    raw_total = sum(item["raw_deduction"] for item in deductions)
    effective_total = min(
        CORRELATION_CAPS["learned_evidence"],
        sum(item["effective_deduction"] for item in deductions),
    )
    available_subweight = sum(
        item["applicable_weight"] for item in inputs
        if item["availability_status"] == "available"
    )
    component = {
        "component_name": "learned_evidence",
        "component_score": round(max(0.0, 100.0 - effective_total), 4),
        "configured_weight": TOP_LEVEL_WEIGHTS["learned_evidence"],
        "available_subcomponent_weight": available_subweight,
        "excluded_subcomponent_weight": 1.0 - available_subweight,
        "raw_deduction_total": raw_total,
        "effective_deduction_total": effective_total,
        "data_quality_status": "sufficient",
        "reason_codes": [item["reason_code"] for item in deductions],
        "details": {
            "candidate_domains": upstream["candidate_domains"],
            "double_count_policy": (
                "Phase 3B serious-event contribution removed; remaining risk "
                "contribution reduced for overlap with current resources."
            ),
        },
    }
    return component, deductions, inputs


def _data_confidence(
    window: dict[str, Any],
    inputs: list[dict[str, Any]],
    upstream: dict[str, Any],
) -> tuple[float, dict[str, float]]:
    coverage = min(1.0, max(0.0, float(window.get("coverage_ratio") or 0.0)))
    required = [
        item for item in inputs if item["input_category"] == "required_core"
    ]
    required_ratio = (
        sum(item["availability_status"] == "available" for item in required)
        / len(required)
        if required else 0.0
    )
    workload_score = min(
        1.0,
        max(0.0, float(window.get("workload_confidence") or 0.0)),
    )
    optional = [
        item for item in inputs
        if item["input_category"] == "optional_sensor"
    ]
    optional_ratio = (
        sum(item["availability_status"] == "available" for item in optional)
        / len(optional)
        if optional else 1.0
    )
    historical = 0.0
    baseline = upstream["baseline"]
    if baseline:
        historical += {
            "established": 0.4,
            "provisional": 0.25,
            "collecting_data": 0.1,
        }.get(baseline["readiness_state"], 0.0)
    if upstream["deviation"]:
        historical += 0.25
    if upstream["risk"]:
        historical += 0.35
    historical = min(1.0, historical)
    factors = {
        "coverage": coverage,
        "required_metrics": required_ratio,
        "workload_context": workload_score,
        "optional_sensors": optional_ratio,
        "historical_readiness": historical,
    }
    confidence = sum(
        factors[name] * weight
        for name, weight in DATA_CONFIDENCE_WEIGHTS.items()
    )
    return round(confidence * 100.0, 4), factors


def _not_evaluated_assessment(
    window: dict[str, Any],
    reasons: list[str],
    inputs: list[dict[str, Any]],
    upstream: dict[str, Any],
) -> dict[str, Any]:
    now = _utc_now().isoformat()
    confidence, factors = _data_confidence(window, inputs, upstream)
    guidance = [
        {
            "guidance_type": "explanation",
            "related_component": "data_confidence",
            "sequence": index,
            "guidance_text": reason.replace("_", " ").capitalize() + ".",
        }
        for index, reason in enumerate(reasons, start=1)
    ]
    guidance.extend(
        {
            "guidance_type": "limitation",
            "related_component": "",
            "sequence": index,
            "guidance_text": text,
        }
        for index, text in enumerate(LIMITATIONS, start=1)
    )
    return {
        "feature_window_id": window["id"],
        "device_id": window["device_id"],
        "window_start_utc": window["window_start_utc"],
        "window_end_utc": window["window_end_utc"],
        "assessed_at_utc": now,
        "system_health_score": None,
        "health_band": "not_evaluated",
        "evaluation_state": "not_evaluated",
        "data_confidence": confidence,
        "coverage_ratio": float(window.get("coverage_ratio") or 0.0),
        "workload_context": window.get("dominant_workload_class"),
        "workload_confidence": window.get("workload_confidence"),
        "workload_rule_version": window.get("workload_rule_version"),
        "available_component_weight": 0.0,
        "excluded_component_weight": 100.0,
        "normalization_method": "not_evaluated_no_normalization",
        "reason_codes": reasons,
        "temporal": {
            "first_observed_utc": window["window_start_utc"],
            "most_recent_observed_utc": window["window_end_utc"],
            "consecutive_window_count": 0,
            "persistence_duration_seconds": 0.0,
            "trend_direction": "not_evaluated",
            "recovery_state": "not_evaluated",
        },
        "components": [],
        "deductions": [],
        "inputs": inputs + _upstream_input_status(upstream),
        "guidance": guidance,
        "data_confidence_factors": factors,
        "upstream": upstream,
        "feature_signature": _feature_signature(window),
        "feature_updated_at_utc": window["updated_at_utc"],
        "created_at_utc": now,
        "updated_at_utc": now,
    }


def _upstream_input_status(upstream: dict[str, Any]) -> list[dict[str, Any]]:
    baseline = upstream["baseline"]
    deviation = upstream["deviation"]
    risk = upstream["risk"]
    return [
        _input(
            "phase3a_baseline", "historical_readiness",
            "available" if baseline and baseline["readiness_state"] in {"provisional", "established"}
            else "excluded_by_context",
            (
                {"id": baseline["id"], "readiness": baseline["readiness_state"]}
                if baseline else None
            ),
            None if baseline and baseline["readiness_state"] in {"provisional", "established"}
            else "baseline_not_ready",
        ),
        _input(
            "phase3a_deviation", "historical_readiness",
            "available" if deviation else "not_applicable",
            {"id": deviation["id"]} if deviation else None,
            None if deviation else "deviation_not_evaluated",
        ),
        _input(
            "phase3b_risk", "historical_readiness",
            "available" if risk else "not_applicable",
            {"id": risk["id"]} if risk else None,
            None if risk else "risk_evidence_not_evaluated",
        ),
    ]


def _build_assessment(
    connection,
    window: dict[str, Any],
    policy: HealthPolicy,
) -> dict[str, Any]:
    eligible, reasons, required_inputs = evaluation_eligibility(window, policy)
    upstream = _upstream_evidence(connection, window)
    if not eligible:
        return _not_evaluated_assessment(
            window, reasons, required_inputs, upstream
        )

    resource, resource_deductions, optional_inputs, exclusions = _resource_component(
        connection, window
    )
    temporal = _temporal_context(connection, window, policy)
    stability, stability_deductions, recovery_guidance = _stability_component(
        temporal, window.get("dominant_workload_class")
    )
    events_component, event_deductions, events = _events_component(
        connection, window
    )
    learned, learned_deductions, learned_inputs = _learned_component(
        upstream, window.get("dominant_workload_class")
    )
    components = [resource, stability, events_component]
    if learned:
        components.append(learned)

    available_weight = sum(item["configured_weight"] for item in components)
    excluded_weight = sum(TOP_LEVEL_WEIGHTS.values()) - available_weight
    for component in components:
        component["effective_weight"] = (
            component["configured_weight"] / available_weight
            if available_weight else 0.0
        )
    score = sum(
        component["component_score"] * component["effective_weight"]
        for component in components
    )
    score = round(max(0.0, min(100.0, score)), 4)
    baseline = upstream["baseline"]
    established = bool(
        baseline
        and baseline["readiness_state"] == "established"
        and upstream["deviation"]
        and upstream["risk"]
    )
    state = "established" if established else "provisional"
    inputs = required_inputs + optional_inputs + learned_inputs
    # Avoid duplicate upstream input names supplied by the learned component.
    existing_names = {item["input_name"] for item in inputs}
    inputs.extend(
        item for item in _upstream_input_status(upstream)
        if item["input_name"] not in existing_names
    )
    confidence, confidence_factors = _data_confidence(window, inputs, upstream)
    reason_codes = sorted({
        code for component in components for code in component["reason_codes"]
    })
    if state == "provisional":
        reason_codes.append("historical_evidence_not_established")
    guidance: list[dict[str, Any]] = []
    explanation_texts = [
        item["explanation"]
        for item in (
            resource_deductions
            + stability_deductions
            + event_deductions
            + learned_deductions
        )
        if item["effective_deduction"] != 0
    ]
    explanation_texts.extend(exclusions)
    if state == "provisional":
        explanation_texts.append(
            "This result is provisional because the device baseline and Risk "
            "Evidence Index are not yet fully established."
        )
    for index, text in enumerate(dict.fromkeys(explanation_texts), start=1):
        guidance.append({
            "guidance_type": "explanation",
            "related_component": "",
            "sequence": index,
            "guidance_text": text,
        })
    active_groups = {
        item["contribution_group"]
        for item in (
            resource_deductions
            + stability_deductions
            + event_deductions
            + learned_deductions
        )
        if item["effective_deduction"] > 0
    }
    recommendations: list[str] = []
    if "cpu" in active_groups:
        recommendations.append(DIAGNOSTIC_STEPS["cpu"])
    if "memory_and_swap" in active_groups:
        recommendations.append(DIAGNOSTIC_STEPS["memory"])
    if "disk_capacity" in active_groups:
        recommendations.append(DIAGNOSTIC_STEPS["disk_capacity"])
    if "disk_io" in active_groups:
        recommendations.append(DIAGNOSTIC_STEPS["disk_io"])
    if "thermal" in active_groups:
        recommendations.append(DIAGNOSTIC_STEPS["thermal"])
    if event_deductions and any(
        item["effective_deduction"] > 0 for item in event_deductions
    ):
        recommendations.append(DIAGNOSTIC_STEPS["events"])
    if temporal["consecutive_window_count"] > 1:
        recommendations.append(DIAGNOSTIC_STEPS["stability"])
    if learned:
        recommendations.append(DIAGNOSTIC_STEPS["learned"])
    for index, text in enumerate(dict.fromkeys(recommendations), start=1):
        guidance.append({
            "guidance_type": "recommendation",
            "related_component": "",
            "sequence": index,
            "guidance_text": text,
        })
    for index, text in enumerate(recovery_guidance, start=1):
        guidance.append({
            "guidance_type": "improvement",
            "related_component": "operating_stability",
            "sequence": index,
            "guidance_text": text,
        })
    for index, text in enumerate(LIMITATIONS, start=1):
        guidance.append({
            "guidance_type": "limitation",
            "related_component": "",
            "sequence": index,
            "guidance_text": text,
        })
    now = _utc_now().isoformat()
    return {
        "feature_window_id": window["id"],
        "device_id": window["device_id"],
        "window_start_utc": window["window_start_utc"],
        "window_end_utc": window["window_end_utc"],
        "assessed_at_utc": now,
        "system_health_score": score,
        "health_band": health_band_for_score(score),
        "evaluation_state": state,
        "data_confidence": confidence,
        "coverage_ratio": float(window["coverage_ratio"]),
        "workload_context": window["dominant_workload_class"],
        "workload_confidence": window["workload_confidence"],
        "workload_rule_version": window.get("workload_rule_version"),
        "available_component_weight": available_weight,
        "excluded_component_weight": excluded_weight,
        "normalization_method": "weighted_mean_renormalized_across_available_components",
        "reason_codes": sorted(set(reason_codes)),
        "temporal": temporal,
        "components": components,
        "deductions": (
            resource_deductions
            + stability_deductions
            + event_deductions
            + learned_deductions
        ),
        "inputs": inputs,
        "guidance": guidance,
        "events": events,
        "data_confidence_factors": confidence_factors,
        "upstream": upstream,
        "feature_signature": _feature_signature(window),
        "feature_updated_at_utc": window["updated_at_utc"],
        "created_at_utc": now,
        "updated_at_utc": now,
    }


def _persist_assessment(connection, assessment: dict[str, Any]) -> int:
    upstream = assessment["upstream"]
    baseline = upstream["baseline"]
    deviation = upstream["deviation"]
    risk = upstream["risk"]
    existing = connection.execute(
        """SELECT id, created_at_utc FROM health_assessments
        WHERE feature_window_id = ? AND algorithm_version = ?
        AND configuration_version = ?""",
        (
            assessment["feature_window_id"],
            ALGORITHM_VERSION,
            CONFIGURATION_VERSION,
        ),
    ).fetchone()
    created_at = existing["created_at_utc"] if existing else assessment["created_at_utc"]
    values = (
        assessment["feature_window_id"],
        assessment["device_id"],
        assessment["window_start_utc"],
        assessment["window_end_utc"],
        assessment["assessed_at_utc"],
        assessment["system_health_score"],
        assessment["health_band"],
        assessment["evaluation_state"],
        assessment["data_confidence"],
        assessment["coverage_ratio"],
        assessment["workload_context"],
        assessment["workload_confidence"],
        assessment["workload_rule_version"],
        assessment["available_component_weight"],
        assessment["excluded_component_weight"],
        assessment["normalization_method"],
        baseline["id"] if baseline else None,
        baseline["algorithm_version"] if baseline else None,
        baseline["configuration_version"] if baseline else None,
        baseline["updated_at_utc"] if baseline else None,
        deviation["id"] if deviation else None,
        deviation["evaluation_timestamp_utc"] if deviation else None,
        risk["id"] if risk else None,
        risk["evaluated_at_utc"] if risk else None,
        deviation["baseline_version_id"] if deviation else (
            risk["baseline_version_id"] if risk else None
        ),
        deviation["baseline_rule_version"] if deviation else (
            risk["baseline_rule_version"] if risk else None
        ),
        ALGORITHM_VERSION,
        CONFIGURATION_VERSION,
        json.dumps(assessment["reason_codes"]),
        assessment["temporal"]["first_observed_utc"],
        assessment["temporal"]["most_recent_observed_utc"],
        assessment["temporal"]["consecutive_window_count"],
        assessment["temporal"]["persistence_duration_seconds"],
        assessment["temporal"]["trend_direction"],
        assessment["temporal"]["recovery_state"],
        assessment["feature_signature"],
        assessment["feature_updated_at_utc"],
        created_at,
        assessment["updated_at_utc"],
    )
    connection.execute(
        """INSERT INTO health_assessments (
            feature_window_id, device_id, window_start_utc, window_end_utc,
            assessed_at_utc, system_health_score, health_band, evaluation_state,
            data_confidence, coverage_ratio, workload_context,
            workload_confidence, workload_rule_version, available_component_weight,
            excluded_component_weight, normalization_method, baseline_id,
            baseline_algorithm_version, baseline_configuration_version,
            baseline_updated_at_utc, deviation_assessment_id,
            deviation_evaluated_at_utc, risk_assessment_id,
            risk_evaluated_at_utc, baseline_version_id, baseline_rule_version,
            algorithm_version, configuration_version,
            reason_codes_json, first_observed_utc, most_recent_observed_utc,
            consecutive_window_count, persistence_duration_seconds,
            trend_direction, recovery_state, feature_signature,
            feature_updated_at_utc,
            created_at_utc, updated_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                  ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(feature_window_id, algorithm_version, configuration_version)
        DO UPDATE SET
            device_id=excluded.device_id,
            window_start_utc=excluded.window_start_utc,
            window_end_utc=excluded.window_end_utc,
            assessed_at_utc=excluded.assessed_at_utc,
            system_health_score=excluded.system_health_score,
            health_band=excluded.health_band,
            evaluation_state=excluded.evaluation_state,
            data_confidence=excluded.data_confidence,
            coverage_ratio=excluded.coverage_ratio,
            workload_context=excluded.workload_context,
            workload_confidence=excluded.workload_confidence,
            workload_rule_version=excluded.workload_rule_version,
            available_component_weight=excluded.available_component_weight,
            excluded_component_weight=excluded.excluded_component_weight,
            normalization_method=excluded.normalization_method,
            baseline_id=excluded.baseline_id,
            baseline_algorithm_version=excluded.baseline_algorithm_version,
            baseline_configuration_version=excluded.baseline_configuration_version,
            baseline_updated_at_utc=excluded.baseline_updated_at_utc,
            deviation_assessment_id=excluded.deviation_assessment_id,
            deviation_evaluated_at_utc=excluded.deviation_evaluated_at_utc,
            risk_assessment_id=excluded.risk_assessment_id,
            risk_evaluated_at_utc=excluded.risk_evaluated_at_utc,
            baseline_version_id=excluded.baseline_version_id,
            baseline_rule_version=excluded.baseline_rule_version,
            reason_codes_json=excluded.reason_codes_json,
            first_observed_utc=excluded.first_observed_utc,
            most_recent_observed_utc=excluded.most_recent_observed_utc,
            consecutive_window_count=excluded.consecutive_window_count,
            persistence_duration_seconds=excluded.persistence_duration_seconds,
            trend_direction=excluded.trend_direction,
            recovery_state=excluded.recovery_state,
            feature_signature=excluded.feature_signature,
            feature_updated_at_utc=excluded.feature_updated_at_utc,
            updated_at_utc=excluded.updated_at_utc""",
        values,
    )
    assessment_id = int(connection.execute(
        """SELECT id FROM health_assessments
        WHERE feature_window_id = ? AND algorithm_version = ?
        AND configuration_version = ?""",
        (
            assessment["feature_window_id"],
            ALGORITHM_VERSION,
            CONFIGURATION_VERSION,
        ),
    ).fetchone()[0])
    for table in (
        "health_component_scores",
        "health_deductions",
        "health_input_status",
        "health_guidance",
    ):
        connection.execute(
            f"DELETE FROM {table} WHERE health_assessment_id = ?",
            (assessment_id,),
        )
    for component in assessment["components"]:
        connection.execute(
            """INSERT INTO health_component_scores (
                health_assessment_id, component_name, component_score,
                configured_weight, effective_weight,
                available_subcomponent_weight, excluded_subcomponent_weight,
                raw_deduction_total, effective_deduction_total,
                data_quality_status, reason_codes_json, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                assessment_id,
                component["component_name"],
                component["component_score"],
                component["configured_weight"],
                component["effective_weight"],
                component["available_subcomponent_weight"],
                component["excluded_subcomponent_weight"],
                component["raw_deduction_total"],
                component["effective_deduction_total"],
                component["data_quality_status"],
                json.dumps(component["reason_codes"]),
                json.dumps(component["details"]),
            ),
        )
    for item in assessment["deductions"]:
        connection.execute(
            """INSERT INTO health_deductions (
                health_assessment_id, component_name, contribution_group,
                signal_name, raw_deduction, effective_deduction,
                maximum_deduction, correlation_or_cap_reason, reason_code,
                explanation, supporting_value_json,
                supporting_event_ids_json, workload_context
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                assessment_id,
                item["component_name"],
                item["contribution_group"],
                item["signal_name"],
                item["raw_deduction"],
                item["effective_deduction"],
                item["maximum_deduction"],
                item["correlation_or_cap_reason"],
                item["reason_code"],
                item["explanation"],
                json.dumps(item["supporting_value"]),
                json.dumps(item["supporting_event_ids"]),
                item["workload_context"],
            ),
        )
    for item in assessment["inputs"]:
        connection.execute(
            """INSERT INTO health_input_status (
                health_assessment_id, input_name, input_category,
                availability_status, observed_value_json, excluded_reason,
                applicable_weight
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                assessment_id,
                item["input_name"],
                item["input_category"],
                item["availability_status"],
                json.dumps(item["observed_value"]),
                item["excluded_reason"],
                item["applicable_weight"],
            ),
        )
    for item in assessment["guidance"]:
        connection.execute(
            """INSERT INTO health_guidance (
                health_assessment_id, guidance_type, related_component,
                sequence, guidance_text
            ) VALUES (?, ?, ?, ?, ?)""",
            (
                assessment_id,
                item["guidance_type"],
                item["related_component"],
                item["sequence"],
                item["guidance_text"],
            ),
        )
    return assessment_id


def _unchanged(
    connection,
    window: dict[str, Any],
    upstream: dict[str, Any],
) -> bool:
    existing = connection.execute(
        """SELECT * FROM health_assessments WHERE feature_window_id = ?
        AND algorithm_version = ? AND configuration_version = ?""",
        (window["id"], ALGORITHM_VERSION, CONFIGURATION_VERSION),
    ).fetchone()
    if (
        existing is None
        or existing["feature_signature"] != _feature_signature(window)
    ):
        return False
    baseline = upstream["baseline"]
    deviation = upstream["deviation"]
    risk = upstream["risk"]
    return (
        existing["baseline_updated_at_utc"]
        == (baseline["updated_at_utc"] if baseline else None)
        and existing["deviation_evaluated_at_utc"]
        == (deviation["evaluation_timestamp_utc"] if deviation else None)
        and existing["risk_evaluated_at_utc"]
        == (risk["evaluated_at_utc"] if risk else None)
    )


def evaluate_database(
    database_path: Path | None = None,
    device_id: str | None = None,
    start: str | None = None,
    end: str | None = None,
    window_id: int | None = None,
    force: bool = False,
    command: str = "evaluate",
    policy: HealthPolicy = DEFAULT_POLICY,
) -> dict[str, int]:
    path = initialize_database(database_path or get_database_path())
    started = _utc_now().isoformat()
    evaluated = 0
    not_evaluated = 0
    skipped: Counter[str] = Counter()
    failures: list[str] = []
    with database_connection(path) as connection:
        clauses = ["1 = 1"]
        parameters: list[Any] = []
        for value, clause in (
            (device_id, "device_id = ?"),
            (start, "window_start_utc >= ?"),
            (end, "window_start_utc <= ?"),
            (window_id, "id = ?"),
        ):
            if value is not None:
                clauses.append(clause)
                parameters.append(value)
        windows = [
            dict(row) for row in connection.execute(
                f"""SELECT * FROM feature_windows
                WHERE {" AND ".join(clauses)}
                ORDER BY window_start_utc, id""",
                parameters,
            )
        ]
        for window in windows:
            upstream = _upstream_evidence(connection, window)
            if not force and _unchanged(connection, window, upstream):
                skipped["already_evaluated_unchanged"] += 1
                continue
            try:
                assessment = _build_assessment(connection, window, policy)
                with connection:
                    _persist_assessment(connection, assessment)
                if assessment["evaluation_state"] == "not_evaluated":
                    not_evaluated += 1
                else:
                    evaluated += 1
            except Exception as error:
                failures.append(type(error).__name__)
                skipped["evaluation_error"] += 1
        with connection:
            connection.execute(
                """INSERT INTO health_evaluation_runs (
                    started_at_utc, finished_at_utc, command, device_id,
                    start_timestamp_utc, end_timestamp_utc, feature_window_id,
                    force_requested, algorithm_version, configuration_version,
                    status, assessed_count, not_evaluated_count, skipped_count,
                    skip_reasons_json, error_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    started,
                    _utc_now().isoformat(),
                    command,
                    device_id,
                    start,
                    end,
                    window_id,
                    int(force),
                    ALGORITHM_VERSION,
                    CONFIGURATION_VERSION,
                    "error" if failures else "success",
                    evaluated,
                    not_evaluated,
                    sum(skipped.values()),
                    json.dumps(dict(sorted(skipped.items()))),
                    ",".join(sorted(set(failures))) if failures else None,
                ),
            )
    return {
        "evaluated": evaluated,
        "not_evaluated": not_evaluated,
        "skipped": sum(skipped.values()),
    }


def health_status(
    database_path: Path | None = None,
    device_id: str | None = None,
) -> dict[str, Any]:
    path = initialize_database(database_path or get_database_path())
    with database_connection(path) as connection:
        resolved = device_id
        if resolved is None:
            row = connection.execute(
                """SELECT device_id FROM feature_windows
                ORDER BY window_start_utc DESC, id DESC LIMIT 1"""
            ).fetchone()
            resolved = row[0] if row else None
        latest_window = connection.execute(
            """SELECT * FROM feature_windows
            WHERE (? IS NULL OR device_id = ?)
            ORDER BY window_start_utc DESC, id DESC LIMIT 1""",
            (resolved, resolved),
        ).fetchone()
        latest = connection.execute(
            """SELECT id, feature_window_id, system_health_score, health_band,
            evaluation_state, data_confidence, assessed_at_utc,
            reason_codes_json
            FROM health_assessments
            WHERE (? IS NULL OR device_id = ?)
            ORDER BY window_start_utc DESC, id DESC LIMIT 1""",
            (resolved, resolved),
        ).fetchone()
        counts = {
            row["evaluation_state"]: int(row["count"])
            for row in connection.execute(
                """SELECT evaluation_state, COUNT(*) AS count
                FROM health_assessments
                WHERE (? IS NULL OR device_id = ?)
                GROUP BY evaluation_state""",
                (resolved, resolved),
            )
        }
        latest_run = connection.execute(
            """SELECT * FROM health_evaluation_runs
            WHERE (? IS NULL OR device_id = ? OR device_id IS NULL)
            ORDER BY finished_at_utc DESC, id DESC LIMIT 1""",
            (resolved, resolved),
        ).fetchone()
    if latest:
        state = latest["evaluation_state"]
        reasons = json.loads(latest["reason_codes_json"])
    elif latest_window:
        eligible, reasons, _ = evaluation_eligibility(dict(latest_window))
        state = "provisional" if eligible else "not_evaluated"
    else:
        state, reasons = "not_evaluated", ["no_feature_windows"]
    return {
        "status": state,
        "device_id": resolved,
        "latest_assessment": (
            {
                key: value
                for key, value in dict(latest).items()
                if key != "reason_codes_json"
            }
            if latest else None
        ),
        "latest_feature_window_id": latest_window["id"] if latest_window else None,
        "reason_codes": reasons,
        "assessment_counts": counts,
        "algorithm_version": ALGORITHM_VERSION,
        "configuration_version": CONFIGURATION_VERSION,
        "interpretation": INTERPRETATION,
        "minimum_coverage": DEFAULT_POLICY.minimum_coverage,
        "required_core_inputs": list(REQUIRED_CORE_INPUTS),
        "latest_run": dict(latest_run) if latest_run else None,
    }


def maybe_evaluate_health(database_path: Path | None = None) -> None:
    path = initialize_database(database_path or get_database_path())
    with database_connection(path) as connection:
        pending = any(
            not _unchanged(
                connection,
                dict(window),
                _upstream_evidence(connection, dict(window)),
            )
            for window in connection.execute(
                "SELECT * FROM feature_windows ORDER BY window_start_utc, id"
            )
        )
    if pending:
        evaluate_database(path, command="agent")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SmartOps explainable System Health Score"
    )
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--status", action="store_true")
    actions.add_argument("--evaluate", action="store_true")
    actions.add_argument("--backfill", action="store_true")
    parser.add_argument("--device")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--window-id", type=int)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.status:
        print(json.dumps(health_status(device_id=args.device), indent=2))
        return
    command = "backfill" if args.backfill else "evaluate"
    result = evaluate_database(
        device_id=args.device,
        start=args.start,
        end=args.end,
        window_id=args.window_id,
        force=args.force,
        command=command,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
