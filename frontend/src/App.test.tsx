import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";

const timestamp = "2026-07-26T10:00:00Z";

const metric = {
  id: 1,
  timestamp_utc: timestamp,
  device_id: "local-device",
  cpu_percent: 42.5,
  cpu_per_core_percent: [40, 45],
  cpu_physical_cores: 4,
  cpu_logical_cores: 8,
  cpu_frequency_mhz: 3200,
  process_count: 120,
  thread_count: 1500,
  ram_percent: 61.2,
  ram_used_bytes: 8_000_000_000,
  ram_available_bytes: 8_000_000_000,
  ram_total_bytes: 16_000_000_000,
  swap_percent: 2,
  swap_used_bytes: 100_000_000,
  swap_total_bytes: 4_000_000_000,
  disk_percent: 55,
  disk_used_bytes: 500_000_000_000,
  disk_free_bytes: 500_000_000_000,
  disk_total_bytes: 1_000_000_000_000,
  disk_partitions: [],
  disk_read_bytes_per_second: 1024,
  disk_write_bytes_per_second: 2048,
  disk_read_ops_per_second: 2,
  disk_write_ops_per_second: 3,
  network_upload_bytes_per_second: 512,
  network_download_bytes_per_second: 4096,
  network_packets_sent_per_second: 2,
  network_packets_received_per_second: 4,
  network_interface_available: true,
  battery_percent: null,
  battery_charging: null,
  ac_power_connected: null,
  battery_seconds_remaining: null,
  uptime_seconds: 3600,
  boot_timestamp_utc: "2026-07-26T09:00:00Z",
  user_idle_seconds: 10,
  user_state: "active",
  foreground_process_name: "Code.exe",
  cpu_temperature_celsius: null,
  gpu_utilization_percent: null,
  gpu_memory_percent: null,
  gpu_temperature_celsius: null,
  workload_class: "development",
  workload_confidence: 0.9,
  workload_reasons: ["development_process"],
  user_activity_state: "active",
  system_activity_state: "background",
  workload_rule_version: "phase7b1-workload-v2",
};

const baseline = {
  state: "collecting_data",
  device_id: "local-device",
  eligible_window_count: 4,
  distinct_collection_days: 1,
  minimum_device_windows: 30,
  minimum_workload_windows: 20,
  minimum_distinct_days: 3,
  recommended_history_days: 7,
  preferred_history_days: 14,
  device_profile: null,
  workload_profiles: [],
  last_successful_training_utc: null,
};

const alertStatus = {
  status: "evaluated",
  open_alert_count: 0,
  state_counts: {},
  active_severity_counts: {},
  algorithm_version: "5a",
  configuration_version: "5a",
  catalogue_version: "5a",
  interpretation:
    "SmartOps alerts indicate observed evidence, not guaranteed failure.",
};

const sampleAlert: Record<string, any> = {
  id: 7,
  alert_code: "SO-CPU-01",
  category: "resource_pressure",
  title: "Sustained CPU pressure",
  description: "CPU pressure persisted across eligible windows.",
  current_severity: "advisory",
  peak_severity: "advisory",
  state: "open",
  evaluation_state: "established",
  data_confidence: 91,
  workload_context: "idle",
  workload_confidence: 0.9,
  first_observed_utc: timestamp,
  latest_observed_utc: timestamp,
  last_evidence_utc: timestamp,
  occurrence_count: 2,
  consecutive_window_count: 2,
  duration_seconds: 300,
  trend_direction: "stable",
  recovery_window_count: 0,
  recovery_state: "not_recovering",
  source_feature_window_id: 4,
  probable_factors: [{ domain: "cpu_pressure", rank: 1, confidence: 0.8 }],
  contradictory_evidence: [],
  excluded_inputs: [],
  reason_codes: ["cpu_pressure"],
  algorithm_version: "5a",
  configuration_version: "5a",
  catalogue_version: "5a",
  acknowledged_at_utc: null,
  resolved_at_utc: null,
  evidence: [
    {
      id: 1,
      evidence_type: "metric",
      evidence_key: "cpu_avg",
      correlation_group: "cpu",
      raw_evidence: {},
      effective_evidence: {},
      suppressed: false,
      suppression_reason: null,
      explanation: "CPU remained elevated.",
    },
  ],
  explanations: [],
  diagnostic_recommendations: ["Review sustained CPU consumers."],
  preventive_guidance: ["Confirm the workload is expected."],
  short_alert_basis: "CPU pressure persisted across eligible windows.",
  alert_confidence: null as number | null,
  confidence_label: null as "low" | "moderate" | "high" | null,
  validation: {
    validation_type: "not_yet_validated",
    display_label: "Not yet validated",
    sample_size: 0,
    accuracy: null,
    precision: null,
    recall: null,
    specificity: null,
    f1_score: null,
    false_positive_rate: null,
    limitations: ["Confirmed outcomes are not yet sufficient."],
  },
  outcome: null,
  explanation_snapshot: null,
  transitions: [] as Array<Record<string, unknown>>,
  occurrences: [] as Array<Record<string, unknown>>,
  notification_deliveries: [] as Array<Record<string, unknown>>,
};

const healthAssessment = {
  id: 3,
  feature_window_id: 4,
  window_start_utc: "2026-07-26T09:55:00Z",
  window_end_utc: timestamp,
  assessed_at_utc: timestamp,
  system_health_score: 88,
  health_band: "good",
  evaluation_state: "provisional",
  data_confidence: 84,
  coverage_ratio: 1,
  workload_context: "development",
  workload_confidence: 0.9,
  available_component_weight: 90,
  excluded_component_weight: 10,
  normalization_method: "available_weight",
  algorithm_version: "4a",
  configuration_version: "4a",
  reason_codes: [],
  first_observed_utc: timestamp,
  most_recent_observed_utc: timestamp,
  consecutive_window_count: 1,
  persistence_duration_seconds: 0,
  trend_direction: "stable",
  recovery_state: "stable",
  components: [],
  deductions: [],
  inputs: [],
  excluded_inputs: [],
  guidance: {
    explanations: [],
    recommendations: [],
    improvements: [],
    limitations: [],
  },
  baseline_reference: null,
  deviation_reference: null,
  risk_reference: null,
  score_reconstruction: 88,
};

const qualityAssessment = {
  id: 2,
  inventory_snapshot_id: 1,
  inventory_timestamp_utc: timestamp,
  assessed_at_utc: timestamp,
  profile_key: "software_development",
  profile_name: "Software Development",
  profile_version: "4b",
  suitability_index: 82,
  suitability_result: "suitable",
  evaluation_state: "assessed",
  detection_confidence: 90,
  available_component_weight: 100,
  excluded_component_weight: 0,
  normalization_method: "available_weight",
  algorithm_version: "4b",
  configuration_version: "4b",
  catalogue_version: "4b",
  reason_codes: [],
  explanation: "Suitable for the selected workload.",
  limitations: [],
  components: [],
  gates_and_caps: [],
  limiting_components: [],
  recommendations: [],
  current_operating_readiness: {
    health: null,
    risk: null,
    newest_health_window_complete: true,
    separation: "Suitability is separate from current operating health.",
  },
  score_reconstruction: {
    weighted_score_before_caps: 82,
    final_score_after_caps: 82,
    stored_score: 82,
    method: "weighted",
  },
  excluded_components: [],
};

type Scenario = {
  alerts?: typeof sampleAlert[];
  alertHistoryUsesSummaries?: boolean;
  alertDetailDelayMs?: number;
  alertDetailFailuresRemaining?: number;
  health?: typeof healthAssessment | null;
  quality?: typeof qualityAssessment | null;
  fail?: boolean;
  historyTotal?: number;
  candidate?: boolean;
  fineQualityUnavailable?: boolean;
  pipelineState?: "processing" | "successfully_waiting" | "failed" | "overdue" | "not_applicable";
};

let scenario: Scenario;
let notificationEnabled = true;

function responseFor(input: RequestInfo | URL, init?: RequestInit) {
  if (scenario.fail) return Promise.reject(new Error("API offline"));
  const url = new URL(String(input));
  const path = url.pathname;

  if (path === "/api/notifications/preferences" && init?.method === "PUT") {
    const body = JSON.parse(String(init.body)) as {
      enabled: boolean;
      eligible_categories?: string[];
    };
    const changedToEnabled = !notificationEnabled && body.enabled;
    const changedToDisabled = notificationEnabled && !body.enabled;
    notificationEnabled = body.enabled;
    return ok({
      preferences: {
        enabled: body.enabled,
        eligible_categories: body.eligible_categories ?? ["warning", "urgent"],
        eligible_severities: body.eligible_categories ?? ["warning", "urgent"],
      },
      transition: changedToEnabled
        ? "enabled"
        : changedToDisabled
          ? "disabled"
          : "unchanged",
      message: changedToEnabled
        ? "Notifications have been enabled."
        : changedToDisabled
          ? "Notifications have been disabled."
          : "Notification preferences have been saved.",
      confirmation_notification: {
        attempted: changedToEnabled,
        delivered: changedToEnabled,
        status: changedToEnabled ? "submitted" : "not_applicable",
        user_message: changedToEnabled
          ? "Windows accepted the notification. Focus Assist or an organizational policy may still suppress its display."
          : null,
      },
    });
  }
  if (path === "/api/notifications/test" && init?.method === "POST") {
    return ok({ delivered: true, status: "submitted", test_only: true });
  }
  if (init?.method && init.method !== "GET") {
    return Promise.resolve(new Response(JSON.stringify({ status: "ok" }), { status: 200 }));
  }
  if (path === "/api/status") return ok({ status: "ok" });
  if (path === "/api/runtime/status") {
    return ok({
      state: "live",
      agent_running: true,
      heartbeat_at_utc: timestamp,
      heartbeat_age_seconds: 2,
      stale_after_seconds: 90,
    });
  }
  if (path === "/api/settings/status") {
    return ok({
      schema_version: 14,
      database_status: "connected",
      integrity_status: "available_for_manual_verification",
      integrity_explanation: "Full integrity checks run as maintenance checks.",
      foreign_keys_enabled: true,
      telemetry_sampling_seconds: 30,
      production_sampling_seconds: 30,
      feature_window_minutes: 5,
      processing_mode: "local_only",
      last_collection_timestamp_utc: timestamp,
      api_version: "0.12.0",
    });
  }
  if (path === "/api/enhanced-evidence/status") {
    return ok({
      status: "available",
      shadow_mode: true,
      device_id: "local-device",
      signals: [
        {
          id: 1,
          collection_run_id: 1,
          device_id: "local-device",
          timestamp_utc: timestamp,
          signal_group: "cpu",
          signal_key: "processor_queue_length",
          signal_label: "Processor queue length",
          numeric_value: 0,
          unit: "count",
          availability_status: "available",
          reason_code: null,
          source_name: "Windows Performance Counters",
          source_status: "supported",
          collection_frequency_seconds: 60,
          shadow_mode: true,
          trend: "stable",
          details: {},
          readiness_state: "needs_more_history",
        },
        {
          id: 2,
          collection_run_id: 1,
          device_id: "local-device",
          timestamp_utc: timestamp,
          signal_group: "storage",
          signal_key: "storage_latency",
          signal_label: "Storage latency",
          numeric_value: null,
          unit: "seconds",
          availability_status: "timeout",
          reason_code: "query_timeout",
          source_name: "Windows Performance Counters",
          source_status: "timeout",
          collection_frequency_seconds: 60,
          shadow_mode: true,
          trend: "insufficient_history",
          details: {},
          readiness_state: "collector_failure",
        },
      ],
      hidden_unsupported_signals: [{
        signal_key: "storage_wear_percent",
        signal_label: "Storage wear used",
        availability_status: "unsupported",
        reason_code: "storage_reliability_not_exposed_by_hardware",
        source_name: "Windows Storage Reliability Counters",
      }],
      capability_display_policy_version: "device-capability-display-v1",
      capability_display_message: "Only evidence supported by this device is displayed.",
      collectors: [
        {
          collector_key: "performance_counters",
          last_attempt_utc: timestamp,
          last_success_utc: timestamp,
          next_due_utc: "2026-07-26T10:01:00Z",
          availability_status: "available",
          reason_code: null,
          source_name: "Windows Performance Counters",
          collection_frequency_seconds: 60,
        },
      ],
      latest_run: {
        status: "completed",
        started_at_utc: timestamp,
        finished_at_utc: timestamp,
        collection_duration_ms: 1200,
        full_cycle_duration_ms: 1800,
        approximate_process_cpu_percent: 0.6,
        process_rss_before_bytes: 100_000_000,
        process_rss_after_bytes: 101_000_000,
        database_bytes_before: 10_000_000,
        database_bytes_after: 10_004_096,
      },
      structured_event_count: 4,
      interpretation:
        "Enhanced evidence is collected locally in shadow mode and does not change Risk Evidence, System Health, or alert severity.",
    });
  }
  if (path === "/api/metrics/latest") return ok(metric);
  if (path === "/api/metrics/count") return ok({ count: 386 });
  if (path === "/api/metrics/history") {
    return ok({
      items: [metric],
      total: scenario.historyTotal ?? 1,
      limit: Number(url.searchParams.get("limit") ?? 50),
      offset: Number(url.searchParams.get("offset") ?? 0),
      sort: "newest",
      start: null,
      end: null,
    });
  }
  if (path === "/api/processes/latest") {
    return ok({ metric_id: 1, timestamp_utc: timestamp, cpu: [], memory: [] });
  }
  if (path === "/api/workload/latest") {
    return ok({
      timestamp_utc: timestamp,
      workload_class: "development",
      workload_confidence: 0.9,
      reason_codes: ["development_process"],
      user_activity_state: "active",
      system_activity_state: "background",
      workload_rule_version: "phase7b1-workload-v2",
    });
  }
  if (path === "/api/events") return ok({ items: [] });
  if (path === "/api/events/summary") {
    return ok({ severity: {}, categories: {}, channels: [] });
  }
  if (path === "/api/features/history") {
    return ok({
      items: [{
        id: 4,
        window_start_utc: "2026-07-26T09:55:00Z",
        window_end_utc: timestamp,
        sample_count: 10,
        expected_sample_count: 10,
        coverage_ratio: 1,
        is_complete: true,
        dominant_workload_class: "development",
        dominant_user_activity_state: "active",
        dominant_system_activity_state: "background",
        workload_rule_version: "phase7b1-workload-v2",
        workload_distribution: { development: 4, browser_or_media: 6 },
        workload_majority_explanation:
          "Development was observed in 4 of 10 samples; browser or media held the foreground majority with 6 samples.",
        workload_composition: {
          sample_count: 10,
          expected_sample_count: 10,
          profiles: {
            development: { count: 4, proportion: 0.4 },
            browser_or_media: { count: 6, proportion: 0.6 },
          },
          secondary_context: "guided_development",
          secondary_rule_version: "guided-development-v1",
          reason_codes: ["recognized_development_and_browser_foreground"],
        },
        secondary_workload_context: "guided_development",
        secondary_workload_rule_version: "guided-development-v1",
        secondary_workload_reason_codes: [
          "recognized_development_and_browser_foreground",
        ],
        cpu_avg: 42,
        ram_avg: 61,
        critical_event_count: 0,
        error_event_count: 0,
        warning_event_count: 0,
      }],
    });
  }
  if (path === "/api/baseline/status") return ok(baseline);
  if (path === "/api/baseline-management/status") {
    return ok({
      device_id: "local-device",
      active_version: {
        id: scenario.candidate ? 1 : 2,
        version_number: scenario.candidate ? 1 : 2,
        version_label: scenario.candidate
          ? "Original established baseline"
          : "Permanent baseline v2",
        lifecycle_state: "active",
        learning_state: "inactive",
        learning_state_explanation: "Baseline learning is not active.",
        previous_version_id: scenario.candidate ? null : 1,
        learning_started_at_utc: timestamp,
        last_learning_at_utc: timestamp,
        last_new_eligible_window_utc: timestamp,
        profiles: [],
      },
      candidate_version: scenario.candidate ? {
        id: 2,
        version_number: 2,
        version_label: "Representative workload candidate v2",
        lifecycle_state: "ready",
        learning_state: "ready_for_validation",
        learning_state_explanation: "Candidate v2 is ready for validation but has not replaced active baseline v1.",
        previous_version_id: 1,
        learning_started_at_utc: timestamp,
        last_learning_at_utc: timestamp,
        last_new_eligible_window_utc: timestamp,
        profiles: [{
          workload_scope: "guided_development",
          observed_window_count: 8,
          eligible_window_count: 7,
          excluded_window_count: 1,
          distinct_day_count: 2,
          sampling_completeness: 0.95,
          readiness_state: "collecting_data",
          last_learning_at_utc: timestamp,
          reason_codes: ["eligible_window_count_below_minimum"],
          blocking_reason_codes: ["eligible_window_count_below_minimum"],
          blocking_reason: "eligible_window_count_below_minimum",
          informational_reason_codes: ["some_windows_excluded_by_quality_or_event_policy"],
          applicability_state: "observed_insufficient",
          applicability_reasons: ["profile_observed_but_requirements_not_met"],
          exclusion_reason_counts: { incomplete_window: 1 },
          membership_audit: {
            historical_acceptance_events: 7,
            audited_removal_events: 1,
            reacceptance_events: 2,
          },
        }],
      } : null,
      minimum_device_windows: 100,
      minimum_workload_windows: 30,
      minimum_distinct_days: 3,
      rule_version: "phase7b1-recalibration-v1",
      automatic_start: false,
      calibration_available: !scenario.candidate,
      calibration_state: scenario.candidate
        ? "candidate_ready"
        : "not_applicable_no_calibration_running",
      guidance: "Use the computer normally and safely for several days.",
      accuracy_limitation: "No accuracy claim.",
    });
  }
  if (path === "/api/deviations/latest") {
    return ok({ status: "not_evaluated", assessment: null, reason_code: "baseline_not_ready" });
  }
  if (path === "/api/deviations/history") return ok({ items: [] });
  if (path === "/api/risk/status") {
    return ok({
      status: "not_evaluated",
      reason_code: "baseline_not_ready",
      baseline_state: "collecting_data",
      deviation_assessment_count: 0,
      risk_assessment_count: 0,
      prerequisites: ["ready_baseline"],
      minimum_coverage: 0.8,
      minimum_workload_confidence: 0.5,
    });
  }
  if (path === "/api/risk/latest") {
    return ok({
      status: "not_evaluated",
      assessment: null,
      reason_code: "baseline_not_ready",
      prerequisites: ["ready_baseline"],
    });
  }
  if (path === "/api/risk/history") return ok({ items: [] });
  if (path === "/api/risk/4") {
    return ok({ status: "not_evaluated", assessment: null });
  }
  if (path === "/api/root-causes/4") {
    return ok({ status: "evaluated", feature_window_id: 4, candidates: [] });
  }
  if (path === "/api/health/status") {
    return ok({
      status: scenario.health ? "provisional" : "not_evaluated",
      reason_codes: scenario.health ? [] : ["insufficient_history"],
      assessment_counts: {},
      algorithm_version: "4a",
      configuration_version: "4a",
      interpretation: "System health is not a failure probability.",
    });
  }
  if (path === "/api/health/latest") {
    return ok({
      status: scenario.health ? "provisional" : "not_evaluated",
      assessment: scenario.health ?? null,
      reason_codes: scenario.health ? [] : ["insufficient_history"],
      interpretation: "System health is not a failure probability.",
    });
  }
  if (path === "/api/health/history") {
    return ok({ items: scenario.health ? [scenario.health] : [] });
  }
  if (path === "/api/quality/status") {
    return ok({
      status: scenario.quality ? "available" : "not_evaluated",
      assessment_counts: {},
      algorithm_version: "4b",
      configuration_version: "4b",
      catalogue_version: "4b",
      interpretation: "Suitability is not current health.",
      privacy_excluded_fields: ["serial_number"],
    });
  }
  if (path === "/api/quality/profiles") {
    return ok({
      profiles: [{
        key: "software_development",
        name: "Software Development",
        description: "Development workload",
        intended_workload: "development",
        profile_version: "4b",
        configuration_version: "4b",
        catalogue_version: "4b",
        components: {},
        limitations: [],
      }],
    });
  }
  if (path === "/api/quality/inventory/latest") {
    const values = ["cpu_name", "ram_installed_bytes", "system_drive_total_bytes", "gpu_name", "windows_edition"]
      .map((field_name) => ({
        field_name,
        component_group: "system",
        value: field_name === "cpu_name" ? "Test CPU" : null,
        availability_status: field_name === "cpu_name" ? "available" : "unavailable",
        reliability_note: null,
        source_name: "test",
      }));
    return ok({
      inventory: {
        id: 1,
        captured_at_utc: timestamp,
        last_checked_at_utc: timestamp,
        detection_confidence: 90,
        inventory_state: "available",
        provider_version: "4b",
        values,
        by_field: Object.fromEntries(values.map((item) => [item.field_name, item])),
        unavailable_or_unreliable: values.filter((item) => item.value === null),
      },
    });
  }
  if (path === "/api/quality/latest") {
    return ok({
      status: scenario.quality ? "assessed" : "not_evaluated",
      assessment: scenario.quality ?? null,
      reason_codes: scenario.quality ? [] : ["not_run"],
      interpretation: "Suitability is not current health.",
    });
  }
  if (path === "/api/quality/history") {
    return ok({ items: scenario.quality ? [scenario.quality] : [], total: scenario.quality ? 1 : 0 });
  }
  if (path === "/api/quality/profile-scores") {
    const taxonomy = [
      ["video_conferencing_online_classes", "Video conferencing and online classes", "fine_grained"],
      ["word_processing", "Word processing", "fine_grained"],
      ["spreadsheet_analysis", "Spreadsheet analysis", "fine_grained"],
      ["presentation_creation", "Presentation creation", "fine_grained"],
      ["email_calendar", "Email and calendar", "fine_grained"],
      ["pdf_document_reading", "PDF and document reading", "fine_grained"],
      ["terminal_scripting", "Terminal and scripting", "fine_grained"],
      ["software_build_compilation_testing", "Software build, compilation and testing", "fine_grained"],
      ["data_science_notebooks", "Data science and notebooks", "fine_grained"],
      ["graphic_design_photo_editing", "Graphic design and photo editing", "fine_grained"],
      ["video_editing_rendering", "Video editing and rendering", "fine_grained"],
      ["audio_production", "Audio production", "fine_grained"],
      ["cad_engineering_3d_modelling", "CAD, engineering and 3D modelling", "fine_grained"],
      ["virtual_machines_containers", "Virtual machines and containers", "fine_grained"],
      ["compression_backup_large_transfers", "File compression, backup and large transfers", "fine_grained"],
      ["remote_desktop_support", "Remote desktop and remote support", "fine_grained"],
      ["communication_chat", "Communication and chat", "fine_grained"],
      ["security_scanning_maintenance", "Security scanning and system maintenance", "fine_grained"],
      ["local_media_playback", "Local media playback", "fine_grained"],
      ["online_learning_research", "Online learning and research", "fine_grained"],
      ["device", "Device", "broad_v2"], ["gaming_or_3d", "Gaming/3D", "broad_v2"],
      ["development", "Development", "broad_v2"], ["guided_development", "Guided Development", "broad_v2"],
      ["browser_or_media", "Browser/Media", "broad_v2"], ["office_productivity", "Office Productivity", "broad_v2"],
      ["interactive_light", "Interactive Light", "broad_v2"], ["compute_intensive", "Compute Intensive", "broad_v2"],
      ["background_activity", "Background Activity", "broad_v2"], ["idle", "Idle", "broad_v2"],
    ] as const;
    const profiles = taxonomy.map(([key, name, kind], index) => {
      const evaluated = !scenario.fineQualityUnavailable && (index < 14 || key === "device");
      const insufficient = key === "compression_backup_large_transfers";
      return {
        key, name, description: `${name} profile`,
        parent_workload_profile: kind === "broad_v2" ? key : "interactive_light",
        catalogue_state: "catalogued",
        detectability_state: key === "online_learning_research"
          ? "not_independently_detectable"
          : kind === "fine_grained" ? "independently_detectable" : key === "device" ? "aggregate_detectable" : "broad_context_detectable",
        detectability_explanation: key === "online_learning_research"
          ? "Browser-based learning cannot currently be distinguished reliably from general Browser/Media without inspecting content."
          : "Recognized local evidence can identify this profile.",
        profile_kind: kind, observed_window_count: evaluated || insufficient ? 3 : 0,
        history_depth: {
          qualifying_observation_count: evaluated ? 3 : 0,
          distinct_observation_days: evaluated ? 2 : 0,
          latest_qualifying_observation_utc: evaluated ? timestamp : null,
          history_category: evaluated ? "few" : "none",
          label: evaluated ? "3 qualifying observations across 2 distinct days" : insufficient ? "Not evaluated" : "Not observed",
          baseline_training_observation_count: evaluated ? 1 : 0,
          independent_post_activation_observation_count: evaluated ? 2 : 0,
          latest_independent_post_activation_observation_utc: evaluated ? timestamp : null,
          historical_independence_unavailable_count: 0,
        },
        evaluation_state: evaluated ? "assessed" : insufficient ? "not_evaluated" : "not_observed",
        status_explanation: evaluated ? "Evaluated from genuine local evidence" : insufficient ? "Not evaluated — insufficient evidence" : "Not observed",
        calibration_required: false,
        metrics: { cpu_avg: { label: "CPU utilisation", direction: "lower_is_better", unit: "%", weight: 25, recommended_high: 80, limit_high: 98 } },
        assessment: evaluated ? {
          id: index + 1, feature_window_id: index + 100,
          profile_quality_score: key === "device"
            ? 88
            : key === "video_conferencing_online_classes" ? 99.9171 : 90 - index,
          evidence_confidence: 92, assessed_at_utc: timestamp,
          observed_at_utc: timestamp, current_evidence_quality: 92,
          detection_reason: "recognised_foreground_executable_composition",
          detection_confidence: 90, detection_evidence: {},
          detection_rule_version: "postcalibration-profile-detection-v1",
          scoring_method_version: "v2-anchored-profile-quality-v1",
          baseline_version_id: 2, baseline_source: kind === "fine_grained" ? "interactive_light" : key,
          baseline_activated_at_utc: "2026-09-01T09:00:00Z",
          baseline_training_evidence: false,
          independent_post_activation_evidence: true,
          evidence_independence_state: "independent_post_calibration_evidence",
          explanation: `Separate score for ${name}.`,
          missing_evidence: [], metric_contributions: [{
            metric_key: "cpu_avg", metric_label: "CPU utilisation", observed_value: 35 + index,
            baseline_centre: 30, expected_low: 0, expected_high: 80,
            configured_threshold: { recommended_high: 80, limit_high: 98 }, availability_status: "available",
            configured_weight: 25, effective_weight: 0.25, metric_score: 100,
            weighted_contribution: 25, explanation: "Within expected range.",
          }],
        } : null,
      };
    });
    return ok({
      active_baseline_version: 2,
      taxonomy_version: "smartops-pc-quality-taxonomy-v2",
      detection_rule_version: "postcalibration-profile-detection-v1",
      scoring_method_version: "v2-anchored-profile-quality-v1",
      semantic_version: "current-workload-headroom-semantics-v1",
      measure_name: "Current Workload Headroom",
      interpretation: "This score describes available operating headroom during the most recent qualifying observed workload period.",
      guide_explanation: "SmartOps observes how the computer behaves during a recognized workload.",
      full_score_explanation: "All measured factors in this observed period remained within their expected ranges.",
      profile_count: 30,
      fine_profile_count: 20,
      research_references: [],
      profiles,
    });
  }
  if (path === "/api/quality/profile-taxonomy") return ok({ profiles: [] });
  if (path === "/api/pipeline/status") {
    return ok({
      database_connected: true, generated_at_utc: timestamp,
      stages: [
        { stage_key: "telemetry_collection", current_state: scenario.pipelineState ?? "successfully_waiting", last_attempted_at_utc: timestamp, last_successful_at_utc: timestamp, expected_interval_seconds: 30, processing_duration_ms: 90, failure_or_overdue_reason: scenario.pipelineState === "failed" ? "controlled failure" : null },
        { stage_key: "active_baseline", current_state: "successfully_waiting", last_attempted_at_utc: null, last_successful_at_utc: timestamp, expected_interval_seconds: null, processing_duration_ms: null, failure_or_overdue_reason: null, active_baseline_version: 2 },
      ],
    });
  }
  if (path === "/api/alerts/status") {
    const alerts = scenario.alerts ?? [];
    const activeSeverityCounts = alerts.reduce<Record<string, number>>((counts, alert) => {
      counts[alert.current_severity] = (counts[alert.current_severity] ?? 0) + 1;
      return counts;
    }, {});
    return ok({ ...alertStatus, open_alert_count: alerts.length, active_severity_counts: activeSeverityCounts });
  }
  if (path === "/api/alerts/history") {
    const alerts = scenario.alerts ?? [];
    return ok({
      items: scenario.alertHistoryUsesSummaries
        ? alerts.map((alert) => ({
            ...alert,
            summary_record: true,
            explanation_snapshot: null,
            transitions: undefined,
            occurrences: undefined,
            notification_deliveries: undefined,
          }))
        : alerts,
    });
  }
  if (path.startsWith("/api/alerts/")) {
    const alertId = Number(path.split("/").at(-1));
    const alert = (scenario.alerts ?? []).find((item) => item.id === alertId);
    if ((scenario.alertDetailFailuresRemaining ?? 0) > 0) {
      scenario.alertDetailFailuresRemaining = (scenario.alertDetailFailuresRemaining ?? 1) - 1;
      return Promise.resolve(
        new Response(JSON.stringify({ detail: "Controlled detail failure." }), {
          status: 503,
          headers: { "Content-Type": "application/json" },
        }),
      );
    }
    if (scenario.alertDetailDelayMs) {
      return new Promise<Response>((resolve) => {
        window.setTimeout(() => resolve(new Response(JSON.stringify({ alert }), {
          status: alert ? 200 : 404,
          headers: { "Content-Type": "application/json" },
        })), scenario.alertDetailDelayMs);
      });
    }
    return alert
      ? ok({ alert })
      : Promise.resolve(
          new Response(JSON.stringify({ detail: "Alert not found." }), {
            status: 404,
            headers: { "Content-Type": "application/json" },
          }),
        );
  }
  if (path === "/api/notifications/status") {
    return ok({
      enabled: notificationEnabled,
      supported: true,
      status: "supported",
      provider_name: "test-provider",
      reason: null,
      eligible_severities: ["warning", "urgent"],
      eligible_categories: ["warning", "urgent"],
      semantic_severity_mapping: {
        warning: "High",
        urgent: "Critical Evidence",
      },
      notification_category_mapping: {
        advisory: "Advisory",
        warning: "Warning",
        urgent: "Urgent",
      },
      delivery_counts: {},
      delivery_meaning: "Submitted to Windows.",
    });
  }
  if (path === "/api/validation/status") {
    return ok({
      status: "insufficient_labeled_evidence",
      incident_count: 0,
      verified_incident_count: 0,
      feedback_count: 0,
      unverified_alert_count: 0,
      completed_observation_period_count: 0,
      latest_run: null,
      algorithm_version: "5b",
      configuration_version: "5b",
      matching_version: "5b",
      interpretation:
        "SmartOps validation results are based on available user-reported outcomes.",
    });
  }
  if (
    path === "/api/incidents/history" ||
    path === "/api/validation/observation-periods" ||
    path === "/api/validation/unverified-alerts" ||
    path === "/api/validation/feedback"
  ) return ok({ items: [] });

  return ok({});
}

function ok(body: unknown) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

async function renderAt(hash: string) {
  window.history.replaceState(null, "", hash);
  render(<App />);
  const normalizedHash = (hash || "#/overview").split("?", 1)[0];
  await screen.findByRole("heading", {
    name: {
      "#/overview": "SmartOps overview",
      "#/live-monitoring": "Live Monitoring",
      "#/predictive-alerts": "Predictive Alerts",
      "#/root-cause-analysis": "Root-Cause Analysis",
      "#/system-health": "System Health",
      "#/pc-quality-check": "PC Quality Check",
      "#/research-validation": "Research & Validation",
      "#/settings": "Settings",
    }[normalizedHash],
  });
  await waitFor(() => {
    expect(screen.queryByText("Connecting to the SmartOps API on this computer...")).not.toBeInTheDocument();
  });
}

beforeEach(() => {
  scenario = {};
  notificationEnabled = true;
  window.localStorage.clear();
  vi.stubGlobal("fetch", vi.fn(responseFor));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("deployment-ready SmartOps dashboard", () => {
  it("uses Overview as the default route and renders automatic data", async () => {
    await renderAt("");
    expect(window.location.hash).toBe("#/overview");
    expect(screen.getByText("386 records")).toBeVisible();
    expect(screen.getByRole("heading", { name: "SmartOps System Overview" })).toBeVisible();
    expect(screen.queryByText(/Phase 7B|Phase 7C/i)).not.toBeInTheDocument();
    expect(document.title).toBe("SmartOps");
  });

  it("renders each genuine pipeline icon inside its unified status ring", async () => {
    scenario.pipelineState = "processing";
    await renderAt("#/overview");
    const collection = screen.getByText("System Data Collection").closest("li")!;
    expect(collection).toHaveClass("overview-pipeline-stage--processing");
    const ring = within(collection).getByRole("button", {
      name: /System Data Collection: Processing/i,
    });
    expect(ring).toHaveClass("overview-pipeline-ring");
    expect(ring.querySelector("svg.overview-pipeline-ring__icon")).toBeTruthy();
    expect(collection.querySelector(".pipeline-stage__icon")).toBeNull();
    expect(screen.getAllByText("Personal Baseline")[0].closest("li"))
      .toHaveClass("overview-pipeline-stage--success");
  });

  it("renders a neutral not-evaluated health gauge without fabricating zero", async () => {
    scenario.health = null;
    await renderAt("#/overview");
    const gauge = screen.getByRole("link", {
      name: /System Health Score not evaluated/i,
    });
    expect(gauge).toHaveClass("overview-health-gauge--empty");
    expect(within(gauge).getByText("Not evaluated")).toBeVisible();
    expect(within(gauge).queryByText("0")).not.toBeInTheDocument();
  });

  it("renders genuine KPI, chart and activity states", async () => {
    scenario.health = healthAssessment;
    scenario.quality = qualityAssessment;
    scenario.alerts = [sampleAlert];
    await renderAt("#/overview");
    expect(screen.getByRole("link", { name: /System Health Score 88 out of 100/i })).toBeVisible();
    expect(screen.getByRole("img", { name: /1 active alerts.*Elevated 1/i })).toBeVisible();
    expect(screen.getByText("Alert observed: Sustained CPU pressure")).toBeVisible();
    expect(screen.getByText("View all 30 workload profiles")).toBeVisible();
  });

  it("shows an honest neutral alert-distribution state when no alert exists", async () => {
    await renderAt("#/overview");
    expect(screen.getByText("No active alerts")).toBeVisible();
    expect(screen.queryByRole("img", { name: /active alerts:/i })).not.toBeInTheDocument();
  });

  it("preserves bounded health ranges and existing expand and zoom controls", async () => {
    scenario.health = healthAssessment;
    await renderAt("#/overview");
    const oneHour = screen.getByRole("button", { name: "Last hour" });
    await userEvent.click(oneHour);
    expect(oneHour).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "Expand" }));
    expect(screen.getByRole("dialog", { name: /Current operating condition expanded chart/i })).toBeVisible();
    expect(screen.getByRole("button", { name: "Reset Zoom" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Return to Live" })).toBeVisible();
  });

  it("navigates with hashes and supports browser history", async () => {
    await renderAt("#/overview");
    await userEvent.click(screen.getByRole("link", { name: "Live Monitoring" }));
    await screen.findByRole("heading", { name: "Live Monitoring" });
    expect(window.location.hash).toBe("#/live-monitoring");
    act(() => window.history.back());
    await waitFor(() => expect(window.location.hash).toBe("#/overview"));
    expect(screen.getByRole("heading", { name: "SmartOps overview" })).toBeVisible();
  });

  it("renders live values and preserves unavailable optional sensors", async () => {
    await renderAt("#/live-monitoring");
    expect(screen.getAllByText("42.5%").length).toBeGreaterThan(0);
    expect(screen.getByText("CPU Temperature Unavailable")).toBeVisible();
    expect(screen.getByText("GPU Metrics Unavailable")).toBeVisible();
  });

  it("separates user activity, system activity and window-majority context", async () => {
    await renderAt("#/live-monitoring");
    expect(screen.getAllByText("User activity").length).toBeGreaterThan(0);
    expect(screen.getAllByText("background").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: /Open complete process and raw telemetry records/ })).toBeVisible();
    expect(screen.queryByText(/Development was observed in 4 of 10 samples/i)).not.toBeVisible();
  });

  it("shows capability-aware advanced signals without development branding", async () => {
    await renderAt("#/live-monitoring");
    expect(
      screen.getByRole("heading", { name: "Advanced System Signals" }),
    ).toBeVisible();
    expect(screen.getByText("Experimental signal — does not affect results")).toBeVisible();
    expect(screen.getByText("Processor queue length")).toBeVisible();
    expect(screen.getByText("Storage latency")).toBeVisible();
    expect(screen.getAllByText("Unavailable").length).toBeGreaterThan(0);
    expect(
      screen.getByText(/do not change risk, health, root-cause ranking/i),
    ).toBeVisible();
  });

  it("shows the genuine no-alert state and upstream readiness", async () => {
    await renderAt("#/predictive-alerts");
    expect(
      screen.getByText("No active alert is currently supported by evaluated evidence."),
    ).toBeVisible();
    expect(screen.getAllByText("not evaluated").length).toBeGreaterThan(0);
  });

  it("renders alert details and keeps acknowledgement semantics explicit", async () => {
    scenario.alerts = [sampleAlert];
    await renderAt("#/predictive-alerts");
    expect(screen.getByText("Sustained CPU pressure")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "View details" }));
    await screen.findByText("What SmartOps detected");
    expect(screen.getAllByText(/CPU pressure persisted/).length).toBeGreaterThan(0);
    expect(screen.getByText("Method validation").nextElementSibling)
      .toHaveTextContent("Not yet validated");
    const button = screen.getByRole("button", { name: "I have seen this alert" });
    expect(button).toHaveAttribute("title", expect.stringContaining("does not resolve"));
    await userEvent.click(button);
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("/api/alerts/7/acknowledge"),
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("offers explicit detail controls for current and resolved summary alerts", async () => {
    const resolvedAlert = {
      ...sampleAlert,
      id: 18,
      alert_code: "SO-MEM-01",
      title: "Memory and page-file pressure",
      category: "memory_pressure",
      state: "resolved",
      resolved_at_utc: timestamp,
    };
    scenario.alerts = [sampleAlert, resolvedAlert];
    scenario.alertHistoryUsesSummaries = true;
    await renderAt("#/predictive-alerts");

    expect(screen.getAllByRole("button", { name: "View details" })).toHaveLength(2);
    const resolvedRow = screen.getByRole("row", { name: /Memory and page-file pressure/i });
    const control = within(resolvedRow).getByRole("button", { name: "View details" });
    expect(control).toHaveAttribute("aria-expanded", "false");
    expect(control).toHaveAttribute("aria-controls", "alert-details-18");

    await userEvent.click(control);
    const details = await screen.findByRole("region", {
      name: "Full details for Memory and page-file pressure",
    });
    expect(details).toBeVisible();
    expect(within(details).getByText("What SmartOps detected")).toBeVisible();
    expect(within(details).getByText("Not yet validated")).toBeVisible();
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/alerts/18"),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(control).toHaveAttribute("aria-expanded", "true");
    expect(within(resolvedRow).getByRole("button", { name: "Hide details" })).toBeVisible();

    await userEvent.click(within(resolvedRow).getByRole("button", { name: "Hide details" }));
    expect(screen.queryByRole("region", {
      name: "Full details for Memory and page-file pressure",
    })).not.toBeInTheDocument();
    expect(control).toHaveAttribute("aria-expanded", "false");
  });

  it("shows loading, friendly failure and retry states for the local detail endpoint", async () => {
    const resolvedAlert = {
      ...sampleAlert,
      id: 19,
      title: "Memory and page-file pressure",
      state: "resolved",
      resolved_at_utc: timestamp,
    };
    scenario.alerts = [resolvedAlert];
    scenario.alertHistoryUsesSummaries = true;
    scenario.alertDetailDelayMs = 2_000;
    await renderAt("#/predictive-alerts");
    await userEvent.click(screen.getByRole("button", { name: "View details" }));
    expect(screen.getByText(/Loading full alert details from the local database/)).toBeVisible();
    await screen.findByText("What SmartOps detected", {}, { timeout: 4_000 });

    await userEvent.click(screen.getByRole("button", { name: "Hide details" }));
    scenario.alertDetailDelayMs = 0;
    scenario.alertDetailFailuresRemaining = 1;
    await userEvent.click(screen.getByRole("button", { name: "View details" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Alert details are temporarily unavailable",
    );
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("region", { name: /Full details for Memory/i })).toBeVisible();
  });

  it("supports keyboard expansion, keeps filters, and permits only one expanded alert", async () => {
    const resolvedAlert = {
      ...sampleAlert,
      id: 20,
      title: "Historical memory pressure",
      state: "resolved",
      resolved_at_utc: timestamp,
    };
    scenario.alerts = [sampleAlert, resolvedAlert];
    scenario.alertHistoryUsesSummaries = true;
    await renderAt("#/predictive-alerts");
    const search = screen.getByRole("searchbox", { name: "Search stored alerts" });
    await userEvent.type(search, "pressure");
    const currentControl = screen.getAllByRole("button", { name: "View details" })[0];
    currentControl.focus();
    await userEvent.keyboard("{Enter}");
    await screen.findByRole("region", { name: /Full details for Sustained CPU pressure/i });
    expect(currentControl).toHaveAttribute("aria-expanded", "true");
    expect(search).toHaveValue("pressure");

    const resolvedControl = screen.getAllByRole("button", { name: "View details" })[0];
    resolvedControl.focus();
    await userEvent.keyboard(" ");
    await screen.findByRole("region", { name: /Full details for Historical memory pressure/i });
    expect(screen.queryByRole("region", { name: /Full details for Sustained CPU pressure/i })).not.toBeInTheDocument();
    expect(search).toHaveValue("pressure");
  });

  it("searches, filters and resets alerts using stored safe fields", async () => {
    scenario.alerts = [sampleAlert];
    await renderAt("#/predictive-alerts");
    const search = screen.getByRole("searchbox", { name: "Search stored alerts" });
    fireEvent.change(search, { target: { value: "browser history" } });
    expect(screen.queryByText("Sustained CPU pressure")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Reset filters" }));
    expect(search).toHaveValue("");
    expect(screen.getByText("Sustained CPU pressure")).toBeVisible();
    expect(screen.getByRole("combobox", { name: "Validation" })).toBeVisible();
    expect(screen.getByRole("combobox", { name: "Sort" })).toBeVisible();
  });

  it("keeps confidence distinct from accuracy and shows actual lifecycle history", async () => {
    scenario.alerts = [{
      ...sampleAlert,
      alert_confidence: 82,
      confidence_label: "high",
      validation: {
        validation_type: "not_yet_validated",
        display_label: "Not yet validated",
        sample_size: 0,
        accuracy: null,
        precision: null,
        recall: null,
        specificity: null,
        f1_score: null,
        false_positive_rate: null,
        limitations: ["Confirmed outcomes are not yet sufficient."],
      },
      transitions: [{ id: 1, transition_timestamp_utc: timestamp, previous_state: null, new_state: "open", previous_severity: null, new_severity: "advisory", transition_type: "activated", reason_code: "genuine_activation" }],
      occurrences: [{ id: 1, observed_at_utc: timestamp, severity: "advisory", condition_met: 1, temporal_pattern: "persistent" }],
      notification_deliveries: [],
    }];
    await renderAt("#/predictive-alerts");
    expect(screen.getAllByText(/82%.*high/i).length).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("button", { name: "View details" }));
    await screen.findByText("What SmartOps detected");
    expect(screen.getAllByText("Not yet validated").length).toBeGreaterThan(0);
    expect(screen.getByText(/Evidence strength describes completeness and consistency/i)).toBeVisible();
    expect(screen.getByRole("link", { name: "View exact Technical Evidence" })).toHaveAttribute("href", "#/technical-evidence?dataset=alerts&contextId=7");
    expect(screen.queryByText("Notification delivered")).not.toBeInTheDocument();
  });

  it("presents registered validation measures with their evidence context", async () => {
    const registeredValidation = {
      validation_type: "real_world_labelled",
      display_label: "Real-world labelled validation",
      sample_size: 40,
      accuracy: .8,
      precision: .8,
      recall: .667,
      specificity: .9,
      f1_score: .727,
      false_positive_rate: .1,
      dataset_description: "Local labelled observation set A",
      validation_date_utc: timestamp,
      limitations: ["Limited to the recorded local observation set."],
    };
    scenario.alerts = [{
      ...sampleAlert,
      validation: registeredValidation,
      explanation_snapshot: {
        baseline_version: 2,
        evidence_start_utc: "2026-07-26T09:55:00Z",
        evidence_end_utc: timestamp,
        source_sample_count: 10,
        evidence_completeness: 1,
        triggering_rule_identifier: "stored-rule",
        triggering_rule_version: "stored-version",
        triggering_metrics: ["cpu_avg"],
        observed_values: { cpu_avg: 92 },
        baseline_values: { cpu_avg: 35 },
        thresholds: { cpu_avg: 90 },
        deviations: [], top_contributors: [], missing_evidence: [],
        plain_language_explanation: "CPU pressure persisted across completed windows.",
        explanation_version: "stored-explanation",
        alert_confidence: 82,
        confidence_label: "high",
        confidence_calculation_version: "stored-confidence",
        confidence_components: [],
        validation: registeredValidation,
      },
    }];
    await renderAt("#/predictive-alerts");
    await userEvent.click(screen.getByRole("button", { name: "View details" }));
    await screen.findByText("What SmartOps detected");
    expect(screen.getAllByText("Real-world labelled validation").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: "View exact Technical Evidence" })).toBeVisible();
    expect(screen.queryByText("Local labelled observation set A")).not.toBeInTheDocument();
  });

  it("opens the exact alert from a valid notification deep link", async () => {
    scenario.alerts = [sampleAlert];
    await renderAt("#/predictive-alerts?alertId=7");
    expect(
      await screen.findByText("Opened alert 7 from the Windows notification."),
    ).toBeVisible();
    expect(screen.getByText("What happened after this alert?")).toBeVisible();
    expect(screen.getByRole("link", { name: "Open Root-Cause Analysis" })).toHaveAttribute("href", "#/root-cause-analysis?alertId=7");
    expect(document.getElementById("alert-7")).toHaveClass("alert-card--selected");
  });

  it("handles missing and invalid notification alert identifiers safely", async () => {
    await renderAt("#/predictive-alerts?alertId=999");
    expect(await screen.findByText(/Alert 999 was not found/)).toBeVisible();
    cleanup();
    await renderAt("#/predictive-alerts?alertId=1%26route%3Devil");
    expect(
      await screen.findByText(
        "The notification link contains an invalid alert identifier.",
      ),
    ).toBeVisible();
  });

  it("keeps Predictive Alerts focused on alert operations", async () => {
    await renderAt("#/predictive-alerts");
    expect(
      screen.queryByRole("switch", {
        name: "Enable native Windows notifications",
      }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Send Test Notification" }),
    ).not.toBeInTheDocument();
  });

  it("centralizes durable notification preferences and explicit testing", async () => {
    await renderAt("#/settings");
    const toggle = screen.getByRole("switch", {
      name: "Enable native Windows notifications",
    });
    expect(toggle).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Advisory/ })).toBeVisible();
    expect(screen.getByRole("checkbox", { name: /Warning/ })).toBeVisible();
    expect(screen.getByRole("checkbox", { name: /Urgent/ })).toBeVisible();
    await userEvent.click(
      screen.getByRole("button", { name: "Send Test Notification" }),
    );
    expect(
      await screen.findByText(
        "Test notification submitted to Windows. No predictive-alert or delivery-history record was created.",
      ),
    ).toBeVisible();
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/notifications/test"),
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("shows a healthy personal baseline and optional confirmed recalibration", async () => {
    await renderAt("#/settings");
    expect(
      screen.getByRole("heading", { name: "Personal Baseline" }),
    ).toBeVisible();
    expect(screen.getByText(/Your personal baseline is healthy/)).toBeVisible();
    const start = screen.getByRole("button", { name: "Start New Calibration" });
    expect(screen.getAllByText("Version 2").length).toBeGreaterThan(0);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    await userEvent.click(start);
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("existing baseline remains active"));
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("/baseline-management/start"))).toBe(false);
    confirm.mockReturnValue(true);
    await userEvent.click(start);
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/baseline-management/start"),
      expect.objectContaining({ method: "POST" }),
    ));
    expect(screen.getAllByText("No calibration is currently running").length)
      .toBeGreaterThan(0);
  });

  it("shows guided development progress during a new calibration", async () => {
    scenario.candidate = true;
    await renderAt("#/settings");
    const row = screen.getByText("guided development").closest("tr");
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getByText("observed insufficient")).toBeVisible();
    expect(within(row as HTMLElement).getByText("8")).toBeVisible();
    expect(within(row as HTMLElement).getByText("7")).toBeVisible();
    expect(
      within(row as HTMLElement).getByText("7 accepted / 1 removed / 2 reaccepted"),
    ).toBeVisible();
    expect(within(row as HTMLElement).getByText("Collecting data")).toBeVisible();
    expect(within(row as HTMLElement).getByText("Eligible-window count below minimum")).toBeVisible();
    expect(screen.getByRole("button", { name: "Continue Calibration" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Pause Calibration" })).not.toBeInTheDocument();
  });

  it("continues a ready candidate through the dedicated action", async () => {
    scenario.candidate = true;
    await renderAt("#/settings");
    await userEvent.click(screen.getByRole("button", { name: "Continue Calibration" }));
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/baseline-management/continue"),
      expect.objectContaining({ method: "POST" }),
    );
    expect(await screen.findByText("Calibration is continuing. It has not replaced your active baseline.")).toBeVisible();
  });

  it("keeps temporary enhanced failures visible and hides permanent unsupported signals", async () => {
    await renderAt("#/live-monitoring");
    expect(screen.getByText("Only evidence supported by this device is displayed.")).toBeVisible();
    expect(screen.getByText("Storage latency")).toBeVisible();
    expect(screen.queryByText("Storage wear used")).not.toBeInTheDocument();
  });

  it("provides consistent expandable controls for all five live graphs", async () => {
    await renderAt("#/live-monitoring");
    expect(screen.getAllByRole("button", { name: "Expand" })).toHaveLength(5);
    await userEvent.click(screen.getAllByRole("button", { name: "Expand" })[0]);
    expect(screen.getByRole("dialog", { name: "CPU utilization expanded chart" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Return to Live" })).toBeVisible();
  });

  it("shows exact enable and disable confirmation messages", async () => {
    await renderAt("#/settings");
    const toggle = screen.getByRole("switch", {
      name: "Enable native Windows notifications",
    });
    await userEvent.click(toggle);
    expect(
      await screen.findByText("Notifications have been disabled."),
    ).toBeVisible();
    await userEvent.click(toggle);
    expect(
      await screen.findByText("Notifications have been enabled."),
    ).toBeVisible();
    expect(screen.queryByText("Test notification sent")).not.toBeInTheDocument();
  });

  it("loads Settings through hash navigation and browser history", async () => {
    await renderAt("#/overview");
    await userEvent.click(screen.getByRole("link", { name: "Settings" }));
    await screen.findByRole("heading", { name: "Settings" });
    expect(window.location.hash).toBe("#/settings");
    act(() => window.history.back());
    await waitFor(() => expect(window.location.hash).toBe("#/overview"));
    act(() => window.history.forward());
    await waitFor(() => expect(window.location.hash).toBe("#/settings"));
  });

  it("persists validated browser display preferences", async () => {
    await renderAt("#/settings");
    await userEvent.selectOptions(
      screen.getByLabelText("Default landing page"),
      "system-health",
    );
    await userEvent.click(
      screen.getByRole("switch", { name: "Remember last opened page" }),
    );
    await userEvent.click(
      screen.getByRole("radio", { name: "24-hour" }),
    );
    await userEvent.click(
      screen.getByRole("switch", { name: "Use relative timestamps" }),
    );
    const stored = JSON.parse(
      window.localStorage.getItem(
        "smartops.dashboard.preferences.v1",
      ) ?? "{}",
    ) as Record<string, unknown>;
    expect(stored).toMatchObject({
      defaultLandingPage: "system-health",
      rememberLastPage: true,
      timeDisplay: "24-hour",
      relativeTimestamps: true,
      automaticRefresh: true,
    });
  });

  it("disables browser polling without stopping manual refresh", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      await renderAt("#/settings");
      await userEvent.click(
        screen.getByRole("switch", {
          name: "Enable automatic dashboard refresh",
        }),
      );
      const before = vi.mocked(fetch).mock.calls.filter(
        ([url]) => new URL(String(url)).pathname === "/api/status",
      ).length;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000);
      });
      const after = vi.mocked(fetch).mock.calls.filter(
        ([url]) => new URL(String(url)).pathname === "/api/status",
      ).length;
      expect(after).toBe(before);
      await userEvent.click(screen.getByRole("button", { name: "Refresh Now" }));
      await waitFor(() => {
        const current = vi.mocked(fetch).mock.calls.filter(
          ([url]) => new URL(String(url)).pathname === "/api/status",
        ).length;
        expect(current).toBeGreaterThan(after);
      });
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows an honest root-cause empty state", async () => {
    await renderAt("#/root-cause-analysis");
    expect(
      screen.getByText("No matching evaluated investigation"),
    ).toBeVisible();
    expect(screen.getByText(/not confirmed hardware diagnoses or proof of causality/i)).toBeVisible();
    expect(
      screen.queryByRole("heading", { name: "Advanced System Signals" }),
    ).not.toBeInTheDocument();
    const urls = vi.mocked(fetch).mock.calls.map(([url]) => String(url));
    expect(
      urls.some((url) => url.includes("/api/enhanced-evidence/status")),
    ).toBe(false);
  });

  it("does not substitute unrelated live evidence when a selected alert has no eligible RCA record", async () => {
    scenario.alerts = [sampleAlert];
    await renderAt("#/root-cause-analysis?alertId=7");
    expect(screen.getByText("No matching evaluated investigation")).toBeVisible();
    expect(screen.getByText(/does not invent a cause/i)).toBeVisible();
    expect(
      screen.queryByRole("heading", { name: "Advanced System Signals" }),
    ).not.toBeInTheDocument();
  });

  it("renders an evaluated health score without calling it a probability", async () => {
    scenario.health = healthAssessment;
    await renderAt("#/system-health");
    expect(screen.getByText("88")).toBeVisible();
    expect(screen.getByText(/System health is not a failure probability/)).toBeVisible();
  });

  it("renders the quality result and previous assessment history", async () => {
    scenario.quality = qualityAssessment;
    await renderAt("#/pc-quality-check");
    expect(screen.getAllByText("82").length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: "All catalogued profiles" })).toBeVisible();
    const selector = screen.getByRole("combobox", { name: "PC Quality profile" });
    expect(within(selector).getAllByRole("option")).toHaveLength(30);
    expect(screen.getByText(/30 catalogued profiles/)).toBeVisible();
    expect(screen.getByText("Device Current Workload Headroom")).toBeVisible();
    expect(screen.getAllByText("Word processing").length).toBeGreaterThan(0);
    await userEvent.selectOptions(selector, "online_learning_research");
    expect(screen.getByText("Not observed — this workload has not been detected on this device.")).toBeVisible();
    expect(screen.getAllByText("Not independently detectable").length).toBeGreaterThan(0);
    await userEvent.selectOptions(selector, "compression_backup_large_transfers");
    expect(screen.getByText("Not evaluated — insufficient evidence is currently available.")).toBeVisible();
    await userEvent.selectOptions(selector, "video_conferencing_online_classes");
    expect(screen.getAllByText("Separate score for Video conferencing and online classes.").length)
      .toBeGreaterThan(0);
    expect(screen.getAllByText("99.9").length).toBeGreaterThan(0);
    expect(screen.getAllByText("25.0%").length).toBeGreaterThan(0);
    expect(selector).toHaveValue("video_conferencing_online_classes");
    expect(screen.getByRole("heading", { name: "Hardware Workload Suitability" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Previous quality-check history" })).toBeVisible();
    expect(screen.getAllByText("Software Development").length).toBeGreaterThan(0);
  });

  it("never substitutes legacy hardware suitability for Device headroom", async () => {
    scenario.quality = qualityAssessment;
    scenario.fineQualityUnavailable = true;
    await renderAt("#/overview");
    const card = screen.getByText("Current Workload Headroom", {
      selector: ".overview-kpi__label",
    }).closest("a");
    expect(card).toHaveTextContent("Not evaluated");
    expect(card).not.toHaveTextContent("82");
    expect(screen.getByRole("link", { name: /Hardware Workload Suitability/i })).toBeVisible();
  });

  it("opens a searched PC Quality profile by its stable identifier", async () => {
    scenario.quality = qualityAssessment;
    await renderAt("#/pc-quality-check?profile=video_editing_rendering");
    expect(screen.getByRole("combobox", { name: "PC Quality profile" })).toHaveValue("video_editing_rendering");
    expect(screen.getAllByText("Video editing and rendering").length).toBeGreaterThan(0);
  });

  it("keeps validation optional and its manual forms collapsed", async () => {
    await renderAt("#/research-validation");
    expect(screen.getByText("Optional academic validation area.")).toBeVisible();
    const methods = screen.getByText("Research support, formulas, and versioned methods");
    fireEvent.click(methods);
    expect(screen.getByText(/Alert Confidence = 25% evidence completeness/)).toBeVisible();
    expect(screen.getByText(/No method-level labelled validation record is stored/)).toBeVisible();
    const disclosure = screen.getByText("Optional incident, feedback, and observation forms");
    expect(disclosure).toBeVisible();
    expect(screen.queryByRole("button", { name: "Review and save incident" })).not.toBeVisible();
    fireEvent.click(disclosure);
    expect(screen.getByRole("button", { name: "Review and save incident" })).toBeVisible();
    expect(screen.getAllByText("Not evaluated").length).toBeGreaterThan(0);
  });

  it("shows an API error with a retry control", async () => {
    scenario.fail = true;
    window.history.replaceState(null, "", "#/overview");
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent("SmartOps API is not reachable");
    expect(screen.getByRole("button", { name: "Try again" })).toBeVisible();
  });

  it("shows a loading state while the local API is pending", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => undefined)));
    window.history.replaceState(null, "", "#/overview");
    render(<App />);
    expect(screen.getByRole("heading", { name: "Loading local history" })).toBeVisible();
  });

  it("moves complete raw-history pagination to Technical Evidence", async () => {
    scenario.historyTotal = 50;
    await renderAt("#/live-monitoring");
    expect(screen.getByRole("link", { name: /Open complete process and raw telemetry records/ })).toHaveAttribute("href", "#/technical-evidence?dataset=monitoring");
    expect(screen.queryByRole("button", { name: "Next" })).not.toBeInTheDocument();
  });

  it("refreshes through the single visible control", async () => {
    await renderAt("#/overview");
    const before = vi.mocked(fetch).mock.calls.length;
    await userEvent.click(screen.getByRole("button", { name: "Refresh data" }));
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.length).toBeGreaterThan(before));
  });

  it("uses one coordinated 30-second automatic refresh", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      await renderAt("#/overview");
      const statusCallsBefore = vi.mocked(fetch).mock.calls.filter(
        ([url]) => new URL(String(url)).pathname === "/api/status",
      ).length;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000);
      });
      const statusCallsAfter = vi.mocked(fetch).mock.calls.filter(
        ([url]) => new URL(String(url)).pathname === "/api/status",
      ).length;
      expect(statusCallsAfter).toBe(statusCallsBefore + 1);
    } finally {
      vi.useRealTimers();
    }
  });
});
