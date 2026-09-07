# SmartOps deployment UI audit

This audit records the evidence and layout contracts used by the Live Monitoring
and Predictive Alerts redesign. The interface continues to use stored local data;
it does not create demonstration values.

## Full-page capture diagnosis

The root document was fixed to one viewport while `.app-content` owned a nested
vertical scrollbar. Specifically, `body` and `.app-shell` used hidden overflow,
`.app-shell` used `height: 100vh`, and `.app-content` used `overflow-y: auto`.
Edge full-page capture correctly captured the root document, but the root was only
768 or 1080 pixels high. The Live Monitoring content inside it was more than
4,200 pixels high and was therefore clipped from the full-page image.

The correction makes the root document the only main vertical scroller. The top
bar and desktop sidebar are sticky, the application shell and content use
`min-height` with normal flow, and only the bounded sidebar may scroll internally.

## Live Monitoring data map

| Presentation | Read-only API | Stored source | Timestamp / unit | Missing-data behaviour | Refresh |
| --- | --- | --- | --- | --- | --- |
| Current resources and freshness | `/api/metrics/latest` | `metrics` | `timestamp_utc`; %, bytes, bytes/s, seconds | Null is Unavailable or Not applicable; never zero-filled | Coordinated 30 seconds / manual |
| Stored sample count | `/api/metrics/count` | `metrics` | Current count | Honest zero when table has no rows | Coordinated 30 seconds / manual |
| CPU, RAM, disk and network trends | `/api/metrics/history` | `metrics` | `timestamp_utc`; %, bytes/s | Null readings remain gaps; zero and one valid point have explicit states | Coordinated 30 seconds / manual |
| Current workload | `/api/workload/latest` | Latest classified `metrics` row | metric timestamp; class and evidence confidence | Unavailable when no classified sample exists | Coordinated 30 seconds / manual |
| Five-minute analysis | `/api/features/history` | `feature_windows` | UTC window boundaries; coverage | Incomplete and missing indicators stay explicit | Coordinated 30 seconds / manual |
| Process snapshots | `/api/processes/latest` | `process_snapshots` | linked metric timestamp; % | Empty bounded list; no command lines or content | Coordinated 30 seconds / manual |
| Operational events | `/api/events`, `/api/events/summary` | `windows_events` | event UTC timestamp; mapped metadata | Access limitations remain explicit | Coordinated 30 seconds / manual |
| Advanced System Signals | `/api/enhanced-evidence/status` | enhanced sample, run, capability and aggregate tables | source timestamp and source unit | Capability-aware Available, Collecting history, Temporary, or Not supported states | Coordinated 30 seconds / manual |
| Collector heartbeat | `/api/runtime/status` | agent runtime/audit state | last heartbeat UTC | Stale/offline is derived from genuine heartbeat and sample age | Coordinated 30 seconds / manual |

Advanced System Signals remain experimental and non-scoring. Technical source
diagnostics and measured collector overhead are collapsed by default.

## Predictive Alerts data map

| Presentation | Read-only API | Stored source | Timestamp / unit | Missing-data behaviour | Refresh |
| --- | --- | --- | --- | --- | --- |
| Alert summary and evaluation state | `/api/alerts/status` | `alerts`, `alert_evaluation_runs` | latest evaluation timestamps and counts | Not evaluated remains explicit | Coordinated 30 seconds / manual |
| Search, filters, list and evidence | `/api/alerts/history` | `alerts`, occurrences, evidence, explanations, confidence snapshots | observed UTC timestamps; evidence confidence % | Legacy missing snapshots say Not available | Coordinated 30 seconds / manual |
| Exact deep-linked alert and RCA link | `/api/alerts/{id}` | Same alert lifecycle tables | stored timestamps | Missing ID is an explicit safe message | Navigation / refresh |
| Lifecycle transitions | `/api/alerts/{id}` | `alert_state_transitions`, `alert_occurrences` | actual UTC transition times | Unrecorded stages are omitted | Navigation / refresh |
| Notification history | `/api/alerts/{id}` | `notification_deliveries` | actual attempt UTC time and provider result | No delivery recorded is not inferred as failure | Navigation / refresh |
| Global notification state | `/api/notifications/status` | preferences and delivery audit | current state and aggregate counts | Unsupported provider remains explicit | Coordinated 30 seconds / manual |
| Baseline, risk and health readiness | existing status APIs | versioned analytical tables | current stored state | Not evaluated remains separate from zero | Coordinated 30 seconds / manual |

The only API shaping change is a bounded, read-only addition of existing
`notification_deliveries` rows to a full alert record plus its stored baseline
version number. Alert history also accepts an opt-in `summary=true` projection
so the list view does not reconstruct every lifecycle/evidence collection; an
exact alert is still loaded from `/api/alerts/{id}` on selection. The existing
alert status query now uses its established device/state index rather than an
optional-parameter expression that forced a full payload-table scan. These
changes do not add writes or alter any analytical contract.

Alert confidence remains evidence strength/completeness/consistency, not failure
probability or accuracy. Precision, recall, F1 score, false-positive rate,
sample count, dataset and validation date are shown only from a matching stored
validation record; otherwise the interface says **Not yet validated**.

## Visual audit findings

- The previous Live page repeated current values across three flat grids and put
  optional-sensor states beside core measurements without a strong hierarchy.
- Advanced signal source timings and overhead were always expanded, making the
  operational catalogue unnecessarily dense.
- Existing charts already provided bounded extrema-preserving downsampling,
  expand, pan, zoom, fit and return-to-live behaviour, but a single valid point
  was invisible and null-only ranges were too terse.
- Predictive Alerts exposed strong evidence but used raw severity names in the
  primary presentation, had no safe text search, validation filter or sort, and
  showed technical versions in every card footer.
- Lifecycle transitions existed in the API but had no compact timeline. Per-alert
  notification records existed in SQLite but were absent from the detail result.
- Stage 1 tokens, Lucide icons, focus styling, fixed navigation behaviour and the
  Stage 2 Overview components are reusable and remain authoritative.
