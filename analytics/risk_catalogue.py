"""Central, versioned Phase 3B evidence rules.

The catalogue is intentionally data-oriented.  A reviewer can inspect this one
file to see which metrics, events, exceptions, weights, and diagnostic checks
are used by the deterministic evidence-fusion layer.
"""

from __future__ import annotations

from dataclasses import dataclass


ALGORITHM_VERSION = "risk-evidence-v1"
CONFIGURATION_VERSION = "phase3b-v1"
CATALOGUE_VERSION = "evidence-catalogue-v1"


@dataclass(frozen=True)
class RiskPolicy:
    minimum_coverage: float = 0.8
    minimum_workload_confidence: float = 0.5
    minimum_core_metrics: int = 3
    persistence_windows: int = 6
    event_correlation_minutes: int = 5


DEFAULT_POLICY = RiskPolicy()

EVIDENCE_LEVELS: tuple[tuple[float, str], ...] = (
    (85.0, "critical_evidence"),
    (70.0, "high"),
    (50.0, "elevated"),
    (25.0, "guarded"),
    (0.0, "low"),
)

VALID_WORKLOADS = {
    "idle",
    "interactive_light",
    "office_productivity",
    "browser_or_media",
    "development",
    "gaming_or_3d",
    "compute_intensive",
    "background_activity",
}

CPU_EXPECTED_WORKLOADS = {
    "development",
    "gaming_or_3d",
    "compute_intensive",
}

CORE_QUALITY_METRICS = (
    "cpu_avg",
    "ram_avg",
    "disk_usage_avg",
    "process_count_avg",
)

# Only the strongest signal in each group is allowed to contribute to the
# statistical component.  This prevents correlated summaries (for example CPU
# average, maximum, and p95) from being counted as independent problems.
CORRELATION_GROUPS: dict[str, tuple[str, ...]] = {
    "cpu": ("cpu_avg", "cpu_max", "cpu_p95"),
    "memory": ("ram_avg", "ram_max"),
    "swap": ("swap_avg", "swap_max"),
    "storage_capacity": ("disk_usage_avg", "disk_usage_max"),
    "storage_io": ("disk_read_avg", "disk_write_avg"),
    "network": ("network_upload_avg", "network_download_avg"),
    "activity": ("active_ratio", "idle_ratio"),
    "processes": ("process_count_avg",),
    "cpu_thermal": ("cpu_temperature_avg",),
    "gpu": ("gpu_utilization_avg", "gpu_memory_avg"),
    "gpu_thermal": ("gpu_temperature_avg",),
}

GROUP_WEIGHTS: dict[str, float] = {
    "cpu": 1.0,
    "memory": 1.0,
    "swap": 0.8,
    "storage_capacity": 0.9,
    "storage_io": 0.7,
    "network": 0.2,
    "activity": 0.3,
    "processes": 0.5,
    "cpu_thermal": 0.9,
    "gpu": 0.4,
    "gpu_thermal": 0.9,
}

# Contribution maxima make score reconstruction straightforward.
COMPONENT_MAXIMUMS = {
    "statistical_deviation": 35.0,
    "isolation_forest": 10.0,
    "persistence": 15.0,
    "trend": 10.0,
    "serious_events": 25.0,
    "cross_metric_corroboration": 10.0,
    "workload_compatibility": 10.0,
    "data_quality_penalty": 5.0,
}

EVENT_STRENGTH: dict[str, float] = {
    "hardware": 18.0,
    "power": 15.0,
    "storage": 12.0,
    "resource_exhaustion": 10.0,
    "application_crash": 6.0,
    "service_failure": 6.0,
    "system_warning": 2.0,
    "application_warning": 1.0,
}

SEVERITY_MULTIPLIER = {
    "Critical": 1.2,
    "Error": 1.0,
    "Warning": 0.5,
}


@dataclass(frozen=True)
class DomainRule:
    domain: str
    required_evidence: tuple[str, ...]
    supporting_evidence: tuple[str, ...]
    contradictory_evidence: tuple[str, ...]
    workload_exceptions: tuple[str, ...]
    persistence_required: int
    event_categories: tuple[str, ...]
    metric_directions: tuple[tuple[str, str], ...]
    evidence_weight: float
    minimum_data_quality: str
    explanation_template: str
    verification_steps: tuple[str, ...]
    limitations: tuple[str, ...]


COMMON_LIMITATION = (
    "Telemetry correlation supports a candidate, but it cannot prove causality.",
)

DOMAIN_RULES: dict[str, DomainRule] = {
    "cpu_pressure": DomainRule(
        "cpu_pressure", ("cpu",), ("processes", "resource_exhaustion"),
        ("cpu_workload_expected",), tuple(CPU_EXPECTED_WORKLOADS), 2,
        ("resource_exhaustion",), (("cpu", "higher"),), 1.0, "sufficient",
        "CPU pressure remained above the applicable baseline{persistence}.",
        (
            "Review the top CPU process snapshot for the affected time.",
            "Repeat the workload and compare CPU behavior with the learned baseline.",
        ),
        COMMON_LIMITATION,
    ),
    "memory_pressure": DomainRule(
        "memory_pressure", ("memory",), ("swap", "resource_exhaustion"),
        ("memory_recovery",), (), 2, ("resource_exhaustion",),
        (("memory", "higher"), ("swap", "higher")), 1.0, "sufficient",
        "RAM pressure exceeded the applicable baseline{persistence}.",
        (
            "Review the top memory process snapshot.",
            "Check whether RAM and page-file use recover after the workload ends.",
        ),
        COMMON_LIMITATION,
    ),
    "possible_memory_growth": DomainRule(
        "possible_memory_growth", ("memory", "increasing_trend"),
        ("swap", "processes"), ("memory_recovery",), (), 3,
        ("resource_exhaustion",), (("memory", "higher"),), 0.9, "sufficient",
        "RAM usage increased across consecutive completed windows{persistence}.",
        (
            "Observe the same workload for additional windows.",
            "Compare repeated top-memory process snapshots for sustained growth.",
        ),
        (
            "Increasing RAM use can be normal caching or workload initialization.",
            *COMMON_LIMITATION,
        ),
    ),
    "swap_pressure": DomainRule(
        "swap_pressure", ("swap",), ("memory", "resource_exhaustion"),
        ("swap_recovery",), (), 2, ("resource_exhaustion",),
        (("swap", "higher"),), 0.9, "sufficient",
        "Page-file activity was elevated relative to the baseline{persistence}.",
        (
            "Check available physical memory and the top memory processes.",
            "Confirm whether swap use falls after applications are closed.",
        ),
        COMMON_LIMITATION,
    ),
    "storage_capacity_pressure": DomainRule(
        "storage_capacity_pressure", ("storage_capacity",), ("storage",),
        ("storage_capacity_recovery",), (), 2, ("storage",),
        (("storage_capacity", "higher"),), 0.9, "sufficient",
        "Disk capacity use exceeded the applicable baseline{persistence}.",
        (
            "Check free space on monitored fixed partitions.",
            "Use Windows storage settings to identify large categories without exposing files to SmartOps.",
        ),
        COMMON_LIMITATION,
    ),
    "storage_io_pressure": DomainRule(
        "storage_io_pressure", ("storage_io",), ("storage",),
        ("disk_io_workload_expected",), ("development",), 2, ("storage",),
        (("storage_io", "higher"),), 0.8, "sufficient",
        "Disk transfer activity was unusual for the applicable baseline{persistence}.",
        (
            "Review the workload running at the affected time.",
            "Check Windows disk health tools if storage events also occurred.",
        ),
        COMMON_LIMITATION,
    ),
    "thermal_stress": DomainRule(
        "thermal_stress", ("cpu_thermal",), ("gpu_thermal",),
        ("temperature_sensor_unavailable",), (), 2, ("hardware",),
        (("cpu_thermal", "higher"), ("gpu_thermal", "higher")), 1.0, "sufficient",
        "Available temperature sensors reported sustained elevation{persistence}.",
        (
            "Verify temperatures with the hardware manufacturer's diagnostic utility.",
            "Check airflow and fan operation without changing settings automatically.",
        ),
        (
            "Temperature support varies by hardware and driver.",
            *COMMON_LIMITATION,
        ),
    ),
    "power_instability": DomainRule(
        "power_instability", ("power",), ("critical_event",), (), (), 1,
        ("power",), (), 1.0, "sufficient",
        "Mapped power events occurred near the evaluated deviation window.",
        (
            "Review Windows reliability history and power connections.",
            "Check manufacturer power diagnostics if events repeat.",
        ),
        COMMON_LIMITATION,
    ),
    "hardware_error_evidence": DomainRule(
        "hardware_error_evidence", ("hardware",), ("critical_event",), (), (), 1,
        ("hardware",), (), 1.0, "sufficient",
        "Mapped hardware-error events occurred near the evaluated deviation window.",
        (
            "Run the relevant manufacturer hardware diagnostic.",
            "Review Windows Event Viewer for the original event details locally.",
        ),
        COMMON_LIMITATION,
    ),
    "application_instability": DomainRule(
        "application_instability", ("application_crash",), ("memory", "cpu"), (),
        (), 1, ("application_crash",), (), 0.7, "sufficient",
        "Mapped application-crash events occurred near the evaluated window.",
        (
            "Check whether the same application repeatedly stops.",
            "Review its local Windows reliability entry for details.",
        ),
        COMMON_LIMITATION,
    ),
    "service_instability": DomainRule(
        "service_instability", ("service_failure",), ("resource_exhaustion",), (),
        (), 1, ("service_failure",), (), 0.7, "sufficient",
        "Mapped service-failure events occurred near the evaluated window.",
        (
            "Check the affected service in Windows Services.",
            "Review its mapped Event Viewer entries locally.",
        ),
        COMMON_LIMITATION,
    ),
    "resource_exhaustion": DomainRule(
        "resource_exhaustion", ("resource_exhaustion",),
        ("memory", "swap", "storage_capacity"), (), (), 1,
        ("resource_exhaustion",), (), 1.0, "sufficient",
        "Windows reported mapped resource-exhaustion evidence near the window.",
        (
            "Review RAM, swap, disk capacity, and top-process evidence together.",
            "Repeat the workload and confirm whether resource pressure recurs.",
        ),
        COMMON_LIMITATION,
    ),
    "unusual_background_activity": DomainRule(
        "unusual_background_activity", ("cpu", "idle_context"),
        ("network", "storage_io", "processes"), ("active_context",), (), 2, (),
        (("cpu", "higher"),), 0.8, "sufficient",
        "CPU activity persisted while the user context was idle{persistence}.",
        (
            "Review the top CPU process snapshot.",
            "Confirm whether scheduled maintenance or security scanning explains the activity.",
        ),
        COMMON_LIMITATION,
    ),
    "network_activity_context": DomainRule(
        "network_activity_context", ("network",), (), ("network_only",), (), 1, (),
        (("network", "two_sided"),), 0.2, "sufficient",
        "Network transfer activity differed from the learned pattern.",
        (
            "Confirm whether downloads, synchronization, or media activity were expected.",
        ),
        (
            "Network activity alone is informational and does not establish a fault.",
            *COMMON_LIMITATION,
        ),
    ),
    "insufficient_data": DomainRule(
        "insufficient_data", (), (), (), (), 1, (), (), 0.0, "insufficient",
        "The evaluation prerequisites were not satisfied.",
        ("Continue collecting complete five-minute windows.",),
        ("No Risk Evidence Index is produced when required evidence is unavailable.",),
    ),
}

