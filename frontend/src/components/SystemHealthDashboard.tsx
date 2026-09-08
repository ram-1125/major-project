import {
  Activity,
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  Clock3,
  ExternalLink,
  Gauge,
  History,
  Info,
  Scale,
  ShieldCheck,
  Stethoscope,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { InteractiveLineChart, type LiveState } from "./InteractiveLineChart";

type HealthComponent = {
  id: number;
  component_name: string;
  component_score: number;
  configured_weight: number;
  effective_weight: number;
  available_subcomponent_weight: number;
  excluded_subcomponent_weight: number;
  raw_deduction_total: number;
  effective_deduction_total: number;
  data_quality_status: string;
  reason_codes: string[];
  details: Record<string, unknown>;
};

type HealthDeduction = {
  id: number;
  component_name: string;
  contribution_group: string;
  signal_name: string;
  raw_deduction: number;
  effective_deduction: number;
  maximum_deduction: number;
  correlation_or_cap_reason: string;
  reason_code: string;
  explanation: string;
  supporting_value: unknown;
  supporting_event_ids: number[];
  workload_context: string | null;
};

type HealthInput = {
  input_name: string;
  input_category: string;
  availability_status: string;
  observed_value: unknown;
  excluded_reason: string | null;
  applicable_weight: number;
};

type Guidance = { guidance_type: string; related_component: string; sequence: number; guidance_text: string };

export type HealthRecord = {
  id: number;
  feature_window_id: number;
  window_start_utc: string;
  window_end_utc: string;
  assessed_at_utc: string;
  system_health_score: number | null;
  health_band: string;
  evaluation_state: "not_evaluated" | "provisional" | "established";
  data_confidence: number;
  coverage_ratio: number;
  workload_context: string | null;
  workload_confidence: number | null;
  available_component_weight: number;
  excluded_component_weight: number;
  normalization_method: string;
  algorithm_version: string;
  configuration_version: string;
  reason_codes: string[];
  first_observed_utc: string | null;
  most_recent_observed_utc: string | null;
  consecutive_window_count: number;
  persistence_duration_seconds: number;
  trend_direction: string;
  recovery_state: string;
  components: HealthComponent[];
  deductions: HealthDeduction[];
  inputs: HealthInput[];
  excluded_inputs: HealthInput[];
  guidance: { explanations: Guidance[]; recommendations: Guidance[]; improvements: Guidance[]; limitations: Guidance[] };
  baseline_reference: { id: number; configuration_version?: string } | null;
  deviation_reference: { id: number } | null;
  risk_reference: { id: number } | null;
  risk_assessment_id?: number | null;
  score_reconstruction: number | null;
};

type Props = {
  latest: HealthRecord | null;
  history: HealthRecord[];
  interpretation: string;
  statusReasons: string[];
  range: "1h" | "6h" | "24h" | "7d" | "all";
  onRangeChange: (range: Props["range"]) => void;
  workload: string;
  onWorkloadChange: (workload: string) => void;
  formatTimestamp: (value: string) => string;
  apiBaseUrl: string;
  liveState: LiveState;
  stale: boolean;
  alerts: Array<{ id: number; alert_code: string; source_feature_window_id: number }>;
};

const RANGE_LABELS: Array<[Props["range"], string]> = [
  ["1h", "Last hour"],
  ["6h", "6 hours"],
  ["24h", "24 hours"],
  ["7d", "7 days"],
  ["all", "All data"],
];

function label(value: string | null | undefined): string {
  if (!value) return "Unavailable";
  const acronyms = new Set(["cpu", "gpu", "ram", "io", "dpc", "whea", "rca"]);
  return value.replaceAll("_", " ").split(" ").map((word) =>
    acronyms.has(word.toLocaleLowerCase()) ? word.toLocaleUpperCase() : word.charAt(0).toLocaleUpperCase() + word.slice(1),
  ).join(" ");
}

function userText(value: string): string {
  return value
    .replace(/Phase 3A/gi, "personal-baseline")
    .replace(/Phase 3B/gi, "operational risk-evidence")
    .replace(/Phase 7B(?:\.1)?/gi, "Advanced System Signal");
}

function formatEvidence(value: unknown): string {
  if (value === null || value === undefined) return "Unavailable";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(2);
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "string") return label(value);
  if (Array.isArray(value)) return value.length ? value.map(formatEvidence).join(", ") : "None";
  if (typeof value === "object") return Object.entries(value as Record<string, unknown>).map(([key, item]) => `${label(key)}: ${formatEvidence(item)}`).join(" · ");
  return String(value);
}

function scoreTone(record: HealthRecord | null): string {
  if (!record || record.system_health_score === null || record.evaluation_state === "not_evaluated") return "neutral";
  if (record.health_band === "good") return "healthy";
  if (record.health_band === "stable") return "info";
  if (record.health_band === "attention") return "caution";
  if (record.health_band === "degraded") return "warning";
  return "critical";
}

export function SystemHealthDashboard({
  latest,
  history,
  interpretation,
  statusReasons,
  range,
  onRangeChange,
  workload,
  onWorkloadChange,
  formatTimestamp,
  apiBaseUrl,
  liveState,
  stale,
  alerts,
}: Props) {
  const [evaluationFilter, setEvaluationFilter] = useState("all");
  const [bandFilter, setBandFilter] = useState("all");
  const [historySort, setHistorySort] = useState<"newest" | "oldest">("newest");
  const [historyPage, setHistoryPage] = useState(0);
  const [linkedRecord, setLinkedRecord] = useState<HealthRecord | null>(null);
  const [linkedState, setLinkedState] = useState<"idle" | "loading" | "error">("idle");

  useEffect(() => {
    const parameters = new URLSearchParams(window.location.hash.split("?", 2)[1] ?? "");
    const windowId = Number(parameters.get("windowId"));
    if (!Number.isInteger(windowId) || windowId <= 0) {
      setLinkedRecord(null);
      setLinkedState("idle");
      return;
    }
    const local = history.find((item) => item.feature_window_id === windowId);
    if (local && Array.isArray(local.components)) {
      setLinkedRecord(local);
      setLinkedState("idle");
      return;
    }
    const controller = new AbortController();
    setLinkedState("loading");
    void fetch(`${apiBaseUrl}/api/health/${windowId}`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(`The local API returned ${response.status}.`);
        return response.json() as Promise<{ assessment: HealthRecord | null }>;
      })
      .then((response) => {
        setLinkedRecord(response.assessment);
        setLinkedState(response.assessment ? "idle" : "error");
      })
      .catch(() => {
        if (!controller.signal.aborted) setLinkedState("error");
      });
    return () => controller.abort();
  }, [apiBaseUrl, history]);

  const displayed = linkedRecord ?? latest;
  const relatedAlert = displayed
    ? alerts.find((item) => item.source_feature_window_id === displayed.feature_window_id) ?? null
    : null;
  const tone = scoreTone(displayed);
  const trend = useMemo(() => [...history].sort((left, right) => Date.parse(left.assessed_at_utc) - Date.parse(right.assessed_at_utc)).map((item) => ({
    timestamp_utc: item.assessed_at_utc,
    score: item.evaluation_state === "not_evaluated" ? null : item.system_health_score,
    band: item.health_band,
    workload: item.workload_context,
    state: item.evaluation_state,
  })), [history]);
  const workloads = useMemo(() => [...new Set(history.map((item) => item.workload_context).filter((item): item is string => Boolean(item)))].sort(), [history]);
  const filteredHistory = useMemo(() => history.filter((item) => {
    if (evaluationFilter !== "all" && item.evaluation_state !== evaluationFilter) return false;
    if (bandFilter !== "all" && item.health_band !== bandFilter) return false;
    return true;
  }).sort((left, right) => {
    const difference = Date.parse(right.assessed_at_utc) - Date.parse(left.assessed_at_utc);
    return historySort === "newest" ? difference : -difference;
  }), [bandFilter, evaluationFilter, history, historySort]);
  const evaluatedHistory = history.filter((item) => item.system_health_score !== null && item.evaluation_state !== "not_evaluated");
  const notEvaluatedCount = history.length - evaluatedHistory.length;
  const bands = ["good", "stable", "attention", "degraded", "critical_condition"];
  const distribution = bands.map((band) => ({ band, count: evaluatedHistory.filter((item) => item.health_band === band).length }));
  const PAGE_SIZE = 10;
  const pageCount = Math.max(1, Math.ceil(filteredHistory.length / PAGE_SIZE));
  const safePage = Math.min(historyPage, pageCount - 1);
  const pageRows = filteredHistory.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE);
  const gaugeValue = displayed?.system_health_score ?? null;
  const gaugeDash = gaugeValue === null ? 18 : Math.max(0, Math.min(100, gaugeValue));
  const reasons = displayed?.reason_codes.length ? displayed.reason_codes : statusReasons;
  const componentIcon = (name: string) => name === "resource_condition" ? Gauge
    : name === "operating_stability" ? Activity
      : name === "operational_events" ? AlertTriangle
        : ShieldCheck;

  return (
    <section className="health-command" aria-labelledby="health-command-title">
      <header className="analysis-page-header health-page-header">
        <div><p className="eyebrow">Current operating condition</p><h2 id="health-command-title">Current condition summary</h2><p>Transparent condition scoring from completed five-minute telemetry, stability, events, and eligible learned evidence.</p></div>
        <div className="analysis-header-facts" aria-label="System health summary">
          <span><Clock3 aria-hidden="true" />Last evaluation<strong>{displayed ? formatTimestamp(displayed.assessed_at_utc) : "Not evaluated"}</strong></span>
          <span><Activity aria-hidden="true" />Workload<strong>{label(displayed?.workload_context)}</strong></span>
          <span><ShieldCheck aria-hidden="true" />Assessment<strong>{label(displayed?.evaluation_state ?? "not_evaluated")}</strong></span>
          <span><History aria-hidden="true" />Freshness<strong>{stale ? "Telemetry stale" : "Current local data"}</strong></span>
        </div>
      </header>

      {linkedRecord && <div className="linked-record-banner" role="status"><Info aria-hidden="true" />Viewing the System Health record linked to feature window #{linkedRecord.feature_window_id}. <a href="#/system-health">Return to latest</a></div>}
      {linkedState === "loading" && <div className="linked-record-banner" role="status"><span className="loading-spinner" aria-hidden="true" />Loading linked health record…</div>}
      {linkedState === "error" && <div className="linked-record-banner linked-record-banner--warning" role="alert"><AlertTriangle aria-hidden="true" />The linked window has no available health assessment. The latest assessment is shown.</div>}

      <section className={`health-command-hero health-command-hero--${tone}`}>
        <div className="health-gauge" role="img" aria-label={gaugeValue === null ? "System Health not evaluated" : `System Health Score ${gaugeValue.toFixed(0)} out of 100`}>
          <svg viewBox="0 0 120 120" aria-hidden="true"><circle cx="60" cy="60" r="49" /><circle className="health-gauge__value" cx="60" cy="60" r="49" pathLength="100" strokeDasharray={`${gaugeDash} ${100 - gaugeDash}`} /></svg>
          <span><strong>{gaugeValue === null ? "—" : gaugeValue.toFixed(0)}</strong><small>{gaugeValue === null ? "Not evaluated" : label(displayed?.health_band)}</small></span>
        </div>
        <div className="health-hero-copy">
          <span className={`analysis-chip analysis-chip--${tone}`}>{label(displayed?.evaluation_state ?? "not_evaluated")}</span>
          <h3>{gaugeValue === null ? "No score is available for this window" : `${label(displayed?.health_band)} current operating condition`}</h3>
          <p>{gaugeValue === null ? "SmartOps preserves inadequate or incomplete evidence as an explicit not-evaluated result; it never substitutes a fake zero." : `Data confidence ${displayed?.data_confidence.toFixed(1)}% · coverage ${((displayed?.coverage_ratio ?? 0) * 100).toFixed(0)}% · ${label(displayed?.trend_direction)} trend.`}</p>
          <dl><div><dt>Personal baseline</dt><dd>{displayed?.baseline_reference ? `Available · record #${displayed.baseline_reference.id}` : "Not established"}</dd></div><div><dt>Risk evidence</dt><dd>{displayed?.risk_reference ? `Available · assessment #${displayed.risk_reference.id}` : "Not evaluated"}</dd></div><div><dt>Analysis window</dt><dd>{displayed ? `${formatTimestamp(displayed.window_start_utc)} – ${formatTimestamp(displayed.window_end_utc)}` : "Unavailable"}</dd></div></dl>
        </div>
        <div className="health-interpretation-card"><Info aria-hidden="true" /><p>{interpretation}</p><strong>It is not a future-failure guarantee or hardware diagnosis.</strong></div>
      </section>

      {(!displayed || displayed.evaluation_state === "not_evaluated") && <div className="health-reason-panel" role="status"><AlertTriangle aria-hidden="true" /><div><strong>Why this assessment is not evaluated</strong><ul>{reasons.length ? reasons.map((reason) => <li key={reason}>{label(reason)}</li>) : <li>The reason was not recorded for this assessment.</li>}</ul></div></div>}

      {displayed && displayed.evaluation_state !== "not_evaluated" && <>
        <section className="health-command-section"><div className="command-section-heading"><div><p className="eyebrow">Reconstructable calculation</p><h3>Health component breakdown</h3></div><p>Stored component scores and effective weights; no frontend scoring.</p></div><div className="health-component-bars">{displayed.components.map((component) => { const ComponentIcon = componentIcon(component.component_name); return <article key={component.id}><div><span className="health-component-name"><i aria-hidden="true"><ComponentIcon /></i>{label(component.component_name)}</span><strong>{component.component_score.toFixed(1)}</strong></div><div className="health-bar" role="meter" aria-label={`${label(component.component_name)} score`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={component.component_score}><i style={{ width: `${Math.max(0, Math.min(100, component.component_score))}%` }} /></div><p>Effective weight {(component.effective_weight * 100).toFixed(0)}% · effective deduction {component.effective_deduction_total.toFixed(1)} · {label(component.data_quality_status)}</p></article>; })}</div><div className="score-reconstruction"><Scale aria-hidden="true" /><span>Stored score <strong>{displayed.system_health_score?.toFixed(4)}</strong></span><span>Reconstructed <strong>{displayed.score_reconstruction?.toFixed(4) ?? "Unavailable"}</strong></span><span>Available weight <strong>{displayed.available_component_weight.toFixed(0)}%</strong></span><span>Excluded weight <strong>{displayed.excluded_component_weight.toFixed(0)}%</strong></span></div></section>

        <div className="health-command-columns">
          <section className="health-command-section"><div className="command-section-heading"><div><p className="eyebrow">Stored interpretation</p><h3>Observed evidence</h3></div></div><div className="health-observation-list">{displayed.deductions.filter((item) => item.supporting_value !== null).map((item) => <article key={item.id}><div><strong>{label(item.signal_name)}</strong><span>{label(item.component_name)}</span></div><p>{formatEvidence(item.supporting_value)}</p><small>{userText(item.explanation)}</small><span className={item.effective_deduction > 0 ? "deduction-active" : "deduction-none"}>{item.effective_deduction > 0 ? `−${item.effective_deduction.toFixed(1)} effective deduction` : "No effective deduction"}</span></article>)}</div></section>
          <section className="health-command-section"><div className="command-section-heading"><div><p className="eyebrow">Evidence quality</p><h3>Excluded and unavailable inputs</h3></div></div>{displayed.excluded_inputs.length ? <ul className="health-excluded-list">{displayed.excluded_inputs.map((input) => <li key={`${input.input_category}-${input.input_name}`}><span><AlertTriangle aria-hidden="true" /></span><div><strong>{label(input.input_name)}</strong><p>{label(input.availability_status)}{input.excluded_reason ? ` · ${label(input.excluded_reason)}` : ""}</p></div></li>)}</ul> : <div className="analysis-empty analysis-empty--compact"><CheckCircle2 aria-hidden="true" /><strong>All applicable inputs were available</strong></div>}<p className="health-comparability">Scores calculated with different evidence availability may not be perfectly comparable.</p></section>
        </div>

        <div className="health-command-columns">
          <section className="health-command-section"><div className="command-section-heading"><div><p className="eyebrow">Safe next steps</p><h3>Diagnostic guidance</h3></div></div>{displayed.guidance.recommendations.length ? <ol className="diagnostic-guidance">{displayed.guidance.recommendations.map((item) => <li key={`${item.sequence}-${item.guidance_text}`}><Stethoscope aria-hidden="true" /><span>{userText(item.guidance_text)}</span></li>)}</ol> : <p>No additional diagnostic check is suggested for this window.</p>}{displayed.guidance.improvements.length > 0 && <><h4>Improvement or recovery evidence</h4><ul>{displayed.guidance.improvements.map((item) => <li key={`${item.sequence}-${item.guidance_text}`}>{userText(item.guidance_text)}</li>)}</ul></>}</section>
          <section className="health-command-section"><div className="command-section-heading"><div><p className="eyebrow">Calculation safeguards</p><h3>Limitations and versioning</h3></div></div><ul>{displayed.guidance.limitations.map((item) => <li key={`${item.sequence}-${item.guidance_text}`}>{userText(item.guidance_text)}</li>)}</ul><details className="technical-record-details"><summary>Technical record identifiers and versions</summary><dl><div><dt>Health assessment</dt><dd>#{displayed.id}</dd></div><div><dt>Feature window</dt><dd>#{displayed.feature_window_id}</dd></div><div><dt>Algorithm</dt><dd>{displayed.algorithm_version}</dd></div><div><dt>Configuration</dt><dd>{displayed.configuration_version}</dd></div><div><dt>Normalization</dt><dd>{label(displayed.normalization_method)}</dd></div></dl></details><nav className="analysis-related-links" aria-label="Related health evidence">{displayed.risk_reference && <a href={`#/root-cause-analysis?windowId=${displayed.feature_window_id}`}><BarChart3 aria-hidden="true" />Open matching root-cause evidence<ExternalLink aria-hidden="true" /></a>}{relatedAlert && <a href={`#/predictive-alerts?alertId=${relatedAlert.id}`}><AlertTriangle aria-hidden="true" />Open alert {relatedAlert.alert_code}<ExternalLink aria-hidden="true" /></a>}<a href={`#/live-monitoring?start=${encodeURIComponent(displayed.window_start_utc)}&end=${encodeURIComponent(displayed.window_end_utc)}`}><Activity aria-hidden="true" />Inspect Live Monitoring period<ExternalLink aria-hidden="true" /></a></nav></section>
        </div>
      </>}

      <section className="health-command-section health-trend-section"><div className="command-section-heading command-section-heading--controls"><div><p className="eyebrow">Evaluated history</p><h3>System Health trend</h3><p>{evaluatedHistory.length} evaluated point(s) · {notEvaluatedCount} not-evaluated gap(s). Gaps are never connected or converted to zero.</p></div><div className="range-control" aria-label="Health score time range">{RANGE_LABELS.map(([value, text]) => <button type="button" className={range === value ? "active" : ""} onClick={() => onRangeChange(value)} key={value}>{text}</button>)}</div></div><InteractiveLineChart title="System Health Score" data={trend} series={[{ label: "Health", color: "#168fd2", value: (item) => item.score }]} fixedMaximum={100} axisFormatter={(value) => value.toFixed(0)} expandable liveState={liveState} tooltipDetails={(item) => [{ label: "Health band", value: label(item.band) }, { label: "Workload", value: label(item.workload) }, { label: "Evaluation state", value: label(item.state) }]} /></section>

      <div className="health-command-columns health-distribution-row">
          <section className="health-command-section"><div className="command-section-heading"><div><p className="eyebrow">Evaluated records only</p><h3>Health-band distribution</h3></div></div>{evaluatedHistory.length ? <div className="health-distribution" role="list" aria-label="Health-band distribution">{distribution.map((item) => <div key={item.band} role="listitem" aria-label={`${label(item.band)}: ${item.count} of ${evaluatedHistory.length} evaluated records`}><span>{label(item.band)}</span><div><i className={`health-band-fill health-band-fill--${item.band}`} style={{ width: `${(item.count / evaluatedHistory.length) * 100}%` }} /></div><strong>{item.count}</strong></div>)}</div> : <div className="analysis-empty analysis-empty--compact"><Gauge aria-hidden="true" /><strong>No evaluated scores in this range</strong></div>}<p>{notEvaluatedCount} not-evaluated record(s) are reported separately and are not plotted as zero.</p></section>
        <section className="health-command-section"><div className="command-section-heading"><div><p className="eyebrow">Current assessment</p><h3>Context and persistence</h3></div></div>{displayed ? <dl className="health-context-list"><div><dt>Trend</dt><dd>{label(displayed.trend_direction)}</dd></div><div><dt>Recovery</dt><dd>{label(displayed.recovery_state)}</dd></div><div><dt>Consecutive windows</dt><dd>{displayed.consecutive_window_count}</dd></div><div><dt>Data confidence</dt><dd>{displayed.data_confidence.toFixed(1)}%</dd></div><div><dt>Workload confidence</dt><dd>{displayed.workload_confidence === null ? "Unavailable" : `${(displayed.workload_confidence * 100).toFixed(0)}%`}</dd></div></dl> : <p>No current assessment is available.</p>}</section>
      </div>

      <section className="health-command-section health-history-section"><div className="command-section-heading command-section-heading--controls"><div><p className="eyebrow">Bounded read-only history</p><h3>Assessment records</h3><p>{filteredHistory.length.toLocaleString()} matching record(s) from {history.length.toLocaleString()} loaded.</p></div><div className="health-history-filters"><label><span>Workload</span><select value={workload} onChange={(event) => { onWorkloadChange(event.target.value); setHistoryPage(0); }}><option value="all">All workloads</option>{workloads.map((value) => <option value={value} key={value}>{label(value)}</option>)}</select></label><label><span>Evaluation</span><select value={evaluationFilter} onChange={(event) => { setEvaluationFilter(event.target.value); setHistoryPage(0); }}><option value="all">All states</option><option value="established">Established</option><option value="provisional">Provisional</option><option value="not_evaluated">Not evaluated</option></select></label><label><span>Health band</span><select value={bandFilter} onChange={(event) => { setBandFilter(event.target.value); setHistoryPage(0); }}><option value="all">All bands</option>{[...bands, "not_evaluated"].map((value) => <option value={value} key={value}>{label(value)}</option>)}</select></label><label><span>Sort</span><select value={historySort} onChange={(event) => setHistorySort(event.target.value as typeof historySort)}><option value="newest">Newest first</option><option value="oldest">Oldest first</option></select></label></div></div><div className="table-wrap"><table><thead><tr><th>Assessment</th><th>Time</th><th>Workload</th><th>Score</th><th>Health band</th><th>State</th><th>Confidence</th><th>Coverage</th><th>Related evidence</th></tr></thead><tbody>{pageRows.map((item) => <tr key={item.id} className={linkedRecord?.id === item.id ? "health-history-linked" : ""}><td>#{item.id}</td><td>{formatTimestamp(item.assessed_at_utc)}</td><td>{label(item.workload_context)}</td><td>{item.system_health_score === null ? "Not evaluated" : item.system_health_score.toFixed(1)}</td><td>{label(item.health_band)}</td><td><span className={`analysis-chip analysis-chip--${scoreTone(item)}`}>{label(item.evaluation_state)}</span></td><td>{item.data_confidence.toFixed(1)}%</td><td>{(item.coverage_ratio * 100).toFixed(0)}%</td><td>{item.risk_reference || item.risk_assessment_id ? <a href={`#/root-cause-analysis?windowId=${item.feature_window_id}`}>Risk / RCA <ExternalLink aria-hidden="true" /></a> : "None"}</td></tr>)}{pageRows.length === 0 && <tr><td colSpan={9}>No health assessments match the selected filters.</td></tr>}</tbody></table></div><div className="pagination"><button type="button" disabled={safePage === 0} onClick={() => setHistoryPage((value) => Math.max(0, value - 1))}>Previous</button><span>Page {safePage + 1} of {pageCount}</span><button type="button" disabled={safePage >= pageCount - 1} onClick={() => setHistoryPage((value) => Math.min(pageCount - 1, value + 1))}>Next</button></div></section>
    </section>
  );
}
