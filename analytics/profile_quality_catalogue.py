"""Versioned fine-grained PC Quality taxonomy and scoring configuration.

The taxonomy is separate from the immutable calibrated workload taxonomy.
Thresholds and weights are initial SmartOps engineering choices informed by
resource-oriented workload-characterisation research; they are not universal
application requirements or validated performance guarantees.
"""

from __future__ import annotations

TAXONOMY_VERSION = "smartops-pc-quality-taxonomy-v2"
DETECTION_RULE_VERSION = "postcalibration-profile-detection-v1"
SCORING_METHOD_VERSION = "v2-anchored-profile-quality-v1"
SEMANTIC_VERSION = "current-workload-headroom-semantics-v1"
MEASURE_NAME = "Current Workload Headroom"

INTERPRETATION = (
    "This score describes available operating headroom during the most recent "
    "qualifying observed workload period. It is not benchmark performance, "
    "hardware capability, prediction accuracy or a guarantee of future performance."
)

GUIDE_EXPLANATION = (
    "SmartOps observes how the computer behaves during a recognized workload. "
    "The headroom score shows whether measured resources remained within expected "
    "ranges during the selected period. A score of 100.0 means no measured factor "
    "crossed its expected range; it does not mean 100% accuracy or perfect hardware. "
    "Profiles without sufficient recognized evidence remain Not observed or Not "
    "evaluated instead of receiving an estimated score."
)

FULL_SCORE_EXPLANATION = (
    "All measured factors in this observed period remained within their expected ranges."
)

RESEARCH_REFERENCES = [
    {
        "title": "PC workload characterization",
        "url": "https://research.ibm.com/publications/pc-workload-characterization",
        "supports": (
            "Dynamic workload traces reveal resource contention and behaviour that "
            "static instruction assumptions omit."
        ),
    },
    {
        "title": "Characterization of runtime resource usage from executable programs",
        "url": "https://doi.org/10.1016/j.asoc.2017.09.013",
        "supports": (
            "CPU, memory and I/O usage patterns can distinguish runtime resource demand."
        ),
    },
    {
        "title": "A Unified Approach to Interpreting Model Predictions",
        "url": "https://doi.org/10.48550/arXiv.1705.07874",
        "supports": (
            "Prediction explanations should expose feature-level contributions; "
            "SmartOps uses a deterministic additive adaptation, not SHAP itself."
        ),
    },
    {
        "title": "The Precision-Recall Plot Is More Informative than the ROC Plot",
        "url": "https://doi.org/10.1371/journal.pone.0118432",
        "supports": (
            "Precision and recall are essential alongside accuracy for imbalanced outcomes."
        ),
    },
]


BASE_METRICS = {
    "cpu_avg": {"label": "CPU utilisation", "direction": "lower_is_better", "unit": "%"},
    "ram_avg": {"label": "RAM utilisation", "direction": "lower_is_better", "unit": "%"},
    "swap_avg": {"label": "Page-file pressure", "direction": "lower_is_better", "unit": "%"},
    "disk_usage_avg": {"label": "Disk capacity used", "direction": "lower_is_better", "unit": "%"},
    "cpu_stddev": {"label": "CPU variability", "direction": "lower_is_better", "unit": "percentage points"},
    "critical_event_count": {"label": "Critical events", "direction": "lower_is_better", "unit": "events"},
    "error_event_count": {"label": "Error events", "direction": "lower_is_better", "unit": "events"},
}


def _metrics(
    cpu: float,
    ram: float,
    swap: float,
    disk: float,
    variability: float,
    events: float,
    *,
    cpu_high: float = 80,
    ram_high: float = 85,
) -> dict[str, dict[str, object]]:
    weights = {
        "cpu_avg": cpu, "ram_avg": ram, "swap_avg": swap,
        "disk_usage_avg": disk, "cpu_stddev": variability,
        "critical_event_count": events * 0.6,
        "error_event_count": events * 0.4,
    }
    return {
        key: {
            **BASE_METRICS[key],
            "weight": weight,
            "recommended_high": (
                cpu_high if key == "cpu_avg" else
                ram_high if key == "ram_avg" else
                10 if key == "swap_avg" else
                85 if key == "disk_usage_avg" else
                25 if key == "cpu_stddev" else
                0
            ),
            "limit_high": (
                98 if key == "cpu_avg" else
                96 if key == "ram_avg" else
                60 if key == "swap_avg" else
                97 if key == "disk_usage_avg" else
                55 if key == "cpu_stddev" else
                (1 if key == "critical_event_count" else 3)
            ),
            "required": key in {"cpu_avg", "ram_avg", "disk_usage_avg"},
        }
        for key, weight in weights.items() if weight > 0
    }


def _profile(
    key: str,
    name: str,
    parent: str,
    foreground: tuple[str, ...],
    supporting: tuple[str, ...],
    weights: tuple[float, float, float, float, float, float],
    *,
    description: str,
    cpu_high: float = 80,
    ram_high: float = 85,
    foreground_required: bool = True,
) -> dict[str, object]:
    return {
        "key": key,
        "name": name,
        "parent_workload_profile": parent,
        "description": description,
        "foreground_executables": list(foreground),
        "supporting_executables": list(supporting),
        "foreground_required": foreground_required,
        "metrics": _metrics(*weights, cpu_high=cpu_high, ram_high=ram_high),
        "taxonomy_version": TAXONOMY_VERSION,
        "detection_rule_version": DETECTION_RULE_VERSION,
        "scoring_method_version": SCORING_METHOD_VERSION,
        "threshold_source": "smartops_engineering_adaptation_requires_validation",
    }


FINE_PROFILES = {
    "video_conferencing_online_classes": _profile(
        "video_conferencing_online_classes", "Video conferencing and online classes",
        "browser_or_media", ("zoom.exe", "webexmta.exe", "webex.exe"),
        ("teams.exe", "brave.exe", "chrome.exe", "msedge.exe"),
        (25, 24, 8, 12, 16, 15), description="Live audio/video conferencing and class clients.",
        cpu_high=75, ram_high=82,
    ),
    "word_processing": _profile(
        "word_processing", "Word processing", "office_productivity",
        ("winword.exe", "wordpad.exe", "libreoffice.exe", "soffice.bin"), (),
        (18, 30, 10, 17, 15, 10), description="Creating and editing local text documents.",
        cpu_high=65, ram_high=80,
    ),
    "spreadsheet_analysis": _profile(
        "spreadsheet_analysis", "Spreadsheet analysis", "office_productivity",
        ("excel.exe", "scalc.exe"), (), (28, 30, 10, 12, 12, 8),
        description="Interactive spreadsheet calculation and analysis.", cpu_high=78,
    ),
    "presentation_creation": _profile(
        "presentation_creation", "Presentation creation", "office_productivity",
        ("powerpnt.exe", "simpress.exe"), (), (20, 28, 8, 14, 18, 12),
        description="Creating and presenting slide decks.", cpu_high=70,
    ),
    "email_calendar": _profile(
        "email_calendar", "Email and calendar", "office_productivity",
        ("outlook.exe", "olk.exe", "thunderbird.exe"), (), (16, 32, 10, 14, 16, 12),
        description="Local email and calendar clients.", cpu_high=65,
    ),
    "pdf_document_reading": _profile(
        "pdf_document_reading", "PDF and document reading", "interactive_light",
        ("acrord32.exe", "acrobat.exe", "sumatrapdf.exe", "foxitpdfreader.exe"), (),
        (12, 28, 8, 17, 20, 15), description="Reading PDF and local document formats.", cpu_high=60,
    ),
    "terminal_scripting": _profile(
        "terminal_scripting", "Terminal and scripting", "development",
        ("windowsterminal.exe", "powershell.exe", "pwsh.exe", "cmd.exe"),
        ("python.exe", "node.exe", "bash.exe", "wsl.exe"),
        (30, 23, 10, 12, 15, 10), description="Foreground command shells and script execution.",
    ),
    "software_build_compilation_testing": _profile(
        "software_build_compilation_testing", "Software build, compilation and testing",
        "development", ("msbuild.exe", "cl.exe", "gcc.exe", "g++.exe", "cargo.exe", "pytest.exe"),
        ("code.exe", "devenv.exe", "python.exe", "node.exe", "java.exe"),
        (38, 27, 9, 10, 10, 6), description="Foreground compiler, build or test execution.",
        cpu_high=90, ram_high=90,
    ),
    "data_science_notebooks": _profile(
        "data_science_notebooks", "Data science and notebooks", "development",
        ("jupyter-notebook.exe", "jupyter-lab.exe", "spyder.exe", "rstudio.exe"),
        ("python.exe", "code.exe", "brave.exe", "chrome.exe"),
        (32, 34, 10, 8, 10, 6), description="Local notebooks, statistical tools and data exploration.",
        cpu_high=88, ram_high=92,
    ),
    "graphic_design_photo_editing": _profile(
        "graphic_design_photo_editing", "Graphic design and photo editing", "compute_intensive",
        ("photoshop.exe", "lightroom.exe", "gimp-2.10.exe", "affinityphoto2.exe"), (),
        (27, 28, 8, 10, 17, 10), description="Foreground raster and photo-editing tools.",
        cpu_high=88, ram_high=90,
    ),
    "video_editing_rendering": _profile(
        "video_editing_rendering", "Video editing and rendering", "compute_intensive",
        ("adobe premiere pro.exe", "resolve.exe", "afterfx.exe", "handbrake.exe"), (),
        (34, 28, 8, 12, 10, 8), description="Video timelines, encoding and rendering tools.",
        cpu_high=94, ram_high=92,
    ),
    "audio_production": _profile(
        "audio_production", "Audio production", "compute_intensive",
        ("audacity.exe", "fl64.exe", "ableton live 12 suite.exe", "reaper.exe"), (),
        (24, 27, 8, 10, 23, 8), description="Foreground digital audio workstations and editors.",
        cpu_high=82, ram_high=88,
    ),
    "cad_engineering_3d_modelling": _profile(
        "cad_engineering_3d_modelling", "CAD, engineering and 3D modelling", "gaming_or_3d",
        ("acad.exe", "solidworks.exe", "fusion360.exe", "blender.exe", "maya.exe"), (),
        (33, 28, 8, 10, 13, 8), description="Interactive CAD and 3D authoring applications.",
        cpu_high=92, ram_high=92,
    ),
    "virtual_machines_containers": _profile(
        "virtual_machines_containers", "Virtual machines and containers", "compute_intensive",
        ("vmware.exe", "virtualbox.exe", "docker desktop.exe", "virt-manager.exe"),
        ("vmware-vmx.exe", "vmmem.exe", "vmmemwsl.exe", "com.docker.backend.exe"),
        (28, 38, 10, 9, 9, 6), description="Foreground VM or container management and execution.",
        cpu_high=90, ram_high=94,
    ),
    "compression_backup_large_transfers": _profile(
        "compression_backup_large_transfers", "File compression, backup and large transfers",
        "background_activity", ("7zfm.exe", "7z.exe", "winrar.exe", "robocopy.exe", "freefilesync.exe"), (),
        (27, 18, 8, 25, 12, 10), description="Foreground compression, backup and file-transfer tools.",
        cpu_high=90,
    ),
    "remote_desktop_support": _profile(
        "remote_desktop_support", "Remote desktop and remote support", "interactive_light",
        ("mstsc.exe", "teamviewer.exe", "anydesk.exe", "rustdesk.exe"), (),
        (20, 25, 8, 12, 22, 13), description="Foreground remote desktop and support clients.",
        cpu_high=75,
    ),
    "communication_chat": _profile(
        "communication_chat", "Communication and chat", "interactive_light",
        ("slack.exe", "discord.exe", "teams.exe", "whatsapp.exe", "telegram.exe"), (),
        (16, 34, 8, 12, 18, 12), description="Foreground messaging and collaboration clients.",
        cpu_high=68,
    ),
    "security_scanning_maintenance": _profile(
        "security_scanning_maintenance", "Security scanning and system maintenance",
        "background_activity", ("msascuil.exe", "securityhealthsystray.exe", "mrt.exe", "cleanmgr.exe"),
        ("msmpeng.exe", "trustedinstaller.exe", "tiworker.exe"),
        (30, 22, 10, 18, 12, 8), description="User-visible local security and maintenance tools.",
        cpu_high=90,
    ),
    "local_media_playback": _profile(
        "local_media_playback", "Local media playback", "browser_or_media",
        ("vlc.exe", "mpv.exe", "wmplayer.exe", "moviesandtv.exe"), (),
        (18, 24, 8, 12, 24, 14), description="Playback in a recognised local media application.",
        cpu_high=70,
    ),
    "online_learning_research": _profile(
        "online_learning_research", "Online learning and research", "browser_or_media",
        (), ("brave.exe", "chrome.exe", "msedge.exe", "firefox.exe", "code.exe"),
        (20, 28, 8, 12, 18, 14), description=(
            "Reserved for unambiguous local evidence. Browser content, URLs and titles "
            "are never inspected, so browser activity alone cannot establish this profile."
        ), foreground_required=True,
    ),
}


BROAD_PROFILES = {
    "device": ("Device", "__device__"),
    "gaming_or_3d": ("Gaming/3D", "gaming_or_3d"),
    "development": ("Development", "development"),
    "guided_development": ("Guided Development", "guided_development"),
    "browser_or_media": ("Browser/Media", "browser_or_media"),
    "office_productivity": ("Office Productivity", "office_productivity"),
    "interactive_light": ("Interactive Light", "interactive_light"),
    "compute_intensive": ("Compute Intensive", "compute_intensive"),
    "background_activity": ("Background Activity", "background_activity"),
    "idle": ("Idle", "idle"),
}


def public_catalogue() -> list[dict[str, object]]:
    fine = [
        {
            **profile,
            "profile_kind": "fine_grained",
            "catalogue_state": "catalogued",
            "detectability_state": (
                "independently_detectable"
                if profile["foreground_executables"]
                else "not_independently_detectable"
            ),
            "detectability_explanation": (
                "Recognized foreground executable activity can identify this profile "
                "without inspecting personal content."
                if profile["foreground_executables"]
                else "Browser-based learning cannot currently be distinguished reliably "
                "from general Browser/Media without inspecting content, so this profile "
                "is not independently detected."
            ),
        }
        for profile in FINE_PROFILES.values()
    ]
    broad = [
        {
            "key": key,
            "name": name,
            "parent_workload_profile": parent,
            "description": "Broad workload context from the active personal baseline.",
            "profile_kind": "broad_v2",
            "catalogue_state": "catalogued",
            "detectability_state": (
                "aggregate_detectable" if key == "device" else "broad_context_detectable"
            ),
            "detectability_explanation": (
                "This aggregate uses qualifying observations across recognized workloads."
                if key == "device"
                else "This broad context is supplied by the transparent workload classifier."
            ),
            "taxonomy_version": TAXONOMY_VERSION,
            "detection_rule_version": "phase7b1-workload-v3",
            "scoring_method_version": SCORING_METHOD_VERSION,
            "metrics": {},
        }
        for key, (name, parent) in BROAD_PROFILES.items()
    ]
    return sorted([*fine, *broad], key=lambda item: str(item["name"]))
