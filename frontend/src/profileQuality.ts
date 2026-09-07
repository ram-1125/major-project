export const CURRENT_HEADROOM_MEANING =
  "This score describes available operating headroom during the most recent qualifying observed workload period. It is not benchmark performance, hardware capability, prediction accuracy or a guarantee of future performance.";

export const FULL_HEADROOM_EXPLANATION =
  "All measured factors in this observed period remained within their expected ranges.";

export function formatHeadroomScore(value: number | null | undefined): string {
  return value == null ? "Not evaluated" : value.toFixed(1);
}

export function formatEffectiveWeight(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

export function effectiveWeightTotal(values: number[]): number {
  return values.reduce((total, value) => total + value * 100, 0);
}

export function weightedDeduction(
  effectiveWeight: number,
  metricScore: number | null,
): number | null {
  return metricScore == null
    ? null
    : effectiveWeight * Math.max(0, 100 - metricScore);
}

export function evidenceIndependenceLabel(value: string | null | undefined): string {
  if (value === "baseline_training_evidence") return "Baseline training evidence";
  if (value === "independent_post_calibration_evidence") {
    return "Independent post-calibration evidence";
  }
  return "Historical evidence — independence unavailable";
}

export function detectabilityLabel(value: string): string {
  if (value === "not_independently_detectable") return "Not independently detectable";
  if (value === "independently_detectable") return "Independently detectable";
  if (value === "aggregate_detectable") return "Aggregate evidence";
  return "Broad-context detectable";
}

