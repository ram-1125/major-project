import { isAppRoute, routeFromHash, type AppRoute } from "./navigation";

export type TimeDisplay = "12-hour" | "24-hour";

export type DashboardPreferences = {
  defaultLandingPage: AppRoute;
  rememberLastPage: boolean;
  timeDisplay: TimeDisplay;
  relativeTimestamps: boolean;
  automaticRefresh: boolean;
};

export const DASHBOARD_PREFERENCES_KEY =
  "smartops.dashboard.preferences.v1";
export const LAST_OPENED_PAGE_KEY = "smartops.dashboard.last-page.v1";

export const DEFAULT_DASHBOARD_PREFERENCES: DashboardPreferences = {
  defaultLandingPage: "overview",
  rememberLastPage: false,
  timeDisplay: "12-hour",
  relativeTimestamps: false,
  automaticRefresh: true,
};

type BrowserStorage = Pick<Storage, "getItem" | "setItem">;

function safeGet(storage: BrowserStorage, key: string): string | null {
  try {
    return storage.getItem(key);
  } catch {
    return null;
  }
}

function safeSet(storage: BrowserStorage, key: string, value: string): void {
  try {
    storage.setItem(key, value);
  } catch {
    // Display preferences are helpful but must never break the dashboard.
  }
}

export function validateDashboardPreferences(
  candidate: unknown,
): DashboardPreferences {
  if (candidate === null || typeof candidate !== "object") {
    return { ...DEFAULT_DASHBOARD_PREFERENCES };
  }
  const values = candidate as Record<string, unknown>;
  return {
    defaultLandingPage:
      typeof values.defaultLandingPage === "string"
      && isAppRoute(values.defaultLandingPage)
        ? values.defaultLandingPage
        : DEFAULT_DASHBOARD_PREFERENCES.defaultLandingPage,
    rememberLastPage:
      typeof values.rememberLastPage === "boolean"
        ? values.rememberLastPage
        : DEFAULT_DASHBOARD_PREFERENCES.rememberLastPage,
    timeDisplay:
      values.timeDisplay === "12-hour" || values.timeDisplay === "24-hour"
        ? values.timeDisplay
        : DEFAULT_DASHBOARD_PREFERENCES.timeDisplay,
    relativeTimestamps:
      typeof values.relativeTimestamps === "boolean"
        ? values.relativeTimestamps
        : DEFAULT_DASHBOARD_PREFERENCES.relativeTimestamps,
    automaticRefresh:
      typeof values.automaticRefresh === "boolean"
        ? values.automaticRefresh
        : DEFAULT_DASHBOARD_PREFERENCES.automaticRefresh,
  };
}

export function loadDashboardPreferences(
  storage: BrowserStorage = window.localStorage,
): DashboardPreferences {
  const raw = safeGet(storage, DASHBOARD_PREFERENCES_KEY);
  if (raw === null) return { ...DEFAULT_DASHBOARD_PREFERENCES };
  try {
    return validateDashboardPreferences(JSON.parse(raw));
  } catch {
    return { ...DEFAULT_DASHBOARD_PREFERENCES };
  }
}

export function saveDashboardPreferences(
  preferences: DashboardPreferences,
  storage: BrowserStorage = window.localStorage,
): DashboardPreferences {
  const validated = validateDashboardPreferences(preferences);
  safeSet(storage, DASHBOARD_PREFERENCES_KEY, JSON.stringify(validated));
  return validated;
}

export function loadLastOpenedPage(
  storage: BrowserStorage = window.localStorage,
): AppRoute | null {
  const value = safeGet(storage, LAST_OPENED_PAGE_KEY);
  return value !== null && isAppRoute(value) ? value : null;
}

export function saveLastOpenedPage(
  route: AppRoute,
  storage: BrowserStorage = window.localStorage,
): void {
  safeSet(storage, LAST_OPENED_PAGE_KEY, route);
}

export function initialRoute(
  hash: string,
  preferences: DashboardPreferences,
  storage: BrowserStorage = window.localStorage,
): AppRoute {
  const candidate = hash.replace(/^#\/?/, "").split(/[/?]/, 1)[0];
  if (candidate && isAppRoute(candidate)) return routeFromHash(hash);
  if (preferences.rememberLastPage) {
    const remembered = loadLastOpenedPage(storage);
    if (remembered !== null) return remembered;
  }
  return preferences.defaultLandingPage;
}

export function formatPreferenceTimestamp(
  value: string,
  preferences: DashboardPreferences,
  now = Date.now(),
): string {
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) return "Unavailable";

  if (preferences.relativeTimestamps) {
    const differenceSeconds = Math.round((timestamp - now) / 1000);
    const absolute = Math.abs(differenceSeconds);
    const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
    if (absolute < 60) return formatter.format(differenceSeconds, "second");
    if (absolute < 3_600) {
      return formatter.format(Math.round(differenceSeconds / 60), "minute");
    }
    if (absolute < 86_400) {
      return formatter.format(Math.round(differenceSeconds / 3_600), "hour");
    }
    return formatter.format(Math.round(differenceSeconds / 86_400), "day");
  }

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "medium",
    hour12: preferences.timeDisplay === "12-hour",
  }).format(timestamp);
}
