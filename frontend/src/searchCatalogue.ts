import { ALL_NAVIGATION_ITEMS, routeHref, type AppRoute } from "./navigation";

export type SearchGroup = "Pages" | "Settings" | "PC Quality Profiles";
export type NavigationSearchItem = { id: string; group: SearchGroup; label: string; description: string; href: string; keywords: string[]; route: AppRoute };

const SETTINGS_SECTIONS = [
  ["general", "General", "Default landing page and remember last opened page"],
  ["dashboard", "Dashboard", "Time display, relative timestamps and automatic refresh"],
  ["notifications", "Notifications", "Windows notifications, severity preferences and test notification"],
  ["personal-baseline", "Personal Baseline", "Active baseline, rollback and optional calibration"],
  ["monitoring-data", "Monitoring & Data", "Collection interval, feature windows and latest collection"],
  ["privacy", "Privacy", "Local-only processing and data boundaries"],
  ["system-information", "System Information", "Schema, database integrity and API details"],
] as const;

// Stable identifiers from the local public PC Quality taxonomy. Search never
// inspects personal activity, browser content, documents, or external data.
export const PC_QUALITY_SEARCH_PROFILES = [
  ["device", "Device"], ["audio_production", "Audio production"], ["background_activity", "Background Activity"],
  ["browser_or_media", "Browser/Media"], ["cad_engineering_3d_modelling", "CAD, engineering and 3D modelling"],
  ["communication_chat", "Communication and chat"], ["compression_backup_large_transfers", "File compression, backup and large transfers"],
  ["compute_intensive", "Compute Intensive"], ["data_science_notebooks", "Data science and notebooks"], ["development", "Development"],
  ["email_calendar", "Email and calendar"], ["gaming_or_3d", "Gaming/3D"], ["graphic_design_photo_editing", "Graphic design and photo editing"],
  ["guided_development", "Guided Development"], ["idle", "Idle"], ["interactive_light", "Interactive Light"],
  ["local_media_playback", "Local media playback"], ["office_productivity", "Office Productivity"], ["online_learning_research", "Online learning and research"],
  ["pdf_document_reading", "PDF and document reading"], ["presentation_creation", "Presentation creation"], ["remote_desktop_support", "Remote desktop and remote support"],
  ["security_scanning_maintenance", "Security scanning and system maintenance"], ["software_build_compilation_testing", "Software build, compilation and testing"],
  ["spreadsheet_analysis", "Spreadsheet analysis"], ["terminal_scripting", "Terminal and scripting"], ["video_conferencing_online_classes", "Video conferencing and online classes"],
  ["video_editing_rendering", "Video editing and rendering"], ["virtual_machines_containers", "Virtual machines and containers"], ["word_processing", "Word processing"],
] as const;

const FEATURE_RESULTS: NavigationSearchItem[] = [
  { id: "feature-pipeline", group: "Pages", label: "Automatic pipeline", description: "System collection, five-minute analysis, baseline, risk, health and alerts", href: routeHref("overview"), keywords: ["pipeline", "collection", "five-minute", "baseline", "risk"], route: "overview" },
  { id: "feature-calibration", group: "Settings", label: "Optional calibration", description: "Start New Calibration and baseline lifecycle controls", href: "#/settings?section=personal-baseline", keywords: ["calibration", "recalibration", "baseline", "training"], route: "settings" },
];

export const NAVIGATION_SEARCH_CATALOGUE: NavigationSearchItem[] = [
  ...ALL_NAVIGATION_ITEMS.map((item) => ({ id: `page-${item.route}`, group: "Pages" as const, label: item.label, description: item.description, href: routeHref(item.route), keywords: [item.route, item.label, item.description], route: item.route })),
  ...FEATURE_RESULTS,
  ...SETTINGS_SECTIONS.map(([id, label, description]) => ({ id: `settings-${id}`, group: "Settings" as const, label, description, href: `#/settings?section=${id}`, keywords: [id, label, description, "settings"], route: "settings" as const })),
  ...PC_QUALITY_SEARCH_PROFILES.map(([id, label]) => ({ id: `profile-${id}`, group: "PC Quality Profiles" as const, label, description: "Open this stable PC Quality profile", href: `#/pc-quality-check?profile=${encodeURIComponent(id)}`, keywords: [id, label, "quality", "profile", "workload"], route: "pc-quality-check" as const })),
];

function normalized(value: string): string { return value.trim().toLocaleLowerCase(); }

export function searchNavigation(query: string): NavigationSearchItem[] {
  const needle = normalized(query);
  if (!needle) return NAVIGATION_SEARCH_CATALOGUE.filter((item) => item.group === "Pages");
  return NAVIGATION_SEARCH_CATALOGUE.filter((item) => [item.label, item.description, ...item.keywords].some((value) => normalized(value).includes(needle)));
}

export function isStableQualityProfile(value: string | null): boolean {
  return value !== null && PC_QUALITY_SEARCH_PROFILES.some(([id]) => id === value);
}
