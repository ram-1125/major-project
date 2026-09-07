import { describe, expect, it } from "vitest";

import { NAVIGATION_SEARCH_CATALOGUE, PC_QUALITY_SEARCH_PROFILES, isStableQualityProfile, searchNavigation } from "./searchCatalogue";

describe("local navigation search catalogue", () => {
  it("contains every page and all 30 stable PC Quality profile IDs", () => {
    expect(NAVIGATION_SEARCH_CATALOGUE.filter((item) => item.group === "Pages" && item.id.startsWith("page-"))).toHaveLength(8);
    expect(PC_QUALITY_SEARCH_PROFILES).toHaveLength(30);
    expect(new Set(PC_QUALITY_SEARCH_PROFILES.map(([id]) => id)).size).toBe(30);
  });

  it("returns genuine page, setting, feature and profile destinations", () => {
    expect(searchNavigation("notifications")).toEqual(expect.arrayContaining([expect.objectContaining({ href: "#/settings?section=notifications" })]));
    expect(searchNavigation("pipeline")).toEqual(expect.arrayContaining([expect.objectContaining({ href: "#/overview" })]));
    expect(searchNavigation("video editing")).toEqual(expect.arrayContaining([expect.objectContaining({ href: "#/pc-quality-check?profile=video_editing_rendering" })]));
  });

  it("accepts only stable public profile identifiers", () => {
    expect(isStableQualityProfile("guided_development")).toBe(true);
    expect(isStableQualityProfile("../../../private")).toBe(false);
    expect(isStableQualityProfile(null)).toBe(false);
  });
});
