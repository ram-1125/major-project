import { AlertLifecycleTimeline } from "./AlertLifecycleTimeline";

type ValidationRecord = {
  validation_type: string;
  display_label: string;
  sample_size: number;
  accuracy: number | null;
  precision: number | null;
  recall: number | null;
  f1_score: number | null;
  false_positive_rate: number | null;
  dataset_description?: string;
  validation_date_utc?: string | null;
  limitations: string[];
};

type ExplanationSnapshot = {
  baseline_version?: number | null;
  evidence_start_utc: string;
  evidence_end_utc: string;
  source_sample_count: number;
  evidence_completeness: number;
  triggering_rule_identifier: string;
  triggering_rule_version: string;
  observed_values: Record<string, unknown>;
  baseline_values: Record<string, unknown>;
  thresholds: Record<string, unknown>;
  deviations: Array<Record<string, unknown>>;
  plain_language_explanation: string;
  explanation_version: string;
  alert_confidence: number | null;
  confidence_components: Array<{
    component_key: string;
    component_value: number | null;
    configured_weight: number;
    weighted_contribution: number | null;
    availability_status: string;
    explanation: string;
  }>;
  validation: ValidationRecord;
};

export type AlertDetailRecord = {
  id: number;
  alert_code: string;
  category: string;
  title: string;
  current_severity: string;
  peak_severity: string;
  state: string;
  evaluation_state: string;
  data_confidence: number;
  workload_context: string | null;
  workload_confidence: number | null;
  first_observed_utc: string;
  latest_observed_utc: string;
  resolved_at_utc: string | null;
  source_feature_window_id: number;
  probable_factors: Array<{ domain: string; rank: number; confidence: number | null }>;
  contradictory_evidence: string[];
  excluded_inputs: Array<{ input: string; status: string; reason: string | null }>;
  algorithm_version: string;
  configuration_version: string;
  catalogue_version: string;
  evidence: Array<{
    id: number;
    evidence_key: string;
    suppressed: boolean;
    suppression_reason: string | null;
    explanation: string;
  }>;
  diagnostic_recommendations: string[];
  preventive_guidance: string[];
  short_alert_basis: string;
  alert_confidence: number | null;
  confidence_label: string | null;
  validation: ValidationRecord;
  explanation_snapshot: ExplanationSnapshot | null;
  outcome: {
    new_outcome: "pending" | "confirmed" | "false_positive" | "inconclusive";
    event_timestamp_utc: string;
    optional_note: string | null;
  } | null;
  transitions?: Array<{
    id: number;
    transition_timestamp_utc: string;
    previous_state: string | null;
    new_state: string;
    transition_type: string;
  }>;
  occurrences?: Array<{
    id: number;
    observed_at_utc: string;
    severity: string;
  }>;
  notification_deliveries?: Array<{
    id: number;
    attempted_at_utc: string;
    delivery_status: "attempting" | "delivered" | "failed";
    notification_type: "activation" | "escalation";
    severity: string;
  }>;
};

type RelatedRisk = {
  risk_evidence_index: number;
  evidence_level: string;
  temporal_pattern: string;
} | null;

type RelatedCandidate = {
  id: number;
  candidate_domain: string;
  explanation: string;
};

function words(value: string): string {
  return value.replaceAll("_", " ");
}

function validationPercent(value: number | null | undefined): string {
  return value == null ? "Not available" : `${(value * 100).toFixed(1)}%`;
}

function storedValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "Unavailable";
  if (typeof value === "number") return Number.isFinite(value) ? value.toLocaleString() : "Unavailable";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function EvidenceValues({
  title,
  values,
}: {
  title: string;
  values: Record<string, unknown> | null;
}) {
  const entries = Object.entries(values ?? {});
  return (
    <section className="alert-evidence-values">
      <h4>{title}</h4>
      {entries.length === 0 ? (
        <p>Unavailable for this stored alert.</p>
      ) : (
        <dl>
          {entries.map(([key, value]) => (
            <div key={key}>
              <dt>{words(key)}</dt>
              <dd>{storedValue(value)}</dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}

export function AlertDetails({
  alert,
  relatedRisk,
  relatedCandidates,
  outcomePending,
  formatTimestamp,
  onLabelOutcome,
  onOpenRootCause,
}: {
  alert: AlertDetailRecord;
  relatedRisk: RelatedRisk;
  relatedCandidates: RelatedCandidate[];
  outcomePending: boolean;
  formatTimestamp: (value: string) => string;
  onLabelOutcome: (
    outcome: "pending" | "confirmed" | "false_positive" | "inconclusive",
  ) => void;
  onOpenRootCause: () => void;
}) {
  const snapshot = alert.explanation_snapshot;
  const validation = snapshot?.validation ?? alert.validation;
  const hasRegisteredValidation = validation.validation_type !== "not_yet_validated";

  return (
    <div className="alert-expanded-content">
      <section className="alert-detail-summary" aria-label={`Details for ${alert.title}`}>
        <h4>Alert details</h4>
        <dl className="alert-validation-grid">
          <div><dt>Category</dt><dd>{words(alert.category)}</dd></div>
          <div><dt>Current severity</dt><dd>{words(alert.current_severity)}</dd></div>
          <div><dt>Lifecycle state</dt><dd>{words(alert.state)}</dd></div>
          <div><dt>Workload profile</dt><dd>{alert.workload_context ? words(alert.workload_context) : "Unavailable"}</dd></div>
          <div><dt>Evidence evaluation</dt><dd>{words(alert.evaluation_state)}</dd></div>
          <div><dt>Data confidence</dt><dd>{alert.data_confidence.toFixed(1)}%</dd></div>
          <div><dt>First observed</dt><dd>{formatTimestamp(alert.first_observed_utc)}</dd></div>
          <div><dt>Latest observed</dt><dd>{formatTimestamp(alert.latest_observed_utc)}</dd></div>
        </dl>
      </section>

      <section className="alert-explanation-content">
        <h4>Why the alert was generated</h4>
        <p>{snapshot?.plain_language_explanation ?? alert.short_alert_basis ?? "Detailed explanation is unavailable for this legacy alert."}</p>
        <dl className="alert-facts">
          <div><dt>Evidence period</dt><dd>{snapshot ? `${formatTimestamp(snapshot.evidence_start_utc)} to ${formatTimestamp(snapshot.evidence_end_utc)}` : "Unavailable for this legacy alert"}</dd></div>
          <div><dt>Completeness</dt><dd>{snapshot ? `${(snapshot.evidence_completeness * 100).toFixed(1)}% from ${snapshot.source_sample_count} samples` : "Unavailable"}</dd></div>
          <div><dt>Alert-specific confidence</dt><dd>{alert.alert_confidence == null ? "Unavailable" : `${alert.alert_confidence.toFixed(1)}%${alert.confidence_label ? ` (${alert.confidence_label})` : ""}`}</dd></div>
        </dl>

        <div className="alert-evidence-value-grid">
          <EvidenceValues title="Observed values or conditions" values={snapshot?.observed_values ?? null} />
          <EvidenceValues title="Expected personal baseline or range" values={snapshot?.baseline_values ?? null} />
          <EvidenceValues title="Recorded differences or deviations" values={snapshot ? { deviations: snapshot.deviations } : null} />
        </div>

        <h4>Alert confidence basis</h4>
        <p>Evidence confidence describes recorded evidence quality, completeness, and consistency. It is not failure probability or accuracy.</p>
        {snapshot?.confidence_components?.length ? (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Component</th><th>Value</th><th>Weight</th><th>Contribution</th><th>Basis or limitation</th></tr></thead>
              <tbody>
                {snapshot.confidence_components.map((component) => (
                  <tr key={component.component_key}>
                    <td>{words(component.component_key)}</td>
                    <td>{component.component_value === null ? "Unavailable" : `${component.component_value.toFixed(1)}%`}</td>
                    <td>{(component.configured_weight * 100).toFixed(1)}%</td>
                    <td>{component.weighted_contribution === null ? "Excluded" : component.weighted_contribution.toFixed(2)}</td>
                    <td>{component.explanation}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p>Confidence-component details are unavailable for this stored alert.</p>
        )}

        <h4>Method-level validation</h4>
        {!hasRegisteredValidation ? (
          <div className="alert-validation-empty">
            <strong>Not yet validated</strong>
            <p>Individual alert correctness requires confirmed outcomes. No score, deviation, risk value, or confidence value is presented as accuracy.</p>
          </div>
        ) : (
          <dl className="alert-validation-grid">
            <div><dt>Precision</dt><dd>{validationPercent(validation.precision)}</dd></div>
            <div><dt>Recall</dt><dd>{validationPercent(validation.recall)}</dd></div>
            <div><dt>F1 score</dt><dd>{validationPercent(validation.f1_score)}</dd></div>
            <div><dt>False-positive rate</dt><dd>{validationPercent(validation.false_positive_rate)}</dd></div>
            <div><dt>Accuracy</dt><dd>{validationPercent(validation.accuracy)}</dd></div>
            <div><dt>Labelled sample/event count</dt><dd>{validation.sample_size.toLocaleString()}</dd></div>
            <div><dt>Validation date</dt><dd>{validation.validation_date_utc ? formatTimestamp(validation.validation_date_utc) : "Not available"}</dd></div>
            <div><dt>Dataset / experiment</dt><dd>{validation.dataset_description ?? "Not available"}</dd></div>
          </dl>
        )}
        {validation.limitations.length > 0 && (
          <p className="alert-validation-limitations"><strong>Validation limitations:</strong> {validation.limitations.join(" ")}</p>
        )}

        <details className="alert-technical-details">
          <summary>Technical Details</summary>
          <dl className="alert-facts">
            <div><dt>Alert identifier</dt><dd>{alert.id}</dd></div>
            <div><dt>Source feature window</dt><dd>{alert.source_feature_window_id}</dd></div>
            <div><dt>Rule/model version</dt><dd>{snapshot ? `${snapshot.triggering_rule_identifier} · ${snapshot.triggering_rule_version}` : "Unavailable"}</dd></div>
            <div><dt>Baseline version</dt><dd>{snapshot?.baseline_version == null ? "Unavailable" : `v${snapshot.baseline_version}`}</dd></div>
            <div><dt>Explanation version</dt><dd>{snapshot?.explanation_version ?? "Unavailable"}</dd></div>
            <div><dt>Stored record versions</dt><dd>{alert.algorithm_version} · {alert.configuration_version} · {alert.catalogue_version}</dd></div>
          </dl>
        </details>
      </section>

      <section className="alert-detail-section">
        <h4>Actual lifecycle and notification history</h4>
        <AlertLifecycleTimeline
          firstObservedUtc={alert.first_observed_utc}
          transitions={alert.transitions}
          occurrences={alert.occurrences}
          deliveries={alert.notification_deliveries}
          outcome={alert.outcome}
          formatTimestamp={formatTimestamp}
        />
        {!(alert.notification_deliveries?.length) && <p>No notification delivery was recorded for this alert.</p>}
      </section>

      <div className="alert-detail-grid">
        <div>
          <h4>Probable contributing factors</h4>
          {alert.probable_factors.length ? (
            <ol>{alert.probable_factors.map((factor) => (
              <li key={`${factor.rank}-${factor.domain}`}>{words(factor.domain)}{factor.confidence === null ? "" : ` · ${(factor.confidence * 100).toFixed(0)}% evidence confidence`}</li>
            ))}</ol>
          ) : <p>Unavailable for this stored alert.</p>}
          <h4>Supporting alert evidence</h4>
          {alert.evidence.length ? (
            <ul>{alert.evidence.map((item) => (
              <li key={item.id}><strong>{words(item.evidence_key)}</strong>: {item.explanation}{item.suppressed ? ` Excluded from an additional contribution (${words(item.suppression_reason ?? "recorded correlation suppression")}).` : ""}</li>
            ))}</ul>
          ) : <p>No detailed supporting-evidence rows were recorded.</p>}
        </div>
        <div>
          <h4>Diagnostic verification</h4>
          {alert.diagnostic_recommendations.length ? <ol>{alert.diagnostic_recommendations.map((item) => <li key={item}>{item}</li>)}</ol> : <p>No diagnostic steps were recorded.</p>}
          <h4>Preventive guidance</h4>
          {alert.preventive_guidance.length ? <ul>{alert.preventive_guidance.map((item) => <li key={item}>{item}</li>)}</ul> : <p>No preventive guidance was recorded.</p>}
        </div>
      </div>

      {(alert.contradictory_evidence.length > 0 || alert.excluded_inputs.length > 0) && (
        <details>
          <summary>Contradictory evidence and excluded inputs</summary>
          <ul>
            {alert.contradictory_evidence.map((item) => <li key={item}>{item}</li>)}
            {alert.excluded_inputs.map((item) => (
              <li key={`${item.input}-${item.status}`}>{words(item.input)}: {words(item.status)}{item.reason ? ` · ${words(item.reason)}` : ""}</li>
            ))}
          </ul>
        </details>
      )}

      <section className="notification-linked-evidence">
        <h4>Related Root-Cause Analysis evidence</h4>
        <p>Risk Evidence Index: <strong>{relatedRisk ? relatedRisk.risk_evidence_index.toFixed(1) : "Not evaluated for this window"}</strong>{relatedRisk ? ` · ${words(relatedRisk.evidence_level)} · ${words(relatedRisk.temporal_pattern)}` : ""}</p>
        {relatedCandidates.length > 0 && <ol>{relatedCandidates.map((candidate) => <li key={candidate.id}><strong>{words(candidate.candidate_domain)}</strong>: {candidate.explanation}</li>)}</ol>}
        <a href={`#/root-cause-analysis?alertId=${alert.id}`} onClick={onOpenRootCause}>Open the complete root-cause evidence workspace</a>
      </section>

      <div className="alert-outcome-control">
        <span><strong>User outcome:</strong> {alert.outcome?.new_outcome ? words(alert.outcome.new_outcome) : "Pending"}</span>
        <div role="group" aria-label={`Outcome label for alert ${alert.id}`}>
          {(["pending", "confirmed", "false_positive", "inconclusive"] as const).map((outcome) => (
            <button type="button" key={outcome} disabled={outcomePending} onClick={() => onLabelOutcome(outcome)}>{words(outcome)}</button>
          ))}
        </div>
      </div>
    </div>
  );
}
