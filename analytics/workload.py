"""Transparent workload and activity-context rules.

The primary workload still describes the foreground user context. Phase
7B.1 also keeps user input activity separate from whole-system activity so a
local monitoring write cannot turn an inactive user into an "active" user.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


WORKLOAD_RULE_VERSION = "phase7b1-workload-v3"
GUIDED_DEVELOPMENT_CONTEXT = "guided_development"
GUIDED_DEVELOPMENT_RULE_VERSION = "guided-development-v1"

# A production window contains ten expected 30-second observations. Guided
# development therefore requires at least one minute (two observations) of
# each foreground context and at least three minutes (six observations) of
# combined development/browser evidence. These are transparent engineering
# rules, not inferred content analysis.
GUIDED_MINIMUM_DEVELOPMENT_SAMPLES = 2
GUIDED_MINIMUM_BROWSER_SAMPLES = 2
GUIDED_MINIMUM_COMBINED_SAMPLES = 6
GUIDED_MINIMUM_COMBINED_PROPORTION = 0.60
USER_IDLE_THRESHOLD_SECONDS = 300.0

# Retain the original quiescence definition. It is not raised to hide SmartOps
# overhead; system activity is stored separately from the input state.
QUIESCENT_CPU_PERCENT = 10.0
QUIESCENT_DISK_BYTES_PER_SECOND = 100_000.0
QUIESCENT_NETWORK_BYTES_PER_SECOND = 100_000.0
BUSY_CPU_PERCENT = 75.0
BUSY_DISK_BYTES_PER_SECOND = 10_000_000.0
BUSY_NETWORK_BYTES_PER_SECOND = 10_000_000.0

DEVELOPMENT_PROCESSES = {
    "code.exe", "devenv.exe", "pycharm64.exe", "idea64.exe",
    "androidstudio64.exe", "rider64.exe",
}
OFFICE_PROCESSES = {
    "winword.exe", "excel.exe", "powerpnt.exe", "outlook.exe",
    "onenote.exe", "acrord32.exe",
}
BROWSER_MEDIA_PROCESSES = {
    "brave.exe", "chrome.exe", "msedge.exe", "firefox.exe", "opera.exe",
    "vlc.exe", "vlc-3.0.23-win64.exe", "wmplayer.exe", "spotify.exe",
}

# Evidence-based mappings only. Riot Client is a launcher and vgc is an
# anti-cheat service, so neither is independently classified as gaming.
GAMING_PROCESSES = {
    "valorant-win64-shipping.exe",
}

PRIMARY_WORKLOAD_PROFILES = (
    "idle",
    "interactive_light",
    "background_activity",
    "development",
    "browser_or_media",
    "office_productivity",
    "compute_intensive",
    "gaming_or_3d",
    "unknown",
)


@dataclass(frozen=True)
class WorkloadClassification:
    workload_class: str
    confidence: float
    reason_codes: list[str]
    user_activity_state: str | None
    system_activity_state: str | None
    rule_version: str = WORKLOAD_RULE_VERSION

    @property
    def provenance(self) -> dict[str, Any]:
        return {
            "rule_version": self.rule_version,
            "user_activity_state": self.user_activity_state,
            "system_activity_state": self.system_activity_state,
            "foreground_only_catalogue_matching": True,
        }


def classify_window_composition(
    workload_classes: list[str],
    *,
    expected_sample_count: int,
    is_complete: bool,
) -> dict[str, Any]:
    """Build auditable foreground composition and an optional mixed context."""
    sample_count = len(workload_classes)
    counts = {
        profile: workload_classes.count(profile)
        for profile in PRIMARY_WORKLOAD_PROFILES
    }
    profiles = {
        profile: {
            "count": count,
            "proportion": count / sample_count if sample_count else 0.0,
        }
        for profile, count in counts.items()
    }
    development_count = counts["development"]
    browser_count = counts["browser_or_media"]
    combined_count = development_count + browser_count
    combined_proportion = combined_count / sample_count if sample_count else 0.0
    qualifies = bool(
        is_complete
        and sample_count >= expected_sample_count
        and development_count >= GUIDED_MINIMUM_DEVELOPMENT_SAMPLES
        and browser_count >= GUIDED_MINIMUM_BROWSER_SAMPLES
        and combined_count >= GUIDED_MINIMUM_COMBINED_SAMPLES
        and combined_proportion >= GUIDED_MINIMUM_COMBINED_PROPORTION
    )
    return {
        "sample_count": sample_count,
        "expected_sample_count": expected_sample_count,
        "profiles": profiles,
        "secondary_context": (
            GUIDED_DEVELOPMENT_CONTEXT if qualifies else None
        ),
        "secondary_rule_version": GUIDED_DEVELOPMENT_RULE_VERSION,
        "reason_codes": (
            [
                "recognized_development_and_browser_foreground",
                "minimum_one_minute_each_context",
                "minimum_three_minutes_combined_context",
            ]
            if qualifies
            else ["guided_development_threshold_not_met"]
        ),
        "thresholds": {
            "development_samples": GUIDED_MINIMUM_DEVELOPMENT_SAMPLES,
            "browser_media_samples": GUIDED_MINIMUM_BROWSER_SAMPLES,
            "combined_samples": GUIDED_MINIMUM_COMBINED_SAMPLES,
            "combined_proportion": GUIDED_MINIMUM_COMBINED_PROPORTION,
        },
        "privacy": "uses_foreground_executable_class_only",
    }


def _number(sample: Mapping[str, Any], name: str) -> float | None:
    value = sample.get(name)
    return float(value) if isinstance(value, (int, float)) else None


def _user_activity(sample: Mapping[str, Any]) -> str | None:
    state = sample.get("user_state")
    idle_seconds = _number(sample, "user_idle_seconds")
    if state == "idle" or (
        idle_seconds is not None and idle_seconds >= USER_IDLE_THRESHOLD_SECONDS
    ):
        return "idle"
    if state == "active" or (
        idle_seconds is not None and idle_seconds < USER_IDLE_THRESHOLD_SECONDS
    ):
        return "active"
    return None


def _sum_if_available(
    sample: Mapping[str, Any], first: str, second: str
) -> float | None:
    left = _number(sample, first)
    right = _number(sample, second)
    if left is None or right is None:
        return None
    return left + right


def classify_system_activity(sample: Mapping[str, Any]) -> str | None:
    """Classify system load without changing the independent input state."""
    cpu = _number(sample, "cpu_percent")
    disk = _sum_if_available(
        sample, "disk_read_bytes_per_second", "disk_write_bytes_per_second"
    )
    network = _sum_if_available(
        sample,
        "network_upload_bytes_per_second",
        "network_download_bytes_per_second",
    )
    if cpu is None or disk is None or network is None:
        return None
    if (
        cpu < QUIESCENT_CPU_PERCENT
        and disk < QUIESCENT_DISK_BYTES_PER_SECOND
        and network < QUIESCENT_NETWORK_BYTES_PER_SECOND
    ):
        return "quiescent"
    if (
        cpu >= BUSY_CPU_PERCENT
        or disk >= BUSY_DISK_BYTES_PER_SECOND
        or network >= BUSY_NETWORK_BYTES_PER_SECOND
    ):
        return "busy"
    return "background"


def _result(
    workload_class: str,
    confidence: float,
    reasons: list[str],
    user_activity: str | None,
    system_activity: str | None,
) -> WorkloadClassification:
    activity_reason = (
        "system_activity_unavailable"
        if system_activity is None
        else f"system_{system_activity}"
    )
    return WorkloadClassification(
        workload_class,
        confidence,
        [*reasons, activity_reason],
        user_activity,
        system_activity,
    )


def classify_workload(sample: Mapping[str, Any]) -> WorkloadClassification:
    """Classify context without interpreting utilization as a failure."""
    user_activity = _user_activity(sample)
    system_activity = classify_system_activity(sample)
    cpu = _number(sample, "cpu_percent")
    gpu = _number(sample, "gpu_utilization_percent")
    foreground = str(sample.get("foreground_process_name") or "").casefold()

    # Idle now means user-input inactivity. Whether the computer itself is
    # quiescent, doing background work, or busy is retained independently.
    if user_activity == "idle":
        return _result(
            "idle", 0.9,
            ["user_idle", "idle_profile_represents_user_inactivity"],
            user_activity, system_activity,
        )

    if user_activity == "active" and foreground in DEVELOPMENT_PROCESSES:
        return _result(
            "development", 0.95,
            ["user_active", "development_foreground"],
            user_activity, system_activity,
        )
    if user_activity == "active" and foreground in OFFICE_PROCESSES:
        return _result(
            "office_productivity", 0.9,
            ["user_active", "office_foreground"],
            user_activity, system_activity,
        )
    if user_activity == "active" and foreground in BROWSER_MEDIA_PROCESSES:
        return _result(
            "browser_or_media", 0.88,
            ["user_active", "browser_media_foreground"],
            user_activity, system_activity,
        )
    if user_activity == "active" and foreground in GAMING_PROCESSES:
        return _result(
            "gaming_or_3d", 0.98,
            ["user_active", "explicit_gaming_foreground"],
            user_activity, system_activity,
        )
    if (
        user_activity == "active"
        and cpu is not None and cpu >= 30
        and gpu is not None and gpu >= 50
    ):
        return _result(
            "gaming_or_3d", 0.85,
            ["user_active", "high_cpu", "high_gpu"],
            user_activity, system_activity,
        )
    if user_activity == "active" and cpu is not None and cpu >= 75:
        return _result(
            "compute_intensive", 0.72,
            ["user_active", "high_cpu"],
            user_activity, system_activity,
        )
    if user_activity == "active" and foreground:
        return _result(
            "interactive_light", 0.65,
            ["user_active", "foreground_available", "foreground_not_in_catalogue"],
            user_activity, system_activity,
        )
    if user_activity == "active" and not foreground and gpu is None:
        return _result(
            "unknown", 0.3,
            ["user_active", "foreground_missing", "gpu_missing"],
            user_activity, system_activity,
        )
    return _result(
        "unknown", 0.2, ["insufficient_signals"],
        user_activity, system_activity,
    )
