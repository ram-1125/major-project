# SmartOps contributor instructions

## Project intent

SmartOps is a Windows-first, local-first PC operations project. Unless the
project owner explicitly requests otherwise, all collection, storage,
processing, APIs, and user interfaces must continue to run on the user's own
computer.

The current milestone is **Phase 7B.1 post-calibration operation**. Active
baseline v2 is the healthy completed personal calibration; baseline v1 remains
the rollback version. Updates must never force another calibration, while one
optional explicitly confirmed new calibration may be created through the
existing audited lifecycle when the owner requests it. It never starts or
activates automatically.
The centralized Settings behavior approved after Phase 7A remains part of the
product contract.

## Architecture to preserve

The data flow is:

`agent (psutil + safe local Windows APIs) -> data/smartops.db (sqlite3) -> backend (FastAPI) -> frontend (React)`

- `agent/` owns configuration, local device identity, metric collection,
  counter-to-rate calculation, process snapshots, and the non-overlapping
  collection loop. It also owns bounded, capability-aware enhanced Windows
  performance/storage/battery collection. The continuously running agent is
  the only automatic Windows-notification dispatcher.
- `backend/` owns additive SQLite migrations, repositories, API response
  schemas, history filtering and pagination, and the local FastAPI application.
- `frontend/` is the React, Vite, and TypeScript menu-based local dashboard.
  Preserve its classic light enterprise style, hash routes, responsive
  sidebar, browser-history behavior, route-scoped polling, and accessible
  navigation unless the owner explicitly requests another redesign.
  The Stage 1 application shell uses centralized CSS design tokens, one
  Lucide outline-icon family, grouped fixed navigation, a validated persisted
  desktop collapse preference, and local catalogue-based navigation search.
  Search may index routes, Settings sections, genuine feature names, and
  stable PC Quality profile IDs only; it must never search personal content or
  trigger database writes.
- `analytics/` owns transparent workload rules, fixed five-minute feature
  aggregation, robust device/workload baselines, local deviation detection,
  versioned deterministic evidence fusion, explainable System Health Score
  evaluation, safe hardware inventory, and deterministic workload-suitability
  assessment. It also owns versioned alert policy, evidence eligibility,
  temporal correlation, and lifecycle evaluation. Deviation, risk evidence,
  health, suitability, alerts, and validation must remain separately
  interpreted. It owns versioned validation eligibility, deterministic
  matching, metric reconstruction, and minimum-evidence policy.
- `scripts/` contains beginner-friendly Windows PowerShell entry points.
- `tests/` must cover collection behavior, rates, migrations, persistence, API
  compatibility, filters, and pagination.

The `metrics` table stores every raw sample. JSON columns contain structured
per-core CPU and fixed-partition usage values. `process_snapshots` stores the
top CPU and memory process rows linked to a metric in the same transaction.
Schema changes must migrate old Phase 1 databases without deleting records.
`windows_events` stores mapped metadata only, checkpoints prevent repeat log
scans, and `feature_windows` contains idempotent five-minute summaries.
Phase 3A baseline and deviation tables store derived, versioned local results.
Phase 3B tables store reconstructable component contributions, ranked
root-cause candidates, normalized supporting/contradictory evidence, and
evaluation-run metadata. Phase 4A tables store versioned health runs,
assessments, component scores, capped deductions, input availability, and
diagnostic guidance. Schema changes remain additive and transactional.
Phase 4B tables store allowlisted inventory facts, workload-suitability
assessments, component results, explicit gates/caps, limiting components, and
generic recommendations. Phase 5A tables store alert runs, durable alert
lifecycles, occurrences, raw/effective evidence, suppressed correlations,
state transitions, explanations, and diagnostic/preventive guidance. Phase 5B
tables store observation periods, append-only incident/feedback revisions,
alert-incident links, validation runs, metric reconstruction, lead time, and
inclusion/exclusion decisions. Schema 9 migration must preserve every schema-8
and earlier row. Phase 7A schema 10 adds durable notification preferences and
delivery-attempt records without changing or deleting any schema-9 row. Phase
7B schema 11 additively stores enhanced collection runs, signal samples,
hourly aggregates, structured event evidence, collector capability state, and
retention audit runs. Phase 7B.1 schema 12 adds exact notification-category
decisions, single-agent runtime ownership, alert observation rollups, enhanced
schedule provenance, and immutable/candidate baseline versions. Schema 13
additively adds two-dimensional user/system activity provenance, transparent
five-minute workload-majority metadata, and baseline-profile applicability and
exclusion audit fields. Schema 14 adds complete-window foreground composition,
versioned secondary mixed-workload context, and explicit assessment workload
rule provenance. Schema 15 adds the candidate learning state independently of
candidate lifecycle so ready-candidate continuation is explicit and auditable.
Schema 16 adds durable single-agent ownership triggers and the canonical WAL
connection/short-writer policy; it does not rewrite telemetry or analytics.
Schema 17 adds authoritative finalised-window source provenance, append-only
feature repair and candidate-membership audit events, and bounded raw-cycle
operational provenance. It does not relax the ten-sample candidate policy or
change genuine analytical scoring.
Schema 18 additively stores the separate fine-grained PC Quality taxonomy,
v2-anchored profile assessments, immutable future-alert explanation and
confidence snapshots, method-level validation records, append-only alert
outcome labels, and genuine pipeline-stage state. It does not rewrite v2
workload labels or change risk, health, RCA, alert, or notification logic.
Browser display preferences still use validated
`localStorage`, while the existing notification tables remain authoritative.

## Non-negotiable defaults

- Keep the raw sampling interval at **30 seconds**. A shorter development
  interval may be set with `SMARTOPS_INTERVAL_SECONDS`.
- Preserve `python -m agent.main --once` and continuous Ctrl+C operation.
- Keep exactly one automatic notification dispatcher in the existing agent
  process. The API may send an explicit user-requested test toast or one
  OFF-to-ON configuration-confirmation toast but must not run a competing
  delivery worker.
- Map alert severities exactly to canonical notification categories:
  informational/advisory -> advisory, warning -> warning, urgent -> urgent.
  Unknown legacy values fail closed. Never infer eligibility from display
  labels, capitalization, or substrings.
- By default, native notifications are eligible only for Phase 5A `warning`
  (semantic High) and `urgent` (semantic Critical Evidence) activations.
  Informational/Low and advisory/Elevated alerts remain excluded unless the
  owner deliberately changes the local severity preference.
- Persist every real notification attempt before calling Windows. Deliver an
  activation once, allow one later delivery for a meaningful eligible severity
  escalation, and never replay unchanged, resolved, or pre-Phase-7 history.
- Notification text must remain evidence-based and must not imply guaranteed
  crash prediction. Clicking a real toast opens the exact local alert at
  `#/predictive-alerts?alertId=<encoded integer>`.
- Test notifications must be explicitly labelled test-only and must never
  create alerts, incidents, feedback, validation evidence, risk assessments,
  or production notification-delivery records.
- An OFF-to-ON preference transition may submit exactly one configuration
  confirmation titled `SmartOps` with the message `Notifications have been
  enabled.` and a `#/settings` link. It is not a test or predictive delivery,
  is never replayed, and creates no notification-delivery or analytical row.
  ON-to-OFF and unchanged saves never submit a toast.
- Keep the notification provider replaceable and failure-safe. Unsupported
  Windows sessions or provider errors are logged/disclosed without stopping
  telemetry, analytics, the API, or the supervisor.
- Collection cycles must remain synchronous/non-overlapping. Log an individual
  cycle failure and retry later instead of terminating the agent.
- Keep raw sampling on the highest-priority 30-second path. Exactly one bounded
  coalescing enhanced worker performs slow optional collection outside writer
  transactions; a busy worker records a coalesced reason without building a
  backlog or delaying raw telemetry.
- Open SQLite only through `backend.database`. Keep WAL persistent, use the
  centralized 15-second timeout/busy policy, serialize in-process writers, and
  reserve write transactions only after slow collection or analysis work.
  Retry only SQLite busy/locked failures with bounded backoff; every retried
  operation must remain idempotent. Never remove SQLite sidecar files manually.
- Repeated unchanged alert observations update compact last-seen/observation
  metadata only. Durable occurrence/evidence rows are reserved for activation,
  escalation, material evidence change, acknowledgement, recovery/resolution,
  and reactivation. Never rewrite unchanged feature-window timestamps.
- A bounded aggregation query may identify window keys only. Rebuild every key
  from its exact authoritative half-open raw range, never finalise the current
  open window, and never downgrade a finalised feature from a partial lookback.
  Candidate training uses finalised quality-evaluated windows only. Every
  correction and every accepted/rejected/removed/reaccepted membership change
  must be append-only audited and idempotent.
- Preserve every active baseline as an immutable version. Recalibration is an
  explicit Settings action, learns a separate candidate from post-start
  quality-eligible windows, and requires explicit activation. It never starts
  automatically, and rollback preserves all versions and operational data.
- A ready candidate does not mean active. Continue Calibration must retain the
  same candidate identity and original start time, record a continuation event,
  and set only its persisted learning state. Maintenance refreshes only an
  explicitly collecting candidate; activation remains a confirmed user action.
- Once baseline v2 is active, it remains the active calibration until an
  explicitly started, separately confirmed future candidate is valid and
  activated by the owner. Allow only one candidate at a time, keep rollback
  history, never force recalibration after an update, and ensure fine-grained
  quality profiles never become calibrated-baseline profiles.
- Future alert occurrences store immutable explanation and evidence-confidence
  snapshots. Confidence measures evidence quality, not failure probability or
  accuracy. Method accuracy is shown only from a version-matched labelled
  validation registry; otherwise use `Not yet validated`.
- Treat user-input activity and system activity as separate facts. The Idle
  baseline profile means genuine user inactivity; background system work must
  not erase that input-idle evidence. Preserve old classifications and attach
  a rule version/provenance only to newly classified samples and windows.
- Candidate readiness is based on eligible count, distinct eligible days, and
  quality/safety eligibility. Keep excluded windows and their reasons
  auditable, but do not let the mere existence of exclusions permanently block
  a profile that has enough eligible evidence. Unused profiles are not required
  for activation.
- Preserve the majority primary workload. For complete new-rule windows,
  `guided_development` may be stored as a secondary context only when recognised
  development and browser/media foreground samples each meet the centralized
  minimum and dominate the combined window. Background process presence, URLs,
  page/window titles, browsing history, and content must never establish it.
- Candidate baseline versions may add and learn a guided-development profile
  without restart. Selection prefers a ready guided profile, then a ready
  primary-workload profile, then the device profile. Never auto-activate it.
- Keep routine downstream analytics in one ordered, coalescing agent-owned
  maintenance worker on the five-minute feature cadence so slow cycles cannot
  repeatedly delay raw sampling or build a backlog. Do not introduce a
  competing alert evaluator or notification dispatcher.
- Enhanced evidence remains in shadow mode: collect, retain, aggregate, expose,
  and display it, but do not change the Phase 3B Risk Evidence Index, Phase 4A
  System Health Score, or Phase 5A predictive-alert policy or severity.
- Keep the complete Phase 7B signal catalogue, capability status, freshness,
  source status, and overhead presentation only on Live Monitoring. Root-Cause
  Analysis may show only stored alert/risk-specific interpreted evidence and
  must never present unrelated live values as proof of a contributing cause.
- The active Enhanced Evidence UI may omit only optional signals with a
  deterministic permanent device-unsupported capability reason. Preserve their
  history and API audit metadata. Permission limits, timeouts, temporary
  failures, and collector failures remain visible and null, never zero.
- Prefer official Windows Performance Counters, CIM/WMI, Storage PowerShell,
  and the already deduplicated structured Windows Event Log metadata. Every
  external query must be read-only, bounded by a timeout, and safe without
  administrator rights.
- Keep enhanced performance counters at a lower frequency than raw telemetry
  and hardware capability checks slower still. Preserve the centralized
  frequencies, timeouts, and retention rules in
  `agent/enhanced_catalogue.py`.
- Never duplicate a signal already stored in `metrics`; expose it by reference
  where enhanced evidence needs the same value. Unavailable and not-applicable
  enhanced values remain null with a source-specific reason.
- Phase 7B retention may aggregate and remove only Phase 7B enhanced rows.
  It must never delete or rewrite raw telemetry, core Windows events, feature
  windows, analytics, alerts, validation, notification, or preference rows.
- Never store raw Windows event messages or XML in enhanced evidence. Derive
  only versioned classifications from the existing safe mapped event metadata.
- Use Python 3.11 or newer, FastAPI, standard-library `sqlite3`, and `psutil`.
- Keep the database and generated device identifier inside `data/` and out of
  Git. Do not add automatic retention or deletion in Phase 5B.
- Bind services to the local computer and allow browser CORS only from the
  local Vite development origin.
- Do not introduce a cloud backend, external telemetry service, data upload,
  Docker, PostgreSQL, or authentication unless the owner explicitly changes
  the project scope.
- Never collect command-line arguments, file contents, browser history, typed
  text, credentials, passwords, document information, or window titles.
  Process snapshots may contain only PID, executable name, CPU percentage, and
  memory percentage.
- Store process CPU percentages on a whole-system 0-100% scale by dividing
  psutil's multi-core value by the logical CPU count. Exclude PID 0 and
  `System Idle Process` from the top CPU ranking.
- Treat unavailable operating-system and hardware metrics as null values rather
  than collection failures. Optional temperature and GPU support must remain
  safe and non-fatal.
- Record sample and boot timestamps in UTC.
- Never store raw Windows event XML or event messages. Event summaries must be
  generated from the category mapping. Warnings are operational evidence, not
  confirmed failures.
- Keep workload rules transparent and contextual. High CPU under gaming,
  development, or compute work is not automatically a fault.
- Keep production feature windows fixed at five UTC minutes with an expected
  ten raw samples, even when a shorter raw test interval is configured.
- Train baselines only from complete, sufficiently covered, closed feature
  windows. Never impute unavailable optional sensors as healthy zero values.
- Keep baseline policy, metric direction, weights, and severity thresholds
  centralized. Prefer workload-specific profiles and explicitly report device
  fallback.
- Isolation Forest is local-only, deterministic, and detects unusual
  combinations. It is not failure prediction. Never persist unsafe pickle
  models; deterministic retraining from SQLite is preferred.
- Never fabricate a Deviation Index during cold start or inadequate data quality.
- Never fabricate a Risk Evidence Index when a completed Phase 3A deviation,
  ready baseline, valid workload, coverage, or core data quality is missing.
- Treat the 0-100 Risk Evidence Index as accumulated operational evidence, not
  failure probability. Root-cause candidates are ranked hypotheses, not proven
  causality. Recommendations must remain diagnostic checks only.
- Keep Phase 3B domain rules, correlation groups, weights, event mappings,
  context exceptions, persistence policy, and explanation templates
  centralized in `analytics/risk_catalogue.py`.
- Prevent correlated summaries such as CPU average, maximum, and p95 from
  independently inflating evidence. Optional missing sensors never count as
  healthy zero or negative evidence.
- Evaluate System Health Score only from complete five-minute windows with at
  least 80% coverage, valid workload context, and available required core
  inputs. An inadequate window is explicitly `not_evaluated`.
- A score may be `provisional` before Phase 3A/3B readiness, but is
  `established` only when eligible baseline, deviation, and risk evidence are
  available. Never fabricate unavailable historical evidence.
- Keep health weights, thresholds, caps, bands, input classifications, and
  explanation templates centralized and versioned in
  `analytics/health_config.py`.
- Store enough normalized data to reconstruct every score. Workload exceptions,
  temporal persistence, recovery, event overlap, correlated groups, raw
  deductions, and effective capped deductions must remain auditable.
- System Health Score describes current observed operating condition. It is not
  a failure probability, future-reliability guarantee, or PC quality rating.
- PC Quality Check describes hardware/system capability for one selected,
  versioned workload profile. It is not a benchmark result, universal PC score,
  failure prediction, compatibility guarantee, or future-reliability score.
- Keep the 30-profile **Current Workload Headroom** assessment semantically
  separate from the six-scenario **Hardware Workload Suitability** assessment.
  Headroom describes the latest qualifying five-minute operating period; an
  exact 100 means its measured factors remained within expected ranges, not
  perfect hardware or validated accuracy. Display Current Evidence Quality
  separately from factual History Depth, disclose baseline-training versus
  independent post-activation evidence, and never use hardware suitability as
  an Overview fallback for missing Device headroom.
- Keep the inventory allowlist in `analytics/quality.py`. Never add hardware
  serials, product keys, user/owner names, MAC/IP addresses, application
  history, command lines, window titles, or other private identifiers.
- Inventory must not run every 30 seconds. Reuse unchanged signatures, refresh
  on a safe interval, and never let inventory/evaluation failures interrupt
  raw telemetry.
- Keep workload profiles, component weights, minimum/recommended thresholds,
  hard gates, bottleneck caps, bands, source metadata, and limitations
  centralized and versioned in `analytics/quality_catalogue.py`.
- Unavailable or unreliable inventory values never become zero. Missing
  required capability produces `not_evaluated`; important uncertainty produces
  `provisional`. Normalize only valid applicable component weights.
- System Health and Risk Evidence may be shown only as separate current
  operating-readiness context and must never modify the Workload Suitability
  Index. Recommendations stay generic and never name a commercial product or
  promise a performance gain.
- Generate alerts only from complete, sufficiently covered five-minute windows
  with an evaluated Phase 4A health assessment. Provisional health may
  contribute, but its state and limitations remain visible. Never turn
  `not_evaluated` evidence into an alert.
- Keep alert categories, thresholds, persistence, severity, workload
  exceptions, recovery, cooldown, explanation text, and safe guidance
  centralized in `analytics/alert_catalogue.py`.
- A continuing condition updates one deterministic fingerprint. Preserve all
  occurrences and transitions through `open`, `acknowledged`, `recovering`,
  and `resolved`; acknowledgement never means the condition disappeared.
- Correlated CPU summaries share one group, RAM/swap share one group, and
  Phase 3B evidence represented by Phase 4A is not counted twice. An isolated
  non-critical signal cannot create a warning or urgent alert.
- Alert recommendations are diagnostic or preventive only. Never stop
  processes, restart services, delete files, install software, update drivers,
  or change Windows configuration automatically.
- Preserve the established primary menu order: Overview, Live Monitoring,
  Predictive Alerts, Root-Cause Analysis, System Health, PC Quality Check, then
  optional Research & Validation. Keep Settings as a separated utility item at
  the bottom. Notification preferences and other genuine application-level
  settings belong only in Settings; alert filters and lifecycle actions remain
  on Predictive Alerts. Manual incident and feedback forms belong only in
  Research & Validation and remain collapsed by default.
- Use one coordinated 30-second frontend refresh mechanism. Scope requests to
  the current route, abort obsolete navigation requests, and prevent an older
  response from replacing newer state.
- Validated browser-only preferences may choose the default/remembered route,
  12/24-hour or relative timestamps, and browser auto-refresh. Disabling
  browser refresh must never stop telemetry or analytics, and must never create
  an additional polling timer.
- Never infer a negative outcome from missing incident reports, missing alert
  feedback, telemetry gaps, or an open/incomplete observation period. Return
  `insufficient_labeled_evidence` and null metrics when publication
  requirements are not met.
- True negatives require an explicitly completed observation period, a user
  declaration that incident reporting is complete, and adequate eligible
  telemetry coverage. Preventive action and short observation horizons must
  remain explicit confounders/exclusions.
- Automatic alert-to-incident matching may propose probable or possible
  candidates but must never create a confirmed match or claim causality.
  Confirmed matches require explicit user action.
- Incident and feedback corrections are append-only revisions. Withdrawn
  evidence remains auditable and is excluded from current metrics.
- Keep validation matching weights, horizons, label definitions, publication
  minimums, and interpretation text centralized and versioned in
  `analytics/validation_config.py`.
- Validation runs must store sufficient counts, reconstruction inputs,
  inclusion/exclusion reasons, source versions, and lead-time records to
  reproduce every result. Unchanged evidence must evaluate idempotently.
- Never put synthetic incidents, feedback, matches, or validation outputs in
  `data/smartops.db`; controlled validation tests use temporary databases.
- Preserve this statement in API documentation and dashboard: “SmartOps
  validation results are based on available user-reported or externally
  verified outcomes. They do not by themselves establish guaranteed failure
  prediction, hardware diagnosis, or universally validated accuracy.”

## Deferred work

Do not implement calibrated failure probability, guaranteed crash prediction,
confirmed root-cause claims, automatic remediation, stress testing,
commercial product recommendations, external notifications, authentication, a cloud backend, publishing
automation, server-side multi-page routing, or Windows service/executable packaging unless
the owner explicitly starts the relevant future phase.

## Change expectations

Keep code beginner-readable and update `README.md` whenever setup, collection,
storage, API, or usage changes. Preserve public API paths where practical, add
tests for behavior changes, and run both Python tests and the frontend type
check/build before handing off. Do not overwrite unrelated files or generated
local telemetry.
