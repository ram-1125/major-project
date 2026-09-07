"""Central Phase 7B enhanced-evidence definitions and retention policy.

These signals are collected in shadow mode. Nothing in this catalogue is
allowed to change Risk Evidence, System Health, or predictive-alert severity.
"""

from __future__ import annotations

from dataclasses import dataclass


ALGORITHM_VERSION = "enhanced-evidence-v1"
CONFIGURATION_VERSION = "enhanced-evidence-config-v1"
SHADOW_MODE = True
DISPLAY_POLICY_VERSION = "device-capability-display-v1"

# These reasons describe capabilities that the supported local interfaces
# cannot expose on the current device. They are safe to omit from the active
# UI catalogue and safe to stop re-recording once the same capability result
# has already been stored. Timeouts, permission failures and collector errors
# are intentionally absent because users must continue to see them.
PERMANENT_UNSUPPORTED_REASONS = frozenset({
    "not_reliably_exposed_by_supported_counter",
    "process_creation_auditing_not_safely_available",
    "battery_metric_not_exposed",
    "battery_not_present",
    "storage_reliability_not_exposed_by_hardware",
    "unsupported_metric",
    "unsupported_platform",
})

PERFORMANCE_COLLECTION_SECONDS = 60
HARDWARE_COLLECTION_SECONDS = 900
RETENTION_MAINTENANCE_SECONDS = 21_600

RAW_SIGNAL_RETENTION_DAYS = 30
HOURLY_AGGREGATE_RETENTION_DAYS = 365
EVENT_EVIDENCE_RETENTION_DAYS = 365
COLLECTION_RUN_RETENTION_DAYS = 30
RETENTION_AUDIT_DAYS = 90

PERFORMANCE_TIMEOUT_SECONDS = 5
HARDWARE_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class PerformanceSignal:
    key: str
    group: str
    label: str
    unit: str
    counter_path: str
    transform: str = "identity"


PERFORMANCE_SIGNALS = (
    PerformanceSignal(
        "cpu_processor_queue_length",
        "cpu",
        "Processor queue length",
        "threads",
        r"\System\Processor Queue Length",
    ),
    PerformanceSignal(
        "cpu_context_switches_per_second",
        "cpu",
        "Context switches",
        "per_second",
        r"\System\Context Switches/sec",
    ),
    PerformanceSignal(
        "cpu_interrupt_time_percent",
        "cpu",
        "Interrupt time",
        "percent",
        r"\Processor Information(_Total)\% Interrupt Time",
    ),
    PerformanceSignal(
        "cpu_dpc_time_percent",
        "cpu",
        "DPC time",
        "percent",
        r"\Processor Information(_Total)\% DPC Time",
    ),
    PerformanceSignal(
        "cpu_maximum_frequency_percent",
        "cpu",
        "Maximum-frequency utilization",
        "percent",
        r"\Processor Information(_Total)\% of Maximum Frequency",
    ),
    PerformanceSignal(
        "cpu_performance_limit_percent",
        "cpu",
        "Processor performance limit",
        "percent",
        r"\Processor Information(_Total)\% Performance Limit",
    ),
    PerformanceSignal(
        "memory_committed_percent",
        "memory",
        "Committed memory",
        "percent",
        r"\Memory\% Committed Bytes In Use",
    ),
    PerformanceSignal(
        "memory_pages_input_per_second",
        "memory",
        "Hard-fault input pages",
        "pages_per_second",
        r"\Memory\Pages Input/sec",
    ),
    PerformanceSignal(
        "memory_page_reads_per_second",
        "memory",
        "Page-read operations",
        "per_second",
        r"\Memory\Page Reads/sec",
    ),
    PerformanceSignal(
        "memory_paged_pool_bytes",
        "memory",
        "Paged pool",
        "bytes",
        r"\Memory\Pool Paged Bytes",
    ),
    PerformanceSignal(
        "memory_nonpaged_pool_bytes",
        "memory",
        "Non-paged pool",
        "bytes",
        r"\Memory\Pool Nonpaged Bytes",
    ),
    PerformanceSignal(
        "storage_read_latency_seconds",
        "storage",
        "Average disk read latency",
        "seconds",
        r"\PhysicalDisk(_Total)\Avg. Disk sec/Read",
    ),
    PerformanceSignal(
        "storage_write_latency_seconds",
        "storage",
        "Average disk write latency",
        "seconds",
        r"\PhysicalDisk(_Total)\Avg. Disk sec/Write",
    ),
    PerformanceSignal(
        "storage_queue_length",
        "storage",
        "Average disk queue length",
        "operations",
        r"\PhysicalDisk(_Total)\Avg. Disk Queue Length",
    ),
    PerformanceSignal(
        "storage_io_active_percent",
        "storage",
        "Busiest physical disk active time",
        "percent",
        r"\PhysicalDisk(*)\% Idle Time",
        "active_from_idle",
    ),
)


HARDWARE_SIGNAL_DETAILS = (
    ("storage_temperature_celsius", "storage", "Storage temperature", "celsius"),
    ("storage_wear_percent", "storage", "Storage wear indication", "percent"),
    ("storage_read_errors_total", "storage", "Storage read errors", "count"),
    ("storage_write_errors_total", "storage", "Storage write errors", "count"),
    (
        "storage_uncorrected_errors_total",
        "storage",
        "Uncorrected storage errors",
        "count",
    ),
    (
        "battery_full_charge_capacity_mwh",
        "power",
        "Battery full-charge capacity",
        "milliwatt_hours",
    ),
    (
        "battery_design_capacity_mwh",
        "power",
        "Battery design capacity",
        "milliwatt_hours",
    ),
    ("battery_health_percent", "power", "Battery capacity health", "percent"),
    ("battery_discharge_rate_mw", "power", "Battery discharge rate", "milliwatts"),
    (
        "battery_remaining_capacity_mwh",
        "power",
        "Battery remaining capacity",
        "milliwatt_hours",
    ),
)


EXPLICIT_UNAVAILABLE_SIGNALS = (
    (
        "memory_paging_duration_percent",
        "memory",
        "Paging duration",
        "percent",
        "not_reliably_exposed_by_supported_counter",
    ),
    (
        "storage_io_stall_duration_seconds",
        "storage",
        "I/O stall duration",
        "seconds",
        "not_reliably_exposed_by_supported_counter",
    ),
    (
        "application_repeated_process_restarts",
        "application",
        "Repeated process restarts",
        "count",
        "process_creation_auditing_not_safely_available",
    ),
)


EXISTING_RAW_SIGNAL_DETAILS = (
    (
        "memory_available_bytes",
        "memory",
        "Available memory",
        "bytes",
        "ram_available_bytes",
    ),
    (
        "storage_read_throughput_bytes_per_second",
        "storage",
        "Disk read throughput",
        "bytes_per_second",
        "disk_read_bytes_per_second",
    ),
    (
        "storage_write_throughput_bytes_per_second",
        "storage",
        "Disk write throughput",
        "bytes_per_second",
        "disk_write_bytes_per_second",
    ),
)
