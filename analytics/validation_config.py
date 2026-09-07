"""Central Phase 5B predictive-validation policy.

These thresholds are SmartOps engineering choices.  They are deliberately
centralized and versioned so that every published result is reconstructable.
They still require validation with representative, independently labelled
real-world evidence.
"""

from __future__ import annotations

from dataclasses import dataclass


ALGORITHM_VERSION = "validation-v1"
CONFIGURATION_VERSION = "phase5b-v1"
MATCHING_VERSION = "incident-matching-v1"

INTERPRETATION = (
    "SmartOps validation results are based on available user-reported or "
    "externally verified outcomes. They do not by themselves establish "
    "guaranteed failure prediction, hardware diagnosis, or universally "
    "validated accuracy."
)

INCIDENT_CATEGORIES = (
    "system_crash",
    "unexpected_restart",
    "application_failure",
    "system_freeze",
    "severe_slowdown",
    "memory_exhaustion",
    "disk_capacity_issue",
    "disk_io_issue",
    "thermal_shutdown_or_throttling",
    "driver_or_device_issue",
    "repeated_serious_event",
    "other_operational_issue",
)
INCIDENT_SEVERITIES = ("minor", "moderate", "serious", "critical")
VERIFICATION_STATUSES = (
    "user_reported",
    "externally_verified",
    "uncertain",
    "withdrawn",
)
FEEDBACK_OUTCOMES = (
    "confirmed_related_issue",
    "likely_related_issue",
    "no_issue_observed",
    "preventive_action_taken",
    "uncertain",
    "not_yet_verified",
    "incorrect_category",
    "withdrawn",
)
MATCH_TYPES = (
    "confirmed_match",
    "probable_match",
    "possible_match",
    "rejected_match",
    "unmatched",
)

# Phase 5A alert categories are intentionally mapped broadly. Automatic
# matching only proposes a candidate; it can never create a confirmed match.
CATEGORY_COMPATIBILITY = {
    "application_instability": {"application_failure", "severe_slowdown"},
    "unexpected_interruption": {"system_crash", "unexpected_restart"},
    "storage_pressure": {"disk_capacity_issue", "disk_io_issue", "severe_slowdown"},
    "memory_pressure": {"memory_exhaustion", "severe_slowdown", "system_freeze"},
    "thermal_pressure": {"thermal_shutdown_or_throttling", "severe_slowdown"},
    "power_instability": {"unexpected_restart", "system_crash"},
    "network_pressure": {"severe_slowdown", "driver_or_device_issue"},
    "resource_pressure": {
        "memory_exhaustion",
        "severe_slowdown",
        "system_freeze",
    },
    "degraded_system_health": {
        "severe_slowdown",
        "system_crash",
        "other_operational_issue",
    },
    "increasing_risk_evidence": set(INCIDENT_CATEGORIES),
    "memory_and_swap_pressure": {
        "memory_exhaustion", "severe_slowdown", "system_freeze"
    },
    "disk_capacity_pressure": {"disk_capacity_issue", "severe_slowdown"},
    "disk_io_pressure": {"disk_io_issue", "severe_slowdown"},
    "thermal_evidence": {
        "thermal_shutdown_or_throttling", "severe_slowdown"
    },
    "repeated_serious_event": {
        "application_failure",
        "system_crash",
        "unexpected_restart",
        "system_freeze",
        "memory_exhaustion",
        "disk_capacity_issue",
        "disk_io_issue",
        "thermal_shutdown_or_throttling",
        "driver_or_device_issue",
        "repeated_serious_event",
    },
    "system_stability": {
        "application_failure",
        "system_crash",
        "unexpected_restart",
        "system_freeze",
    },
    "data_quality_limitation": set(),
}


@dataclass(frozen=True)
class ValidationPolicy:
    """Versioned evidence and publication requirements."""

    minimum_window_coverage: float = 0.8
    minimum_no_issue_horizon_seconds: float = 24 * 60 * 60
    matching_lookback_seconds: float = 24 * 60 * 60
    matching_late_seconds: float = 60 * 60
    probable_match_score: float = 0.70
    possible_match_score: float = 0.45
    minimum_precision_alerts: int = 20
    minimum_recall_incidents: int = 10
    minimum_accuracy_windows: int = 100
    minimum_accuracy_distinct_days: int = 7
    minimum_accuracy_period_seconds: float = 7 * 24 * 60 * 60
    minimum_lead_time_matches: int = 5


DEFAULT_POLICY = ValidationPolicy()
