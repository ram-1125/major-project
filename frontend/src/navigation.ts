export type AppRoute =
  | "overview"
  | "live-monitoring"
  | "predictive-alerts"
  | "root-cause-analysis"
  | "system-health"
  | "pc-quality-check"
  | "research-validation"
  | "settings";

export type NavigationGroup = "monitoring" | "insights" | "application";

export type NavigationItem = {
  route: AppRoute;
  label: string;
  shortLabel: string;
  description: string;
  group: NavigationGroup;
  optional?: boolean;
};

export const NAVIGATION_ITEMS: NavigationItem[] = [
  { route: "overview", label: "Overview", shortLabel: "OV", description: "Current operating picture and automatic pipeline status", group: "monitoring" },
  { route: "live-monitoring", label: "Live Monitoring", shortLabel: "LM", description: "Live PC telemetry, enhanced evidence and historical data", group: "monitoring" },
  { route: "predictive-alerts", label: "Predictive Alerts", shortLabel: "PA", description: "Evidence-supported alerts and lifecycle details", group: "monitoring" },
  { route: "root-cause-analysis", label: "Root-Cause Analysis", shortLabel: "RC", description: "Alert-specific contributing evidence and diagnostic checks", group: "monitoring" },
  { route: "system-health", label: "System Health", shortLabel: "SH", description: "Current operating condition and explainable health deductions", group: "insights" },
  { route: "pc-quality-check", label: "PC Quality Check", shortLabel: "PQ", description: "Current workload headroom and separate hardware suitability", group: "insights" },
  { route: "research-validation", label: "Research & Validation", shortLabel: "RV", description: "Optional labelled outcomes and method validation", group: "insights", optional: true },
];

export const UTILITY_NAVIGATION_ITEMS: NavigationItem[] = [
  { route: "settings", label: "Settings", shortLabel: "ST", description: "Application preferences, notifications and personal baseline", group: "application" },
];

export const ALL_NAVIGATION_ITEMS = [...NAVIGATION_ITEMS, ...UTILITY_NAVIGATION_ITEMS];

export const NAVIGATION_GROUP_LABELS: Record<NavigationGroup, string> = {
  monitoring: "Monitoring",
  insights: "Insights",
  application: "Application",
};

const ROUTES = new Set<AppRoute>(ALL_NAVIGATION_ITEMS.map((item) => item.route));

export function isAppRoute(value: string): value is AppRoute {
  return ROUTES.has(value as AppRoute);
}

export function routeFromHash(hash: string): AppRoute {
  const candidate = hash.replace(/^#\/?/, "").split(/[/?]/, 1)[0];
  return isAppRoute(candidate) ? candidate : "overview";
}

export function routeHref(route: AppRoute): string {
  return `#/${route}`;
}

export type AlertDeepLink = { present: boolean; alertId: number | null; error: string | null };

export function alertDeepLinkFromHash(hash: string): AlertDeepLink {
  const query = hash.split("?", 2)[1];
  if (query === undefined) return { present: false, alertId: null, error: null };
  const rawValue = new URLSearchParams(query).get("alertId");
  if (rawValue === null) return { present: false, alertId: null, error: null };
  if (!/^[1-9]\d*$/.test(rawValue)) return { present: true, alertId: null, error: "The notification link contains an invalid alert identifier." };
  const alertId = Number(rawValue);
  if (!Number.isSafeInteger(alertId)) return { present: true, alertId: null, error: "The notification link contains an invalid alert identifier." };
  return { present: true, alertId, error: null };
}
