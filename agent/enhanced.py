"""Capability-aware Phase 7B enhanced evidence collection.

The collector uses supported local Windows interfaces with bounded timeouts.
All outputs are shadow-mode evidence and are deliberately isolated from the
existing analytics and alert evaluators.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import psutil

from agent.config import get_database_path
from agent.device import get_or_create_device_id
from agent.enhanced_catalogue import (
    ALGORITHM_VERSION,
    CONFIGURATION_VERSION,
    EXPLICIT_UNAVAILABLE_SIGNALS,
    HARDWARE_COLLECTION_SECONDS,
    HARDWARE_SIGNAL_DETAILS,
    HARDWARE_TIMEOUT_SECONDS,
    PERFORMANCE_COLLECTION_SECONDS,
    PERFORMANCE_SIGNALS,
    PERFORMANCE_TIMEOUT_SECONDS,
    RETENTION_MAINTENANCE_SECONDS,
)
from backend.enhanced_repository import (
    collector_is_due,
    collector_schedule,
    create_collection_run,
    finish_collection_run,
    get_enhanced_status,
    run_retention,
    store_enhanced_events,
    store_signal_samples,
    unmirrored_windows_events,
    update_collector_state,
)


LOGGER = logging.getLogger("smartops.enhanced")
PowerShellRunner = Callable[[str, int], Any]
TEMPORARY_QUERY_ATTEMPTS = 2


@dataclass
class EnhancedRunContext:
    run_id: int
    started_monotonic: float
    process_cpu_started: float
    process_rss_before_bytes: int | None
    database_bytes_before: int | None
    scheduled_at_utc: str | None = None
    schedule_delay_seconds: float | None = None
    agent_session_id: str | None = None
    collection_duration_ms: float = 0.0
    successful_collectors: list[str] = field(default_factory=list)
    failed_collectors: list[str] = field(default_factory=list)


def _safe_file_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _safe_rss() -> int | None:
    try:
        return int(psutil.Process().memory_info().rss)
    except (OSError, psutil.Error):
        return None


def _run_powershell_json(script: str, timeout_seconds: int) -> Any:
    """Run one local read-only PowerShell query with a hard timeout."""
    if sys.platform != "win32":
        raise OSError("Windows PowerShell data sources require Windows.")
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    output = result.stdout.strip()
    return json.loads(output) if output else None


def _run_with_bounded_timeout_retry(
    runner: PowerShellRunner,
    script: str,
    timeout_seconds: int,
) -> Any:
    """Retry one transient timeout; never build a background backlog."""
    for attempt in range(TEMPORARY_QUERY_ATTEMPTS):
        try:
            return runner(script, timeout_seconds)
        except subprocess.TimeoutExpired:
            if attempt + 1 >= TEMPORARY_QUERY_ATTEMPTS:
                raise
            LOGGER.warning("Optional Windows query timed out; retrying once.")
    raise RuntimeError("unreachable")


def _signal(
    *,
    key: str,
    group: str,
    label: str,
    unit: str,
    value: float | int | None,
    status: str,
    reason: str | None,
    source: str,
    source_status: str,
    frequency_seconds: int,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    numeric: float | None
    if value is None:
        numeric = None
    else:
        numeric = float(value)
        if not math.isfinite(numeric):
            numeric = None
            status = "unavailable"
            reason = "non_finite_source_value"
    return {
        "signal_key": key,
        "signal_group": group,
        "signal_label": label,
        "unit": unit,
        "numeric_value": numeric,
        "availability_status": status,
        "reason_code": reason,
        "source_name": source,
        "source_status": source_status,
        "collection_frequency_seconds": frequency_seconds,
        "details": dict(details or {}),
    }


def _unavailable_performance_signals(
    status: str,
    reason: str,
) -> list[dict[str, Any]]:
    source_status = "timeout" if status == "timeout" else "unavailable"
    return [
        _signal(
            key=definition.key,
            group=definition.group,
            label=definition.label,
            unit=definition.unit,
            value=None,
            status=status,
            reason=reason,
            source="Windows Performance Counters",
            source_status=source_status,
            frequency_seconds=PERFORMANCE_COLLECTION_SECONDS,
        )
        for definition in PERFORMANCE_SIGNALS
    ]


def collect_performance_signals(
    runner: PowerShellRunner = _run_powershell_json,
) -> tuple[list[dict[str, Any]], bool, str | None]:
    """Collect one bounded sample from official Windows performance counters."""
    if sys.platform != "win32":
        return (
            _unavailable_performance_signals(
                "unsupported",
                "unsupported_platform",
            ),
            False,
            "unsupported_platform",
        )
    quoted_paths = ",\n".join(
        "'" + definition.counter_path.replace("'", "''") + "'"
        for definition in PERFORMANCE_SIGNALS
    )
    script = f"""
$ErrorActionPreference = 'Stop'
$paths = @(
{quoted_paths}
)
$sample = Get-Counter -Counter $paths -SampleInterval 1 -MaxSamples 1
@($sample.CounterSamples | ForEach-Object {{
    [pscustomobject]@{{
        path = $_.Path
        cooked_value = $_.CookedValue
        status = [uint32]$_.Status
    }}
}}) | ConvertTo-Json -Depth 4 -Compress
"""
    try:
        raw = _run_with_bounded_timeout_retry(
            runner, script, PERFORMANCE_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired:
        return (
            _unavailable_performance_signals("timeout", "query_timeout"),
            False,
            "query_timeout",
        )
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, json.JSONDecodeError):
        return (
            _unavailable_performance_signals(
                "permission_limited",
                "counter_query_failed_or_permission_limited",
            ),
            False,
            "counter_query_failed_or_permission_limited",
        )

    rows = raw if isinstance(raw, list) else [raw] if isinstance(raw, dict) else []
    by_suffix: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        path = str(row.get("path") or "").casefold()
        by_suffix[path] = row

    signals: list[dict[str, Any]] = []
    for definition in PERFORMANCE_SIGNALS:
        if definition.transform == "active_from_idle":
            rows_for_signal = [
                candidate
                for path, candidate in by_suffix.items()
                if "\\physicaldisk(" in path
                and "\\physicaldisk(_total)" not in path
                and path.endswith("\\% idle time")
                and int(candidate.get("status") or 0) == 0
            ]
            idle_values = [
                _valid_nonnegative(candidate.get("cooked_value"))
                for candidate in rows_for_signal
            ]
            idle_values = [value for value in idle_values if value is not None]
            value = (
                max(0.0, min(100.0, 100.0 - min(idle_values)))
                if idle_values else None
            )
            details = {
                "counter_path": definition.counter_path,
                "semantics": "maximum_active_time_across_physical_disks",
                "derived_from": "100_minus_minimum_physical_disk_idle_time",
                "bounded_percent": True,
                "physical_disk_count": len(idle_values),
            }
        else:
            suffix = definition.counter_path.casefold()
            row = next(
                (candidate for path, candidate in by_suffix.items() if path.endswith(suffix)),
                None,
            )
            counter_ok = row is not None and int(row.get("status") or 0) == 0
            value = row.get("cooked_value") if counter_ok and row else None
            details = {"counter_path": definition.counter_path}
        signals.append(
            _signal(
                key=definition.key,
                group=definition.group,
                label=definition.label,
                unit=definition.unit,
                value=value,
                status="available" if value is not None else "unavailable",
                reason=None if value is not None else "counter_unavailable",
                source="Windows Performance Counters",
                source_status="available" if value is not None else "unavailable",
                frequency_seconds=PERFORMANCE_COLLECTION_SECONDS,
                details=details,
            )
        )

    for key, group, label, unit, reason in EXPLICIT_UNAVAILABLE_SIGNALS:
        source = (
            "Windows process-creation auditing"
            if group == "application"
            else "Windows Performance Counters"
        )
        signals.append(
            _signal(
                key=key,
                group=group,
                label=label,
                unit=unit,
                value=None,
                status="unavailable",
                reason=reason,
                source=source,
                source_status="unsupported_metric",
                frequency_seconds=PERFORMANCE_COLLECTION_SECONDS,
            )
        )
    available = any(item["availability_status"] == "available" for item in signals)
    return signals, available, None if available else "no_supported_counters"


def _hardware_unavailable(
    status: str,
    reason: str,
) -> list[dict[str, Any]]:
    return [
        _signal(
            key=key,
            group=group,
            label=label,
            unit=unit,
            value=None,
            status=status,
            reason=reason,
            source=(
                "Windows Storage PowerShell"
                if group == "storage"
                else "Windows Battery WMI"
            ),
            source_status=status,
            frequency_seconds=HARDWARE_COLLECTION_SECONDS,
        )
        for key, group, label, unit in HARDWARE_SIGNAL_DETAILS
    ]


def _valid_nonnegative(value: Any) -> float | None:
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return None
    return converted if math.isfinite(converted) and converted >= 0 else None


def collect_hardware_signals(
    runner: PowerShellRunner = _run_powershell_json,
) -> tuple[list[dict[str, Any]], bool, str | None]:
    """Collect slower storage-reliability and battery-capacity evidence."""
    if sys.platform != "win32":
        return (
            _hardware_unavailable("unsupported", "unsupported_platform"),
            False,
            "unsupported_platform",
        )
    script = r"""
$ErrorActionPreference = 'Stop'
$storageReason = $null
$storageStatus = 'unsupported'
$reliability = @()
try {
    $physicalDisks = @(Get-PhysicalDisk -ErrorAction Stop)
    foreach ($disk in $physicalDisks) {
        try {
            $counter = $disk | Get-StorageReliabilityCounter -ErrorAction Stop
            if ($null -ne $counter) {
                $reliability += [pscustomobject]@{
                    temperature = $counter.Temperature
                    wear = $counter.Wear
                    read_errors_total = $counter.ReadErrorsTotal
                    read_errors_uncorrected = $counter.ReadErrorsUncorrected
                    write_errors_total = $counter.WriteErrorsTotal
                    write_errors_uncorrected = $counter.WriteErrorsUncorrected
                }
            }
        }
        catch {
            if ($_.Exception -is [System.UnauthorizedAccessException] -or
                $_.FullyQualifiedErrorId -match 'AccessDenied|Unauthorized') {
                $storageReason = 'storage_reliability_permission_limited'
                $storageStatus = 'permission_limited'
            }
            else {
                $storageReason = 'storage_reliability_not_exposed_by_hardware'
                $storageStatus = 'unsupported'
            }
        }
    }
    if ($reliability.Count -eq 0 -and $null -eq $storageReason) {
        $storageReason = 'storage_reliability_not_exposed_by_hardware'
        $storageStatus = 'unsupported'
    }
}
catch {
    if ($_.Exception -is [System.UnauthorizedAccessException] -or
        $_.FullyQualifiedErrorId -match 'AccessDenied|Unauthorized') {
        $storageReason = 'physical_disk_query_permission_limited'
        $storageStatus = 'permission_limited'
    }
    else {
        $storageReason = 'physical_disk_query_failed'
        $storageStatus = 'collector_failure'
    }
}

$batteryPresent = $false
$fullCapacity = @()
$designCapacity = @()
$batteryStatus = @()
try {
    $winBattery = @(Get-CimInstance -ClassName Win32_Battery -OperationTimeoutSec 3 -ErrorAction Stop)
    $batteryPresent = $winBattery.Count -gt 0
}
catch {}
if ($batteryPresent) {
    try {
        $fullCapacity = @(
            Get-CimInstance -Namespace root\WMI -ClassName BatteryFullChargedCapacity -OperationTimeoutSec 3 -ErrorAction Stop |
            ForEach-Object { $_.FullChargedCapacity }
        )
    }
    catch {}
    try {
        $designCapacity = @(
            Get-CimInstance -Namespace root\WMI -ClassName BatteryStaticData -OperationTimeoutSec 3 -ErrorAction Stop |
            ForEach-Object { $_.DesignedCapacity }
        )
    }
    catch {}
    try {
        $batteryStatus = @(
            Get-CimInstance -Namespace root\WMI -ClassName BatteryStatus -OperationTimeoutSec 3 -ErrorAction Stop |
            ForEach-Object {
                [pscustomobject]@{
                    discharge_rate = $_.DischargeRate
                    remaining_capacity = $_.RemainingCapacity
                    power_online = $_.PowerOnline
                    discharging = $_.Discharging
                }
            }
        )
    }
    catch {}
}

[pscustomobject]@{
    storage_reason = $storageReason
    storage_status = $storageStatus
    reliability = $reliability
    battery_present = $batteryPresent
    full_capacity = $fullCapacity
    design_capacity = $designCapacity
    battery_status = $batteryStatus
} | ConvertTo-Json -Depth 6 -Compress
"""
    try:
        raw = _run_with_bounded_timeout_retry(
            runner, script, HARDWARE_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired:
        return (
            _hardware_unavailable("timeout", "query_timeout"),
            False,
            "query_timeout",
        )
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, json.JSONDecodeError):
        return (
            _hardware_unavailable(
                "permission_limited",
                "hardware_query_failed_or_permission_limited",
            ),
            False,
            "hardware_query_failed_or_permission_limited",
        )
    if not isinstance(raw, dict):
        return (
            _hardware_unavailable("unavailable", "empty_hardware_query_result"),
            False,
            "empty_hardware_query_result",
        )

    reliability = raw.get("reliability") or []
    if isinstance(reliability, dict):
        reliability = [reliability]
    storage_reason = str(
        raw.get("storage_reason") or "storage_reliability_not_exposed_by_hardware"
    )
    storage_status = str(raw.get("storage_status") or "unsupported")

    def values(name: str) -> list[float]:
        result = [
            value
            for row in reliability
            if isinstance(row, dict)
            for value in [_valid_nonnegative(row.get(name))]
            if value is not None
        ]
        return result

    temperatures = values("temperature")
    wear = values("wear")
    read_total = values("read_errors_total")
    write_total = values("write_errors_total")
    uncorrected = [
        *values("read_errors_uncorrected"),
        *values("write_errors_uncorrected"),
    ]
    storage_values: dict[str, float | None] = {
        "storage_temperature_celsius": max(temperatures) if temperatures else None,
        "storage_wear_percent": max(wear) if wear else None,
        "storage_read_errors_total": sum(read_total) if read_total else None,
        "storage_write_errors_total": sum(write_total) if write_total else None,
        "storage_uncorrected_errors_total": (
            sum(uncorrected) if uncorrected else None
        ),
    }

    battery_present = bool(raw.get("battery_present"))
    full_values = raw.get("full_capacity") or []
    design_values = raw.get("design_capacity") or []
    status_rows = raw.get("battery_status") or []
    if not isinstance(full_values, list):
        full_values = [full_values]
    if not isinstance(design_values, list):
        design_values = [design_values]
    if isinstance(status_rows, dict):
        status_rows = [status_rows]
    full = next(
        (value for raw_value in full_values if (value := _valid_nonnegative(raw_value))),
        None,
    )
    design = next(
        (
            value
            for raw_value in design_values
            if (value := _valid_nonnegative(raw_value))
        ),
        None,
    )
    discharge_values = [
        value
        for row in status_rows
        if isinstance(row, dict)
        and bool(row.get("discharging"))
        for value in [_valid_nonnegative(row.get("discharge_rate"))]
        if value is not None and value < 2_147_483_647
    ]
    remaining_values = [
        value
        for row in status_rows
        if isinstance(row, dict)
        for value in [_valid_nonnegative(row.get("remaining_capacity"))]
        if value is not None and value < 2_147_483_647
    ]
    battery_values: dict[str, float | None] = {
        "battery_full_charge_capacity_mwh": full,
        "battery_design_capacity_mwh": design,
        "battery_health_percent": (
            max(0.0, min(100.0, full / design * 100.0))
            if full is not None and design not in (None, 0)
            else None
        ),
        "battery_discharge_rate_mw": (
            sum(discharge_values) if discharge_values else None
        ),
        "battery_remaining_capacity_mwh": (
            sum(remaining_values) if remaining_values else None
        ),
    }

    definitions = {
        key: (group, label, unit)
        for key, group, label, unit in HARDWARE_SIGNAL_DETAILS
    }
    signals: list[dict[str, Any]] = []
    for key, value in {**storage_values, **battery_values}.items():
        group, label, unit = definitions[key]
        if group == "storage":
            reason = None if value is not None else storage_reason
            status = "available" if value is not None else storage_status
            source = "Windows Storage PowerShell"
        elif not battery_present:
            reason = "battery_not_present"
            status = "not_applicable"
            source = "Windows Battery WMI"
        else:
            reason = None if value is not None else "battery_metric_not_exposed"
            status = "available" if value is not None else "unavailable"
            source = "Windows Battery WMI"
        signals.append(
            _signal(
                key=key,
                group=group,
                label=label,
                unit=unit,
                value=value,
                status=status,
                reason=reason,
                source=source,
                source_status=status,
                frequency_seconds=HARDWARE_COLLECTION_SECONDS,
            )
        )
    available = any(item["availability_status"] == "available" for item in signals)
    return signals, True, None if available else "no_optional_hardware_metrics"


def collect_sustained_core_signal(
    connection: Any,
    device_id: str,
) -> dict[str, Any]:
    rows = connection.execute(
        """
        SELECT cpu_per_core_json FROM metrics
        WHERE device_id = ? AND cpu_per_core_json IS NOT NULL
        ORDER BY timestamp_utc DESC, id DESC LIMIT 5
        """,
        (device_id,),
    ).fetchall()
    samples: list[list[float]] = []
    for row in rows:
        try:
            values = json.loads(row[0])
            if isinstance(values, list) and values:
                samples.append([float(value) for value in values])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    value: int | None = None
    reason: str | None = "insufficient_recent_core_samples"
    if len(samples) >= 4:
        core_count = min(len(sample) for sample in samples)
        sustained = 0
        for core_index in range(core_count):
            saturated = sum(
                sample[core_index] >= 95.0 for sample in samples
            )
            if saturated >= max(4, math.ceil(len(samples) * 0.8)):
                sustained += 1
        value = sustained
        reason = None
    return _signal(
        key="cpu_sustained_saturated_core_count",
        group="cpu",
        label="Sustained saturated cores",
        unit="cores",
        value=value,
        status="available" if value is not None else "unavailable",
        reason=reason,
        source="SmartOps recent per-core telemetry",
        source_status="available" if value is not None else "collecting_data",
        frequency_seconds=PERFORMANCE_COLLECTION_SECONDS,
        details={"sample_count": len(samples), "threshold_percent": 95.0},
    )


def classify_structured_event(row: Mapping[str, Any]) -> dict[str, Any]:
    """Classify stored metadata without reading or storing event messages."""
    provider = str(row["provider_name"]).casefold()
    event_id = int(row["event_id"])
    level = str(row["event_level"]).casefold()
    evidence_type = "operational_event"
    subtype = str(row["smartops_category"])
    group = "stability"
    reason = "structured_operational_event"
    summary = str(row["safe_summary"])

    if "whea" in provider:
        group = "hardware"
        evidence_type = "hardware_reliability"
        if event_id in {17, 19, 20, 47}:
            subtype = "whea_corrected"
            reason = "whea_corrected_hardware_event"
        elif event_id in {1, 18, 46} or level == "critical":
            subtype = "whea_fatal"
            reason = "whea_fatal_hardware_event"
        else:
            subtype = "whea_hardware_event"
            reason = "whea_hardware_event"
    elif (
        "kernel-power" in provider
        or ("eventlog" in provider and event_id == 6008)
    ):
        group = "power"
        evidence_type = "unexpected_power"
        subtype = "unexpected_shutdown_or_power_loss"
        reason = "kernel_power_unexpected_shutdown"
    elif "bugcheck" in provider:
        group = "stability"
        evidence_type = "bugcheck"
        subtype = "windows_bugcheck"
        reason = "windows_bugcheck_record"
    elif "resource-exhaustion" in provider:
        group = "memory"
        evidence_type = "resource_exhaustion"
        subtype = "windows_resource_exhaustion"
        reason = "windows_resource_exhaustion_event"
    elif any(marker in provider for marker in ("disk", "stor", "ntfs", "volmgr")):
        group = "storage"
        evidence_type = "storage_reliability"
        subtype = (
            "filesystem_warning"
            if any(marker in provider for marker in ("ntfs", "refs", "fat"))
            else "storage_driver_warning"
        )
        reason = "storage_or_filesystem_event"
    elif "application hang" in provider:
        group = "application"
        evidence_type = "application_stability"
        subtype = "application_hang"
        reason = "application_hang_event"
    elif "application error" in provider:
        group = "application"
        evidence_type = "application_stability"
        subtype = "application_crash"
        reason = "application_crash_event"
    elif "windows error reporting" in provider and event_id == 1001:
        group = "application"
        evidence_type = "application_stability"
        subtype = "abnormal_termination_report"
        reason = "windows_error_reporting_event"
    elif "service control manager" in provider:
        group = "service"
        evidence_type = "service_stability"
        if event_id == 7032:
            subtype = "service_recovery_attempt"
            reason = "service_recovery_attempt_event"
        elif event_id in {7031, 7034}:
            subtype = "unexpected_service_termination"
            reason = "unexpected_service_termination_event"
        else:
            subtype = "service_start_or_termination_failure"
            reason = "service_failure_event"

    return {
        "windows_event_id": int(row["id"]),
        "device_id": str(row["device_id"]),
        "event_timestamp_utc": str(row["event_timestamp_utc"]),
        "evidence_group": group,
        "evidence_type": evidence_type,
        "evidence_subtype": subtype,
        "evidence_level": level,
        "source_name": "Structured Windows Event Log metadata",
        "source_record_key": f"windows-event:{row['channel']}:{row['record_id']}",
        "reason_code": reason,
        "safe_summary": summary,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "details": {
            "channel": row["channel"],
            "provider_name": row["provider_name"],
            "event_id": event_id,
        },
    }


def collect_power_transition(connection: Any) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT id, device_id, timestamp_utc, ac_power_connected
        FROM metrics WHERE ac_power_connected IS NOT NULL
        ORDER BY timestamp_utc DESC, id DESC LIMIT 2
        """
    ).fetchall()
    if len(rows) < 2 or bool(rows[0]["ac_power_connected"]) == bool(
        rows[1]["ac_power_connected"]
    ):
        return []
    connected = bool(rows[0]["ac_power_connected"])
    return [
        {
            "windows_event_id": None,
            "device_id": rows[0]["device_id"],
            "event_timestamp_utc": rows[0]["timestamp_utc"],
            "evidence_group": "power",
            "evidence_type": "power_transition",
            "evidence_subtype": "ac_connected" if connected else "ac_disconnected",
            "evidence_level": "informational",
            "source_name": "SmartOps AC-power telemetry",
            "source_record_key": f"metric-power-transition:{rows[0]['id']}",
            "reason_code": (
                "ac_power_connected_transition"
                if connected
                else "battery_power_transition"
            ),
            "safe_summary": (
                "SmartOps observed a transition to AC power."
                if connected
                else "SmartOps observed a transition to battery power."
            ),
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "details": {"metric_id": rows[0]["id"]},
        }
    ]


def classify_schedule_gap(
    delay_seconds: float,
    frequency_seconds: int,
    previous_session_id: str | None,
    current_session_id: str | None,
) -> str:
    """Separate in-session misses from normal restarts and offline gaps."""
    if delay_seconds < frequency_seconds / 2:
        return "on_schedule"
    if previous_session_id and previous_session_id == current_session_id:
        return "missed_while_running"
    return "offline_or_shutdown_gap"


def collect_enhanced_evidence(
    database_path: Path,
    sample: Mapping[str, Any],
    *,
    force: bool = False,
    runner: PowerShellRunner = _run_powershell_json,
    cycle_started_monotonic: float | None = None,
    process_cpu_started: float | None = None,
    scheduled_at_utc: datetime | None = None,
    agent_session_id: str | None = None,
) -> EnhancedRunContext:
    """Collect due evidence without invoking any existing analytical evaluator."""
    from backend.database import database_connection, initialize_database

    path = initialize_database(database_path)
    now = datetime.now(timezone.utc)
    device_id = str(sample["device_id"])
    started_monotonic = cycle_started_monotonic or time.monotonic()
    cpu_started = process_cpu_started if process_cpu_started is not None else time.process_time()
    rss_before = _safe_rss()
    database_before = _safe_file_size(path)
    schedules: dict[str, datetime] = {}
    gap_states: dict[str, str] = {}
    with database_connection(path) as connection:
        due = ["event_enrichment", "power_transition"]
        if force or collector_is_due(connection, "performance_counters", now):
            due.append("performance_counters")
        if force or collector_is_due(connection, "hardware_capabilities", now):
            due.append("hardware_capabilities")
        if force or collector_is_due(connection, "enhanced_retention", now):
            due.append("enhanced_retention")
        for collector_key in due:
            scheduled = collector_schedule(connection, collector_key, now)
            schedules[collector_key] = scheduled
            previous = connection.execute(
                """SELECT last_agent_session_id FROM enhanced_collector_state
                WHERE collector_key = ?""",
                (collector_key,),
            ).fetchone()
            delay = max(0.0, (now - scheduled).total_seconds())
            frequency = {
                "performance_counters": PERFORMANCE_COLLECTION_SECONDS,
                "hardware_capabilities": HARDWARE_COLLECTION_SECONDS,
                "enhanced_retention": RETENTION_MAINTENANCE_SECONDS,
                "event_enrichment": 30,
                "power_transition": 30,
            }[collector_key]
            gap_states[collector_key] = classify_schedule_gap(
                delay,
                frequency,
                previous[0] if previous else None,
                agent_session_id,
            )
        run_scheduled = scheduled_at_utc or now
        run_id = create_collection_run(
            connection,
            device_id=device_id,
            started_at_utc=now.isoformat(),
            due_collectors=due,
            process_rss_before_bytes=rss_before,
            database_bytes_before=database_before,
            algorithm_version=ALGORITHM_VERSION,
            configuration_version=CONFIGURATION_VERSION,
            scheduled_at_utc=run_scheduled.isoformat(),
            schedule_delay_seconds=max(0.0, (now - run_scheduled).total_seconds()),
            agent_session_id=agent_session_id,
        )

    context = EnhancedRunContext(
        run_id=run_id,
        started_monotonic=started_monotonic,
        process_cpu_started=cpu_started,
        process_rss_before_bytes=rss_before,
        database_bytes_before=database_before,
        scheduled_at_utc=run_scheduled.isoformat(),
        schedule_delay_seconds=max(0.0, (now - run_scheduled).total_seconds()),
        agent_session_id=agent_session_id,
    )
    enhanced_started = time.monotonic()
    # External counters describe the collection instant. This differs from
    # referenced core telemetry when the maintenance CLI is run between raw
    # agent cycles, so store the actual UTC attempt time here.
    timestamp = now.isoformat()

    if "performance_counters" in due:
        collector_started = time.monotonic()
        signals, query_succeeded, reason = collect_performance_signals(runner)
        with database_connection(path) as connection:
            signals.append(collect_sustained_core_signal(connection, device_id))
            store_signal_samples(connection, run_id, device_id, timestamp, signals)
            update_collector_state(
                connection,
                collector_key="performance_counters",
                attempted_at_utc=now,
                frequency_seconds=PERFORMANCE_COLLECTION_SECONDS,
                availability_status=(
                    "available"
                    if any(
                        item["availability_status"] == "available"
                        for item in signals
                    )
                    else "unavailable"
                ),
                reason_code=reason,
                source_name="Windows Performance Counters",
                successful=query_succeeded,
                details={"signal_count": len(signals), "shadow_mode": True},
                scheduled_at_utc=schedules["performance_counters"],
                collection_duration_ms=(time.monotonic() - collector_started) * 1000.0,
                gap_classification=gap_states["performance_counters"],
                agent_session_id=agent_session_id,
            )
        (
            context.successful_collectors
            if query_succeeded
            else context.failed_collectors
        ).append("performance_counters")

    if "hardware_capabilities" in due:
        collector_started = time.monotonic()
        signals, query_succeeded, reason = collect_hardware_signals(runner)
        with database_connection(path) as connection:
            store_signal_samples(connection, run_id, device_id, timestamp, signals)
            update_collector_state(
                connection,
                collector_key="hardware_capabilities",
                attempted_at_utc=now,
                frequency_seconds=HARDWARE_COLLECTION_SECONDS,
                availability_status=(
                    "available"
                    if any(
                        item["availability_status"] == "available"
                        for item in signals
                    )
                    else "unavailable"
                ),
                reason_code=reason,
                source_name="Windows Storage PowerShell and Battery WMI",
                successful=query_succeeded,
                details={"signal_count": len(signals), "shadow_mode": True},
                scheduled_at_utc=schedules["hardware_capabilities"],
                collection_duration_ms=(time.monotonic() - collector_started) * 1000.0,
                gap_classification=gap_states["hardware_capabilities"],
                agent_session_id=agent_session_id,
            )
        (
            context.successful_collectors
            if query_succeeded
            else context.failed_collectors
        ).append("hardware_capabilities")

    with database_connection(path) as connection:
        rows = unmirrored_windows_events(connection)
        events = [classify_structured_event(row) for row in rows]
        inserted = store_enhanced_events(connection, events)
        update_collector_state(
            connection,
            collector_key="event_enrichment",
            attempted_at_utc=now,
            frequency_seconds=30,
            availability_status="available",
            reason_code=None,
            source_name="Existing deduplicated Windows Event Log records",
            successful=True,
        details={"new_rows": inserted, "shadow_mode": True},
            scheduled_at_utc=schedules["event_enrichment"],
            gap_classification=gap_states["event_enrichment"],
            agent_session_id=agent_session_id,
        )
    context.successful_collectors.append("event_enrichment")

    with database_connection(path) as connection:
        transitions = collect_power_transition(connection)
        inserted = store_enhanced_events(connection, transitions)
        update_collector_state(
            connection,
            collector_key="power_transition",
            attempted_at_utc=now,
            frequency_seconds=30,
            availability_status=(
                "available"
                if sample.get("ac_power_connected") is not None
                else "unavailable"
            ),
            reason_code=(
                None
                if sample.get("ac_power_connected") is not None
                else "ac_power_state_unavailable"
            ),
            source_name="Existing SmartOps AC-power telemetry",
            successful=True,
            details={"new_rows": inserted, "shadow_mode": True},
            scheduled_at_utc=schedules["power_transition"],
            gap_classification=gap_states["power_transition"],
            agent_session_id=agent_session_id,
        )
    context.successful_collectors.append("power_transition")

    if "enhanced_retention" in due:
        with database_connection(path) as connection:
            run_retention(connection, now)
            update_collector_state(
                connection,
                collector_key="enhanced_retention",
                attempted_at_utc=now,
                frequency_seconds=RETENTION_MAINTENANCE_SECONDS,
                availability_status="available",
                reason_code=None,
                source_name="SmartOps enhanced-data retention",
                successful=True,
                details={"core_tables_affected": False},
                scheduled_at_utc=schedules["enhanced_retention"],
                gap_classification=gap_states["enhanced_retention"],
                agent_session_id=agent_session_id,
            )
        context.successful_collectors.append("enhanced_retention")

    context.collection_duration_ms = (time.monotonic() - enhanced_started) * 1000.0
    return context


def finish_enhanced_collection(
    database_path: Path,
    context: EnhancedRunContext,
    *,
    error_code: str | None = None,
) -> None:
    from backend.database import database_connection

    finished = datetime.now(timezone.utc)
    full_duration_ms = (time.monotonic() - context.started_monotonic) * 1000.0
    process_cpu_ms = (time.process_time() - context.process_cpu_started) * 1000.0
    logical_cpu_count = psutil.cpu_count(logical=True) or 1
    approximate_cpu = (
        process_cpu_ms / full_duration_ms * 100.0 / logical_cpu_count
        if full_duration_ms > 0
        else None
    )
    status = (
        "error"
        if error_code
        else "partial"
        if context.failed_collectors
        else "success"
    )
    with database_connection(database_path) as connection:
        finish_collection_run(
            connection,
            context.run_id,
            finished_at_utc=finished.isoformat(),
            status=status,
            successful_collectors=context.successful_collectors,
            failed_collectors=context.failed_collectors,
            collection_duration_ms=context.collection_duration_ms,
            full_cycle_duration_ms=full_duration_ms,
            process_cpu_time_ms=process_cpu_ms,
            approximate_process_cpu_percent=approximate_cpu,
            process_rss_after_bytes=_safe_rss(),
            database_bytes_after=_safe_file_size(database_path),
            error_code=error_code,
        )


def _main() -> None:
    from backend.database import database_connection, initialize_database
    from backend.repository import get_latest_metric

    parser = argparse.ArgumentParser(
        description="SmartOps enhanced shadow-evidence maintenance"
    )
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--collect", action="store_true")
    parser.add_argument("--retention", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    path = initialize_database(get_database_path())
    if args.collect:
        with database_connection(path) as connection:
            sample = get_latest_metric(connection)
        if sample is None:
            raise SystemExit("Collect one raw SmartOps sample first.")
        context = collect_enhanced_evidence(path, sample, force=args.force)
        finish_enhanced_collection(path, context)
    if args.retention:
        with database_connection(path) as connection:
            print(json.dumps(run_retention(connection), indent=2))
    if args.status or not (args.collect or args.retention):
        with database_connection(path) as connection:
            print(json.dumps(get_enhanced_status(connection), indent=2))


if __name__ == "__main__":
    _main()
