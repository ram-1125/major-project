import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SystemHealthDashboard, type HealthRecord } from "./SystemHealthDashboard";

const health: HealthRecord = {
  id: 19,
  feature_window_id: 501,
  window_start_utc: "2026-09-04T10:00:00Z",
  window_end_utc: "2026-09-04T10:05:00Z",
  assessed_at_utc: "2026-09-04T10:06:00Z",
  system_health_score: 82.4,
  health_band: "stable",
  evaluation_state: "established",
  data_confidence: 91.5,
  coverage_ratio: 1,
  workload_context: "development",
  workload_confidence: 0.9,
  available_component_weight: 100,
  excluded_component_weight: 0,
  normalization_method: "weighted_mean",
  algorithm_version: "system-health-v1",
  configuration_version: "phase4a-v1",
  reason_codes: ["ram_pressure_observed"],
  first_observed_utc: "2026-09-04T10:00:00Z",
  most_recent_observed_utc: "2026-09-04T10:05:00Z",
  consecutive_window_count: 2,
  persistence_duration_seconds: 600,
  trend_direction: "stable",
  recovery_state: "not_recovering",
  components: [{ id: 1, component_name: "resource_condition", component_score: 80, configured_weight: 45, effective_weight: 0.45, available_subcomponent_weight: 100, excluded_subcomponent_weight: 0, raw_deduction_total: 20, effective_deduction_total: 20, data_quality_status: "sufficient", reason_codes: ["ram_pressure_observed"], details: {} }],
  deductions: [{ id: 3, component_name: "resource_condition", contribution_group: "memory_and_swap", signal_name: "ram_pressure", raw_deduction: 12, effective_deduction: 12, maximum_deduction: 70, correlation_or_cap_reason: "combined_cap", reason_code: "ram_pressure_observed", explanation: "RAM pressure reduced the component.", supporting_value: { average: 87.3, slope: 0.2 }, supporting_event_ids: [], workload_context: "development" }],
  inputs: [],
  excluded_inputs: [{ input_name: "cpu_temperature_avg", input_category: "optional_sensor", availability_status: "unavailable_optional", observed_value: null, excluded_reason: "sensor_unavailable", applicable_weight: 10 }],
  guidance: { explanations: [], recommendations: [{ guidance_type: "recommendation", related_component: "memory", sequence: 1, guidance_text: "Review top memory processes." }], improvements: [], limitations: [{ guidance_type: "limitation", related_component: "", sequence: 1, guidance_text: "This is not a future reliability guarantee." }] },
  baseline_reference: { id: 2 },
  deviation_reference: { id: 10 },
  risk_reference: { id: 11 },
  score_reconstruction: 82.4,
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.location.hash = "";
});

function renderHealth(latest: HealthRecord | null, history: HealthRecord[] = latest ? [latest] : [], reasons: string[] = []) {
  return render(<SystemHealthDashboard
    latest={latest}
    history={history}
    interpretation="System Health Score summarizes the current observed operating condition."
    statusReasons={reasons}
    range="1h"
    onRangeChange={vi.fn()}
    workload="all"
    onWorkloadChange={vi.fn()}
    formatTimestamp={(value) => value}
    apiBaseUrl="http://127.0.0.1:8000"
    liveState="live"
    stale={false}
    alerts={[{ id: 88, alert_code: "MEM-001", source_feature_window_id: 501 }]}
  />);
}

describe("SystemHealthDashboard", () => {
  it("renders the genuine gauge, stored components, context and cross-page link", () => {
    renderHealth(health);
    expect(screen.getByRole("img", { name: "System Health Score 82 out of 100" })).toBeVisible();
    expect(screen.getByRole("meter", { name: "Resource Condition score" })).toHaveAttribute("aria-valuenow", "80");
    expect(screen.getByText(/Average: 87.30/)).toBeVisible();
    expect(screen.getByText("CPU Temperature Avg")).toBeVisible();
    expect(screen.getByRole("link", { name: /Open matching root-cause evidence/ })).toHaveAttribute("href", "#/root-cause-analysis?windowId=501");
    expect(screen.getByRole("link", { name: /Open alert MEM-001/ })).toHaveAttribute("href", "#/predictive-alerts?alertId=88");
    expect(screen.getByText(/not a future-failure guarantee/i)).toBeVisible();
  });

  it("keeps not-evaluated scores neutral and never presents a fake zero", () => {
    const notEvaluated = { ...health, id: 20, system_health_score: null, score_reconstruction: null, health_band: "not_evaluated", evaluation_state: "not_evaluated" as const, reason_codes: ["coverage_below_minimum"] };
    const { container } = renderHealth(notEvaluated, [notEvaluated]);
    expect(screen.getByRole("img", { name: "System Health not evaluated" })).toHaveTextContent("Not evaluated");
    expect(screen.getByText("Coverage Below Minimum")).toBeVisible();
    expect(screen.getByText(/not plotted as zero/i)).toBeVisible();
    expect(container.querySelector(".health-command-hero--neutral")).not.toBeNull();
  });

  it("preserves missing history as a chart gap and supports expansion", () => {
    const gap = { ...health, id: 20, assessed_at_utc: "2026-09-04T10:11:00Z", system_health_score: null, score_reconstruction: null, health_band: "not_evaluated", evaluation_state: "not_evaluated" as const };
    const recovered = { ...health, id: 21, assessed_at_utc: "2026-09-04T10:16:00Z", system_health_score: 90, score_reconstruction: 90, health_band: "good" };
    const { container } = renderHealth(recovered, [health, gap, recovered]);
    expect(container.querySelectorAll(".health-trend-section .chart-series-path")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Expand" }));
    expect(screen.getByRole("dialog", { name: "System Health Score expanded chart" })).toBeVisible();
    expect(screen.getByText(/1 not-evaluated record/)).toBeVisible();
  });

  it("counts only evaluated records in the health-band distribution", () => {
    const gap = { ...health, id: 20, system_health_score: null, score_reconstruction: null, health_band: "not_evaluated", evaluation_state: "not_evaluated" as const };
    const good = { ...health, id: 21, system_health_score: 91, score_reconstruction: 91, health_band: "good" };
    renderHealth(good, [health, gap, good]);
    expect(screen.getByRole("listitem", { name: "Good: 1 of 2 evaluated records" })).toBeVisible();
    expect(screen.getByRole("listitem", { name: "Stable: 1 of 2 evaluated records" })).toBeVisible();
    expect(screen.getByText(/1 not-evaluated record\(s\) are reported separately/)).toBeVisible();
  });

  it("keeps history bounded behind pagination and retains all range controls", () => {
    const history = Array.from({ length: 11 }, (_, index) => ({
      ...health,
      id: 100 + index,
      feature_window_id: 600 + index,
      assessed_at_utc: `2026-09-04T10:${String(index).padStart(2, "0")}:00Z`,
    }));
    renderHealth(history[10], history);
    expect(screen.getByText("Page 1 of 2")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("Page 2 of 2")).toBeVisible();
    expect(screen.getByText("#100")).toBeVisible();
    expect(screen.getByRole("button", { name: "6 hours" })).toBeVisible();
  });
});
