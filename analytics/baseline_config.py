"""Central Phase 3A baseline, deviation, and model policy."""

from __future__ import annotations

import os
from dataclasses import dataclass


DEVICE_SCOPE = "__device__"
ALGORITHM_VERSION = "robust-baseline-v1"
CONFIGURATION_VERSION = "phase3a-v1"
ISOLATION_VERSION = "isolation-forest-v1"


@dataclass(frozen=True)
class BaselinePolicy:
    minimum_coverage: float = 0.8
    minimum_valid_features: int = 5
    minimum_device_windows: int = 100
    minimum_workload_windows: int = 30
    minimum_distinct_days: int = 3
    recommended_days: int = 7
    preferred_days: int = 14
    stale_after_days: int = 7
    exclude_critical_events: bool = True
    exclude_serious_events: bool = True
    isolation_minimum_windows: int = 100
    isolation_contamination: float = 0.05
    isolation_random_seed: int = 42
    retrain_after_hours: int = 24
    retrain_after_new_windows: int = 10


DEFAULT_POLICY = BaselinePolicy(
    retrain_after_hours=int(os.getenv("SMARTOPS_BASELINE_RETRAIN_HOURS", "24")),
    retrain_after_new_windows=int(
        os.getenv("SMARTOPS_BASELINE_NEW_WINDOWS", "10")
    ),
)


@dataclass(frozen=True)
class FeatureDefinition:
    direction: str
    weight: float
    optional: bool = False
    isolation_forest: bool = True


# "higher" means only above-normal observations contribute to concern.
# "two_sided" means either direction can be operationally unusual.
# "informational" is explained but has a deliberately small combined weight.
FEATURE_DEFINITIONS: dict[str, FeatureDefinition] = {
    "cpu_avg": FeatureDefinition("higher", 1.0),
    "cpu_max": FeatureDefinition("higher", 0.8),
    "cpu_p95": FeatureDefinition("higher", 0.9),
    "ram_avg": FeatureDefinition("higher", 1.0),
    "ram_max": FeatureDefinition("higher", 0.8),
    "swap_avg": FeatureDefinition("higher", 0.8),
    "swap_max": FeatureDefinition("higher", 0.7),
    "disk_usage_avg": FeatureDefinition("higher", 0.9),
    "disk_usage_max": FeatureDefinition("higher", 0.7),
    "disk_read_avg": FeatureDefinition("two_sided", 0.6),
    "disk_write_avg": FeatureDefinition("two_sided", 0.7),
    "network_upload_avg": FeatureDefinition("informational", 0.2),
    "network_download_avg": FeatureDefinition("informational", 0.2),
    "active_ratio": FeatureDefinition("two_sided", 0.3),
    "idle_ratio": FeatureDefinition("two_sided", 0.3),
    "process_count_avg": FeatureDefinition("two_sided", 0.5),
    "cpu_temperature_avg": FeatureDefinition("higher", 0.9, optional=True),
    "gpu_utilization_avg": FeatureDefinition("two_sided", 0.4, optional=True),
    "gpu_memory_avg": FeatureDefinition("higher", 0.5, optional=True),
    "gpu_temperature_avg": FeatureDefinition("higher", 0.9, optional=True),
    # Event outcomes remain transparent statistical context and are excluded
    # from Isolation Forest inputs.
    "critical_event_count": FeatureDefinition("higher", 1.0, isolation_forest=False),
    "hardware_event_count": FeatureDefinition("higher", 1.0, isolation_forest=False),
    "storage_event_count": FeatureDefinition("higher", 1.0, isolation_forest=False),
    "power_event_count": FeatureDefinition("higher", 1.0, isolation_forest=False),
    "application_crash_count": FeatureDefinition(
        "higher", 0.8, isolation_forest=False
    ),
    "service_failure_count": FeatureDefinition(
        "higher", 0.8, isolation_forest=False
    ),
    "resource_exhaustion_count": FeatureDefinition(
        "higher", 0.9, isolation_forest=False
    ),
}

CONTEXTUAL_HIGH_CPU_WORKLOADS = {
    "development",
    "guided_development",
    "gaming_or_3d",
    "compute_intensive",
}

SEVERITY_THRESHOLDS = {
    "mild": 1.5,
    "elevated": 2.5,
    "high": 4.0,
}
