import { describe, expect, it } from "vitest";

import {
  DASHBOARD_PREFERENCES_KEY,
  DEFAULT_DASHBOARD_PREFERENCES,
  LAST_OPENED_PAGE_KEY,
  initialRoute,
  loadDashboardPreferences,
  loadLastOpenedPage,
  saveDashboardPreferences,
  validateDashboardPreferences,
} from "./preferences";

function memoryStorage(initial: Record<string, string> = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => {
      values.set(key, value);
    },
  };
}

describe("dashboard preferences", () => {
  it("validates untrusted stored values field by field", () => {
    expect(
      validateDashboardPreferences({
        defaultLandingPage: "external-page",
        rememberLastPage: "yes",
        timeDisplay: "military",
        relativeTimestamps: true,
        automaticRefresh: false,
      }),
    ).toEqual({
      ...DEFAULT_DASHBOARD_PREFERENCES,
      relativeTimestamps: true,
      automaticRefresh: false,
    });
  });

  it("falls back safely for malformed localStorage JSON", () => {
    const storage = memoryStorage({
      [DASHBOARD_PREFERENCES_KEY]: "{not-json",
    });
    expect(loadDashboardPreferences(storage)).toEqual(
      DEFAULT_DASHBOARD_PREFERENCES,
    );
  });

  it("persists only validated browser display preferences", () => {
    const storage = memoryStorage();
    const saved = saveDashboardPreferences(
      {
        defaultLandingPage: "settings",
        rememberLastPage: true,
        timeDisplay: "24-hour",
        relativeTimestamps: true,
        automaticRefresh: false,
      },
      storage,
    );
    expect(saved.defaultLandingPage).toBe("settings");
    expect(loadDashboardPreferences(storage)).toEqual(saved);
  });

  it("uses explicit, remembered, then default routes in that order", () => {
    const storage = memoryStorage({ [LAST_OPENED_PAGE_KEY]: "system-health" });
    const preferences = {
      ...DEFAULT_DASHBOARD_PREFERENCES,
      rememberLastPage: true,
      defaultLandingPage: "live-monitoring" as const,
    };
    expect(initialRoute("#/settings", preferences, storage)).toBe("settings");
    expect(initialRoute("", preferences, storage)).toBe("system-health");
    expect(loadLastOpenedPage(storage)).toBe("system-health");
  });
});
