# Root-Cause Analysis and System Health data map

The Root-Cause Analysis and System Health screens are read-only presentations
of evidence already evaluated and stored by SmartOps. Neither screen runs an
analysis, changes a score, or writes a database record.

## Root-Cause Analysis

| Presentation | Local API | Stored source | Time field | Unit or scale | Missing-data behaviour | Refresh |
|---|---|---|---|---|---|---|
| Investigation list | `GET /api/risk/history` | `risk_assessments`, `risk_component_contributions`, `root_cause_candidates` | `evaluated_at_utc` and feature-window start/end | Risk Evidence Index 0–100; stored evidence level | No row is invented for a window that was not evaluated | Coordinated browser refresh (30 seconds by default) |
| Selected risk detail | `GET /api/risk/{window_id}` | The same risk tables plus normalized stored component evidence | `evaluated_at_utc` | Stored contributions and reconstruction values | A missing assessment is shown as unavailable | Only when the user opens an investigation |
| Ranked contributor detail | `GET /api/root-causes/{window_id}` | `root_cause_candidates` and normalized candidate-evidence rows | Candidate `first_observed_utc` | Stored evidence confidence 0–1, displayed as percent; never described as failure probability | An empty candidate list remains an honest empty state | Only when the user opens an investigation |
| Related alert evidence | `GET /api/alerts/history?summary=true` for bounded summaries and `GET /api/alerts/{id}` for detail | Alert lifecycle, evidence, explanation, transition, occurrence and notification tables | `latest_observed_utc`, occurrence/transition timestamps | Stored severity/category/state and evidence-confidence fields | An absent related alert does not prevent the risk investigation and is not fabricated | Summary on coordinated refresh; detail on demand |
| Correlated event timeline | Embedded in the stored risk-component evidence | Versioned references to safe, mapped `windows_events` metadata | `event_timestamp_utc` | Event level/category/timing | No timeline point is inferred for a missing timestamp | On selected detail request |
| Baseline context | `GET /api/baseline/status` | Active baseline/profile tables | Last successful training time | Readiness state and eligible counts | Unavailable baseline context is labelled unavailable | Coordinated browser refresh |

Filters operate on at most 100 risk assessments returned by the bounded history
request. Opening a row starts an independent, abortable detail request, so the
periodic page refresh cannot leave a detail panel half loaded. Cross-page links
use the immutable alert ID or feature-window ID.

## System Health

| Presentation | Local API | Stored source | Time field | Unit or scale | Missing-data behaviour | Refresh |
|---|---|---|---|---|---|---|
| Current summary and gauge | `GET /api/health/latest` | `health_assessments` | `assessed_at_utc` | System Health Score 0–100 when evaluated | `not_evaluated` is a neutral incomplete gauge; never zero | Coordinated browser refresh |
| Component breakdown | Embedded in the latest/full health record | `health_component_scores` | Parent assessment time | Component score 0–100 and stored effective weight | Only stored applicable components are shown | Coordinated browser refresh |
| Deductions and observed evidence | Embedded in the latest/full health record | `health_deductions` | Parent assessment/window time | Raw/effective deduction and original metric units in `supporting_value` | Null stays unavailable; the frontend performs no scoring | Coordinated browser refresh |
| Excluded inputs | Embedded in the latest/full health record | `health_input_availability` | Parent assessment time | Availability classification and applicable weight | Optional sensor absence is disclosed and is never converted to zero | Coordinated browser refresh |
| Trend, gaps and distribution | `GET /api/health/history` | `health_assessments` | `assessed_at_utc` | Score 0–100 and stored health band | Not-evaluated records form gaps and are counted separately | Coordinated browser refresh |
| Linked historical record | `GET /api/health/{window_id}` | Full normalized health assessment | `assessed_at_utc` | Same as current health | Missing linked windows show a limitation and keep the latest record visible | On cross-page navigation when outside the loaded range |
| Guidance and limitations | Embedded in the full health record | `health_guidance` | Parent assessment time | Text generated and stored by the analytics rule version | No recommendation is invented by the frontend | Coordinated browser refresh |

The history API is capped at 5,000 records. The chart utility further bounds
rendering to 1,200 points while retaining endpoints and bucket extrema. Health
bands, component values, weights, deductions, confidence, and reconstruction
are all backend results; the frontend only formats them.

## Interpretation boundary

- Root-cause contributors are ranked hypotheses supported by available stored
  evidence. They are not proven causes or confirmed hardware diagnoses.
- System Health describes the current observed operating condition. It is not a
  failure probability, future-reliability guarantee, or PC quality rating.
- Phase 7B enhanced evidence remains shadow-only and is not introduced into
  either page's scoring or ranking.
- No browsing history, URLs, file content, typed text, or window titles are
  queried or displayed.
