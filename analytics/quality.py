"""Phase 4B safe local inventory and PC workload-suitability assessment."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psutil

from agent.config import get_database_path
from agent.device import get_or_create_device_id
from analytics.quality_catalogue import (
    ALGORITHM_VERSION,
    CATALOGUE_VERSION,
    CONFIGURATION_VERSION,
    INVENTORY_PROVIDER_VERSION,
    INVENTORY_REFRESH_HOURS,
    INTERPRETATION,
    PRIORITY_ORDER,
    SUITABILITY_BANDS,
    WORKLOAD_PROFILES,
)
from backend.database import database_connection, initialize_database


INVENTORY_FIELDS = {
    "cpu_name": ("cpu", "unavailable_optional"),
    "system_architecture": ("cpu", "unavailable_required"),
    "physical_cores": ("cpu", "unavailable_required"),
    "logical_processors": ("cpu", "unavailable_optional"),
    "cpu_max_clock_mhz": ("cpu", "unavailable_optional"),
    "virtualization_capable": ("cpu", "unavailable_optional"),
    "ram_installed_bytes": ("memory", "unavailable_required"),
    "ram_usable_bytes": ("memory", "unavailable_required"),
    "memory_module_count": ("memory", "unavailable_optional"),
    "system_drive_total_bytes": ("storage", "unavailable_required"),
    "system_drive_free_bytes": ("storage", "unavailable_optional"),
    "storage_media_type": ("storage", "unavailable_optional"),
    "local_drive_count": ("storage", "unavailable_optional"),
    "gpu_name": ("graphics", "unavailable_optional"),
    "gpu_classification": ("graphics", "unavailable_optional"),
    "gpu_memory_bytes": ("graphics", "unavailable_optional"),
    "gpu_driver_version": ("graphics", "unavailable_optional"),
    "graphics_available": ("graphics", "unavailable_optional"),
    "windows_edition": ("operating_system", "unavailable_optional"),
    "windows_version": ("operating_system", "unavailable_optional"),
    "windows_build": ("operating_system", "unavailable_optional"),
    "os_architecture": ("operating_system", "unavailable_required"),
}

PRIVACY_EXCLUDED_FIELDS = (
    "hardware_serial_numbers",
    "windows_product_key",
    "username",
    "computer_owner",
    "mac_addresses",
    "ip_addresses",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _field(
    name: str,
    value: Any,
    *,
    status: str | None = None,
    source: str,
    note: str | None = None,
) -> dict[str, Any]:
    group, missing_status = INVENTORY_FIELDS[name]
    resolved_status = status or ("available" if value is not None else missing_status)
    return {
        "field_name": name,
        "component_group": group,
        "value": value,
        "availability_status": resolved_status,
        "reliability_note": note,
        "source_name": source,
    }


def _run_powershell_json() -> dict[str, Any] | None:
    """Read a strict allowlist of structured CIM fields, never private IDs."""
    executable = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if executable is None:
        return None
    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$cpu = Get-CimInstance Win32_Processor |
    Select-Object -First 1 Name, Architecture, NumberOfCores,
        NumberOfLogicalProcessors, MaxClockSpeed,
        VirtualizationFirmwareEnabled,
        SecondLevelAddressTranslationExtensions, VMMonitorModeExtensions
$computer = Get-CimInstance Win32_ComputerSystem |
    Select-Object -First 1 TotalPhysicalMemory
$os = Get-CimInstance Win32_OperatingSystem |
    Select-Object -First 1 Caption, Version, BuildNumber, OSArchitecture,
        TotalVisibleMemorySize
$memoryModuleCount = @(Get-CimInstance Win32_PhysicalMemory).Count
$gpus = @(Get-CimInstance Win32_VideoController |
    Select-Object Name, AdapterRAM, DriverVersion)
try {
    $physicalDisks = @(Get-PhysicalDisk |
        Select-Object MediaType, BusType, Size)
} catch {
    $physicalDisks = @()
}
[pscustomobject]@{
    Cpu = $cpu
    Computer = $computer
    OperatingSystem = $os
    MemoryModuleCount = $memoryModuleCount
    Gpus = $gpus
    PhysicalDisks = $physicalDisks
} | ConvertTo-Json -Depth 5 -Compress
"""
    try:
        result = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            check=True,
            timeout=12,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return json.loads(result.stdout.lstrip("\ufeff")) if result.stdout.strip() else None
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None


def _nvidia_inventory() -> dict[str, Any] | None:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [
                executable,
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=4,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        first = next(
            (line for line in result.stdout.splitlines() if line.strip()),
            None,
        )
        if first is None:
            return None
        name, memory_mib, driver = [part.strip() for part in first.split(",", 2)]
        return {
            "name": name,
            "memory_bytes": int(float(memory_mib) * 1024**2),
            "driver_version": driver,
        }
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _gpu_classification(name: str | None) -> tuple[str | None, str]:
    if not name:
        return None, "unavailable_optional"
    lowered = name.casefold()
    if any(token in lowered for token in ("nvidia", "geforce", "quadro", "tesla")):
        return "dedicated", "available"
    if any(token in lowered for token in ("intel", "uhd graphics", "iris", "radeon graphics")):
        return "integrated", "available"
    if "radeon rx" in lowered or "radeon pro" in lowered:
        return "dedicated", "available"
    return "unknown", "unreliable"


def collect_inventory(device_id: str) -> dict[str, Any]:
    """Collect capability facts only; no serials, user identity, or network IDs."""
    cim = _run_powershell_json() or {}
    cpu = cim.get("Cpu") or {}
    computer = cim.get("Computer") or {}
    operating_system = cim.get("OperatingSystem") or {}
    gpus = cim.get("Gpus") or []
    if isinstance(gpus, dict):
        gpus = [gpus]
    physical_disks = cim.get("PhysicalDisks") or []
    if isinstance(physical_disks, dict):
        physical_disks = [physical_disks]

    virtual_memory = psutil.virtual_memory()
    system_drive = os.environ.get("SystemDrive", "C:") + "\\"
    try:
        disk_usage = psutil.disk_usage(system_drive)
    except OSError:
        disk_usage = None
    try:
        drive_count = len({
            partition.device
            for partition in psutil.disk_partitions(all=False)
            if "fixed" in partition.opts.casefold()
            or partition.device[:1].isalpha()
        })
    except (OSError, psutil.Error):
        drive_count = None

    architecture_map = {9: "x86_64", 12: "arm64", 0: "x86"}
    architecture = architecture_map.get(
        cpu.get("Architecture"),
        platform.machine() or None,
    )
    physical_cores = cpu.get("NumberOfCores") or psutil.cpu_count(logical=False)
    logical_processors = (
        cpu.get("NumberOfLogicalProcessors") or psutil.cpu_count(logical=True)
    )
    installed_ram = computer.get("TotalPhysicalMemory") or virtual_memory.total
    usable_kib = operating_system.get("TotalVisibleMemorySize")
    usable_ram = int(usable_kib) * 1024 if usable_kib else virtual_memory.total

    nvidia = _nvidia_inventory()
    selected_gpu = (
        next(
            (
                gpu for gpu in gpus
                if "nvidia" in str(gpu.get("Name", "")).casefold()
            ),
            gpus[0] if gpus else {},
        )
    )
    gpu_name = nvidia["name"] if nvidia else selected_gpu.get("Name")
    gpu_class, gpu_class_status = _gpu_classification(gpu_name)
    adapter_ram = selected_gpu.get("AdapterRAM")
    if nvidia:
        gpu_memory = nvidia["memory_bytes"]
        gpu_memory_status = "available"
        gpu_memory_note = "Reported by the local NVIDIA management interface."
    elif isinstance(adapter_ram, (int, float)) and adapter_ram > 0:
        gpu_memory = int(adapter_ram)
        gpu_memory_status = "unreliable"
        gpu_memory_note = (
            "Win32_VideoController AdapterRAM can be truncated or represent "
            "shared memory; it is not treated as a reliable dedicated-memory value."
        )
    else:
        gpu_memory = None
        gpu_memory_status = "unavailable_optional"
        gpu_memory_note = "Graphics memory was not reported by a reliable local interface."

    media_types = {
        str(item.get("MediaType", "")).strip().upper()
        for item in physical_disks
        if str(item.get("MediaType", "")).strip().upper() in {"SSD", "HDD"}
    }
    if len(media_types) == 1:
        media_type = next(iter(media_types))
        media_status = "available"
        media_note = "One unambiguous local physical-media type was reported."
    else:
        media_type = "unknown"
        media_status = "unreliable"
        media_note = (
            "The system drive could not be mapped reliably to one physical-media type."
        )

    os_arch = operating_system.get("OSArchitecture")
    if not os_arch:
        os_arch = "64-bit" if "64" in str(architecture) else platform.architecture()[0]
    virtualization_values = [
        cpu.get("VirtualizationFirmwareEnabled"),
        cpu.get("SecondLevelAddressTranslationExtensions"),
        cpu.get("VMMonitorModeExtensions"),
    ]
    virtualization = (
        all(value is True for value in virtualization_values)
        if any(value is not None for value in virtualization_values)
        else None
    )
    fields = [
        _field("cpu_name", cpu.get("Name") or platform.processor() or None, source="CIM/platform"),
        _field("system_architecture", architecture, source="CIM/platform"),
        _field("physical_cores", physical_cores, source="CIM/psutil"),
        _field("logical_processors", logical_processors, source="CIM/psutil"),
        _field("cpu_max_clock_mhz", cpu.get("MaxClockSpeed"), source="CIM"),
        _field("virtualization_capable", virtualization, source="CIM"),
        _field("ram_installed_bytes", int(installed_ram) if installed_ram else None, source="CIM/psutil"),
        _field("ram_usable_bytes", int(usable_ram) if usable_ram else None, source="CIM/psutil"),
        _field("memory_module_count", cim.get("MemoryModuleCount"), source="CIM"),
        _field("system_drive_total_bytes", int(disk_usage.total) if disk_usage else None, source="psutil"),
        _field("system_drive_free_bytes", int(disk_usage.free) if disk_usage else None, source="psutil"),
        _field(
            "storage_media_type",
            media_type,
            status=media_status,
            source="Get-PhysicalDisk",
            note=media_note,
        ),
        _field("local_drive_count", drive_count, source="psutil"),
        _field("gpu_name", gpu_name, source="nvidia-smi/CIM"),
        _field(
            "gpu_classification",
            gpu_class,
            status=gpu_class_status,
            source="safe vendor-family classification",
            note=(
                None if gpu_class_status == "available"
                else "The graphics class could not be classified reliably."
            ),
        ),
        _field(
            "gpu_memory_bytes",
            gpu_memory,
            status=gpu_memory_status,
            source="nvidia-smi/CIM",
            note=gpu_memory_note,
        ),
        _field(
            "gpu_driver_version",
            nvidia["driver_version"] if nvidia else selected_gpu.get("DriverVersion"),
            source="nvidia-smi/CIM",
        ),
        _field("graphics_available", bool(gpu_name) if gpus or nvidia else None, source="CIM"),
        _field("windows_edition", operating_system.get("Caption") or platform.system(), source="CIM/platform"),
        _field("windows_version", operating_system.get("Version") or platform.version(), source="CIM/platform"),
        _field("windows_build", operating_system.get("BuildNumber"), source="CIM"),
        _field("os_architecture", os_arch, source="CIM/platform"),
    ]
    required = [
        field for field in fields
        if INVENTORY_FIELDS[field["field_name"]][1] == "unavailable_required"
    ]
    unavailable_required = [
        field["field_name"] for field in required
        if field["availability_status"] != "available"
    ]
    confidence_values = {
        "available": 1.0,
        "unreliable": 0.5,
        "unavailable_optional": 0.7,
        "not_applicable": 1.0,
        "excluded_for_privacy": 1.0,
        "unavailable_required": 0.0,
    }
    confidence = round(
        100 * sum(confidence_values[field["availability_status"]] for field in fields)
        / len(fields),
        2,
    )
    signature_payload = [
        {
            "field_name": field["field_name"],
            "value": field["value"],
            "availability_status": field["availability_status"],
        }
        for field in fields
        # Free space is current installation-readiness context, not a hardware
        # configuration change. Including it would create a new snapshot as
        # ordinary files are added or removed.
        if field["field_name"] != "system_drive_free_bytes"
    ]
    signature = hashlib.sha256(
        json.dumps(signature_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "device_id": device_id,
        "captured_at_utc": _utc_now().isoformat(),
        "inventory_signature": signature,
        "provider_version": INVENTORY_PROVIDER_VERSION,
        "detection_confidence": confidence,
        "inventory_state": "available" if not unavailable_required else "inadequate",
        "reason_codes": [
            f"{name}_unavailable" for name in unavailable_required
        ],
        "fields": fields,
        "privacy_excluded_fields": list(PRIVACY_EXCLUDED_FIELDS),
    }


def store_inventory(
    connection,
    inventory: dict[str, Any],
) -> tuple[int, bool]:
    existing = connection.execute(
        """SELECT id FROM device_inventory_snapshots
        WHERE device_id = ? AND inventory_signature = ?""",
        (inventory["device_id"], inventory["inventory_signature"]),
    ).fetchone()
    if existing:
        with connection:
            connection.execute(
                """UPDATE device_inventory_snapshots
                SET last_checked_at_utc = ?
                WHERE id = ?""",
                (inventory["captured_at_utc"], int(existing[0])),
            )
        return int(existing[0]), True
    with connection:
        cursor = connection.execute(
            """INSERT INTO device_inventory_snapshots (
                device_id, captured_at_utc, last_checked_at_utc,
                inventory_signature,
                provider_version, detection_confidence, inventory_state,
                reason_codes_json, created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                inventory["device_id"],
                inventory["captured_at_utc"],
                inventory["captured_at_utc"],
                inventory["inventory_signature"],
                inventory["provider_version"],
                inventory["detection_confidence"],
                inventory["inventory_state"],
                json.dumps(inventory["reason_codes"]),
                inventory["captured_at_utc"],
            ),
        )
        snapshot_id = int(cursor.lastrowid)
        connection.executemany(
            """INSERT INTO inventory_component_values (
                inventory_snapshot_id, component_group, field_name,
                value_json, availability_status, reliability_note, source_name
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    snapshot_id,
                    field["component_group"],
                    field["field_name"],
                    json.dumps(field["value"]),
                    field["availability_status"],
                    field["reliability_note"],
                    field["source_name"],
                )
                for field in inventory["fields"]
            ],
        )
    return snapshot_id, False


def refresh_inventory(
    database_path: Path | None = None,
    device_id: str | None = None,
) -> dict[str, Any]:
    path = initialize_database(database_path or get_database_path())
    resolved = device_id or get_or_create_device_id()
    inventory = collect_inventory(resolved)
    with database_connection(path) as connection:
        snapshot_id, reused = store_inventory(connection, inventory)
    return {
        "snapshot_id": snapshot_id,
        "reused": reused,
        **inventory,
    }


def _load_inventory(connection, snapshot_id: int) -> dict[str, Any]:
    snapshot = dict(connection.execute(
        "SELECT * FROM device_inventory_snapshots WHERE id = ?",
        (snapshot_id,),
    ).fetchone())
    fields = {}
    for row in connection.execute(
        """SELECT * FROM inventory_component_values
        WHERE inventory_snapshot_id = ? ORDER BY id""",
        (snapshot_id,),
    ):
        item = dict(row)
        item["value"] = json.loads(item.pop("value_json"))
        fields[item["field_name"]] = item
    snapshot["fields"] = fields
    snapshot["reason_codes"] = json.loads(snapshot.pop("reason_codes_json"))
    return snapshot


def _numeric_score(value: float, minimum: float, recommended: float) -> float:
    if value >= recommended:
        return 100.0
    if value >= minimum:
        span = max(recommended - minimum, 1.0)
        return 60.0 + 40.0 * (value - minimum) / span
    return max(0.0, 60.0 * value / max(minimum, 1.0))


def suitability_result_for_score(score: float) -> str:
    for threshold, result in SUITABILITY_BANDS:
        if score >= threshold:
            return result
    return "insufficient"


def _component_result(
    name: str,
    specification: dict[str, Any],
    inventory: dict[str, Any],
) -> dict[str, Any]:
    fields = inventory["fields"]
    metric_name = str(specification["metric"])
    field = fields.get(metric_name)
    status = field["availability_status"] if field else "unavailable_required"
    value = field["value"] if field else None
    required = bool(specification["required"])
    minimum = specification["minimum"]
    recommended = specification["recommended"]
    reason_codes: list[str] = []
    limitations: list[str] = []
    score: float | None = None
    minimum_passed: bool | None = None
    recommended_passed: bool | None = None
    hard_gate_status = "not_applicable"
    detected: Any = value

    if name in {"cpu_capability", "memory_capability", "storage_capability"}:
        if value is not None and status in {"available", "unreliable"}:
            numeric = float(value)
            score = _numeric_score(numeric, float(minimum), float(recommended))
            minimum_passed = numeric >= float(minimum)
            recommended_passed = numeric >= float(recommended)
            reason_codes.append(
                f"{name}_{'recommended_met' if recommended_passed else 'minimum_met' if minimum_passed else 'below_minimum'}"
            )
        else:
            reason_codes.append(f"{metric_name}_unavailable")
    elif name == "operating_system_capability":
        if value is not None:
            is_64 = "64" in str(value).casefold()
            score = 100.0 if is_64 else 20.0
            minimum_passed = recommended_passed = is_64
            hard_gate_status = "passed" if is_64 else "failed"
            reason_codes.append("os_64_bit_available" if is_64 else "os_64_bit_required")
        else:
            hard_gate_status = "unknown"
            reason_codes.append("os_architecture_unavailable")
    elif name == "graphics_capability":
        class_field = fields.get("gpu_classification")
        memory_field = fields.get("gpu_memory_bytes")
        gpu_field = fields.get("gpu_name")
        classification = class_field["value"] if class_field else None
        class_status = (
            class_field["availability_status"]
            if class_field else "unavailable_optional"
        )
        dedicated_required = bool(specification.get("dedicated_required"))
        memory_minimum = specification.get("gpu_memory_minimum")
        memory_recommended = specification.get("gpu_memory_recommended")
        detected = {
            "name": gpu_field["value"] if gpu_field else None,
            "classification": classification,
            "memory_bytes": memory_field["value"] if memory_field else None,
        }
        status = class_status
        if dedicated_required:
            if classification in {"integrated", "dedicated"}:
                dedicated = classification == "dedicated"
                hard_gate_status = "passed" if dedicated else "failed"
                minimum_passed = dedicated
                recommended_passed = dedicated
                score = 60.0 if dedicated else 15.0
                if dedicated and memory_minimum is not None:
                    memory_value = memory_field["value"] if memory_field else None
                    if memory_value is None:
                        status = "unavailable_required"
                        score = None
                        minimum_passed = recommended_passed = None
                        hard_gate_status = "unknown"
                        reason_codes.append("gpu_memory_unavailable_required")
                    else:
                        memory_score = _numeric_score(
                            float(memory_value),
                            float(memory_minimum),
                            float(memory_recommended),
                        )
                        score = (score + memory_score) / 2
                        minimum_passed = float(memory_value) >= float(memory_minimum)
                        recommended_passed = (
                            float(memory_value) >= float(memory_recommended)
                        )
                        hard_gate_status = (
                            "passed" if minimum_passed else "failed"
                        )
                        if memory_field["availability_status"] == "unreliable":
                            status = "unreliable"
                            limitations.append(
                                "GPU memory is reported by an interface that may be unreliable."
                            )
                reason_codes.append(
                    "dedicated_graphics_requirement_met"
                    if hard_gate_status == "passed"
                    else "dedicated_graphics_requirement_not_met"
                    if hard_gate_status == "failed"
                    else "dedicated_graphics_requirement_unknown"
                )
            else:
                status = (
                    "unreliable" if classification == "unknown"
                    else "unavailable_required"
                )
                hard_gate_status = "unknown"
                reason_codes.append("dedicated_graphics_classification_unavailable")
        else:
            if classification in {"integrated", "dedicated"}:
                score = 100.0 if classification == "dedicated" else 90.0
                minimum_passed = True
                recommended_passed = classification == "dedicated" or not specification.get("gpu_memory_minimum")
                reason_codes.append("graphics_capability_available")
            else:
                score = None
                status = "unavailable_optional" if classification is None else "unreliable"
                reason_codes.append("graphics_capability_excluded")
    elif name == "optional_acceleration_capability":
        if value is None:
            score = None
            status = "unavailable_optional"
            reason_codes.append("virtualization_capability_unavailable")
        else:
            score = 100.0 if bool(value) else 60.0
            minimum_passed = True
            recommended_passed = bool(value)
            reason_codes.append(
                "virtualization_available" if value else "virtualization_not_enabled"
            )

    if status == "unreliable":
        limitations.append(f"{metric_name.replace('_', ' ')} was reported as unreliable.")
    passed_text = (
        "met the recommended level"
        if recommended_passed
        else "met the minimum but not the recommended level"
        if minimum_passed
        else "did not meet the minimum level"
        if minimum_passed is False
        else "could not be evaluated safely"
    )
    action = (
        None
        if recommended_passed
        else f"Verify or improve {name.replace('_capability', '').replace('_', ' ')} capability toward the recommended profile level."
    )
    return {
        "component_name": name,
        "detected_value": detected,
        "detection_status": status,
        "minimum_threshold": minimum,
        "recommended_threshold": recommended,
        "raw_component_score": round(score, 4) if score is not None else None,
        "effective_component_score": round(score, 4) if score is not None else None,
        "configured_weight": float(specification["weight"]),
        "effective_weight": 0.0,
        "minimum_passed": minimum_passed,
        "recommended_passed": recommended_passed,
        "hard_gate_status": hard_gate_status,
        "reason_codes": reason_codes,
        "explanation": f"{name.replace('_', ' ').capitalize()} {passed_text}.",
        "limitations": limitations,
        "suggested_action": action,
        "required": required,
    }


def _current_readiness(connection, device_id: str) -> dict[str, Any]:
    health = connection.execute(
        """SELECT id, feature_window_id, system_health_score, health_band,
        evaluation_state, data_confidence, assessed_at_utc
        FROM health_assessments WHERE device_id = ?
        ORDER BY window_start_utc DESC, id DESC LIMIT 1""",
        (device_id,),
    ).fetchone()
    health_item = dict(health) if health else None
    feature_complete = None
    if health_item:
        feature = connection.execute(
            "SELECT is_complete, coverage_ratio FROM feature_windows WHERE id = ?",
            (health_item["feature_window_id"],),
        ).fetchone()
        if feature:
            feature_complete = bool(feature["is_complete"])
            health_item["coverage_ratio"] = feature["coverage_ratio"]
    risk = connection.execute(
        """SELECT id, risk_evidence_index, evidence_level, evaluated_at_utc
        FROM risk_assessments WHERE device_id = ?
        ORDER BY window_start_utc DESC, id DESC LIMIT 1""",
        (device_id,),
    ).fetchone()
    return {
        "health": health_item,
        "risk": dict(risk) if risk else None,
        "newest_health_window_complete": feature_complete,
        "separation": (
            "Current operating readiness is informational and does not alter "
            "the Workload Suitability Index."
        ),
    }


def build_assessment(
    connection,
    inventory: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    components = [
        _component_result(name, specification, inventory)
        for name, specification in profile["components"].items()
    ]
    missing_required = [
        component["component_name"]
        for component in components
        if component["required"]
        and component["raw_component_score"] is None
    ]
    uncertain = [
        component["component_name"]
        for component in components
        if component["detection_status"] in {"unreliable", "unavailable_optional"}
    ]
    scored = [
        component for component in components
        if component["raw_component_score"] is not None
    ]
    available_weight = sum(component["configured_weight"] for component in scored)
    total_weight = sum(
        float(spec["weight"]) for spec in profile["components"].values()
    )
    excluded_weight = total_weight - available_weight
    for component in scored:
        component["effective_weight"] = (
            component["configured_weight"] / available_weight
            if available_weight else 0.0
        )
    weighted_score = (
        sum(
            component["raw_component_score"] * component["effective_weight"]
            for component in scored
        )
        if scored else None
    )
    state = (
        "not_evaluated"
        if missing_required or weighted_score is None
        else "provisional"
        if uncertain
        else "assessed"
    )
    gates: list[dict[str, Any]] = []
    final_score = weighted_score
    if final_score is not None and state != "not_evaluated":
        for component in components:
            if component["hard_gate_status"] == "failed":
                cap = float(profile["bottleneck_rules"]["hard_gate_failure_cap"])
                before = final_score
                final_score = min(final_score, cap)
                gates.append({
                    "component_name": component["component_name"],
                    "rule_type": "hard_requirement_gate",
                    "configured_cap": cap,
                    "applied": before > final_score,
                    "pre_cap_score": before,
                    "post_cap_score": final_score,
                    "reason_code": "hard_requirement_failed",
                    "explanation": (
                        f"{component['component_name'].replace('_', ' ')} is a "
                        "mandatory capability for this profile."
                    ),
                })
            elif (
                component["required"]
                and component["minimum_passed"] is False
            ):
                severe = (component["raw_component_score"] or 0) < 30
                cap = float(
                    profile["bottleneck_rules"][
                        "severe_below_minimum_cap"
                        if severe else "below_minimum_cap"
                    ]
                )
                before = final_score
                final_score = min(final_score, cap)
                gates.append({
                    "component_name": component["component_name"],
                    "rule_type": "bottleneck_cap",
                    "configured_cap": cap,
                    "applied": before > final_score,
                    "pre_cap_score": before,
                    "post_cap_score": final_score,
                    "reason_code": "required_component_below_minimum",
                    "explanation": (
                        "A strong component cannot hide this below-minimum "
                        "required capability."
                    ),
                })
    if state == "not_evaluated":
        final_score = None
        result = "not_evaluated"
    else:
        final_score = round(max(0.0, min(100.0, float(final_score))), 4)
        result = suitability_result_for_score(final_score)

    limiting_candidates = [
        component for component in components
        if component["raw_component_score"] is not None
        and not component["recommended_passed"]
    ]
    limiting_candidates.sort(
        key=lambda component: (
            component["minimum_passed"] is not False,
            component["raw_component_score"],
            -component["configured_weight"],
        )
    )
    limiting = []
    recommendations = []
    for rank, component in enumerate(limiting_candidates, start=1):
        severity = (
            "critical" if component["hard_gate_status"] == "failed"
            else "high" if component["minimum_passed"] is False
            else "medium"
        )
        gap = 100.0 - float(component["raw_component_score"])
        limiting.append({
            "component_name": component["component_name"],
            "rank": rank,
            "severity": severity,
            "capability_gap": round(gap, 4),
            "reason_code": component["reason_codes"][0],
            "explanation": component["explanation"],
        })
        recommendations.append({
            "component_name": component["component_name"],
            "priority": severity,
            "rank": rank,
            "current_capability": component["detected_value"],
            "target_minimum": component["minimum_threshold"],
            "target_recommended": component["recommended_threshold"],
            "reason_code": component["reason_codes"][0],
            "explanation": component["suggested_action"] or component["explanation"],
            "expected_suitability_benefit": (
                "May reduce this profile-specific capability gap after the "
                "configuration is re-detected; no performance gain is guaranteed."
            ),
            "limitation": (
                "This is profile-based upgrade or verification guidance, not "
                "a commercial product recommendation."
            ),
        })
    recommendations.sort(
        key=lambda item: (PRIORITY_ORDER[item["priority"]], item["rank"])
    )
    for rank, item in enumerate(recommendations, start=1):
        item["rank"] = rank

    readiness = _current_readiness(connection, inventory["device_id"])
    confidence = float(inventory["detection_confidence"])
    relevant_count = len(components)
    uncertain_ratio = len(uncertain) / relevant_count if relevant_count else 1.0
    profile_confidence = round(max(0.0, confidence - uncertain_ratio * 10), 2)
    reasons = [
        *(f"{name}_unavailable_required" for name in missing_required),
        *("important_capability_uncertain" for _ in uncertain[:1]),
        *(gate["reason_code"] for gate in gates),
    ]
    now = _utc_now().isoformat()
    return {
        "device_id": inventory["device_id"],
        "inventory_snapshot_id": inventory["id"],
        "inventory_timestamp_utc": inventory["captured_at_utc"],
        "assessed_at_utc": now,
        "profile_key": profile["key"],
        "profile_name": profile["name"],
        "profile_version": profile["profile_version"],
        "suitability_index": final_score,
        "suitability_result": result,
        "evaluation_state": state,
        "detection_confidence": profile_confidence,
        "available_component_weight": available_weight,
        "excluded_component_weight": excluded_weight,
        "normalization_method": "weighted_mean_across_valid_applicable_components_then_gates",
        "reason_codes": sorted(set(reasons)),
        "explanation": profile["explanation_template"].format(profile=profile["name"]),
        "limitations": profile["limitations"],
        "components": components,
        "gates": gates,
        "limiting_components": limiting,
        "recommendations": recommendations,
        "current_readiness": readiness,
        "created_at_utc": now,
        "updated_at_utc": now,
    }


def _persist_assessment(connection, assessment: dict[str, Any]) -> int:
    health = assessment["current_readiness"]["health"]
    risk = assessment["current_readiness"]["risk"]
    existing = connection.execute(
        """SELECT id, created_at_utc FROM workload_suitability_assessments
        WHERE device_id = ? AND inventory_snapshot_id = ? AND profile_key = ?
        AND profile_version = ? AND algorithm_version = ?
        AND configuration_version = ?""",
        (
            assessment["device_id"],
            assessment["inventory_snapshot_id"],
            assessment["profile_key"],
            assessment["profile_version"],
            ALGORITHM_VERSION,
            CONFIGURATION_VERSION,
        ),
    ).fetchone()
    created = existing["created_at_utc"] if existing else assessment["created_at_utc"]
    connection.execute(
        """INSERT INTO workload_suitability_assessments (
            device_id, inventory_snapshot_id, inventory_timestamp_utc,
            assessed_at_utc, profile_key, profile_name, profile_version,
            suitability_index, suitability_result, evaluation_state,
            detection_confidence, available_component_weight,
            excluded_component_weight, normalization_method,
            algorithm_version, configuration_version, catalogue_version,
            health_assessment_id, risk_assessment_id, current_readiness_json,
            reason_codes_json, explanation, limitations_json,
            created_at_utc, updated_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                  ?, ?, ?, ?, ?, ?)
        ON CONFLICT(
            device_id, inventory_snapshot_id, profile_key, profile_version,
            algorithm_version, configuration_version
        ) DO UPDATE SET
            assessed_at_utc=excluded.assessed_at_utc,
            suitability_index=excluded.suitability_index,
            suitability_result=excluded.suitability_result,
            evaluation_state=excluded.evaluation_state,
            detection_confidence=excluded.detection_confidence,
            available_component_weight=excluded.available_component_weight,
            excluded_component_weight=excluded.excluded_component_weight,
            normalization_method=excluded.normalization_method,
            health_assessment_id=excluded.health_assessment_id,
            risk_assessment_id=excluded.risk_assessment_id,
            current_readiness_json=excluded.current_readiness_json,
            reason_codes_json=excluded.reason_codes_json,
            explanation=excluded.explanation,
            limitations_json=excluded.limitations_json,
            updated_at_utc=excluded.updated_at_utc""",
        (
            assessment["device_id"],
            assessment["inventory_snapshot_id"],
            assessment["inventory_timestamp_utc"],
            assessment["assessed_at_utc"],
            assessment["profile_key"],
            assessment["profile_name"],
            assessment["profile_version"],
            assessment["suitability_index"],
            assessment["suitability_result"],
            assessment["evaluation_state"],
            assessment["detection_confidence"],
            assessment["available_component_weight"],
            assessment["excluded_component_weight"],
            assessment["normalization_method"],
            ALGORITHM_VERSION,
            CONFIGURATION_VERSION,
            CATALOGUE_VERSION,
            health["id"] if health else None,
            risk["id"] if risk else None,
            json.dumps(assessment["current_readiness"]),
            json.dumps(assessment["reason_codes"]),
            assessment["explanation"],
            json.dumps(assessment["limitations"]),
            created,
            assessment["updated_at_utc"],
        ),
    )
    assessment_id = int(connection.execute(
        """SELECT id FROM workload_suitability_assessments
        WHERE inventory_snapshot_id = ? AND profile_key = ?
        AND algorithm_version = ? AND configuration_version = ?""",
        (
            assessment["inventory_snapshot_id"],
            assessment["profile_key"],
            ALGORITHM_VERSION,
            CONFIGURATION_VERSION,
        ),
    ).fetchone()[0])
    for table in (
        "suitability_component_results",
        "suitability_gates_caps",
        "suitability_limiting_components",
        "suitability_recommendations",
    ):
        connection.execute(f"DELETE FROM {table} WHERE assessment_id = ?", (assessment_id,))
    for item in assessment["components"]:
        connection.execute(
            """INSERT INTO suitability_component_results (
                assessment_id, component_name, detected_value_json,
                detection_status, minimum_threshold_json,
                recommended_threshold_json, raw_component_score,
                effective_component_score, configured_weight, effective_weight,
                minimum_passed, recommended_passed, hard_gate_status,
                reason_codes_json, explanation, limitations_json, suggested_action
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                assessment_id, item["component_name"],
                json.dumps(item["detected_value"]), item["detection_status"],
                json.dumps(item["minimum_threshold"]),
                json.dumps(item["recommended_threshold"]),
                item["raw_component_score"], item["effective_component_score"],
                item["configured_weight"], item["effective_weight"],
                item["minimum_passed"], item["recommended_passed"],
                item["hard_gate_status"], json.dumps(item["reason_codes"]),
                item["explanation"], json.dumps(item["limitations"]),
                item["suggested_action"],
            ),
        )
    for item in assessment["gates"]:
        connection.execute(
            """INSERT INTO suitability_gates_caps (
                assessment_id, component_name, rule_type, configured_cap,
                applied, pre_cap_score, post_cap_score, reason_code, explanation
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                assessment_id, item["component_name"], item["rule_type"],
                item["configured_cap"], int(item["applied"]),
                item["pre_cap_score"], item["post_cap_score"],
                item["reason_code"], item["explanation"],
            ),
        )
    for item in assessment["limiting_components"]:
        connection.execute(
            """INSERT INTO suitability_limiting_components (
                assessment_id, component_name, rank, severity, capability_gap,
                reason_code, explanation
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                assessment_id, item["component_name"], item["rank"],
                item["severity"], item["capability_gap"], item["reason_code"],
                item["explanation"],
            ),
        )
    for item in assessment["recommendations"]:
        connection.execute(
            """INSERT INTO suitability_recommendations (
                assessment_id, component_name, priority, rank,
                current_capability_json, target_minimum_json,
                target_recommended_json, reason_code, explanation,
                expected_suitability_benefit, limitation
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                assessment_id, item["component_name"], item["priority"],
                item["rank"], json.dumps(item["current_capability"]),
                json.dumps(item["target_minimum"]),
                json.dumps(item["target_recommended"]), item["reason_code"],
                item["explanation"], item["expected_suitability_benefit"],
                item["limitation"],
            ),
        )
    return assessment_id


def evaluate_profiles(
    database_path: Path | None = None,
    *,
    device_id: str | None = None,
    profile_key: str | None = None,
    force: bool = False,
    command: str = "evaluate",
) -> dict[str, int]:
    path = initialize_database(database_path or get_database_path())
    started = _utc_now().isoformat()
    with database_connection(path) as connection:
        row = connection.execute(
            """SELECT id FROM device_inventory_snapshots
            WHERE (? IS NULL OR device_id = ?)
            ORDER BY captured_at_utc DESC, id DESC LIMIT 1""",
            (device_id, device_id),
        ).fetchone()
        if row is None:
            raise RuntimeError("No inventory snapshot exists. Run --inventory first.")
        inventory = _load_inventory(connection, int(row[0]))
        requested = (
            {profile_key: WORKLOAD_PROFILES[profile_key]}
            if profile_key else WORKLOAD_PROFILES
        )
        assessed = provisional = not_evaluated = skipped = 0
        failures: list[str] = []
        for key, profile in requested.items():
            existing = connection.execute(
                """SELECT id FROM workload_suitability_assessments
                WHERE inventory_snapshot_id = ? AND profile_key = ?
                AND profile_version = ? AND algorithm_version = ?
                AND configuration_version = ?""",
                (
                    inventory["id"], key, profile["profile_version"],
                    ALGORITHM_VERSION, CONFIGURATION_VERSION,
                ),
            ).fetchone()
            if existing and not force:
                skipped += 1
                continue
            try:
                result = build_assessment(connection, inventory, profile)
                with connection:
                    _persist_assessment(connection, result)
                if result["evaluation_state"] == "assessed":
                    assessed += 1
                elif result["evaluation_state"] == "provisional":
                    provisional += 1
                else:
                    not_evaluated += 1
            except Exception as error:
                failures.append(type(error).__name__)
        with connection:
            connection.execute(
                """INSERT INTO quality_evaluation_runs (
                    started_at_utc, finished_at_utc, command, device_id,
                    profile_key, force_requested, algorithm_version,
                    configuration_version, catalogue_version, status,
                    assessed_count, provisional_count, not_evaluated_count,
                    skipped_count, reason_codes_json, error_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    started, _utc_now().isoformat(), command,
                    inventory["device_id"], profile_key, int(force),
                    ALGORITHM_VERSION, CONFIGURATION_VERSION, CATALOGUE_VERSION,
                    "error" if failures else "success",
                    assessed, provisional, not_evaluated, skipped,
                    json.dumps([]), ",".join(sorted(set(failures))) if failures else None,
                ),
            )
    return {
        "assessed": assessed,
        "provisional": provisional,
        "not_evaluated": not_evaluated,
        "skipped": skipped,
    }


def quality_status(
    database_path: Path | None = None,
    device_id: str | None = None,
    *,
    ensure_initialized: bool = True,
) -> dict[str, Any]:
    requested_path = database_path or get_database_path()
    path = (
        initialize_database(requested_path)
        if ensure_initialized else requested_path
    )
    with database_connection(path) as connection:
        inventory = connection.execute(
            """SELECT * FROM device_inventory_snapshots
            WHERE (? IS NULL OR device_id = ?)
            ORDER BY captured_at_utc DESC, id DESC LIMIT 1""",
            (device_id, device_id),
        ).fetchone()
        counts = {
            row["evaluation_state"]: int(row["count"])
            for row in connection.execute(
                """SELECT evaluation_state, COUNT(*) AS count
                FROM workload_suitability_assessments
                WHERE (? IS NULL OR device_id = ?)
                GROUP BY evaluation_state""",
                (device_id, device_id),
            )
        }
    return {
        "status": "available" if inventory else "not_evaluated",
        "inventory": dict(inventory) if inventory else None,
        "assessment_counts": counts,
        "profiles": list(WORKLOAD_PROFILES),
        "algorithm_version": ALGORITHM_VERSION,
        "configuration_version": CONFIGURATION_VERSION,
        "catalogue_version": CATALOGUE_VERSION,
        "interpretation": INTERPRETATION,
        "privacy_excluded_fields": list(PRIVACY_EXCLUDED_FIELDS),
        "privacy_excluded_inputs": [
            {
                "field_name": name,
                "availability_status": "excluded_for_privacy",
            }
            for name in PRIVACY_EXCLUDED_FIELDS
        ],
    }


def maybe_maintain_quality(database_path: Path | None = None) -> None:
    """Refresh at most daily and evaluate only a new inventory/configuration."""
    path = initialize_database(database_path or get_database_path())
    with database_connection(path) as connection:
        latest = connection.execute(
            """SELECT COALESCE(last_checked_at_utc, captured_at_utc)
            FROM device_inventory_snapshots
            ORDER BY COALESCE(last_checked_at_utc, captured_at_utc) DESC,
                     id DESC LIMIT 1"""
        ).fetchone()
    refresh_due = (
        latest is None
        or datetime.fromisoformat(latest[0])
        <= _utc_now() - timedelta(hours=INVENTORY_REFRESH_HOURS)
    )
    if refresh_due:
        refresh_inventory(path)
    with database_connection(path) as connection:
        snapshot = connection.execute(
            """SELECT id FROM device_inventory_snapshots
            ORDER BY captured_at_utc DESC, id DESC LIMIT 1"""
        ).fetchone()
        if snapshot is None:
            return
        missing = connection.execute(
            """SELECT 1 FROM (
                SELECT ? AS profile_key UNION ALL SELECT ? UNION ALL SELECT ?
                UNION ALL SELECT ? UNION ALL SELECT ? UNION ALL SELECT ?
            ) p LEFT JOIN workload_suitability_assessments q
              ON q.inventory_snapshot_id = ? AND q.profile_key = p.profile_key
             AND q.algorithm_version = ? AND q.configuration_version = ?
            WHERE q.id IS NULL LIMIT 1""",
            (
                *WORKLOAD_PROFILES.keys(),
                int(snapshot[0]),
                ALGORITHM_VERSION,
                CONFIGURATION_VERSION,
            ),
        ).fetchone()
    if missing:
        evaluate_profiles(path, command="agent")
    # Fine-grained profile evidence is an additive v2-anchored presentation
    # layer. It never writes calibrated statistics or analytical scores.
    from analytics.profile_quality import evaluate_profile_quality

    evaluate_profile_quality(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="SmartOps PC Quality Check")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--status", action="store_true")
    action.add_argument("--inventory", action="store_true")
    action.add_argument("--refresh-inventory", action="store_true")
    action.add_argument("--evaluate", action="store_true")
    parser.add_argument("--device")
    parser.add_argument("--profile", choices=sorted(WORKLOAD_PROFILES))
    parser.add_argument("--all-profiles", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.status:
        result = quality_status(device_id=args.device)
    elif args.inventory or args.refresh_inventory:
        result = refresh_inventory(device_id=args.device)
    else:
        if args.profile is None and not args.all_profiles:
            args.all_profiles = True
        result = evaluate_profiles(
            device_id=args.device,
            profile_key=args.profile,
            force=args.force,
        )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
