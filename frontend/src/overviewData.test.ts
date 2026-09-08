import { describe, expect, it } from "vitest";

import {
  buildHealthTrend,
  buildRecentActivity,
  buildWorkloadHeatmap,
  healthTone,
  last24HourFeatureRange,
  pipelineTone,
  selectQualityProfiles,
} from "./overviewData";

describe("Overview genuine-data shaping", () => {
  it("keeps not-evaluated health periods as null gaps", () => {
    const points = buildHealthTrend([
      { id: 2, assessed_at_utc: "2026-09-03T10:05:00Z", system_health_score: null, health_band: "not_evaluated", evaluation_state: "not_evaluated" },
      { id: 1, assessed_at_utc: "2026-09-03T10:00:00Z", system_health_score: 92, health_band: "good", evaluation_state: "established" },
    ]);
    expect(points.map((item) => item.score)).toEqual([92, null]);
    expect(points[0].timestamp_utc).toBe("2026-09-03T10:00:00Z");
  });

  it("excludes not-observed and not-evaluated PC Quality profiles instead of plotting zero", () => {
    const selected = selectQualityProfiles([
      { key: "device", name: "Device", evaluation_state: "assessed", assessment: { profile_quality_score: 88, assessed_at_utc: "2026-09-03T10:00:00Z" } },
      { key: "missing", name: "Missing", evaluation_state: "not_evaluated", assessment: null },
      { key: "low", name: "Low", evaluation_state: "assessed", assessment: { profile_quality_score: 62, assessed_at_utc: "2026-09-03T09:00:00Z" } },
      { key: "high", name: "High", evaluation_state: "assessed", assessment: { profile_quality_score: 96, assessed_at_utc: "2026-09-03T08:00:00Z" } },
    ]);
    expect(selected.map((item) => item.key)).toEqual(expect.arrayContaining(["device", "low", "high"]));
    expect(selected.some((item) => item.key === "missing")).toBe(false);
  });

  it("leaves missing workload periods empty and preserves stored classes", () => {
    const slots = buildWorkloadHeatmap([
      { id: 1, window_start_utc: "2026-09-03T10:00:00Z", window_end_utc: "2026-09-03T10:05:00Z", dominant_workload_class: "development" },
      { id: 2, window_start_utc: "2026-09-03T10:10:00Z", window_end_utc: "2026-09-03T10:15:00Z", dominant_workload_class: "browser_media", secondary_workload_context: "guided_development" },
    ], 3);
    expect(slots.map((slot) => slot.workload)).toEqual(["development", null, "guided_development"]);
    expect(slots[1].feature).toBeNull();
    expect(slots[1].state).toBe("missing");
  });

  it("uses stable workload identifiers and keeps an exact chronological 24-hour grid", () => {
    const end = "2026-09-08T16:03:12.000Z";
    const slots = buildWorkloadHeatmap([
      { id: 9, window_start_utc: "2026-09-08T15:55:00Z", window_end_utc: "2026-09-08T16:00:00Z", dominant_workload_class: "gaming_or_3d" },
      { id: 8, window_start_utc: "2026-09-08T15:50:00Z", window_end_utc: "2026-09-08T15:55:00Z", dominant_workload_class: "background_activity" },
    ], 288, end);
    expect(slots).toHaveLength(288);
    expect(slots.at(-2)?.workload).toBe("background_activity");
    expect(slots.at(-1)?.workload).toBe("gaming_or_3d");
    expect(slots[0].timestamp_utc < slots.at(-1)!.timestamp_utc).toBe(true);
    expect(slots[0].state).toBe("missing");
    expect(last24HourFeatureRange(new Date(end))).toEqual({
      start: "2026-09-07T16:00:00.000Z",
      end: "2026-09-08T16:00:00.000Z",
    });
  });

  it("distinguishes a stored not-evaluated period from a missing period", () => {
    const slots = buildWorkloadHeatmap([{
      id: 10,
      window_start_utc: "2026-09-08T15:55:00Z",
      window_end_utc: "2026-09-08T16:00:00Z",
      dominant_workload_class: null,
    }], 2, "2026-09-08T16:03:12.000Z");
    expect(slots[0].state).toBe("missing");
    expect(slots[1].state).toBe("not_evaluated");
  });

  it("projects recent activity from genuine source timestamps only", () => {
    const activity = buildRecentActivity([
      { stage_key: "health_evaluation", current_state: "successfully_waiting", last_attempted_at_utc: "2026-09-03T10:05:00Z", last_successful_at_utc: "2026-09-03T10:05:00Z" },
    ], [{ id: 3, title: "Memory pressure", current_severity: "warning", latest_observed_utc: "2026-09-03T10:06:00Z" }], "2026-09-03T10:05:30Z");
    expect(activity[0].description).toBe("Alert observed: Memory pressure");
    expect(activity.map((item) => item.description)).toContain("System data collected");
    expect(activity.map((item) => item.description)).toContain("Health analysis completed");
  });

  it("maps pipeline and health states to explicit semantic tones", () => {
    expect(pipelineTone("processing")).toBe("processing");
    expect(pipelineTone("overdue")).toBe("critical");
    expect(pipelineTone("not_yet_executed")).toBe("neutral");
    expect(healthTone("not_evaluated")).toBe("neutral");
    expect(healthTone("good")).toBe("success");
  });
});

