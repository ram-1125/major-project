import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Clock3,
  Cpu,
  ExternalLink,
  FilterX,
  Gauge,
  HardDrive,
  Info,
  MemoryStick,
  Network,
  Search,
  ShieldCheck,
  Thermometer,
  Workflow,
  XCircle,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

type CandidateEvidence = {
  evidence_kind: string;
  evidence_key: string;
  observed_value: unknown;
  supports_candidate: boolean;
  reason_code: string;
};

type Candidate = {
  id: number;
  candidate_domain: string;
  rank: number;
  evidence_confidence: number;
  workload_context: string;
  first_observed_utc: string;
  persistence_duration_seconds: number;
  reason_codes: string[];
  explanation: string;
  recommended_verification_steps: string[];
  limitations: string[];
  evidence: CandidateEvidence[];
  supporting_metrics: CandidateEvidence[];
  supporting_events: CandidateEvidence[];
  contradictory_evidence: CandidateEvidence[];
};

type RiskComponent = {
  id: number;
  component_name: string;
  correlation_group: string;
  raw_value: number | null;
  normalized_value: number | null;
  contribution: number;
  reason_code: string;
  evidence: { events?: CorrelatedEvent[]; [key: string]: unknown };
};

type CorrelatedEvent = {
  id: number;
  event_timestamp_utc: string;
  event_level: string;
  smartops_category: string;
  safe_summary: string;
  timing: "before" | "during" | "after";
};

export type RcaRiskAssessment = {
  id: number;
  feature_window_id: number;
  window_start_utc: string;
  window_end_utc: string;
  workload_context: string;
  workload_confidence: number;
  evaluated_at_utc: string;
  risk_evidence_index: number;
  evidence_level: string;
  data_quality_status: string;
  temporal_pattern: string;
  persistence_window_count: number;
  reason_codes: string[];
  components: RiskComponent[];
  candidates: Candidate[];
  score_reconstruction: number;
  algorithm_version?: string;
  configuration_version?: string;
  catalogue_version?: string;
  baseline_version_id?: number | null;
  workload_rule_version?: string | null;
};

type AlertEvidence = {
  id: number;
  evidence_key: string;
  correlation_group: string;
  suppressed: boolean;
  suppression_reason: string | null;
  explanation: string;
};

export type RcaAlert = {
  id: number;
  alert_code: string;
  category: string;
  title: string;
  description: string;
  current_severity: string;
  state: string;
  evaluation_state: string;
  data_confidence: number;
  workload_context: string | null;
  workload_confidence: number | null;
  first_observed_utc: string;
  latest_observed_utc: string;
  last_evidence_utc: string;
  consecutive_window_count: number;
  duration_seconds: number;
  trend_direction: string;
  recovery_state: string;
  source_feature_window_id: number;
  probable_factors: Array<{ domain: string; rank: number; confidence: number | null }>;
  contradictory_evidence: string[];
  excluded_inputs: Array<{ input: string; status: string; reason: string | null }>;
  evidence: AlertEvidence[];
  diagnostic_recommendations: string[];
  preventive_guidance: string[];
};

type DetailState = {
  risk: RcaRiskAssessment;
  candidates: Candidate[];
  alert: RcaAlert | null;
  deviation: {
    id: number;
    deviation_index: number | null;
    overall_level: string;
    baseline_scope: string;
    feature_results: Array<{
      feature_name: string;
      observed_value: number | null;
      baseline_centre: number | null;
      expected_low: number | null;
      expected_high: number | null;
      normalized_deviation_score: number | null;
      severity_band: string;
      reason_code: string;
    }>;
  } | null;
  health: {
    id: number;
    system_health_score: number | null;
    health_band: string;
    evaluation_state: string;
  } | null;
};

type Props = {
  risks: RcaRiskAssessment[];
  latestRisk: RcaRiskAssessment | null;
  alerts: RcaAlert[];
  baselineState: string | null;
  range: "1h" | "6h" | "24h" | "7d" | "all";
  onRangeChange: (range: Props["range"]) => void;
  workload: string;
  onWorkloadChange: (workload: string) => void;
  formatTimestamp: (value: string) => string;
  apiBaseUrl: string;
  stale: boolean;
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

function evidenceValue(value: unknown): string {
  if (value === null || value === undefined) return "Unavailable";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(2);
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "string") return label(value);
  if (Array.isArray(value)) return value.length ? value.map(evidenceValue).join(", ") : "None recorded";
  if (typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, item]) => `${label(key)}: ${evidenceValue(item)}`)
      .join(" · ");
  }
  return String(value);
}

function severityTone(value: string): string {
  if (value === "urgent" || value === "critical_evidence") return "critical";
  if (value === "warning" || value === "high") return "warning";
  if (value === "advisory" || value === "elevated") return "caution";
  return "info";
}

function ContributorIcon({ domain }: { domain: string }) {
  const normalized = domain.toLocaleLowerCase();
  const Icon = normalized.includes("cpu") ? Cpu
    : normalized.includes("memory") || normalized.includes("ram") || normalized.includes("swap") ? MemoryStick
      : normalized.includes("disk") || normalized.includes("storage") ? HardDrive
        : normalized.includes("network") ? Network
          : normalized.includes("thermal") || normalized.includes("temperature") ? Thermometer
            : Activity;
  return <Icon aria-hidden="true" />;
}

async function fetchDetail<T>(url: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error(`The local API returned ${response.status}.`);
  return response.json() as Promise<T>;
}

export function RootCauseDashboard({
  risks,
  latestRisk,
  alerts,
  baselineState,
  range,
  onRangeChange,
  workload,
  onWorkloadChange,
  formatTimestamp,
  apiBaseUrl,
  stale,
}: Props) {
  const [search, setSearch] = useState("");
  const [severity, setSeverity] = useState("all");
  const [category, setCategory] = useState("all");
  const [candidateDomain, setCandidateDomain] = useState("all");
  const [evaluationState, setEvaluationState] = useState("all");
  const [sort, setSort] = useState<"newest" | "oldest" | "evidence">("newest");
  const [visibleCount, setVisibleCount] = useState(12);
  const [expandedWindowId, setExpandedWindowId] = useState<number | null>(null);
  const [detail, setDetail] = useState<DetailState | null>(null);
  const [detailState, setDetailState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [detailError, setDetailError] = useState("");
  const [detailRequestVersion, setDetailRequestVersion] = useState(0);
  const [linkedAlert, setLinkedAlert] = useState<RcaAlert | null>(null);
  const [linkResolutionState, setLinkResolutionState] = useState<"idle" | "loading" | "error">("idle");

  const alertsByWindow = useMemo(() => {
    const result = new Map<number, RcaAlert>();
    for (const alert of alerts) {
      if (!result.has(alert.source_feature_window_id)) result.set(alert.source_feature_window_id, alert);
    }
    if (linkedAlert) result.set(linkedAlert.source_feature_window_id, linkedAlert);
    return result;
  }, [alerts, linkedAlert]);

  const domains = useMemo(() => [...new Set(
    risks.flatMap((item) => item.candidates.map((candidate) => candidate.candidate_domain)),
  )].sort(), [risks]);
  const workloads = useMemo(() => [...new Set(risks.map((item) => item.workload_context))].sort(), [risks]);
  const categories = useMemo(() => [...new Set(alerts.map((item) => item.category))].sort(), [alerts]);

  const filtered = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    return risks.filter((item) => {
      const alert = alertsByWindow.get(item.feature_window_id);
      const searchable = [
        `investigation ${item.id}`,
        item.workload_context,
        item.evidence_level,
        ...item.reason_codes,
        ...item.candidates.flatMap((candidate) => [candidate.candidate_domain, candidate.explanation]),
        alert?.alert_code ?? "",
        alert?.title ?? "",
      ].join(" ").toLocaleLowerCase();
      if (query && !searchable.includes(query)) return false;
      if (severity !== "all" && alert?.current_severity !== severity) return false;
      if (category !== "all" && alert?.category !== category) return false;
      if (candidateDomain !== "all" && !item.candidates.some((candidate) => candidate.candidate_domain === candidateDomain)) return false;
      if (evaluationState === "with_alert" && !alert) return false;
      if (evaluationState === "without_alert" && alert) return false;
      return true;
    }).sort((left, right) => {
      if (sort === "evidence") return right.risk_evidence_index - left.risk_evidence_index;
      const difference = Date.parse(right.evaluated_at_utc) - Date.parse(left.evaluated_at_utc);
      return sort === "newest" ? difference : -difference;
    });
  }, [alertsByWindow, candidateDomain, category, evaluationState, risks, search, severity, sort]);

  const resetFilters = () => {
    setSearch("");
    setSeverity("all");
    setCategory("all");
    setCandidateDomain("all");
    setEvaluationState("all");
    setSort("newest");
    onWorkloadChange("all");
    onRangeChange("1h");
    setVisibleCount(12);
  };

  useEffect(() => {
    const parameters = new URLSearchParams(window.location.hash.split("?", 2)[1] ?? "");
    const alertId = Number(parameters.get("alertId"));
    const windowId = Number(parameters.get("windowId"));
    if (Number.isInteger(alertId) && alertId > 0) {
      const linked = alerts.find((item) => item.id === alertId);
      if (linked) {
        setLinkedAlert(linked);
        setExpandedWindowId(linked.source_feature_window_id);
        setLinkResolutionState("idle");
        return;
      }
      const controller = new AbortController();
      setLinkResolutionState("loading");
      void fetchDetail<{ alert: RcaAlert }>(`${apiBaseUrl}/api/alerts/${alertId}`, controller.signal)
        .then((response) => {
          setLinkedAlert(response.alert);
          setExpandedWindowId(response.alert.source_feature_window_id);
          setLinkResolutionState("idle");
        })
        .catch(() => {
          if (!controller.signal.aborted) setLinkResolutionState("error");
        });
      return () => controller.abort();
    } else if (Number.isInteger(windowId) && windowId > 0) {
      setExpandedWindowId(windowId);
    }
  }, [alerts, apiBaseUrl]);

  useEffect(() => {
    if (expandedWindowId === null) {
      setDetail(null);
      setDetailState("idle");
      return;
    }
    const controller = new AbortController();
    const alertSummary = alertsByWindow.get(expandedWindowId) ?? null;
    setDetailState("loading");
    setDetailError("");
    void Promise.all([
      fetchDetail<{ status: string; assessment: RcaRiskAssessment | null }>(
        `${apiBaseUrl}/api/risk/${expandedWindowId}`,
        controller.signal,
      ),
      fetchDetail<{ status: string; candidates: Candidate[] }>(
        `${apiBaseUrl}/api/root-causes/${expandedWindowId}`,
        controller.signal,
      ),
      fetchDetail<{ status: string; assessment: DetailState["deviation"] }>(
        `${apiBaseUrl}/api/deviations/${expandedWindowId}`,
        controller.signal,
      ),
      fetchDetail<{ status: string; assessment: DetailState["health"] }>(
        `${apiBaseUrl}/api/health/${expandedWindowId}`,
        controller.signal,
      ),
      alertSummary
        ? fetchDetail<{ alert: RcaAlert }>(`${apiBaseUrl}/api/alerts/${alertSummary.id}`, controller.signal)
        : Promise.resolve(null),
    ]).then(([riskResponse, rootResponse, deviationResponse, healthResponse, alertResponse]) => {
      if (!riskResponse.assessment) throw new Error("No evaluated risk record exists for this feature window.");
      setDetail({
        risk: { ...riskResponse.assessment, candidates: rootResponse.candidates },
        candidates: rootResponse.candidates,
        alert: alertResponse?.alert ?? null,
        deviation: deviationResponse.assessment,
        health: healthResponse.assessment,
      });
      setDetailState("ready");
    }).catch((error: unknown) => {
      if (controller.signal.aborted) return;
      setDetailState("error");
      setDetailError(error instanceof Error ? error.message : "The investigation could not be loaded.");
    });
    return () => controller.abort();
  }, [alertsByWindow, apiBaseUrl, detailRequestVersion, expandedWindowId]);

  const toggleDetails = (windowId: number) => {
    setExpandedWindowId((current) => current === windowId ? null : windowId);
  };

  const displayedRisks = detail?.risk && !filtered.some((item) => item.id === detail.risk.id)
    ? [detail.risk, ...filtered]
    : filtered;

  return (
    <section className="rca-command" aria-labelledby="rca-command-title">
      <header className="analysis-page-header">
        <div>
          <p className="eyebrow">Explainable investigation workspace</p>
          <h2 id="rca-command-title">Investigation summary</h2>
          <p>Review stored contributing evidence for evaluated five-minute windows and related alerts.</p>
        </div>
        <div className="analysis-header-facts" aria-label="Root-cause analysis summary">
          <span><Clock3 aria-hidden="true" />Latest evaluation<strong>{latestRisk ? formatTimestamp(latestRisk.evaluated_at_utc) : "Not evaluated"}</strong></span>
          <span><Workflow aria-hidden="true" />Workload<strong>{label(latestRisk?.workload_context)}</strong></span>
          <span><Activity aria-hidden="true" />Investigations<strong>{risks.length.toLocaleString()} loaded</strong></span>
          <span><ShieldCheck aria-hidden="true" />Evidence freshness<strong>{stale ? "Telemetry stale" : "Current local data"}</strong></span>
        </div>
      </header>

      <div className="analysis-disclaimer" role="note">
        <AlertTriangle aria-hidden="true" />
        <span>Ranked contributors are evidence-supported hypotheses, not confirmed hardware diagnoses or proof of causality. SmartOps performs no automatic remediation.</span>
      </div>

      <details className="analysis-filter-panel" open>
        <summary><Search aria-hidden="true" />Investigation filters</summary>
        <div className="analysis-filter-grid">
          <label className="analysis-search"><span>Search investigations</span><div><Search aria-hidden="true" /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Alert, workload, contributor…" /></div></label>
          <label><span>Time range</span><select value={range} onChange={(event) => onRangeChange(event.target.value as Props["range"])}>{RANGE_LABELS.map(([value, text]) => <option value={value} key={value}>{text}</option>)}</select></label>
          <label><span>Workload</span><select value={workload} onChange={(event) => onWorkloadChange(event.target.value)}><option value="all">All workloads</option>{workloads.map((value) => <option value={value} key={value}>{label(value)}</option>)}</select></label>
          <label><span>Alert category</span><select value={category} onChange={(event) => setCategory(event.target.value)}><option value="all">All categories</option>{categories.map((value) => <option value={value} key={value}>{label(value)}</option>)}</select></label>
          <label><span>Alert severity</span><select value={severity} onChange={(event) => setSeverity(event.target.value)}><option value="all">All severities</option>{["informational", "advisory", "warning", "urgent"].map((value) => <option value={value} key={value}>{label(value)}</option>)}</select></label>
          <label><span>Contributor</span><select value={candidateDomain} onChange={(event) => setCandidateDomain(event.target.value)}><option value="all">All contributors</option>{domains.map((value) => <option value={value} key={value}>{label(value)}</option>)}</select></label>
          <label><span>Evaluation state</span><select value={evaluationState} onChange={(event) => setEvaluationState(event.target.value)}><option value="all">All evaluated</option><option value="with_alert">Related alert available</option><option value="without_alert">No related alert</option></select></label>
          <label><span>Sort</span><select value={sort} onChange={(event) => setSort(event.target.value as typeof sort)}><option value="newest">Newest first</option><option value="oldest">Oldest first</option><option value="evidence">Strongest evidence first</option></select></label>
          <button type="button" className="button-secondary analysis-reset" onClick={resetFilters}><FilterX aria-hidden="true" />Reset filters</button>
        </div>
      </details>

      <div className="analysis-list-heading">
        <div><h3>Stored investigations</h3><p>{displayedRisks.length.toLocaleString()} matching evaluated record(s) · active baseline {label(baselineState)}</p></div>
      </div>

      {linkResolutionState === "loading" && <div className="analysis-detail-state" role="status"><span className="loading-spinner" aria-hidden="true" />Resolving the linked alert and its source feature window…</div>}
      {linkResolutionState === "error" && <div className="analysis-detail-state analysis-detail-state--error" role="alert"><XCircle aria-hidden="true" /><div><strong>The linked alert is unavailable.</strong><p>SmartOps did not substitute unrelated current evidence.</p></div></div>}
      {displayedRisks.length === 0 ? (
        <div className="analysis-empty" role="status"><Gauge aria-hidden="true" /><strong>No matching evaluated investigation</strong><p>Adjust the filters or allow completed five-minute evidence to be evaluated. SmartOps does not invent a cause when evidence is unavailable, and no RCA record is not a guarantee that the computer is healthy.</p></div>
      ) : (
        <div className="analysis-investigation-list">
          {displayedRisks.slice(0, visibleCount).map((item) => {
            const relatedAlert = alertsByWindow.get(item.feature_window_id);
            const topCandidate = item.candidates[0] ?? null;
            const isExpanded = expandedWindowId === item.feature_window_id;
            const supportCount = item.candidates.reduce((total, candidate) => total + candidate.supporting_metrics.length + candidate.supporting_events.length, 0);
            return (
              <article className={`investigation-card${isExpanded ? " investigation-card--expanded" : ""}`} key={item.id}>
                <div className="investigation-summary">
                  <div className={`investigation-icon investigation-icon--${severityTone(relatedAlert?.current_severity ?? item.evidence_level)}`} aria-hidden="true"><Workflow /></div>
                  <div className="investigation-primary">
                    <span>Investigation #{item.id} · window #{item.feature_window_id}</span>
                    <h4>{relatedAlert ? relatedAlert.title : `${label(item.evidence_level)} operational evidence`}</h4>
                    <p>{topCandidate ? `Highest-ranked possible contributor: ${label(topCandidate.candidate_domain)}` : "No evidence-supported contributor was stored."}</p>
                  </div>
                  <dl className="investigation-facts">
                    <div><dt>Evaluated</dt><dd>{formatTimestamp(item.evaluated_at_utc)}</dd></div>
                    <div><dt>Workload</dt><dd>{label(item.workload_context)}</dd></div>
                    <div><dt>Evidence</dt><dd>{item.candidates.length} contributor(s) · {supportCount} support item(s)</dd></div>
                    <div><dt>State</dt><dd><span className={`analysis-chip analysis-chip--${severityTone(item.evidence_level)}`}>Evaluated · {label(item.evidence_level)}</span></dd></div>
                  </dl>
                  <button type="button" className="analysis-toggle" aria-expanded={isExpanded} aria-controls={`investigation-detail-${item.feature_window_id}`} onClick={() => toggleDetails(item.feature_window_id)}>{isExpanded ? <ChevronUp aria-hidden="true" /> : <ChevronDown aria-hidden="true" />}{isExpanded ? "Hide analysis" : "View analysis"}</button>
                </div>

                {isExpanded && (
                  <div id={`investigation-detail-${item.feature_window_id}`} className="investigation-detail" role="region" aria-label={`Investigation ${item.id} analysis`}>
                    {detailState === "loading" && <div className="analysis-detail-state" role="status"><span className="loading-spinner" aria-hidden="true" />Loading this investigation from the local database…</div>}
                    {detailState === "error" && <div className="analysis-detail-state analysis-detail-state--error" role="alert"><XCircle aria-hidden="true" /><div><strong>Investigation details are temporarily unavailable.</strong><p>{detailError}</p><button type="button" onClick={() => setDetailRequestVersion((value) => value + 1)}>Retry</button></div></div>}
                    {detailState === "ready" && detail && detail.risk.feature_window_id === item.feature_window_id && (
                      <InvestigationDetails detail={detail} formatTimestamp={formatTimestamp} />
                    )}
                  </div>
                )}
              </article>
            );
          })}
          {visibleCount < displayedRisks.length && <button type="button" className="button-secondary analysis-load-more" onClick={() => setVisibleCount((value) => value + 12)}>Load 12 more investigations</button>}
        </div>
      )}
    </section>
  );
}

function InvestigationDetails({ detail, formatTimestamp }: { detail: DetailState; formatTimestamp: (value: string) => string }) {
  const { risk, candidates, alert, deviation, health } = detail;
  const events = risk.components.flatMap((component) => component.evidence.events ?? []);
  const timeline = [
    { time: risk.window_start_utc, text: "Evidence window opened" },
    { time: risk.window_end_utc, text: "Evidence window completed" },
    { time: risk.evaluated_at_utc, text: "Risk evidence evaluated" },
    ...(alert ? [
      { time: alert.first_observed_utc, text: `${alert.alert_code} first observed` },
      { time: alert.latest_observed_utc, text: `${alert.alert_code} most recently observed` },
    ] : []),
    ...events.map((event) => ({ time: event.event_timestamp_utc, text: `${label(event.smartops_category)} event (${event.timing})` })),
  ].filter((entry, index, rows) => rows.findIndex((item) => item.time === entry.time && item.text === entry.text) === index)
    .sort((left, right) => Date.parse(left.time) - Date.parse(right.time));

  return (
    <div className="investigation-workspace">
      <div className="investigation-overview-grid">
        <article><span>Observed condition</span><strong>Risk Evidence {risk.risk_evidence_index.toFixed(1)} · {label(risk.evidence_level)}</strong><p>{label(risk.temporal_pattern)} across {risk.persistence_window_count} completed window(s).</p></article>
        <article><span>Evidence period</span><strong>{formatTimestamp(risk.window_start_utc)}</strong><p>to {formatTimestamp(risk.window_end_utc)} · quality {label(risk.data_quality_status)}</p></article>
        <article><span>Workload context</span><strong>{label(risk.workload_context)}</strong><p>{(risk.workload_confidence * 100).toFixed(0)}% stored workload confidence.</p></article>
        <article><span>Related states</span><strong>{health ? `${label(health.health_band)} health` : alert ? `${label(alert.state)} alert` : "No linked health or alert"}</strong><p>{health ? `${label(health.evaluation_state)} · score ${health.system_health_score?.toFixed(1) ?? "unavailable"}` : alert ? `${label(alert.current_severity)} · ${alert.alert_code}` : "The analysis remains available without fabricating a relationship."}</p></article>
      </div>

      <section className="investigation-section">
        <div className="investigation-section-heading"><div><p className="eyebrow">Ranked stored interpretation</p><h5>Likely contributing factors</h5></div><span>Evidence strength is not failure probability.</span></div>
        {candidates.length === 0 ? <div className="analysis-empty analysis-empty--compact"><CheckCircle2 aria-hidden="true" /><strong>No evidence-supported contributor was stored</strong><p>This evaluated window did not produce a ranked root-cause candidate.</p></div> : (
          <div className="contributor-ranking">
            {candidates.map((candidate) => {
              const confidence = Math.max(0, Math.min(100, candidate.evidence_confidence * 100));
              return <article key={candidate.id}>
                <div className="contributor-rank" title={`Rank ${candidate.rank}`}><ContributorIcon domain={candidate.candidate_domain} /><span>{candidate.rank}</span></div>
                <div className="contributor-content"><div><strong>{label(candidate.candidate_domain)}</strong><span>{candidate.explanation}</span></div><div className="contributor-bar" role="meter" aria-label={`${label(candidate.candidate_domain)} evidence support`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(confidence)}><i style={{ width: `${confidence}%` }} /></div><small>{confidence.toFixed(0)}% stored evidence support</small></div>
              </article>;
            })}
          </div>
        )}
      </section>

      <section className="investigation-section">
        <div className="investigation-section-heading"><div><p className="eyebrow">Personal-baseline comparison</p><h5>Observed values and expected ranges</h5></div><span>{deviation ? `Deviation assessment #${deviation.id}` : "Baseline comparison unavailable"}</span></div>
        {deviation?.feature_results.length ? <div className="table-wrap"><table><thead><tr><th>Metric</th><th>Observed</th><th>Baseline centre</th><th>Expected range</th><th>Difference</th><th>Stored interpretation</th></tr></thead><tbody>{deviation.feature_results.filter((result) => result.severity_band !== "not_evaluated").slice(0, 12).map((result) => <tr key={result.feature_name}><td>{label(result.feature_name)}</td><td>{result.observed_value === null ? "Unavailable" : result.observed_value.toFixed(2)}</td><td>{result.baseline_centre === null ? "Unavailable" : result.baseline_centre.toFixed(2)}</td><td>{result.expected_low === null || result.expected_high === null ? "Unavailable" : `${result.expected_low.toFixed(2)} – ${result.expected_high.toFixed(2)}`}</td><td>{result.observed_value === null || result.baseline_centre === null ? "Unavailable" : `${result.observed_value - result.baseline_centre >= 0 ? "+" : ""}${(result.observed_value - result.baseline_centre).toFixed(2)}`}</td><td>{label(result.reason_code)}</td></tr>)}</tbody></table></div> : <div className="analysis-empty analysis-empty--compact"><Info aria-hidden="true" /><strong>Personal-baseline comparison unavailable</strong><p>No stored deviation feature results were returned for this window. Values are not inferred from another period.</p></div>}
      </section>

      <div className="investigation-columns">
        <section className="investigation-section"><h5>Supporting and contradicting evidence</h5>{candidates.length ? candidates.map((candidate) => <div className="candidate-evidence" key={candidate.id}><strong>{label(candidate.candidate_domain)}</strong><ul>{candidate.supporting_metrics.map((evidence, index) => <li key={`support-${candidate.id}-${index}`}><CheckCircle2 aria-hidden="true" /><span><b>{label(evidence.evidence_key)}</b>: {evidenceValue(evidence.observed_value)} · {label(evidence.reason_code)}</span></li>)}{candidate.supporting_events.map((evidence, index) => <li key={`event-${candidate.id}-${index}`}><AlertTriangle aria-hidden="true" /><span>{label(evidence.reason_code)}</span></li>)}{candidate.contradictory_evidence.map((evidence, index) => <li className="evidence-healthy" key={`contradict-${candidate.id}-${index}`}><ShieldCheck aria-hidden="true" /><span>{label(evidence.reason_code)} · {evidenceValue(evidence.observed_value)}</span></li>)}</ul></div>) : <p>No candidate evidence is stored.</p>}</section>
        <section className="investigation-section"><h5>Stored evidence relationships</h5><div className="relationship-map" aria-label="Stored evidence relationship diagram"><div>Observed telemetry</div>{deviation && <><span aria-hidden="true">→</span><div>Baseline deviation</div></>}<span aria-hidden="true">→</span><div>Ranked hypotheses</div>{health && <><span aria-hidden="true">→</span><div>Related health</div></>}{alert && <><span aria-hidden="true">→</span><div>Related alert</div></>}</div><p>These are stored associations from the evaluated feature window. They support investigation but do not establish causality.</p><dl className="evidence-component-list">{risk.components.filter((component) => component.contribution > 0).map((component) => <div key={component.id}><dt>{label(component.correlation_group || component.component_name)}</dt><dd>+{component.contribution.toFixed(2)} · {label(component.reason_code)}</dd></div>)}</dl></section>
      </div>

      <section className="investigation-section"><h5>Temporal sequence</h5><ol className="evidence-timeline">{timeline.map((entry, index) => <li key={`${entry.time}-${entry.text}-${index}`}><i aria-hidden="true" /><div><strong>{entry.text}</strong><time dateTime={entry.time}>{formatTimestamp(entry.time)}</time></div></li>)}</ol></section>

      <div className="investigation-columns">
        <section className="investigation-section"><h5>Missing evidence and limitations</h5>{alert?.excluded_inputs.length ? <ul>{alert.excluded_inputs.map((input) => <li key={`${input.input}-${input.status}`}><strong>{label(input.input)}</strong>: {label(input.status)}{input.reason ? ` · ${label(input.reason)}` : ""}</li>)}</ul> : <p>No alert-specific excluded input was recorded.</p>}{candidates.flatMap((candidate) => candidate.limitations).length ? <ul>{[...new Set(candidates.flatMap((candidate) => candidate.limitations))].map((limitation) => <li key={limitation}>{limitation}</li>)}</ul> : <p>Telemetry correlation cannot prove causality.</p>}</section>
        <section className="investigation-section"><h5>Safe diagnostic checks</h5>{candidates.flatMap((candidate) => candidate.recommended_verification_steps).length ? <ol>{[...new Set(candidates.flatMap((candidate) => candidate.recommended_verification_steps))].map((step) => <li key={step}>{step}</li>)}</ol> : alert?.diagnostic_recommendations.length ? <ol>{alert.diagnostic_recommendations.map((step) => <li key={step}>{step}</li>)}</ol> : <p>No diagnostic check was stored for this window.</p>}</section>
      </div>

      <nav className="analysis-related-links" aria-label="Related SmartOps records">
        <a href={`#/system-health?windowId=${risk.feature_window_id}`}><Gauge aria-hidden="true" />Open matching System Health record<ExternalLink aria-hidden="true" /></a>
        {alert && <a href={`#/predictive-alerts?alertId=${alert.id}`}><AlertTriangle aria-hidden="true" />Open alert {alert.alert_code}<ExternalLink aria-hidden="true" /></a>}
        <a href={`#/live-monitoring?start=${encodeURIComponent(risk.window_start_utc)}&end=${encodeURIComponent(risk.window_end_utc)}`}><Activity aria-hidden="true" />Inspect Live Monitoring period<ExternalLink aria-hidden="true" /></a>
      </nav>

      <details className="technical-record-details"><summary>Technical record identifiers and versions</summary><dl><div><dt>Risk assessment</dt><dd>#{risk.id}</dd></div><div><dt>Feature window</dt><dd>#{risk.feature_window_id}</dd></div><div><dt>Algorithm</dt><dd>{risk.algorithm_version ?? "Unavailable"}</dd></div><div><dt>Configuration</dt><dd>{risk.configuration_version ?? "Unavailable"}</dd></div><div><dt>Catalogue</dt><dd>{risk.catalogue_version ?? "Unavailable"}</dd></div><div><dt>Baseline version</dt><dd>{risk.baseline_version_id ?? "Unavailable"}</dd></div><div><dt>Workload rule</dt><dd>{risk.workload_rule_version ?? "Unavailable"}</dd></div></dl></details>
    </div>
  );
}
