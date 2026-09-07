import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  BatteryCharging,
  ChevronDown,
  ChevronUp,
  Gauge,
  HardDrive,
  Info,
  ListFilter,
  MemoryStick,
  Network,
  RefreshCw,
  Thermometer,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";

import { AppShell } from "./components/AppShell";
import { OverviewDashboard } from "./components/OverviewDashboard";
import { LiveMonitoringSummary } from "./components/LiveMonitoringSummary";
import { AlertDetails } from "./components/AlertDetails";
import { AlertLifecycleTimeline } from "./components/AlertLifecycleTimeline";
import { RootCauseDashboard } from "./components/RootCauseDashboard";
import { SystemHealthDashboard } from "./components/SystemHealthDashboard";
import {
  InteractiveLineChart,
  type LiveState,
} from "./components/InteractiveLineChart";
import {
  alertDeepLinkFromHash,
  routeFromHash,
  type AppRoute,
} from "./navigation";
import {
  formatPreferenceTimestamp,
  initialRoute,
  loadDashboardPreferences,
  saveDashboardPreferences,
  saveLastOpenedPage,
  type DashboardPreferences,
} from "./preferences";
import { isStableQualityProfile } from "./searchCatalogue";
import { METRIC_ICONS } from "./components/icons";
import {
  CURRENT_HEADROOM_MEANING,
  detectabilityLabel,
  evidenceIndependenceLabel,
  formatEffectiveWeight,
  formatHeadroomScore,
  weightedDeduction,
} from "./profileQuality";

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";
const REFRESH_INTERVAL_MS = 30_000;
const STALE_AFTER_MS = 90_000;
const PAGE_SIZE = 25;

type DiskPartition = {
  mountpoint: string;
  percent: number | null;
  used_bytes: number | null;
  free_bytes: number | null;
  total_bytes: number | null;
};

type Metric = {
  id: number;
  timestamp_utc: string;
  device_id: string;
  cpu_percent: number | null;
  cpu_per_core_percent: number[] | null;
  cpu_physical_cores: number | null;
  cpu_logical_cores: number | null;
  cpu_frequency_mhz: number | null;
  process_count: number | null;
  thread_count: number | null;
  ram_percent: number | null;
  ram_used_bytes: number | null;
  ram_available_bytes: number | null;
  ram_total_bytes: number | null;
  swap_percent: number | null;
  swap_used_bytes: number | null;
  swap_total_bytes: number | null;
  disk_percent: number | null;
  disk_used_bytes: number | null;
  disk_free_bytes: number | null;
  disk_total_bytes: number | null;
  disk_partitions: DiskPartition[] | null;
  disk_read_bytes_per_second: number | null;
  disk_write_bytes_per_second: number | null;
  disk_read_ops_per_second: number | null;
  disk_write_ops_per_second: number | null;
  network_upload_bytes_per_second: number | null;
  network_download_bytes_per_second: number | null;
  network_packets_sent_per_second: number | null;
  network_packets_received_per_second: number | null;
  network_interface_available: boolean | null;
  battery_percent: number | null;
  battery_charging: boolean | null;
  ac_power_connected: boolean | null;
  battery_seconds_remaining: number | null;
  uptime_seconds: number | null;
  boot_timestamp_utc: string | null;
  user_idle_seconds: number | null;
  user_state: "active" | "idle" | null;
  foreground_process_name: string | null;
  cpu_temperature_celsius: number | null;
  gpu_utilization_percent: number | null;
  gpu_memory_percent: number | null;
  gpu_temperature_celsius: number | null;
  workload_class: string | null;
  workload_confidence: number | null;
  workload_reasons: string[] | null;
  user_activity_state: "active" | "idle" | null;
  system_activity_state: "quiescent" | "background" | "busy" | null;
  workload_rule_version: string | null;
};

type HistoryResponse = {
  items: Metric[];
  total: number;
  limit: number;
  offset: number;
  sort: "oldest" | "newest";
  start: string | null;
  end: string | null;
};

type ProcessRow = {
  rank: number;
  pid: number;
  process_name: string;
  cpu_percent: number | null;
  memory_percent: number | null;
};

type ProcessResponse = {
  metric_id: number;
  timestamp_utc: string;
  cpu: ProcessRow[];
  memory: ProcessRow[];
};

type WorkloadRow = {
  timestamp_utc: string;
  workload_class: string;
  workload_confidence: number | null;
  reason_codes: string[];
  user_activity_state: "active" | "idle" | null;
  system_activity_state: "quiescent" | "background" | "busy" | null;
  workload_rule_version: string | null;
};

type EventRow = {
  id: number;
  event_timestamp_utc: string;
  channel: string;
  provider_name: string;
  event_id: number;
  event_level: string;
  smartops_category: string;
  safe_summary: string;
};

type EventSummary = {
  severity: Record<string, number>;
  categories: Record<string, number>;
  channels: Array<{ channel: string; available: boolean; status: string }>;
};

type FeatureRow = {
  id: number;
  window_start_utc: string;
  window_end_utc: string;
  sample_count: number;
  expected_sample_count: number;
  coverage_ratio: number;
  is_complete: boolean;
  dominant_workload_class: string | null;
  dominant_user_activity_state: string | null;
  dominant_system_activity_state: string | null;
  workload_rule_version: string | null;
  workload_distribution: Record<string, number> | null;
  workload_majority_explanation: string | null;
  workload_composition: {
    sample_count: number;
    expected_sample_count: number;
    profiles: Record<string, { count: number; proportion: number }>;
    secondary_context: string | null;
    secondary_rule_version: string;
    reason_codes: string[];
  } | null;
  secondary_workload_context: string | null;
  secondary_workload_rule_version: string | null;
  secondary_workload_reason_codes: string[];
  cpu_avg: number | null;
  ram_avg: number | null;
  critical_event_count: number;
  error_event_count: number;
  warning_event_count: number;
};

type BaselineProfile = {
  id: number;
  workload_scope: string;
  eligible_window_count: number;
  distinct_day_count: number;
  readiness_state: string;
  updated_at_utc: string;
  isolation_forest: {
    readiness_state: string;
    training_count?: number;
  };
};

type BaselineStatus = {
  state: string;
  device_id: string | null;
  eligible_window_count: number;
  distinct_collection_days: number;
  minimum_device_windows: number;
  minimum_workload_windows: number;
  minimum_distinct_days: number;
  recommended_history_days: number;
  preferred_history_days: number;
  device_profile: BaselineProfile | null;
  workload_profiles: BaselineProfile[];
  last_successful_training_utc: string | null;
};

type DeviationFeature = {
  feature_name: string;
  observed_value: number | null;
  baseline_centre: number | null;
  expected_low: number | null;
  expected_high: number | null;
  normalized_deviation_score: number | null;
  severity_band: string;
  reason_code: string;
};

type DeviationAssessment = {
  id: number;
  feature_window_id: number;
  evaluation_timestamp_utc: string;
  baseline_scope: string;
  baseline_readiness: string;
  isolation_forest_score: number | null;
  isolation_forest_result: string;
  deviation_index: number | null;
  overall_level: string;
  top_contributing_metrics: Array<{
    feature_name: string;
    score: number;
    severity: string;
    reason_code: string;
  }>;
  reason_codes: string[];
  workload_context: string | null;
  data_quality_status: string;
  feature_results: DeviationFeature[];
};

type DeviationLatestResponse = {
  status: "evaluated" | "not_evaluated";
  assessment: DeviationAssessment | null;
  reason_code?: string;
  baseline_state?: string;
};

type CorrelatedEvent = {
  id: number;
  event_timestamp_utc: string;
  event_level: string;
  smartops_category: string;
  safe_summary: string;
  timing: "before" | "during" | "after";
};

type RiskComponent = {
  id: number;
  component_name: string;
  correlation_group: string;
  raw_value: number | null;
  normalized_value: number | null;
  weight: number;
  contribution: number;
  reason_code: string;
  evidence: {
    events?: CorrelatedEvent[];
    [key: string]: unknown;
  };
};

type CandidateEvidence = {
  evidence_kind: string;
  evidence_key: string;
  observed_value: unknown;
  supports_candidate: boolean;
  reason_code: string;
};

type RootCauseCandidate = {
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

type RiskAssessment = {
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
  candidates: RootCauseCandidate[];
  score_reconstruction: number;
};

type RiskLatestResponse = {
  status: "evaluated" | "not_evaluated";
  assessment: RiskAssessment | null;
  reason_code?: string;
  baseline_state?: string;
  prerequisites?: string[];
};

type RiskStatus = {
  status: "evaluated" | "not_evaluated";
  reason_code: string | null;
  baseline_state: string;
  deviation_assessment_count: number;
  risk_assessment_count: number;
  prerequisites: string[];
  minimum_coverage: number;
  minimum_workload_confidence: number;
};

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
  availability_status:
    | "available"
    | "unavailable_optional"
    | "unavailable_required"
    | "excluded_by_context"
    | "not_applicable";
  observed_value: unknown;
  excluded_reason: string | null;
  applicable_weight: number;
};

type HealthGuidanceItem = {
  guidance_type: string;
  related_component: string;
  sequence: number;
  guidance_text: string;
};

type HealthAssessment = {
  id: number;
  feature_window_id: number;
  window_start_utc: string;
  window_end_utc: string;
  assessed_at_utc: string;
  system_health_score: number | null;
  health_band:
    | "good"
    | "stable"
    | "attention"
    | "degraded"
    | "critical_condition"
    | "not_evaluated";
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
  guidance: {
    explanations: HealthGuidanceItem[];
    recommendations: HealthGuidanceItem[];
    improvements: HealthGuidanceItem[];
    limitations: HealthGuidanceItem[];
  };
  baseline_reference: { id: number } | null;
  deviation_reference: { id: number } | null;
  risk_reference: { id: number } | null;
  score_reconstruction: number | null;
};

type HealthLatestResponse = {
  status: "not_evaluated" | "provisional" | "established";
  assessment: HealthAssessment | null;
  reason_codes?: string[];
  interpretation: string;
};

type HealthStatus = {
  status: "not_evaluated" | "provisional" | "established";
  reason_codes: string[];
  assessment_counts: Record<string, number>;
  algorithm_version: string;
  configuration_version: string;
  interpretation: string;
};

type InventoryValue = {
  field_name: string;
  component_group: string;
  value: unknown;
  availability_status: string;
  reliability_note: string | null;
  source_name: string;
};

type InventorySnapshot = {
  id: number;
  captured_at_utc: string;
  last_checked_at_utc: string;
  detection_confidence: number;
  inventory_state: string;
  provider_version: string;
  values: InventoryValue[];
  by_field: Record<string, InventoryValue>;
  unavailable_or_unreliable: InventoryValue[];
};

type QualityProfile = {
  key: string;
  name: string;
  description: string;
  intended_workload: string;
  profile_version: string;
  configuration_version: string;
  catalogue_version: string;
  components: Record<string, {
    required: boolean;
    weight: number;
    minimum: unknown;
    recommended: unknown;
    unit: string;
  }>;
  limitations: string[];
};

type QualityComponent = {
  component_name: string;
  detected_value: unknown;
  detection_status: string;
  minimum_threshold: unknown;
  recommended_threshold: unknown;
  raw_component_score: number | null;
  effective_weight: number;
  minimum_passed: boolean | null;
  recommended_passed: boolean | null;
  hard_gate_status: string;
  explanation: string;
  limitations: string[];
};

type QualityAssessment = {
  id: number;
  inventory_snapshot_id: number;
  inventory_timestamp_utc: string;
  assessed_at_utc: string;
  profile_key: string;
  profile_name: string;
  profile_version: string;
  suitability_index: number | null;
  suitability_result: string;
  evaluation_state: "assessed" | "provisional" | "not_evaluated";
  detection_confidence: number;
  available_component_weight: number;
  excluded_component_weight: number;
  normalization_method: string;
  algorithm_version: string;
  configuration_version: string;
  catalogue_version: string;
  reason_codes: string[];
  explanation: string;
  limitations: string[];
  components: QualityComponent[];
  gates_and_caps: Array<{
    component_name: string;
    rule_type: string;
    applied: boolean;
    configured_cap: number | null;
    explanation: string;
  }>;
  limiting_components: Array<{
    component_name: string;
    rank: number;
    severity: string;
    explanation: string;
  }>;
  recommendations: Array<{
    component_name: string;
    priority: string;
    rank: number;
    explanation: string;
    expected_suitability_benefit: string;
    limitation: string;
  }>;
  current_operating_readiness: {
    health: {
      system_health_score: number | null;
      health_band: string;
      evaluation_state: string;
      data_confidence: number;
    } | null;
    risk: {
      risk_evidence_index: number;
      evidence_level: string;
    } | null;
    newest_health_window_complete: boolean | null;
    separation: string;
  };
  score_reconstruction: {
    weighted_score_before_caps: number;
    final_score_after_caps: number | null;
    stored_score: number | null;
    method: string;
  };
  excluded_components: QualityComponent[];
};

type QualityStatus = {
  status: "available" | "not_evaluated";
  assessment_counts: Record<string, number>;
  algorithm_version: string;
  configuration_version: string;
  catalogue_version: string;
  interpretation: string;
  privacy_excluded_fields: string[];
};

type QualityLatestResponse = {
  status: "assessed" | "provisional" | "not_evaluated";
  assessment: QualityAssessment | null;
  reason_codes: string[];
  interpretation: string;
};

type AlertEvidence = {
  id: number;
  evidence_type: string;
  evidence_key: string;
  correlation_group: string;
  raw_evidence: Record<string, unknown>;
  effective_evidence: Record<string, unknown>;
  suppressed: boolean;
  suppression_reason: string | null;
  explanation: string;
};

type PredictiveAlert = {
  summary_record?: boolean;
  id: number;
  alert_code: string;
  category: string;
  title: string;
  description: string;
  current_severity: "informational" | "advisory" | "warning" | "urgent";
  peak_severity: "informational" | "advisory" | "warning" | "urgent";
  state: "open" | "acknowledged" | "recovering" | "resolved";
  evaluation_state: string;
  data_confidence: number;
  workload_context: string | null;
  workload_confidence: number | null;
  first_observed_utc: string;
  latest_observed_utc: string;
  last_evidence_utc: string;
  occurrence_count: number;
  consecutive_window_count: number;
  duration_seconds: number;
  trend_direction: string;
  recovery_window_count: number;
  recovery_state: string;
  source_feature_window_id: number;
  probable_factors: Array<{
    domain: string;
    rank: number;
    confidence: number | null;
  }>;
  contradictory_evidence: string[];
  excluded_inputs: Array<{
    input: string;
    status: string;
    reason: string | null;
  }>;
  reason_codes: string[];
  algorithm_version: string;
  configuration_version: string;
  catalogue_version: string;
  acknowledged_at_utc: string | null;
  resolved_at_utc: string | null;
  evidence: AlertEvidence[];
  explanations: Array<{
    explanation_type: string;
    sequence: number;
    explanation_text: string;
  }>;
  diagnostic_recommendations: string[];
  preventive_guidance: string[];
  short_alert_basis: string;
  alert_confidence: number | null;
  confidence_label: "low" | "moderate" | "high" | null;
  explanation_snapshot: AlertExplanationSnapshot | null;
  validation: MethodValidation;
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
    previous_severity: string | null;
    new_severity: string;
    transition_type: string;
    reason_code: string;
  }>;
  occurrences?: Array<{
    id: number;
    observed_at_utc: string;
    severity: string;
    condition_met: number;
    temporal_pattern: string;
  }>;
  notification_deliveries?: Array<{
    id: number;
    attempted_at_utc: string;
    delivery_status: "attempting" | "delivered" | "failed";
    notification_type: "activation" | "escalation";
    severity: string;
    failure_reason: string | null;
  }>;
};

type MethodValidation = {
  rule_identifier?: string;
  rule_version?: string;
  applicable_workload_scope?: string;
  validation_type: string;
  display_label: string;
  sample_size: number;
  accuracy: number | null;
  precision: number | null;
  recall: number | null;
  specificity: number | null;
  f1_score: number | null;
  false_positive_rate: number | null;
  dataset_description?: string;
  validation_procedure?: string;
  validation_date_utc?: string | null;
  supporting_research?: Array<{ title?: string; url?: string; supports?: string }>;
  limitations: string[];
};

type AlertExplanationSnapshot = {
  baseline_version?: number | null;
  evidence_start_utc: string;
  evidence_end_utc: string;
  source_sample_count: number;
  evidence_completeness: number;
  triggering_rule_identifier: string;
  triggering_rule_version: string;
  triggering_metrics: string[];
  observed_values: Record<string, unknown>;
  baseline_values: Record<string, unknown>;
  thresholds: Record<string, unknown>;
  deviations: Array<Record<string, unknown>>;
  top_contributors: Array<Record<string, unknown>>;
  missing_evidence: Array<Record<string, unknown>>;
  plain_language_explanation: string;
  explanation_version: string;
  alert_confidence: number | null;
  confidence_label: string | null;
  confidence_calculation_version: string | null;
  confidence_components: Array<{
    component_key: string;
    component_value: number | null;
    configured_weight: number;
    weighted_contribution: number | null;
    availability_status: string;
    explanation: string;
  }>;
  validation: MethodValidation;
};

type AlertStatus = {
  status: "evaluated" | "not_evaluated";
  open_alert_count: number;
  state_counts: Record<string, number>;
  active_severity_counts: Record<string, number>;
  algorithm_version: string;
  configuration_version: string;
  catalogue_version: string;
  interpretation: string;
  latest_run?: { finished_at_utc: string | null } | null;
};

type NotificationStatus = {
  enabled: boolean;
  supported: boolean;
  status: "supported" | "unsupported" | "error";
  provider_name: string;
  reason: string | null;
  eligible_severities: Array<
    "informational" | "advisory" | "warning" | "urgent"
  >;
  eligible_categories: NotificationCategory[];
  semantic_severity_mapping: Record<string, string>;
  notification_category_mapping: Record<NotificationCategory, string>;
  delivery_counts: Record<string, number>;
  delivery_meaning: string;
};

type NotificationCategory = "advisory" | "warning" | "urgent";

type BaselineVersionProfile = {
  workload_scope: string;
  observed_window_count: number;
  eligible_window_count: number;
  excluded_window_count: number;
  distinct_day_count: number;
  sampling_completeness: number | null;
  readiness_state: string;
  last_learning_at_utc: string | null;
  reason_codes: string[];
  blocking_reason_codes: string[];
  blocking_reason: "none" | "none_not_required" | "eligible_window_count_below_minimum" | "distinct_collection_days_below_minimum" | "both_requirements_pending";
  informational_reason_codes: string[];
  applicability_state: string | null;
  applicability_reasons: string[];
  exclusion_reason_counts: Record<string, number>;
  membership_audit?: {
    historical_acceptance_events: number;
    audited_removal_events: number;
    reacceptance_events: number;
  };
};

type BaselineVersion = {
  id: number;
  version_number: number;
  version_label: string;
  lifecycle_state: string;
  learning_state: "collecting" | "paused" | "ready_for_validation" | "inactive";
  learning_state_explanation: string;
  previous_version_id: number | null;
  learning_started_at_utc: string | null;
  activated_at_utc?: string | null;
  last_learning_at_utc: string | null;
  last_new_eligible_window_utc: string | null;
  profiles: BaselineVersionProfile[];
};

type BaselineManagementStatus = {
  device_id: string | null;
  active_version: BaselineVersion | null;
  candidate_version: BaselineVersion | null;
  minimum_device_windows: number;
  minimum_workload_windows: number;
  minimum_distinct_days: number;
  rule_version: string;
  automatic_start: false;
  guidance: string;
  accuracy_limitation: string;
  calibration_available?: boolean;
  calibration_state?: string;
};

type ApplicationSettingsStatus = {
  schema_version: number;
  database_status: string;
  integrity_status: string;
  integrity_explanation: string;
  foreign_keys_enabled: boolean;
  telemetry_sampling_seconds: number;
  production_sampling_seconds: number;
  feature_window_minutes: number;
  processing_mode: "local_only";
  last_collection_timestamp_utc: string | null;
  api_version: string;
};

type EnhancedSignal = {
  id: number | null;
  collection_run_id: number | null;
  device_id: string;
  timestamp_utc: string;
  signal_group: string;
  signal_key: string;
  signal_label: string;
  numeric_value: number | null;
  unit: string;
  availability_status:
    | "available"
    | "unavailable"
    | "unsupported"
    | "permission_limited"
    | "timeout"
    | "not_applicable";
  reason_code: string | null;
  source_name: string;
  source_status: string;
  collection_frequency_seconds: number;
  shadow_mode: boolean;
  trend: "increasing" | "decreasing" | "stable" | "insufficient_history";
  details: Record<string, unknown>;
  readiness_state:
    | "needs_more_history"
    | "unsupported_by_device"
    | "permission_limited"
    | "collector_failure"
    | "shadow_only"
    | "redesign_required";
  display_state:
    | "Available"
    | "Collecting history"
    | "Temporarily unavailable"
    | "Not applicable"
    | "Unsupported on this device"
    | "Collector failure";
  available_history_count: number;
};

type EnhancedCollectorState = {
  collector_key: string;
  last_attempt_utc: string | null;
  last_success_utc: string | null;
  next_due_utc: string | null;
  availability_status: string;
  reason_code: string | null;
  source_name: string;
  collection_frequency_seconds: number;
  last_schedule_delay_seconds?: number | null;
  last_collection_duration_ms?: number | null;
  last_gap_classification?: string | null;
};

type EnhancedEvidenceStatus = {
  status: "available" | "collecting_data";
  shadow_mode: true;
  device_id: string | null;
  signals: EnhancedSignal[];
  hidden_unsupported_signals: Array<{
    signal_key: string;
    signal_label: string;
    availability_status: string;
    reason_code: string | null;
    source_name: string;
  }>;
  capability_display_policy_version: string;
  capability_display_message: string;
  collectors: EnhancedCollectorState[];
  technical_diagnostics: Array<{
    display_state: string;
    reason_code: string;
    source_name: string;
    signal_count: number;
  }>;
  latest_run: {
    status: string;
    started_at_utc: string;
    finished_at_utc: string | null;
    collection_duration_ms: number | null;
    full_cycle_duration_ms: number | null;
    approximate_process_cpu_percent: number | null;
    process_rss_before_bytes: number | null;
    process_rss_after_bytes: number | null;
    database_bytes_before: number | null;
    database_bytes_after: number | null;
  } | null;
  structured_event_count: number;
  interpretation: string;
};

type RuntimeStatus = {
  state: "live" | "paused" | "stale" | "offline";
  agent_running: boolean;
  heartbeat_at_utc: string | null;
  heartbeat_age_seconds: number | null;
  stale_after_seconds: number;
  reason?: string | null;
};

type PipelineStage = {
  stage_key: string;
  current_state:
    | "processing"
    | "successfully_waiting"
    | "overdue"
    | "failed"
    | "not_applicable"
    | "not_yet_executed";
  last_attempted_at_utc: string | null;
  last_successful_at_utc: string | null;
  expected_interval_seconds: number | null;
  processing_duration_ms: number | null;
  failure_or_overdue_reason: string | null;
  success_age_seconds?: number | null;
  overdue_tolerance_seconds?: number;
  active_baseline_version?: number | null;
};

type PipelineStatus = {
  database_connected: boolean;
  generated_at_utc: string;
  stages: PipelineStage[];
};

type FineQualityContribution = {
  metric_key: string;
  metric_label: string;
  observed_value: number | null;
  baseline_centre: number | null;
  expected_low: number | null;
  expected_high: number | null;
  configured_threshold: {
    recommended_high?: number;
    limit_high?: number;
  };
  availability_status: string;
  configured_weight: number;
  effective_weight: number;
  metric_score: number | null;
  weighted_contribution: number | null;
  explanation: string;
};

type FineQualityProfile = {
  key: string;
  name: string;
  description: string;
  parent_workload_profile: string;
  profile_kind: "fine_grained" | "broad_v2";
  catalogue_state: "catalogued";
  detectability_state:
    | "independently_detectable"
    | "not_independently_detectable"
    | "aggregate_detectable"
    | "broad_context_detectable";
  detectability_explanation: string;
  metrics?: Record<string, {
    label: string;
    direction: string;
    unit: string;
    weight: number;
    recommended_high: number;
    limit_high: number;
  }>;
  observed_window_count: number;
  history_depth: {
    qualifying_observation_count: number;
    distinct_observation_days: number;
    latest_qualifying_observation_utc: string | null;
    history_category: "none" | "one" | "few" | "multiple";
    label: string;
    baseline_training_observation_count: number;
    independent_post_activation_observation_count: number;
    latest_independent_post_activation_observation_utc: string | null;
    historical_independence_unavailable_count: number;
  };
  evaluation_state: "assessed" | "not_observed" | "not_evaluated";
  status_explanation: string;
  calibration_required: false;
  assessment: null | {
    id: number;
    feature_window_id: number;
    profile_quality_score: number | null;
    evidence_confidence: number | null;
    assessed_at_utc: string;
    detection_reason: string | null;
    detection_confidence: number | null;
    detection_evidence: Record<string, unknown>;
    detection_rule_version: string;
    scoring_method_version: string;
    baseline_version_id: number;
    baseline_version_number?: number;
    baseline_source: string;
    baseline_activated_at_utc: string | null;
    baseline_training_evidence: boolean;
    independent_post_activation_evidence: boolean;
    evidence_independence_state: string;
    observed_at_utc: string;
    current_evidence_quality: number | null;
    explanation: string;
    missing_evidence: string[];
    metric_contributions: FineQualityContribution[];
  };
};

type FineQualityStatus = {
  active_baseline_version: number | null;
  taxonomy_version: string;
  detection_rule_version: string;
  scoring_method_version: string;
  semantic_version: string;
  measure_name: string;
  interpretation: string;
  guide_explanation: string;
  full_score_explanation: string;
  profile_count: number;
  fine_profile_count: number;
  profiles: FineQualityProfile[];
  research_references: Array<{ title: string; url: string; supports: string }>;
};

type ValidationMetric = {
  id: number;
  scope_type: string;
  scope_value: string;
  metric_name: string;
  numerator: number | null;
  denominator: number;
  metric_value: number | null;
  evaluation_state: "evaluated" | "insufficient_labeled_evidence";
  data_confidence: number;
  minimum_requirement: number;
  reason_codes: string[];
  reconstruction: Record<string, unknown>;
};

type ValidationRun = {
  id: number;
  status: "evaluated" | "insufficient_labeled_evidence";
  finished_at_utc: string;
  eligible_alert_count: number;
  eligible_incident_count: number;
  eligible_window_count: number;
  matched_count: number;
  excluded_count: number;
  distinct_observation_days: number;
  data_confidence: number;
  confidence_level: "high" | "moderate" | "limited" | "insufficient";
  algorithm_version: string;
  configuration_version: string;
  matching_version: string;
  reason_codes: string[];
  metrics: ValidationMetric[];
  evidence_decisions: Array<{
    id: number;
    evidence_type: string;
    evidence_id: number;
    included: boolean;
    classification: string | null;
    reason_codes: string[];
  }>;
};

type ValidationStatus = {
  status: "not_evaluated" | "evaluated" | "insufficient_labeled_evidence";
  incident_count: number;
  verified_incident_count: number;
  feedback_count: number;
  unverified_alert_count: number;
  completed_observation_period_count: number;
  latest_run: ValidationRun | null;
  algorithm_version: string;
  configuration_version: string;
  matching_version: string;
  interpretation: string;
};

type IncidentReport = {
  id: number;
  category: string;
  severity: string;
  start_utc: string;
  timestamp_precision: string;
  verification_status: string;
  symptoms_text: string;
  status: "active" | "withdrawn";
  current_revision_number: number;
};

type ObservationPeriod = {
  id: number;
  start_utc: string;
  end_utc: string | null;
  state: "open" | "completed" | "incomplete" | "withdrawn";
  incident_reporting_complete: boolean;
  coverage_ratio: number | null;
  reason_codes: string[];
};

type UnverifiedAlert = {
  id: number;
  title: string;
  category: string;
  current_severity: string;
  first_observed_utc: string;
};

type AlertFeedbackRow = {
  id: number;
  alert_id: number;
  alert_title: string;
  outcome: string;
  verification_status: string;
  verification_timestamp_utc: string;
  observation_horizon_seconds: number | null;
  condition_state: string;
  current_revision_number: number;
  alert_lifecycle_state: string;
};

type LoadState = "loading" | "ready" | "error";
type HistoryRange = "1h" | "6h" | "24h" | "7d" | "all";

const PAGE_DETAILS: Record<
  AppRoute,
  { eyebrow: string; title: string; description: string }
> = {
  overview: {
    eyebrow: "Current operating picture",
    title: "SmartOps overview",
    description:
      "Automatic local monitoring, predictive evidence, system health, and PC suitability at a glance.",
  },
  "live-monitoring": {
    eyebrow: "30-second local system data",
    title: "Live Monitoring",
    description:
      "Current system metrics, workload context, Windows events, processes, and local history.",
  },
  "predictive-alerts": {
    eyebrow: "Automatic operational evidence",
    title: "Predictive Alerts",
    description:
      "Evidence-supported conditions and their lifecycle. No manual incident input is required.",
  },
  "root-cause-analysis": {
    eyebrow: "Explainable evidence",
    title: "Root-Cause Analysis",
    description:
      "Probable contributing factors, supporting and contradictory evidence, and safe diagnostic checks.",
  },
  "system-health": {
    eyebrow: "Current operating condition",
    title: "System Health",
    description:
      "A transparent summary of available resource, stability, event, and learned evidence.",
  },
  "pc-quality-check": {
    eyebrow: "Operating headroom and hardware suitability",
    title: "PC Quality Check",
    description:
      "Recent observed workload headroom and separate hardware capability evidence.",
  },
  "research-validation": {
    eyebrow: "Optional academic workflow",
    title: "Research & Validation",
    description:
      "Optional labelled outcomes used to evaluate predictive performance without changing automatic monitoring.",
  },
  settings: {
    eyebrow: "Application preferences",
    title: "Settings",
    description:
      "Browser display preferences, local Windows notifications, monitoring facts, privacy boundaries, and system information.",
  },
};

function formatPercent(value: number | null): string {
  return value === null ? "Unavailable" : `${value.toFixed(1)}%`;
}

function alertSeverityLabel(
  severity: PredictiveAlert["current_severity"],
): string {
  return {
    informational: "Low",
    advisory: "Medium",
    warning: "High",
    urgent: "Critical",
  }[severity];
}

function validationPercent(value: number | null | undefined): string {
  return value == null ? "Not available" : `${(value * 100).toFixed(1)}%`;
}

function formatBytes(value: number | null): string {
  if (value === null) return "Unavailable";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  return `${size.toFixed(unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

function formatRate(value: number | null): string {
  if (value === null) return "Unavailable";
  if (value >= 1024 * 1024) return `${(value / 1024 / 1024).toFixed(2)} MB/s`;
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KB/s`;
  return `${value.toFixed(0)} B/s`;
}

function formatCompactRate(value: number): string {
  if (value >= 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB/s`;
  if (value >= 1024) return `${(value / 1024).toFixed(0)} KB/s`;
  return `${value.toFixed(0)} B/s`;
}

function formatEnhancedSignal(signal: EnhancedSignal): string {
  if (
    signal.availability_status !== "available"
    || signal.numeric_value === null
  ) {
    return "Unavailable";
  }
  const value = signal.numeric_value;
  if (signal.unit === "percent") return `${value.toFixed(1)}%`;
  if (signal.unit === "bytes") return formatBytes(value);
  if (signal.unit === "bytes_per_second") return formatRate(value);
  if (signal.unit === "seconds") {
    return value < 1
      ? `${(value * 1000).toFixed(2)} ms`
      : `${value.toFixed(2)} s`;
  }
  if (signal.unit === "per_second") return `${value.toFixed(1)}/s`;
  if (signal.unit === "pages_per_second") {
    return `${value.toFixed(1)} pages/s`;
  }
  if (signal.unit === "celsius") return `${value.toFixed(1)} °C`;
  if (signal.unit === "milliwatts") return `${value.toFixed(0)} mW`;
  if (signal.unit === "milliwatt_hours") return `${value.toFixed(0)} mWh`;
  if (signal.unit === "cores") return `${value.toFixed(0)} cores`;
  return `${value.toFixed(2)} ${signal.unit.replaceAll("_", " ")}`;
}

function formatFeatureValue(value: number | null): string {
  return value === null ? "Unavailable" : value.toFixed(2);
}

function formatCapability(value: unknown, fieldName = ""): string {
  if (value === null || value === undefined) return "Unavailable";
  if (typeof value === "boolean") return value ? "Available" : "Not detected";
  if (
    typeof value === "number"
    && value >= 1024 * 1024
    && (
      fieldName.includes("bytes")
      || fieldName.includes("memory")
      || fieldName.includes("storage")
      || fieldName.includes("graphics")
    )
  ) {
    return formatBytes(value);
  }
  if (typeof value === "object") {
    const values = Object.entries(value as Record<string, unknown>)
      .filter(([, item]) => item !== null && item !== undefined)
      .map(([key, item]) => `${key.replaceAll("_", " ")}: ${formatCapability(item, key)}`);
    return values.length ? values.join(", ") : "Unavailable";
  }
  return String(value);
}

function DeviationChart({ data }: { data: DeviationAssessment[] }) {
  const values = data
    .filter((item) => item.deviation_index !== null)
    .slice()
    .reverse();
  if (values.length === 0) {
    return <div className="deviation-chart-empty">No evaluated history yet.</div>;
  }
  const width = 620;
  const height = 150;
  const points = values.map((item, index) => {
    const x = values.length === 1 ? width / 2 : (index / (values.length - 1)) * width;
    const y = height - ((item.deviation_index ?? 0) / 100) * height;
    return `${x},${y}`;
  });
  return (
    <div className="deviation-chart" aria-label="Historical Deviation Index chart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img">
        <line x1="0" y1={height * 0.6} x2={width} y2={height * 0.6} />
        <polyline points={points.join(" ")} />
      </svg>
      <div><span>0</span><span>Deviation Index</span><span>100</span></div>
    </div>
  );
}

function RiskChart({ data }: { data: RiskAssessment[] }) {
  const values = data.slice().reverse();
  if (values.length === 0) {
    return (
      <div className="deviation-chart-empty">
        No evaluated risk-evidence history yet.
      </div>
    );
  }
  const width = 620;
  const height = 150;
  const points = values.map((item, index) => {
    const x = values.length === 1 ? width / 2 : (index / (values.length - 1)) * width;
    const y = height - (item.risk_evidence_index / 100) * height;
    return `${x},${y}`;
  });
  return (
    <div className="deviation-chart risk-chart" aria-label="Historical Risk Evidence Index chart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img">
        <line x1="0" y1={height * 0.5} x2={width} y2={height * 0.5} />
        <polyline points={points.join(" ")} />
      </svg>
      <div><span>0</span><span>Risk Evidence Index</span><span>100</span></div>
    </div>
  );
}

function HealthChart({ data }: { data: HealthAssessment[] }) {
  const values = data.slice().reverse();
  if (
    values.length === 0
    || values.every((item) => item.system_health_score === null)
  ) {
    return (
      <div className="deviation-chart-empty">
        No evaluated health history in this range.
      </div>
    );
  }
  const width = 620;
  const height = 150;
  const xFor = (index: number) =>
    values.length === 1 ? width / 2 : (index / (values.length - 1)) * width;
  const segments: string[][] = [];
  let segment: string[] = [];
  values.forEach((item, index) => {
    if (item.system_health_score === null) {
      if (segment.length) segments.push(segment);
      segment = [];
      return;
    }
    segment.push(
      `${xFor(index)},${height - (item.system_health_score / 100) * height}`,
    );
  });
  if (segment.length) segments.push(segment);
  return (
    <div className="deviation-chart health-chart" aria-label="Historical System Health Score chart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img">
        <line x1="0" y1={height * 0.15} x2={width} y2={height * 0.15} />
        {segments.map((points, index) => (
          <polyline key={index} points={points.join(" ")} />
        ))}
      </svg>
      <div><span>0</span><span>System Health Score</span><span>100</span></div>
    </div>
  );
}

function formatChartTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatDuration(value: number | null): string {
  if (value === null) return "Unavailable";
  const totalSeconds = Math.max(0, Math.floor(value));
  const days = Math.floor(totalSeconds / 86400);
  const hours = Math.floor((totalSeconds % 86400) / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return [
    days > 0 ? `${days}d` : "",
    hours > 0 ? `${hours}h` : "",
    minutes > 0 ? `${minutes}m` : "",
    days === 0 && hours === 0 ? `${seconds}s` : "",
  ]
    .filter(Boolean)
    .join(" ");
}

function formatBlockingReason(profile: BaselineVersionProfile): string {
  const labels: Record<BaselineVersionProfile["blocking_reason"], string> = {
    none: "None",
    none_not_required: "None — not required",
    eligible_window_count_below_minimum: "Eligible-window count below minimum",
    distinct_collection_days_below_minimum: "Distinct collection days below minimum",
    both_requirements_pending: "Both requirements pending",
  };
  return labels[profile.blocking_reason] ?? "None";
}

function formatProfileStatus(profile: BaselineVersionProfile): string {
  if (profile.readiness_state === "not_observed") return "Not observed";
  if (profile.readiness_state === "ready") return "Ready";
  if (profile.readiness_state === "collector_limited") return "Collector limited";
  return "Collecting data";
}

function formatUserReason(value: string): string {
  const labels: Record<string, string> = {
    not_evaluated: "Not evaluated",
    incomplete_feature_window: "Incomplete analysis period",
    coverage_below_minimum: "Data coverage below minimum",
    no_eligible_completed_window: "No suitable completed analysis period",
    insufficient_history: "More representative history is needed",
    baseline_not_ready: "Personal baseline is not ready",
    missing_required_metrics: "Required system data is unavailable",
    invalid_workload_context: "Workload context is unavailable",
  };
  return labels[value] ?? value.replaceAll("_", " ");
}

function formatQualityDescription(profile: FineQualityProfile): string {
  if (profile.profile_kind === "fine_grained") return profile.description;
  if (profile.key === "device") {
    return "Current operating headroom across the latest qualifying workload context observed on this computer.";
  }
  return `Recent operating headroom within the ${profile.name.toLowerCase()} workload context.`;
}

function formatQualityDetectionBasis(reason: string | null | undefined): string {
  const labels: Record<string, string> = {
    recognised_foreground_executable_composition:
      "Recognised foreground application activity",
    broad_v2_profile_fallback_no_unambiguous_fine_evidence:
      "Broad workload context from the active personal baseline",
    device_profile:
      "Overall evidence from the active personal baseline",
  };
  return reason
    ? labels[reason] ?? reason.replaceAll("_", " ")
    : "No unambiguous foreground evidence has been recorded.";
}

function rangeStart(range: HistoryRange): string | null {
  if (range === "all") return null;
  const hours = range === "1h" ? 1 : range === "6h" ? 6 : range === "24h" ? 24 : 24 * 7;
  return new Date(Date.now() - hours * 60 * 60 * 1000).toISOString();
}

function requestUrl(path: string, parameters?: Record<string, string | number>) {
  const url = new URL(`${API_BASE_URL}${path}`);
  for (const [name, value] of Object.entries(parameters ?? {})) {
    url.searchParams.set(name, String(value));
  }
  return url.toString();
}

async function fetchJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal });
  if (!response.ok) {
    throw new Error(`Local API request failed with status ${response.status}.`);
  }
  return (await response.json()) as T;
}

async function fetchOptionalJson<T>(
  url: string,
  signal?: AbortSignal,
): Promise<T | null> {
  const response = await fetch(url, { signal });
  if (response.status === 404) return null;
  if (!response.ok) {
    throw new Error(`Local API request failed with status ${response.status}.`);
  }
  return (await response.json()) as T;
}

function MetricCard({
  label,
  value,
  detail,
  tone,
  icon: Icon,
}: {
  label: string;
  value: number | null;
  detail: string;
  tone: "cyan" | "violet" | "green";
  icon: LucideIcon;
}) {
  const boundedValue = Math.max(0, Math.min(value ?? 0, 100));
  return (
    <article className={`metric-card metric-card--${tone}`}>
      <div className="metric-card__header">
        <span className="metric-card__title"><span className="metric-card__icon" aria-hidden="true"><Icon size={18} /></span>{label}</span>
        <span className="metric-card__dot" aria-hidden="true" />
      </div>
      <strong>{formatPercent(value)}</strong>
      <div
        className="progress"
        role="progressbar"
        aria-label={`${label} utilization`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={value ?? undefined}
      >
        <span style={{ width: `${boundedValue}%` }} />
      </div>
      <p>{detail}</p>
    </article>
  );
}

function SignalLabel({ icon: Icon, children }: { icon: LucideIcon; children: string }) {
  return <span className="detail-card__label"><span aria-hidden="true"><Icon size={16} /></span>{children}</span>;
}

function ProcessTable({
  title,
  rows,
}: {
  title: string;
  rows: ProcessRow[];
}) {
  return (
    <article className="process-card">
      <h3>{title}</h3>
      {rows.length === 0 ? (
        <p className="unavailable-copy">No process snapshot is available.</p>
      ) : (
        <div className="table-wrap table-wrap--plain">
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>Process</th>
                <th>PID</th>
                <th>CPU</th>
                <th>Memory</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((process) => (
                <tr key={`${process.rank}-${process.pid}`}>
                  <td>{process.rank}</td>
                  <td>{process.process_name}</td>
                  <td>{process.pid}</td>
                  <td>{formatPercent(process.cpu_percent)}</td>
                  <td>{formatPercent(process.memory_percent)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </article>
  );
}

function App() {
  const initialPreferences = useRef(loadDashboardPreferences()).current;
  const [dashboardPreferences, setDashboardPreferences] =
    useState<DashboardPreferences>(initialPreferences);
  const [activeRoute, setActiveRoute] = useState<AppRoute>(() =>
    initialRoute(window.location.hash, initialPreferences),
  );
  const requestController = useRef<AbortController | null>(null);
  const requestSequence = useRef(0);
  const [latest, setLatest] = useState<Metric | null>(null);
  const [chartHistory, setChartHistory] = useState<Metric[]>([]);
  const [tableHistory, setTableHistory] = useState<Metric[]>([]);
  const [matchingTotal, setMatchingTotal] = useState(0);
  const [totalSamples, setTotalSamples] = useState(0);
  const [processes, setProcesses] = useState<ProcessResponse | null>(null);
  const [workload, setWorkload] = useState<WorkloadRow | null>(null);
  const [events, setEvents] = useState<EventRow[]>([]);
  const [eventSummary, setEventSummary] = useState<EventSummary | null>(null);
  const [features, setFeatures] = useState<FeatureRow[]>([]);
  const [baseline, setBaseline] = useState<BaselineStatus | null>(null);
  const [deviation, setDeviation] = useState<DeviationLatestResponse | null>(null);
  const [deviationHistory, setDeviationHistory] = useState<DeviationAssessment[]>([]);
  const [deviationWorkload, setDeviationWorkload] = useState("all");
  const [riskStatus, setRiskStatus] = useState<RiskStatus | null>(null);
  const [risk, setRisk] = useState<RiskLatestResponse | null>(null);
  const [riskHistory, setRiskHistory] = useState<RiskAssessment[]>([]);
  const [riskWorkload, setRiskWorkload] = useState("all");
  const [analysisTarget, setAnalysisTarget] = useState("latest-risk");
  const [healthStatus, setHealthStatus] = useState<HealthStatus | null>(null);
  const [health, setHealth] = useState<HealthLatestResponse | null>(null);
  const [healthHistory, setHealthHistory] = useState<HealthAssessment[]>([]);
  const [healthWorkload, setHealthWorkload] = useState("all");
  const [qualityStatus, setQualityStatus] = useState<QualityStatus | null>(null);
  const [qualityProfiles, setQualityProfiles] = useState<QualityProfile[]>([]);
  const [qualityInventory, setQualityInventory] =
    useState<InventorySnapshot | null>(null);
  const [quality, setQuality] = useState<QualityLatestResponse | null>(null);
  const [qualityHistory, setQualityHistory] = useState<QualityAssessment[]>([]);
  // The public selector uses the separate 30-profile PC Quality taxonomy.
  // The six hardware scenarios remain available below for their original
  // workload-suitability purpose.
  const [fineQualityProfile, setFineQualityProfile] = useState(() => {
    const value = new URLSearchParams(window.location.hash.split("?", 2)[1] ?? "").get("profile");
    return isStableQualityProfile(value) ? value! : "device";
  });
  const [qualityProfile, setQualityProfile] = useState("software_development");
  const [alertStatus, setAlertStatus] = useState<AlertStatus | null>(null);
  const [alertHistory, setAlertHistory] = useState<PredictiveAlert[]>([]);
  const initialDeepLink = alertDeepLinkFromHash(window.location.hash);
  const [selectedAlertId, setSelectedAlertId] = useState<number | null>(
    initialDeepLink.alertId,
  );
  const [deepLinkMessage, setDeepLinkMessage] = useState(
    initialDeepLink.error ?? "",
  );
  const [alertDetailLoading, setAlertDetailLoading] = useState(
    initialDeepLink.alertId !== null,
  );
  const [alertDetailError, setAlertDetailError] = useState("");
  const [alertDetailRequestVersion, setAlertDetailRequestVersion] = useState(0);
  const [alertSelectionFromDeepLink, setAlertSelectionFromDeepLink] = useState(
    initialDeepLink.alertId !== null,
  );
  const [selectedAlertRisk, setSelectedAlertRisk] =
    useState<RiskAssessment | null>(null);
  const [selectedAlertCandidates, setSelectedAlertCandidates] =
    useState<RootCauseCandidate[]>([]);
  const [notificationStatus, setNotificationStatus] =
    useState<NotificationStatus | null>(null);
  const [notificationActionMessage, setNotificationActionMessage] = useState("");
  const [notificationLimitationMessage, setNotificationLimitationMessage] =
    useState("");
  const [notificationActionPending, setNotificationActionPending] = useState(false);
  const [applicationSettings, setApplicationSettings] =
    useState<ApplicationSettingsStatus | null>(null);
  const [baselineManagement, setBaselineManagement] =
    useState<BaselineManagementStatus | null>(null);
  const [baselineActionPending, setBaselineActionPending] = useState(false);
  const [baselineActionMessage, setBaselineActionMessage] = useState("");
  const [baselineTechnicalMessage, setBaselineTechnicalMessage] = useState("");
  const [enhancedEvidence, setEnhancedEvidence] =
    useState<EnhancedEvidenceStatus | null>(null);
  const [runtimeStatus, setRuntimeStatus] = useState<RuntimeStatus | null>(null);
  const [pipelineStatus, setPipelineStatus] = useState<PipelineStatus | null>(null);
  const [fineQuality, setFineQuality] = useState<FineQualityStatus | null>(null);
  const [showEnhancedDiagnostics, setShowEnhancedDiagnostics] = useState(false);
  const [outcomePending, setOutcomePending] = useState<number | null>(null);
  const [outcomeMessage, setOutcomeMessage] = useState("");
  const [alertSeverity, setAlertSeverity] = useState("all");
  const [alertState, setAlertState] = useState("all");
  const [alertCategory, setAlertCategory] = useState("all");
  const [alertWorkload, setAlertWorkload] = useState("all");
  const [alertSearch, setAlertSearch] = useState("");
  const [alertValidation, setAlertValidation] = useState("all");
  const [alertSort, setAlertSort] = useState<"newest" | "severity" | "confidence">("newest");
  const [acknowledgingAlert, setAcknowledgingAlert] = useState<number | null>(null);
  const [validation, setValidation] = useState<ValidationStatus | null>(null);
  const [validationRegistry, setValidationRegistry] = useState<MethodValidation[]>([]);
  const [incidents, setIncidents] = useState<IncidentReport[]>([]);
  const [observationPeriods, setObservationPeriods] = useState<ObservationPeriod[]>([]);
  const [unverifiedAlerts, setUnverifiedAlerts] = useState<UnverifiedAlert[]>([]);
  const [feedbackHistory, setFeedbackHistory] = useState<AlertFeedbackRow[]>([]);
  const [validationActionMessage, setValidationActionMessage] = useState("");
  const [incidentCategory, setIncidentCategory] = useState("application_failure");
  const [incidentSeverity, setIncidentSeverity] = useState("moderate");
  const [incidentStart, setIncidentStart] = useState(
    new Date().toISOString().slice(0, 16),
  );
  const [incidentSymptoms, setIncidentSymptoms] = useState("");
  const [revisionIncidentId, setRevisionIncidentId] = useState("");
  const [revisionSeverity, setRevisionSeverity] = useState("moderate");
  const [revisionReason, setRevisionReason] = useState("");
  const [feedbackAlertId, setFeedbackAlertId] = useState("");
  const [feedbackOutcome, setFeedbackOutcome] = useState("uncertain");
  const [feedbackHorizonHours, setFeedbackHorizonHours] = useState("24");
  const [feedbackNotes, setFeedbackNotes] = useState("");
  const [feedbackConditionState, setFeedbackConditionState] = useState("unclear");
  const [feedbackAction, setFeedbackAction] = useState("");
  const [feedbackMode, setFeedbackMode] = useState<"create" | "revise">("create");
  const [linkIncidentId, setLinkIncidentId] = useState("");
  const [linkAlertId, setLinkAlertId] = useState("");
  const [linkMatchType, setLinkMatchType] = useState("confirmed_match");
  const [linkReason, setLinkReason] = useState("");
  const [validationBreakdownScope, setValidationBreakdownScope] =
    useState<"category" | "severity" | "workload" | "algorithm_configuration">(
      "category",
    );
  const [range, setRange] = useState<HistoryRange>("1h");
  const [page, setPage] = useState(0);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [clock, setClock] = useState(Date.now());

  useEffect(() => {
    document.title = "SmartOps";
  }, []);

  useEffect(() => {
    const candidate = window.location.hash
      .replace(/^#\/?/, "")
      .split(/[/?]/, 1)[0];
    if (
      !candidate
      || (
        routeFromHash(window.location.hash) === "overview"
        && candidate !== "overview"
      )
    ) {
      window.history.replaceState(null, "", `#/${activeRoute}`);
    }
    const updateRoute = () => {
      setActiveRoute(routeFromHash(window.location.hash));
      const parameters = new URLSearchParams(window.location.hash.split("?", 2)[1] ?? "");
      const profile = parameters.get("profile");
      if (isStableQualityProfile(profile)) setFineQualityProfile(profile!);
      const section = parameters.get("section");
      if (section) {
        window.setTimeout(() => document.getElementById(`settings-${section}`)?.scrollIntoView({ block: "start" }), 0);
      }
      const deepLink = alertDeepLinkFromHash(window.location.hash);
      setSelectedAlertId(deepLink.alertId);
      setDeepLinkMessage(deepLink.error ?? "");
      setAlertSelectionFromDeepLink(deepLink.alertId !== null);
      setAlertDetailLoading(deepLink.alertId !== null);
      setAlertDetailError("");
    };
    window.addEventListener("hashchange", updateRoute);
    return () => window.removeEventListener("hashchange", updateRoute);
  }, [activeRoute]);

  useEffect(() => {
    if (dashboardPreferences.rememberLastPage) {
      saveLastOpenedPage(activeRoute);
    }
  }, [activeRoute, dashboardPreferences.rememberLastPage]);

  useEffect(() => {
    const parameters = new URLSearchParams(window.location.hash.split("?", 2)[1] ?? "");
    const section = parameters.get("section");
    if (activeRoute === "settings" && section) {
      window.setTimeout(() => document.getElementById(`settings-${section}`)?.scrollIntoView({ block: "start" }), 0);
    }
  }, [activeRoute]);

  const loadMetrics = useCallback(async () => {
    const isFirstRequest = requestSequence.current === 0;
    requestController.current?.abort();
    const controller = new AbortController();
    requestController.current = controller;
    const sequence = ++requestSequence.current;
    const signal = controller.signal;
    const isCurrent = () =>
      !signal.aborted && sequence === requestSequence.current;

    setIsRefreshing(true);
    if (isFirstRequest) setLoadState("loading");

    try {
      const selectedStart = rangeStart(range);
      const rangeParameters: Record<string, string | number> = {
        limit: 5000,
        offset: 0,
        sort: "newest",
      };
      if (selectedStart) rangeParameters.start = selectedStart;
      const tableParameters: Record<string, string | number> = {
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
        sort: "newest",
      };
      if (selectedStart) tableParameters.start = selectedStart;

      // Every page needs connection and freshness information. All other
      // requests are scoped to the visible route.
      const [status, latestMetric, runtime] = await Promise.all([
        fetchJson<{ status: string }>(requestUrl("/api/status"), signal),
        fetchJson<Metric | null>(requestUrl("/api/metrics/latest"), signal),
        fetchJson<RuntimeStatus>(requestUrl("/api/runtime/status"), signal),
      ]);
      if (status.status !== "ok") {
        throw new Error("The local API did not report a healthy status.");
      }
      if (!isCurrent()) return;
      setLatest(latestMetric);
      setRuntimeStatus(runtime);

      if (activeRoute === "overview") {
        const [
          count,
          chartPage,
          latestWorkload,
          featurePage,
          baselineResponse,
          riskStatusResponse,
          riskResponse,
          healthStatusResponse,
          healthResponse,
          healthPage,
          qualityStatusResponse,
          qualityProfilesResponse,
          qualityInventoryResponse,
          qualityResponse,
          fineQualityResponse,
          alertStatusResponse,
          alertPage,
        ] = await Promise.all([
          fetchJson<{ count: number }>(requestUrl("/api/metrics/count"), signal),
          fetchJson<HistoryResponse>(
            requestUrl("/api/metrics/history", {
              ...rangeParameters,
              limit: 120,
            }),
            signal,
          ),
          fetchJson<WorkloadRow | null>(requestUrl("/api/workload/latest"), signal),
          fetchJson<{ items: FeatureRow[] }>(
            requestUrl("/api/features/history", {
              limit: 288,
              offset: 0,
              sort: "newest",
            }),
            signal,
          ),
          fetchJson<BaselineStatus>(requestUrl("/api/baseline/status"), signal),
          fetchJson<RiskStatus>(requestUrl("/api/risk/status"), signal),
          fetchJson<RiskLatestResponse>(requestUrl("/api/risk/latest"), signal),
          fetchJson<HealthStatus>(requestUrl("/api/health/status"), signal),
          fetchJson<HealthLatestResponse>(requestUrl("/api/health/latest"), signal),
          fetchJson<{ items: HealthAssessment[] }>(
            requestUrl("/api/health/history", {
              limit: 288,
              offset: 0,
              sort: "newest",
              summary: "true",
            }),
            signal,
          ),
          fetchJson<QualityStatus>(requestUrl("/api/quality/status"), signal),
          fetchJson<{ profiles: QualityProfile[] }>(
            requestUrl("/api/quality/profiles"),
            signal,
          ),
          fetchJson<{ inventory: InventorySnapshot | null }>(
            requestUrl("/api/quality/inventory/latest"),
            signal,
          ),
          fetchJson<QualityLatestResponse>(
            requestUrl("/api/quality/latest", { profile: qualityProfile }),
            signal,
          ),
          fetchJson<FineQualityStatus>(
            requestUrl("/api/quality/profile-scores"),
            signal,
          ),
          fetchJson<AlertStatus>(requestUrl("/api/alerts/status"), signal),
          fetchJson<{ items: PredictiveAlert[] }>(
            requestUrl("/api/alerts/history", {
              limit: 8,
              offset: 0,
              sort: "newest",
            }),
            signal,
          ),
        ]);
        if (!isCurrent()) return;
        setTotalSamples(count.count);
        setChartHistory([...chartPage.items].reverse());
        setWorkload(latestWorkload);
        setFeatures(featurePage.items);
        setBaseline(baselineResponse);
        setRiskStatus(riskStatusResponse);
        setRisk(riskResponse);
        setHealthStatus(healthStatusResponse);
        setHealth(healthResponse);
        setHealthHistory(healthPage.items);
        setQualityStatus(qualityStatusResponse);
        setQualityProfiles(qualityProfilesResponse.profiles);
        setQualityInventory(qualityInventoryResponse.inventory);
        setQuality(qualityResponse);
        setFineQuality(fineQualityResponse);
        setAlertStatus(alertStatusResponse);
        setAlertHistory(alertPage.items);
        // Pipeline reconstruction can be slower on large historical databases.
        // Keep it in the same coordinated refresh cycle, but do not hold the
        // complete Overview behind this independent read-only response.
        void fetchJson<PipelineStatus>(requestUrl("/api/pipeline/status"), signal)
          .then((pipelineResponse) => {
            if (isCurrent()) setPipelineStatus(pipelineResponse);
          })
          .catch((error: unknown) => {
            if (!(error instanceof DOMException && error.name === "AbortError")) {
              console.warn("Pipeline status was not available for this refresh.");
            }
          });
      } else if (activeRoute === "live-monitoring") {
        const [
          count,
          processSnapshot,
          chartPage,
          historyPage,
          latestWorkload,
          eventPage,
          eventSummaryResponse,
          featurePage,
          baselineManagementResponse,
        ] = await Promise.all([
          fetchJson<{ count: number }>(requestUrl("/api/metrics/count"), signal),
          fetchJson<ProcessResponse | null>(
            requestUrl("/api/processes/latest"),
            signal,
          ),
          fetchJson<HistoryResponse>(
            requestUrl("/api/metrics/history", rangeParameters),
            signal,
          ),
          fetchJson<HistoryResponse>(
            requestUrl("/api/metrics/history", tableParameters),
            signal,
          ),
          fetchJson<WorkloadRow | null>(requestUrl("/api/workload/latest"), signal),
          fetchJson<{ items: EventRow[] }>(
            requestUrl("/api/events", {
              limit: 10,
              offset: 0,
              sort: "newest",
            }),
            signal,
          ),
          fetchJson<EventSummary>(requestUrl("/api/events/summary"), signal),
          fetchJson<{ items: FeatureRow[] }>(
            requestUrl("/api/features/history", {
              limit: 10,
              offset: 0,
              sort: "newest",
            }),
            signal,
          ),
          fetchJson<BaselineManagementStatus>(
            requestUrl("/api/baseline-management/status"),
            signal,
          ),
        ]);
        if (!isCurrent()) return;
        setTotalSamples(count.count);
        setProcesses(processSnapshot);
        setChartHistory([...chartPage.items].reverse());
        setTableHistory(historyPage.items);
        setMatchingTotal(historyPage.total);
        setWorkload(latestWorkload);
        setEvents(eventPage.items);
        setEventSummary(eventSummaryResponse);
        setFeatures(featurePage.items);
        setBaselineManagement(baselineManagementResponse);
        // Advanced System Signals are optional, shadow-only evidence.  Their
        // slower Windows-status reconstruction must not block current core
        // telemetry from rendering.
        void fetchJson<EnhancedEvidenceStatus>(
          requestUrl("/api/enhanced-evidence/status"),
          signal,
        )
          .then((enhancedEvidenceResponse) => {
            if (isCurrent()) setEnhancedEvidence(enhancedEvidenceResponse);
          })
          .catch((error: unknown) => {
            if (!(error instanceof DOMException && error.name === "AbortError")) {
              console.warn(
                "Advanced System Signals were not available for this refresh.",
              );
            }
          });
      } else if (activeRoute === "predictive-alerts") {
        const [
          alertStatusResponse,
          alertPage,
          baselineResponse,
          riskStatusResponse,
          healthStatusResponse,
          notificationStatusResponse,
        ] = await Promise.all([
          fetchJson<AlertStatus>(requestUrl("/api/alerts/status"), signal),
          fetchJson<{ items: PredictiveAlert[] }>(
            requestUrl("/api/alerts/history", {
              limit: 50,
              offset: 0,
              sort: "newest",
              summary: "true",
              ...(selectedStart ? { start: selectedStart } : {}),
              ...(alertSeverity === "all" ? {} : { severity: alertSeverity }),
              ...(alertState === "all" ? {} : { state: alertState }),
              ...(alertCategory === "all" ? {} : { category: alertCategory }),
              ...(alertWorkload === "all" ? {} : { workload: alertWorkload }),
            }),
            signal,
          ),
          fetchJson<BaselineStatus>(requestUrl("/api/baseline/status"), signal),
          fetchJson<RiskStatus>(requestUrl("/api/risk/status"), signal),
          fetchJson<HealthStatus>(requestUrl("/api/health/status"), signal),
          fetchJson<NotificationStatus>(requestUrl("/api/notifications/status"), signal),
        ]);
        if (!isCurrent()) return;
        setAlertStatus(alertStatusResponse);
        setAlertHistory((current) => {
          const selectedFullRecord = current.find(
            (item) => item.id === selectedAlertId && !item.summary_record,
          );
          return selectedFullRecord
            ? [
                selectedFullRecord,
                ...alertPage.items.filter((item) => item.id !== selectedFullRecord.id),
              ]
            : alertPage.items;
        });
        setBaseline(baselineResponse);
        setRiskStatus(riskStatusResponse);
        setHealthStatus(healthStatusResponse);
        setNotificationStatus(notificationStatusResponse);
      } else if (activeRoute === "root-cause-analysis") {
        const [baselineResponse, riskPage, alertPage] = await Promise.all([
          fetchJson<BaselineStatus>(requestUrl("/api/baseline/status"), signal),
          fetchJson<{ items: RiskAssessment[] }>(
            requestUrl("/api/risk/history", {
              limit: 100,
              offset: 0,
              sort: "newest",
              ...(selectedStart ? { start: selectedStart } : {}),
              ...(riskWorkload === "all" ? {} : { workload: riskWorkload }),
            }),
            signal,
          ),
          fetchJson<{ items: PredictiveAlert[] }>(
            requestUrl("/api/alerts/history", {
              limit: 100,
              offset: 0,
              sort: "newest",
              summary: "true",
            }),
            signal,
          ),
        ]);
        if (!isCurrent()) return;
        setBaseline(baselineResponse);
        setRisk({
          status: riskPage.items.length ? "evaluated" : "not_evaluated",
          assessment: riskPage.items[0] ?? null,
        });
        setRiskHistory(riskPage.items);
        setAlertHistory(alertPage.items);
      } else if (activeRoute === "system-health") {
        const [healthStatusResponse, healthResponse, healthPage, healthAlertPage] =
          await Promise.all([
            fetchJson<HealthStatus>(requestUrl("/api/health/status"), signal),
            fetchJson<HealthLatestResponse>(
              requestUrl(
                "/api/health/latest",
                healthWorkload === "all"
                  ? undefined
                  : { workload: healthWorkload },
              ),
              signal,
            ),
            fetchJson<{ items: HealthAssessment[] }>(
              requestUrl("/api/health/history", {
                limit: 5000,
                offset: 0,
                sort: "newest",
                summary: "true",
                ...(selectedStart ? { start: selectedStart } : {}),
                ...(healthWorkload === "all"
                  ? {}
                  : { workload: healthWorkload }),
              }),
              signal,
            ),
            fetchJson<{ items: PredictiveAlert[] }>(
              requestUrl("/api/alerts/history", {
                limit: 100,
                offset: 0,
                sort: "newest",
                summary: "true",
              }),
              signal,
            ),
          ]);
        if (!isCurrent()) return;
        setHealthStatus(healthStatusResponse);
        setHealth(healthResponse);
        setHealthHistory(healthPage.items);
        setAlertHistory(healthAlertPage.items);
      } else if (activeRoute === "pc-quality-check") {
        const [
          qualityStatusResponse,
          qualityProfilesResponse,
          qualityInventoryResponse,
          qualityResponse,
          qualityHistoryResponse,
          fineQualityResponse,
        ] = await Promise.all([
          fetchJson<QualityStatus>(requestUrl("/api/quality/status"), signal),
          fetchJson<{ profiles: QualityProfile[] }>(
            requestUrl("/api/quality/profiles"),
            signal,
          ),
          fetchJson<{ inventory: InventorySnapshot | null }>(
            requestUrl("/api/quality/inventory/latest"),
            signal,
          ),
          fetchJson<QualityLatestResponse>(
            requestUrl("/api/quality/latest", { profile: qualityProfile }),
            signal,
          ),
          fetchJson<{ items: QualityAssessment[] }>(
            requestUrl("/api/quality/history", {
              limit: 20,
              offset: 0,
              sort: "newest",
              profile: qualityProfile,
            }),
            signal,
          ),
          fetchJson<FineQualityStatus>(
            requestUrl("/api/quality/profile-scores"),
            signal,
          ),
        ]);
        if (!isCurrent()) return;
        setQualityStatus(qualityStatusResponse);
        setQualityProfiles(qualityProfilesResponse.profiles);
        setQualityInventory(qualityInventoryResponse.inventory);
        setQuality(qualityResponse);
        setQualityHistory(qualityHistoryResponse.items);
        setFineQuality(fineQualityResponse);
      } else if (activeRoute === "settings") {
        const [
          settingsResponse,
          notificationStatusResponse,
          baselineResponse,
          baselineManagementResponse,
        ] = await Promise.all([
          fetchJson<ApplicationSettingsStatus>(
            requestUrl("/api/settings/status"),
            signal,
          ),
          fetchJson<NotificationStatus>(
            requestUrl("/api/notifications/status"),
            signal,
          ),
          fetchJson<BaselineStatus>(requestUrl("/api/baseline/status"), signal),
          fetchJson<BaselineManagementStatus>(
            requestUrl("/api/baseline-management/status"),
            signal,
          ),
        ]);
        if (!isCurrent()) return;
        setApplicationSettings(settingsResponse);
        setNotificationStatus(notificationStatusResponse);
        setBaseline(baselineResponse);
        setBaselineManagement(baselineManagementResponse);
      } else {
        const [
          validationResponse,
          incidentResponse,
          observationResponse,
          unverifiedResponse,
          feedbackResponse,
          validationRegistryResponse,
          fineQualityResponse,
        ] = await Promise.all([
          fetchJson<ValidationStatus>(requestUrl("/api/validation/status"), signal),
          fetchJson<{ items: IncidentReport[] }>(
            requestUrl("/api/incidents/history", { limit: 20, offset: 0 }),
            signal,
          ),
          fetchJson<{ items: ObservationPeriod[] }>(
            requestUrl("/api/validation/observation-periods", { limit: 20 }),
            signal,
          ),
          fetchJson<{ items: UnverifiedAlert[] }>(
            requestUrl("/api/validation/unverified-alerts", { limit: 100 }),
            signal,
          ),
          fetchJson<{ items: AlertFeedbackRow[] }>(
            requestUrl("/api/validation/feedback", { limit: 20, offset: 0 }),
            signal,
          ),
          fetchJson<{ items: MethodValidation[] }>(
            requestUrl("/api/validation/registry"),
            signal,
          ),
          fetchJson<FineQualityStatus>(
            requestUrl("/api/quality/profile-taxonomy"),
            signal,
          ),
        ]);
        if (!isCurrent()) return;
        setValidation(validationResponse);
        setIncidents(incidentResponse.items);
        setObservationPeriods(observationResponse.items);
        setUnverifiedAlerts(unverifiedResponse.items);
        setFeedbackHistory(feedbackResponse.items);
        setValidationRegistry(validationRegistryResponse.items ?? []);
        setFineQuality(fineQualityResponse);
      }

      if (!isCurrent()) return;
      setLoadState("ready");
      setErrorMessage("");
      setClock(Date.now());
    } catch (error) {
      if (signal.aborted || sequence !== requestSequence.current) return;
      setLoadState("error");
      setErrorMessage(
        error instanceof Error ? error.message : "Could not reach SmartOps.",
      );
    } finally {
      if (sequence === requestSequence.current) setIsRefreshing(false);
    }
  }, [
    activeRoute,
    alertCategory,
    alertSeverity,
    alertState,
    alertWorkload,
    deviationWorkload,
    healthWorkload,
    page,
    qualityProfile,
    range,
    riskWorkload,
    selectedAlertId,
  ]);

  useEffect(() => {
    void loadMetrics();
    const refreshTimer = dashboardPreferences.automaticRefresh
      ? window.setInterval(
          () => void loadMetrics(),
          REFRESH_INTERVAL_MS,
        )
      : null;
    return () => {
      if (refreshTimer !== null) window.clearInterval(refreshTimer);
      requestController.current?.abort();
    };
  }, [dashboardPreferences.automaticRefresh, loadMetrics]);

  // Alert details have their own request lifecycle. A slow summary/status refresh
  // must never abort a user-initiated detail request or leave its loading state
  // stuck when the coordinated 30-second refresh begins.
  useEffect(() => {
    if (activeRoute !== "predictive-alerts" || selectedAlertId === null) return;
    const controller = new AbortController();
    let active = true;

    const loadAlertDetails = async () => {
      setAlertDetailLoading(true);
      setAlertDetailError("");
      try {
        const response = await fetchOptionalJson<{ alert: PredictiveAlert }>(
          requestUrl(`/api/alerts/${selectedAlertId}`),
          controller.signal,
        );
        if (!active) return;
        if (response === null) {
          setAlertDetailLoading(false);
          setAlertDetailError(`Alert ${selectedAlertId} was not found in this local database.`);
          if (alertSelectionFromDeepLink) {
            setDeepLinkMessage(
              `Alert ${selectedAlertId} was not found. It may have been removed from this database.`,
            );
          }
          return;
        }

        const linkedAlert = response.alert;
        setAlertHistory((current) => [
          linkedAlert,
          ...current.filter((item) => item.id !== linkedAlert.id),
        ]);
        setAlertDetailLoading(false);
        if (alertSelectionFromDeepLink) {
          setDeepLinkMessage(
            `Opened alert ${linkedAlert.id} from the Windows notification.`,
          );
        } else {
          setDeepLinkMessage("");
        }

        const [riskResult, candidateResult] = await Promise.allSettled([
          fetchJson<RiskLatestResponse>(
            requestUrl(`/api/risk/${linkedAlert.source_feature_window_id}`),
            controller.signal,
          ),
          fetchJson<{ status: string; candidates: RootCauseCandidate[] }>(
            requestUrl(`/api/root-causes/${linkedAlert.source_feature_window_id}`),
            controller.signal,
          ),
        ]);
        if (!active) return;
        setSelectedAlertRisk(
          riskResult.status === "fulfilled"
            ? riskResult.value.assessment ?? null
            : null,
        );
        setSelectedAlertCandidates(
          candidateResult.status === "fulfilled"
            ? candidateResult.value.candidates ?? []
            : [],
        );
      } catch (error) {
        if (!active || (error instanceof DOMException && error.name === "AbortError")) return;
        setAlertDetailLoading(false);
        setAlertDetailError(
          "SmartOps could not retrieve this alert's full details from the local API.",
        );
      }
    };

    void loadAlertDetails();
    return () => {
      active = false;
      controller.abort();
    };
  }, [
    activeRoute,
    alertDetailRequestVersion,
    alertSelectionFromDeepLink,
    selectedAlertId,
  ]);

  useEffect(() => {
    const clockTimer = window.setInterval(() => setClock(Date.now()), 10_000);
    return () => window.clearInterval(clockTimer);
  }, []);

  const sampleAge = latest
    ? Math.max(0, clock - new Date(latest.timestamp_utc).getTime())
    : null;
  const isStale = sampleAge !== null && sampleAge > STALE_AFTER_MS;
  const calibrationPaused =
    baselineManagement?.candidate_version?.learning_state === "paused";
  const chartLiveState: LiveState = calibrationPaused
    ? "paused"
    : runtimeStatus?.state === "live"
    ? isStale ? "stale" : "live"
    : runtimeStatus?.state === "paused"
      ? "paused"
      : "offline";
  const latestDeviation = deviation?.assessment ?? null;
  const selectedRisk =
    analysisTarget.startsWith("risk-")
      ? riskHistory.find(
          (item) => item.id === Number(analysisTarget.replace("risk-", "")),
        ) ?? null
      : null;
  const selectedAlert =
    analysisTarget.startsWith("alert-")
      ? alertHistory.find(
          (item) => item.id === Number(analysisTarget.replace("alert-", "")),
        ) ?? null
      : null;
  const latestRisk = selectedAlert
    ? riskHistory.find(
        (item) => item.feature_window_id === selectedAlert.source_feature_window_id,
      ) ?? null
    : selectedRisk ?? risk?.assessment ?? null;
  const latestHealth = health?.assessment ?? null;
  const latestQuality = quality?.assessment ?? null;
  const selectedFineQuality = fineQuality?.profiles.find(
    (profile) => profile.key === fineQualityProfile,
  ) ?? fineQuality?.profiles.find((profile) => profile.key === "device") ?? null;
  const fineQualityEvaluatedCount = fineQuality?.profiles.filter(
    (profile) => profile.assessment?.profile_quality_score != null,
  ).length ?? 0;
  const fineQualityNotObservedCount = fineQuality?.profiles.filter(
    (profile) => profile.evaluation_state === "not_observed",
  ).length ?? 0;
  const filteredAlertHistory = useMemo(() => {
    const query = alertSearch.trim().toLocaleLowerCase();
    const severityRank: Record<PredictiveAlert["current_severity"], number> = {
      urgent: 4,
      warning: 3,
      advisory: 2,
      informational: 1,
    };
    const filtered = alertHistory.filter((item) => {
      const validationAvailable = item.validation != null
        && [item.validation.precision, item.validation.recall, item.validation.f1_score]
          .some((value) => value != null);
      if (alertValidation === "validated" && !validationAvailable) return false;
      if (alertValidation === "not_validated" && validationAvailable) return false;
      if (!query) return true;
      return [
        item.title,
        item.category,
        item.alert_code,
        item.short_alert_basis,
        item.workload_context,
      ].some((value) => value?.toLocaleLowerCase().includes(query));
    });
    return [...filtered].sort((left, right) => {
      if (alertSort === "severity") {
        return severityRank[right.current_severity] - severityRank[left.current_severity]
          || Date.parse(right.latest_observed_utc) - Date.parse(left.latest_observed_utc);
      }
      if (alertSort === "confidence") {
        return (right.alert_confidence ?? -1) - (left.alert_confidence ?? -1)
          || Date.parse(right.latest_observed_utc) - Date.parse(left.latest_observed_utc);
      }
      return Date.parse(right.latest_observed_utc) - Date.parse(left.latest_observed_utc);
    });
  }, [alertHistory, alertSearch, alertSort, alertValidation]);
  const activeAlerts = filteredAlertHistory.filter((item) => item.state !== "resolved");
  const resolvedAlerts = filteredAlertHistory.filter((item) => item.state === "resolved");
  const validationMetrics = Object.fromEntries(
    (validation?.latest_run?.metrics ?? [])
      .filter((item) => item.scope_type === "overall")
      .map((item) => [item.metric_name, item]),
  ) as Record<string, ValidationMetric>;
  const openObservationPeriod = observationPeriods.find(
    (item) => item.state === "open",
  );
  const correlatedRiskEvents =
    latestRisk?.components.find(
      (component) => component.component_name === "serious_events",
    )?.evidence.events ?? [];
  const pageCount = Math.max(1, Math.ceil(matchingTotal / PAGE_SIZE));
  const rangeLabels: Record<HistoryRange, string> = {
    "1h": "Last hour",
    "6h": "6 hours",
    "24h": "24 hours",
    "7d": "7 days",
    all: "All data",
  };
  const formatTimestamp = (value: string) =>
    formatPreferenceTimestamp(value, dashboardPreferences, clock);

  const updateDashboardPreferences = (
    changes: Partial<DashboardPreferences>,
  ) => {
    setDashboardPreferences((current) =>
      saveDashboardPreferences({ ...current, ...changes }),
    );
  };

  const changeRange = (newRange: HistoryRange) => {
    setRange(newRange);
    setPage(0);
  };

  const toggleAlertDetails = (alertId: number) => {
    if (selectedAlertId === alertId) {
      setSelectedAlertId(null);
      setAlertDetailLoading(false);
      setAlertDetailError("");
      setAlertSelectionFromDeepLink(false);
      setDeepLinkMessage("");
      setSelectedAlertRisk(null);
      setSelectedAlertCandidates([]);
      window.history.replaceState(null, "", "#/predictive-alerts");
      return;
    }

    setSelectedAlertId(alertId);
    setAlertDetailLoading(true);
    setAlertDetailError("");
    setAlertSelectionFromDeepLink(false);
    setDeepLinkMessage("");
    setSelectedAlertRisk(null);
    setSelectedAlertCandidates([]);
    window.history.replaceState(
      null,
      "",
      `#/predictive-alerts?alertId=${encodeURIComponent(String(alertId))}`,
    );
  };

  const retryAlertDetails = () => {
    setAlertDetailLoading(true);
    setAlertDetailError("");
    setAlertDetailRequestVersion((current) => current + 1);
  };

  const acknowledge = async (alertId: number) => {
    setAcknowledgingAlert(alertId);
    try {
      const response = await fetch(
        requestUrl(`/api/alerts/${alertId}/acknowledge`),
        { method: "POST" },
      );
      if (!response.ok) {
        throw new Error(`Acknowledgement failed with status ${response.status}.`);
      }
      await loadMetrics();
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : "Could not acknowledge alert.",
      );
    } finally {
      setAcknowledgingAlert(null);
    }
  };

  const labelAlertOutcome = async (
    alertId: number,
    outcome: "pending" | "confirmed" | "false_positive" | "inconclusive",
  ) => {
    setOutcomePending(alertId);
    setOutcomeMessage("");
    try {
      const response = await fetch(requestUrl(`/api/alerts/${alertId}/outcome`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ outcome, note: null }),
      });
      if (!response.ok) throw new Error("Outcome label could not be stored.");
      setOutcomeMessage(
        "Outcome label stored for academic validation only; no scoring or baseline changed.",
      );
      await loadMetrics();
    } catch (error) {
      setOutcomeMessage(
        error instanceof Error ? error.message : "Outcome label could not be stored.",
      );
    } finally {
      setOutcomePending(null);
    }
  };

  const saveNotificationPreferences = async (
    enabled: boolean,
    eligibleCategories = notificationStatus?.eligible_categories,
  ) => {
    setNotificationActionPending(true);
    setNotificationActionMessage("");
    setNotificationLimitationMessage("");
    try {
      const response = await fetch(requestUrl("/api/notifications/preferences"), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          enabled,
          eligible_categories: eligibleCategories,
        }),
      });
      if (!response.ok) {
        throw new Error(`Preference update failed with status ${response.status}.`);
      }
      const result = (await response.json()) as {
        message: string;
        confirmation_notification?: {
          attempted: boolean;
          delivered: boolean;
          user_message: string | null;
        };
      };
      setNotificationActionMessage(result.message);
      setNotificationLimitationMessage(
        result.confirmation_notification?.user_message ?? "",
      );
      await loadMetrics();
    } catch {
      setNotificationActionMessage(
        "Could not update notification preferences. The rest of SmartOps is still running.",
      );
    } finally {
      setNotificationActionPending(false);
    }
  };

  const updateNotificationCategory = (
    category: NotificationCategory,
    selected: boolean,
  ) => {
    if (notificationStatus === null) return;
    const eligible = new Set(notificationStatus.eligible_categories);
    if (selected) eligible.add(category);
    else eligible.delete(category);
    void saveNotificationPreferences(
      notificationStatus.enabled,
      Array.from(eligible) as NotificationCategory[],
    );
  };

  const runBaselineAction = async (
    action: "start" | "pause" | "resume" | "continue" | "cancel" | "activate" | "rollback",
  ) => {
    if (
      action === "start"
      && !window.confirm(
        "Start a new optional calibration? Your existing baseline remains active and normal monitoring continues. Representative learning may require several days. The new calibration will not activate automatically and can be paused, resumed, or cancelled.",
      )
    ) return;
    if (
      ["cancel", "activate", "rollback"].includes(action)
      && !window.confirm(
        action === "cancel"
          ? "Cancel this candidate? Collected operational history is preserved, but this candidate can no longer continue."
          : action === "activate"
            ? "Activate this candidate and replace the current active baseline?"
            : "Roll back to the previous baseline version?",
      )
    ) return;
    setBaselineActionPending(true);
    setBaselineActionMessage("");
    setBaselineTechnicalMessage("");
    try {
      const response = await fetch(
        requestUrl(`/api/baseline-management/${action}`),
        { method: "POST" },
      );
      const result = (await response.json()) as BaselineManagementStatus & {
        detail?: string | { message: string; technical_detail?: string };
      };
      if (!response.ok) {
        const technical = typeof result.detail === "string"
          ? result.detail
          : result.detail?.technical_detail;
        const friendly = typeof result.detail === "string"
          ? "The calibration action could not be completed."
          : result.detail?.message ?? "The calibration action could not be completed.";
        setBaselineActionMessage(`${friendly} Monitoring continues with your active personal baseline.`);
        setBaselineTechnicalMessage(technical ?? "No additional technical detail is available.");
        return;
      }
      setBaselineManagement(result);
      setBaselineActionMessage(
        action === "start" ? "New calibration started. Your current personal baseline remains active."
          : action === "pause" ? "New calibration paused. Your collected progress is preserved."
            : action === "resume" ? "New calibration resumed."
              : action === "continue" ? "Calibration is continuing. It has not replaced your active baseline."
              : action === "cancel" ? "New calibration cancelled. Your active baseline was not changed."
                : action === "activate" ? "The validated new calibration is now your active personal baseline."
                  : "The previous baseline version is now active.",
      );
    } catch (error) {
      setBaselineActionMessage("The calibration action could not be completed. Monitoring continues with your active personal baseline.");
      setBaselineTechnicalMessage(
        error instanceof Error ? error.message : "No additional technical detail is available.",
      );
    } finally {
      setBaselineActionPending(false);
    }
  };

  const sendTestNotification = async () => {
    setNotificationActionPending(true);
    setNotificationActionMessage("");
    try {
      const response = await fetch(requestUrl("/api/notifications/test"), {
        method: "POST",
      });
      const result = (await response.json()) as {
        delivered: boolean;
        reason?: string | null;
      };
      if (!response.ok) {
        throw new Error(`Test notification failed with status ${response.status}.`);
      }
      setNotificationActionMessage(
        result.delivered
          ? "Test notification submitted to Windows. No predictive-alert or delivery-history record was created."
          : result.reason ?? "Windows did not accept the test notification.",
      );
    } catch {
      setNotificationActionMessage(
        "Could not send the test notification. The rest of SmartOps is still running.",
      );
    } finally {
      setNotificationActionPending(false);
    }
  };

  const postValidation = async (
    path: string,
    body: Record<string, unknown>,
    successMessage: string,
  ) => {
    const response = await fetch(requestUrl(path), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      const detail = (await response.json().catch(() => null)) as
        | { detail?: string | Array<{ msg: string }> }
        | null;
      const message = Array.isArray(detail?.detail)
        ? detail.detail.map((item) => item.msg).join(" ")
        : detail?.detail;
      throw new Error(message || `Request failed with status ${response.status}.`);
    }
    setValidationActionMessage(successMessage);
    await loadMetrics();
  };

  const reportIncident = async () => {
    if (!latest || !incidentSymptoms.trim()) {
      setValidationActionMessage("A device sample and incident symptoms are required.");
      return;
    }
    if (!window.confirm("Save this user-reported incident to the local SmartOps database?")) {
      return;
    }
    try {
      await postValidation("/api/incidents", {
        device_id: latest.device_id,
        category: incidentCategory,
        severity: incidentSeverity,
        start_utc: new Date(incidentStart).toISOString(),
        timestamp_precision: "approximate",
        verification_status: "user_reported",
        symptoms_text: incidentSymptoms.trim(),
        data_confidence: 0.75,
        confirmation: true,
      }, "Incident saved locally. Run validation when you are ready.");
      setIncidentSymptoms("");
    } catch (error) {
      setValidationActionMessage(
        error instanceof Error ? error.message : "Could not save the incident.",
      );
    }
  };

  const reviseIncident = async () => {
    const incidentId = Number(revisionIncidentId);
    if (!incidentId || !revisionReason.trim()) {
      setValidationActionMessage("Incident ID and correction reason are required.");
      return;
    }
    if (!window.confirm("Append this correction as a new incident revision?")) return;
    try {
      await postValidation(`/api/incidents/${incidentId}/revise`, {
        severity: revisionSeverity,
        revision_reason: revisionReason.trim(),
        confirmation: true,
      }, "Incident correction saved as a new revision.");
      setRevisionReason("");
    } catch (error) {
      setValidationActionMessage(
        error instanceof Error ? error.message : "Could not revise the incident.",
      );
    }
  };

  const withdrawIncident = async (incidentId: number) => {
    const reason = window.prompt("Why should this incident report be withdrawn?");
    if (!reason?.trim() || !window.confirm(
      "Withdraw this report? Its revision history will remain auditable.",
    )) return;
    try {
      await postValidation(`/api/incidents/${incidentId}/withdraw`, {
        revision_reason: reason.trim(),
        confirmation: true,
      }, "Incident withdrawn; its previous revisions were preserved.");
    } catch (error) {
      setValidationActionMessage(
        error instanceof Error ? error.message : "Could not withdraw the incident.",
      );
    }
  };

  const saveAlertFeedback = async () => {
    const alertId = Number(feedbackAlertId);
    if (!alertId) {
      setValidationActionMessage("Select or enter an alert ID.");
      return;
    }
    if (!window.confirm(
      `${feedbackMode === "create" ? "Save" : "Revise"} this alert outcome locally?`,
    )) return;
    const horizon = feedbackOutcome === "no_issue_observed"
      ? Number(feedbackHorizonHours) * 3600
      : null;
    const path = feedbackMode === "create"
      ? `/api/alerts/${alertId}/feedback`
      : `/api/alerts/${alertId}/feedback/revise`;
    try {
      await postValidation(path, {
        outcome: feedbackOutcome,
        observation_horizon_seconds: horizon,
        verification_status: "user_reported",
        notes: feedbackNotes.trim() || null,
        user_reason_codes: [],
        structured_action_taken: feedbackAction.trim() || null,
        condition_state: feedbackConditionState,
        preventive_action_taken: feedbackOutcome === "preventive_action_taken",
        data_confidence: 0.75,
        ...(feedbackMode === "revise"
          ? { revision_reason: "User corrected the recorded alert outcome." }
          : {}),
        confirmation: true,
      }, feedbackMode === "create"
        ? "Alert feedback saved locally."
        : "Alert feedback correction saved as a new revision.");
      setFeedbackNotes("");
    } catch (error) {
      setValidationActionMessage(
        error instanceof Error ? error.message : "Could not save alert feedback.",
      );
    }
  };

  const linkAlertAndIncident = async () => {
    const incidentId = Number(linkIncidentId);
    const alertId = Number(linkAlertId);
    if (!incidentId || !alertId || !linkReason.trim()) {
      setValidationActionMessage(
        "Incident ID, alert ID, and a matching reason are required.",
      );
      return;
    }
    if (!window.confirm(
      `Save this ${linkMatchType.replaceAll("_", " ")} as explicit user validation?`,
    )) return;
    try {
      await postValidation(`/api/incidents/${incidentId}/link-alert`, {
        alert_id: alertId,
        match_type: linkMatchType,
        reason: linkReason.trim(),
        confirmation: true,
      }, "Alert-to-incident classification saved locally.");
      setLinkReason("");
    } catch (error) {
      setValidationActionMessage(
        error instanceof Error ? error.message : "Could not save the link.",
      );
    }
  };

  const startObservationPeriod = async () => {
    if (!latest || !window.confirm(
      "Start a local observation period now? You must explicitly close it later.",
    )) return;
    try {
      await postValidation("/api/validation/observation-periods", {
        device_id: latest.device_id,
        start_utc: new Date().toISOString(),
        confirmation: true,
      }, "Observation period started.");
    } catch (error) {
      setValidationActionMessage(
        error instanceof Error ? error.message : "Could not start the period.",
      );
    }
  };

  const closeObservationPeriod = async (periodId: number) => {
    if (!window.confirm(
      "Close this period and declare incident reporting complete? Only do this if all known incidents in the period were reported.",
    )) return;
    try {
      await postValidation(
        `/api/validation/observation-periods/${periodId}/close`,
        {
          end_utc: new Date().toISOString(),
          state: "completed",
          incident_reporting_complete: true,
          missing_intervals: [],
          confirmation: true,
        },
        "Observation period closed. Eligibility still depends on telemetry coverage.",
      );
    } catch (error) {
      setValidationActionMessage(
        error instanceof Error ? error.message : "Could not close the period.",
      );
    }
  };

  const pageDetails = PAGE_DETAILS[activeRoute];
  const renderAlertDetails = (alert: PredictiveAlert) => {
    if (alert.id !== selectedAlertId) return null;
    const detailsId = `alert-details-${alert.id}`;
    return (
      <div
        className="alert-detail-region"
        id={detailsId}
        role="region"
        aria-label={`Full details for ${alert.title}`}
      >
        {alertDetailLoading ? (
          <div className="alert-detail-loading" role="status" aria-live="polite">
            <span className="loading-spinner" aria-hidden="true" />
            Loading full alert details from the local database…
          </div>
        ) : alertDetailError ? (
          <div className="alert-detail-error" role="alert">
            <strong>Alert details are temporarily unavailable.</strong>
            <p>{alertDetailError}</p>
            <button type="button" onClick={retryAlertDetails}>Retry</button>
          </div>
        ) : alert.summary_record ? (
          <div className="alert-detail-error" role="alert">
            <strong>Full alert details were not returned.</strong>
            <p>The alert summary remains available. Retry the local detail request.</p>
            <button type="button" onClick={retryAlertDetails}>Retry</button>
          </div>
        ) : (
          <AlertDetails
            alert={alert}
            relatedRisk={selectedAlertRisk}
            relatedCandidates={selectedAlertCandidates}
            outcomePending={outcomePending === alert.id}
            formatTimestamp={formatTimestamp}
            onLabelOutcome={(outcome) => void labelAlertOutcome(alert.id, outcome)}
            onOpenRootCause={() => setAnalysisTarget(`alert-${alert.id}`)}
          />
        )}
      </div>
    );
  };

  return (
    <AppShell
      activeRoute={activeRoute}
      connection={
        <>
          <div
            className={`connection connection--${
              loadState === "error" ? "error" : isStale ? "stale" : loadState
            }`}
            aria-live="polite"
          >
            <span aria-hidden="true" />
            {loadState === "loading"
              ? "Connecting to local database"
              : loadState === "error"
                ? "Local API unavailable"
                : isStale
                  ? "Database connected · Agent data stale"
                  : "Database connected · Agent data fresh"}
          </div>
        </>
      }
      refreshAction={
          <button
            className="refresh-button"
            type="button"
            onClick={() => void loadMetrics()}
            disabled={isRefreshing || loadState === "loading"}
          >
            <RefreshCw aria-hidden="true" size={16} />
            {isRefreshing ? "Refreshing…" : "Refresh data"}
          </button>
      }
    >

      <section className={`hero hero--${activeRoute}`}>
        <div>
          <p className="eyebrow">{pageDetails.eyebrow}</p>
          <h1>{pageDetails.title}</h1>
          <p className="subtitle">{pageDetails.description}</p>
        </div>
      </section>

      {loadState === "loading" && (
        <section className="state-panel" aria-live="polite">
          <div className="spinner" aria-hidden="true" />
          <h2>Loading local history</h2>
          <p>Connecting to the SmartOps API on this computer...</p>
        </section>
      )}

      {loadState === "error" && (
        <section className="state-panel state-panel--error" role="alert">
          <span className="state-icon" aria-hidden="true"><TriangleAlert size={22} /></span>
          <h2>SmartOps API is not reachable</h2>
          <p>
            {errorMessage} Start SmartOps with{" "}
            <code>.\scripts\run_local.ps1</code>, then try again.
          </p>
          <button type="button" onClick={() => void loadMetrics()}>
            Try again
          </button>
        </section>
      )}

      {loadState === "ready" && latest === null && (
        <section className="state-panel">
          <span className="state-icon" aria-hidden="true"><Info size={22} /></span>
          <h2>No samples yet</h2>
          <p>
            Run <code>.\scripts\collect_once.ps1</code> to store the first raw
            system data sample.
          </p>
        </section>
      )}

      {loadState === "ready" && latest && (
        <>
          {isStale && (
            <section className="stale-banner" role="status">
              <strong>Collection appears stale.</strong>
              <span>
                The latest record is {formatDuration((sampleAge ?? 0) / 1000)} old.
                Check that the agent PowerShell window is still running.
              </span>
            </section>
          )}

          {activeRoute === "overview" && (
            <OverviewDashboard
              latest={latest}
              totalSamples={totalSamples}
              sampleAgeMs={sampleAge}
              isStale={isStale}
              chartHistory={chartHistory}
              workload={workload}
              features={features}
              baselineState={baseline?.state ?? null}
              latestHealth={latestHealth}
              healthHistory={healthHistory}
              latestRisk={latestRisk}
              riskReason={riskStatus?.reason_code ?? null}
              fineQuality={fineQuality}
              alertStatus={alertStatus}
              alerts={alertHistory}
              pipelineStatus={pipelineStatus}
              liveState={chartLiveState}
              onRefresh={() => void loadMetrics()}
              isRefreshing={isRefreshing}
            />
          )}

          {activeRoute === "live-monitoring" && (
            <LiveMonitoringSummary
              latest={latest}
              workload={workload}
              totalSamples={totalSamples}
              sampleAgeLabel={formatDuration((sampleAge ?? 0) / 1000)}
              formattedTimestamp={formatTimestamp(latest.timestamp_utc)}
              liveState={chartLiveState}
              isStale={isStale}
              isRefreshing={isRefreshing}
              onRefresh={() => void loadMetrics()}
            />
          )}

          {activeRoute === "live-monitoring" && (
            <section
              className="enhanced-evidence-section"
              aria-label="Advanced system signals"
            >
            <div className="section-heading section-heading--controls">
              <div>
                <p className="eyebrow">Optional local Windows evidence</p>
                <h2>Advanced System Signals</h2>
              </div>
              <span className="shadow-mode-badge">Experimental signal — does not affect results</span>
            </div>
            <p className="interpretation-statement">
              Advanced system signals are collected locally for additional
              context. They do not change risk, health, root-cause ranking,
              predictive alerts, or notifications.
            </p>
            <p className="enhanced-capability-message">
              {enhancedEvidence?.capability_display_message
                ?? "Only evidence supported by this device is displayed."}
            </p>

            {enhancedEvidence === null || enhancedEvidence.signals.length === 0 ? (
              <div className="enhanced-evidence-empty">
                <strong>Advanced signal history is still being collected.</strong>
                <p>
                  Normal 30-second system data collection remains active. Supported
                  performance counters are sampled once their lower-frequency
                  collection is due.
                </p>
              </div>
            ) : (
              <>
                <div className="enhanced-summary">
                  <article>
                    <span>Available</span>
                    <strong>
                      {enhancedEvidence.signals.filter(
                        (signal) =>
                          signal.display_state === "Available"
                          || signal.availability_status === "available",
                      ).length}
                    </strong>
                  </article>
                  <article>
                    <span>Collecting history</span>
                    <strong>
                      {enhancedEvidence.signals.filter(
                        (signal) => signal.display_state === "Collecting history",
                      ).length}
                    </strong>
                  </article>
                  <article>
                    <span>Temporarily unavailable</span>
                    <strong>
                      {enhancedEvidence.signals.filter(
                        (signal) => signal.display_state === "Temporarily unavailable",
                      ).length}
                    </strong>
                  </article>
                  <article>
                    <span>Not supported / not applicable</span>
                    <strong>
                      {enhancedEvidence.signals.filter(
                        (signal) => ["Unsupported on this device", "Not applicable"].includes(signal.display_state),
                      ).length}
                    </strong>
                  </article>
                </div>

                <div className="enhanced-signal-grid">
                  {enhancedEvidence.signals.map((signal) => (
                    <article
                      className={`enhanced-signal-card enhanced-signal-card--${(signal.display_state ?? (signal.availability_status === "available" ? "Available" : "Temporarily unavailable")).toLowerCase().replaceAll(" ", "-")}`}
                      key={signal.signal_key}
                    >
                      <header>
                        <span>{signal.signal_group}</span>
                        <em>{signal.display_state ?? signal.readiness_state.replaceAll("_", " ")}</em>
                      </header>
                      <h3>{signal.signal_label}</h3>
                      <strong>{formatEnhancedSignal(signal)}</strong>
                      <dl>
                        <div>
                          <dt>Recent trend</dt>
                          <dd>{signal.trend.replaceAll("_", " ")}</dd>
                        </div>
                        <div>
                          <dt>Source status</dt>
                          <dd>{signal.source_status.replaceAll("_", " ")}</dd>
                        </div>
                        <div>
                          <dt>Evidence status</dt>
                          <dd>{signal.display_state ?? signal.readiness_state.replaceAll("_", " ")}</dd>
                        </div>
                        <div>
                          <dt>Available readings</dt>
                          <dd>{(signal.available_history_count ?? (signal.numeric_value == null ? 0 : 1)).toLocaleString()}</dd>
                        </div>
                        <div>
                          <dt>Last collection</dt>
                          <dd>{formatTimestamp(signal.timestamp_utc)}</dd>
                        </div>
                      </dl>
                      <p>{signal.source_name}</p>
                      {signal.reason_code && (
                        <small>
                          Reason: {signal.reason_code.replaceAll("_", " ")}
                        </small>
                      )}
                    </article>
                  ))}
                </div>
              </>
            )}

            <details className="enhanced-technical-panel">
              <summary>Technical Details</summary>
              <p>
                Collector cadence, source diagnostics, query duration, and measured local-agent overhead.
                These experimental signals do not affect SmartOps results.
              </p>
            <div className="enhanced-source-status">
              <div>
                <h3>Data-source status</h3>
                <ul>
                  {(enhancedEvidence?.collectors ?? []).map((collector) => (
                    <li key={collector.collector_key}>
                      <strong>
                        {collector.collector_key.replaceAll("_", " ")}
                      </strong>
                      <span>
                        {collector.availability_status.replaceAll("_", " ")}
                        {" · "}
                        every {collector.collection_frequency_seconds} seconds
                      </span>
                      <span>
                        Last successful collection:{" "}
                        {collector.last_success_utc
                          ? formatTimestamp(collector.last_success_utc)
                          : "Unavailable"}
                      </span>
                      <span>
                        Schedule: {collector.last_gap_classification?.replaceAll("_", " ") ?? "not yet classified"}
                        {collector.last_schedule_delay_seconds == null
                          ? ""
                          : ` · ${collector.last_schedule_delay_seconds.toFixed(1)} s delay`}
                      </span>
                      {collector.reason_code && (
                        <small>
                          {collector.reason_code.replaceAll("_", " ")}
                        </small>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
              <div>
                <h3>Measured agent overhead</h3>
                <dl>
                  <div>
                    <dt>Enhanced query time</dt>
                    <dd>
                      {enhancedEvidence?.latest_run?.collection_duration_ms ===
                      null
                      || enhancedEvidence?.latest_run?.collection_duration_ms ===
                        undefined
                        ? "Unavailable"
                        : `${enhancedEvidence.latest_run.collection_duration_ms.toFixed(1)} ms`}
                    </dd>
                  </div>
                  <div>
                    <dt>Full collection cycle</dt>
                    <dd>
                      {enhancedEvidence?.latest_run?.full_cycle_duration_ms ===
                      null
                      || enhancedEvidence?.latest_run?.full_cycle_duration_ms ===
                        undefined
                        ? "Unavailable"
                        : `${enhancedEvidence.latest_run.full_cycle_duration_ms.toFixed(1)} ms`}
                    </dd>
                  </div>
                  <div>
                    <dt>Approximate agent CPU (system scale)</dt>
                    <dd>
                      {enhancedEvidence?.latest_run
                        ?.approximate_process_cpu_percent === null
                      || enhancedEvidence?.latest_run
                        ?.approximate_process_cpu_percent === undefined
                        ? "Unavailable"
                        : `${enhancedEvidence.latest_run.approximate_process_cpu_percent.toFixed(2)}%`}
                    </dd>
                  </div>
                  <div>
                    <dt>Agent memory change</dt>
                    <dd>
                      {enhancedEvidence?.latest_run?.process_rss_before_bytes ===
                        null
                      || enhancedEvidence?.latest_run?.process_rss_after_bytes ===
                        null
                      || enhancedEvidence?.latest_run?.process_rss_before_bytes ===
                        undefined
                      || enhancedEvidence?.latest_run?.process_rss_after_bytes ===
                        undefined
                        ? "Unavailable"
                        : formatBytes(
                            Math.abs(
                              enhancedEvidence.latest_run.process_rss_after_bytes
                                - enhancedEvidence.latest_run.process_rss_before_bytes,
                            ),
                          )}
                    </dd>
                  </div>
                  <div>
                    <dt>Database growth this cycle</dt>
                    <dd>
                      {enhancedEvidence?.latest_run?.database_bytes_before ===
                        null
                      || enhancedEvidence?.latest_run?.database_bytes_after ===
                        null
                      || enhancedEvidence?.latest_run?.database_bytes_before ===
                        undefined
                      || enhancedEvidence?.latest_run?.database_bytes_after ===
                        undefined
                        ? "Unavailable"
                        : formatBytes(
                            Math.max(
                              0,
                              enhancedEvidence.latest_run.database_bytes_after
                                - enhancedEvidence.latest_run.database_bytes_before,
                            ),
                          )}
                    </dd>
                  </div>
                </dl>
              </div>
            </div>
            {(enhancedEvidence?.technical_diagnostics?.length ?? 0) > 0 && (
              <div className="enhanced-technical-diagnostics">
                <button
                  type="button"
                  aria-expanded={showEnhancedDiagnostics}
                  onClick={() => setShowEnhancedDiagnostics((current) => !current)}
                >
                  {showEnhancedDiagnostics ? "Hide" : "Show"} technical diagnostics
                </button>
                {showEnhancedDiagnostics && (
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Display state</th>
                          <th>Reason</th>
                          <th>Source</th>
                          <th>Signals</th>
                        </tr>
                      </thead>
                      <tbody>
                        {enhancedEvidence?.technical_diagnostics?.map((item) => (
                          <tr key={`${item.display_state}-${item.reason_code}-${item.source_name}`}>
                            <td>{item.display_state}</td>
                            <td>{item.reason_code.replaceAll("_", " ")}</td>
                            <td>{item.source_name}</td>
                            <td>{item.signal_count}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}
            </details>
            </section>
          )}

          <section className="phase2b-section" hidden={activeRoute !== "live-monitoring"}>
            <div className="section-heading">
              <div>
                <p className="eyebrow">Workload context</p>
                <h2>Current operational context</h2>
              </div>
              <span>Context only - not a health or failure assessment</span>
            </div>
            <div className="context-grid">
              <article className="context-card">
                <span className="workload-badge">
                  {workload?.workload_class?.replaceAll("_", " ") ?? "unknown"}
                </span>
                <strong>
                  Confidence{" "}
                  {workload?.workload_confidence === null || !workload
                    ? "Unavailable"
                    : `${(workload.workload_confidence * 100).toFixed(0)}%`}
                </strong>
                <p>
                  {workload?.reason_codes.length
                    ? workload.reason_codes.join(", ").replaceAll("_", " ")
                    : "Insufficient context signals"}
                </p>
              </article>
              <article className="context-card">
                <span>User activity</span>
                <strong>
                  {latest.user_activity_state ?? latest.user_state ?? "Unavailable"}
                </strong>
                <p>
                  Based on safe input-idle duration; SmartOps background work
                  does not make the user active.
                </p>
              </article>
              <article className="context-card">
                <span>System activity</span>
                <strong>{latest.system_activity_state ?? "Unavailable"}</strong>
                <p>
                  Quiescent, background or busy is recorded separately from
                  user activity. High utilization can be normal for the workload.
                </p>
              </article>
              <article className="context-card">
                <span>System data collection</span>
                <strong>{chartLiveState === "live" ? "Current" : chartLiveState.replaceAll("_", " ")}</strong>
                <p>Raw local measurements are scheduled every 30 seconds. Latest sample: {formatTimestamp(latest.timestamp_utc)}.</p>
              </article>
              <article className="context-card">
                <span>Five-minute analysis</span>
                <strong>{features[0]?.is_complete ? "Complete" : features[0] ? "Incomplete" : "Not available"}</strong>
                <p>{features[0] ? `Latest completed period ended ${formatTimestamp(features[0].window_end_utc)}.` : "No stored analysis period is available."}</p>
              </article>
            </div>
          </section>

          <section className="phase2b-section" hidden={activeRoute !== "live-monitoring"}>
            <div className="section-heading">
              <div>
                <p className="eyebrow">Operational evidence</p>
                <h2>Recent Windows events</h2>
              </div>
              <span>Mapped metadata, not predicted failures</span>
            </div>
            {eventSummary?.channels.some((channel) => !channel.available) && (
              <div className="event-unavailable">
                Event Log Access Unavailable for{" "}
                {eventSummary.channels
                  .filter((channel) => !channel.available)
                  .map((channel) => channel.channel)
                  .join(", ")}
              </div>
            )}
            <div className="event-counts">
              {["Critical", "Error", "Warning"].map((level) => (
                <article className="detail-card" key={level}>
                  <span>{level} events</span>
                  <strong>{eventSummary?.severity[level] ?? 0}</strong>
                </article>
              ))}
            </div>
            <div className="event-categories" aria-label="Event counts by category">
              {Object.entries(eventSummary?.categories ?? {}).map(
                ([category, count]) => (
                  <article className="detail-card" key={category}>
                    <span>{category.replaceAll("_", " ")}</span>
                    <strong>{count}</strong>
                  </article>
                ),
              )}
              {Object.keys(eventSummary?.categories ?? {}).length === 0 && (
                <p>No mapped event categories have been stored.</p>
              )}
            </div>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Time</th><th>Level</th><th>Channel</th><th>Category</th><th>Event</th><th>Safe summary</th></tr></thead>
                <tbody>
                  {events.map((event) => (
                    <tr key={event.id}>
                      <td>{formatTimestamp(event.event_timestamp_utc)}</td>
                      <td>{event.event_level}</td><td>{event.channel}</td>
                      <td>{event.smartops_category.replaceAll("_", " ")}</td>
                      <td>{event.provider_name} / {event.event_id}</td>
                      <td>{event.safe_summary}</td>
                    </tr>
                  ))}
                  {events.length === 0 && <tr><td colSpan={6}>No relevant events stored.</td></tr>}
                </tbody>
              </table>
            </div>
          </section>

          <section className="phase2b-section" hidden={activeRoute !== "live-monitoring"}>
            <div className="section-heading">
              <div>
                <p className="eyebrow">Five-minute features</p>
                <h2>Aggregation windows</h2>
              </div>
              <span>Derived from raw 30-second samples</span>
            </div>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Window start</th><th>Coverage</th><th>Status</th><th>Dominant workload</th><th>User activity</th><th>System activity</th><th>Classification detail</th><th>CPU avg</th><th>RAM avg</th><th>Events</th></tr></thead>
                <tbody>
                  {features.map((feature) => (
                    <tr key={feature.id}>
                      <td>{formatTimestamp(feature.window_start_utc)}</td>
                      <td>{(feature.coverage_ratio * 100).toFixed(0)}% ({feature.sample_count}/{feature.expected_sample_count})</td>
                      <td>{feature.is_complete ? "Complete" : "Incomplete"}</td>
                      <td>{feature.dominant_workload_class?.replaceAll("_", " ") ?? "Unavailable"}</td>
                      <td>{feature.dominant_user_activity_state?.replaceAll("_", " ") ?? "Historical / unavailable"}</td>
                      <td>{feature.dominant_system_activity_state?.replaceAll("_", " ") ?? "Historical / unavailable"}</td>
                      <td>
                        {feature.workload_majority_explanation
                          ?? "Detailed workload evidence was not recorded for this older analysis period."}
                        {feature.secondary_workload_context && (
                          <><br /><strong>
                            Mixed context: {feature.secondary_workload_context.replaceAll("_", " ")}
                          </strong></>
                        )}
                        {feature.workload_composition && (
                          <><br /><span>
                            {Object.entries(feature.workload_composition.profiles)
                              .filter(([, value]) => value.count > 0)
                              .map(([profile, value]) =>
                                `${profile.replaceAll("_", " ")}: ${value.count} (${(value.proportion * 100).toFixed(0)}%)`,
                              )
                              .join(" · ")}
                          </span></>
                        )}
                      </td>
                      <td>{formatPercent(feature.cpu_avg)}</td>
                      <td>{formatPercent(feature.ram_avg)}</td>
                      <td>{feature.critical_event_count + feature.error_event_count + feature.warning_event_count}</td>
                    </tr>
                  ))}
                  {features.length === 0 && <tr><td colSpan={10}>No completed analysis periods are available yet.</td></tr>}
                </tbody>
              </table>
            </div>
          </section>

          <div hidden={activeRoute !== "root-cause-analysis"}>
            <RootCauseDashboard
              risks={riskHistory}
              latestRisk={risk?.assessment ?? riskHistory[0] ?? null}
              alerts={alertHistory}
              baselineState={baseline?.state ?? null}
              range={range}
              onRangeChange={changeRange}
              workload={riskWorkload}
              onWorkloadChange={setRiskWorkload}
              formatTimestamp={formatTimestamp}
              apiBaseUrl={API_BASE_URL}
              stale={isStale}
            />
          </div>

          {Boolean(0) && <section
            className="phase3-section phase3-section--legacy"
            hidden
          >
            <p className="interpretation-statement">
              Root-cause results identify probable contributing factors
              supported by available operational evidence. They are not
              confirmed hardware diagnoses.
            </p>
            <div className="analysis-selector">
              <label htmlFor="analysis-target">
                <span>Evidence source</span>
                <select
                  id="analysis-target"
                  value={analysisTarget}
                  onChange={(event) => setAnalysisTarget(event.target.value)}
                >
                  <option value="latest-risk">Latest eligible risk assessment</option>
                  {riskHistory.map((item) => (
                    <option key={`risk-${item.id}`} value={`risk-${item.id}`}>
                      Risk assessment #{item.id} · {formatTimestamp(item.window_end_utc)}
                    </option>
                  ))}
                  {alertHistory.map((item) => (
                    <option key={`alert-${item.id}`} value={`alert-${item.id}`}>
                      Alert {item.alert_code} · {item.title}
                    </option>
                  ))}
                </select>
              </label>
              <div>
                <span>Selected evidence</span>
                <strong>
                  {selectedAlert
                    ? `${selectedAlert.alert_code}: ${selectedAlert.title}`
                    : latestRisk
                      ? `Risk assessment #${latestRisk.id}`
                      : "No eligible alert or risk assessment"}
                </strong>
                <p>
                  {selectedAlert
                    ? `${selectedAlert.probable_factors.length} probable factor(s), ${selectedAlert.evidence.length} supporting evidence item(s)`
                    : latestRisk
                      ? `${latestRisk.candidates.length} ranked candidate(s) · ${latestRisk.data_quality_status.replaceAll("_", " ")}`
                      : "SmartOps does not invent a cause when upstream evidence is unavailable."}
                </p>
              </div>
            </div>
            {!selectedAlert && !latestRisk && (
              <div className="rca-selection-empty" role="status">
                <strong>No alert-specific or evaluated risk evidence is selected.</strong>
                <p>
                  SmartOps will show interpreted contributing evidence here only
                  when an eligible alert or Risk Evidence assessment exists. The
                  complete advanced-signal catalogue remains on Live Monitoring.
                </p>
              </div>
            )}
            {selectedAlert && (
              <section
                className="selected-alert-evidence"
                aria-label="Alert-specific interpreted evidence"
              >
                <div className="selected-alert-evidence__heading">
                  <div>
                    <p className="eyebrow">Selected alert evidence only</p>
                    <h2>Alert-specific interpreted evidence</h2>
                  </div>
                  <span>
                    {selectedAlert.evaluation_state.replaceAll("_", " ")}
                  </span>
                </div>
                <p className="selected-alert-evidence__scope">
                  This workspace uses evidence stored with the selected alert and
                  its matching analysis period. It does not treat unrelated current
                  unrelated live readings as proof of a cause.
                </p>
                <div className="selected-alert-analysis">
                <article>
                  <h3>Ranked likely contributing causes</h3>
                  {selectedAlert.probable_factors.length ? (
                    <ol>
                      {selectedAlert.probable_factors.map((factor) => (
                        <li key={`${factor.rank}-${factor.domain}`}>
                          <strong>{factor.domain.replaceAll("_", " ")}</strong>
                          {factor.confidence === null
                            ? " · confidence unavailable"
                            : ` · ${(factor.confidence * 100).toFixed(0)}% evidence confidence`}
                        </li>
                      ))}
                    </ol>
                  ) : (
                    <p>No probable factor is supported by this alert.</p>
                  )}
                </article>
                <article>
                  <h3>Evidence strength and completeness</h3>
                  <p>
                    Data confidence {selectedAlert.data_confidence.toFixed(1)}%
                    {" · "}{selectedAlert.evidence.length} supporting item(s)
                    {" · "}{selectedAlert.excluded_inputs.length} excluded input(s)
                  </p>
                  <p>
                    Evaluation {selectedAlert.evaluation_state.replaceAll("_", " ")}
                    {" · "}analysis period #{selectedAlert.source_feature_window_id}
                  </p>
                </article>
                <article>
                  <h3>Supporting signals and relationships</h3>
                  {selectedAlert.evidence.length ? (
                    <ul>
                      {selectedAlert.evidence.map((item) => (
                        <li key={item.id}>
                          <strong>{item.evidence_key.replaceAll("_", " ")}</strong>
                          {" · "}{item.correlation_group.replaceAll("_", " ")}: {item.explanation}
                          {item.suppressed
                            ? ` Excluded from another contribution (${item.suppression_reason?.replaceAll("_", " ") ?? "correlation rule"}).`
                            : ""}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p>No supporting signal was stored for this alert.</p>
                  )}
                </article>
                <article>
                  <h3>Contradicting or healthy evidence</h3>
                  {selectedAlert.contradictory_evidence.length ? (
                    <ul>
                      {selectedAlert.contradictory_evidence.map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  ) : (
                    <p>No explicit contradictory or healthy evidence was recorded.</p>
                  )}
                </article>
                <article>
                  <h3>Temporal sequence and system context</h3>
                  <dl>
                    <div><dt>First observed</dt><dd>{formatTimestamp(selectedAlert.first_observed_utc)}</dd></div>
                    <div><dt>Most recent</dt><dd>{formatTimestamp(selectedAlert.latest_observed_utc)}</dd></div>
                    <div><dt>Persistence</dt><dd>{selectedAlert.consecutive_window_count} windows · {formatDuration(selectedAlert.duration_seconds)}</dd></div>
                    <div><dt>Trend / recovery</dt><dd>{selectedAlert.trend_direction.replaceAll("_", " ")} · {selectedAlert.recovery_state.replaceAll("_", " ")}</dd></div>
                    <div><dt>Workload</dt><dd>{selectedAlert.workload_context?.replaceAll("_", " ") ?? "Unavailable"}</dd></div>
                    <div><dt>Workload confidence</dt><dd>{selectedAlert.workload_confidence === null ? "Unavailable" : `${(selectedAlert.workload_confidence * 100).toFixed(0)}%`}</dd></div>
                  </dl>
                </article>
                <article>
                  <h3>Missing or unavailable evidence</h3>
                  {selectedAlert.excluded_inputs.length ? (
                    <ul>
                      {selectedAlert.excluded_inputs.map((item) => (
                        <li key={`${item.input}-${item.status}`}>
                          <strong>{item.input.replaceAll("_", " ")}</strong>: {item.status.replaceAll("_", " ")}
                          {item.reason ? ` · ${item.reason.replaceAll("_", " ")}` : ""}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p>No excluded input was recorded for this alert.</p>
                  )}
                </article>
                <article>
                  <h3>Recommended safe diagnostic actions</h3>
                  {selectedAlert.diagnostic_recommendations.length ? (
                    <ol>
                      {selectedAlert.diagnostic_recommendations.map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ol>
                  ) : (
                    <p>No diagnostic action was recorded.</p>
                  )}
                </article>
                <article>
                  <h3>Evidence limitations</h3>
                  <p>
                    Ranked causes are evidence-supported hypotheses, not confirmed
                    hardware diagnoses. Missing inputs are not converted to zero,
                    and SmartOps performs no automatic remediation.
                  </p>
                  {selectedAlert.preventive_guidance.length > 0 && (
                    <ul>
                      {selectedAlert.preventive_guidance.map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  )}
                </article>
                </div>
              </section>
            )}
            <div className="section-heading section-heading--controls">
              <div>
                <p className="eyebrow">Device-specific learning</p>
                <h2>Device baseline and deviation</h2>
              </div>
              <div className="deviation-filter">
                <label htmlFor="deviation-workload"><ListFilter aria-hidden="true" size={15} />Workload</label>
                <select
                  id="deviation-workload"
                  value={deviationWorkload}
                  onChange={(event) => setDeviationWorkload(event.target.value)}
                >
                  <option value="all">All workloads</option>
                  {[
                    "idle",
                    "interactive_light",
                    "office_productivity",
                    "browser_or_media",
                    "development",
                    "gaming_or_3d",
                    "compute_intensive",
                    "background_activity",
                    "unknown",
                  ].map((item) => (
                    <option value={item} key={item}>
                      {item.replaceAll("_", " ")}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <p className="deviation-explainer">
              Deviation indicates a difference from this device&apos;s learned
              operating pattern. It is not a confirmed failure or failure
              probability.
            </p>

            <div className="baseline-summary">
              <article className="context-card">
                <span>Baseline readiness</span>
                <strong className={`readiness readiness--${baseline?.state ?? "unavailable"}`}>
                  {(baseline?.state ?? "unavailable").replaceAll("_", " ")}
                </strong>
                <p>
                  {baseline?.eligible_window_count ?? 0} eligible analysis periods across{" "}
                  {baseline?.distinct_collection_days ?? 0} collection days
                </p>
              </article>
              <article className="context-card">
                <span>History guidance</span>
                <strong>
                  {baseline?.recommended_history_days ?? 7}+ days recommended
                </strong>
                <p>
                  Minimum {baseline?.minimum_device_windows ?? 100} device analysis periods
                  and {baseline?.minimum_distinct_days ?? 3} distinct days
                </p>
              </article>
              <article className="context-card">
                <span>Last successful training</span>
                <strong>
                  {baseline?.last_successful_training_utc
                    ? formatTimestamp(baseline.last_successful_training_utc)
                    : "Not trained yet"}
                </strong>
                <p>
                  Workload profiles: {baseline?.workload_profiles.length ?? 0}
                </p>
              </article>
              <article className="context-card">
                <span>Current baseline scope</span>
                <strong>
                  {latestDeviation?.baseline_scope ?? "Not evaluated"}
                </strong>
                <p>
                  Isolation Forest:{" "}
                  {latestDeviation?.isolation_forest_result?.replaceAll("_", " ")
                    ?? baseline?.device_profile?.isolation_forest.readiness_state
                    ?? "not evaluated"}
                </p>
              </article>
            </div>
            <div className="profile-status-list" aria-label="Baseline profile readiness">
              <span>
                Device-wide:{" "}
                <strong>
                  {baseline?.device_profile?.readiness_state.replaceAll("_", " ")
                    ?? "not trained"}
                </strong>
              </span>
              {(baseline?.workload_profiles ?? []).map((profile) => (
                <span key={profile.id}>
                  {profile.workload_scope.replaceAll("_", " ")}:{" "}
                  <strong>{profile.readiness_state.replaceAll("_", " ")}</strong>
                  {" "}({profile.eligible_window_count})
                </span>
              ))}
            </div>

            {deviation?.status !== "evaluated" || !latestDeviation ? (
              <div className="baseline-cold-start">
                <strong>Not enough history yet</strong>
                <p>
                  SmartOps is collecting complete five-minute windows. No
                  Deviation Index is fabricated during cold start.
                </p>
              </div>
            ) : (
              <>
                <div className="deviation-current">
                  <article className={`deviation-index deviation-index--${latestDeviation.overall_level}`}>
                    <span>Current Deviation Index</span>
                    <strong>
                      {latestDeviation.deviation_index === null
                        ? "Not evaluated"
                        : latestDeviation.deviation_index.toFixed(0)}
                    </strong>
                    <p>{latestDeviation.overall_level.replaceAll("_", " ")}</p>
                  </article>
                  <article className="context-card">
                    <span>Statistical result</span>
                    <strong>{latestDeviation.overall_level.replaceAll("_", " ")}</strong>
                    <p>Data quality: {latestDeviation.data_quality_status}</p>
                  </article>
                  <article className="context-card">
                    <span>Isolation Forest</span>
                    <strong>
                      {latestDeviation.isolation_forest_result.replaceAll("_", " ")}
                    </strong>
                    <p>
                      {latestDeviation.isolation_forest_score === null
                        ? "Insufficient model history"
                        : `Local model score ${latestDeviation.isolation_forest_score.toFixed(3)}`}
                    </p>
                  </article>
                </div>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Metric</th><th>Observed</th><th>Expected range</th>
                        <th>Severity</th><th>Explanation</th>
                      </tr>
                    </thead>
                    <tbody>
                      {latestDeviation.feature_results
                        .filter((item) => item.severity_band !== "not_evaluated")
                        .slice(0, 8)
                        .map((item) => (
                          <tr key={item.feature_name}>
                            <td>{item.feature_name.replaceAll("_", " ")}</td>
                            <td>{formatFeatureValue(item.observed_value)}</td>
                            <td>
                              {formatFeatureValue(item.expected_low)} –{" "}
                              {formatFeatureValue(item.expected_high)}
                            </td>
                            <td>{item.severity_band}</td>
                            <td>{item.reason_code.replaceAll("_", " ")}</td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}

            <div className="deviation-history">
              <div>
                <h3>Historical Deviation Index</h3>
                <span>{deviationHistory.length} evaluated analysis periods in range</span>
              </div>
              <DeviationChart data={deviationHistory} />
            </div>
            {latestDeviation?.reason_codes.length ? (
              <p className="deviation-reasons">
                Top reasons:{" "}
                {latestDeviation.reason_codes.join(", ").replaceAll("_", " ")}
              </p>
            ) : null}
          </section>}

          {Boolean(0) && <section
            className="phase3-section risk-section risk-section--legacy"
            id="risk-evidence"
            hidden
          >
            <div className="section-heading section-heading--controls">
              <div>
                <p className="eyebrow">Interpretable evidence fusion</p>
                <h2>Risk evidence and contributing factors</h2>
              </div>
              <div className="risk-controls">
                <div className="deviation-filter">
                  <label htmlFor="risk-workload">Workload</label>
                  <select
                    id="risk-workload"
                    value={riskWorkload}
                    onChange={(event) => setRiskWorkload(event.target.value)}
                  >
                    <option value="all">All workloads</option>
                    {[
                      "idle",
                      "interactive_light",
                      "office_productivity",
                      "browser_or_media",
                      "development",
                      "gaming_or_3d",
                      "compute_intensive",
                      "background_activity",
                    ].map((item) => (
                      <option value={item} key={item}>
                        {item.replaceAll("_", " ")}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="range-control" aria-label="Risk evidence time range">
                  {(Object.keys(rangeLabels) as HistoryRange[]).map((option) => (
                    <button
                      type="button"
                      className={range === option ? "active" : ""}
                      onClick={() => changeRange(option)}
                      key={option}
                    >
                      {rangeLabels[option]}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            <p className="risk-explainer">
              Risk Evidence Index represents the strength of operational evidence
              associated with potential system problems. It is not a failure
              probability, confirmed failure, or confirmed root cause.
            </p>

            {risk?.status !== "evaluated" || !latestRisk ? (
              <div className="risk-not-evaluated">
                <div>
                  <span>Current evaluation</span>
                  <strong>Not evaluated</strong>
                  <p>
                    Reason:{" "}
                    {(risk?.reason_code ?? riskStatus?.reason_code ?? "prerequisites_unavailable")
                      .replaceAll("_", " ")}
                  </p>
                  <p>
                    Baseline:{" "}
                    {(risk?.baseline_state ?? riskStatus?.baseline_state ?? "unavailable")
                      .replaceAll("_", " ")}
                  </p>
                </div>
                <div>
                  <strong>Evaluation prerequisites</strong>
                  <ul>
                    {(risk?.prerequisites ?? riskStatus?.prerequisites ?? []).map(
                      (prerequisite) => <li key={prerequisite}>{prerequisite}</li>,
                    )}
                  </ul>
                </div>
                <p className="risk-event-separation">
                  Serious Windows events remain visible in the operational evidence
                  section, but they do not create a predictive score while baseline
                  or deviation prerequisites are unavailable.
                </p>
              </div>
            ) : (
              <>
                <div className="risk-summary">
                  <article className={`risk-index risk-index--${latestRisk.evidence_level}`}>
                    <span>Risk Evidence Index</span>
                    <strong>{latestRisk.risk_evidence_index.toFixed(0)}</strong>
                    <p>{latestRisk.evidence_level.replaceAll("_", " ")}</p>
                  </article>
                  <article className="context-card">
                    <span>Evidence level</span>
                    <strong>{latestRisk.evidence_level.replaceAll("_", " ")}</strong>
                    <p>Confidence in evidence, not failure probability</p>
                  </article>
                  <article className="context-card">
                    <span>Persistence and trend</span>
                    <strong>{latestRisk.temporal_pattern.replaceAll("_", " ")}</strong>
                    <p>{latestRisk.persistence_window_count} consecutive window(s)</p>
                  </article>
                  <article className="context-card">
                    <span>Data quality</span>
                    <strong>{latestRisk.data_quality_status.replaceAll("_", " ")}</strong>
                    <p>
                      Score reconstruction: {latestRisk.score_reconstruction.toFixed(2)}
                    </p>
                  </article>
                </div>

                <div className="risk-subsection">
                  <h3>Evidence component breakdown</h3>
                  <p>
                    Correlated metric summaries contribute once through their
                    configured group.
                  </p>
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Component</th><th>Correlation group</th>
                          <th>Contribution</th><th>Explanation</th>
                        </tr>
                      </thead>
                      <tbody>
                        {latestRisk.components.map((component) => (
                          <tr key={`${component.component_name}-${component.correlation_group}`}>
                            <td>{component.component_name.replaceAll("_", " ")}</td>
                            <td>
                              {component.correlation_group
                                ? component.correlation_group.replaceAll("_", " ")
                                : "Independent"}
                            </td>
                            <td>
                              {component.contribution > 0 ? "+" : ""}
                              {component.contribution.toFixed(2)}
                            </td>
                            <td>{component.reason_code.replaceAll("_", " ")}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>

                <div className="risk-subsection">
                  <h3>Relevant correlated events</h3>
                  {correlatedRiskEvents.length === 0 ? (
                    <p className="risk-empty">No mapped events correlated with this window.</p>
                  ) : (
                    <div className="table-wrap">
                      <table>
                        <thead>
                          <tr><th>Time</th><th>Timing</th><th>Level</th><th>Category</th><th>Safe summary</th></tr>
                        </thead>
                        <tbody>
                          {correlatedRiskEvents.map((event) => (
                            <tr key={event.id}>
                              <td>{formatTimestamp(event.event_timestamp_utc)}</td>
                              <td>{event.timing}</td>
                              <td>{event.event_level}</td>
                              <td>{event.smartops_category.replaceAll("_", " ")}</td>
                              <td>{event.safe_summary}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>

                <div className="risk-subsection">
                  <h3>Ranked contributing-factor candidates</h3>
                  {latestRisk.candidates.length === 0 ? (
                    <p className="risk-empty">
                      No evidence-supported root-cause candidate was produced.
                    </p>
                  ) : (
                    <div className="candidate-list">
                      {latestRisk.candidates.map((candidate) => (
                        <article className="candidate-card" key={candidate.id}>
                          <div className="candidate-card__heading">
                            <div>
                              <span>Candidate {candidate.rank}</span>
                              <h4>{candidate.candidate_domain.replaceAll("_", " ")}</h4>
                            </div>
                            <strong>
                              Confidence in evidence{" "}
                              {(candidate.evidence_confidence * 100).toFixed(0)}%
                            </strong>
                          </div>
                          <p>{candidate.explanation}</p>
                          <div className="candidate-grid">
                            <div>
                              <h5>Supporting evidence</h5>
                              <ul>
                                {[
                                  ...candidate.supporting_metrics,
                                  ...candidate.supporting_events,
                                ].map((evidence, index) => (
                                  <li key={`${evidence.evidence_key}-${index}`}>
                                    {evidence.reason_code.replaceAll("_", " ")}
                                  </li>
                                ))}
                              </ul>
                            </div>
                            <div>
                              <h5>Contradictory evidence</h5>
                              {candidate.contradictory_evidence.length ? (
                                <ul>
                                  {candidate.contradictory_evidence.map(
                                    (evidence, index) => (
                                      <li key={`${evidence.evidence_key}-${index}`}>
                                        {evidence.reason_code.replaceAll("_", " ")}
                                      </li>
                                    ),
                                  )}
                                </ul>
                              ) : <p>None recorded.</p>}
                            </div>
                            <div>
                              <h5>Recommended verification</h5>
                              <ul>
                                {candidate.recommended_verification_steps.map(
                                  (step) => <li key={step}>{step}</li>,
                                )}
                              </ul>
                            </div>
                            <div>
                              <h5>Limitations</h5>
                              <ul>
                                {candidate.limitations.map(
                                  (limitation) => <li key={limitation}>{limitation}</li>,
                                )}
                              </ul>
                            </div>
                          </div>
                        </article>
                      ))}
                    </div>
                  )}
                </div>
              </>
            )}

            <div className="deviation-history">
              <div>
                <h3>Historical Risk Evidence Index</h3>
                <span>{riskHistory.length} evaluated analysis periods in range</span>
              </div>
              <RiskChart data={riskHistory} />
            </div>
          </section>}

          <div hidden={activeRoute !== "system-health"}>
            <SystemHealthDashboard
              latest={health?.assessment ?? null}
              history={healthHistory}
              interpretation={health?.interpretation ?? healthStatus?.interpretation ?? "System Health Score summarizes the computer's current observed operating condition using available telemetry, stability and operational evidence. It is not a failure probability, future-reliability guarantee or PC quality rating."}
              statusReasons={health?.reason_codes ?? healthStatus?.reason_codes ?? []}
              range={range}
              onRangeChange={changeRange}
              workload={healthWorkload}
              onWorkloadChange={setHealthWorkload}
              formatTimestamp={formatTimestamp}
              apiBaseUrl={API_BASE_URL}
              liveState={chartLiveState}
              stale={isStale}
              alerts={alertHistory}
            />
          </div>

          {Boolean(0) && <section
            className="health-section health-section--legacy"
            id="system-health"
            hidden
          >
            <div className="section-heading section-heading--controls">
              <div>
                <p className="eyebrow">Explainable current condition</p>
                <h2>System health and current operating condition</h2>
              </div>
              <div className="health-controls">
                <div className="deviation-filter">
                  <label htmlFor="health-workload">Workload</label>
                  <select
                    id="health-workload"
                    value={healthWorkload}
                    onChange={(event) => setHealthWorkload(event.target.value)}
                  >
                    <option value="all">All workloads</option>
                    {[
                      "idle",
                      "interactive_light",
                      "office_productivity",
                      "browser_or_media",
                      "development",
                      "gaming_or_3d",
                      "compute_intensive",
                      "background_activity",
                      "unknown",
                    ].map((item) => (
                      <option value={item} key={item}>
                        {item.replaceAll("_", " ")}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="range-control" aria-label="Health score time range">
                  {(Object.keys(rangeLabels) as HistoryRange[]).map((option) => (
                    <button
                      type="button"
                      className={range === option ? "active" : ""}
                      onClick={() => changeRange(option)}
                      key={option}
                    >
                      {rangeLabels[option]}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            <p className="health-interpretation">
              {healthStatus?.interpretation
                ?? "System Health Score summarizes the computer’s current observed operating condition using available telemetry, stability and operational evidence. It is not a failure probability, future-reliability guarantee or PC quality rating."}
            </p>

            <div className="index-separation" aria-label="SmartOps output definitions">
              <article>
                <strong>System Health Score</strong>
                <span>Current observed operating condition.</span>
              </article>
              <article>
                <strong>Deviation Index</strong>
                <span>Difference from the device&apos;s learned operating pattern.</span>
              </article>
              <article>
                <strong>Risk Evidence Index</strong>
                <span>Strength of evidence associated with potential problems.</span>
              </article>
              <article>
                <strong>PC Quality Check</strong>
                <span>Hardware and software suitability, evaluated separately from current system health.</span>
              </article>
            </div>

            {!latestHealth || latestHealth.evaluation_state === "not_evaluated" ? (
              <div className="health-not-evaluated">
                <article>
                  <span>Current assessment</span>
                  <strong>Not evaluated</strong>
                  <p>No score is shown because the latest eligible assessment does not contain sufficient genuine core data.</p>
                </article>
                <article>
                  <span>Reason</span>
                  <strong>
                    {(latestHealth?.reason_codes
                      ?? health?.reason_codes
                      ?? healthStatus?.reason_codes
                      ?? ["no_eligible_completed_window"])
                      .map(formatUserReason)
                      .join(", ")}
                  </strong>
                  <p>Incomplete analysis periods and inadequate data are retained as explicit gaps.</p>
                </article>
                {latestHealth && (
                  <article>
                    <span>Data confidence</span>
                    <strong>{latestHealth.data_confidence.toFixed(1)}%</strong>
                    <p>Coverage {(latestHealth.coverage_ratio * 100).toFixed(0)}%</p>
                  </article>
                )}
              </div>
            ) : (
              <>
                <div className="health-summary">
                  <article className={`health-score health-score--${latestHealth.health_band}`}>
                    <span>System Health Score</span>
                    <strong>{latestHealth.system_health_score?.toFixed(0)}</strong>
                    <p>{latestHealth.health_band.replaceAll("_", " ")}</p>
                  </article>
                  <article className="context-card">
                    <span>Evaluation state</span>
                    <strong className={`health-state health-state--${latestHealth.evaluation_state}`}>
                      {latestHealth.evaluation_state}
                    </strong>
                    <p>
                      {latestHealth.evaluation_state === "provisional"
                        ? "Valid current-condition result with less historical evidence."
                        : "Current and required historical evidence are available."}
                    </p>
                  </article>
                  <article className="context-card">
                    <span>Data confidence</span>
                    <strong>{latestHealth.data_confidence.toFixed(1)}%</strong>
                    <p>
                      Coverage {(latestHealth.coverage_ratio * 100).toFixed(0)}%;
                      workload confidence{" "}
                      {latestHealth.workload_confidence === null
                        ? "unavailable"
                        : `${(latestHealth.workload_confidence * 100).toFixed(0)}%`}
                    </p>
                  </article>
                  <article className="context-card">
                    <span>Workload and trend</span>
                    <strong>
                      {latestHealth.workload_context?.replaceAll("_", " ") ?? "Unavailable"}
                    </strong>
                    <p>
                      {latestHealth.trend_direction.replaceAll("_", " ")};{" "}
                      {latestHealth.consecutive_window_count} consecutive pressure window(s)
                    </p>
                  </article>
                </div>

                {latestHealth.evaluation_state === "provisional" && (
                  <div className="health-provisional" role="status">
                    <strong>Provisional current-condition result.</strong>
                    <span>
                      The score is valid for this completed window, but the
                      device baseline, Deviation Index, or Risk Evidence Index
                      is not yet established.
                    </span>
                  </div>
                )}

                <div className="health-subsection">
                  <h3>Component score breakdown</h3>
                  <p>
                    Available component weight {latestHealth.available_component_weight.toFixed(0)}%;
                    excluded weight {latestHealth.excluded_component_weight.toFixed(0)}%.
                    Scores are normalized across valid applicable components.
                  </p>
                  <div className="health-component-grid">
                    {latestHealth.components.map((component) => (
                      <article key={component.component_name}>
                        <span>{component.component_name.replaceAll("_", " ")}</span>
                        <strong>{component.component_score.toFixed(1)}</strong>
                        <p>
                          Effective weight {(component.effective_weight * 100).toFixed(1)}%
                          {" "}· deduction {component.effective_deduction_total.toFixed(1)}
                        </p>
                      </article>
                    ))}
                  </div>
                  <p className="health-reconstruction">
                    Stored score {latestHealth.system_health_score?.toFixed(4)} ·
                    reconstructed {latestHealth.score_reconstruction?.toFixed(4)}
                  </p>
                </div>

                <div className="health-subsection">
                  <h3>Health deductions and explanations</h3>
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Component</th><th>Signal group</th>
                          <th>Raw deduction</th><th>Effective deduction</th>
                          <th>Explanation</th><th>Cap / correlation rule</th>
                        </tr>
                      </thead>
                      <tbody>
                        {latestHealth.deductions
                          .filter((item) => item.raw_deduction !== 0 || item.effective_deduction !== 0)
                          .map((item) => (
                            <tr key={item.id}>
                              <td>{item.component_name.replaceAll("_", " ")}</td>
                              <td>{item.contribution_group.replaceAll("_", " ")}</td>
                              <td>{item.raw_deduction.toFixed(1)}</td>
                              <td>{item.effective_deduction.toFixed(1)}</td>
                              <td className="wrap-cell">{item.explanation}</td>
                              <td className="wrap-cell">
                                {item.correlation_or_cap_reason.replaceAll("_", " ")}
                              </td>
                            </tr>
                          ))}
                        {latestHealth.deductions.every(
                          (item) => item.raw_deduction === 0 && item.effective_deduction === 0,
                        ) && (
                          <tr><td colSpan={6}>No health deduction was applied.</td></tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>

                <div className="health-detail-grid">
                  <article>
                    <h3>Historical and serious-event evidence</h3>
                    <dl>
                      <div><dt>Personal baseline</dt><dd>{latestHealth.baseline_reference ? "Available" : "Not established"}</dd></div>
                      <div><dt>Deviation Index</dt><dd>{latestHealth.deviation_reference ? "Available" : "Not evaluated"}</dd></div>
                      <div><dt>Risk Evidence Index</dt><dd>{latestHealth.risk_reference ? "Available" : "Not evaluated"}</dd></div>
                      <div>
                        <dt>Serious-event evidence</dt>
                        <dd>
                          {latestHealth.deductions.some(
                            (item) => item.component_name === "operational_events"
                              && item.effective_deduction > 0,
                          ) ? "Contributed with configured caps" : "No effective deduction"}
                        </dd>
                      </div>
                    </dl>
                  </article>
                  <article>
                    <h3>Excluded or unavailable inputs</h3>
                    {latestHealth.excluded_inputs.length ? (
                      <ul>
                        {latestHealth.excluded_inputs.map((input) => (
                          <li key={`${input.input_category}-${input.input_name}`}>
                            <strong>{input.input_name.replaceAll("_", " ")}</strong>:{" "}
                            {input.availability_status.replaceAll("_", " ")}
                            {input.excluded_reason
                              ? ` — ${input.excluded_reason.replaceAll("_", " ")}`
                              : ""}
                          </li>
                        ))}
                      </ul>
                    ) : <p>All applicable inputs were available.</p>}
                    <p className="health-comparability">
                      Scores with different evidence availability may not be perfectly comparable.
                    </p>
                  </article>
                </div>

                <div className="health-detail-grid">
                  <article>
                    <h3>Improvement and recovery evidence</h3>
                    {latestHealth.guidance.improvements.length ? (
                      <ul>
                        {latestHealth.guidance.improvements.map((item) => (
                          <li key={`${item.sequence}-${item.guidance_text}`}>{item.guidance_text}</li>
                        ))}
                      </ul>
                    ) : <p>No explicit recovery trend was detected in this assessment.</p>}
                  </article>
                  <article>
                    <h3>Diagnostic verification steps</h3>
                    {latestHealth.guidance.recommendations.length ? (
                      <ol>
                        {latestHealth.guidance.recommendations.map((item) => (
                          <li key={`${item.sequence}-${item.guidance_text}`}>{item.guidance_text}</li>
                        ))}
                      </ol>
                    ) : <p>No additional diagnostic check is suggested for this window.</p>}
                  </article>
                </div>

                <details className="health-limitations">
                  <summary>Assessment limitations</summary>
                  <ul>
                    {latestHealth.guidance.limitations.map((item) => (
                      <li key={`${item.sequence}-${item.guidance_text}`}>{item.guidance_text}</li>
                    ))}
                  </ul>
                </details>
              </>
            )}

            <div className="health-history">
              <div>
                <h3>Historical System Health Score</h3>
                <span>{healthHistory.length} analysis periods in range, including not-evaluated gaps</span>
              </div>
              <HealthChart data={healthHistory} />
            </div>
            <p className="health-version">
              Algorithm {latestHealth?.algorithm_version ?? healthStatus?.algorithm_version ?? "Unavailable"}
              {" "}· configuration {latestHealth?.configuration_version ?? healthStatus?.configuration_version ?? "Unavailable"}
            </p>
          </section>}

          <section className="history-section" hidden={activeRoute !== "live-monitoring"}>
            <div className="section-heading section-heading--controls">
              <div>
                <p className="eyebrow">Historical monitoring</p>
                <h2>Selected range</h2>
              </div>
              <div className="range-control" aria-label="History range">
                {(Object.keys(rangeLabels) as HistoryRange[]).map((option) => (
                  <button
                    type="button"
                    className={range === option ? "active" : ""}
                    onClick={() => changeRange(option)}
                    key={option}
                  >
                    {rangeLabels[option]}
                  </button>
                ))}
              </div>
            </div>

            <div className="charts-grid">
              <InteractiveLineChart
                title="CPU utilization"
                data={chartHistory}
                series={[
                  { label: "CPU", color: "#2f6fad", value: (metric) => metric.cpu_percent },
                ]}
                fixedMaximum={100}
                axisFormatter={(value) => `${value.toFixed(0)}%`}
                expandable
                liveState={chartLiveState}
              />
              <InteractiveLineChart
                title="RAM utilization"
                data={chartHistory}
                series={[
                  { label: "RAM", color: "#6f42a6", value: (metric) => metric.ram_percent },
                ]}
                fixedMaximum={100}
                axisFormatter={(value) => `${value.toFixed(0)}%`}
                expandable
                liveState={chartLiveState}
              />
              <InteractiveLineChart
                title="Disk usage"
                data={chartHistory}
                series={[
                  { label: "Used", color: "#2e7d32", value: (metric) => metric.disk_percent },
                ]}
                fixedMaximum={100}
                axisFormatter={(value) => `${value.toFixed(0)}%`}
                expandable
                liveState={chartLiveState}
              />
              <InteractiveLineChart
                title="Disk transfer rate"
                data={chartHistory}
                series={[
                  {
                    label: "Read",
                    color: "#2f6fad",
                    value: (metric) => metric.disk_read_bytes_per_second,
                  },
                  {
                    label: "Write",
                    color: "#c46b18",
                    value: (metric) => metric.disk_write_bytes_per_second,
                  },
                ]}
                axisFormatter={formatCompactRate}
                expandable
                liveState={chartLiveState}
              />
              <InteractiveLineChart
                title="Network transfer rate"
                data={chartHistory}
                series={[
                  {
                    label: "Download",
                    color: "#2e7d32",
                    value: (metric) => metric.network_download_bytes_per_second,
                  },
                  {
                    label: "Upload",
                    color: "#6f42a6",
                    value: (metric) => metric.network_upload_bytes_per_second,
                  },
                ]}
                axisFormatter={formatCompactRate}
                expandable
                liveState={chartLiveState}
              />
            </div>
            {matchingTotal > 5000 && (
              <p className="range-note">
                Charts show the newest 5,000 records in this range; SQLite retains
                all {matchingTotal.toLocaleString()} matching samples.
              </p>
            )}
          </section>

          <section
            className="alerts-section"
            id="predictive-alerts"
            hidden={activeRoute !== "predictive-alerts"}
          >
            <div className="section-heading section-heading--controls">
              <div>
                <p className="eyebrow">Automatic alert monitoring</p>
                <h2>Predictive alerts and operational evidence</h2>
              </div>
              <span>Evaluated completed five-minute windows only</span>
            </div>

            <p className="interpretation-statement">
              {alertStatus?.interpretation
                ?? "SmartOps alerts indicate observed operational evidence that may require attention. They are not guaranteed predictions of failure, confirmed hardware diagnoses, or calibrated probabilities of a future crash."}
            </p>
            {deepLinkMessage && (
              <div
                className={`deep-link-message ${
                  selectedAlertId === null ? "deep-link-message--warning" : ""
                }`}
                role="status"
              >
                {deepLinkMessage}
              </div>
            )}
            <div className="upstream-readiness" aria-label="Prediction readiness">
              <span>
                Baseline:{" "}
                <strong>{baseline?.state.replaceAll("_", " ") ?? "Unavailable"}</strong>
              </span>
              <span>
                Risk evidence:{" "}
                <strong>{riskStatus?.status.replaceAll("_", " ") ?? "Not evaluated"}</strong>
              </span>
              <span>
                System health:{" "}
                <strong>{healthStatus?.status.replaceAll("_", " ") ?? "Not evaluated"}</strong>
              </span>
              {alertStatus?.status === "not_evaluated" && (
                <p>
                  Prediction is waiting for sufficient eligible baseline history
                  and evaluated upstream evidence. No alert is fabricated.
                </p>
              )}
            </div>

            <div className="alert-summary" aria-label="Alert summary">
              <article className="alert-summary__primary">
                <span>Open alerts</span>
                <strong>{alertStatus?.open_alert_count ?? 0}</strong>
                <p>Open, acknowledged, or recovering conditions</p>
              </article>
              {([
                ["urgent", "Critical"],
                ["warning", "High"],
                ["advisory", "Medium"],
                ["informational", "Low"],
              ] as const).map(([severity, label]) => (
                <article className={`alert-summary__severity alert-summary__severity--${severity}`} key={severity}>
                  <span>{label}</span>
                  <strong>{alertStatus?.active_severity_counts[severity] ?? 0}</strong>
                  <p>{severity === "informational" ? "Informational evidence" : "Current evaluated severity"}</p>
                </article>
              ))}
              <article>
                <span>Latest alert time</span>
                <strong>{alertHistory[0] ? formatTimestamp(alertHistory[0].latest_observed_utc) : "None recorded"}</strong>
                <p>Evaluation freshness: {alertStatus?.latest_run?.finished_at_utc ? formatTimestamp(alertStatus.latest_run.finished_at_utc) : alertStatus?.status.replaceAll("_", " ") ?? "Unavailable"}</p>
              </article>
              <article>
                <span>Windows notifications</span>
                <strong>{notificationStatus?.enabled ? "Enabled" : "Disabled"}</strong>
                <p>{notificationStatus?.supported ? `${notificationStatus.delivery_counts.delivered ?? 0} accepted by Windows` : "Native provider unavailable"}</p>
              </article>
            </div>

            <div className="alert-filters">
              <label className="alert-filter-search">
                <span>Search stored alerts</span>
                <input
                  type="search"
                  value={alertSearch}
                  onChange={(event) => setAlertSearch(event.target.value)}
                  placeholder="Title, category, basis, or workload"
                />
              </label>
              <label>
                <span>Severity</span>
                <select
                  value={alertSeverity}
                  onChange={(event) => setAlertSeverity(event.target.value)}
                >
                  <option value="all">All severities</option>
                  <option value="informational">Informational</option>
                  <option value="advisory">Advisory</option>
                  <option value="warning">Warning</option>
                  <option value="urgent">Urgent</option>
                </select>
              </label>
              <label>
                <span>State</span>
                <select
                  value={alertState}
                  onChange={(event) => setAlertState(event.target.value)}
                >
                  <option value="all">All states</option>
                  <option value="open">Open</option>
                  <option value="acknowledged">Acknowledged</option>
                  <option value="recovering">Recovering</option>
                  <option value="resolved">Resolved</option>
                </select>
              </label>
              <label>
                <span>Category</span>
                <select
                  value={alertCategory}
                  onChange={(event) => setAlertCategory(event.target.value)}
                >
                  <option value="all">All categories</option>
                  <option value="resource_pressure">Resource pressure</option>
                  <option value="memory_and_swap_pressure">Memory and swap</option>
                  <option value="disk_capacity_pressure">Disk capacity</option>
                  <option value="disk_io_pressure">Disk I/O</option>
                  <option value="thermal_evidence">Thermal evidence</option>
                  <option value="repeated_serious_event">Serious events</option>
                  <option value="system_stability">System stability</option>
                  <option value="increasing_risk_evidence">Risk evidence</option>
                  <option value="degraded_system_health">System health</option>
                  <option value="data_quality_limitation">Data quality</option>
                </select>
              </label>
              <label>
                <span>Workload</span>
                <select
                  value={alertWorkload}
                  onChange={(event) => setAlertWorkload(event.target.value)}
                >
                  <option value="all">All workloads</option>
                  <option value="idle">Idle</option>
                  <option value="light_desktop">Light desktop</option>
                  <option value="development">Development</option>
                  <option value="gaming_or_3d">Gaming or 3D</option>
                  <option value="compute_intensive">Compute intensive</option>
                  <option value="mixed">Mixed</option>
                </select>
              </label>
              <label>
                <span>Time range</span>
                <select value={range} onChange={(event) => { setRange(event.target.value as HistoryRange); setPage(0); }}>
                  <option value="1h">Last hour</option>
                  <option value="6h">Last 6 hours</option>
                  <option value="24h">Last 24 hours</option>
                  <option value="all">Available history</option>
                </select>
              </label>
              <label>
                <span>Validation</span>
                <select value={alertValidation} onChange={(event) => setAlertValidation(event.target.value)}>
                  <option value="all">All validation states</option>
                  <option value="validated">Registered validation available</option>
                  <option value="not_validated">Not yet validated</option>
                </select>
              </label>
              <label>
                <span>Sort</span>
                <select value={alertSort} onChange={(event) => setAlertSort(event.target.value as typeof alertSort)}>
                  <option value="newest">Newest activity</option>
                  <option value="severity">Highest severity</option>
                  <option value="confidence">Strongest available confidence</option>
                </select>
              </label>
              <div className="alert-filter-actions">
                <button type="button" onClick={() => {
                  setAlertSearch("");
                  setAlertSeverity("all");
                  setAlertState("all");
                  setAlertCategory("all");
                  setAlertWorkload("all");
                  setAlertValidation("all");
                  setAlertSort("newest");
                  setRange("1h");
                  setPage(0);
                }}>Reset filters</button>
              </div>
            </div>

            {alertStatus?.status === "not_evaluated" ? (
              <div className="alert-empty">
                <strong>Alert evidence has not been evaluated yet.</strong>
                <p>
                  SmartOps will evaluate eligible completed windows locally.
                  No alert is fabricated from incomplete or not-evaluated data.
                </p>
              </div>
            ) : activeAlerts.length === 0 ? (
              <div className="alert-empty" role="status">
                <strong>
                  No active alert is currently supported by evaluated evidence.
                </strong>
                <p>
                  Zero active alerts is a valid result. Unavailable inputs are
                  disclosed and are never replaced with healthy or unhealthy zeroes.
                  The absence of an alert is not a guarantee of system safety or future reliability.
                </p>
              </div>
            ) : (
              <div className="alert-list">
                {activeAlerts.map((alert) => (
                  <article
                    className={`alert-card alert-card--${alert.current_severity} ${
                      alert.id === selectedAlertId ? "alert-card--selected" : ""
                    }`}
                    key={alert.id}
                    id={`alert-${alert.id}`}
                    tabIndex={-1}
                  >
                    <header>
                      <div>
                        <span>{alert.alert_code} · {alert.category.replaceAll("_", " ")}</span>
                        <h3>{alert.title}</h3>
                      </div>
                      <div className="alert-labels">
                        <span className={`alert-severity alert-severity--${alert.current_severity}`}>
                          {alertSeverityLabel(alert.current_severity)}
                        </span>
                        <span className={`alert-state alert-state--${alert.state}`}>
                          {alert.state}
                        </span>
                      </div>
                    </header>
                    <p>{alert.description}</p>
                    <p className="alert-short-basis"><strong>Alert basis:</strong> {alert.short_alert_basis ?? "Legacy alert evidence is available in the expanded record."}</p>
                    <p className="alert-science-note">
                      Alert confidence describes evidence strength, completeness, and consistency; it is not failure probability. No score, deviation, risk value, or confidence value is presented as accuracy.
                    </p>
                    <div className="alert-facts">
                      <span><strong>Alert confidence:</strong> {alert.alert_confidence == null ? "Not available" : `${alert.alert_confidence.toFixed(1)}% (${alert.confidence_label})`}</span>
                      <span><strong>Method validation:</strong> {alert.validation?.display_label ?? "Not yet validated"}</span>
                      <span><strong>Evidence:</strong> {alert.evaluation_state.replaceAll("_", " ")}</span>
                      <span><strong>Data confidence:</strong> {alert.data_confidence.toFixed(1)}%</span>
                      <span><strong>Generated:</strong> {formatTimestamp(alert.first_observed_utc)}</span>
                      <span><strong>Latest observed:</strong> {formatTimestamp(alert.latest_observed_utc)}</span>
                      <span><strong>Evidence period:</strong> {alert.explanation_snapshot ? `${formatTimestamp(alert.explanation_snapshot.evidence_start_utc)} to ${formatTimestamp(alert.explanation_snapshot.evidence_end_utc)}` : "Not available for this legacy alert"}</span>
                      <span><strong>Notification:</strong> {alert.summary_record ? "Open details to view history" : alert.notification_deliveries?.at(-1)?.delivery_status.replaceAll("_", " ") ?? "No delivery recorded"}</span>
                      <span><strong>Duration:</strong> {formatDuration(alert.duration_seconds)}</span>
                      <span><strong>Occurrences:</strong> {alert.occurrence_count}</span>
                      <span><strong>Persistence:</strong> {alert.consecutive_window_count} windows</span>
                      <span><strong>Trend:</strong> {alert.trend_direction.replaceAll("_", " ")}</span>
                      <span><strong>Recovery:</strong> {alert.recovery_state.replaceAll("_", " ")}</span>
                      <span>
                        <strong>Workload:</strong>{" "}
                        {alert.workload_context?.replaceAll("_", " ") ?? "Unavailable"}
                        {alert.workload_confidence === null
                          ? ""
                          : ` (${(alert.workload_confidence * 100).toFixed(0)}%)`}
                      </span>
                    </div>
                    <button
                      type="button"
                      className="alert-review-button"
                      aria-expanded={alert.id === selectedAlertId}
                      aria-controls={`alert-details-${alert.id}`}
                      onClick={() => toggleAlertDetails(alert.id)}
                    >
                      <span>{alert.id === selectedAlertId ? "Hide details" : "View details"}</span>
                      {alert.id === selectedAlertId
                        ? <ChevronUp size={18} aria-hidden="true" />
                        : <ChevronDown size={18} aria-hidden="true" />}
                    </button>
                    {alert.id === selectedAlertId ? (
                    alertDetailLoading ? (
                      <div id={`alert-details-${alert.id}`} className="alert-detail-loading" role="status" aria-live="polite">
                        <span className="loading-spinner" aria-hidden="true" />
                        Loading full alert details from the local database…
                      </div>
                    ) : alertDetailError ? (
                      <div id={`alert-details-${alert.id}`} className="alert-detail-error" role="alert">
                        <strong>Alert details are temporarily unavailable.</strong>
                        <p>{alertDetailError}</p>
                        <button type="button" onClick={retryAlertDetails}>Retry</button>
                      </div>
                    ) : (
                    <div id={`alert-details-${alert.id}`} className="alert-detail-region" role="region" aria-label={`Full details for ${alert.title}`}>
                    <>
                    <details className="alert-explanation-details">
                      <summary>Why was this alert generated?</summary>
                      {alert.explanation_snapshot == null ? (
                        <p>Legacy alert — detailed explanation was not recorded when this alert was generated.</p>
                      ) : (
                        <div className="alert-explanation-content">
                          <p>{alert.explanation_snapshot.plain_language_explanation}</p>
                          <dl className="alert-facts">
                            <div><dt>Evidence window</dt><dd>{formatTimestamp(alert.explanation_snapshot.evidence_start_utc)} to {formatTimestamp(alert.explanation_snapshot.evidence_end_utc)}</dd></div>
                            <div><dt>Completeness</dt><dd>{(alert.explanation_snapshot.evidence_completeness * 100).toFixed(1)}% from {alert.explanation_snapshot.source_sample_count} samples</dd></div>
                          </dl>
                          <h4>Alert confidence components</h4>
                          <p>Evidence confidence describes evidence quality and consistency. It is not the probability that a failure will occur.</p>
                          {alert.explanation_snapshot.alert_confidence === null ? <p>Confidence unavailable</p> : (
                            <div className="table-wrap"><table><thead><tr><th>Component</th><th>Value</th><th>Weight</th><th>Contribution</th><th>Explanation</th></tr></thead><tbody>
                              {alert.explanation_snapshot.confidence_components.map((component) => <tr key={component.component_key}><td>{component.component_key.replaceAll("_", " ")}</td><td>{component.component_value === null ? "Unavailable" : `${component.component_value.toFixed(1)}%`}</td><td>{(component.configured_weight * 100).toFixed(1)}%</td><td>{component.weighted_contribution === null ? "Excluded" : component.weighted_contribution.toFixed(2)}</td><td>{component.explanation}</td></tr>)}
                            </tbody></table></div>
                          )}
                          <h4>Method-level validation</h4>
                          {alert.explanation_snapshot.validation.validation_type === "not_yet_validated" ? (
                            <div className="alert-validation-empty">
                              <strong>Not yet validated</strong>
                              <p>Individual alert correctness requires confirmed outcomes. No score, deviation, risk value, or confidence value is presented as accuracy.</p>
                            </div>
                          ) : (
                            <dl className="alert-validation-grid">
                              <div><dt>Precision</dt><dd>{validationPercent(alert.explanation_snapshot.validation.precision)}</dd></div>
                              <div><dt>Recall</dt><dd>{validationPercent(alert.explanation_snapshot.validation.recall)}</dd></div>
                              <div><dt>F1 score</dt><dd>{validationPercent(alert.explanation_snapshot.validation.f1_score)}</dd></div>
                              <div><dt>False-positive rate</dt><dd>{validationPercent(alert.explanation_snapshot.validation.false_positive_rate)}</dd></div>
                              <div><dt>Accuracy</dt><dd>{validationPercent(alert.explanation_snapshot.validation.accuracy)}</dd></div>
                              <div><dt>Labelled sample/event count</dt><dd>{alert.explanation_snapshot.validation.sample_size.toLocaleString()}</dd></div>
                              <div><dt>Validation date</dt><dd>{alert.explanation_snapshot.validation.validation_date_utc ? formatTimestamp(alert.explanation_snapshot.validation.validation_date_utc) : "Not available"}</dd></div>
                              <div><dt>Dataset / experiment</dt><dd>{alert.explanation_snapshot.validation.dataset_description ?? "Not available"}</dd></div>
                            </dl>
                          )}
                          {alert.explanation_snapshot.validation.limitations.length > 0 && (
                            <p className="alert-validation-limitations">{alert.explanation_snapshot.validation.limitations.join(" ")}</p>
                          )}
                          <details className="alert-technical-details">
                            <summary>Technical Details</summary>
                            <dl className="alert-facts">
                              <div><dt>Rule/model version</dt><dd>{alert.explanation_snapshot.triggering_rule_identifier} · {alert.explanation_snapshot.triggering_rule_version}</dd></div>
                              <div><dt>Baseline version</dt><dd>{alert.explanation_snapshot.baseline_version == null ? "Not available" : `v${alert.explanation_snapshot.baseline_version}`}</dd></div>
                              <div><dt>Explanation version</dt><dd>{alert.explanation_snapshot.explanation_version}</dd></div>
                            </dl>
                            <div className="alert-explanation-columns">
                              <div><h4>Observed values</h4><pre>{JSON.stringify(alert.explanation_snapshot.observed_values, null, 2)}</pre></div>
                              <div><h4>Baseline / expected values</h4><pre>{JSON.stringify(alert.explanation_snapshot.baseline_values, null, 2)}</pre></div>
                              <div><h4>Thresholds</h4><pre>{JSON.stringify(alert.explanation_snapshot.thresholds, null, 2)}</pre></div>
                              <div><h4>Recorded differences / deviations</h4><pre>{JSON.stringify(alert.explanation_snapshot.deviations, null, 2)}</pre></div>
                            </div>
                          </details>
                        </div>
                      )}
                    </details>
                    <details className="alert-lifecycle-details">
                      <summary>Lifecycle and notification history</summary>
                      <AlertLifecycleTimeline
                        firstObservedUtc={alert.first_observed_utc}
                        transitions={alert.transitions}
                        occurrences={alert.occurrences}
                        deliveries={alert.notification_deliveries}
                        outcome={alert.outcome}
                        formatTimestamp={formatTimestamp}
                      />
                    </details>
                    <div className="alert-detail-grid">
                      <div>
                        <h4>Probable contributing factors</h4>
                        <ol>
                          {alert.probable_factors.map((factor) => (
                            <li key={`${factor.rank}-${factor.domain}`}>
                              {factor.domain.replaceAll("_", " ")}
                              {factor.confidence === null
                                ? ""
                                : ` · ${(factor.confidence * 100).toFixed(0)}% evidence confidence`}
                            </li>
                          ))}
                        </ol>
                        <h4>Supporting alert evidence</h4>
                        <ul>
                          {alert.evidence.map((item) => (
                            <li key={item.id}>
                              <strong>{item.evidence_key.replaceAll("_", " ")}</strong>:{" "}
                              {item.explanation}
                              {item.suppressed
                                ? ` Excluded from an additional contribution (${item.suppression_reason?.replaceAll("_", " ")}).`
                                : ""}
                            </li>
                          ))}
                        </ul>
                      </div>
                      <div>
                        <h4>Diagnostic verification</h4>
                        <ol>
                          {alert.diagnostic_recommendations.map((item) => (
                            <li key={item}>{item}</li>
                          ))}
                        </ol>
                        <h4>Preventive guidance</h4>
                        <ul>
                          {alert.preventive_guidance.map((item) => (
                            <li key={item}>{item}</li>
                          ))}
                        </ul>
                      </div>
                    </div>
                    {(alert.contradictory_evidence.length > 0
                      || alert.excluded_inputs.length > 0) && (
                      <details>
                        <summary>Contradictory evidence and excluded inputs</summary>
                        <ul>
                          {alert.contradictory_evidence.map((item) => (
                            <li key={item}>{item}</li>
                          ))}
                          {alert.excluded_inputs.map((item) => (
                            <li key={`${item.input}-${item.status}`}>
                              {item.input.replaceAll("_", " ")}:{" "}
                              {item.status.replaceAll("_", " ")}
                              {item.reason ? ` · ${item.reason.replaceAll("_", " ")}` : ""}
                            </li>
                          ))}
                        </ul>
                      </details>
                    )}
                    {alert.id === selectedAlertId && (
                      <div className="notification-linked-evidence">
                        <h4>Related analytical evidence</h4>
                        <p>
                          Risk Evidence Index:{" "}
                          <strong>
                            {selectedAlertRisk
                              ? selectedAlertRisk.risk_evidence_index.toFixed(1)
                              : "Not evaluated for this window"}
                          </strong>
                          {selectedAlertRisk
                            ? ` · ${selectedAlertRisk.evidence_level.replaceAll("_", " ")} · ${selectedAlertRisk.temporal_pattern.replaceAll("_", " ")}`
                            : ""}
                        </p>
                        {selectedAlertCandidates.length > 0 && (
                          <ol>
                            {selectedAlertCandidates.map((candidate) => (
                              <li key={candidate.id}>
                                <strong>
                                  {candidate.candidate_domain.replaceAll("_", " ")}
                                </strong>
                                : {candidate.explanation}
                              </li>
                            ))}
                          </ol>
                        )}
                        <a
                          href={`#/root-cause-analysis?alertId=${alert.id}`}
                          onClick={() => setAnalysisTarget(`alert-${alert.id}`)}
                        >
                          Open the complete root-cause evidence workspace
                        </a>
                      </div>
                    )}
                    <div className="alert-outcome-control">
                      <span><strong>Outcome:</strong> {alert.outcome?.new_outcome.replaceAll("_", " ") ?? "Pending"}</span>
                      <div role="group" aria-label={`Outcome label for alert ${alert.id}`}>
                        {(["pending", "confirmed", "false_positive", "inconclusive"] as const).map((outcome) => (
                          <button type="button" key={outcome} disabled={outcomePending === alert.id} onClick={() => void labelAlertOutcome(alert.id, outcome)}>{outcome.replaceAll("_", " ")}</button>
                        ))}
                      </div>
                    </div>
                    </>
                    </div>
                    )
                    ) : null}
                    <footer>
                      <details className="alert-version-details">
                        <summary>Technical record versions</summary>
                        <span>
                          Algorithm {alert.algorithm_version} · configuration{" "}
                          {alert.configuration_version} · catalogue {alert.catalogue_version}
                        </span>
                      </details>
                      <a
                        className="alert-report-link"
                        href="#/research-validation"
                        onClick={() => setFeedbackAlertId(String(alert.id))}
                      >
                        Report what happened
                      </a>
                      {alert.acknowledged_at_utc === null && (
                        <button
                          type="button"
                          title="I have seen this alert. This does not resolve or verify it."
                          onClick={() => void acknowledge(alert.id)}
                          disabled={acknowledgingAlert === alert.id}
                        >
                          {acknowledgingAlert === alert.id
                            ? "Acknowledging…"
                            : "I have seen this alert"}
                        </button>
                      )}
                    </footer>
                  </article>
                ))}
              </div>
            )}
            {outcomeMessage && <p className="notification-action-message" role="status" aria-live="polite">{outcomeMessage}</p>}

            <div className="alert-history">
              <div className="section-heading">
                <div>
                  <p className="eyebrow">Preserved lifecycle history</p>
                  <h3>Resolved alerts</h3>
                </div>
                <span>{resolvedAlerts.length} shown in selected filters</span>
              </div>
              {resolvedAlerts.length === 0 ? (
                <p>No resolved alerts match the selected filters and time range.</p>
              ) : (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Latest observation</th><th>Alert</th><th>Category</th>
                        <th>Peak severity</th><th>Occurrences</th><th>Resolved</th><th>Details</th>
                      </tr>
                    </thead>
                    <tbody>
                      {resolvedAlerts.map((alert) => (
                        <Fragment key={alert.id}>
                          <tr className={alert.id === selectedAlertId ? "alert-history-row--selected" : ""}>
                            <td>{formatTimestamp(alert.latest_observed_utc)}</td>
                            <td>{alert.title}</td>
                            <td>{alert.category.replaceAll("_", " ")}</td>
                            <td>{alert.peak_severity}</td>
                            <td>{alert.occurrence_count}</td>
                            <td>
                              {alert.resolved_at_utc
                                ? formatTimestamp(alert.resolved_at_utc)
                                : "Not recorded"}
                            </td>
                            <td>
                              <button
                                type="button"
                                className="alert-table-detail-button"
                                aria-expanded={alert.id === selectedAlertId}
                                aria-controls={`alert-details-${alert.id}`}
                                onClick={() => toggleAlertDetails(alert.id)}
                              >
                                <span>{alert.id === selectedAlertId ? "Hide details" : "View details"}</span>
                                {alert.id === selectedAlertId
                                  ? <ChevronUp size={16} aria-hidden="true" />
                                  : <ChevronDown size={16} aria-hidden="true" />}
                              </button>
                            </td>
                          </tr>
                          {alert.id === selectedAlertId && (
                            <tr className="alert-history-detail-row">
                              <td colSpan={7}>{renderAlertDetails(alert)}</td>
                            </tr>
                          )}
                        </Fragment>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            <details className="alert-version">
              <summary>Technical evaluation versions</summary>
              <p>
                Algorithm {alertStatus?.algorithm_version ?? "Unavailable"} ·
                configuration {alertStatus?.configuration_version ?? "Unavailable"} ·
                catalogue {alertStatus?.catalogue_version ?? "Unavailable"}
              </p>
            </details>
          </section>

          <section
            className="validation-section"
            id="predictive-validation"
            hidden={activeRoute !== "research-validation"}
          >
            <div className="section-heading section-heading--controls">
              <div>
                <p className="eyebrow">Labelled outcome research</p>
                <h2>Incident feedback and predictive validation</h2>
              </div>
              <span>User-reported or externally verified outcomes only</span>
            </div>

            <p className="interpretation-statement">
              {validation?.interpretation
                ?? "SmartOps validation results are based on available user-reported or externally verified outcomes. They do not by themselves establish guaranteed failure prediction, hardware diagnosis, or universally validated accuracy."}
            </p>
            <div className="optional-research-notice" role="note">
              <strong>Optional academic validation area.</strong>
              <span>
                These records are not required for automatic monitoring,
                predictive alerts, root-cause analysis, System Health Score or
                PC Quality Check.
              </span>
            </div>

            <details className="research-methods">
              <summary>Research support, formulas, and versioned methods</summary>
              <p>
                Research supports resource-oriented workload characterisation,
                feature-level explanations, and labelled-outcome validation.
                SmartOps-specific profile thresholds, weights, confidence bands,
                and score mappings are transparent engineering adaptations that
                still require labelled real-world validation.
              </p>
              <h3>Primary research support</h3>
              <ul>
                {(fineQuality?.research_references ?? []).map((reference) => (
                  <li key={reference.url}>
                    <a href={reference.url} target="_blank" rel="noreferrer">
                      {reference.title}
                    </a>{" "}
                    — {reference.supports}
                  </li>
                ))}
              </ul>
              <h3>Definitions</h3>
              <p>
                Profile metric score = 100 below the recommended bound, declines
                linearly toward 0 at the configured limit, and is combined as
                Σ(valid metric score × normalized applicable weight). Missing
                optional evidence is excluded rather than replaced with zero.
              </p>
              <p>
                Alert Confidence = 25% evidence completeness + 20% active-baseline
                adequacy + 15% required-metric availability + 15% sampling
                continuity + 15% threshold margin + 5% indicator agreement + 5%
                evidence freshness. It measures evidence quality, not failure
                probability or accuracy.
              </p>
              <p>
                Accuracy = (TP + TN) / N; precision = TP / (TP + FP); recall =
                TP / (TP + FN); specificity = TN / (TN + FP); F1 = 2 × precision
                × recall / (precision + recall); false-positive rate = FP / (FP + TN).
                Undefined denominators remain unavailable.
              </p>
              <h3>Method-level validation registry</h3>
              {(validationRegistry ?? []).length === 0 ? (
                <p>
                  <strong>Not yet validated.</strong> No method-level labelled
                  validation record is stored.
                </p>
              ) : (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr><th>Rule / version</th><th>Scope</th><th>Validation</th><th>Sample</th><th>Accuracy</th><th>Date</th></tr>
                    </thead>
                    <tbody>
                      {(validationRegistry ?? []).map((item, index) => (
                        <tr key={`${item.rule_identifier ?? "method"}-${item.rule_version ?? index}`}>
                          <td>{item.rule_identifier ?? "Unavailable"} / {item.rule_version ?? "Unavailable"}</td>
                          <td>{item.applicable_workload_scope ?? "All applicable workloads"}</td>
                          <td>{item.display_label}</td>
                          <td>{item.sample_size}</td>
                          <td>{item.accuracy === null ? "Not yet validated" : `${(item.accuracy * 100).toFixed(1)}%`}</td>
                          <td>{item.validation_date_utc ? formatTimestamp(item.validation_date_utc) : "Not available"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <h3>PC Quality profile configuration</h3>
              <p>
                Each fine-grained profile below has independent versioned metric
                weights. These are v2-anchored operating-headroom scores and do not
                alter the calibrated baseline or predictive pipeline.
              </p>
              <div className="research-profile-list">
                {(fineQuality?.profiles ?? [])
                  .filter((profile) => profile.profile_kind === "fine_grained")
                  .map((profile) => (
                    <details key={profile.key}>
                      <summary>
                        {profile.name} — parent {profile.parent_workload_profile.replaceAll("_", " ")}
                      </summary>
                      <p>{profile.description}</p>
                      <div className="table-wrap">
                        <table>
                          <thead>
                            <tr><th>Metric</th><th>Weight</th><th>Direction</th><th>Recommended</th><th>Limit</th></tr>
                          </thead>
                          <tbody>
                            {Object.entries(profile.metrics ?? {}).map(([key, metric]) => (
                              <tr key={key}>
                                <td>{metric.label}</td>
                                <td>{metric.weight.toFixed(1)}%</td>
                                <td>{metric.direction.replaceAll("_", " ")}</td>
                                <td>{metric.recommended_high} {metric.unit}</td>
                                <td>{metric.limit_high} {metric.unit}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </details>
                  ))}
              </div>
            </details>

            <div className="validation-summary" aria-label="Validation summary">
              {(["precision", "recall", "accuracy", "balanced_accuracy"] as const).map(
                (name) => {
                  const metric = validationMetrics[name];
                  return (
                    <article key={name}>
                      <span>{name.replaceAll("_", " ")}</span>
                      <strong>
                        {metric?.metric_value === null || metric === undefined
                          ? "Not evaluated"
                          : `${(metric.metric_value * 100).toFixed(1)}%`}
                      </strong>
                      <p>
                        {metric?.evaluation_state === "evaluated"
                          ? `${metric.numerator ?? "—"} / ${metric.denominator}`
                          : "Insufficient labelled evidence"}
                      </p>
                    </article>
                  );
                },
              )}
              <article>
                <span>Mean warning lead time</span>
                <strong>
                  {validationMetrics.mean_warning_lead_time_seconds?.metric_value === null
                    || validationMetrics.mean_warning_lead_time_seconds === undefined
                    ? "Not evaluated"
                    : formatDuration(
                      validationMetrics.mean_warning_lead_time_seconds.metric_value,
                    )}
                </strong>
                <p>
                  Confirmed alert-to-incident matches only; negative means late detection
                </p>
              </article>
            </div>

            <div className="validation-readiness">
              <article>
                <span>Validation state</span>
                <strong>
                  {(validation?.status ?? "not_evaluated").replaceAll("_", " ")}
                </strong>
                <p>
                  Missing feedback is excluded. It is never counted as a no-issue outcome.
                </p>
              </article>
              <article>
                <span>Validation confidence</span>
                <strong>
                  {validation?.latest_run?.confidence_level ?? "insufficient"}
                </strong>
                <p>
                  {validation?.latest_run
                    ? `${(validation.latest_run.data_confidence * 100).toFixed(1)}% evidence confidence`
                    : "No evaluation run is available"}
                </p>
              </article>
              <article>
                <span>Labelled evidence</span>
                <strong>
                  {validation?.verified_incident_count ?? 0} incidents ·{" "}
                  {validation?.feedback_count ?? 0} alert outcomes
                </strong>
                <p>{validation?.unverified_alert_count ?? 0} alerts still unverified</p>
              </article>
              <article>
                <span>Eligible observations</span>
                <strong>
                  {validation?.latest_run?.eligible_window_count ?? 0} windows
                </strong>
                <p>
                  {validation?.completed_observation_period_count ?? 0} completed
                  reporting periods
                </p>
              </article>
              <article>
                <span>Matched / missed incidents</span>
                <strong>
                  {validationMetrics.detected_incident_count?.metric_value ?? 0} /{" "}
                  {validationMetrics.missed_incident_count?.metric_value ?? 0}
                </strong>
                <p>
                  Observation coverage:{" "}
                  {validationMetrics.observation_coverage?.metric_value === null
                    || validationMetrics.observation_coverage === undefined
                    ? "Not evaluated"
                    : `${(validationMetrics.observation_coverage.metric_value * 100).toFixed(1)}%`}
                </p>
              </article>
              <article>
                <span>Excluded validation evidence</span>
                <strong>{validation?.latest_run?.excluded_count ?? 0}</strong>
                <p>
                  {(validation?.latest_run?.evidence_decisions ?? [])
                    .filter((item) => !item.included)
                    .flatMap((item) => item.reason_codes)
                    .slice(0, 2)
                    .map((item) => item.replaceAll("_", " "))
                    .join("; ") || "No excluded records in the latest run"}
                </p>
              </article>
            </div>

            <details className="validation-form-disclosure">
              <summary>Optional incident, feedback, and observation forms</summary>
              <p>
                Open this area only when you choose to provide a labelled
                real-world observation for academic validation.
              </p>
            <div className="validation-forms">
              <article>
                <h3>Report an incident</h3>
                <p>
                  Record only operational symptoms. Do not enter passwords,
                  document contents, command lines, or private text.
                </p>
                <label>
                  <span>Category</span>
                  <select
                    value={incidentCategory}
                    onChange={(event) => setIncidentCategory(event.target.value)}
                  >
                    <option value="system_crash">System crash</option>
                    <option value="unexpected_restart">Unexpected restart</option>
                    <option value="application_failure">Application failure</option>
                    <option value="system_freeze">System freeze</option>
                    <option value="severe_slowdown">Severe slowdown</option>
                    <option value="memory_exhaustion">Memory exhaustion</option>
                    <option value="disk_capacity_issue">Disk capacity issue</option>
                    <option value="disk_io_issue">Disk I/O issue</option>
                    <option value="thermal_shutdown_or_throttling">Thermal shutdown or throttling</option>
                    <option value="driver_or_device_issue">Driver or device issue</option>
                    <option value="repeated_serious_event">Repeated serious event</option>
                    <option value="other_operational_issue">Other operational issue</option>
                  </select>
                </label>
                <label>
                  <span>Severity</span>
                  <select
                    value={incidentSeverity}
                    onChange={(event) => setIncidentSeverity(event.target.value)}
                  >
                    <option value="minor">Minor</option>
                    <option value="moderate">Moderate</option>
                    <option value="serious">Serious</option>
                    <option value="critical">Critical</option>
                  </select>
                </label>
                <label>
                  <span>Approximate start</span>
                  <input
                    type="datetime-local"
                    value={incidentStart}
                    onChange={(event) => setIncidentStart(event.target.value)}
                  />
                </label>
                <label>
                  <span>Operational symptoms</span>
                  <textarea
                    value={incidentSymptoms}
                    onChange={(event) => setIncidentSymptoms(event.target.value)}
                    maxLength={1500}
                    placeholder="What was observed?"
                  />
                </label>
                <button type="button" onClick={() => void reportIncident()}>
                  Review and save incident
                </button>
              </article>

              <article>
                <h3>Record alert outcome</h3>
                <p>
                  A confirmed issue still requires an explicit incident link before
                  it can count as a true positive.
                </p>
                <label>
                  <span>Mode</span>
                  <select
                    value={feedbackMode}
                    onChange={(event) => setFeedbackMode(
                      event.target.value as "create" | "revise",
                    )}
                  >
                    <option value="create">New feedback</option>
                    <option value="revise">Correct existing feedback</option>
                  </select>
                </label>
                <label>
                  <span>Alert</span>
                  <select
                    value={feedbackAlertId}
                    onChange={(event) => setFeedbackAlertId(event.target.value)}
                  >
                    <option value="">Select an unverified alert</option>
                    {unverifiedAlerts.map((alert) => (
                      <option value={alert.id} key={alert.id}>
                        #{alert.id} · {alert.title}
                      </option>
                    ))}
                    {feedbackMode === "revise" && alertHistory.map((alert) => (
                      <option value={alert.id} key={`revision-${alert.id}`}>
                        #{alert.id} · {alert.title}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>Observed outcome</span>
                  <select
                    value={feedbackOutcome}
                    onChange={(event) => setFeedbackOutcome(event.target.value)}
                  >
                    <option value="uncertain">Uncertain</option>
                    <option value="confirmed_related_issue">Confirmed related issue</option>
                    <option value="likely_related_issue">Likely related issue</option>
                    <option value="no_issue_observed">No issue observed</option>
                    <option value="preventive_action_taken">Preventive action taken</option>
                    <option value="not_yet_verified">Not yet verified</option>
                    <option value="incorrect_category">Incorrect category</option>
                    <option value="withdrawn">Withdrawn</option>
                  </select>
                </label>
                {feedbackOutcome === "no_issue_observed" && (
                  <label>
                    <span>Observation horizon (hours)</span>
                    <input
                      type="number"
                      min="0"
                      value={feedbackHorizonHours}
                      onChange={(event) => setFeedbackHorizonHours(event.target.value)}
                    />
                  </label>
                )}
                <label>
                  <span>Condition after observation</span>
                  <select
                    value={feedbackConditionState}
                    onChange={(event) => setFeedbackConditionState(event.target.value)}
                  >
                    <option value="unclear">Unclear</option>
                    <option value="continued">Continued</option>
                    <option value="recovered">Recovered</option>
                  </select>
                </label>
                <label>
                  <span>Structured action taken (optional)</span>
                  <input
                    value={feedbackAction}
                    onChange={(event) => setFeedbackAction(event.target.value)}
                    maxLength={500}
                  />
                </label>
                <label>
                  <span>Notes (optional)</span>
                  <textarea
                    value={feedbackNotes}
                    onChange={(event) => setFeedbackNotes(event.target.value)}
                    maxLength={1500}
                  />
                </label>
                <button type="button" onClick={() => void saveAlertFeedback()}>
                  Review and save feedback
                </button>
              </article>

              <article>
                <h3>Observation period</h3>
                <p>
                  True negatives are eligible only after a covered period is
                  explicitly closed with incident reporting declared complete.
                </p>
                {openObservationPeriod ? (
                  <>
                    <dl>
                      <div>
                        <dt>Started</dt>
                        <dd>{formatTimestamp(openObservationPeriod.start_utc)}</dd>
                      </div>
                      <div>
                        <dt>State</dt>
                        <dd>Open · no negative labels inferred</dd>
                      </div>
                    </dl>
                    <button
                      type="button"
                      onClick={() => void closeObservationPeriod(
                        openObservationPeriod.id,
                      )}
                    >
                      Review and close period
                    </button>
                  </>
                ) : (
                  <button type="button" onClick={() => void startObservationPeriod()}>
                    Start observation period
                  </button>
                )}
                <h3>Correct an incident</h3>
                <label>
                  <span>Incident ID</span>
                  <input
                    type="number"
                    min="1"
                    value={revisionIncidentId}
                    onChange={(event) => setRevisionIncidentId(event.target.value)}
                  />
                </label>
                <label>
                  <span>Corrected severity</span>
                  <select
                    value={revisionSeverity}
                    onChange={(event) => setRevisionSeverity(event.target.value)}
                  >
                    <option value="minor">Minor</option>
                    <option value="moderate">Moderate</option>
                    <option value="serious">Serious</option>
                    <option value="critical">Critical</option>
                  </select>
                </label>
                <label>
                  <span>Correction reason</span>
                  <input
                    value={revisionReason}
                    onChange={(event) => setRevisionReason(event.target.value)}
                    maxLength={500}
                  />
                </label>
                <button type="button" onClick={() => void reviseIncident()}>
                  Review and append correction
                </button>
                <h3>Link alert and incident</h3>
                <label>
                  <span>Incident ID</span>
                  <input
                    type="number"
                    min="1"
                    value={linkIncidentId}
                    onChange={(event) => setLinkIncidentId(event.target.value)}
                  />
                </label>
                <label>
                  <span>Alert ID</span>
                  <input
                    type="number"
                    min="1"
                    value={linkAlertId}
                    onChange={(event) => setLinkAlertId(event.target.value)}
                  />
                </label>
                <label>
                  <span>Match classification</span>
                  <select
                    value={linkMatchType}
                    onChange={(event) => setLinkMatchType(event.target.value)}
                  >
                    <option value="confirmed_match">Confirmed match</option>
                    <option value="probable_match">Probable match</option>
                    <option value="possible_match">Possible match</option>
                    <option value="rejected_match">Rejected match</option>
                    <option value="unmatched">Unmatched</option>
                  </select>
                </label>
                <label>
                  <span>Supporting or contradictory reason</span>
                  <input
                    value={linkReason}
                    onChange={(event) => setLinkReason(event.target.value)}
                    maxLength={500}
                  />
                </label>
                <button type="button" onClick={() => void linkAlertAndIncident()}>
                  Review and save match
                </button>
              </article>
            </div>
            </details>

            {validationActionMessage && (
              <p className="validation-action" role="status">
                {validationActionMessage}
              </p>
            )}

            <div className="section-heading section-heading--controls">
              <div>
                <p className="eyebrow">Version-separated results</p>
                <h3>Validation breakdown</h3>
              </div>
              <label className="quality-profile-control">
                <span>Break down by</span>
                <select
                  value={validationBreakdownScope}
                  onChange={(event) => setValidationBreakdownScope(
                    event.target.value as typeof validationBreakdownScope,
                  )}
                >
                  <option value="category">Category</option>
                  <option value="severity">Severity</option>
                  <option value="workload">Workload</option>
                  <option value="algorithm_configuration">Algorithm/configuration</option>
                </select>
              </label>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Group</th><th>Metric</th><th>Numerator</th>
                    <th>Denominator</th><th>Result</th><th>State</th>
                  </tr>
                </thead>
                <tbody>
                  {(validation?.latest_run?.metrics ?? [])
                    .filter((metric) => metric.scope_type === validationBreakdownScope)
                    .map((metric) => (
                      <tr key={metric.id}>
                        <td>{metric.scope_value.replaceAll("_", " ")}</td>
                        <td>{metric.metric_name}</td>
                        <td>{metric.numerator ?? "Unavailable"}</td>
                        <td>{metric.denominator}</td>
                        <td>
                          {metric.metric_value === null
                            ? "Insufficient labelled evidence"
                            : `${(metric.metric_value * 100).toFixed(1)}%`}
                        </td>
                        <td>{metric.evaluation_state.replaceAll("_", " ")}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>

            <div className="section-heading">
              <div>
                <p className="eyebrow">Alert lifecycle remains separate</p>
                <h3>Recent alert-feedback revisions</h3>
              </div>
              <span>{feedbackHistory.length} current feedback records shown</span>
            </div>
            {feedbackHistory.length === 0 ? (
              <div className="alert-empty">
                <strong>No alert feedback has been recorded.</strong>
                <p>Unverified alerts are not classified as false alerts.</p>
              </div>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Verified</th><th>Alert</th><th>Outcome</th>
                      <th>Condition</th><th>Revision</th><th>Alert lifecycle</th>
                    </tr>
                  </thead>
                  <tbody>
                    {feedbackHistory.map((feedback) => (
                      <tr key={feedback.id}>
                        <td>{formatTimestamp(feedback.verification_timestamp_utc)}</td>
                        <td>#{feedback.alert_id} · {feedback.alert_title}</td>
                        <td>{feedback.outcome.replaceAll("_", " ")}</td>
                        <td>{feedback.condition_state}</td>
                        <td>{feedback.current_revision_number}</td>
                        <td>{feedback.alert_lifecycle_state}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <div className="section-heading">
              <div>
                <p className="eyebrow">Append-only evidence history</p>
                <h3>Recent incident reports</h3>
              </div>
              <span>{incidents.length} recent records shown</span>
            </div>
            {incidents.length === 0 ? (
              <div className="alert-empty">
                <strong>No incident reports have been entered.</strong>
                <p>This is an empty labelled dataset, not evidence of no incidents.</p>
              </div>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Start</th><th>Category</th><th>Severity</th>
                      <th>Verification</th><th>State</th><th>Revision</th><th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {incidents.map((incident) => (
                      <tr key={incident.id}>
                        <td>{formatTimestamp(incident.start_utc)}</td>
                        <td>{incident.category.replaceAll("_", " ")}</td>
                        <td>{incident.severity}</td>
                        <td>{incident.verification_status.replaceAll("_", " ")}</td>
                        <td>{incident.status}</td>
                        <td>{incident.current_revision_number}</td>
                        <td>
                          {incident.status === "active" ? (
                            <button
                              type="button"
                              onClick={() => void withdrawIncident(incident.id)}
                            >
                              Withdraw
                            </button>
                          ) : "Preserved"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <p className="alert-version">
              Algorithm {validation?.algorithm_version ?? "Unavailable"} ·
              configuration {validation?.configuration_version ?? "Unavailable"} ·
              matching {validation?.matching_version ?? "Unavailable"}
            </p>
          </section>

          <section
            id="pc-quality-profile-selector"
            className="quality-section"
            hidden={activeRoute !== "pc-quality-check"}
          >
            <div className="section-heading section-heading--controls">
              <div>
                <p className="eyebrow">Recent observed operating condition</p>
                <h2>PC Quality Check</h2>
              </div>
              <label className="quality-profile-control">
                <span>PC Quality profile</span>
                <select
                  value={fineQualityProfile}
                  onChange={(event) => setFineQualityProfile(event.target.value)}
                  aria-label="PC Quality profile"
                >
                  <optgroup label="Fine-grained PC Quality Profiles">
                    {(fineQuality?.profiles ?? [])
                      .filter((profile) => profile.profile_kind === "fine_grained")
                      .map((profile) => (
                        <option value={profile.key} key={profile.key}>
                          {profile.name}
                        </option>
                      ))}
                  </optgroup>
                  <optgroup label="Broad Baseline Contexts">
                    {(fineQuality?.profiles ?? [])
                      .filter((profile) => profile.profile_kind === "broad_v2")
                      .map((profile) => (
                        <option value={profile.key} key={profile.key}>
                          {profile.name}
                        </option>
                      ))}
                  </optgroup>
                </select>
              </label>
            </div>

            <p className="interpretation-statement">
              {fineQuality?.interpretation ?? CURRENT_HEADROOM_MEANING}
            </p>

            <p className="quality-guide-explanation">
              {fineQuality?.guide_explanation
                ?? "SmartOps observes how the computer behaves during a recognized workload. Profiles without sufficient recognized evidence remain Not observed or Not evaluated instead of receiving an estimated score."}
            </p>

            <div className="profile-count-summary" role="status">
              {fineQuality?.profile_count ?? 0} catalogued profiles •{" "}
              {fineQualityEvaluatedCount} evaluated •{" "}
              {fineQualityNotObservedCount} not observed
            </div>

            {selectedFineQuality && (
              <article className={`selected-quality-profile fine-quality-card--${selectedFineQuality.evaluation_state}`}>
                <header>
                  <div>
                    <span>Current Workload Headroom</span>
                    <h3>{selectedFineQuality.name}</h3>
                  </div>
                  <strong>{formatHeadroomScore(selectedFineQuality.assessment?.profile_quality_score)}</strong>
                </header>
                <p>{formatQualityDescription(selectedFineQuality)}</p>
                <p className="quality-profile-state" role="status">
                  {selectedFineQuality.evaluation_state === "not_observed"
                    ? "Not observed — this workload has not been detected on this device."
                    : selectedFineQuality.assessment?.profile_quality_score == null
                      ? "Not evaluated — insufficient evidence is currently available."
                      : selectedFineQuality.assessment.profile_quality_score === 100
                        ? fineQuality?.full_score_explanation
                          ?? "All measured factors in this observed period remained within their expected ranges."
                        : "One or more measured factors crossed an expected range during this observed period."}
                </p>
                <dl>
                  <div><dt>Profile state</dt><dd>{selectedFineQuality.evaluation_state === "assessed" ? "Evaluated" : selectedFineQuality.evaluation_state.replaceAll("_", " ")}</dd></div>
                  <div><dt>Detectability</dt><dd>{detectabilityLabel(selectedFineQuality.detectability_state)}</dd></div>
                  <div><dt>Parent baseline context</dt><dd>{selectedFineQuality.parent_workload_profile.replaceAll("_", " ")}</dd></div>
                  <div><dt>Current Evidence Quality</dt><dd>{selectedFineQuality.assessment?.current_evidence_quality == null ? "Unavailable" : `${selectedFineQuality.assessment.current_evidence_quality.toFixed(1)}%`}</dd></div>
                  <div><dt>History Depth</dt><dd>{selectedFineQuality.history_depth.label}</dd></div>
                  <div><dt>Latest qualifying observation</dt><dd>{selectedFineQuality.history_depth.latest_qualifying_observation_utc ? formatTimestamp(selectedFineQuality.history_depth.latest_qualifying_observation_utc) : "Unavailable"}</dd></div>
                  <div><dt>Detection basis</dt><dd>{formatQualityDetectionBasis(selectedFineQuality.assessment?.detection_reason)}</dd></div>
                </dl>
                <p className="quality-evidence-quality-note">
                  Current Evidence Quality reflects this period&apos;s completeness,
                  workload-detection confidence, available measurements and
                  baseline availability. It is not long-term confidence or accuracy.
                </p>
                <p className="quality-detectability-note">{selectedFineQuality.detectability_explanation}</p>
                <details className="quality-evidence-details" open={Boolean(selectedFineQuality.assessment)}>
                  <summary>Evidence Details</summary>
                  <p>{fineQuality?.interpretation ?? CURRENT_HEADROOM_MEANING}</p>
                  {selectedFineQuality.assessment && (
                    <dl className="quality-evidence-summary">
                      <div><dt>Observation time</dt><dd>{formatTimestamp(selectedFineQuality.assessment.observed_at_utc)}</dd></div>
                      <div><dt>Workload profile</dt><dd>{selectedFineQuality.name}</dd></div>
                      <div><dt>Baseline source</dt><dd>{selectedFineQuality.assessment.baseline_source === "__device__" ? "Device" : selectedFineQuality.assessment.baseline_source.replaceAll("_", " ")}</dd></div>
                      <div><dt>Evidence independence</dt><dd>{evidenceIndependenceLabel(selectedFineQuality.assessment.evidence_independence_state)}</dd></div>
                    </dl>
                  )}
                  <div className="table-wrap">
                    <table>
                      <thead><tr><th>Metric</th><th>Effective weight</th><th>Observed</th><th>Expected/reference limit</th><th>Component score</th><th>Deduction</th></tr></thead>
                      <tbody>
                        {(selectedFineQuality.assessment?.metric_contributions ?? []).map((metric) => {
                          const deduction = weightedDeduction(metric.effective_weight, metric.metric_score);
                          return (
                            <tr key={metric.metric_key}>
                              <td>{metric.metric_label}</td>
                              <td>{formatEffectiveWeight(metric.effective_weight)}</td>
                              <td>{metric.observed_value == null ? "Unavailable" : metric.observed_value.toFixed(2)}</td>
                              <td>{metric.configured_threshold.recommended_high == null ? "Unavailable" : `${metric.configured_threshold.recommended_high.toFixed(2)} recommended; ${metric.configured_threshold.limit_high?.toFixed(2) ?? "no"} limit`}</td>
                              <td>{metric.metric_score == null ? "Excluded" : metric.metric_score.toFixed(2)}</td>
                              <td>{deduction == null ? "Excluded" : `${deduction.toFixed(2)} points`}</td>
                            </tr>
                          );
                        })}
                        {!selectedFineQuality.assessment && Object.entries(selectedFineQuality.metrics ?? {}).map(([key, metric]) => (
                          <tr key={key}>
                            <td>{metric.label}</td><td>{metric.weight.toFixed(1)}%</td><td>Not observed</td>
                            <td>Up to {metric.recommended_high} {metric.unit}</td><td>Not evaluated</td><td>Not evaluated</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {selectedFineQuality.assessment && (
                    <p className="quality-weight-total">
                      Displayed effective weight total: {selectedFineQuality.assessment.metric_contributions
                        .filter((metric) => metric.metric_score != null)
                        .reduce((total, metric) => total + metric.effective_weight * 100, 0)
                        .toFixed(1)}%
                    </p>
                  )}
                </details>
                {selectedFineQuality.assessment?.missing_evidence.length ? (
                  <p><strong>Missing optional or required evidence:</strong> {selectedFineQuality.assessment.missing_evidence.join(", ").replaceAll("_", " ")}</p>
                ) : null}
                <details>
                  <summary>Technical Details</summary>
                  {fineQuality?.interpretation && <p>{fineQuality.interpretation}</p>}
                  {selectedFineQuality.assessment?.explanation && (
                    <p><strong>Stored score explanation:</strong> {selectedFineQuality.assessment.explanation}</p>
                  )}
                  <dl>
                    <div><dt>Stable profile ID</dt><dd>{selectedFineQuality.key}</dd></div>
                    <div><dt>Catalogue state</dt><dd>Catalogued</dd></div>
                    <div><dt>Exact raw stored score</dt><dd>{selectedFineQuality.assessment?.profile_quality_score == null ? "Not evaluated" : selectedFineQuality.assessment.profile_quality_score.toFixed(4)}</dd></div>
                    <div><dt>Taxonomy version</dt><dd>{fineQuality?.taxonomy_version ?? "Unavailable"}</dd></div>
                    <div><dt>Semantic version</dt><dd>{fineQuality?.semantic_version ?? "Unavailable"}</dd></div>
                    <div><dt>Scoring method</dt><dd>{fineQuality?.scoring_method_version ?? "Unavailable"}</dd></div>
                    <div><dt>Detection rule</dt><dd>{selectedFineQuality.assessment?.detection_rule_version ?? fineQuality?.detection_rule_version ?? "Unavailable"}</dd></div>
                    <div><dt>Baseline version</dt><dd>{selectedFineQuality.assessment?.baseline_version_number ?? fineQuality?.active_baseline_version ?? "Unavailable"}</dd></div>
                    <div><dt>Baseline training observations</dt><dd>{selectedFineQuality.history_depth.baseline_training_observation_count}</dd></div>
                    <div><dt>Independent post-calibration observations</dt><dd>{selectedFineQuality.history_depth.independent_post_activation_observation_count}</dd></div>
                  </dl>
                  <p><strong>Known limitation:</strong> Current Evidence Quality describes one selected five-minute period. History Depth is factual context and is not an accuracy or reliability percentage.</p>
                </details>
              </article>
            )}

            <section className="fine-quality-section" aria-label="All catalogued PC Quality profiles">
              <div className="section-heading">
                <div>
                  <p className="eyebrow">Complete profile catalogue</p>
                  <h3>All catalogued profiles</h3>
                </div>
                <span>{fineQuality?.profile_count ?? 0} profiles</span>
              </div>
              <p className="settings-note">
                Catalogued, detectable, observed and evaluated are separate states.
                Current Workload Headroom does not alter risk,
                health, alerts, root-cause analysis or notifications.
              </p>
              {!fineQuality?.profiles ? (
                <div className="quality-not-evaluated">
                  <article><strong>Profile quality is not evaluated.</strong><p>Waiting for local profile evidence.</p></article>
                </div>
              ) : (
                <>
                  {fineQuality.profiles.filter((profile) => profile.key === "device").map((profile) => (
                    <article className="device-quality-summary" key={profile.key}>
                      <span>Device Current Workload Headroom</span>
                      <strong>{formatHeadroomScore(profile.assessment?.profile_quality_score)}</strong>
                      <p>{profile.status_explanation}</p>
                      <small>{profile.history_depth.label}; active personal baseline v{profile.assessment?.baseline_version_id ?? fineQuality.active_baseline_version ?? "unavailable"}</small>
                    </article>
                  ))}
                  <div className="fine-quality-grid">
                    {fineQuality.profiles.filter((profile) => profile.key !== "device").map((profile) => (
                      <article className={`fine-quality-card fine-quality-card--${profile.evaluation_state}`} key={profile.key}>
                        <header>
                          <span>{profile.profile_kind === "fine_grained" ? "Fine-grained profile" : "Broad baseline context"}</span>
                          <strong>{profile.assessment ? formatHeadroomScore(profile.assessment.profile_quality_score) : profile.evaluation_state.replaceAll("_", " ")}</strong>
                        </header>
                        <h4>{profile.name}</h4>
                        <p>{profile.status_explanation}</p>
                        <p className="quality-detectability-note">{profile.detectability_explanation}</p>
                        <dl>
                          <div><dt>Observed analysis periods</dt><dd>{profile.observed_window_count}</dd></div>
                          <div><dt>Parent baseline context</dt><dd>{profile.parent_workload_profile.replaceAll("_", " ")}</dd></div>
                          <div><dt>Detectability</dt><dd>{detectabilityLabel(profile.detectability_state)}</dd></div>
                          <div><dt>Current Evidence Quality</dt><dd>{profile.assessment?.current_evidence_quality == null ? "Unavailable" : `${profile.assessment.current_evidence_quality.toFixed(1)}%`}</dd></div>
                          <div><dt>History Depth</dt><dd>{profile.history_depth.label}</dd></div>
                        </dl>
                        {profile.assessment && (
                          <details>
                            <summary>Evidence Details</summary>
                            <p>{profile.assessment.explanation}</p>
                            <p>{profile.assessment.profile_quality_score === 100 ? fineQuality.full_score_explanation : fineQuality.interpretation}</p>
                            <p><strong>Observation:</strong> {formatTimestamp(profile.assessment.observed_at_utc)}</p>
                            <p><strong>Evidence independence:</strong> {evidenceIndependenceLabel(profile.assessment.evidence_independence_state)}</p>
                            <p><strong>Detection rule:</strong> {profile.assessment.detection_rule_version}</p>
                            <p><strong>Scoring method:</strong> {profile.assessment.scoring_method_version}</p>
                            <p><strong>Baseline:</strong> version {profile.assessment.baseline_version_number ?? fineQuality.active_baseline_version ?? "Unavailable"}</p>
                            <p><strong>Baseline source:</strong> {profile.assessment.baseline_source === "__device__" ? "Device" : profile.assessment.baseline_source.replaceAll("_", " ")}</p>
                            <p><strong>Exact stored score:</strong> {profile.assessment.profile_quality_score?.toFixed(4) ?? "Not evaluated"}</p>
                            <div className="table-wrap">
                              <table>
                                <thead><tr><th>Metric</th><th>Observed</th><th>Reference</th><th>Component score</th><th>Effective weight</th><th>Deduction</th></tr></thead>
                                <tbody>
                                  {profile.assessment.metric_contributions.map((metric) => (
                                    <tr key={metric.metric_key}>
                                      <td>{metric.metric_label}</td>
                                      <td>{metric.observed_value == null ? "Unavailable" : metric.observed_value.toFixed(2)}</td>
                                      <td>{metric.configured_threshold.recommended_high?.toFixed(2) ?? "Unavailable"}</td>
                                      <td>{metric.metric_score == null ? "Excluded" : metric.metric_score.toFixed(2)}</td>
                                      <td>{formatEffectiveWeight(metric.effective_weight)}</td>
                                      <td>{weightedDeduction(metric.effective_weight, metric.metric_score)?.toFixed(2) ?? "Excluded"}</td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                            {profile.assessment.missing_evidence.length > 0 && <p><strong>Missing evidence:</strong> {profile.assessment.missing_evidence.join(", ").replaceAll("_", " ")}</p>}
                          </details>
                        )}
                      </article>
                    ))}
                  </div>
                </>
              )}
            </section>

            <section id="hardware-suitability" className="legacy-suitability-selector" aria-label="Hardware Workload Suitability scenarios">
              <div>
                <h3>Hardware Workload Suitability</h3>
                <p>
                  These six established scenarios compare detected hardware
                  capabilities. This is a different measure from Current Workload
                  Headroom and is never substituted for it.
                </p>
              </div>
              <label className="quality-profile-control">
                <span>Hardware scenario</span>
                <select
                  value={qualityProfile}
                  onChange={(event) => setQualityProfile(event.target.value)}
                >
                  {qualityProfiles.map((profile) => (
                    <option value={profile.key} key={profile.key}>{profile.name}</option>
                  ))}
                </select>
              </label>
            </section>

            {!qualityInventory ? (
              <div className="quality-not-evaluated">
                <article>
                  <span>Evaluation state</span>
                  <strong>Not evaluated</strong>
                  <p>
                    No allowlisted hardware inventory has been collected yet.
                    Restart SmartOps to collect the local hardware inventory.
                  </p>
                </article>
              </div>
            ) : (
              <>
                <div className="quality-summary">
                  <article className={`quality-score quality-score--${
                    latestQuality?.evaluation_state === "assessed"
                      ? latestQuality.suitability_result
                      : latestQuality?.evaluation_state ?? "not_evaluated"
                  }`}>
                    <span>Workload Suitability Index</span>
                    <strong>
                      {latestQuality?.suitability_index === null
                        || latestQuality?.suitability_index === undefined
                        ? "Not evaluated"
                        : latestQuality.suitability_index.toFixed(0)}
                    </strong>
                    <p>
                      {latestQuality?.suitability_result.replaceAll("_", " ")
                        ?? "Assessment not run"}
                    </p>
                  </article>
                  <article>
                    <span>Evaluation state</span>
                    <strong>{latestQuality?.evaluation_state.replaceAll("_", " ") ?? "Not evaluated"}</strong>
                    <p>
                      {latestQuality?.evaluation_state === "provisional"
                        ? "A result is available, but an important detected capability is uncertain."
                        : latestQuality?.evaluation_state === "assessed"
                          ? "Required profile inputs were detected with sufficient reliability."
                          : "Required profile inputs are missing or no assessment exists."}
                    </p>
                  </article>
                  <article>
                    <span>Detection confidence</span>
                    <strong>
                      {latestQuality
                        ? `${latestQuality.detection_confidence.toFixed(1)}%`
                        : `${qualityInventory.detection_confidence.toFixed(1)}%`}
                    </strong>
                    <p>
                      Inventory checked {formatTimestamp(qualityInventory.last_checked_at_utc)}
                    </p>
                  </article>
                  <article>
                    <span>Selected workload</span>
                    <strong>
                      {qualityProfiles.find((profile) => profile.key === qualityProfile)?.name
                        ?? qualityProfile.replaceAll("_", " ")}
                    </strong>
                    <p>
                      {qualityProfiles.find((profile) => profile.key === qualityProfile)?.description
                        ?? "Select a workload profile to compare detected capabilities."}
                    </p>
                  </article>
                </div>

                {latestQuality?.evaluation_state === "provisional" && (
                  <div className="quality-provisional" role="status">
                    <strong>Provisional workload-suitability result.</strong>
                    <span>
                      Uncertain or unreliable capability detection is disclosed
                      below; unavailable values were not replaced with zero.
                    </span>
                  </div>
                )}

                <div className="quality-hardware-grid">
                  {[
                    ["CPU", "cpu_name"],
                    ["Installed RAM", "ram_installed_bytes"],
                    ["System storage", "system_drive_total_bytes"],
                    ["Graphics", "gpu_name"],
                    ["Windows", "windows_edition"],
                  ].map(([label, field]) => (
                    <article key={field}>
                      <span>{label}</span>
                      <strong>
                        {formatCapability(
                          qualityInventory.by_field[field]?.value,
                          field,
                        )}
                      </strong>
                      <p>
                        {qualityInventory.by_field[field]?.availability_status
                          .replaceAll("_", " ") ?? "unavailable"}
                      </p>
                    </article>
                  ))}
                </div>

                {latestQuality ? (
                  <>
                    <div className="quality-subsection">
                      <h3>Detected capabilities compared with this profile</h3>
                      <p>
                        Available component weight {latestQuality.available_component_weight.toFixed(0)}%;
                        excluded weight {latestQuality.excluded_component_weight.toFixed(0)}%.
                        Valid applicable weights are normalized before explicit
                        hard-requirement and bottleneck caps.
                      </p>
                      <div className="table-wrap">
                        <table>
                          <thead>
                            <tr>
                              <th>Component</th><th>Detected</th><th>Minimum</th>
                              <th>Recommended</th><th>Status</th><th>Score</th>
                              <th>Explanation</th>
                            </tr>
                          </thead>
                          <tbody>
                            {latestQuality.components.map((component) => (
                              <tr key={component.component_name}>
                                <td>{component.component_name.replaceAll("_", " ")}</td>
                                <td className="wrap-cell">
                                  {formatCapability(component.detected_value, component.component_name)}
                                </td>
                                <td>{formatCapability(component.minimum_threshold, component.component_name)}</td>
                                <td>{formatCapability(component.recommended_threshold, component.component_name)}</td>
                                <td>
                                  <span className={`quality-result quality-result--${
                                    component.hard_gate_status === "failed"
                                      ? "failed"
                                      : component.detection_status
                                  }`}>
                                    {component.hard_gate_status === "failed"
                                      ? "hard requirement failed"
                                      : component.detection_status.replaceAll("_", " ")}
                                  </span>
                                </td>
                                <td>
                                  {component.raw_component_score === null
                                    ? "Not evaluated"
                                    : component.raw_component_score.toFixed(1)}
                                </td>
                                <td className="wrap-cell">{component.explanation}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>

                    <div className="quality-detail-grid">
                      <article>
                        <h3>Hard requirements and bottleneck caps</h3>
                        {latestQuality.gates_and_caps.length ? (
                          <ul>
                            {latestQuality.gates_and_caps.map((gate, index) => (
                              <li key={`${gate.component_name}-${index}`}>
                                <strong>{gate.component_name.replaceAll("_", " ")}</strong>:{" "}
                                {gate.explanation}
                                {gate.applied && gate.configured_cap !== null
                                  ? ` Final score capped at ${gate.configured_cap}.`
                                  : ""}
                              </li>
                            ))}
                          </ul>
                        ) : <p>No hard-requirement or bottleneck cap was applied.</p>}
                        <p className="quality-reconstruction">
                          Weighted score before caps{" "}
                          {latestQuality.score_reconstruction.weighted_score_before_caps.toFixed(4)}
                          {" "}· stored result{" "}
                          {latestQuality.score_reconstruction.stored_score?.toFixed(4)
                            ?? "Not evaluated"}
                        </p>
                      </article>
                      <article>
                        <h3>Limiting components</h3>
                        {latestQuality.limiting_components.length ? (
                          <ol>
                            {latestQuality.limiting_components.map((item) => (
                              <li key={item.component_name}>
                                <strong>{item.component_name.replaceAll("_", " ")}</strong>
                                {" "}({item.severity}): {item.explanation}
                              </li>
                            ))}
                          </ol>
                        ) : <p>No profile-specific limiting component was identified.</p>}
                      </article>
                    </div>

                    <div className="quality-detail-grid">
                      <article>
                        <h3>Prioritized capability guidance</h3>
                        {latestQuality.recommendations.length ? (
                          <ol>
                            {latestQuality.recommendations.map((item) => (
                              <li key={`${item.rank}-${item.component_name}`}>
                                <strong>{item.priority}: {item.component_name.replaceAll("_", " ")}</strong>
                                {" "}{item.explanation} {item.expected_suitability_benefit}
                              </li>
                            ))}
                          </ol>
                        ) : <p>No capability improvement is suggested for this profile.</p>}
                        <p>
                          Guidance is generic and profile-based. SmartOps does
                          not recommend commercial products or guarantee a gain.
                        </p>
                      </article>
                      <article>
                        <h3>Unavailable, excluded, or unreliable inputs</h3>
                        {qualityInventory.unavailable_or_unreliable.length
                          || latestQuality.excluded_components.length ? (
                          <ul>
                            {qualityInventory.unavailable_or_unreliable.map((item) => (
                              <li key={item.field_name}>
                                <strong>{item.field_name.replaceAll("_", " ")}</strong>:{" "}
                                {item.availability_status.replaceAll("_", " ")}
                                {item.reliability_note ? ` — ${item.reliability_note}` : ""}
                              </li>
                            ))}
                            {latestQuality.excluded_components.map((item) => (
                              <li key={`excluded-${item.component_name}`}>
                                <strong>{item.component_name.replaceAll("_", " ")}</strong>:
                                excluded from weight normalization
                              </li>
                            ))}
                          </ul>
                        ) : <p>All applicable inputs were detected reliably.</p>}
                        <p>
                          Privacy-excluded inputs:{" "}
                          {qualityStatus?.privacy_excluded_fields
                            .map((item) => item.replaceAll("_", " "))
                            .join(", ") ?? "See the local API privacy boundary"}.
                          These fields are classified as excluded for privacy
                          and are not collected.
                        </p>
                      </article>
                    </div>

                    <div className="quality-readiness">
                      <h3>Current operating readiness — separate context</h3>
                      <div>
                        <span>
                          System Health Score:{" "}
                          <strong>
                            {latestQuality.current_operating_readiness.health
                              ?.system_health_score?.toFixed(0) ?? "Not evaluated"}
                          </strong>
                        </span>
                        <span>
                          Risk Evidence Index:{" "}
                          <strong>
                            {latestQuality.current_operating_readiness.risk
                              ?.risk_evidence_index.toFixed(0) ?? "Not evaluated"}
                          </strong>
                        </span>
                        <span>
                          Health data confidence:{" "}
                          <strong>
                            {latestQuality.current_operating_readiness.health
                              ? `${latestQuality.current_operating_readiness.health.data_confidence.toFixed(1)}%`
                              : "Not evaluated"}
                          </strong>
                        </span>
                        <span>
                          Newest health window:{" "}
                          <strong>
                            {latestQuality.current_operating_readiness
                              .newest_health_window_complete === null
                              ? "Not evaluated"
                              : latestQuality.current_operating_readiness
                                  .newest_health_window_complete
                                ? "Complete"
                                : "Incomplete"}
                          </strong>
                        </span>
                        <span>
                          Current operational limitation:{" "}
                          <strong>
                            {latestQuality.current_operating_readiness.health
                              ? latestQuality.current_operating_readiness.health
                                  .health_band.replaceAll("_", " ")
                              : "Not evaluated"}
                          </strong>
                        </span>
                      </div>
                      <p>{latestQuality.current_operating_readiness.separation}</p>
                    </div>

                    <details className="quality-limitations">
                      <summary>Profile assumptions and limitations</summary>
                      <ul>
                        {latestQuality.limitations.map((item) => (
                          <li key={item}>{item}</li>
                        ))}
                      </ul>
                    </details>
                  </>
                ) : (
                  <div className="quality-not-evaluated">
                    <article>
                      <strong>Inventory available; profile not evaluated.</strong>
                      <p>
                        Run <code>python -m analytics.quality --evaluate</code>{" "}
                        to create deterministic assessments.
                      </p>
                    </article>
                  </div>
                )}

                <div className="quality-subsection">
                  <div className="section-heading">
                    <div>
                      <p className="eyebrow">Preserved local assessments</p>
                      <h3>Previous quality-check history</h3>
                    </div>
                    <span>{qualityHistory.length} recent results</span>
                  </div>
                  {qualityHistory.length === 0 ? (
                    <p className="unavailable-copy">
                      No previous assessment is available for this workload profile.
                    </p>
                  ) : (
                    <div className="table-wrap">
                      <table>
                        <thead>
                          <tr>
                            <th>Checked</th>
                            <th>Profile</th>
                            <th>Result</th>
                            <th>Index</th>
                            <th>Detection confidence</th>
                          </tr>
                        </thead>
                        <tbody>
                          {qualityHistory.map((item) => (
                            <tr key={item.id}>
                              <td>{formatTimestamp(item.assessed_at_utc)}</td>
                              <td>{item.profile_name}</td>
                              <td>{item.suitability_result.replaceAll("_", " ")}</td>
                              <td>
                                {item.suitability_index === null
                                  ? "Not evaluated"
                                  : item.suitability_index.toFixed(1)}
                              </td>
                              <td>{item.detection_confidence.toFixed(1)}%</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>

                <p className="quality-version">
                  Inventory provider {qualityInventory.provider_version}
                  {" "}· algorithm {latestQuality?.algorithm_version
                    ?? qualityStatus?.algorithm_version ?? "Unavailable"}
                  {" "}· configuration {latestQuality?.configuration_version
                    ?? qualityStatus?.configuration_version ?? "Unavailable"}
                  {" "}· catalogue {latestQuality?.catalogue_version
                    ?? qualityStatus?.catalogue_version ?? "Unavailable"}
                </p>
              </>
            )}
          </section>

          <section
            className="settings-page"
            hidden={activeRoute !== "settings"}
            aria-label="SmartOps application settings"
          >
            <div className="settings-intro">
              <div>
                <p className="eyebrow">Centralized application preferences</p>
                <h2>Application settings</h2>
              </div>
              <span>Display preferences stay in this browser</span>
            </div>

            <div className="settings-grid">
              <article id="settings-general" className="settings-card">
                <h3>General</h3>
                <p className="settings-card__description">
                  Choose where SmartOps opens when no explicit hash route is
                  present.
                </p>
                <label className="settings-field">
                  <span>Default landing page</span>
                  <select
                    value={dashboardPreferences.defaultLandingPage}
                    onChange={(event) =>
                      updateDashboardPreferences({
                        defaultLandingPage: event.target.value as AppRoute,
                      })
                    }
                  >
                    <option value="overview">Overview</option>
                    <option value="live-monitoring">Live Monitoring</option>
                    <option value="predictive-alerts">Predictive Alerts</option>
                    <option value="root-cause-analysis">Root-Cause Analysis</option>
                    <option value="system-health">System Health</option>
                    <option value="pc-quality-check">PC Quality Check</option>
                    <option value="research-validation">
                      Research &amp; Validation
                    </option>
                    <option value="settings">Settings</option>
                  </select>
                </label>
                <label className="settings-toggle">
                  <span>
                    <strong>Remember last opened page</strong>
                    <small>
                      Overrides the default landing page on the next route-less
                      launch.
                    </small>
                  </span>
                  <input
                    type="checkbox"
                    role="switch"
                    aria-label="Remember last opened page"
                    aria-checked={dashboardPreferences.rememberLastPage}
                    checked={dashboardPreferences.rememberLastPage}
                    onChange={(event) =>
                      updateDashboardPreferences({
                        rememberLastPage: event.target.checked,
                      })
                    }
                  />
                </label>
              </article>

              <article id="settings-dashboard" className="settings-card">
                <h3>Dashboard</h3>
                <p className="settings-card__description">
                  These options change browser presentation only. They never
                  stop telemetry collection or local analytics.
                </p>
                <fieldset className="settings-choice-group">
                  <legend>Time display</legend>
                  <label>
                    <input
                      type="radio"
                      name="time-display"
                      value="12-hour"
                      checked={dashboardPreferences.timeDisplay === "12-hour"}
                      onChange={() =>
                        updateDashboardPreferences({ timeDisplay: "12-hour" })
                      }
                    />
                    12-hour
                  </label>
                  <label>
                    <input
                      type="radio"
                      name="time-display"
                      value="24-hour"
                      checked={dashboardPreferences.timeDisplay === "24-hour"}
                      onChange={() =>
                        updateDashboardPreferences({ timeDisplay: "24-hour" })
                      }
                    />
                    24-hour
                  </label>
                </fieldset>
                <label className="settings-toggle">
                  <span>
                    <strong>Relative timestamps</strong>
                    <small>Show values such as “2 minutes ago”.</small>
                  </span>
                  <input
                    type="checkbox"
                    role="switch"
                    aria-label="Use relative timestamps"
                    aria-checked={dashboardPreferences.relativeTimestamps}
                    checked={dashboardPreferences.relativeTimestamps}
                    onChange={(event) =>
                      updateDashboardPreferences({
                        relativeTimestamps: event.target.checked,
                      })
                    }
                  />
                </label>
                <label className="settings-toggle">
                  <span>
                    <strong>Automatic dashboard refresh</strong>
                    <small>
                      One coordinated browser refresh every 30 seconds.
                    </small>
                  </span>
                  <input
                    type="checkbox"
                    role="switch"
                    aria-label="Enable automatic dashboard refresh"
                    aria-checked={dashboardPreferences.automaticRefresh}
                    checked={dashboardPreferences.automaticRefresh}
                    onChange={(event) =>
                      updateDashboardPreferences({
                        automaticRefresh: event.target.checked,
                      })
                    }
                  />
                </label>
                <button
                  type="button"
                  className="settings-action"
                  onClick={() => void loadMetrics()}
                  disabled={isRefreshing}
                >
                  {isRefreshing ? "Refreshing…" : "Refresh Now"}
                </button>
              </article>

              <article id="settings-notifications" className="settings-card settings-card--wide">
                <h3>Notifications</h3>
                <p className="settings-card__description">
                  SmartOps uses the native Windows provider directly. No cloud
                  notification service is involved.
                </p>
                <div className="notification-settings-header">
                  <div>
                    <span>Current state</span>
                    <strong>
                      {notificationStatus?.enabled ? "Enabled" : "Disabled"}
                    </strong>
                  </div>
                  <label className="settings-toggle settings-toggle--compact">
                    <span>
                      <strong>Master notification toggle</strong>
                    </span>
                    <input
                      type="checkbox"
                      role="switch"
                      aria-label="Enable native Windows notifications"
                      aria-checked={notificationStatus?.enabled ?? false}
                      checked={notificationStatus?.enabled ?? false}
                      disabled={
                        notificationActionPending || notificationStatus === null
                      }
                      onChange={(event) =>
                        void saveNotificationPreferences(event.target.checked)
                      }
                    />
                  </label>
                </div>
                <dl className="settings-facts">
                  <div>
                    <dt>Windows native support</dt>
                    <dd>
                      {notificationStatus === null
                        ? "Checking"
                        : notificationStatus.supported
                          ? "Available"
                          : "Unavailable in this execution environment"}
                    </dd>
                  </div>
                  <div>
                    <dt>Provider</dt>
                    <dd>{notificationStatus?.provider_name ?? "Checking"}</dd>
                  </div>
                  <div>
                    <dt>Eligible levels</dt>
                    <dd>
                      {(notificationStatus?.eligible_categories ?? [])
                        .map(
                          (category) =>
                            notificationStatus?.notification_category_mapping[
                              category
                            ] ?? category,
                        )
                        .join(", ") || "None selected"}
                    </dd>
                  </div>
                </dl>
                <fieldset className="notification-severity-settings">
                  <legend>Automatic notification eligibility</legend>
                  <label>
                    <input
                      type="checkbox"
                      checked={
                        notificationStatus?.eligible_categories.includes(
                          "advisory",
                        ) ?? false
                      }
                      disabled={
                        notificationActionPending || notificationStatus === null
                      }
                      onChange={(event) =>
                        updateNotificationCategory(
                          "advisory",
                          event.target.checked,
                        )
                      }
                    />
                    <span>
                      <strong>Advisory</strong>
                      <small>Informational and advisory alerts; off by default.</small>
                    </span>
                  </label>
                  <label>
                    <input
                      type="checkbox"
                      checked={
                        notificationStatus?.eligible_categories.includes(
                          "warning",
                        ) ?? false
                      }
                      disabled={
                        notificationActionPending || notificationStatus === null
                      }
                      onChange={(event) =>
                        updateNotificationCategory(
                          "warning",
                          event.target.checked,
                        )
                      }
                    />
                    <span>
                      <strong>Warning</strong>
                      <small>High alerts; enabled by default.</small>
                    </span>
                  </label>
                  <label>
                    <input
                      type="checkbox"
                      checked={
                        notificationStatus?.eligible_categories.includes(
                          "urgent",
                        ) ?? false
                      }
                      disabled={
                        notificationActionPending || notificationStatus === null
                      }
                      onChange={(event) =>
                        updateNotificationCategory(
                          "urgent",
                          event.target.checked,
                        )
                      }
                    />
                    <span>
                      <strong>Urgent</strong>
                      <small>Critical Evidence alerts; enabled by default.</small>
                    </span>
                  </label>
                </fieldset>
                <p className="settings-note">
                  Low, Guarded and Elevated do not notify by default. One
                  notification is allowed per activation, plus one additional
                  notification for a meaningful eligible escalation. Historical,
                  unchanged, resolved, incomplete and not-evaluated conditions
                  are never replayed as notifications.
                </p>
                <div className="notification-test-action">
                  <div>
                    <strong>Troubleshoot native notifications</strong>
                    <span>
                      This explicit test is separate from enabling notifications
                      and creates no predictive evidence or delivery history.
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => void sendTestNotification()}
                    disabled={
                      notificationActionPending
                      || !notificationStatus?.enabled
                      || !notificationStatus.supported
                    }
                  >
                    {notificationActionPending
                      ? "Working…"
                      : "Send Test Notification"}
                  </button>
                </div>
                {notificationActionMessage && (
                  <p
                    className="notification-action-message"
                    role="status"
                    aria-live="polite"
                  >
                    {notificationActionMessage}
                  </p>
                )}
                {notificationLimitationMessage && (
                  <p className="settings-note" role="status">
                    {notificationLimitationMessage}
                  </p>
                )}
                <p className="settings-note">
                  Windows Focus Assist, notification permissions, session type,
                  or organizational policy can suppress a visible toast even
                  after Windows accepts the submission.
                </p>
              </article>

              <article id="settings-monitoring-data" className="settings-card">
                <h3>Monitoring &amp; Data</h3>
                <dl className="settings-facts settings-facts--stacked">
                  <div>
                    <dt>System data collection</dt>
                    <dd>
                      {applicationSettings?.production_sampling_seconds ?? 30}
                      {" "}seconds
                    </dd>
                  </div>
                  <div>
                    <dt>Analysis periods</dt>
                    <dd>
                      {applicationSettings?.feature_window_minutes ?? 5} minutes
                    </dd>
                  </div>
                  <div>
                    <dt>Last successful collection</dt>
                    <dd>
                      {applicationSettings?.last_collection_timestamp_utc
                        ? formatTimestamp(
                            applicationSettings.last_collection_timestamp_utc,
                          )
                        : "Unavailable"}
                    </dd>
                  </div>
                  <div>
                    <dt>Calibration status</dt>
                    <dd>
                      {baselineManagement?.calibration_state === "not_applicable_no_calibration_running"
                        ? "No calibration is currently running"
                        : baseline?.state.replaceAll("_", " ") ?? "Unavailable"}
                    </dd>
                  </div>
                </dl>
                <p className="settings-note">
                  Collection timing, five-minute analysis, analytical thresholds,
                  and data retention are read-only here.
                </p>
              </article>

              <article id="settings-personal-baseline" className="settings-card settings-card--wide">
                <h3>Personal Baseline</h3>
                <p className="settings-card__description">
                  Your personal baseline is healthy. No recalibration is required.
                  Software updates and additional PC Quality profiles do not require
                  another calibration. Previous versions remain available for rollback.
                </p>
                <dl className="settings-facts">
                  <div>
                    <dt>Active version</dt>
                    <dd>
                      {baselineManagement?.active_version
                        ? `Version ${baselineManagement.active_version.version_number}`
                        : "Unavailable"}
                    </dd>
                  </div>
                  <div>
                    <dt>Activation date</dt>
                    <dd>
                      {baselineManagement?.active_version?.activated_at_utc
                        ? formatTimestamp(baselineManagement.active_version.activated_at_utc)
                        : "Not available"}
                    </dd>
                  </div>
                  <div>
                    <dt>Integrity / health status</dt>
                    <dd>{baselineManagement?.active_version ? "Healthy and available" : "Unavailable"}</dd>
                  </div>
                  <div>
                    <dt>New calibration</dt>
                    <dd>
                      {baselineManagement?.candidate_version
                        ? `Version ${baselineManagement.candidate_version.version_number}`
                        : "No calibration is currently running"}
                    </dd>
                  </div>
                  <div>
                    <dt>Calibration status</dt>
                    <dd>
                      {baselineManagement?.candidate_version?.lifecycle_state
                        .replaceAll("_", " ") ?? "Not applicable"}
                    </dd>
                  </div>
                  <div>
                    <dt>Collection status</dt>
                    <dd>
                      {baselineManagement?.candidate_version?.learning_state
                        .replaceAll("_", " ") ?? "Not applicable"}
                    </dd>
                  </div>
                  <div>
                    <dt>Original start time</dt>
                    <dd>
                      {baselineManagement?.candidate_version?.learning_started_at_utc
                        ? formatTimestamp(baselineManagement.candidate_version.learning_started_at_utc)
                        : "Not available"}
                    </dd>
                  </div>
                  <div>
                    <dt>Last learning time</dt>
                    <dd>
                      {baselineManagement?.candidate_version?.last_learning_at_utc
                        ? formatTimestamp(
                            baselineManagement.candidate_version.last_learning_at_utc,
                          )
                        : "Not available"}
                    </dd>
                  </div>
                  <div>
                    <dt>Last new eligible window</dt>
                    <dd>
                      {baselineManagement?.candidate_version?.last_new_eligible_window_utc
                        ? formatTimestamp(baselineManagement.candidate_version.last_new_eligible_window_utc)
                        : "Not available"}
                    </dd>
                  </div>
                </dl>
                {baselineManagement?.candidate_version && (
                  <p className="baseline-state-explanation" role="status">
                    {baselineManagement.candidate_version.learning_state_explanation}
                  </p>
                )}
                {baselineManagement?.candidate_version && (
                  <p className="settings-note">
                    Profile status shows readiness. Completeness is supporting context and
                    need not reach 100% once the required representative evidence is available.
                    Unused optional workloads do not block activation.
                  </p>
                )}
                {baselineManagement?.candidate_version && (
                  <div className="table-scroll">
                    <table className="data-table baseline-management-table">
                      <thead>
                        <tr>
                          <th>Workload profile</th>
                          <th>Status</th>
                          <th>Applicability</th>
                          <th>Observed</th>
                           <th>Accepted analysis periods</th>
                           <th>Evidence audit</th>
                          <th>Excluded</th>
                          <th>Days</th>
                          <th>Completeness</th>
                          <th>Blocking reason</th>
                          <th>Information</th>
                        </tr>
                      </thead>
                      <tbody>
                        {baselineManagement.candidate_version.profiles.map((profile) => (
                          <tr key={profile.workload_scope}>
                            <td>{profile.workload_scope.replaceAll("_", " ")}</td>
                            <td title="Readiness based on eligible complete windows and distinct collection days.">
                              {formatProfileStatus(profile)}
                            </td>
                            <td title="Average analysis-period coverage; 100% is not required after readiness requirements are met.">
                              {(profile.applicability_state ?? "not evaluated")
                                .replaceAll("_", " ")}
                            </td>
                            <td>{profile.observed_window_count}</td>
                           <td>{profile.eligible_window_count}</td>
                           <td title="Append-only accepted, removed and reaccepted membership transitions.">
                             {profile.membership_audit
                               ? `${profile.membership_audit.historical_acceptance_events} accepted / ${profile.membership_audit.audited_removal_events} removed / ${profile.membership_audit.reacceptance_events} reaccepted`
                               : "Not available"}
                           </td>
                            <td>{profile.excluded_window_count}</td>
                            <td>{profile.distinct_day_count}</td>
                            <td>
                              {profile.sampling_completeness === null
                                ? "Not available"
                                : `${(profile.sampling_completeness * 100).toFixed(0)}%`}
                            </td>
                            <td title="Only unmet readiness requirements appear here.">
                              {formatBlockingReason(profile)}
                            </td>
                            <td>
                              {[...profile.informational_reason_codes,
                                ...profile.applicability_reasons]
                                .join(", ").replaceAll("_", " ") || "None"}
                              {Object.keys(profile.exclusion_reason_counts ?? {}).length > 0
                                ? `; exclusions: ${Object.entries(profile.exclusion_reason_counts)
                                    .map(([reason, count]) => `${reason.replaceAll("_", " ")} (${count})`)
                                    .join(", ")}`
                                : ""}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                <div className="optional-calibration-intro">
                  <h4>Optional recalibration</h4>
                  <p>
                    Recalibration is optional. Consider it after major hardware
                    changes, major system changes, significantly different usage
                    patterns, or when the current baseline is no longer representative.
                    SmartOps updates do not require recalibration.
                  </p>
                </div>
                <div className="baseline-management-actions" aria-label="Optional calibration actions">
                  {!baselineManagement?.candidate_version
                    && baselineManagement?.calibration_available === true && (
                    <button
                      type="button"
                      onClick={() => void runBaselineAction("start")}
                      disabled={baselineActionPending || !baselineManagement?.active_version}
                    >
                      Start New Calibration
                    </button>
                  )}
                  {baselineManagement?.candidate_version?.learning_state === "collecting" && (
                    <button type="button" onClick={() => void runBaselineAction("pause")} disabled={baselineActionPending}>
                      Pause Calibration
                    </button>
                  )}
                  {baselineManagement?.candidate_version?.learning_state === "paused" && (
                    <button type="button" onClick={() => void runBaselineAction("resume")} disabled={baselineActionPending}>
                      Resume Calibration
                    </button>
                  )}
                  {baselineManagement?.candidate_version?.lifecycle_state === "ready"
                    && baselineManagement.candidate_version.learning_state === "ready_for_validation" && (
                    <button type="button" onClick={() => void runBaselineAction("continue")} disabled={baselineActionPending}>
                      Continue Calibration
                    </button>
                  )}
                  {baselineManagement?.candidate_version && (
                    <button type="button" onClick={() => void runBaselineAction("cancel")} disabled={baselineActionPending}>
                      Cancel New Calibration
                    </button>
                  )}
                  {baselineManagement?.candidate_version?.lifecycle_state === "ready" && (
                    <button type="button" onClick={() => void runBaselineAction("activate")} disabled={baselineActionPending}>
                      Activate New Calibration
                    </button>
                  )}
                  {(baselineManagement?.active_version?.previous_version_id ?? null) !== null && (
                    <button type="button" onClick={() => void runBaselineAction("rollback")} disabled={baselineActionPending}>
                      Roll back to previous version
                    </button>
                  )}
                </div>
                {baselineActionMessage && (
                  <p className="notification-action-message" role="status" aria-live="polite">
                    {baselineActionMessage}
                  </p>
                )}
                {baselineTechnicalMessage && (
                  <details className="settings-technical-details">
                    <summary>Show Technical Details</summary>
                    <p>{baselineTechnicalMessage}</p>
                  </details>
                )}
                <p className="settings-note">
                  {baselineManagement?.guidance
                    ?? "No calibration is currently running. Your active personal baseline remains in use."}
                </p>
                {baselineManagement?.candidate_version && <p className="settings-note">
                  The Idle profile means genuine user-input inactivity of at least
                  five minutes. System activity is tracked separately, so safe
                  background work does not erase an idle period. Ordinary excluded
                  windows remain auditable but do not block readiness after the
                  required eligible analysis periods and days are present. Unused profiles
                  are not required for activation. Never intentionally overheat,
                  crash, or dangerously stress the computer. Recalibration does not
                  prove improved predictive accuracy.
                </p>}
              </article>

              <article id="settings-privacy" className="settings-card">
                <h3>Privacy</h3>
                <p>
                  SmartOps processes operational system data and analysis
                  notification decisions locally on this computer. It does not
                  upload system data or use cloud notification services.
                </p>
                <p>
                  It does not collect file contents, command lines, browser
                  history, typed text, credentials, passwords, private documents
                  or window titles.
                </p>
                <p className="settings-note">
                  This page intentionally provides no data-reset, history-delete,
                  agent-shutdown or automatic-remediation controls.
                </p>
              </article>

              <article id="settings-system-information" className="settings-card settings-card--wide">
                <h3>About and Diagnostics</h3>
                <p>SmartOps is running locally on this computer.</p>
                <details className="settings-technical-details">
                  <summary>Technical Details</summary>
                <dl className="settings-facts">
                  <div>
                    <dt>Processing</dt>
                    <dd>Local-only</dd>
                  </div>
                  <div>
                    <dt>Schema version</dt>
                    <dd>{applicationSettings?.schema_version ?? "Unavailable"}</dd>
                  </div>
                  <div>
                    <dt>Database</dt>
                    <dd>
                      {applicationSettings?.database_status ?? "Unavailable"}
                    </dd>
                  </div>
                  <div>
                    <dt>Foreign keys</dt>
                    <dd>
                      {applicationSettings === null
                        ? "Unavailable"
                        : applicationSettings.foreign_keys_enabled
                          ? "Enabled"
                          : "Disabled"}
                    </dd>
                  </div>
                  <div>
                    <dt>Integrity status</dt>
                    <dd>
                      {applicationSettings?.integrity_status.replaceAll("_", " ")
                        ?? "Unavailable"}
                    </dd>
                  </div>
                  <div>
                    <dt>API version</dt>
                    <dd>{applicationSettings?.api_version ?? "Unavailable"}</dd>
                  </div>
                </dl>
                <p className="settings-note">
                  {applicationSettings?.integrity_explanation
                    ?? "Database maintenance information is unavailable."}
                </p>
                </details>
              </article>
            </div>
          </section>

          <section className="process-section" hidden={activeRoute !== "live-monitoring"}>
            <div className="section-heading">
              <div>
                <p className="eyebrow">Latest snapshot</p>
                <h2>Top processes</h2>
              </div>
              <span>Names and utilization only - no command lines</span>
            </div>
            <div className="process-grid">
              <ProcessTable title="Top five by CPU" rows={processes?.cpu ?? []} />
              <ProcessTable
                title="Top five by memory"
                rows={processes?.memory ?? []}
              />
            </div>
          </section>

          <section className="history-section" hidden={activeRoute !== "live-monitoring"}>
            <div className="section-heading">
              <div>
                <p className="eyebrow">Raw records</p>
                <h2>Historical data</h2>
              </div>
              <span>{matchingTotal.toLocaleString()} records in range</span>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Collected</th>
                    <th>CPU</th>
                    <th>RAM</th>
                    <th>Disk</th>
                    <th>Disk read</th>
                    <th>Disk write</th>
                    <th>Download</th>
                    <th>Upload</th>
                    <th>User</th>
                  </tr>
                </thead>
                <tbody>
                  {tableHistory.map((metric) => (
                    <tr key={metric.id}>
                      <td>{formatTimestamp(metric.timestamp_utc)}</td>
                      <td>{formatPercent(metric.cpu_percent)}</td>
                      <td>{formatPercent(metric.ram_percent)}</td>
                      <td>{formatPercent(metric.disk_percent)}</td>
                      <td>{formatRate(metric.disk_read_bytes_per_second)}</td>
                      <td>{formatRate(metric.disk_write_bytes_per_second)}</td>
                      <td>{formatRate(metric.network_download_bytes_per_second)}</td>
                      <td>{formatRate(metric.network_upload_bytes_per_second)}</td>
                      <td>{metric.user_state ?? "Unavailable"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="pagination">
              <button
                type="button"
                disabled={page === 0}
                onClick={() => setPage((current) => Math.max(0, current - 1))}
              >
                Previous
              </button>
              <span>
                Page {Math.min(page + 1, pageCount)} of {pageCount}
              </span>
              <button
                type="button"
                disabled={page + 1 >= pageCount}
                onClick={() => setPage((current) => current + 1)}
              >
                Next
              </button>
            </div>
          </section>
        </>
      )}

      <footer>
          <span>SmartOps local operations</span>
        <span>Local-only — no system data leaves this PC</span>
      </footer>
    </AppShell>
  );
}

export default App;
