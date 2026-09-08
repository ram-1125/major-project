# SmartOps dashboard

The deployment-facing Root-Cause Analysis and System Health screens use only
stored, read-only analytical evidence. Their endpoint, timestamp, unit,
refresh, and missing-data mapping is documented in
[`RCA_HEALTH_DATA_SOURCES.md`](RCA_HEALTH_DATA_SOURCES.md).

This React, Vite, and TypeScript application is the local SmartOps dashboard.
It uses hash routes so localhost refreshes need no backend fallback route and
browser Back/Forward continue to work.

Routes:

- `#/overview` — concise current state and automatic pipeline readiness
- `#/live-monitoring` — telemetry, events, processes, charts, filters, history
- `#/predictive-alerts` — automatic alert evidence and lifecycle
- `#/root-cause-analysis` — selected alert/risk contributing-factor evidence
- `#/system-health` — System Health Score details and history
- `#/pc-quality-check` — inventory and workload suitability
- `#/research-validation` — optional Phase 5B academic validation
- `#/settings` — centralized browser, notification, monitoring and privacy settings

Settings is a separated utility item at the bottom of desktop and mobile
navigation. Predictive Alerts contains no global notification configuration.
Settings owns the existing durable notification preference, provider status,
eligible Advisory/Warning/Urgent categories, exact OFF-to-ON confirmation, and
explicit test action. It also owns Baseline Management: opt-in candidate start,
pause/resume, ready-candidate continuation, cancel, explicit activation, and
safe rollback. **Start New Calibration** remains an optional, confirmed action;
updates never force it and the active personal baseline remains in use. Continue
Calibration preserves the same candidate ID, version,
start time and learned windows; it never activates or duplicates it. A candidate never
changes analytics merely by being collected.

Baseline Management shows observed, eligible and excluded windows, exclusion
reason counts, completeness, applicability, and separate blocking versus
informational readiness reasons. Its Idle profile means user-input inactivity;
Live Monitoring displays system quiescent/background/busy state separately.
The five-minute table states when development or gaming was observed but
another foreground workload won the window majority. Historical rows without
the newer provenance remain labelled historical/unavailable rather than being
silently reinterpreted.

New complete windows also show primary-workload counts/proportions and the
secondary `guided development` label when recognised Brave/browser-media and
VS Code/development foreground activity meet the documented mixed-workflow
rule. Baseline Management exposes that candidate profile with the same
observed, eligible, excluded, day, completeness and readiness fields as every
other workload. The primary workload remains available for compatibility.
It also separates the current eligible membership count from append-only audit
totals for accepted, audited removal and reaccepted windows. These audit totals
explain a legitimate corrected decrease without treating historical acceptance
as current training membership.

Live Monitoring owns the complete **Advanced System Signals** area. It shows each
Phase 7B signal's genuine value or unavailable state, recent trend, source
status, last collection time, capability reason, collector frequency, and
measured agent/database overhead. Needs-more-history, device-unsupported,
permission-limited, collector-failure, shadow-only, and redesign-required
states are distinct. The standard interface labels this evidence
**Experimental signal â€” does not affect results**; internal documentation
retains the shadow-mode identifier. Its data does not alter Risk Evidence, System Health, root-cause
ranking, or predictive-alert severity.

The active Advanced System Signals catalogue omits only permanent device-unsupported
optional signals. Temporary timeout, permission and collector failures remain
visible. The API retains an auditable filtered-signal list, and no history is
deleted or converted to zero.

The desktop ribbon and sidebar are fixed; `.app-content` owns vertical
scrolling. Mobile navigation opens below the fixed ribbon. All five Live
Monitoring charts share one expandable interaction model: pan, wheel/pinch
zoom, Shift+drag selection, bounded timeline scrolling, reset, fit-all, return
to live, crosshair and exact-value tooltip. Long histories use bucket-extrema
downsampling capped at 1,200 rendered points from the existing bounded 5,000
row API response. Only the latest point pulses, and only when the real agent
heartbeat and raw-data freshness both say Live. Reduced-motion disables it.

Root-Cause Analysis does not repeat that live catalogue. It displays only
stored evidence interpreted for the selected alert or Risk Evidence result:
ranked factors, supporting and contradictory evidence, correlation groups,
temporal/workload context, excluded inputs, safe diagnostics, and limitations.
When no eligible result exists, the page shows a not-evaluated state rather
than using unrelated current readings as proof of a cause.

The investigation list is intentionally bounded and concise. Full risk,
root-cause, deviation, health, and related-alert records are fetched only after
the user chooses **View analysis**. The open investigation keeps its own
abortable request lifecycle, so a coordinated 30-second summary refresh does
not replace its evidence or leave it stuck loading. Contributor bars represent
the stored evidence-support value, not probability or proof of causality.

System Health presents the latest stored operating-condition assessment with
an accessible score gauge, stored component scores and effective weights,
observed deductions, exclusions, guidance, and reconstruction details. Its
history is paginated in the browser from a bounded summary response. The trend
supports the existing expansion, pan, zoom, fit-all, reset, and return-to-live
controls; an explicit not-evaluated record remains a disconnected chart gap.
Health-band distribution counts evaluated records only and reports
not-evaluated periods separately.

All pages use at most one coordinated 30-second refresh. The browser preference
can disable that dashboard timer without stopping telemetry, analytics, or
automatic notifications. The visible route determines which API data is
requested. Navigating aborts obsolete requests, and a monotonically increasing
request sequence prevents stale responses from replacing newer state.

Default/remembered page, 12/24-hour display, relative timestamps, and browser
auto-refresh are validated and stored locally in `localStorage`. Notification
preferences remain in SQLite. Sampling and feature-window durations,
analytical policies, retention, deletion, and agent control are read-only.

The responsive sidebar becomes an accessible overlay menu below 900 pixels.
Navigation, menu controls, skip link, filters, and tables remain keyboard
accessible. Missing readings are displayed as unavailable or not evaluated;
they are never converted into fake zero values.

## Stage 1 application shell

The light interface now uses centralized colour, spacing, type, radius,
border, shadow, focus and status tokens. Its deep-navy sidebar groups
Monitoring, Insights and Application routes, uses consistent outline icons,
and can be collapsed on desktop; that browser-only choice is validated in
`localStorage`. At tablet/mobile widths it becomes an accessible drawer.

Navigation Search is local application navigation, not personal-data search.
It indexes the nine routes, Settings sections, major SmartOps features and
the 30 stable PC Quality profile identifiers. Use the sidebar field, the top
bar Search control, or `Ctrl+K` (`Cmd+K` on macOS-style keyboards). Arrow keys
move through results, Enter opens one and Escape closes the dialog. Search
never inspects browser history, URLs, file or document names, typed content,
or telemetry and never writes to SQLite.

The fixed top bar shows the current route, genuine database/agent freshness,
the existing coordinated Refresh action, and shortcuts to notification
settings and Settings. Light theme remains the only complete theme in Stage 1;
dark mode and page-specific chart restyling are intentionally deferred.

Icons are supplied by `lucide-react` 1.39.0, distributed under the ISC
license. Decorative icons are hidden from assistive technology, while every
icon-only action has an accessible name.

Run:

```powershell
npm run test
npm run typecheck
npm run build
```

The frontend has no cloud connection, tracking, authentication, or synthetic
production data. Manual incident and outcome forms are confined to the
optional Research & Validation route and collapsed by default.

## Post-calibration interface

Overview now shows six persisted pipeline states. Green is successful waiting,
blue pulse is genuine backend processing, red is failure/overdue, and grey is
not applicable/not yet executed. CSS motion is disabled under
`prefers-reduced-motion` while state text remains.

PC Quality Check keeps **Hardware Workload Suitability** as a distinct
six-scenario capability assessment and presents all 30 recent operating
assessments as **Current Workload Headroom**. The Overview KPI uses only Device
Current Workload Headroom and shows Not evaluated instead of silently falling
back to hardware suitability. Scores use one decimal place (`99.9171` becomes
`99.9`; an exact `100` becomes `100.0`) while Technical Details preserves full
stored precision. Fractional effective weights are converted to percentages,
so `0.25` displays as `25.0%`.

Each profile separates Current Evidence Quality for its selected five-minute
period from factual History Depth, and discloses whether the evidence was part
of baseline training, is independent post-calibration evidence, or has unknown
historical independence. Expandable Evidence Details reconstruct observed
values, references, component scores, effective weights, deductions, missing
inputs, parent context, and rule/baseline provenance. A profile remains Not
observed or Not evaluated rather than borrowing another score. Online Learning
and Research remains catalogued but is labelled Not independently detectable:
SmartOps will not inspect URLs, browser history, page titles, or content to
distinguish it from general Browser/Media.

Predictive Alerts shows a compact basis, evidence confidence,
method-validation state, safe checks and simple append-only outcome controls.
Exact immutable values, baselines, thresholds, lifecycle records and delivery
history are available through the optional read-only **Technical Evidence**
route. Legacy alerts are explicitly labelled and never fabricated.

Advanced System Signals uses Available, Collecting history, Temporarily unavailable,
Not applicable, Unsupported on this device, and Collector failure. Permanent
unsupported items remain hidden by default; provenance and collector
diagnostics are available in Technical Evidence. Internally, these signals
remain analytically isolated and do not change risk, health or alert results.

Technical Evidence has seven audience-oriented tabs: Monitoring, Alerts, Root
Causes, System Health, PC Quality, Personal Baseline and Analytical Records.
Each uses bounded server pagination and local dataset search. It is read-only;
Audit Records remain exclusively in Settings, and Research & Validation remains
the home for outcome metrics, dataset sufficiency and academic methodology.
