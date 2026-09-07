import { describe, expect, it } from "vitest";

import {
  ALL_NAVIGATION_ITEMS,
  NAVIGATION_ITEMS,
  UTILITY_NAVIGATION_ITEMS,
  alertDeepLinkFromHash,
  routeFromHash,
  routeHref,
} from "./navigation";

describe("SmartOps navigation", () => {
  it("uses Overview for an empty or unknown route", () => {
    expect(routeFromHash("")).toBe("overview");
    expect(routeFromHash("#/does-not-exist")).toBe("overview");
  });

  it("round-trips every menu route through a safe hash", () => {
    for (const item of ALL_NAVIGATION_ITEMS) {
      expect(routeFromHash(routeHref(item.route))).toBe(item.route);
    }
  });

  it("keeps optional Research & Validation last", () => {
    expect(NAVIGATION_ITEMS.at(-1)).toMatchObject({
      route: "research-validation",
      optional: true,
    });
  });

  it("keeps Settings as a separated utility route", () => {
    expect(UTILITY_NAVIGATION_ITEMS).toEqual([
      expect.objectContaining({ route: "settings", label: "Settings" }),
    ]);
    expect(NAVIGATION_ITEMS.some((item) => item.route === "settings")).toBe(false);
    expect(routeFromHash("#/settings")).toBe("settings");
  });

  it("parses only a positive integer alert deep link", () => {
    expect(
      alertDeepLinkFromHash("#/predictive-alerts?alertId=27"),
    ).toEqual({ present: true, alertId: 27, error: null });
    expect(
      alertDeepLinkFromHash("#/predictive-alerts?alertId=1%26route%3Devil"),
    ).toMatchObject({ present: true, alertId: null });
    expect(alertDeepLinkFromHash("#/predictive-alerts")).toEqual({
      present: false,
      alertId: null,
      error: null,
    });
  });
});
