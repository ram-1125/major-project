"""Collect privacy-conscious Windows telemetry for one raw sample."""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar

import psutil


T = TypeVar("T")
IDLE_THRESHOLD_SECONDS = 300.0
PROCESS_CPU_OVERFLOW_TOLERANCE = 0.000001


def _try_metric(reader: Callable[[], T]) -> T | None:
    """Return None when Windows or the hardware cannot provide a metric."""
    try:
        return reader()
    except (
        AttributeError,
        OSError,
        PermissionError,
        RuntimeError,
        TypeError,
        ValueError,
        psutil.Error,
    ):
        return None


def _system_disk_root() -> str:
    """Choose the system drive on Windows and the filesystem root elsewhere."""
    home_anchor = Path.home().anchor
    return home_anchor or os.path.abspath(os.sep)


class CounterRateCalculator:
    """Convert cumulative operating-system counters into per-second rates."""

    def __init__(self) -> None:
        self._previous: dict[str, tuple[float, float]] = {}

    def calculate(
        self,
        counters: dict[str, float | int | None],
        timestamp_seconds: float,
    ) -> dict[str, float | None]:
        rates: dict[str, float | None] = {}
        for name, current_value in counters.items():
            if current_value is None:
                rates[name] = None
                continue

            current = float(current_value)
            previous = self._previous.get(name)
            if previous is None:
                rates[name] = None
            else:
                previous_value, previous_time = previous
                elapsed = timestamp_seconds - previous_time
                difference = current - previous_value
                rates[name] = (
                    difference / elapsed
                    if elapsed > 0 and difference >= 0
                    else None
                )

            self._previous[name] = (current, timestamp_seconds)
        return rates


def _is_fixed_partition(mountpoint: str) -> bool:
    """Use the Windows drive type API; other platforms use mounted partitions."""
    if sys.platform != "win32":
        return True
    try:
        drive_type = ctypes.windll.kernel32.GetDriveTypeW(mountpoint)
        return drive_type == 3  # DRIVE_FIXED
    except (AttributeError, OSError):
        return False


def _collect_disk_partitions() -> list[dict[str, Any]]:
    partitions: list[dict[str, Any]] = []
    for partition in psutil.disk_partitions(all=False):
        if not _is_fixed_partition(partition.mountpoint):
            continue
        usage = _try_metric(lambda path=partition.mountpoint: psutil.disk_usage(path))
        if usage is None:
            continue
        partitions.append(
            {
                "mountpoint": partition.mountpoint,
                "percent": float(usage.percent),
                "used_bytes": int(usage.used),
                "free_bytes": int(usage.free),
                "total_bytes": int(usage.total),
            }
        )
    return partitions


def _normalize_process_cpu_percent(
    raw_cpu_percent: float,
    logical_cpu_count: int,
) -> float | None:
    """Convert psutil's per-process value to a whole-system 0-100% value.

    psutil can report 100% for each fully used logical CPU, so a multi-threaded
    process may legitimately exceed 100% before normalization. Division happens
    before the tiny floating-point overflow check for that reason.
    """
    if logical_cpu_count < 1 or raw_cpu_percent < 0:
        return None

    normalized = raw_cpu_percent / logical_cpu_count
    if 100.0 < normalized <= 100.0 + PROCESS_CPU_OVERFLOW_TOLERANCE:
        return 100.0
    if normalized > 100.0:
        return None
    return normalized


def _is_system_idle_process(pid: int, process_name: str) -> bool:
    return pid == 0 or process_name.strip().casefold() == "system idle process"


def _collect_process_metrics() -> dict[str, Any]:
    """Collect aggregate process counts and the allowed top-process fields."""
    process_rows: list[tuple[psutil.Process, dict[str, Any]]] = []
    thread_count = 0
    logical_cpu_count = _try_metric(lambda: psutil.cpu_count(logical=True)) or 1

    for process in psutil.process_iter(["pid", "name", "memory_percent"]):
        try:
            process.cpu_percent(interval=None)
            thread_count += process.num_threads()
            process_rows.append((process, process.info))
        except (psutil.Error, OSError, PermissionError):
            continue

    # A short measurement window makes per-process CPU values useful even for
    # --once, while keeping a collection cycle well below the 30-second interval.
    if process_rows:
        time.sleep(0.05)

    snapshots: list[dict[str, Any]] = []
    for process, info in process_rows:
        try:
            pid = int(info["pid"])
            process_name = str(info.get("name") or "Unknown")
            raw_cpu_percent = float(process.cpu_percent(interval=None))
            snapshots.append(
                {
                    "pid": pid,
                    "process_name": process_name,
                    "cpu_percent": _normalize_process_cpu_percent(
                        raw_cpu_percent,
                        logical_cpu_count,
                    ),
                    "memory_percent": float(info.get("memory_percent") or 0.0),
                }
            )
        except (psutil.Error, OSError, PermissionError, TypeError, ValueError):
            continue

    cpu_candidates = [
        process
        for process in snapshots
        if process["cpu_percent"] is not None
        and not _is_system_idle_process(
            process["pid"],
            process["process_name"],
        )
    ]
    top_cpu = sorted(
        cpu_candidates,
        key=lambda process: process["cpu_percent"],
        reverse=True,
    )[:5]
    top_memory = sorted(
        snapshots,
        key=lambda process: process["memory_percent"],
        reverse=True,
    )[:5]
    process_count = _try_metric(lambda: len(psutil.pids()))
    return {
        "process_count": (
            process_count if process_count is not None else len(process_rows)
        ),
        "thread_count": thread_count,
        "process_snapshots": {"cpu": top_cpu, "memory": top_memory},
    }


def _collect_windows_user_state() -> tuple[float | None, str | None, str | None]:
    """Return idle seconds, active/idle state, and foreground executable name."""
    if sys.platform != "win32":
        return None, None, None

    class LastInputInfo(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    idle_seconds: float | None = None
    foreground_name: str | None = None
    try:
        last_input = LastInputInfo()
        last_input.cbSize = ctypes.sizeof(last_input)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(last_input)):
            current_ticks = ctypes.windll.kernel32.GetTickCount64() & 0xFFFFFFFF
            idle_milliseconds = (current_ticks - last_input.dwTime) & 0xFFFFFFFF
            idle_seconds = max(0.0, idle_milliseconds / 1000.0)
    except (AttributeError, OSError, ValueError):
        idle_seconds = None

    try:
        window_handle = ctypes.windll.user32.GetForegroundWindow()
        if window_handle:
            process_id = ctypes.c_ulong()
            ctypes.windll.user32.GetWindowThreadProcessId(
                window_handle,
                ctypes.byref(process_id),
            )
            if process_id.value:
                foreground_name = psutil.Process(process_id.value).name()
    except (AttributeError, OSError, psutil.Error):
        foreground_name = None

    user_state = (
        "idle" if idle_seconds is not None and idle_seconds >= IDLE_THRESHOLD_SECONDS
        else "active" if idle_seconds is not None
        else None
    )
    return idle_seconds, user_state, foreground_name


def _collect_battery() -> dict[str, Any]:
    battery = _try_metric(psutil.sensors_battery)
    if battery is None:
        return {
            "battery_percent": None,
            "battery_charging": None,
            "ac_power_connected": None,
            "battery_seconds_remaining": None,
        }

    power_plugged = (
        bool(battery.power_plugged)
        if battery.power_plugged is not None
        else None
    )
    seconds_remaining = (
        float(battery.secsleft)
        if isinstance(battery.secsleft, (int, float)) and battery.secsleft >= 0
        else None
    )
    return {
        "battery_percent": float(battery.percent),
        "battery_charging": (
            power_plugged and battery.percent < 100
            if power_plugged is not None
            else None
        ),
        "ac_power_connected": power_plugged,
        "battery_seconds_remaining": seconds_remaining,
    }


def _collect_cpu_temperature() -> float | None:
    temperature_reader = getattr(psutil, "sensors_temperatures", None)
    if temperature_reader is None:
        return None
    readings = _try_metric(temperature_reader)
    if not readings:
        return None
    cpu_sensor_names = ("cpu", "coretemp", "k10temp", "zenpower")
    values = [
        float(entry.current)
        for sensor_name, entries in readings.items()
        if any(name in sensor_name.lower() for name in cpu_sensor_names)
        for entry in entries
        if getattr(entry, "current", None) is not None
    ]
    return sum(values) / len(values) if values else None


def _collect_gpu_metrics() -> dict[str, float | None]:
    """Use nvidia-smi only when that optional local hardware tool is present."""
    unavailable = {
        "gpu_utilization_percent": None,
        "gpu_memory_percent": None,
        "gpu_temperature_celsius": None,
    }
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return unavailable

    try:
        result = subprocess.run(
            [
                executable,
                "--query-gpu=utilization.gpu,utilization.memory,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        rows = [
            [float(value.strip()) for value in line.split(",")]
            for line in result.stdout.splitlines()
            if line.strip()
        ]
        if not rows:
            return unavailable
        return {
            "gpu_utilization_percent": sum(row[0] for row in rows) / len(rows),
            "gpu_memory_percent": sum(row[1] for row in rows) / len(rows),
            "gpu_temperature_celsius": sum(row[2] for row in rows) / len(rows),
        }
    except (
        OSError,
        subprocess.SubprocessError,
        TypeError,
        ValueError,
        IndexError,
    ):
        return unavailable


def collect_metrics(
    device_id: str,
    rate_calculator: CounterRateCalculator | None = None,
) -> dict[str, Any]:
    """Collect one raw sample without reading private user content."""
    calculator = rate_calculator or CounterRateCalculator()
    monotonic_time = time.monotonic()
    now = datetime.now(timezone.utc)

    memory = _try_metric(psutil.virtual_memory)
    swap = _try_metric(psutil.swap_memory)
    disk = _try_metric(lambda: psutil.disk_usage(_system_disk_root()))
    disk_io = _try_metric(psutil.disk_io_counters)
    network_io = _try_metric(psutil.net_io_counters)
    network_stats = _try_metric(psutil.net_if_stats)
    boot_time = _try_metric(psutil.boot_time)
    cpu_frequency = _try_metric(psutil.cpu_freq)
    per_core = _try_metric(lambda: psutil.cpu_percent(interval=0.1, percpu=True))
    total_cpu = (
        sum(per_core) / len(per_core)
        if isinstance(per_core, list) and per_core
        else _try_metric(lambda: psutil.cpu_percent(interval=0.1))
    )
    process_metrics = _try_metric(_collect_process_metrics) or {
        "process_count": None,
        "thread_count": None,
        "process_snapshots": {"cpu": [], "memory": []},
    }
    user_idle_seconds, user_state, foreground_process_name = (
        _collect_windows_user_state()
    )

    cumulative_counters = {
        "disk_read_bytes": getattr(disk_io, "read_bytes", None),
        "disk_write_bytes": getattr(disk_io, "write_bytes", None),
        "disk_read_ops": getattr(disk_io, "read_count", None),
        "disk_write_ops": getattr(disk_io, "write_count", None),
        "network_upload_bytes": getattr(network_io, "bytes_sent", None),
        "network_download_bytes": getattr(network_io, "bytes_recv", None),
        "network_packets_sent": getattr(network_io, "packets_sent", None),
        "network_packets_received": getattr(network_io, "packets_recv", None),
    }
    rates = calculator.calculate(cumulative_counters, monotonic_time)
    partitions = _try_metric(_collect_disk_partitions) or []
    battery = _collect_battery()
    gpu = _collect_gpu_metrics()

    return {
        "timestamp_utc": now.isoformat(),
        "device_id": device_id,
        "cpu_percent": total_cpu,
        "cpu_per_core_percent": per_core,
        "cpu_physical_cores": _try_metric(lambda: psutil.cpu_count(logical=False)),
        "cpu_logical_cores": _try_metric(lambda: psutil.cpu_count(logical=True)),
        "cpu_frequency_mhz": getattr(cpu_frequency, "current", None),
        "process_count": process_metrics["process_count"],
        "thread_count": process_metrics["thread_count"],
        "ram_percent": getattr(memory, "percent", None),
        "ram_used_bytes": getattr(memory, "used", None),
        "ram_available_bytes": getattr(memory, "available", None),
        "ram_total_bytes": getattr(memory, "total", None),
        "swap_percent": getattr(swap, "percent", None),
        "swap_used_bytes": getattr(swap, "used", None),
        "swap_total_bytes": getattr(swap, "total", None),
        "disk_percent": getattr(disk, "percent", None),
        "disk_used_bytes": getattr(disk, "used", None),
        "disk_free_bytes": getattr(disk, "free", None),
        "disk_total_bytes": getattr(disk, "total", None),
        "disk_partitions": partitions,
        "disk_read_bytes_per_second": rates["disk_read_bytes"],
        "disk_write_bytes_per_second": rates["disk_write_bytes"],
        "disk_read_ops_per_second": rates["disk_read_ops"],
        "disk_write_ops_per_second": rates["disk_write_ops"],
        "disk_read_bytes_total": cumulative_counters["disk_read_bytes"],
        "disk_write_bytes_total": cumulative_counters["disk_write_bytes"],
        "disk_read_ops_total": cumulative_counters["disk_read_ops"],
        "disk_write_ops_total": cumulative_counters["disk_write_ops"],
        "network_upload_bytes_per_second": rates["network_upload_bytes"],
        "network_download_bytes_per_second": rates["network_download_bytes"],
        "network_packets_sent_per_second": rates["network_packets_sent"],
        "network_packets_received_per_second": rates["network_packets_received"],
        "network_bytes_sent_total": cumulative_counters["network_upload_bytes"],
        "network_bytes_received_total": cumulative_counters[
            "network_download_bytes"
        ],
        "network_packets_sent_total": cumulative_counters[
            "network_packets_sent"
        ],
        "network_packets_received_total": cumulative_counters[
            "network_packets_received"
        ],
        "network_interface_available": (
            any(interface.isup for interface in network_stats.values())
            if network_stats is not None
            else None
        ),
        **battery,
        "uptime_seconds": (
            max(0.0, now.timestamp() - float(boot_time))
            if boot_time is not None
            else None
        ),
        "boot_timestamp_utc": (
            datetime.fromtimestamp(float(boot_time), timezone.utc).isoformat()
            if boot_time is not None
            else None
        ),
        "user_idle_seconds": user_idle_seconds,
        "user_state": user_state,
        "foreground_process_name": foreground_process_name,
        "cpu_temperature_celsius": _collect_cpu_temperature(),
        **gpu,
        "process_snapshots": process_metrics["process_snapshots"],
    }
