"""Central, versioned Phase 5A predictive-alert policy.

The values in this module are transparent SmartOps engineering choices.  They
are intentionally kept together so an alert can always be reconstructed and
so future validation can revise policy without hiding behavioural changes.
"""

from __future__ import annotations

from dataclasses import dataclass


ALGORITHM_VERSION = "smartops-alerts-1.0"
CONFIGURATION_VERSION = "phase5a-config-1"
CATALOGUE_VERSION = "phase5a-catalogue-1"

# Documentation metadata only. Alert generation continues to use the existing
# rule code in alerts.py; these values make future explanation snapshots
# reconstructable without changing thresholds or severity behaviour.
ALERT_TRIGGER_THRESHOLDS = {
    "resource_pressure": {"cpu_avg_minimum": 85.0, "cpu_high_ratio_minimum": 0.70},
    "memory_and_swap_pressure": {"ram_avg_minimum": 85.0, "swap_corroboration_minimum": 10.0},
    "disk_capacity_pressure": {"disk_usage_avg_minimum": 90.0},
    "disk_io_pressure": {"combined_bytes_per_second_minimum": 50 * 1024 * 1024},
    "thermal_evidence": {"available_temperature_celsius_minimum": 85.0},
    "repeated_serious_event": {"critical_event_minimum": 1, "mapped_error_event_minimum": 2},
    "system_stability": {"operating_stability_score_below": 70.0},
    "increasing_risk_evidence": {"risk_evidence_index_minimum": 50.0},
    "degraded_system_health": {"system_health_score_below": 70.0},
    "data_quality_limitation": {"data_confidence_below": 60.0},
}

INTERPRETATION = (
    "SmartOps alerts indicate observed operational evidence that may require "
    "attention. They are not guaranteed predictions of failure, confirmed "
    "hardware diagnoses, or calibrated probabilities of a future crash."
)

SEVERITY_ORDER = {
    "informational": 0,
    "advisory": 1,
    "warning": 2,
    "urgent": 3,
}


@dataclass(frozen=True)
class AlertDefinition:
    code: str
    title: str
    description: str
    evidence_domain: str
    required_evidence: tuple[str, ...]
    optional_evidence: tuple[str, ...]
    minimum_persistence: int
    recovery_windows: int
    cooldown_windows: int
    workload_exceptions: tuple[str, ...]
    explanation_template: str
    diagnostic_steps: tuple[str, ...]
    preventive_guidance: tuple[str, ...]
    limitations: tuple[str, ...]


COMMON_LIMITATIONS = (
    "This alert is an operational interpretation, not a failure probability.",
    "Thresholds require empirical validation with labelled real-world data.",
)

DEFINITIONS = {
    "resource_pressure": AlertDefinition(
        "ALT-CPU-001", "Persistent CPU resource pressure",
        "CPU pressure supported by completed five-minute windows.",
        "cpu", ("cpu_avg",), ("cpu_high_ratio", "cpu_p95"),
        2, 2, 2, ("gaming_or_3d", "development", "compute_intensive"),
        "CPU pressure persisted for {consecutive} completed windows.",
        (
            "Review the top CPU process snapshot and confirm the workload is expected.",
            "Observe whether utilization returns to its normal range after the workload ends.",
        ),
        ("Keep adequate cooling and avoid unnecessary concurrent heavy workloads.",),
        COMMON_LIMITATIONS,
    ),
    "memory_and_swap_pressure": AlertDefinition(
        "ALT-MEM-001", "Memory and page-file pressure",
        "Correlated RAM and page-file evidence from completed windows.",
        "memory", ("ram_avg",), ("swap_avg", "memory_high_ratio"),
        2, 2, 2, (),
        "Memory pressure persisted for {consecutive} completed windows.",
        (
            "Review the top memory process snapshot for an expected workload.",
            "Check whether available memory recovers and page-file use stops increasing.",
        ),
        ("Close unneeded applications only after saving work.",),
        COMMON_LIMITATIONS,
    ),
    "disk_capacity_pressure": AlertDefinition(
        "ALT-DISK-001", "Low disk-capacity headroom",
        "The monitored disk has limited remaining capacity.",
        "storage_capacity", ("disk_usage_avg",), (),
        1, 2, 6, (),
        "Disk utilization reduced available capacity headroom.",
        (
            "Confirm free space in Windows Settings or File Explorer.",
            "Identify safe, user-approved cleanup opportunities without deleting data automatically.",
        ),
        ("Maintain enough free space for Windows updates and temporary files.",),
        COMMON_LIMITATIONS,
    ),
    "disk_io_pressure": AlertDefinition(
        "ALT-DISK-002", "Persistent disk I/O pressure",
        "Read and write throughput indicate sustained disk activity.",
        "storage_io", ("disk_read_avg", "disk_write_avg"), (),
        2, 2, 2, ("development",),
        "Disk I/O pressure persisted for {consecutive} completed windows.",
        (
            "Confirm whether installation, compilation, backup, or file-copy work is active.",
            "Review storage-related Windows events if the workload does not explain the activity.",
        ),
        ("Allow intensive storage work to complete before starting another heavy task.",),
        COMMON_LIMITATIONS,
    ),
    "thermal_evidence": AlertDefinition(
        "ALT-THERM-001", "Sustained thermal evidence",
        "Available temperature sensors show elevated thermal evidence.",
        "thermal", ("cpu_temperature_avg",), ("gpu_temperature_avg",),
        2, 2, 3, (),
        "Available temperature evidence remained elevated.",
        (
            "Verify ventilation is unobstructed and fans are operating normally.",
            "Cross-check temperatures with a trusted local hardware utility.",
        ),
        ("Use the computer on a firm surface with unobstructed airflow.",),
        COMMON_LIMITATIONS + ("Sensor availability and accuracy vary by hardware.",),
    ),
    "repeated_serious_event": AlertDefinition(
        "ALT-EVENT-001", "Serious operational event evidence",
        "Mapped Critical/Error Windows events provide explicit operational evidence.",
        "windows_events", ("mapped_windows_event",), (),
        1, 2, 4, (),
        "Relevant Windows event evidence was observed in this completed window.",
        (
            "Review the mapped provider, event ID, category, and time in SmartOps.",
            "Use Windows Event Viewer for administrator-approved deeper investigation.",
        ),
        ("Keep Windows and vendor-supported drivers maintained through normal channels.",),
        COMMON_LIMITATIONS + ("Mapped events are evidence, not confirmed causality.",),
    ),
    "system_stability": AlertDefinition(
        "ALT-STAB-001", "Reduced operating stability",
        "Repeated instability evidence is present across completed windows.",
        "stability", ("health_operating_stability",), ("application_events",),
        2, 2, 3, (),
        "Operating-stability evidence persisted across recent completed windows.",
        (
            "Review recent application, service, power, and interruption evidence.",
            "Confirm whether the condition recurs after normal workload activity ends.",
        ),
        ("Save work regularly while stability evidence remains active.",),
        COMMON_LIMITATIONS,
    ),
    "increasing_risk_evidence": AlertDefinition(
        "ALT-RISK-001", "Increasing Risk Evidence Index",
        "Eligible Phase 3B operational evidence is elevated or increasing.",
        "learned_evidence", ("risk_assessment",), ("root_cause_candidates",),
        2, 2, 3, (),
        "Eligible risk evidence persisted or increased across completed windows.",
        (
            "Review the Phase 3B evidence components and probable contributing factors.",
            "Verify the strongest factor using the associated diagnostic steps.",
        ),
        ("Continue monitoring for recovery or independently corroborating evidence.",),
        COMMON_LIMITATIONS + (
            "The Risk Evidence Index is evidence strength, not failure probability.",
        ),
    ),
    "degraded_system_health": AlertDefinition(
        "ALT-HEALTH-001", "Degraded current operating condition",
        "An evaluated Phase 4A System Health Score indicates reduced condition.",
        "system_health", ("health_assessment",), ("health_deductions",),
        1, 2, 3, (),
        "The evaluated System Health Score is in the {health_band} band.",
        (
            "Review the System Health component deductions and excluded inputs.",
            "Verify the highest effective deduction before changing system settings.",
        ),
        ("Continue monitoring completed windows for recovery evidence.",),
        COMMON_LIMITATIONS + (
            "System Health Score summarizes current condition and is not a future guarantee.",
        ),
    ),
    "data_quality_limitation": AlertDefinition(
        "ALT-DATA-001", "Operational data-quality limitation",
        "An evaluated assessment has limited confidence in available evidence.",
        "data_quality", ("evaluated_health_assessment",), ("excluded_inputs",),
        2, 2, 6, (),
        "Data confidence remained limited across completed windows.",
        (
            "Review excluded required inputs and feature-window coverage.",
            "Confirm the telemetry agent is running continuously.",
        ),
        ("Keep SmartOps running long enough to collect complete windows.",),
        COMMON_LIMITATIONS,
    ),
}
