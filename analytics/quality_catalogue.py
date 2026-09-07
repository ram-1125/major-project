"""Versioned SmartOps Phase 4B workload-suitability profiles.

These are initial SmartOps engineering profiles, not universal requirements or
benchmark standards. Software-specific profiles added later must cite verified
official requirements rather than copying these general-purpose assumptions.
"""

from __future__ import annotations

ALGORITHM_VERSION = "workload-suitability-v1"
CONFIGURATION_VERSION = "phase4b-v1"
CATALOGUE_VERSION = "smartops-workloads-v1"
INVENTORY_PROVIDER_VERSION = "windows-safe-inventory-v1"
INVENTORY_REFRESH_HOURS = 24

_LEGACY_INTERPRETATION_TEXT = (
    "PC Quality Check estimates the computer’s suitability for a selected "
    "workload using detected hardware and system capabilities. It is not a "
    "benchmark result, failure prediction, future-reliability guarantee or "
    "confirmation that every application will run successfully."
)

INTERPRETATION = (
    "PC Quality Check estimates the computer\u2019s suitability for a selected "
    "workload using detected hardware and system capabilities. It is not a "
    "benchmark result, failure prediction, future-reliability guarantee or "
    "confirmation that every application will run successfully."
)

GB = 1024**3

COMMON_LIMITATIONS = [
    "The profile thresholds are initial SmartOps engineering assumptions requiring validation.",
    "The assessment does not run a performance benchmark or stress test.",
    "Application performance also depends on software versions, drivers, cooling, and workload details.",
]


def _profile(
    key: str,
    name: str,
    description: str,
    intended_workload: str,
    *,
    cpu: tuple[int, int],
    ram_gb: tuple[int, int],
    storage_gb: tuple[int, int],
    weights: dict[str, float],
    dedicated_gpu: bool = False,
    gpu_memory_gb: tuple[int, int] | None = None,
    graphics_required: bool = False,
    virtualization_optional: bool = False,
) -> dict[str, object]:
    components: dict[str, dict[str, object]] = {
        "cpu_capability": {
            "required": True,
            "weight": weights["cpu_capability"],
            "metric": "physical_cores",
            "minimum": cpu[0],
            "recommended": cpu[1],
            "unit": "physical cores",
            "hard_gate": False,
        },
        "memory_capability": {
            "required": True,
            "weight": weights["memory_capability"],
            "metric": "ram_installed_bytes",
            "minimum": ram_gb[0] * GB,
            "recommended": ram_gb[1] * GB,
            "unit": "bytes",
            "hard_gate": False,
        },
        "storage_capability": {
            "required": True,
            "weight": weights["storage_capability"],
            "metric": "system_drive_total_bytes",
            "minimum": storage_gb[0] * GB,
            "recommended": storage_gb[1] * GB,
            "unit": "bytes",
            "hard_gate": False,
        },
        "graphics_capability": {
            "required": graphics_required,
            "weight": weights["graphics_capability"],
            "metric": "gpu_classification",
            "minimum": "dedicated" if dedicated_gpu else "integrated_or_better",
            "recommended": "dedicated" if dedicated_gpu else "integrated_or_better",
            "unit": "graphics class",
            "hard_gate": dedicated_gpu,
            "dedicated_required": dedicated_gpu,
            "gpu_memory_minimum": (
                gpu_memory_gb[0] * GB if gpu_memory_gb else None
            ),
            "gpu_memory_recommended": (
                gpu_memory_gb[1] * GB if gpu_memory_gb else None
            ),
        },
        "operating_system_capability": {
            "required": True,
            "weight": weights["operating_system_capability"],
            "metric": "os_architecture",
            "minimum": "64-bit Windows",
            "recommended": "64-bit Windows",
            "unit": "architecture",
            "hard_gate": True,
        },
    }
    optional_weight = weights.get("optional_acceleration_capability", 0.0)
    if optional_weight:
        components["optional_acceleration_capability"] = {
            "required": False,
            "weight": optional_weight,
            "metric": "virtualization_capable",
            "minimum": False,
            "recommended": True,
            "unit": "capability",
            "hard_gate": False,
            "not_applicable_when_missing": not virtualization_optional,
        }
    return {
        "key": key,
        "name": name,
        "description": description,
        "intended_workload": intended_workload,
        "profile_version": "1.0",
        "configuration_version": CONFIGURATION_VERSION,
        "catalogue_version": CATALOGUE_VERSION,
        "threshold_source": {
            "kind": "smartops_engineering_profile",
            "validation_state": "requires_labelled_real_world_validation",
            "official_application_requirements": False,
        },
        "components": components,
        "bottleneck_rules": {
            "below_minimum_cap": 59.0,
            "severe_below_minimum_cap": 39.0,
            "hard_gate_failure_cap": 39.0,
        },
        "explanation_template": (
            "{profile} was compared with detected local CPU, memory, storage, "
            "graphics, and Windows architecture capabilities."
        ),
        "limitations": list(COMMON_LIMITATIONS),
    }


WORKLOAD_PROFILES = {
    "everyday_productivity": _profile(
        "everyday_productivity",
        "Everyday productivity",
        "Web, communication, office documents, media, and routine multitasking.",
        "General daily Windows use",
        cpu=(2, 4),
        ram_gb=(8, 16),
        storage_gb=(128, 256),
        weights={
            "cpu_capability": 25,
            "memory_capability": 30,
            "storage_capability": 20,
            "graphics_capability": 10,
            "operating_system_capability": 15,
        },
    ),
    "software_development": _profile(
        "software_development",
        "Software development",
        "Editors, local builds, test suites, containers used manually, and developer tools.",
        "General local software development",
        cpu=(4, 8),
        ram_gb=(8, 16),
        storage_gb=(256, 512),
        virtualization_optional=True,
        weights={
            "cpu_capability": 28,
            "memory_capability": 30,
            "storage_capability": 20,
            "graphics_capability": 5,
            "operating_system_capability": 12,
            "optional_acceleration_capability": 5,
        },
    ),
    "data_analysis_and_light_ml": _profile(
        "data_analysis_and_light_ml",
        "Data analysis and light ML",
        "Local notebooks, tabular analysis, visualization, and small classical ML workloads.",
        "CPU-oriented data analysis and light machine learning",
        cpu=(4, 8),
        ram_gb=(16, 32),
        storage_gb=(256, 512),
        virtualization_optional=True,
        weights={
            "cpu_capability": 25,
            "memory_capability": 35,
            "storage_capability": 20,
            "graphics_capability": 5,
            "operating_system_capability": 10,
            "optional_acceleration_capability": 5,
        },
    ),
    "local_ai_and_gpu_compute": _profile(
        "local_ai_and_gpu_compute",
        "Local AI and GPU compute",
        "GPU-accelerated local models and compute tasks that require dedicated graphics memory.",
        "Local GPU compute and model experimentation",
        cpu=(6, 12),
        ram_gb=(16, 32),
        storage_gb=(512, 1024),
        dedicated_gpu=True,
        graphics_required=True,
        gpu_memory_gb=(6, 12),
        weights={
            "cpu_capability": 15,
            "memory_capability": 20,
            "storage_capability": 15,
            "graphics_capability": 40,
            "operating_system_capability": 10,
        },
    ),
    "content_creation": _profile(
        "content_creation",
        "Content creation",
        "Photo work, timeline-based video editing, audio projects, and graphics creation.",
        "General local creative workloads",
        cpu=(6, 12),
        ram_gb=(16, 32),
        storage_gb=(512, 1024),
        gpu_memory_gb=(4, 8),
        weights={
            "cpu_capability": 25,
            "memory_capability": 25,
            "storage_capability": 20,
            "graphics_capability": 20,
            "operating_system_capability": 10,
        },
    ),
    "modern_3d_gaming": _profile(
        "modern_3d_gaming",
        "Modern 3D gaming",
        "Recent 3D games where a dedicated GPU and sufficient graphics memory are expected.",
        "General modern 3D gaming",
        cpu=(6, 8),
        ram_gb=(16, 32),
        storage_gb=(512, 1024),
        dedicated_gpu=True,
        graphics_required=True,
        gpu_memory_gb=(6, 12),
        weights={
            "cpu_capability": 25,
            "memory_capability": 20,
            "storage_capability": 15,
            "graphics_capability": 30,
            "operating_system_capability": 10,
        },
    ),
}

SUITABILITY_BANDS = (
    (90.0, "well_suited"),
    (75.0, "suitable"),
    (60.0, "suitable_with_limits"),
    (40.0, "upgrade_recommended"),
    (0.0, "insufficient"),
)

PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
