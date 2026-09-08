type ValidationRecord = { validation_type: string; display_label: string; sample_size: number; precision: number | null; limitations: string[] };
type ExplanationSnapshot = { baseline_version?: number | null; evidence_start_utc: string; evidence_end_utc: string; source_sample_count: number; evidence_completeness: number; observed_values: Record<string, unknown>; baseline_values: Record<string, unknown>; deviations: Array<Record<string, unknown>>; plain_language_explanation: string; alert_confidence: number | null; validation: ValidationRecord };

export type AlertDetailRecord = {
  id: number; alert_code: string; category: string; title: string; current_severity: string; peak_severity: string; state: string; evaluation_state: string; data_confidence: number; workload_context: string | null; first_observed_utc: string; latest_observed_utc: string; resolved_at_utc: string | null; source_feature_window_id: number;
  probable_factors: Array<{ domain: string; rank: number; confidence: number | null }>;
  contradictory_evidence: string[]; excluded_inputs: Array<{ input: string; status: string; reason: string | null }>;
  evidence: Array<{ id: number; evidence_key: string; suppressed: boolean; explanation: string }>;
  diagnostic_recommendations: string[]; preventive_guidance: string[]; short_alert_basis: string; alert_confidence: number | null; confidence_label: string | null; validation: ValidationRecord; explanation_snapshot: ExplanationSnapshot | null;
  outcome: { new_outcome: "pending" | "confirmed" | "false_positive" | "inconclusive"; event_timestamp_utc: string; optional_note: string | null } | null;
  material_occurrence?: { id: number; observed_at_utc: string; severity: string } | null;
};

type RelatedRisk = { risk_evidence_index: number; evidence_level: string; temporal_pattern: string } | null;
type RelatedCandidate = { id: number; candidate_domain: string; explanation: string };

function words(value: string): string { return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()); }
function userText(value: string): string {
  return value
    .replace(/Phase 3A/gi, "personal-baseline")
    .replace(/Phase 3B/gi, "operational risk-evidence")
    .replace(/Phase 7B(?:\.1)?/gi, "Advanced System Signal");
}
function severityLabel(value: string): string {
  return ({ informational: "Low", advisory: "Elevated", warning: "High", urgent: "Critical Evidence" } as Record<string, string>)[value] ?? "Unknown severity";
}

function firstUsefulDeviation(snapshot: ExplanationSnapshot | null): string | null {
  if (!snapshot) return null;
  const entry = snapshot.deviations.find((value) => {
    const difference = value.difference ?? value.deviation ?? value.delta;
    return typeof difference === "number" && Number.isFinite(difference);
  });
  if (!entry) return null;
  const metric = String(entry.metric ?? entry.metric_name ?? entry.feature_name ?? "The recorded value");
  const difference = Number(entry.difference ?? entry.deviation ?? entry.delta);
  const unit = metric.includes("percent") || metric.includes("ratio") ? " percentage points" : "";
  return `${words(metric)} was approximately ${Math.abs(difference).toFixed(1)}${unit} ${difference >= 0 ? "above" : "below"} this computer's expected ${snapshot.baseline_version ? `baseline v${snapshot.baseline_version}` : "personal baseline"} range.`;
}

const OUTCOME_OPTIONS = [
  ["pending", "Pending", "I have not checked this alert yet."],
  ["confirmed", "Confirmed", "I observed a real issue matching this alert."],
  ["false_positive", "False positive", "I checked the computer and found no meaningful issue matching this alert."],
  ["inconclusive", "Inconclusive", "I could not determine whether the alert was correct."],
] as const;

export function AlertDetails({ alert, relatedRisk, relatedCandidates, outcomePending, formatTimestamp, onLabelOutcome, onOpenRootCause }: {
  alert: AlertDetailRecord; relatedRisk: RelatedRisk; relatedCandidates: RelatedCandidate[]; outcomePending: boolean; formatTimestamp: (value: string) => string;
  onLabelOutcome: (outcome: "pending" | "confirmed" | "false_positive" | "inconclusive") => void; onOpenRootCause: () => void;
}) {
  const snapshot = alert.explanation_snapshot;
  const validation = snapshot?.validation ?? alert.validation;
  const deviationSentence = firstUsefulDeviation(snapshot);
  const probableContributor = alert.probable_factors[0];
  const riskContributor = relatedCandidates[0];
  const diagnostics = [...new Set(alert.diagnostic_recommendations.map(userText))].slice(0, 5);
  const prevention = [...new Set(alert.preventive_guidance.map(userText))].slice(0, 4);
  const limitations = [...new Set([...alert.contradictory_evidence.map(userText), ...alert.excluded_inputs.map((item) => `${words(item.input)}: ${words(item.status)}`)])].slice(0, 4);
  return <div id={`alert-details-${alert.id}`} className="alert-expanded-content" aria-label={`Details for ${alert.title}`}>
    <section className="alert-user-summary"><h4>What SmartOps detected</h4><p>{userText(snapshot?.plain_language_explanation ?? alert.short_alert_basis ?? "Detailed trigger evidence was not recorded for this legacy alert.")}</p>{deviationSentence && <p className="alert-baseline-sentence">{deviationSentence}</p>}
      <dl className="alert-facts alert-facts--compact"><div><dt>Seriousness</dt><dd>{severityLabel(alert.current_severity)}</dd></div><div><dt>Still active?</dt><dd>{words(alert.state)}</dd></div><div><dt>Workload</dt><dd>{alert.workload_context ? words(alert.workload_context) : "Context not recorded"}</dd></div><div><dt>Latest meaningful evidence</dt><dd>{formatTimestamp(alert.material_occurrence?.observed_at_utc ?? alert.latest_observed_utc)}</dd></div><div><dt>Evidence strength</dt><dd>{alert.alert_confidence == null ? "Not available for this stored alert" : `${alert.alert_confidence.toFixed(0)}% · ${words(alert.confidence_label ?? "recorded")}`}</dd></div><div><dt>Method validation</dt><dd>{validation.display_label ?? "Not yet validated"}</dd></div></dl>
    </section>
    {(probableContributor || riskContributor) && <section className="alert-user-summary"><h4>Most relevant recorded contributor</h4><p><strong>{words(probableContributor?.domain ?? riskContributor!.candidate_domain)}</strong>{riskContributor && !probableContributor ? ` — ${userText(riskContributor.explanation)}` : " was the highest-ranked stored contributor."}</p></section>}
    <div className="alert-detail-grid"><section><h4>What to check</h4>{diagnostics.length ? <ol>{diagnostics.map((item) => <li key={item}>{item}</li>)}</ol> : <ol><li>Open Task Manager.</li><li>Sort the relevant CPU or Memory column.</li><li>Check whether one application remains unusually high.</li><li>Observe whether usage returns toward its normal range.</li></ol>}</section><section><h4>Safe preventive guidance</h4>{prevention.length ? <ul>{prevention.map((item) => <li key={item}>{item}</li>)}</ul> : <p>Save current work, close applications you do not need, and seek technical help if the same warning repeatedly returns.</p>}</section></div>
    {limitations.length > 0 && <details><summary>Evidence limitations</summary><ul>{limitations.map((item) => <li key={item}>{item}</li>)}</ul></details>}
    <nav className="alert-context-links" aria-label="Related alert records"><a href={`#/root-cause-analysis?alertId=${alert.id}`} onClick={onOpenRootCause}>Open Root-Cause Analysis</a><a href={`#/technical-evidence?dataset=alerts&contextId=${encodeURIComponent(String(alert.id))}`}>View exact Technical Evidence</a></nav>
    <section className="alert-outcome-control"><h4>What happened after this alert?</h4><p>Your latest selection: <strong>{words(alert.outcome?.new_outcome ?? "pending")}</strong>. This review does not alter monitoring or the personal baseline.</p><div className="outcome-choice-grid" role="group" aria-label={`Outcome label for alert ${alert.id}`}>{OUTCOME_OPTIONS.map(([value, label, explanation]) => <button type="button" key={value} disabled={outcomePending} aria-pressed={(alert.outcome?.new_outcome ?? "pending") === value} onClick={() => onLabelOutcome(value)}><strong>{label}</strong><span>{explanation}</span></button>)}</div></section>
    <details className="alert-lifecycle-summary"><summary>Lifecycle summary</summary><p>Triggered {formatTimestamp(alert.first_observed_utc)}. Latest observation {formatTimestamp(alert.latest_observed_utc)}.{alert.resolved_at_utc ? ` Resolved ${formatTimestamp(alert.resolved_at_utc)}.` : " This condition has not been recorded as resolved."}</p></details>
    <p className="alert-science-note">Evidence strength describes completeness and consistency; it is not failure probability or accuracy. Exact values, versions, occurrences, transitions and delivery history are available in Technical Evidence.</p>
    {relatedRisk && <p className="visually-hidden">Related Risk Evidence Index {relatedRisk.risk_evidence_index.toFixed(1)}; {words(relatedRisk.evidence_level)}.</p>}
  </div>;
}
