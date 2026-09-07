# SmartOps UI Redesign Stage 2 — Overview data audit

Date: 2026-09-03

This audit was recorded before the Stage 2 Overview implementation. The
external visual reference could not be opened from the development environment,
so the owner's written requirements are the authoritative specification. No
reference branding, content, assets, source code, security terminology, or
layout is used.

## Production safety state

- Production schema is 18.
- Personal baseline v2 is active and inactive for learning; v1 is archived and
  remains available for rollback.
- There is no candidate baseline and no calibration is collecting.
- Ports 8000 and 5173 were free before the current Overview was captured at
  1366×768 and 1920×1080.
- Stage 2 is frontend-led. It adds one opt-in read-only health-history summary
  response for bounded chart payloads, but no database writes, migration, or
  analytical calculation.

## Read-only data mapping

All Overview requests remain inside the existing route-scoped, abortable,
coordinated 30-second refresh. Missing values stay null and are rendered as
Unavailable or Not evaluated; no chart substitutes zero.

| Overview element | Read-only API | Stored source / evidence | Timestamp | Unit | Missing-data behavior | Meaning |
| --- | --- | --- | --- | --- | --- | --- |
| Connection and freshness | `/api/status`, `/api/runtime/status`, `/api/metrics/latest` | Database connection, runtime ownership/heartbeat, newest `metrics` row | heartbeat / `timestamp_utc` | age | Offline or stale text; never simulated live | Current |
| System Health hero/KPI | `/api/health/latest`, `/api/health/status` | `health_assessments` and normalized health evidence | `assessed_at_utc` | 0–100 score | Neutral incomplete gauge and `Not evaluated` | Current derived assessment |
| System Health trend | `/api/health/history?limit=288&summary=true` | `health_assessments` | `assessed_at_utc` | 0–100 score | Null assessments split the line into honest gaps | Historical, bounded to 24 hours; summary mode omits full normalized detail used only by the dedicated page |
| Risk Evidence KPI | `/api/risk/latest`, `/api/risk/status` | `risk_assessments` and stored component contributions | `evaluated_at_utc` | 0–100 index | `Not evaluated`; never presented as probability | Current derived assessment |
| Predictive Alerts KPI/distribution | `/api/alerts/status`, `/api/alerts/history?limit=8` | Durable alert lifecycle and `alert_occurrences` | `latest_observed_utc` | count | Neutral zero-alert state; unknown categories are not invented | Current distribution and recent history |
| Current workload KPI | `/api/workload/latest` | Newest stored raw workload classification | `timestamp_utc` | class and confidence | Unavailable when not classified | Current |
| Workload activity heatmap | `/api/features/history?limit=288` | Finalised `feature_windows` workload provenance | `window_start_utc` / `window_end_utc` | observed 5-minute window | Missing periods remain empty cells | Historical, bounded to 24 hours |
| Data Freshness KPI | `/api/metrics/latest`, `/api/metrics/count`, `/api/runtime/status` | `metrics`, runtime heartbeat | `timestamp_utc` / heartbeat | age and records | Stale/offline stated explicitly | Current |
| Resource utilisation | `/api/metrics/latest` | Newest `metrics` row | `timestamp_utc` | CPU/RAM/disk %, network B/s | Each unavailable metric is labelled separately | Current raw telemetry |
| PC Quality KPI/comparison | `/api/quality/latest`, `/api/quality/status`, `/api/quality/profile-scores` | Schema-18 v2-anchored profile assessments and inventory evidence | `assessed_at_utc` | 0–100 suitability/profile score | Not observed/evaluated profiles are excluded, not plotted as zero | Current stored assessments |
| Health component comparison | `/api/health/latest` | Normalized `health_component_scores` returned with the assessment | `assessed_at_utc` | 0–100 component score | Only returned components are plotted | Current derived breakdown |
| Pipeline status | `/api/pipeline/status` | `pipeline_stage_status` plus genuine latest telemetry/feature/risk/health evidence | generated, attempt, success timestamps | state, duration, interval | Grey Not yet run/not applicable; no synthetic processing | Current derived status |
| Recent activity | Existing pipeline response plus alert history and newest metric | Genuine pipeline stage success timestamps, alert lifecycle timestamps, `metrics` | source timestamps above | event list | Honest empty state when no suitable events exist | Frontend projection of stored/runtime evidence |

## Chart and performance decisions

- No chart dependency is added. The existing locally bundled SVG interaction
  component supplies health-trend expansion, pan, wheel/pinch zoom, selection,
  reset, fit-all, return-to-live, crosshair, and exact tooltip behavior.
- Overview history is bounded to 288 five-minute health/workload records (24
  hours) and 120 raw records (one hour). It does not request unbounded history.
- The alert donut uses only canonical severity counts returned by the API.
- Resource percentages share a labelled 0–100 utilization scale. Network rates
  are shown separately in MB/s and are not placed on that axis.
- PC Quality comparison selects a small deterministic set from evaluated
  profiles: the device/current context plus highest and lowest genuine scores.
- The workload grid represents stored windows only. Empty slots mean no stored
  window and are never inferred as inactivity.
- Recent activity is a read-only presentation assembled from already-fetched
  responses, so it creates no records and triggers no additional request.
