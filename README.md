<<<<<<< HEAD
# Major-Project---SmartOps
=======
# SmartOps

SmartOps is a Windows-first, local-only application that monitors the current
operating condition of this computer. It stores system data locally, explains
health and risk evidence, and never uploads system data to a cloud service.

## User Guide

### Start SmartOps

Open the SmartOps folder in VS Code, select **Terminal > New Terminal**, and run:

```powershell
.\scripts\run_local.ps1
```

Open `http://localhost:5173/#/overview`. Press `Ctrl+C` once in the same
PowerShell terminal to stop the collector, local API, and dashboard.

### Main pages

- **Overview** shows current health, alerts, risk, PC Quality, and pipeline status.
- **Live Monitoring** shows current and historical system measurements.
- **Predictive Alerts** explains evidence-supported operating conditions.
- **Root-Cause Analysis** provides a bounded investigation list and loads the
  selected window's stored contributors, baseline comparison, timeline,
  limitations, and related records on demand. Rankings are possible
  contributors, never asserted physical causes.
- **System Health** summarizes the current observed operating condition with an
  honest score/not-evaluated gauge, stored components and deductions,
  disconnected history gaps, evaluated-only band distribution, and bounded
  assessment history.
- **PC Quality Check** compares this PC with 30 workload-specific profiles.
- **Research & Validation** contains optional academic evidence and methodology.
- **Settings** contains display, notification, privacy, diagnostics, and optional
  recalibration controls.

The redesigned light application shell groups these routes in a fixed icon
sidebar and shows genuine database/agent freshness in the fixed top bar. Use
Navigation Search or press `Ctrl+K` to find pages, Settings sections, major
features, and any of the 30 PC Quality profiles. The search catalogue is local
and static: it does not search browser history, files, typed content, or other
personal information, and it does not write to the database. Desktop sidebar
collapse is a browser-only preference. At smaller widths the sidebar becomes
an accessible drawer.

Health describes the current operating condition. Risk Evidence describes the
strength of operational warning evidence. PC Quality describes workload-specific
capability. None is a guaranteed prediction of failure or future reliability.

### Optional recalibration

One successful personal calibration is normally sufficient. SmartOps updates
and new PC Quality profiles do not force another calibration. **Start New
Calibration** remains available under **Settings > Personal Baseline** for major
hardware/system changes or substantially different long-term use. Starting it
requires confirmation; the existing baseline remains active, monitoring
continues, and a new calibration never activates automatically.

### Notifications and privacy

Notification preferences are managed only in Settings. Automatic notifications
remain local Windows notifications. SmartOps does not collect typed text,
passwords, file contents, browser history, URLs, document names, or window
titles, and it does not send system data to the cloud.

### Troubleshooting

If the dashboard cannot connect, confirm the PowerShell supervisor is still
running and that ports 8000 and 5173 are not used by another program. Run
`.\scripts\setup.ps1` if Python or frontend packages are missing. Optional
hardware readings may remain unavailable when Windows or the device does not
expose them; this does not stop normal monitoring.

## Technical and Research Documentation

The material below preserves architecture, schema, algorithm, research,
development-history, and verification details for maintainers and academic
review. Phase names and internal versions are historical technical identifiers,
not current application branding.

SmartOps is a Windows-first, local-only PC operations project with the purpose
**“Where Systems Speak Before They Fail.”** Phase 7B preserves every Phase
1–7A collector, database, analytics, API, alert, health, quality, validation,
dashboard, centralized Settings, and native-notification behavior. It adds
capability-aware Windows performance, storage, battery, and structured
failure-related evidence in a deliberately non-scoring **Shadow Mode**. Phase
7B.1 corrects notification-category filtering, stops unchanged alert-history
write amplification, makes enhanced scheduling auditable, bounds physical-disk
activity semantics, and adds opt-in versioned baseline recalibration. It does
not implement Phase 7C or promote enhanced evidence into genuine analytics.

The automatic pipeline

`Telemetry → Feature Window → Baseline → Risk Evaluation → Health Evaluation → Predictive Alert`

requires no manual incident, severity, workload, category, or outcome entry.
Optional labelled incident and feedback input is isolated under **Research &
Validation** and is used only for academic evaluation.

SmartOps
preserves the Phase 3A pipeline: it collects raw operational measurements every
**30 seconds**, retains every sample in SQLite, safely maps relevant Windows
events, assigns transparent workload context, derives fixed five-minute
feature windows, and learns local device/workload baselines for transparent
deviation detection. It preserves Phase 3B evidence fusion and ranked
contributing-factor candidates and the Phase 4A System Health Score. It adds a
safe, versioned local hardware inventory and transparent PC Quality Check for
workload-specific capability assessment. Phase 5A adds local predictive-alert
records with traceable evidence, persistence, acknowledgement, recovery, and
resolution. Phase 5B adds explicitly labelled local incident reports, alert
outcomes, observation periods, deterministic alert-to-incident matching, and
auditable predictive-validation summaries. Missing feedback is never treated
as evidence that no incident occurred.

```text
local PC -> psutil and safe Windows APIs -> data/smartops.db
         -> FastAPI on 127.0.0.1 -> React dashboard on localhost
```

There is no cloud backend and no telemetry upload.

## Personal baseline and post-calibration features

The successfully activated **baseline v2 remains the active personal baseline**
for this device/user, while baseline v1 remains immutable and available for
rollback. One successful calibration is normally sufficient; updates and new
PC Quality profiles never force another. An explicit, confirmed **Start New
Calibration** action remains available for major hardware/system or long-term
usage changes. It creates one isolated candidate through the existing audited
lifecycle, never activates automatically, and leaves the active baseline in
use. Fine-grained PC Quality profiles remain a separate taxonomy anchored to
the active baseline and do not create calibrated profile statistics.

The 30-profile assessment is presented as **Current Workload Headroom**. It
describes available operating headroom during the most recent qualifying
five-minute observed workload period; it is not benchmark performance,
hardware capability, prediction accuracy, or a future-performance guarantee.
The interface keeps **Hardware Workload Suitability** as a separate capability
assessment and never substitutes one measure for the other. Current Evidence
Quality describes the selected period, while History Depth reports factual
observation counts, distinct days, latest evidence, and available
post-activation independence. Calibration-period evidence is explicitly
labelled and is not claimed as independent validation.

Schema 18 additively introduces:

- privacy-safe fine-grained PC Quality observations and independent stored
  profile scores (20 fine-grained profiles plus the existing broad profiles);
- immutable explanation and evidence-confidence snapshots for newly created
  alert occurrences;
- a method-level validation registry and append-only alert outcome labels;
- persisted operational state for Telemetry, Feature Windows, Active Baseline,
  Risk, Health, and Predictive Alerts.

Older alerts are not rewritten. They display: **Legacy alert — detailed
explanation was not recorded when this alert was generated.** An individual
alert's evidence confidence is not accuracy and is not failure probability.
Accuracy, precision, recall, specificity, F1, and false-positive rate appear
only when an applicable method-level labelled validation record exists;
otherwise the result is **Not yet validated**.

The Overview Pipeline Status circles use persisted backend truth: green means
the latest expected work succeeded, gently pulsing blue means genuine work is
running, red means failed/overdue/disconnected, and grey means not yet run or
not applicable. Reduced-motion preferences disable the pulse without removing
the text state. Existing expandable Live Monitoring charts, fixed navigation,
30-second browser refresh, centralized notifications, and alert-specific RCA
evidence are preserved.

New read APIs are:

- `GET /api/pipeline/status`
- `GET /api/quality/profile-taxonomy`
- `GET /api/quality/profile-scores`
- `GET /api/validation/registry`

`POST /api/alerts/{alert_id}/outcome` appends `pending`, `confirmed`,
`false_positive`, or `inconclusive` feedback to the latest occurrence. It does
not retrain, change baseline v2, alter severity, or affect scoring.

While the supervisor is running, the continuously running telemetry agent is
also the single automatic notification dispatcher. The dashboard may be
closed. A native toast can still appear for a new High or Critical Evidence
alert and clicking it opens that exact alert in the local dashboard.

Predictive alerts indicate observed operational evidence, not guaranteed
future failure. Root-cause results identify probable contributing factors
supported by available operational evidence; they are not confirmed hardware
diagnoses. The System Health Score summarizes available current-condition
evidence and is not a probability that the computer will or will not fail.
Accuracy claims require sufficient genuine labelled evidence; missing optional
validation input remains unverified rather than being interpreted as a
successful prediction.

## Required software

Install:

1. Windows 10 or Windows 11.
2. Python 3.11 or newer. Select **Add Python to PATH** during installation.
3. A current Node.js LTS release, which includes npm.
4. Visual Studio Code.

Confirm the tools in a new PowerShell terminal:

```powershell
python --version
node --version
npm --version
```

## Open PowerShell in VS Code

1. Open VS Code.
2. Select **File > Open Folder** and choose the `SmartOps` folder.
3. Select **Terminal > New Terminal**.
4. If necessary, use the arrow beside the terminal `+`, select **Select Default
   Profile**, choose **PowerShell**, and open a new terminal.
5. Confirm the prompt is inside the SmartOps folder.

Run all remaining commands from the project root.

## Setup

The setup script creates `.venv`, installs the Python project and test
dependencies, and installs the frontend packages:

```powershell
.\scripts\setup.ps1
```

No database contents are removed when setup is run again.

## Collect one sample

The Phase 1 command remains supported:

```powershell
.\scripts\collect_once.ps1
```

Equivalent direct command:

```powershell
.\.venv\Scripts\python.exe -m agent.main --once
```

The database initializes automatically. If `data\smartops.db` has an older
schema, SmartOps additively migrates telemetry, event, workload, feature,
baseline, deviation, risk-evidence, candidate, and health structures without
deleting old rows. Phase 7B.1 originally introduced schema 12; this targeted
correction introduced schema 13 for versioned workload/activity provenance and
baseline-profile applicability audit fields. Mixed-workflow support advances
the additive schema to version 14, storing complete-window foreground
composition, secondary-context provenance, and the workload-rule version used
by future assessments. This update advances schema 14 to schema 15 by adding
only the persisted candidate learning state, so a ready candidate can be
continued or paused without changing its immutable version or lifecycle audit.
Schema 16 adds durable single-agent ownership guards and standardizes the
SQLite concurrency policy without changing analytical records. Schema 17 adds
authoritative half-open feature-window provenance, append-only repair and
candidate-membership events, and compact raw-cycle audit records. Every earlier
row is preserved. Browser display
preferences need no migration.

### SQLite concurrency and lock handling

SmartOps uses one canonical SQLite policy on every Python connection:

- persistent WAL journal mode, configured during database initialization rather
  than on every sample;
- a 15-second connection and `busy_timeout` window;
- foreign-key enforcement;
- `synchronous=NORMAL`, SQLite's durable and efficient WAL policy for this
  local telemetry workload;
- concurrent readers with serialized, short writer transactions;
- priority for the 30-second raw telemetry write over queued maintenance work;
- four bounded retries with exponential backoff and small jitter only for
  genuine SQLite busy/locked errors.

Telemetry and alert calculations happen before their write transaction where
practical. A Windows notification is always reserved and committed before the
native provider is called, then its result is audited in a separate short
transaction. This prevents a database retry from submitting the same toast
twice. SmartOps never manually removes `-wal`, `-shm`, or journal files.

## Start continuous SmartOps

Run:

```powershell
.\scripts\run_local.ps1
```

The script starts and supervises:

1. the continuous agent;
2. FastAPI at `http://127.0.0.1:8000`;
3. the React/Vite dashboard at `http://localhost:5173`.

It displays each process ID and the dashboard address. Open:

**http://localhost:5173**

The agent stores a sample immediately, then schedules non-overlapping raw
collection cycles every 30 seconds. Slow optional enhanced evidence runs on one
bounded coalescing worker and cannot block the raw schedule or build a backlog.
One collection error is logged and retried on the next cycle instead of
stopping the agent. The dashboard refreshes every 30 seconds.

## Change the development interval

The production/default raw interval is 30 seconds. To test with a shorter
interval in the current PowerShell terminal:

```powershell
$env:SMARTOPS_INTERVAL_SECONDS = "2"
.\.venv\Scripts\python.exe -m agent.main
```

Stop it with Ctrl+C, then remove the override:

```powershell
Remove-Item Env:SMARTOPS_INTERVAL_SECONDS
```

The next run uses the 30-second default. Do not commit a development override.

## Stop SmartOps

When using `run_local.ps1`, return to its PowerShell terminal and press
**Ctrl+C once**. The supervisor stops its agent, API, and frontend processes and
prints a completion message.

When running `python -m agent.main` directly, Ctrl+C stops the agent cleanly.
Stopping SmartOps never deletes stored history.

## Metrics collected

All timestamps are recorded in UTC. Hardware-dependent values are nullable.

### CPU and processes

- total CPU utilization percentage;
- per-core utilization as a structured JSON array;
- physical and logical core counts;
- current CPU frequency;
- process count and safely available thread count;
- separate top-five CPU and top-five memory snapshots.

Process snapshots contain only PID, executable name, CPU percentage, and memory
percentage. A process CPU percentage uses a whole-system 0-100% scale:
SmartOps divides psutil's multi-core process percentage by the logical CPU
count. This preserves legitimate multi-core work without showing values above
100%. PID 0 and `System Idle Process` are excluded from the top CPU list.
SmartOps does not collect process command lines.

### Memory

- RAM utilization;
- RAM used, available, and total bytes;
- swap/page-file utilization;
- swap used and total bytes.

### Disk

- fixed-partition usage with mountpoint, percentage, and used/free/total bytes;
- compatible system-disk fields retained from Phase 1;
- disk read/write bytes per second;
- disk read/write operations per second;
- cumulative disk counters used to calculate later rates.

### Network

- upload and download bytes per second;
- packets sent and received per second;
- whether at least one network interface is available;
- cumulative counters used to calculate rates.

### Battery and power

- battery percentage;
- charging status;
- AC power connection;
- remaining battery time when Windows provides it.

Desktop computers normally report these fields as unavailable.

### System and user state

- uptime and UTC boot timestamp;
- safe Windows last-input idle duration;
- active or idle state;
- foreground executable name when available.

SmartOps does not collect window titles, typed text, or foreground application
contents. Foreground-process collection has successfully displayed `Code.exe`
on this PC. It can still be null when permissions are restricted or SmartOps is
run from a non-interactive execution environment.

### Optional sensors

- CPU temperature through `psutil` when supported;
- NVIDIA GPU utilization, memory utilization, and temperature through a local
  `nvidia-smi` installation when present.

Windows temperature and GPU support varies by manufacturer, driver, hardware,
and account permissions. `Unavailable` is expected on many PCs and is not an
implementation failure.

## Phase 7B enhanced evidence

The agent schedules enhanced collectors on one lower-priority, bounded worker
at lower frequencies than the 30-second raw sample. If that worker is still
active, the next request is audibly coalesced instead of delaying raw telemetry:

| Collector | Frequency | Local source |
| --- | ---: | --- |
| Core psutil telemetry and event checkpoints | 30 seconds | psutil and structured Windows Event Logs |
| Performance evidence | 60 seconds | Windows `Get-Counter` |
| Hardware capability evidence | 15 minutes | `Get-PhysicalDisk`, `Get-StorageReliabilityCounter`, and battery CIM/WMI |
| Enhanced-data retention maintenance | 6 hours | local SQLite |

Performance evidence includes processor queue length, sustained per-core
saturation, context switches/sec, interrupt time, DPC time, maximum-frequency
and performance-limit evidence, committed-memory percentage, hard-fault input
pages, page-read operations, paged/non-paged pools, disk read/write latency,
disk queue length, and disk active time. Existing available-memory and disk
throughput values are referenced directly from `metrics`; they are not stored
twice.

Phase 7B.1 defines disk activity as the busiest individual physical disk's
active time: `100 - minimum physical-disk idle time`. `_Total` is excluded and
the result is bounded to 0–100%. Older aggregate rows that lack this provenance
remain stored and are labelled `redesign_required`; they are not rewritten or
used in scoring.

Hardware evidence attempts storage temperature, wear/lifetime indication,
read/write/uncorrected error counters, battery full/design capacity, capacity
health, discharge rate, and remaining capacity. Windows drivers and firmware
often do not expose storage reliability or battery design capacity. An
unsupported, permission-limited, timed-out, or not-applicable reading is stored
as null with a clear reason, never as zero.

Structured evidence is derived from the already checkpointed and deduplicated
safe Windows Event Log metadata. It distinguishes WHEA corrected/fatal events,
Kernel-Power unexpected shutdowns, BugCheck records, resource exhaustion,
storage-driver/file-system warnings, application crash/hang/termination
records, unexpected service termination/recovery attempts, and AC/battery
transitions. SmartOps still stores no raw event XML or message text.

Every PowerShell/CIM query has a hard timeout. A failed optional source is
recorded and isolated; it cannot stop raw telemetry, analytics, FastAPI, or
notification dispatch. Normal use does not require administrator privileges,
although permissions can reduce availability.

Collector state records scheduled time, actual attempt, delay, duration,
agent-session identity, and gap classification. The next due time advances on
the fixed 60-second schedule instead of drifting from the delayed attempt.
Offline/session changes are distinct from missed-while-running samples,
timeouts, and collector failures. Live Monitoring distinguishes needs-more-
history, device-unsupported, permission-limited, collector-failure,
shadow-only, and redesign-required states; merely running longer cannot make
hardware-unsupported signals available.

**Shadow Mode is a strict boundary:** Phase 7B evidence is collected, stored,
trended, and displayed, but does not alter a genuine Risk Evidence Index,
System Health Score, root-cause rank, or predictive-alert severity. Future use
in scoring requires separate validation and an explicitly versioned policy.

Manual inspection commands:

```powershell
.\.venv\Scripts\python.exe -m agent.enhanced --status
.\.venv\Scripts\python.exe -m agent.enhanced --collect --force
.\.venv\Scripts\python.exe -m agent.enhanced --retention
```

## Counter rates

Windows exposes disk and network counters as totals since boot. SmartOps keeps
the previous in-memory value and timestamp, then calculates:

```text
rate per second = (current counter - previous counter) / elapsed seconds
```

The first sample after an agent start has null rate fields because there is no
earlier in-process counter. Counter resets also produce null rather than a fake
negative rate. Cumulative totals are retained for future feature engineering.

## Database history

The local database is `data\smartops.db`.

- Every core raw telemetry sample remains retained; Phase 7B never deletes
  from `metrics` or any Phase 1–7A operational/analytical table.
- Enhanced signal rows use a bounded policy: 30 days at collection frequency,
  then hourly min/average/max/first/last aggregates for 365 days. Enhanced
  structured-event evidence is retained for 365 days, collection-run overhead
  metadata for 30 days, and retention audit rows for 90 days.
- Existing Phase 1 records are preserved with null Phase 2A fields.
- `metrics` stores raw system records.
- `process_snapshots` stores top-process rows linked to a metric.
- `enhanced_signal_samples` and `enhanced_signal_hourly` store nullable,
  source-labelled shadow measurements and trends.
- `enhanced_event_evidence` stores deduplicated structured classifications;
  `enhanced_collector_state` discloses source availability and last success.
- `enhanced_collection_runs` measures duration, approximate whole-system-scale
  process CPU, process RSS, and database growth.
- `collection_cycle_audit` compactly links each future runtime session and
  scheduled raw slot to its actual start/completion, metric row, delay,
  enhanced-run status, skip/failure reason, and SQLite retry category. Records
  are retained for 30 days; an in-memory bounded queue bridges a temporary
  database outage without claiming that a missed sample was stored.
- `feature_windows` stores authoritative source count, first/last source IDs and
  timestamps, maximum internal gap, aggregation rule, and finalisation state.
  Append-only `feature_window_repair_events` records every audited correction.
- Current candidate links remain in `baseline_version_training_windows`;
  append-only `baseline_training_membership_events` records acceptance,
  rejection, audited removal, and reacceptance. An unchanged refresh writes no
  membership event and cannot silently reduce a count.
- Alert occurrence/evidence rows are written only for meaningful lifecycle or
  material-evidence changes. Unchanged evaluations update compact observation
  and last-seen metadata; unchanged feature windows keep their original update
  timestamp. Historical amplified rows are preserved for auditability and are
  not deleted by this migration.
- A metric and its process rows are inserted in one transaction.
- Timestamp and device/timestamp indexes support historical queries.
- The database and local device identifier remain ignored by Git.

The dashboard may request no more than 5,000 chart points at once for browser
performance. This does not remove or limit SQLite records.

## Local API

Interactive documentation is available while SmartOps runs:

**http://127.0.0.1:8000/docs**

Endpoints:

- `GET /api/status`
- `GET /api/settings/status`
- `GET /api/metrics/latest`
- `GET /api/metrics/history`
- `GET /api/metrics/count`
- `GET /api/processes/latest`
- `GET /api/enhanced-evidence/status`
- `GET /api/enhanced-evidence/history?signal=<signal-key>`
- `GET /api/enhanced-evidence/events`
- `GET /api/enhanced-evidence/overhead`

History query parameters:

| Parameter | Meaning |
| --- | --- |
| `limit` | Page size from 1 to 5000; default 50 |
| `offset` | Number of matching rows to skip |
| `start` | Optional ISO-8601 start timestamp |
| `end` | Optional ISO-8601 end timestamp |
| `sort` | `oldest` or `newest` |

Example:

```text
http://127.0.0.1:8000/api/metrics/history?limit=25&offset=0&sort=newest
```

The response includes `items`, `total`, `limit`, `offset`, `sort`, `start`, and
`end`, allowing reliable pagination.

## Dashboard behavior

The Phase 7B dashboard is one React application with safe hash navigation. Its
desktop layout uses a dark-blue left sidebar and header; below 900 pixels the
sidebar becomes an accessible compact menu. Hash routes survive localhost
refresh and work with browser Back and Forward. The classic light enterprise
style, accessible charts, scrollable tables, and genuine null/unavailable
states are preserved. No external fonts, assets, analytics, or tracking are
loaded.

Menu sections:

- **Overview** is the default route and summarizes current health, predictive
  alerts, risk evidence, PC Quality Check, workload, collection freshness,
  baseline readiness, the automatic pipeline, compact trends, recent alerts,
  and system identity.
- **Live Monitoring** contains CPU, RAM, swap/page-file, disk, network,
  optional temperature/GPU data, workload explanation, Windows events,
  processes, charts, filters, data coverage, paginated raw history, and the
  enhanced-evidence availability/trend workspace.
- **Predictive Alerts** contains automatic open, recovering, acknowledged, and
  resolved lifecycle evidence. Acknowledgement means only “I have seen this
  alert”; it does not resolve, recover, or verify an alert. Notification
  preferences are intentionally absent so this route remains focused on alert
  evidence, exact-alert deep links, filters, and lifecycle actions.
- **Root-Cause Analysis** lets the user inspect existing alert or eligible risk
  evidence and its probable factors, contradictory evidence, excluded inputs,
  persistence, events, diagnostic checks, and limitations. The generic Phase
  7B live-signal catalogue is intentionally absent; enhanced evidence appears
  here only when it is already relevant to the selected alert or risk result.
- **System Health** contains the current operating-condition score, band,
  confidence, components, deductions, guidance, exclusions, and history.
- **PC Quality Check** contains local hardware inventory, workload suitability,
  component results, gates/caps, limiting capabilities, generic guidance, and
  preserved assessment history. Hardware/configuration suitability remains
  separate from current operational health.
- **Research & Validation** is last and visibly optional. All Phase 5B manual
  forms live only here and are collapsed by default.
- **Settings** is a separated utility item at the bottom of desktop and mobile
  navigation. It owns browser display preferences, native notification
  preferences, monitoring/data facts, privacy boundaries, and system
  information.

The focused pages preserve all prior dashboard content, including:

- latest CPU, RAM, and system-disk values;
- total stored sample count, last timestamp, data age, and stale-agent warning;
- user, process, disk-rate, network-rate, battery, and optional sensor details;
- CPU, RAM, disk usage, disk transfer, and network transfer charts;
- last-hour, 6-hour, 24-hour, and all-data ranges;
- latest top CPU and memory process tables;
- a 25-row paginated raw history table;
- loading, empty, stale, unavailable-hardware, and API-error states.
- Phase 3A baseline/deviation readiness and historical Deviation Index;
- Phase 3B Risk Evidence Index prerequisites, auditable component
  contributions, temporal evidence, correlated events, ranked candidates,
  diagnostic verification steps, and historical evidence chart.
- Phase 4A System Health Score, evaluation state, data confidence, component
  scores, auditable deductions, excluded inputs, recovery evidence, diagnostic
  checks, and a historical chart with explicit not-evaluated gaps.
- Phase 4B PC Quality Check with workload selector, detected capability
  inventory, minimum/recommended comparisons, hard gates, limiting components,
  ranked generic guidance, and separate current operating readiness.
- Phase 5A predictive alerts with severity/state summaries, evidence,
  persistence and trend, contributing factors, excluded inputs, diagnostic
  verification, preventive guidance, acknowledgement, and resolved history.
- Phase 5B incident and alert-feedback forms, explicit observation-period
  controls, validation readiness, eligible/excluded evidence, reconstructable
  metrics, and append-only correction/withdrawal history.

Null readings remain visibly unavailable and are never displayed as fake zero
measurements.

## Phase 7A notifications and centralized Settings

SmartOps uses the native Windows toast interface locally through the
`windows-toasts` Python package. No cloud push service, browser notification,
email, SMS, or external API is used.

Automatic delivery is deliberately narrow:

- Phase 5A `warning` maps to the user-facing semantic level **High**;
- Phase 5A `urgent` maps to **Critical Evidence**;
- `advisory` maps to **Elevated** and is excluded by default;
- `informational` maps to **Low** and is excluded by default;
- collecting, not-evaluated, ineligible, unchanged, resolved, and historical
  conditions never produce an automatic toast.

Notification preferences use one exact internal category vocabulary:
`advisory`, `warning`, and `urgent`. Internal `informational` and `advisory`
alerts map to Advisory, `warning` maps to Warning, and `urgent` maps to Urgent.
Unknown or legacy severity strings are suppressed; display labels,
capitalization, and substring matches are never used. The master toggle and
the mapped category must both be eligible before dispatch. A
preference-suppressed alert remains visible in Predictive Alerts and creates
no delivery record.

The agent reserves a delivery record in SQLite before asking Windows to show a
toast. One lifecycle activation is submitted once. A later notification is
allowed only when that same lifecycle meaningfully escalates to a higher
eligible severity. A failed submission is recorded with its local reason and
is not rapidly retried, preventing duplicate loops. “Delivered” means the
Windows provider accepted the toast; it does not prove the user saw it.

Clicking a real toast opens:

```text
http://localhost:5173/#/predictive-alerts?alertId=<alert ID>
```

The dashboard validates the identifier, fetches the exact alert independently
of current filters, selects and scrolls to it, and shows the existing evidence,
Risk Evidence Index availability, contributing-factor candidates, diagnostic
checks, and root-cause workspace link. An invalid or missing identifier shows a
clear non-breaking message and never changes routes through injected text.

Notification controls now exist only at `#/settings`; Predictive Alerts has no
notification toggle, status, severity preference, or test control.

Changing the master preference from OFF to ON first persists the preference
and then submits one user-initiated configuration confirmation:

```text
Title: SmartOps
Message: Notifications have been enabled.
Link: http://localhost:5173/#/settings
```

It is not a test or predictive alert. It creates no alert, risk assessment,
incident, validation row, lifecycle change, or genuine
`notification_deliveries` row. Saving an unchanged enabled preference,
refreshing Settings, or turning notifications OFF sends no toast. Turning OFF
shows “Notifications have been disabled.” in the page.

The **Send Test Notification** button is a separate troubleshooting action in
Settings. It uses the title **SmartOps Test Notification** and message
**Windows notifications are working correctly.** It creates no alert,
incident, feedback, validation, risk, or production delivery record.

Preferences are stored in `notification_preferences`. Real attempts are stored
in `notification_deliveries`. The first Phase 7A preference row contains a
watermark so existing historical alerts are not replayed after migration.
Disabling and re-enabling notifications moves that watermark forward, so
conditions that became active while disabled are not delivered late.

Advanced local configuration:

```powershell
# Change the loopback dashboard origin used in future toast links.
$env:SMARTOPS_DASHBOARD_URL = "http://localhost:5173"

# First-run default categories. Add advisory only if deliberately wanted.
$env:SMARTOPS_NOTIFICATION_SEVERITIES = "warning,urgent,advisory"
```

`SMARTOPS_DASHBOARD_URL` accepts only local `http(s)` hosts (`localhost`,
`127.0.0.1`, or `::1`). Existing severity preferences can also be changed
through `PUT /api/notifications/preferences`. Settings exposes exact Advisory,
Warning, and Urgent category choices; Warning and Urgent are enabled by default
and selecting no category is a valid suppress-all policy while the master
preference remains independently stored.

Notification endpoints:

- `GET /api/notifications/status`
- `PUT /api/notifications/preferences`
- `POST /api/notifications/test`
- `GET /api/settings/status`

Automatic dispatch is not an API worker and no GET request sends a toast.

### Editable and read-only Settings values

The browser safely validates and stores these display preferences in
`localStorage`; invalid or malformed values fall back to defaults:

- default landing page and optional remembered last page;
- 12-hour or 24-hour time display;
- relative timestamps;
- automatic dashboard refresh, enabled by default at one 30-second timer;
- manual **Refresh Now** action.

Disabling browser refresh does not stop the telemetry agent, five-minute
feature aggregation, analytics, or notification dispatch. Notification
enablement and eligible Advisory/Warning/Urgent categories are durable SQLite
preferences.

### Optional personal-baseline recalibration

Settings contains **Personal Baseline**. The active baseline is stored as an
immutable version. **Start New Calibration** is optional and requires explicit
confirmation. It creates one separate candidate and learns only from complete,
quality-eligible analysis periods observed after that action. The active
baseline remains in use, monitoring continues, calibration never starts or
activates automatically, and the candidate cannot affect Deviation, Risk
Evidence, System Health, RCA, alerts, or notifications until the user separately
activates a valid ready version.

While a new calibration exists, the page reports active/new versions, observed,
accepted and excluded analysis-period counts, exact aggregated exclusion reasons, distinct days, sampling
completeness, applicability, last learning time, and blocking versus
informational readiness reasons. An ordinary incomplete or excluded window
remains auditable but does not keep a profile in `collecting_data` after its
required eligible-window and distinct-day thresholds are genuinely met.
Collection can be paused and resumed. A ready, unactivated calibration
shows **Continue Calibration**; this keeps the same candidate ID, version,
original start time and eligible windows while recording an auditable
continuation event. Ready means ready for validation, never active. Cancel,
activate and rollback actions require confirmation.
Unobserved workloads are distinguished from not-applicable workloads,
observed-but-insufficient profiles, and foreground collector/permission
limitations. Unused profiles, including gaming when it is not genuinely used,
do not block observed applicable profiles.

The Baseline Management **Idle** profile means at least five minutes of genuine
user-input inactivity. It does not require the whole computer to be quiet.
New samples independently store user activity (`active` or `idle`) and system
activity (`quiescent`, `background`, or `busy`), so SmartOps' own bounded work
or normal background activity cannot turn an inactive user into an active one.
Historical rows retain their original classifications; only new classification
results carry the Phase 7B.1 rule version and provenance.

Normal programming study does not need to be artificially separated. A new
complete window may additionally carry the secondary context
`guided_development` while retaining its existing majority-based primary
workload. With ten expected 30-second samples, the mixed context requires at
least two recognised development foreground samples, two recognised
browser/media foreground samples, and six combined samples (60% of the
window). This represents at least one minute in each context and three minutes
combined. Browser-only playback remains browser/media, pure VS Code remains
development, and background Code presence is ignored. SmartOps uses executable
categories only; it never reads browsing history, URLs, page/window titles, or
video content.

Candidate v2 can learn `guided_development` as another workload profile without
being restarted. Its row in Baseline Management reports observed, eligible and
excluded windows, collection days, completeness, applicability and readiness
reasons. Until that profile is ready, future baseline selection conservatively
falls back to the ready primary-workload profile and then the device profile.
Activation remains an explicit Settings action.

For a representative candidate, use the computer normally and safely across
several days: include idle periods, browsing, YouTube/video playback, VS Code
and coding, office work, ordinary multitasking, and ordinary heavier work. Do
not intentionally overheat, crash, or dangerously stress the laptop.
Recalibration adapts the learned operating pattern; it is not proof of improved
predictive accuracy. Existing safety/event evidence remains active throughout.

Baseline-management API:

- `GET /api/baseline-management/status`
- `POST /api/baseline-management/start`
- `POST /api/baseline-management/pause`
- `POST /api/baseline-management/resume`
- `POST /api/baseline-management/continue`
- `POST /api/baseline-management/cancel`
- `POST /api/baseline-management/activate`
- `POST /api/baseline-management/rollback`

The readiness table separates Profile status, Completeness, Blocking reason
and Additional information. Ready profiles show `None` as the blocker;
unobserved optional profiles show `None — not required`. Completeness may be
below 100% after eligible-window and distinct-day requirements are satisfied.
Excluded-window counts and reasons remain visible as audit information.

### Live graph interaction and capability-aware evidence

The desktop sidebar and top ribbon remain fixed while only the main content
scrolls. On smaller screens the sidebar becomes a keyboard-operable overlay
below the fixed ribbon. Each of the five Live Monitoring graphs can expand
without changing the selected history range. Expanded views support wheel and
pinch zoom, drag panning, Shift+drag range selection, a scrollable timeline,
Reset Zoom, Fit All and Return to Live. Crosshairs show exact local timestamps
and values. Queries remain bounded to 5,000 raw rows; rendering is capped at
1,200 points using endpoint and per-series bucket extrema so spikes remain
visible. Zooming uses the already-fetched higher-resolution subset.

The latest point pulses gently only when `/api/runtime/status` reports a fresh
running-agent heartbeat and raw telemetry is not stale. Paused, stale or
offline monitoring has no pulse. Reduced-motion preferences disable animation
while keeping the textual state.

Enhanced Evidence now displays the message “Only evidence supported by this
device is displayed.” Permanently unsupported optional capability results are
omitted from the active UI catalogue and their identical rows are not
repeatedly stored. Existing history remains intact. Timeouts, permission
limits, collector failures and temporary provider failures remain visible and
null; the API returns the filtered capability list and policy version for
audit. All retained enhanced evidence remains shadow-only.

Sampling interval, five-minute windows, analytical thresholds, baseline
eligibility, risk/health/alert logic, retention, data deletion, and agent
shutdown are read-only or intentionally unavailable. Settings truthfully shows
the production 30-second sample interval, five-minute feature windows, schema,
database/foreign-key status, latest collection, baseline readiness, native
provider support, and local-only processing. Full SQLite integrity checks are
maintenance checks rather than 30-second dashboard requests.

## Tests and build

Run Python tests:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Run frontend checks:

```powershell
Set-Location .\frontend
npm run test
npm run typecheck
npm run build
Set-Location ..
```

Tests use temporary databases and mocked hardware readings. They do not alter
`data\smartops.db`.

## Privacy boundaries

SmartOps stays on this PC. It does not collect or transmit:

- file or private document contents;
- browser history;
- typed text or clipboard contents;
- passwords, credentials, or authentication tokens;
- process command-line arguments;
- window titles;
- cloud telemetry.

Only the explicitly documented operational metrics are stored.

## Common Windows errors

### PowerShell says scripts are disabled

For locally created scripts, run:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Restart the VS Code terminal. On a managed school or work PC, ask the
administrator instead of changing organization policy.

### Python, Node, or npm is not recognized

Install the missing current release, select Python's **Add to PATH** option, and
restart VS Code completely.

### Port 8000 or 5173 is already in use

Stop the older SmartOps/Uvicorn/Vite terminal with Ctrl+C. `run_local.ps1`
expects those fixed local ports.

### Dashboard reports API unavailable

Open `http://127.0.0.1:8000/api/status`. If it does not return JSON, check the
supervisor terminal for an API startup error and restart `run_local.ps1`.

### Dashboard reports stale data

Confirm the supervised agent is still running. Data becomes stale when the
latest timestamp is more than 90 seconds old.

### Windows notification does not appear

Keep `run_local.ps1` running and check **Predictive Alerts > Windows alert
notifications**. Confirm notifications are enabled and Windows support reports
Available, then use **Send Test Notification** once. Windows Focus Assist/Do
Not Disturb, organization policy, a non-interactive session, or disabled
Windows notifications can suppress the visible toast even after the provider
accepts it. SmartOps records real delivery failures safely and telemetry
continues.

### Rates or optional hardware metrics are unavailable

The first sample after agent startup has no rate baseline. Battery,
temperature, GPU, and some counter values depend on local hardware and Windows
support. Foreground executable detection is supported and has worked on this
PC, but permissions or a non-interactive execution environment can make it
temporarily unavailable. SmartOps saves all remaining fields.

## Phase 2B events, workload, and features

The agent polls the local `System` and `Application` Windows Event Log channels
for Critical, Error, and selected operational Warning metadata. It reads at
most the newest 200 matching records per poll and stores a checkpoint per
channel. Generic warnings are retained only for a small allowlist of
system-oriented providers. The
`(channel, record ID)` uniqueness rule prevents duplicates and checkpoint reset
handling supports log rollover. No administrator privilege is required, though
Windows policy may deny a channel.

SmartOps stores only channel, provider, event ID, record ID, severity, UTC time,
mapped category, and a mapping-generated safe summary. It never stores raw XML
or the Windows message, which may contain usernames, paths, or document names.
Categories include power, hardware, storage, application crash, service
failure, resource exhaustion, and generic system/application warnings. An
event is operational evidence, not a predicted failure.

Workload classes are `idle`, `interactive_light`, `office_productivity`,
`browser_or_media`, `development`, `gaming_or_3d`, `compute_intensive`,
`background_activity`, and `unknown`. Rules use user state, foreground
executable name, CPU/GPU and I/O activity. For new samples, user input activity
and system activity are separate dimensions. `Code.exe` remains an explicit
development foreground process; observed `Brave.exe`, Chrome, Edge, Firefox,
and VLC executables are browser/media processes. The exact observed foreground
executable `VALORANT-Win64-Shipping.exe` is explicitly gaming/3D even when GPU
counters are unavailable. Riot Client and `vgc.exe` are not proof of gameplay
by themselves. Unknown executables fail transparently instead of using broad
substring matching. Confidence describes classification certainty, never
health. High utilization can be normal during development, gaming, 3D, or
compute work.

Feature aggregation uses fixed five-minute UTC boundaries and expects ten
30-second raw samples. Coverage is actual count divided by ten and capped at
100%; fewer than ten marks the window incomplete without inventing values.
Features include CPU,
RAM, swap, disk, network, activity, process, optional sensor, workload, missing
ratio, and event-count summaries. Standard deviation is population standard
deviation. New windows also store the workload distribution and explain when a
short development or gaming foreground interval loses the five-minute majority
to another workload. Raw samples and historical classifications remain
unchanged.

Continuous mode keeps raw sampling on the highest-priority owner. Exactly one
coalescing enhanced worker performs slow Windows queries outside writer
transactions, while Event Log and downstream analytics use one separate,
ordered maintenance worker on the five-minute cadence. Neither worker can
queue an unbounded backlog. Routine aggregation uses a bounded lookback only
to identify affected UTC window keys; each key is then rebuilt from the full
authoritative `timestamp >= start AND timestamp < end` raw range. The open
window is not finalised, and closed windows wait one minute for bounded
lateness. This prevents a partial rolling query from downgrading a complete
window and never fabricates missed samples.

Backfill or safely refresh all historical windows:

```powershell
.\.venv\Scripts\python.exe -m analytics.aggregate --backfill
```

The command is idempotent: running it twice re-reads the same authoritative raw
range and leaves an unchanged `(device, window start)` record untouched. A
late source row produces an explicit audited correction.

Read-only integrity comparison and the separately authorised audited repair are
available through:

```powershell
.\.venv\Scripts\python.exe -m analytics.repair --dry-run
```

`--apply` must be used only while SmartOps is stopped, after a verified SQLite
backup and review of the dry-run window list. It repairs only mismatches proven
against preserved raw telemetry. The diagnostic output also compares 8/9-row
windows with a proposed temporal-quality policy in shadow mode; that comparison
does not alter candidate-v2's strict ten-sample eligibility.

Additional local endpoints are:

- `GET /api/events` with time, channel, level, category, sort and pagination;
- `GET /api/events/summary`;
- `GET /api/workload/latest` and `GET /api/workload/history`;
- `GET /api/features/latest`, `GET /api/features/history`, and
  `GET /api/features/count`.

To verify Event Log access, start SmartOps and inspect
`http://127.0.0.1:8000/api/events/summary`. Each channel reports `available` or
`unavailable_or_permission_restricted`.

## Phase 3A device baseline and deviation

SmartOps learns from `feature_windows`, never directly from individual
30-second samples. A window is eligible only when it is complete, closed, has
at least 80% coverage, contains enough valid feature values, and is not marked
by invalid data. The default normal-baseline policy excludes windows containing
Critical events or mapped hardware, storage, or power events. These event
windows remain stored in history.

The primary profiles are per local device and per workload. A ready
workload-specific profile is preferred. If that profile lacks history, SmartOps
uses a ready device-wide profile and reports that fallback. It never silently
compares gaming with an idle profile.

Cold-start defaults are:

- 100 eligible windows for a provisional device-wide baseline;
- 30 eligible windows for a provisional workload baseline;
- at least three distinct collection days for `established`;
- seven days recommended and 14–30 days preferred.

Readiness states are `collecting_data`, `provisional`, `established`, `stale`,
`unavailable`, and `error`. Readiness describes training history, not PC
health. SmartOps does not generate a Deviation Index when readiness or data
quality is inadequate.

Each supported feature profile stores valid/missing counts, mean, population
standard deviation, median, median absolute deviation, minimum, maximum,
5th/25th/75th/95th percentiles, and interquartile range. Metric direction,
weight, robust scoring fallbacks, and severity thresholds are centralized in
`analytics/baseline_config.py`. Network activity is informational and is not
treated as unhealthy merely because it is high. Workload context prevents high
gaming, development, or compute CPU from automatically becoming a high alert.

The optional multivariate detector is a deterministic, local Isolation Forest.
It uses training-median preprocessing, never substitutes missing sensors with
zero, excludes identifiers/timestamps/event outcomes, and only runs with
sufficient eligible history. No pickle model is stored: versioned metadata is
saved and the model is deterministically reconstructed from SQLite. Isolation
Forest detects unusual combinations, not failures.

Commands:

```powershell
.\.venv\Scripts\python.exe -m analytics.baseline --status
.\.venv\Scripts\python.exe -m analytics.baseline --train
.\.venv\Scripts\python.exe -m analytics.baseline --evaluate
```

Use `--device`, `--workload`, or `--force` when intentionally narrowing or
re-evaluating derived results. Phase 7B.1 preserves the active baseline as an
immutable version. The running agent refreshes only a candidate explicitly
started in Settings and never silently replaces an established baseline. A
failed candidate refresh leaves the active version and prior snapshots intact.

Phase 3A endpoints:

- `GET /api/baseline/status`
- `GET /api/baseline/profiles`
- `GET /api/deviations/latest`
- `GET /api/deviations/history`
- `GET /api/deviations/{window_id}`
- `GET /api/deviations/count`

The dashboard section **Device baseline and deviation** shows readiness,
history requirements, current scope, data quality, statistical and Isolation
Forest components, expected ranges, contributing metrics, and historical
Deviation Index values.

> Deviation indicates a difference from this device's learned operating
> pattern. It is not a confirmed failure or failure probability.

## Phase 3B risk evidence and contributing factors

Phase 3B evaluates only completed Phase 3A deviation assessments backed by an
applicable provisional or established baseline. The feature window must have
at least 80% coverage, sufficient core metrics, and a valid workload context
with adequate confidence. If any prerequisite is missing, the result is
`not_evaluated`. A serious Windows event remains visible as operational
evidence but cannot manufacture a score during cold start.

The versioned catalogue in `analytics/risk_catalogue.py` centralizes issue
domains, required/supporting/contradictory evidence, workload exceptions,
persistence requirements, event mappings, metric direction, weights,
data-quality requirements, explanation templates, diagnostic checks, and
limitations. Domains include CPU, memory, possible memory growth, swap,
storage capacity/I/O, thermal, power, hardware, application/service
instability, resource exhaustion, unusual background activity, and
informational network context.

The deterministic fusion method combines:

- robust Phase 3A metric deviations, counting only the strongest signal in
  each correlated group;
- Isolation Forest unusual-pattern evidence when its model is ready;
- isolated, repeated, sustained, increasing, and recovering temporal patterns;
- mapped events occurring before, during, or shortly after a feature window;
- independent cross-metric corroboration;
- workload-aware reductions, such as CPU expected during development, gaming,
  or compute work;
- a bounded coverage penalty that never penalizes missing optional sensors.

Every component and its signed contribution is stored, so the final score can
be reconstructed. The resulting 0-100 **Risk Evidence Index** uses `low`,
`guarded`, `elevated`, `high`, and `critical_evidence` bands.

> Risk Evidence Index represents the strength of operational evidence
> associated with potential system problems. It is not a failure probability,
> confirmed failure, or confirmed root cause.

Ranked root-cause candidates contain confidence in evidence, supporting
metrics/events, contradictory evidence, workload context, first-observed time,
persistence, structured reasons, an explanation, recommended diagnostic
verification steps, and explicit limitations. They are hypotheses supported by
the available telemetry; SmartOps cannot prove causality and performs no
automatic remediation.

Commands:

```powershell
.\.venv\Scripts\python.exe -m analytics.risk --status
.\.venv\Scripts\python.exe -m analytics.risk --evaluate
.\.venv\Scripts\python.exe -m analytics.risk --backfill
```

The commands accept `--device`, `--start`, `--end`, and `--force` where
appropriate. Normal runs are transactional and idempotent. The agent
automatically checks new eligible windows after Phase 3A maintenance; a risk
evaluation error is logged without interrupting raw telemetry.

Phase 3B endpoints:

- `GET /api/risk/status`
- `GET /api/risk/latest`
- `GET /api/risk/history`
- `GET /api/risk/count`
- `GET /api/risk/{window_id}`
- `GET /api/root-causes/latest`
- `GET /api/root-causes/history`
- `GET /api/root-causes/{window_id}`

History and count endpoints support local device, workload, evidence-level,
start/end timestamp, safe limit/offset pagination, and sort filters. GET
requests are read-only and never trigger evaluation or backfill.

The catalogue structure is research-informed, but its current weights and
thresholds are SmartOps-specific engineering choices. Genuine predictive
performance cannot be measured until representative labelled normal and
failure-related operating histories are collected. Future validation must
measure calibration, precision, recall, false positives, workload bias, and
temporal generalization without weakening privacy boundaries.

## Phase 4A System Health Score

**System Health Score** summarizes the computer's current observed operating
condition from completed five-minute windows. Higher is better, from 0 to 100.
It remains separate from the other SmartOps outputs:

| Output | Meaning |
| --- | --- |
| System Health Score | Current observed operating condition |
| Deviation Index | Difference from the device's learned operating pattern |
| Risk Evidence Index | Strength of operational evidence associated with potential problems |
| PC Quality Check | Hardware/software suitability assessment; not part of Phase 4A |

> System Health Score summarizes the computer’s current observed operating
> condition using available telemetry, stability and operational evidence. It
> is not a failure probability, future-reliability guarantee or PC quality
> rating.

The Phase 3A and Phase 3B interpretation statements above continue to apply.

### Evaluation state and confidence

- `not_evaluated`: the window is incomplete, coverage is below 80%, a required
  core metric is unavailable, workload context is invalid, or data quality is
  inadequate. No fake zero score is produced.
- `provisional`: genuine core current-condition evidence supports a valid score,
  but a Phase 3A baseline, deviation assessment, or Phase 3B assessment is not
  established. It contains less historical evidence than an established result.
- `established`: current core inputs plus an established applicable baseline,
  completed deviation assessment, and Phase 3B assessment are available.

Data confidence is a separate 0–100 disclosure calculated from window coverage
(35%), required metric availability (25%), workload confidence (15%), optional
sensor availability (5%), and historical readiness (20%). A lower confidence
does not silently become a health deduction.

### Components, weighting, and normalization

The initial configured top-level weights are resource condition 45%, operating
stability 25%, operational-event condition 20%, and eligible learned evidence
10%. Each available component is scored from 0–100. When an optional or
historical component is unavailable, its weight is excluded and the weighted
mean is normalized across valid applicable components. The database records
available and excluded weights, effective weights, and the normalization method.
Scores calculated with different evidence availability may therefore not be
perfectly comparable.

Resource condition covers correlated CPU saturation, memory/swap pressure,
disk-capacity headroom, disk-I/O pressure, optional thermal evidence, and
applicable battery/power evidence. Operating stability evaluates persistence,
trend, repeated independent pressure, and recovery across recent completed
windows. Operational events use mapped Critical, Error, and carefully bounded
Warning evidence. Learned evidence can use eligible Phase 3A deviation and
Phase 3B risk results.

CPU summaries share one contribution group. RAM and swap deductions share a
cap. Read/write disk rates share one I/O group. Phase 3B serious-event
contribution is removed before its reduced learned-evidence contribution is
used, preventing a full event penalty twice. Both raw and effective deductions,
cap reasons, reason codes, supporting values, temporal metadata, explanations,
and diagnostic-only verification steps are stored.

Workload context reduces deductions for expected CPU or disk work during
development, gaming, compute, installation, or related activity when confidence
is adequate. Persistent idle CPU, memory accompanied by swap growth, low disk
headroom, and repeated mapped serious events can justify deductions. One
non-critical Warning does not create a health deduction by itself.

Missing CPU temperature or GPU sensors remain null and do not lower health.
Battery is `not_applicable` on devices without one. Optional absence is
disclosed and may affect data confidence; a missing required core input makes
the result `not_evaluated`.

Initial operational bands are:

- 85–100 `good`
- 70–84 `stable`
- 50–69 `attention`
- 30–49 `degraded`
- 0–29 `critical_condition`
- `not_evaluated` when no valid score exists

These weights, thresholds, formulas, caps, and bands are SmartOps-specific
engineering choices requiring empirical validation. They are not validated
industry standards, medical-style diagnoses, calibrated failure predictions,
or guarantees.

Commands:

```powershell
.\.venv\Scripts\python.exe -m analytics.health --status
.\.venv\Scripts\python.exe -m analytics.health --evaluate
.\.venv\Scripts\python.exe -m analytics.health --backfill
```

The commands support `--device`, `--start`, `--end`, `--window-id`, and
`--force`. Evaluation/backfill is transactional and idempotent for the same
window, algorithm/configuration version, feature update, and upstream evidence.
Normal agent operation checks newly completed windows after aggregation,
baseline maintenance, and risk processing. A health-evaluation error is logged
without interrupting raw telemetry.

Phase 4A read-only endpoints:

- `GET /api/health/status`
- `GET /api/health/latest`
- `GET /api/health/history`
- `GET /api/health/count`
- `GET /api/health/{window_id}`

History/count support device, workload, health-band, evaluation-state,
start/end time, sort, safe limit/offset pagination, and a total matching count.
The window endpoint includes component and score reconstruction, deductions,
input availability, upstream references, temporal evidence, explanations,
diagnostic checks, and limitations. GET requests never run evaluation or
backfill.

The health dashboard keeps not-evaluated gaps disconnected in the historical
chart, uses text alongside color, and reserves conventional red for evaluated
`degraded` or `critical_condition` results. Provisional and unavailable states
are not shown as failures.

The System Health Score has not been clinically, commercially, or industrially
validated. Research-supported monitoring principles include explicit missing
data, workload context, temporal corroboration, transparent contributions, and
separation of observation from prediction. The actual SmartOps weights,
thresholds, bands, and formulas are project engineering choices. Labelled
real-world validation is still required before making predictive-performance
claims.

## Phase 4B PC Quality Check

The current PC Quality Check interface presents two deliberately separate
measures:

- **Current Workload Headroom** is the stored 30-profile operating-headroom
  result for the most recent qualifying five-minute observation. An exact
  `100.0` means all measured factors in that observed period remained within
  their expected ranges; it does not mean perfect hardware or 100% accuracy.
  Scores display to one decimal place, while Technical Details retains the
  stored precision and formula/version provenance.
- **Hardware Workload Suitability** is the original six-scenario capability
  assessment described below. It is never used as a fallback for unavailable
  Device Current Workload Headroom.

SmartOps also separates **Current Evidence Quality** (period completeness,
workload-detection confidence, available measurements, and baseline
availability) from **History Depth** (qualifying observations, distinct days,
latest qualifying observation, and independent post-activation observations).
A single qualifying period is labelled limited history. Baseline-training
evidence remains visible but is not described as independent validation.

> SmartOps observes how the computer behaves during a recognized workload. The
> headroom score shows whether measured resources remained within expected
> ranges during the selected period. A score of 100.0 means no measured factor
> crossed its expected range; it does not mean 100% accuracy or perfect
> hardware. Profiles without sufficient recognized evidence remain Not
> observed or Not evaluated instead of receiving an estimated score.

**PC Quality Check** compares safely detected local hardware and Windows
capabilities with a selected, versioned workload profile. Its 0-100 **Workload
Suitability Index** indicates how closely those capabilities align with that
one profile. Scores from different profiles describe different tasks and must
not be treated as one universal ranking.

> PC Quality Check estimates the computer’s suitability for a selected workload
> using detected hardware and system capabilities. It is not a benchmark
> result, failure prediction, future-reliability guarantee or confirmation that
> every application will run successfully.

The four outputs remain deliberately separate:

| Output | Meaning |
| --- | --- |
| System Health Score | Current observed operating condition |
| Deviation Index | Difference from the device's learned operating pattern |
| Risk Evidence Index | Strength of operational evidence associated with potential problems |
| PC Quality Check | Hardware and system suitability for a selected workload |

The latest Phase 4A health and Phase 3B risk results may appear in **Current
operating readiness**, but they do not alter the stored suitability index.
Current CPU, RAM, GPU, or disk utilization is not scored as hardware
capability.

### Safe local inventory and privacy

The allowlisted inventory contains processor name/architecture/core counts,
reported clock and virtualization support; installed/usable RAM and module
count; system-drive capacity/free space, safely detected media type, and local
drive count; GPU name/classification/memory/driver when reliable; and Windows
edition/version/build/architecture. It uses only the existing locally generated
SmartOps device identifier.

SmartOps does **not** request or store hardware serial numbers, Windows product
keys, usernames, owner names, MAC/IP addresses, file contents, window titles,
browser or installed-application history, command lines, typed text, or
passwords. Inventory is collected on first startup and checked at most once
per 24 hours during normal operation. An unchanged signature reuses the
existing snapshot; a meaningful change creates a new one. Inventory or
quality-evaluation failure never stops telemetry.

Every expected safe field is classified as `available`,
`unavailable_optional`, `unavailable_required`, `unreliable`, or
`not_applicable`; privacy exclusions are separately disclosed as
`excluded_for_privacy`. Unknown GPU memory is never zero. Unknown storage media
is never assumed to be an HDD. A missing required field produces
`not_evaluated`; important uncertainty produces `provisional`; reliable
required inputs produce `assessed`.

### Profiles and calculation

The audited catalogue in `analytics/quality_catalogue.py` contains:

- `everyday_productivity`
- `software_development`
- `data_analysis_and_light_ml`
- `local_ai_and_gpu_compute`
- `content_creation`
- `modern_3d_gaming`

Each profile has distinct required/optional components, weights, minimum and
recommended thresholds, mandatory graphics/architecture gates, bottleneck
caps, explanations, limitations, and version/source metadata. Integrated
graphics is acceptable for non-GPU profiles. A reliably detected dedicated GPU
is a hard requirement for local GPU compute and modern 3D gaming.

Each applicable component is scored once; physical and logical cores are not
double-counted, and total storage is not mixed with current free-space
readiness. Valid weights are normalized, then explicit hard gates and
bottleneck caps prevent a strong component from hiding a mandatory failure.
SQLite stores raw/effective component scores, configured/effective weights,
minimum/recommended results, gates, caps, reconstruction data, limitations,
and ranked generic verification or upgrade guidance.

Initial suitability bands are:

- 90-100 `well_suited`
- 75-89 `suitable`
- 60-74 `suitable_with_limits`
- 40-59 `upgrade_recommended`
- 0-39 `insufficient`
- `not_evaluated` when a safe score cannot be calculated

These thresholds, weights, bands, and general workloads are SmartOps-specific
engineering assumptions, not benchmark percentiles or universal industry
standards. They require labelled real-world performance validation.
Software-specific profiles added later must cite verified official minimum and
recommended requirements.

### Commands and local API

```powershell
.\.venv\Scripts\python.exe -m analytics.quality --status
.\.venv\Scripts\python.exe -m analytics.quality --inventory
.\.venv\Scripts\python.exe -m analytics.quality --evaluate
.\.venv\Scripts\python.exe -m analytics.quality --evaluate --profile software_development
.\.venv\Scripts\python.exe -m analytics.quality --refresh-inventory
```

Use `--device`, `--profile`, `--all-profiles`, `--force`, and `--json` where
appropriate. Evaluation is transactional and idempotent for the same device,
inventory, workload/profile version, algorithm version, and configuration
version.

Phase 4B read-only endpoints:

- `GET /api/quality/status`
- `GET /api/quality/profiles`
- `GET /api/quality/inventory/latest`
- `GET /api/quality/latest`
- `GET /api/quality/history`
- `GET /api/quality/count`
- `GET /api/quality/{assessment_id}`

History/count support safe limit/offset pagination, sorting, timestamps, device,
workload profile, suitability result, and evaluation-state filters. Responses
include component comparisons, gates/caps, reconstruction, detection
confidence, excluded inputs, limiting components, ranked guidance, versions,
and current-readiness references. GET requests never collect inventory,
evaluate profiles, or write to SQLite.

## Phase 5A local predictive alerts

Phase 5A converts eligible operational evidence into durable local alert
lifecycles. It does not use Phase 4B PC Quality Check to create recurring
alerts. Every alert begins with a completed five-minute feature window with at
least 80% coverage and a genuine evaluated Phase 4A health assessment.
Provisional health evidence is allowed but remains visibly provisional.
Incomplete, inadequate, and `not_evaluated` evidence never produces an alert.

> SmartOps alerts indicate observed operational evidence that may require
> attention. They are not guaranteed predictions of failure, confirmed
> hardware diagnoses, or calibrated probabilities of a future crash.

The versioned catalogue covers resource pressure, correlated memory/swap
pressure, disk capacity, disk I/O, available thermal evidence, explicitly
mapped serious Windows events, stability, eligible increasing Phase 3B risk
evidence, degraded Phase 4A condition, and evaluated data-quality limitations.
Missing temperature/GPU inputs and non-applicable hardware are disclosed, not
turned into zero or alert evidence.

Severity is `informational`, `advisory`, `warning`, or `urgent`. These are
operational interpretations, not probabilities. Ordinary CPU, memory,
disk-I/O, thermal, stability, risk, and data-quality signals require configured
persistence; a mapped Critical event may be immediately urgent. High CPU or
disk activity receives a workload exception only when workload confidence is
sufficient. Low-confidence context is interpreted conservatively.

One deterministic fingerprint represents a continuing correlated condition.
CPU average/p95/high-ratio evidence contributes through one CPU group;
RAM/swap through one memory group; and Phase 3B evidence already represented
through Phase 4A is not fully counted again. Every raw/effective contribution,
suppressed input, reason, upstream reference, occurrence, and transition is
stored. Two subsequent clear completed windows provide hysteresis for recovery
and resolution. A genuinely returning resolved condition creates a linked new
lifecycle after cooldown.

Alert states are:

- `open`: evaluated evidence currently supports the condition;
- `acknowledged`: the user has seen it, but the condition remains;
- `recovering`: later completed windows no longer support it;
- `resolved`: configured recovery evidence is complete; history is retained.

Commands:

```powershell
.\.venv\Scripts\python.exe -m analytics.alerts --status
.\.venv\Scripts\python.exe -m analytics.alerts --evaluate
.\.venv\Scripts\python.exe -m analytics.alerts --backfill
.\.venv\Scripts\python.exe -m analytics.alerts --list-open
```

Use `--device`, `--start`, `--end`, `--window-id`, `--severity`, `--state`,
`--force`, and `--json` where supported. Evaluation/backfill is transactional
and idempotent. An alert failure is isolated from raw telemetry collection.

Phase 5A API:

- `GET /api/alerts/status`
- `GET /api/alerts/latest`
- `GET /api/alerts/history`
- `GET /api/alerts/count`
- `GET /api/alerts/{alert_id}`
- `POST /api/alerts/{alert_id}/acknowledge`

History/count support limit/offset pagination, sorting, device, time, category,
severity, state, and workload filters. GET requests are read-only and never
evaluate alerts. The POST endpoint only records acknowledgement; it cannot
resolve the underlying condition. CORS remains limited to the local Vite
origin.

The alert thresholds, bands, persistence, cooldown, and workload exceptions
are transparent SmartOps-specific engineering choices. Temporal monitoring,
correlation, missing-data disclosure, and evidence preservation are
implemented and tested; predictive usefulness, calibration, precision, recall,
and operating thresholds still require representative labelled real-world
validation. No external notification or automatic remediation is performed.

## Phase 5B incident feedback and predictive validation

Phase 5B evaluates SmartOps alerts only against available, explicit outcome
evidence. It does not retroactively claim that unlabelled history was
incident-free.

> SmartOps validation results are based on available user-reported or
> externally verified outcomes. They do not by themselves establish guaranteed
> failure prediction, hardware diagnosis, or universally validated accuracy.

The local dashboard can:

- start and explicitly close a validation observation period;
- record a structured incident category, severity, exact/approximate UTC time,
  workload context, safe symptom summary, optional mapped Windows-event
  references, action and outcome;
- record or revise an alert outcome as confirmed related issue, no issue
  observed, preventive action taken, or uncertain;
- append incident corrections and preserve withdrawn reports;
- show eligible/excluded evidence, validation readiness, precision, recall,
  accuracy, balanced accuracy, warning lead time, and minimum-evidence status.

Incident categories are `system_crash`, `unexpected_restart`,
`application_failure`, `system_freeze`, `severe_slowdown`,
`memory_exhaustion`, `disk_capacity_issue`, `disk_io_issue`,
`thermal_shutdown_or_throttling`, `driver_or_device_issue`,
`repeated_serious_event`, and `other_operational_issue`. Verification is
`user_reported`, `externally_verified`, `uncertain`, or `withdrawn`.
User-reported evidence describes an observed operational outcome and does not
prove a hardware defect.

Alert feedback supports `confirmed_related_issue`, `likely_related_issue`,
`no_issue_observed`, `preventive_action_taken`, `uncertain`,
`not_yet_verified`, `incorrect_category`, and `withdrawn`. Feedback stores its
verification time/source, observation horizon, user-selected reason codes,
structured action, continued/recovered/unclear state, and revision lineage.
Feedback never acknowledges, resolves, recovers, or reopens the Phase 5A alert;
alert lifecycle and validation outcome remain separate.

All notes are local user-entered fields. Do not enter passwords, command lines,
file/document contents, typed text, browser history, credentials, window
titles, raw Windows event messages, or other private data. SmartOps stores only
the structured fields submitted through the local form/API.

### Evidence eligibility and labels

- A **true positive** requires verified incident evidence, a user-confirmed
  alert-to-incident link, and confirmed-related-issue alert feedback.
- A **false positive** requires verified `no_issue_observed` feedback, no
  preventive-action confounder, and at least the configured observation
  horizon (initially 24 hours).
- A **false negative** requires a verified incident inside an eligible,
  explicitly completed observation period without a confirmed alert link.
- A **true negative** is possible only for a complete five-minute window inside
  an explicitly closed period whose incident reporting was declared complete
  and whose eligible telemetry coverage is at least 80%.
- Unverified, uncertain, withdrawn, incomplete, interrupted, low-coverage, or
  inadequately observed evidence is excluded with stored reason codes.

Automatic matching uses versioned category, time, and workload rules to create
only probable, possible, or rejected candidates. It never confirms causality.
A `confirmed_match` requires explicit user confirmation. Warning lead time uses
the alert's first-active timestamp; a negative value is disclosed as late
detection.

Default headline publication minimums are 20 verified alerts for precision, 10
verified incidents for recall, 100 eligible windows across at least seven
distinct observation days for accuracy/balanced accuracy, and five confirmed matches for mean lead
time. If a requirement is unmet, the metric is null with
`insufficient_labeled_evidence`; it is not displayed as 0%. Metrics are also
stored by supported category, severity, workload, and alert
algorithm/configuration cohort. These thresholds and matching weights are
versioned SmartOps engineering choices requiring representative external
validation.

Reconstructable formulas use separate denominators:

- precision = true-positive alerts / (true-positive + false-positive alerts);
- false-alert proportion = false-positive alerts / verified classified alerts;
- recall = detected confirmed incidents / (detected + missed incidents);
- accuracy = (true-positive + true-negative windows) / eligible labelled
  windows;
- balanced accuracy = (incident-window sensitivity + no-incident-window
  specificity) / 2;
- warning lead time = incident start UTC - alert first-supported-observation
  UTC.

Median, minimum, maximum, late-detection count, no-warning count, feedback
completion, observation coverage, and raw TP/FP/FN/TN counts are stored
separately. Validation confidence is reported as `high`, `moderate`, `limited`,
or `insufficient` from verification source, timestamp precision, eligible
coverage, labelled sample volume, and distinct observation days; it is not
alert severity or a performance metric.

Commands:

```powershell
.\.venv\Scripts\python.exe -m analytics.validation --status
.\.venv\Scripts\python.exe -m analytics.validation --evaluate
.\.venv\Scripts\python.exe -m analytics.validation --backfill
.\.venv\Scripts\python.exe -m analytics.validation --summary
.\.venv\Scripts\python.exe -m analytics.validation --list-incidents
.\.venv\Scripts\python.exe -m analytics.validation --list-unverified-alerts
```

Generate a read-only experimental/PPT summary with:

```powershell
.\.venv\Scripts\python.exe -m analytics.evaluation_report
```

Add `--json` for machine-readable output. This command reads only the latest
stored validation run; it neither creates labels nor runs validation. It
reports window-level TP/TN/FP/FN and class counts plus accuracy, balanced
accuracy, precision, recall, specificity, F1, FPR, and FNR only when the
evidence and publication rules are satisfied. Otherwise results remain `Not
currently calculable`. SmartOps has no calibrated binary prediction
probability, so Average Prediction Confidence is `Not currently measurable`;
Risk Evidence Index and alert evidence confidence must not be relabelled as
prediction confidence.

`--device`, `--start`, `--end`, `--force`, and `--limit` are available where
appropriate. Evaluation/backfill uses an evidence signature, is transactional,
is idempotent for unchanged evidence, and rolls back incomplete writes.
Validation is intentionally separate from the 30-second telemetry transaction,
so invalid feedback or an evaluation failure cannot stop raw collection.

Read-only Phase 5B API:

- `GET /api/validation/status`
- `GET /api/validation/summary`
- `GET /api/validation/history`
- `GET /api/validation/metrics`
- `GET /api/validation/lead-times`
- `GET /api/validation/feedback`
- `GET /api/validation/unverified-alerts`
- `GET /api/validation/periods`
- `GET /api/validation/observation-periods`
- `GET /api/incidents`
- `GET /api/incidents/history`
- `GET /api/incidents/{incident_id}`
- `GET /api/alerts/{alert_id}/feedback`

Local evidence writes:

- `POST /api/incidents`
- `POST /api/incidents/{incident_id}/revise`
- `POST /api/incidents/{incident_id}/withdraw`
- `POST /api/incidents/{incident_id}/link-alert`
- `POST /api/alerts/{alert_id}/feedback`
- `POST /api/alerts/{alert_id}/feedback/revise`
- `POST /api/validation/observation-periods`
- `POST /api/validation/observation-periods/{period_id}/close`
- `POST /api/validation/periods`
- `POST /api/validation/periods/{period_id}/close`

Write payloads have strict enumerations, timestamp and length checks, and an
explicit `confirmation: true` field. Revisions are append-only. GET requests
never trigger matching, evaluation, or backfill. CORS remains limited to the
local Vite development address.

No genuine incident or alert-feedback record is created automatically. Test
scenarios use isolated temporary SQLite databases only.

## Current limitations

- Event polling is bounded; extremely high event volume between polls can
  exceed the newest-200 safety window.
- Workload context is rule-based and intentionally conservative.
- Meaningful baseline evaluation requires days of representative local history.
- Time-of-day/day-of-week profiles and full concept-drift adaptation are
  deferred. Controlled rebuilds currently provide conservative drift protection.
- Optional sensor coverage varies across Windows hardware.
- The user active/idle threshold is currently five minutes.
- The browser chart view is limited to the newest 5,000 matching points.
- Core operational and analytical history has no automatic deletion. Only the
  additive Phase 7B enhanced tables use their documented bounded
  raw-to-hourly retention policy.
- Vite is a development server; desktop packaging is deferred.
- Isolation Forest detects unusual combinations only. Risk Evidence Index is
  not a calibrated probability, and root-cause candidates do not prove
  causality.
- The System Health Score is an unvalidated engineering summary of observed
  condition; scores made with different available evidence may not be perfectly
  comparable.
- Windows may report GPU memory or storage media ambiguously; such values stay
  unreliable or unavailable and are disclosed rather than invented.
- PC Quality Check performs no benchmark or stress test. It cannot guarantee
  application compatibility, performance, future reliability, or a commercial
  upgrade benefit.
- Predictive alerts are unvalidated evidence interpretations. They are not
  confirmed diagnoses, calibrated future-crash probabilities, or guarantees.
- Phase 5B validation remains `insufficient_labeled_evidence` until enough
  explicit, representative outcomes and completed reporting periods exist.
  User reports can contain recall or timing error, observation periods can be
  incomplete, preventive actions can confound outcomes, and the initial
  matching/publication thresholds are not externally validated.
- Phase 7B preserves Phase 7A Windows desktop toasts only while the
  supervisor/agent is running. Windows policy, Focus Assist, and
  non-interactive sessions can suppress them. There is no background Windows
  service or executable package.
- Email, SMS, browser push, Slack, and cloud notification channels are
  deliberately excluded.
- Storage reliability, battery design capacity, paging-duration, and explicit
  I/O-stall-duration values depend on drivers/hardware and may remain
  unavailable. Process restart counting is not fabricated when Windows process
  creation auditing is unavailable.
- There is no guaranteed crash prediction, calibrated failure probability,
  automatic remediation, authentication, or cloud backend.

## Future phases

### Future validation and integration

- collect a larger representative, privacy-safe labelled dataset across
  devices and operating conditions;
- externally review labels and validate/recalibrate SmartOps-specific matching,
  alert, and publication thresholds;
- add calibration plots or probabilistic outputs only after enough
  independently verified evidence exists;
- continue improving the existing local hash-route integration without
  introducing server-side routing or cloud services;
- retain bounded language and diagnostic-only recommendations.

Calibrated failure probability, guaranteed crash prediction, confirmed
root-cause conclusions, automatic remediation, Remaining Useful Life,
benchmarking, and commercial product recommendations remain deliberately
excluded.
>>>>>>> 8145153 (first push)
#   m a j o r - p r o j e c t  
 #   m a j o r - p r o j e c t  
 