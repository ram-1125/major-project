export type OverviewHealth = {
  id: number;
  assessed_at_utc: string;
  system_health_score: number | null;
  health_band: string;
  evaluation_state: string;
};

export type OverviewQualityProfile = {
  key: string;
  name: string;
  evaluation_state: string;
  assessment: null | {
    profile_quality_score: number | null;
    assessed_at_utc: string;
  };
};

export type OverviewFeature = {
  id: number;
  window_start_utc: string;
  window_end_utc: string;
  dominant_workload_class: string | null;
  secondary_workload_context?: string | null;
};

export type OverviewPipelineStage = {
  stage_key: string;
  current_state: string;
  last_attempted_at_utc: string | null;
  last_successful_at_utc: string | null;
};

export type OverviewAlert = {
  id: number;
  title: string;
  current_severity: string;
  latest_observed_utc: string;
};

export type HealthTrendPoint = {
  timestamp_utc: string;
  score: number | null;
  health_band: string;
  evaluation_state: string;
};

export type WorkloadHeatmapSlot = {
  timestamp_utc: string;
  feature: OverviewFeature | null;
  workload: string | null;
};

export type RecentActivityItem = {
  key: string;
  kind: "collection" | "analysis" | "risk" | "health" | "alert" | "baseline";
  description: string;
  timestamp_utc: string;
  href: string;
  tone: "success" | "information" | "warning" | "critical";
};

export const CANONICAL_SEVERITIES = [
  "informational",
  "advisory",
  "warning",
  "urgent",
] as const;

export function healthTone(
  band: string | null | undefined,
): "success" | "information" | "warning" | "critical" | "neutral" {
  if (band === "good") return "success";
  if (band === "stable") return "information";
  if (band === "attention") return "warning";
  if (band === "degraded" || band === "critical_condition") return "critical";
  return "neutral";
}

export function pipelineTone(
  state: string,
): "success" | "processing" | "warning" | "critical" | "neutral" {
  if (state === "successfully_waiting") return "success";
  if (state === "processing") return "processing";
  if (state === "failed" || state === "overdue") return "critical";
  if (state === "delayed" || state === "recoverable") return "warning";
  return "neutral";
}

/** Preserve not-evaluated points as null so line paths contain visible gaps. */
export function buildHealthTrend(
  assessments: OverviewHealth[],
): HealthTrendPoint[] {
  return [...assessments]
    .sort(
      (left, right) =>
        new Date(left.assessed_at_utc).getTime()
        - new Date(right.assessed_at_utc).getTime(),
    )
    .map((assessment) => ({
      timestamp_utc: assessment.assessed_at_utc,
      score:
        assessment.evaluation_state === "not_evaluated"
          ? null
          : assessment.system_health_score,
      health_band: assessment.health_band,
      evaluation_state: assessment.evaluation_state,
    }));
}

/**
 * Keep the comparison compact and deterministic: device/current context,
 * newest assessment, and genuine highest/lowest evaluated values.
 */
export function selectQualityProfiles(
  profiles: OverviewQualityProfile[],
  preferredKey = "device",
  maximum = 6,
): OverviewQualityProfile[] {
  const evaluated = profiles.filter(
    (profile) =>
      profile.evaluation_state === "assessed"
      && profile.assessment?.profile_quality_score != null,
  );
  const selected = new Map<string, OverviewQualityProfile>();
  const add = (profile: OverviewQualityProfile | undefined) => {
    if (profile) selected.set(profile.key, profile);
  };
  add(evaluated.find((profile) => profile.key === preferredKey));
  add(
    [...evaluated].sort(
      (left, right) =>
        new Date(right.assessment!.assessed_at_utc).getTime()
        - new Date(left.assessment!.assessed_at_utc).getTime(),
    )[0],
  );
  const byScore = [...evaluated].sort(
    (left, right) =>
      left.assessment!.profile_quality_score!
      - right.assessment!.profile_quality_score!,
  );
  add(byScore[0]);
  add(byScore[1]);
  add(byScore.at(-1));
  add(byScore.at(-2));
  return [...selected.values()].slice(0, maximum);
}

function fiveMinuteKey(value: string): number {
  return Math.floor(new Date(value).getTime() / 300_000) * 300_000;
}

/** Build an explicit 24-hour grid; a missing slot stays null rather than idle. */
export function buildWorkloadHeatmap(
  features: OverviewFeature[],
  maximumSlots = 288,
): WorkloadHeatmapSlot[] {
  if (features.length === 0) return [];
  const map = new Map<number, OverviewFeature>();
  for (const feature of features) {
    map.set(fiveMinuteKey(feature.window_start_utc), feature);
  }
  const earliest = Math.min(...features.map((item) => fiveMinuteKey(item.window_start_utc)));
  const latest = Math.max(...features.map((item) => fiveMinuteKey(item.window_start_utc)));
  const first = Math.max(earliest, latest - (maximumSlots - 1) * 300_000);
  const slotCount = Math.min(maximumSlots, Math.floor((latest - first) / 300_000) + 1);
  return Array.from({ length: slotCount }, (_, index) => {
    const timestamp = first + index * 300_000;
    const feature = map.get(timestamp) ?? null;
    return {
      timestamp_utc: new Date(timestamp).toISOString(),
      feature,
      workload:
        feature?.secondary_workload_context
        ?? feature?.dominant_workload_class
        ?? null,
    };
  });
}

const PIPELINE_ACTIVITY: Record<
  string,
  Pick<RecentActivityItem, "kind" | "description" | "href">
> = {
  telemetry_collection: {
    kind: "collection",
    description: "System data collected",
    href: "#/live-monitoring",
  },
  feature_window_generation: {
    kind: "analysis",
    description: "Five-minute analysis completed",
    href: "#/live-monitoring",
  },
  active_baseline: {
    kind: "baseline",
    description: "Personal baseline verified",
    href: "#/settings?section=monitoring-data",
  },
  risk_evaluation: {
    kind: "risk",
    description: "Risk analysis completed",
    href: "#/root-cause-analysis",
  },
  health_evaluation: {
    kind: "health",
    description: "Health analysis completed",
    href: "#/system-health",
  },
  predictive_alert_evaluation: {
    kind: "alert",
    description: "Predictive-alert evaluation completed",
    href: "#/predictive-alerts",
  },
};

/** Project already-fetched genuine timestamps; this never creates activity. */
export function buildRecentActivity(
  stages: OverviewPipelineStage[],
  alerts: OverviewAlert[],
  latestMetricTimestamp: string | null,
  maximum = 8,
): RecentActivityItem[] {
  const activity: RecentActivityItem[] = [];
  for (const stage of stages) {
    const definition = PIPELINE_ACTIVITY[stage.stage_key];
    const timestamp = stage.last_successful_at_utc;
    if (!definition || !timestamp) continue;
    activity.push({
      key: `pipeline-${stage.stage_key}-${timestamp}`,
      ...definition,
      timestamp_utc: timestamp,
      tone: stage.current_state === "failed" || stage.current_state === "overdue"
        ? "critical"
        : "success",
    });
  }
  if (latestMetricTimestamp && !activity.some((item) => item.kind === "collection")) {
    activity.push({
      key: `metric-${latestMetricTimestamp}`,
      kind: "collection",
      description: "System data collected",
      timestamp_utc: latestMetricTimestamp,
      href: "#/live-monitoring",
      tone: "success",
    });
  }
  for (const alert of alerts) {
    activity.push({
      key: `alert-${alert.id}-${alert.latest_observed_utc}`,
      kind: "alert",
      description: `Alert observed: ${alert.title}`,
      timestamp_utc: alert.latest_observed_utc,
      href: `#/predictive-alerts?alertId=${encodeURIComponent(alert.id)}`,
      tone:
        alert.current_severity === "urgent"
          ? "critical"
          : alert.current_severity === "warning"
            ? "warning"
            : "information",
    });
  }
  return activity
    .sort(
      (left, right) =>
        new Date(right.timestamp_utc).getTime()
        - new Date(left.timestamp_utc).getTime(),
    )
    .slice(0, maximum);
}
