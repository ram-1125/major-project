import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RootCauseDashboard, type RcaAlert, type RcaRiskAssessment } from "./RootCauseDashboard";

const candidate = {
  id: 41,
  candidate_domain: "memory_pressure",
  rank: 1,
  evidence_confidence: 0.62,
  workload_context: "development",
  first_observed_utc: "2026-09-04T10:00:00Z",
  persistence_duration_seconds: 600,
  reason_codes: ["ram_above_baseline"],
  explanation: "RAM pressure remained above the applicable baseline.",
  recommended_verification_steps: ["Review the top memory processes."],
  limitations: ["Association does not prove causality."],
  evidence: [],
  supporting_metrics: [{ evidence_kind: "metric", evidence_key: "ram_avg", observed_value: 88.2, supports_candidate: true, reason_code: "ram_above_baseline" }],
  supporting_events: [],
  contradictory_evidence: [{ evidence_kind: "metric", evidence_key: "swap_avg", observed_value: 0, supports_candidate: false, reason_code: "swap_healthy" }],
};

const risk: RcaRiskAssessment = {
  id: 17,
  feature_window_id: 501,
  window_start_utc: "2026-09-04T10:00:00Z",
  window_end_utc: "2026-09-04T10:05:00Z",
  workload_context: "development",
  workload_confidence: 0.9,
  evaluated_at_utc: "2026-09-04T10:06:00Z",
  risk_evidence_index: 38,
  evidence_level: "elevated",
  data_quality_status: "sufficient",
  temporal_pattern: "repeated_pressure",
  persistence_window_count: 2,
  reason_codes: ["ram_above_baseline"],
  components: [{ id: 9, component_name: "statistical_deviation", correlation_group: "memory", raw_value: 2, normalized_value: 0.4, contribution: 8, reason_code: "memory_group_strongest_signal_only", evidence: { events: [] } }],
  candidates: [candidate],
  score_reconstruction: 38,
  algorithm_version: "risk-evidence-v1",
  configuration_version: "phase3b-v1",
  catalogue_version: "evidence-catalogue-v1",
  baseline_version_id: 2,
  workload_rule_version: "phase7b1-workload-v3",
};

const alert: RcaAlert = {
  id: 88,
  alert_code: "MEM-001",
  category: "memory_pressure",
  title: "Persistent memory pressure",
  description: "Memory evidence persisted.",
  current_severity: "warning",
  state: "open",
  evaluation_state: "evaluated",
  data_confidence: 91,
  workload_context: "development",
  workload_confidence: 0.9,
  first_observed_utc: "2026-09-04T10:00:00Z",
  latest_observed_utc: "2026-09-04T10:05:00Z",
  last_evidence_utc: "2026-09-04T10:05:00Z",
  consecutive_window_count: 2,
  duration_seconds: 600,
  trend_direction: "stable",
  recovery_state: "not_recovering",
  source_feature_window_id: 501,
  probable_factors: [{ domain: "memory_pressure", rank: 1, confidence: 0.62 }],
  contradictory_evidence: ["Swap remained stable."],
  excluded_inputs: [{ input: "cpu_temperature", status: "unavailable_optional", reason: "sensor_unavailable" }],
  evidence: [],
  diagnostic_recommendations: ["Review memory processes."],
  preventive_guidance: [],
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.location.hash = "";
});

function renderDashboard(risks: RcaRiskAssessment[] = [risk], alerts: RcaAlert[] = [alert]) {
  return render(<RootCauseDashboard
    risks={risks}
    latestRisk={risks[0] ?? null}
    alerts={alerts}
    baselineState="established"
    range="1h"
    onRangeChange={vi.fn()}
    workload="all"
    onWorkloadChange={vi.fn()}
    formatTimestamp={(value) => value}
    apiBaseUrl="http://127.0.0.1:8000"
    stale={false}
  />);
}

describe("RootCauseDashboard", () => {
  it("keeps summaries concise and loads exactly one full investigation on demand", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      const body = url.includes("/api/root-causes/")
        ? { status: "evaluated", candidates: [candidate] }
        : url.includes("/api/alerts/")
          ? { alert }
          : url.includes("/api/deviations/")
            ? { status: "evaluated", assessment: { id: 20, deviation_index: 35, overall_level: "elevated", baseline_scope: "development", feature_results: [{ feature_name: "ram_avg", observed_value: 88.2, baseline_centre: 70, expected_low: 60, expected_high: 80, normalized_deviation_score: 2, severity_band: "elevated", reason_code: "ram_above_baseline" }] } }
            : url.includes("/api/health/")
              ? { status: "established", assessment: { id: 22, system_health_score: 74, health_band: "stable", evaluation_state: "established" } }
          : { status: "evaluated", assessment: risk };
      return { ok: true, status: 200, json: async () => body } as Response;
    });
    renderDashboard();

    expect(screen.getByText("Stored investigations")).toBeVisible();
    expect(screen.queryByText("Observed condition")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "View analysis" }));
    expect(screen.getByRole("status")).toHaveTextContent("Loading this investigation");
    await screen.findByText("Observed condition");

    expect(fetchMock).toHaveBeenCalledTimes(5);
    expect(screen.getByRole("button", { name: "Hide analysis" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("meter", { name: "Memory Pressure evidence support" })).toHaveAttribute("aria-valuenow", "62");
    expect(screen.getByText("+18.20")).toBeVisible();
    expect(screen.getByText(/not confirmed hardware diagnoses/i)).toBeVisible();
    expect(screen.getByRole("link", { name: /Open alert MEM-001/ })).toHaveAttribute("href", "#/predictive-alerts?alertId=88");
  });

  it("filters stored summaries and shows an honest empty state", () => {
    renderDashboard();
    fireEvent.change(screen.getByPlaceholderText("Alert, workload, contributor…"), { target: { value: "storage" } });
    expect(screen.getByText("No matching evaluated investigation")).toBeVisible();
    expect(screen.getByText(/does not invent a cause/i)).toBeVisible();
  });

  it("sorts multiple stored investigations without opening or refetching them", () => {
    const stronger = {
      ...risk,
      id: 18,
      feature_window_id: 502,
      evaluated_at_utc: "2026-09-04T09:06:00Z",
      risk_evidence_index: 71,
      candidates: [{ ...candidate, id: 42, candidate_domain: "storage_capacity_pressure" }],
    };
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const { container } = renderDashboard([risk, stronger], [alert]);
    expect(screen.getAllByRole("button", { name: "View analysis" })).toHaveLength(2);
    fireEvent.change(screen.getByLabelText("Sort"), { target: { value: "evidence" } });
    expect(container.querySelector(".investigation-card")?.textContent).toContain("Investigation #18");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("preserves the complete set of supported time ranges", () => {
    renderDashboard();
    expect(screen.getByLabelText("Time range")).toContainHTML('<option value="6h">6 hours</option>');
  });

  it("keeps detail failure isolated and offers retry", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("local API unavailable"));
    renderDashboard();
    fireEvent.click(screen.getByRole("button", { name: "View analysis" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("temporarily unavailable"));
    expect(screen.getByRole("button", { name: "Retry" })).toBeVisible();
  });
});
