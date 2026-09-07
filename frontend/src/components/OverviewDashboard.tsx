import {
  Activity,
  BadgeCheck,
  BellRing,
  BrainCircuit,
  CircleCheckBig,
  Clock3,
  Cpu,
  Database,
  Gauge,
  HardDrive,
  HeartPulse,
  RefreshCw,
  ScanSearch,
  Settings,
  ShieldCheck,
  TriangleAlert,
  Wifi,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import { useMemo, useState, type CSSProperties } from "react";

import { PIPELINE_ICONS } from "./icons";
import { InteractiveLineChart, type LiveState } from "./InteractiveLineChart";
import {
  buildHealthTrend,
  buildRecentActivity,
  buildWorkloadHeatmap,
  CANONICAL_SEVERITIES,
  healthTone,
  pipelineTone,
  selectQualityProfiles,
  type OverviewAlert,
  type OverviewFeature,
  type OverviewHealth,
  type OverviewPipelineStage,
  type OverviewQualityProfile,
  type RecentActivityItem,
} from "../overviewData";
import { formatHeadroomScore } from "../profileQuality";

type Metric = {
  timestamp_utc: string;
  cpu_percent: number | null;
  ram_percent: number | null;
  disk_percent: number | null;
  disk_read_bytes_per_second: number | null;
  disk_write_bytes_per_second: number | null;
  network_upload_bytes_per_second: number | null;
  network_download_bytes_per_second: number | null;
};

type Workload = {
  workload_class: string;
  workload_confidence: number | null;
};

type HealthAssessment = OverviewHealth & {
  data_confidence: number;
  components: Array<{
    component_name: string;
    component_score: number;
  }>;
};

type RiskAssessment = {
  risk_evidence_index: number;
  evidence_level: string;
  evaluated_at_utc: string;
};

type PipelineStage = OverviewPipelineStage & {
  expected_interval_seconds: number | null;
  processing_duration_ms: number | null;
  failure_or_overdue_reason: string | null;
  active_baseline_version?: number | null;
};

type Props = {
  latest: Metric;
  totalSamples: number;
  sampleAgeMs: number | null;
  isStale: boolean;
  chartHistory: Metric[];
  workload: Workload | null;
  features: OverviewFeature[];
  baselineState: string | null;
  latestHealth: HealthAssessment | null;
  healthHistory: HealthAssessment[];
  latestRisk: RiskAssessment | null;
  riskReason: string | null;
  fineQuality: { profiles: OverviewQualityProfile[] } | null;
  alertStatus: {
    status: string;
    open_alert_count: number;
    active_severity_counts: Record<string, number>;
  } | null;
  alerts: OverviewAlert[];
  pipelineStatus: { generated_at_utc: string; stages: PipelineStage[] } | null;
  liveState: LiveState;
  onRefresh: () => void;
  isRefreshing: boolean;
};

const PIPELINE_LABELS: Record<string, string> = {
  telemetry_collection: "System Data Collection",
  feature_window_generation: "Five-Minute Analysis",
  active_baseline: "Personal Baseline",
  risk_evaluation: "Risk Analysis",
  health_evaluation: "Health Analysis",
  predictive_alert_evaluation: "Predictive Alerts",
};

const PIPELINE_STATE_LABELS: Record<string, string> = {
  processing: "Processing",
  successfully_waiting: "Healthy",
  overdue: "Overdue",
  failed: "Failed",
  not_applicable: "Not applicable",
  not_yet_executed: "Not yet run",
};

const SEVERITY_LABELS: Record<string, string> = {
  informational: "Low",
  advisory: "Elevated",
  warning: "High",
  urgent: "Critical Evidence",
};

const SEVERITY_COLORS: Record<string, string> = {
  informational: "#168fd2",
  advisory: "#d99a24",
  warning: "#ed6a2c",
  urgent: "#d94343",
};

const WORKLOAD_CLASSES: Record<string, string> = {
  idle: "overview-heat--idle",
  interactive_light: "overview-heat--interactive",
  browser_media: "overview-heat--browser",
  development: "overview-heat--development",
  guided_development: "overview-heat--guided",
  office_productivity: "overview-heat--office",
  background_activity: "overview-heat--background",
  compute_intensive: "overview-heat--compute",
  gaming_3d: "overview-heat--gaming",
};

const ACTIVITY_ICONS: Record<RecentActivityItem["kind"], LucideIcon> = {
  collection: Database,
  analysis: Workflow,
  risk: ScanSearch,
  health: HeartPulse,
  alert: BellRing,
  baseline: BrainCircuit,
};

function words(value: string | null | undefined): string {
  if (!value) return "Unavailable";
  return value
    .replaceAll("_", " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function localTimestamp(value: string | null | undefined): string {
  if (!value) return "Unavailable";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Unavailable";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function duration(ageMs: number | null): string {
  if (ageMs === null) return "Unavailable";
  const seconds = Math.max(0, Math.round(ageMs / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  return `${Math.round(seconds / 3600)}h ago`;
}

function rate(value: number | null): string {
  if (value === null) return "Unavailable";
  return `${(value / 1_000_000).toFixed(value >= 10_000_000 ? 1 : 2)} MB/s`;
}

function bounded(value: number): number {
  return Math.min(100, Math.max(0, value));
}

function KpiCard({
  icon: Icon,
  label,
  value,
  status,
  explanation,
  timestamp,
  href,
  tone,
}: {
  icon: LucideIcon;
  label: string;
  value: string;
  status: string;
  explanation: string;
  timestamp?: string | null;
  href: string;
  tone: "success" | "information" | "warning" | "critical" | "neutral";
}) {
  return (
    <a className={`overview-kpi overview-kpi--${tone}`} href={href}>
      <span className="overview-kpi__icon" aria-hidden="true"><Icon size={20} /></span>
      <span className="overview-kpi__label">{label}</span>
      <strong>{value}</strong>
      <span className="overview-kpi__status">{status}</span>
      <small>{explanation}</small>
      {timestamp && <time dateTime={timestamp}>{localTimestamp(timestamp)}</time>}
    </a>
  );
}

function HealthGauge({ assessment }: { assessment: HealthAssessment | null }) {
  const valid = assessment?.system_health_score != null
    && assessment.evaluation_state !== "not_evaluated";
  const score = valid ? bounded(assessment!.system_health_score!) : 0;
  const tone = healthTone(assessment?.health_band);
  const style = valid
    ? ({ "--overview-gauge-value": `${score * 3.6}deg` } as CSSProperties)
    : undefined;
  const label = valid
    ? `System Health Score ${score.toFixed(0)} out of 100, ${words(assessment!.health_band)}`
    : "System Health Score not evaluated";
  return (
    <a
      className={`overview-health-gauge overview-health-gauge--${tone}${valid ? "" : " overview-health-gauge--empty"}`}
      href="#/system-health"
      aria-label={`${label}. Open System Health.`}
      style={style}
    >
      <span className="overview-health-gauge__inner">
        <HeartPulse aria-hidden="true" size={18} />
        <strong>{valid ? score.toFixed(0) : "—"}</strong>
        <small>{valid ? words(assessment!.health_band) : "Not evaluated"}</small>
      </span>
    </a>
  );
}

function Pipeline({ status }: { status: Props["pipelineStatus"] }) {
  const [openStage, setOpenStage] = useState<string | null>(null);
  const stages = status?.stages ?? [];
  return (
    <section className="overview-panel overview-pipeline" aria-labelledby="overview-pipeline-title">
      <header className="overview-section-heading">
        <div>
          <span>Automatic local pipeline</span>
          <h2 id="overview-pipeline-title">Live pipeline status</h2>
          <p>Each ring is driven by genuine backend stage state.</p>
        </div>
        <time dateTime={status?.generated_at_utc}>{localTimestamp(status?.generated_at_utc)}</time>
      </header>
      {stages.length === 0 ? (
        <div className="overview-empty">Pipeline status has not been returned.</div>
      ) : (
        <ol className="overview-pipeline__stages" aria-label="SmartOps automatic pipeline">
          {stages.map((stage) => {
            const Icon = PIPELINE_ICONS[stage.stage_key] ?? Activity;
            const tone = pipelineTone(stage.current_state);
            const detailId = `pipeline-detail-${stage.stage_key}`;
            const expanded = openStage === stage.stage_key;
            return (
              <li key={stage.stage_key} className={`overview-pipeline-stage overview-pipeline-stage--${tone}`}>
                <button
                  type="button"
                  className="overview-pipeline-ring"
                  aria-label={`${PIPELINE_LABELS[stage.stage_key] ?? words(stage.stage_key)}: ${PIPELINE_STATE_LABELS[stage.current_state] ?? words(stage.current_state)}. Show technical details.`}
                  aria-expanded={expanded}
                  aria-controls={detailId}
                  onClick={() => setOpenStage(expanded ? null : stage.stage_key)}
                >
                  <Icon className="overview-pipeline-ring__icon" aria-hidden="true" size={23} />
                </button>
                <span className="overview-pipeline-stage__tooltip" role="tooltip">
                  <strong>{PIPELINE_STATE_LABELS[stage.current_state] ?? words(stage.current_state)}</strong>
                  <span>Attempt: {localTimestamp(stage.last_attempted_at_utc)}</span>
                  <span>Success: {localTimestamp(stage.last_successful_at_utc)}</span>
                  <span>Duration: {stage.processing_duration_ms == null ? "Unavailable" : `${stage.processing_duration_ms.toFixed(0)} ms`}</span>
                </span>
                <strong>{PIPELINE_LABELS[stage.stage_key] ?? words(stage.stage_key)}</strong>
                <small>{PIPELINE_STATE_LABELS[stage.current_state] ?? words(stage.current_state)}</small>
                <details
                  id={detailId}
                  open={expanded}
                  onToggle={(event) => {
                    if (event.currentTarget.open) setOpenStage(stage.stage_key);
                    else if (openStage === stage.stage_key) setOpenStage(null);
                  }}
                >
                  <summary>Technical Details</summary>
                  <dl>
                    <div><dt>Last attempt</dt><dd>{localTimestamp(stage.last_attempted_at_utc)}</dd></div>
                    <div><dt>Last success</dt><dd>{localTimestamp(stage.last_successful_at_utc)}</dd></div>
                    <div><dt>Duration</dt><dd>{stage.processing_duration_ms == null ? "Unavailable" : `${stage.processing_duration_ms.toFixed(1)} ms`}</dd></div>
                    <div><dt>Expected interval</dt><dd>{stage.expected_interval_seconds == null ? "Not applicable" : `${stage.expected_interval_seconds}s`}</dd></div>
                    {stage.active_baseline_version != null && <div><dt>Active baseline</dt><dd>v{stage.active_baseline_version}</dd></div>}
                  </dl>
                  {stage.failure_or_overdue_reason && <p>{words(stage.failure_or_overdue_reason)}</p>}
                </details>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}

function ResourceChart({ latest, isStale }: { latest: Metric; isStale: boolean }) {
  const rows = [
    { label: "CPU", value: latest.cpu_percent, icon: Cpu },
    { label: "Memory", value: latest.ram_percent, icon: BrainCircuit },
    { label: "Storage capacity used", value: latest.disk_percent, icon: HardDrive },
  ];
  return (
    <article className="overview-panel overview-chart-card" aria-labelledby="resource-utilisation-title">
      <header className="overview-chart-heading">
        <div><h3 id="resource-utilisation-title">Resource utilisation</h3><p>Latest valid percentage values; throughput is shown separately.</p></div>
        <span className={isStale ? "overview-data-state overview-data-state--stale" : "overview-data-state"}>{isStale ? "Stale" : "Current"}</span>
      </header>
      <div className="overview-bar-list" role="img" aria-label="Current CPU, memory, and storage-capacity utilisation percentages">
        {rows.map(({ label, value, icon: Icon }) => (
          <div className="overview-bar-row" key={label}>
            <span><Icon aria-hidden="true" size={16} />{label}</span>
            <div className="overview-bar-track"><i style={{ width: `${bounded(value ?? 0)}%` }} data-unavailable={value == null ? "true" : undefined} /></div>
            <strong>{value == null ? "Unavailable" : `${value.toFixed(1)}%`}</strong>
          </div>
        ))}
      </div>
      <dl className="overview-throughput">
        <div><dt>Disk read / write</dt><dd>{rate(latest.disk_read_bytes_per_second)} / {rate(latest.disk_write_bytes_per_second)}</dd></div>
        <div><dt>Network down / up</dt><dd>{rate(latest.network_download_bytes_per_second)} / {rate(latest.network_upload_bytes_per_second)}</dd></div>
      </dl>
      <time dateTime={latest.timestamp_utc}>Sample {localTimestamp(latest.timestamp_utc)}</time>
    </article>
  );
}

function AlertDonut({ status, isStale }: { status: Props["alertStatus"]; isStale: boolean }) {
  const counts = CANONICAL_SEVERITIES.map((severity) => ({
    severity,
    count: Math.max(0, status?.active_severity_counts[severity] ?? 0),
  }));
  const total = counts.reduce((sum, item) => sum + item.count, 0);
  let cursor = 0;
  const slices = counts
    .filter((item) => item.count > 0)
    .map((item) => {
      const start = (cursor / total) * 100;
      cursor += item.count;
      const end = (cursor / total) * 100;
      return `${SEVERITY_COLORS[item.severity]} ${start}% ${end}%`;
    });
  return (
    <article className="overview-panel overview-chart-card" aria-labelledby="alert-distribution-title">
      <header className="overview-chart-heading">
        <div><h3 id="alert-distribution-title">Alert severity distribution</h3><p>Current durable alerts by canonical SmartOps severity.</p></div>
        <span className={isStale ? "overview-data-state overview-data-state--stale" : "overview-data-state"}>{isStale ? "Stale" : "Current"}</span>
      </header>
      {total === 0 ? (
        <div className="overview-donut-empty"><span aria-hidden="true" /><strong>No active alerts</strong><p>No current evaluated alert is stored.</p></div>
      ) : (
        <div className="overview-donut-layout">
          <div
            className="overview-donut"
            style={{ background: `conic-gradient(${slices.join(", ")})` }}
            role="img"
            aria-label={`${total} active alerts: ${counts.map((item) => `${SEVERITY_LABELS[item.severity]} ${item.count}`).join(", ")}`}
          ><span><strong>{total}</strong><small>active</small></span></div>
          <ul className="overview-donut-legend">
            {counts.map((item) => <li key={item.severity}><i style={{ background: SEVERITY_COLORS[item.severity] }} /><span>{SEVERITY_LABELS[item.severity]}</span><strong>{item.count}</strong></li>)}
          </ul>
        </div>
      )}
      <a className="overview-panel-link" href="#/predictive-alerts">View alert lifecycle</a>
    </article>
  );
}

function QualityComparison({ profiles }: { profiles: OverviewQualityProfile[] }) {
  const selected = useMemo(() => selectQualityProfiles(profiles), [profiles]);
  return (
    <article className="overview-panel overview-chart-card" aria-labelledby="quality-comparison-title">
      <header className="overview-chart-heading">
        <div><h3 id="quality-comparison-title">Current Workload Headroom comparison</h3><p>Recent qualifying workload periods; not-evaluated profiles are excluded.</p></div>
        <span className="overview-data-state">Current assessments</span>
      </header>
      {selected.length === 0 ? <div className="overview-empty">No evaluated workload-headroom profile is available.</div> : (
        <div className="overview-quality-bars" role="img" aria-label={`Evaluated workload headroom scores: ${selected.map((item) => `${item.name} ${formatHeadroomScore(item.assessment!.profile_quality_score)}`).join(", ")}`}>
          {selected.map((profile) => <div key={profile.key} className="overview-quality-row"><span>{profile.name}</span><div><i style={{ width: `${bounded(profile.assessment!.profile_quality_score!)}%` }} /></div><strong>{formatHeadroomScore(profile.assessment!.profile_quality_score)}</strong></div>)}
        </div>
      )}
      <a className="overview-panel-link" href="#/pc-quality-check">View all 30 workload profiles</a>
    </article>
  );
}

function WorkloadHeatmap({ features }: { features: OverviewFeature[] }) {
  const slots = useMemo(() => buildWorkloadHeatmap(features), [features]);
  const observed = slots.filter((slot) => slot.feature !== null).length;
  return (
    <article className="overview-panel overview-chart-card overview-chart-card--wide" aria-labelledby="workload-heatmap-title">
      <header className="overview-chart-heading">
        <div><h3 id="workload-heatmap-title">Workload activity</h3><p>Stored five-minute workload observations across the last 24 hours.</p></div>
        <span className="overview-data-state">{observed} observed periods</span>
      </header>
      {slots.length === 0 ? <div className="overview-empty">No workload analysis period is available.</div> : <>
        <div className="overview-heatmap" role="grid" aria-label="Workload activity heatmap. Empty cells represent missing periods.">
          {slots.map((slot) => <a
            key={slot.timestamp_utc}
            href={slot.feature ? "#/live-monitoring" : undefined}
            className={`overview-heat ${slot.workload ? WORKLOAD_CLASSES[slot.workload] ?? "overview-heat--other" : "overview-heat--missing"}`}
            aria-label={`${localTimestamp(slot.timestamp_utc)}: ${slot.workload ? words(slot.workload) : "No stored analysis period"}`}
            title={`${localTimestamp(slot.timestamp_utc)} · ${slot.workload ? words(slot.workload) : "No stored analysis period"}`}
            role="gridcell"
            tabIndex={slot.feature ? 0 : -1}
          />)}
        </div>
        <div className="overview-heatmap-axis"><span>{localTimestamp(slots[0].timestamp_utc)}</span><span>5-minute periods</span><span>{localTimestamp(slots.at(-1)?.timestamp_utc)}</span></div>
        <div className="overview-heatmap-legend" aria-label="Workload colour legend">
          {Object.entries(WORKLOAD_CLASSES).map(([name, className]) => <span key={name}><i className={className} />{words(name)}</span>)}
          <span><i className="overview-heat--missing" />Missing</span>
        </div>
      </>}
    </article>
  );
}

function ComponentComparison({ health }: { health: HealthAssessment | null }) {
  const components = health?.components ?? [];
  return (
    <article className="overview-panel overview-chart-card" aria-labelledby="health-components-title">
      <header className="overview-chart-heading">
        <div><h3 id="health-components-title">Health components</h3><p>Stored component scores supporting the current operating condition.</p></div>
        <span className="overview-data-state">0–100 score</span>
      </header>
      {components.length === 0 ? <div className="overview-empty">No evaluated health-component scores are available.</div> : <div className="overview-quality-bars" role="img" aria-label={components.map((item) => `${words(item.component_name)} ${item.component_score.toFixed(0)}`).join(", ")}>
        {components.map((component) => <div className="overview-quality-row" key={component.component_name}><span>{words(component.component_name)}</span><div><i style={{ width: `${bounded(component.component_score)}%` }} /></div><strong>{component.component_score.toFixed(0)}</strong></div>)}
      </div>}
      <a className="overview-panel-link" href="#/system-health">Inspect deductions and evidence</a>
    </article>
  );
}

export function OverviewDashboard(props: Props) {
  const {
    latest, totalSamples, sampleAgeMs, isStale, workload, latestHealth,
    healthHistory, latestRisk, riskReason, fineQuality,
    alertStatus, alerts, pipelineStatus, features, liveState, onRefresh,
    isRefreshing, baselineState,
  } = props;
  const [healthRange, setHealthRange] = useState<"1h" | "24h">("24h");
  const healthTrend = useMemo(() => {
    const all = buildHealthTrend(healthHistory);
    const newest = all.at(-1)?.timestamp_utc;
    const anchor = newest ? new Date(newest).getTime() : Date.now();
    const start = anchor - (healthRange === "1h" ? 3_600_000 : 86_400_000);
    return all.filter((item) => new Date(item.timestamp_utc).getTime() >= start);
  }, [healthHistory, healthRange]);
  const recentActivity = useMemo(
    () => buildRecentActivity(pipelineStatus?.stages ?? [], alerts, latest.timestamp_utc),
    [pipelineStatus, alerts, latest.timestamp_utc],
  );
  const healthValid = latestHealth?.system_health_score != null
    && latestHealth.evaluation_state !== "not_evaluated";
  const healthStatus = healthValid ? words(latestHealth!.health_band) : "Not evaluated";
  const riskStatus = latestRisk ? words(latestRisk.evidence_level) : "Not evaluated";
  const qualityProfile = fineQuality?.profiles.find((profile) => profile.key === "device") ?? null;
  const qualityScore = qualityProfile?.assessment?.profile_quality_score ?? null;
  const qualityTimestamp = qualityProfile?.assessment?.assessed_at_utc ?? null;
  const systemStatus = isStale
    ? "Needs attention"
    : healthTone(latestHealth?.health_band) === "critical"
      ? "Health attention"
      : "Monitoring normally";

  return (
    <section className="overview-command" aria-label="SmartOps Overview command dashboard">
      <section className="overview-command__hero" aria-labelledby="overview-command-title">
        <div className="overview-command__hero-copy">
          <span className="overview-command__eyebrow"><ShieldCheck aria-hidden="true" size={15} /> Local predictive PC monitoring</span>
          <h2 id="overview-command-title">SmartOps System Overview</h2>
          <p>See the current operating condition, evidence readiness, workload context, and anything that needs attention—entirely on this computer.</p>
          <div className="overview-command__hero-status">
            <span><CircleCheckBig aria-hidden="true" size={15} />{systemStatus}</span>
            <span><Gauge aria-hidden="true" size={15} />{words(workload?.workload_class)}</span>
            <span><BrainCircuit aria-hidden="true" size={15} />Personal Baseline: {baselineState === "ready" || baselineState === "established" ? "Ready" : words(baselineState)}</span>
            <span className={isStale ? "is-stale" : ""}><Clock3 aria-hidden="true" size={15} />{isStale ? "Data stale" : `Fresh · ${duration(sampleAgeMs)}`}</span>
          </div>
          <div className="overview-command__hero-actions">
            <a href="#/system-health">Open System Health</a>
            <a href="#/predictive-alerts">Review Alerts</a>
          </div>
          <small>System Health describes the current observed operating condition. It is not a failure probability.</small>
        </div>
        <div className="overview-command__hero-gauge">
          <HealthGauge assessment={latestHealth} />
          <dl>
            <div><dt>Current risk</dt><dd>{latestRisk ? latestRisk.risk_evidence_index.toFixed(0) : "Not evaluated"}</dd></div>
            <div><dt>Open alerts</dt><dd>{alertStatus?.open_alert_count ?? 0}</dd></div>
          </dl>
        </div>
      </section>

      <section className="overview-kpi-grid" aria-label="Primary SmartOps indicators">
        <KpiCard icon={HeartPulse} label="System Health" value={healthValid ? latestHealth!.system_health_score!.toFixed(0) : "Not evaluated"} status={healthStatus} explanation="Current observed operating condition" timestamp={latestHealth?.assessed_at_utc} href="#/system-health" tone={healthTone(latestHealth?.health_band)} />
        <KpiCard icon={ScanSearch} label="Risk Evidence" value={latestRisk ? latestRisk.risk_evidence_index.toFixed(0) : "Not evaluated"} status={riskStatus} explanation={latestRisk ? "Strength of available operational evidence" : words(riskReason)} timestamp={latestRisk?.evaluated_at_utc} href="#/root-cause-analysis" tone={latestRisk ? (latestRisk.risk_evidence_index >= 70 ? "critical" : latestRisk.risk_evidence_index >= 40 ? "warning" : "information") : "neutral"} />
        <KpiCard icon={BellRing} label="Predictive Alerts" value={`${alertStatus?.open_alert_count ?? 0}`} status={alertStatus?.status === "evaluated" ? "Evaluated" : "Not evaluated"} explanation="Open evidence-based alert lifecycles" href="#/predictive-alerts" tone={(alertStatus?.active_severity_counts.urgent ?? 0) > 0 ? "critical" : (alertStatus?.active_severity_counts.warning ?? 0) > 0 ? "warning" : alertStatus?.status === "evaluated" ? "success" : "neutral"} />
        <KpiCard icon={BadgeCheck} label="Current Workload Headroom" value={formatHeadroomScore(qualityScore)} status={qualityScore == null ? "Not evaluated" : words(qualityProfile?.evaluation_state)} explanation="Most recent qualifying Device workload period" timestamp={qualityTimestamp} href="#/pc-quality-check?profile=device" tone={qualityScore == null ? "neutral" : qualityScore >= 80 ? "success" : qualityScore >= 60 ? "information" : "warning"} />
        <KpiCard icon={Activity} label="Current Workload" value={words(workload?.workload_class)} status={workload?.workload_confidence == null ? "Confidence unavailable" : `${(workload.workload_confidence * 100).toFixed(0)}% confidence`} explanation="Latest transparent workload classification" href="#/live-monitoring" tone={workload ? "information" : "neutral"} />
        <KpiCard icon={Database} label="Data Freshness" value={isStale ? "Stale" : "Fresh"} status={`${totalSamples.toLocaleString()} records`} explanation={`Latest sample ${duration(sampleAgeMs)}`} timestamp={latest.timestamp_utc} href="#/live-monitoring" tone={isStale ? "warning" : "success"} />
      </section>

      <Pipeline status={pipelineStatus} />

      <section className="overview-charts" aria-label="SmartOps charts">
        <article className="overview-panel overview-chart-card overview-chart-card--wide overview-health-trend">
          <header className="overview-chart-heading">
            <div><h3>System Health trend</h3><p>Evaluated health over time. Not-evaluated periods remain visible gaps.</p></div>
            <div className="overview-range" aria-label="Health history range">
              <button type="button" aria-pressed={healthRange === "1h"} onClick={() => setHealthRange("1h")}>Last hour</button>
              <button type="button" aria-pressed={healthRange === "24h"} onClick={() => setHealthRange("24h")}>Last 24 hours</button>
            </div>
          </header>
          {isStale && <p className="overview-chart-warning"><TriangleAlert aria-hidden="true" size={15} />History is available, but the newest source data is stale.</p>}
          <InteractiveLineChart
            title="Current operating condition"
            data={healthTrend}
            series={[{ label: "System Health", color: "#168fd2", value: (item) => item.score }]}
            fixedMaximum={100}
            axisFormatter={(value) => `${value.toFixed(0)}`}
            expandable
            liveState={liveState}
          />
          <div className="overview-health-bands" aria-label="Health band references"><span>Critical 0–29</span><span>Degraded 30–49</span><span>Attention 50–69</span><span>Stable 70–84</span><span>Good 85–100</span></div>
          <p className="overview-accessible-summary">{healthTrend.filter((item) => item.score !== null).length} evaluated values and {healthTrend.filter((item) => item.score === null).length} not-evaluated gaps in this range.</p>
        </article>
        <ResourceChart latest={latest} isStale={isStale} />
        <AlertDonut status={alertStatus} isStale={isStale} />
        <QualityComparison profiles={fineQuality?.profiles ?? []} />
        <ComponentComparison health={latestHealth} />
        <WorkloadHeatmap features={features} />
      </section>

      <section className="overview-lower-grid">
        <article className="overview-panel overview-quick-actions" aria-labelledby="quick-actions-title">
          <header className="overview-section-heading"><div><span>Shortcuts</span><h2 id="quick-actions-title">Quick actions</h2><p>Open existing SmartOps functions without changing analytical state.</p></div></header>
          <div>
            <a href="#/predictive-alerts"><BellRing aria-hidden="true" />View Predictive Alerts</a>
            <a href="#/system-health"><HeartPulse aria-hidden="true" />Open System Health</a>
            <a href="#/pc-quality-check"><BadgeCheck aria-hidden="true" />Open PC Quality</a>
            <a href="#/pc-quality-check?section=hardware-suitability"><Cpu aria-hidden="true" />Hardware Workload Suitability</a>
            <button type="button" onClick={onRefresh} disabled={isRefreshing}><RefreshCw aria-hidden="true" className={isRefreshing ? "is-spinning" : ""} />{isRefreshing ? "Refreshing…" : "Refresh Data"}</button>
            <a href="#/live-monitoring"><Activity aria-hidden="true" />View Technical Diagnostics</a>
            <a href="#/settings?section=notifications"><Settings aria-hidden="true" />Manage Notifications</a>
            <a href="#/settings?section=monitoring-data"><BrainCircuit aria-hidden="true" />View Personal Baseline</a>
          </div>
        </article>
        <article className="overview-panel overview-activity" aria-labelledby="recent-activity-title">
          <header className="overview-section-heading"><div><span>Stored and runtime evidence</span><h2 id="recent-activity-title">Recent SmartOps activity</h2><p>Newest genuine stage successes and alert observations.</p></div></header>
          {recentActivity.length === 0 ? <div className="overview-empty">No suitable recent activity is available.</div> : <ol>
            {recentActivity.map((item) => {
              const Icon = ACTIVITY_ICONS[item.kind];
              return <li key={item.key} className={`overview-activity--${item.tone}`}><span aria-hidden="true"><Icon size={16} /></span><div><a href={item.href}>{item.description}</a><time dateTime={item.timestamp_utc}>{localTimestamp(item.timestamp_utc)}</time></div></li>;
            })}
          </ol>}
        </article>
      </section>
    </section>
  );
}
