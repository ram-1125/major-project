import { describe, expect, it } from "vitest";

import {
  effectiveWeightTotal,
  evidenceIndependenceLabel,
  formatEffectiveWeight,
  formatHeadroomScore,
  weightedDeduction,
} from "./profileQuality";

describe("PC Quality headroom presentation", () => {
  it("keeps genuine score precision without turning 99.9 into 100", () => {
    expect(formatHeadroomScore(100)).toBe("100.0");
    expect(formatHeadroomScore(99.9171)).toBe("99.9");
    expect(formatHeadroomScore(99.9205)).toBe("99.9");
    expect(formatHeadroomScore(94.191)).toBe("94.2");
    expect(formatHeadroomScore(null)).toBe("Not evaluated");
  });

  it("converts fractional effective weights to percentages", () => {
    expect(formatEffectiveWeight(0.25)).toBe("25.0%");
    expect(formatEffectiveWeight(0.18)).toBe("18.0%");
    expect(formatEffectiveWeight(0.10)).toBe("10.0%");
    expect(formatEffectiveWeight(0.04)).toBe("4.0%");
    expect(effectiveWeightTotal([0.25, 0.25, 0.1, 0.18, 0.12, 0.06, 0.04]))
      .toBeCloseTo(100);
  });

  it("reconstructs weighted deductions and evidence independence labels", () => {
    expect(weightedDeduction(0.3, 80.6365)).toBeCloseTo(5.80905);
    expect(weightedDeduction(0.25, null)).toBeNull();
    expect(evidenceIndependenceLabel("baseline_training_evidence"))
      .toBe("Baseline training evidence");
    expect(evidenceIndependenceLabel("independent_post_calibration_evidence"))
      .toBe("Independent post-calibration evidence");
  });
});
