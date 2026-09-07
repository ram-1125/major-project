"""Central, versioned configuration for the Phase 4A System Health Score.

These weights, thresholds, bands, and explanation templates are transparent
SmartOps engineering choices.  They require labelled real-world validation
and must not be presented as calibrated failure prediction.
"""

from __future__ import annotations

from dataclasses import dataclass


ALGORITHM_VERSION = "system-health-v1"
CONFIGURATION_VERSION = "phase4a-v1"

INTERPRETATION = (
    "System Health Score summarizes the computer’s current observed operating "
    "condition using available telemetry, stability and operational evidence. "
    "It is not a failure probability, future-reliability guarantee or PC "
    "quality rating."
)


@dataclass(frozen=True)
class HealthPolicy:
    minimum_coverage: float = 0.8
    minimum_workload_confidence: float = 0.5
    temporal_window_count: int = 6
    minimum_required_metrics: int = 3


DEFAULT_POLICY = HealthPolicy()

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

DISK_IO_EXPECTED_WORKLOADS = {
    "development",
}

REQUIRED_CORE_INPUTS = (
    "cpu_avg",
    "ram_avg",
    "disk_usage_avg",
)

TOP_LEVEL_WEIGHTS = {
    "resource_condition": 45.0,
    "operating_stability": 25.0,
    "operational_events": 20.0,
    "learned_evidence": 10.0,
}

RESOURCE_GROUP_WEIGHTS = {
    "cpu": 25.0,
    "memory": 25.0,
    "swap": 15.0,
    "disk_capacity": 20.0,
    "disk_io": 15.0,
    "cpu_thermal": 10.0,
    "gpu_thermal": 8.0,
    "power": 10.0,
}

OPTIONAL_RESOURCE_GROUPS = {
    "cpu_thermal",
    "gpu_thermal",
    "power",
}

# Each ordered tuple is (lower bound inclusive, deduction on a 0-100 component
# scale).  The first matching threshold is used.
THRESHOLDS: dict[str, tuple[tuple[float, float], ...]] = {
    "cpu": ((90.0, 55.0), (80.0, 30.0), (65.0, 12.0), (50.0, 4.0)),
    "memory": ((95.0, 55.0), (90.0, 35.0), (85.0, 20.0), (75.0, 6.0)),
    "swap": ((75.0, 45.0), (50.0, 30.0), (25.0, 15.0), (10.0, 4.0)),
    "disk_capacity": ((97.0, 70.0), (95.0, 50.0), (90.0, 30.0), (85.0, 15.0), (75.0, 5.0)),
    "disk_io_bytes_per_second": (
        (100 * 1024 * 1024, 45.0),
        (50 * 1024 * 1024, 30.0),
        (10 * 1024 * 1024, 12.0),
        (2 * 1024 * 1024, 3.0),
    ),
    "cpu_thermal": ((95.0, 70.0), (90.0, 45.0), (85.0, 25.0), (75.0, 6.0)),
    "gpu_thermal": ((95.0, 70.0), (90.0, 45.0), (85.0, 25.0), (75.0, 6.0)),
}

CORRELATION_CAPS = {
    "cpu": 55.0,
    "memory_and_swap": 70.0,
    "disk_io": 45.0,
    "thermal": 70.0,
    "stability": 100.0,
    "operational_events": 100.0,
    "learned_evidence": 100.0,
}

HEALTH_BANDS: tuple[tuple[float, str], ...] = (
    (85.0, "good"),
    (70.0, "stable"),
    (50.0, "attention"),
    (30.0, "degraded"),
    (0.0, "critical_condition"),
)

DATA_CONFIDENCE_WEIGHTS = {
    "coverage": 0.35,
    "required_metrics": 0.25,
    "workload_context": 0.15,
    "optional_sensors": 0.05,
    "historical_readiness": 0.20,
}

EVENT_DEDUCTIONS = {
    "critical_event": 35.0,
    "hardware": 28.0,
    "power": 24.0,
    "storage": 20.0,
    "resource_exhaustion": 20.0,
    "application_crash": 10.0,
    "service_failure": 10.0,
    "error_event": 6.0,
    "warning_event": 2.0,
}

DIAGNOSTIC_STEPS = {
    "cpu": "Review the top CPU process snapshot and repeat the same workload.",
    "memory": "Review top memory processes and confirm whether RAM and swap recover.",
    "disk_capacity": "Check free space on monitored fixed partitions using Windows storage settings.",
    "disk_io": "Confirm whether development, installation, or file-copy activity explains disk I/O.",
    "thermal": "Verify temperatures with the hardware manufacturer’s local diagnostic utility.",
    "events": "Review the corresponding mapped entries in Windows Event Viewer locally.",
    "stability": "Continue collecting completed windows to confirm persistence or recovery.",
    "learned": "Compare the Phase 3A deviation and Phase 3B evidence explanations.",
}

LIMITATIONS = (
    "The System Health Score describes observed operating condition, not future reliability.",
    "SmartOps-specific weights and bands have not been clinically, commercially, or industrially validated.",
    "Scores with different available evidence weights may not be perfectly comparable.",
    "Root-cause candidates remain evidence-supported hypotheses and do not prove causality.",
)
