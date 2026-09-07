# Analytics extension point

This package implements SmartOps analytics through Phase 5B while Phases 7A,
7B, and 7B.1 preserve the shadow-only analytical boundary:

- `workload.py` contains transparent operational-context rules.
- `aggregate.py` creates fixed UTC five-minute windows and uses population
  standard deviation.
- `baseline_config.py` centralizes eligibility, readiness, feature direction,
  weighting, severity, and Isolation Forest settings.
- `baseline.py` builds robust device/workload profiles and evaluates later
  windows without training-target leakage.
- `recalibration.py` manages immutable active versions and an explicit,
  workload-aware candidate lifecycle without automatic activation.
- `risk_catalogue.py` is the versioned, auditable evidence catalogue.
- `risk.py` fuses eligible Phase 3A results into reconstructable Risk Evidence
  Index assessments and ranked contributing-factor candidates.
- `health_config.py` centralizes versioned health weights, bands, thresholds,
  caps, confidence rules, and diagnostic text.
- `health.py` evaluates completed feature windows into reconstructable current
  operating-condition assessments.
- `quality_catalogue.py` centralizes versioned workload profiles, thresholds,
  weights, gates, caps, interpretation bands, and source metadata.
- `quality.py` collects a strict local inventory allowlist and produces
  reconstructable workload-suitability assessments.
- `alert_catalogue.py` centralizes versioned categories, severity,
  persistence, recovery, cooldown, workload exceptions, explanations, and
  diagnostic-only guidance.
- `alerts.py` turns eligible completed-window evidence into transactional,
  idempotent alert lifecycles.

Phases 7A, 7B, and 7B.1 do not add another analytical score or let enhanced
evidence change alert eligibility.
After `alerts.py` completes, the existing agent invokes one replaceable native
notification dispatcher. It maps stored `warning` to semantic High and
`urgent` to Critical Evidence, then uses durable delivery state to avoid
duplicates. Centralized user preferences remain on the Settings route without
creating another worker or analytical dependency. Notification failures never
roll back or interrupt analytics.

Phase 7B enhanced evidence is intentionally outside the analytical input path.
The agent stores capability-aware Windows counter, hardware, battery, and
structured-event rows in schema-11 `enhanced_*` tables. Schema 12 adds schedule
provenance, compact alert observation audit, exact notification decisions, and
versioned baseline-management tables. Schema 13 adds workload rule provenance,
separate user/system activity context, transparent majority-window metadata,
and baseline applicability/exclusion audit fields. Schema 14 adds structured
foreground composition, secondary mixed-context provenance, and workload-rule
version fields on future assessments. Neither `risk.py`,
`health.py`, nor `alerts.py` reads those tables. This makes the Shadow Mode
boundary mechanically testable: collecting enhanced evidence cannot change
Risk Evidence, System Health, root-cause ranking, or alert severity. Any future
promotion into scoring requires labelled validation and a new explicit
algorithm/configuration version.

Schema 15 adds only `baseline_versions.learning_state`. Candidate lifecycle
(`candidate`, `ready`, `active`, etc.) is separate from collection state
(`collecting`, `paused`, `ready_for_validation`, `inactive`). A user-requested
continuation of a ready candidate records `continued` in
`baseline_recalibration_events` and preserves its identity, original start and
training-window associations. Maintenance refreshes only a candidate whose
learning state is explicitly `collecting`.

Schema 16 does not change baseline, deviation, risk, health, or alert formulas.
It adds durable single-agent ownership guards and uses the shared SQLite WAL,
busy-timeout, writer-serialization, and bounded lock-retry policy. Expensive
analytics stay outside write transactions where practical; Phase 7B evidence
remains shadow-only.

Schema 17 makes every closed five-minute feature authoritative. A bounded
maintenance query identifies UTC keys, then aggregation re-reads each complete
half-open raw range. Finalisation waits one minute; provenance stores source
count, ID/timestamp range, maximum gap, rule version and correction state.
Repairs are append-only audited, and a finalised feature cannot be downgraded by
a partial lookback. Candidate learning consumes finalised windows only and
reconciles current training membership through append-only accepted, rejected,
removed-after-correction and reaccepted-after-repair events. Repeating an
unchanged refresh produces no link, count, or audit change.

```powershell
.\.venv\Scripts\python.exe -m analytics.aggregate --backfill
.\.venv\Scripts\python.exe -m analytics.repair --dry-run
```

Coverage is `actual sample count / 10`, capped at 100%; fewer than ten samples
makes a window incomplete. Missing sensors remain null and have explicit
missing ratios.

The strict ten-sample candidate-v2 policy remains unchanged. The repair command
reports a shadow-only temporal-quality comparison for 8/9-row windows using at
least 80% count and span coverage, first/last boundary position, maximum gap,
required core values, candidate pause overlap and safety-event policy. It does
not change membership or scoring; historical runtime continuity may only be
inferred when immutable cycle provenance predates schema 17.

Train, inspect, and evaluate local baselines:

```powershell
.\.venv\Scripts\python.exe -m analytics.baseline --train
.\.venv\Scripts\python.exe -m analytics.baseline --status
.\.venv\Scripts\python.exe -m analytics.baseline --evaluate
```

Eligible baseline inputs are closed, complete five-minute windows with at least
80% coverage and enough valid values. By default, Critical and serious
hardware, storage, or power event windows are excluded. Highly deviating
windows are not immediately absorbed during the next controlled rebuild.

Profiles are per device and per workload. A workload profile needs 30 eligible
windows; the device profile needs 100. Three distinct collection days are
required for `established`. Seven days are recommended and 14–30 days are
preferred. Until then, readiness remains `collecting_data` or `provisional`.

Candidate recalibration reports excluded windows and exact reason totals, but
ordinary exclusions are informational after the profile has enough eligible
windows and distinct days. It distinguishes applicable,
observed-insufficient, not-observed, not-applicable, and
collector/permission-limited profiles. Unused profiles do not block a
candidate with sufficient applicable representative evidence.

`workload.py` classifies new samples with rule version
`phase7b1-workload-v3`. User inactivity (at least 300 seconds without input) is
independent of system quiescence. System activity retains the conservative
quiescent thresholds and is stored as quiescent/background/busy. Explicit
foreground catalogues recognize Code for development, observed browser/media
executables including Brave, and the exact Valorant game executable for
gaming/3D. Launchers, services, background editor presence, and broad name
substrings do not establish a foreground workload. Five-minute aggregation
stores counts and a human-readable majority explanation without rewriting old
classifications.

Guided development is a secondary context, not a replacement for the primary
majority label. A complete ten-sample window needs at least two development and
two browser/media foreground samples plus at least six combined samples. These
thresholds correspond to one minute in each context and three minutes combined
at the production 30-second cadence. Composition stores counts and proportions
for all primary profiles. It uses only the already classified foreground
executable; browser content and background Code processes are excluded.

Baseline recalibration includes `guided_development` in the existing candidate
version. A ready guided profile is preferred for a guided window; otherwise
selection falls back to the ready primary profile and then the device profile.
The feature-window, assessment workload-rule version, baseline version and
baseline rule version keep the decision reconstructable.

Each feature stores mean, population standard deviation, median, MAD, min/max,
5th/25th/75th/95th percentiles, IQR, and missing counts. Robust z-score is used
first, followed by IQR or standard-deviation fallback when needed.

Isolation Forest uses an explicit operational feature list, deterministic seed,
training-median preprocessing, and no event outcomes or identifiers. Missing
sensors are never converted to zero. SmartOps stores versioned metadata rather
than unsafe Python pickle files and deterministically retrains from SQLite.

The Deviation Index describes how different a window is from this device's
learned pattern. It is not failure risk, failure probability, crash prediction,
or a Health Score.

## Phase 3B evidence fusion

Run:

```powershell
.\.venv\Scripts\python.exe -m analytics.risk --status
.\.venv\Scripts\python.exe -m analytics.risk --evaluate
.\.venv\Scripts\python.exe -m analytics.risk --backfill
```

`--device`, `--start`, `--end`, and `--force` can deliberately narrow or
refresh derived assessments. Evaluation and backfill are idempotent for the
same feature window, algorithm/configuration version, baseline update, and
Phase 3A deviation assessment.

Evaluation requires a completed five-minute window, at least 80% coverage,
sufficient core metrics, valid workload context with adequate confidence, a
provisional or established applicable baseline, and a completed, sufficient
Phase 3A deviation assessment. Missing any prerequisite returns
`not_evaluated`; an event never bypasses cold-start protection.

The deterministic score stores every contribution needed for reconstruction:

- one strongest signal per configured correlation group;
- optional Isolation Forest unusual-pattern evidence;
- consecutive-window persistence and increasing/recovery trend;
- mapped local Windows events correlated before, during, or after the window;
- independent cross-metric corroboration;
- workload-compatibility reductions;
- coverage-based data-quality penalty.

CPU average, maximum, and p95 share one group, as do other correlated
summaries, so they cannot inflate the score as independent failures. High CPU
during development, gaming, or compute work is reduced as workload-compatible;
high CPU while idle can support unusual-background-activity evidence. Network
activity alone remains informational. Missing CPU temperature or GPU sensors
are recorded as unavailable and never reduce health or become zero.

The 0-100 **Risk Evidence Index** uses the bands low, guarded, elevated, high,
and critical evidence. It measures accumulated operational evidence, not a
calibrated probability. Candidates contain supporting and contradictory
evidence, timestamps, persistence, reason codes, explanations, diagnostic
verification steps, and limitations. They are evidence-supported hypotheses,
not confirmed root causes, and SmartOps performs no remediation.

The catalogue and thresholds are SmartOps-specific engineering choices
informed by robust statistics, temporal correlation, and interpretable
evidence-combination techniques. They are not clinically or industrially
validated failure thresholds. Genuine predictive performance, calibration,
precision, and recall cannot be measured until representative labelled normal
and failure-related histories are collected under a documented validation
protocol.

## Phase 4A explainable health assessment

Run:

```powershell
.\.venv\Scripts\python.exe -m analytics.health --status
.\.venv\Scripts\python.exe -m analytics.health --evaluate
.\.venv\Scripts\python.exe -m analytics.health --backfill
```

The evaluator consumes completed five-minute windows. Coverage below 80%,
missing CPU/RAM/disk core inputs, or invalid workload context creates an
explicit `not_evaluated` assessment with a null score. Genuine current core
evidence can create a `provisional` result during baseline cold start. Only
eligible Phase 3A and Phase 3B evidence plus established baseline readiness can
create `established`.

The top-level components are resource condition, operating stability,
operational events, and eligible learned evidence. Available components are
weighted and normalized; excluded weight and every input availability state
are stored. Temperature/GPU absence never becomes zero, and a device without a
battery is not penalized. Data confidence separately discloses coverage, core
availability, workload confidence, optional sensors, and historical readiness.

Correlated signals use named groups and caps. Phase 3B event overlap is removed
before its learned contribution is applied. Raw/effective deductions,
explanations, supporting values/events, temporal persistence and trend,
recovery, excluded inputs, diagnostic steps, upstream references, and versions
make the final score reconstructable.

The initial weights, thresholds, health bands, confidence formula, and
workload exceptions in `health_config.py` are SmartOps-specific engineering
choices requiring labelled real-world validation.

> System Health Score summarizes the computer’s current observed operating
> condition using available telemetry, stability and operational evidence. It
> is not a failure probability, future-reliability guarantee or PC quality
> rating.

The System Health Score is not clinically, commercially, or industrially
validated. It does not replace the Deviation Index, Risk Evidence Index, or
Phase 4B PC Quality Check.

## Phase 4B PC Quality Check

Run:

```powershell
.\.venv\Scripts\python.exe -m analytics.quality --status
.\.venv\Scripts\python.exe -m analytics.quality --inventory
.\.venv\Scripts\python.exe -m analytics.quality --evaluate
.\.venv\Scripts\python.exe -m analytics.quality --evaluate --profile software_development
.\.venv\Scripts\python.exe -m analytics.quality --refresh-inventory
```

The inventory provider requests only an explicit set of CPU, RAM, storage,
graphics, and Windows capability fields from psutil, platform, selected CIM
properties, and optional local NVIDIA tooling. It never requests hardware
serials, product keys, user/owner names, network identifiers, application
history, file data, window titles, or command lines. Unknown media type remains
`unknown`/`unreliable`; it is never assumed to be an HDD. Unreliable GPU memory
is disclosed and never treated as zero.

An unchanged inventory signature reuses its normalized snapshot. Normal agent
operation checks inventory no more than once every 24 hours and evaluates only
previously unseen inventory/profile/algorithm/configuration combinations.
Every inventory and evaluation write is transactional; failures are isolated
from telemetry.

The catalogue includes everyday productivity, software development, data
analysis/light ML, local AI/GPU compute, content creation, and modern 3D gaming.
Each versioned profile has different component weights, minimum/recommended
thresholds, applicable optional inputs, mandatory gates, bottleneck caps,
explanation text, and validation metadata. Integrated graphics is valid for
non-GPU profiles; dedicated graphics is a mandatory gate only where explicitly
required.

Valid applicable component weights are normalized. Hard gates and bottleneck
caps are then applied so a strong component cannot hide a mandatory failure.
Physical cores and logical processors are not scored twice, and capacity is
not confused with current free-space readiness. Raw/effective scores, weights,
pass states, gates, caps, reasons, limiting components, generic
recommendations, and versions are stored for reconstruction.

Evaluation states are `assessed`, `provisional`, and `not_evaluated`; results
are `well_suited`, `suitable`, `suitable_with_limits`,
`upgrade_recommended`, `insufficient`, or `not_evaluated`. Missing required
facts create no score. Missing optional facts are excluded from capability and
may reduce detection confidence. Important unreliable profile inputs make a
result provisional.

> PC Quality Check estimates the computer’s suitability for a selected workload
> using detected hardware and system capabilities. It is not a benchmark
> result, failure prediction, future-reliability guarantee or confirmation that
> every application will run successfully.

Phase 4A health and Phase 3B risk are linked only as current
operating-readiness context. They do not affect the Workload Suitability Index.
The built-in thresholds, weights, caps, and bands are initial SmartOps
engineering assumptions, not research-validated benchmark standards. Labelled
real-world performance validation is required. Future software-specific
profiles must cite verified official requirements.

## Phase 5A local predictive alerts

Run:

```powershell
.\.venv\Scripts\python.exe -m analytics.alerts --status
.\.venv\Scripts\python.exe -m analytics.alerts --evaluate
.\.venv\Scripts\python.exe -m analytics.alerts --backfill
.\.venv\Scripts\python.exe -m analytics.alerts --list-open
```

Evaluation consumes completed five-minute windows with at least 80% coverage
and a genuine evaluated Phase 4A assessment. A provisional health assessment
can support an alert and remains labelled provisional. Incomplete,
low-coverage, missing-health, and `not_evaluated` inputs are skipped; the
engine never fabricates Phase 3A, 3B, or 4A evidence. Phase 4B suitability is
static capability context and does not generate operational alerts.

The catalogue defines resource, memory/swap, disk-capacity, disk-I/O, thermal,
serious-event, stability, increasing-risk, degraded-health, and data-quality
categories. CPU summaries are one correlation group, memory and swap are one
group, and Phase 3B evidence already represented through Phase 4A is stored as
suppressed supporting evidence rather than counted twice. Strong workload
exceptions require adequate classification confidence.

Non-critical conditions normally require persistence. Mapped Critical events
can create an immediate urgent alert. Continuing evidence updates the same
deterministic fingerprint; subsequent clear completed windows move the alert
through recovery and resolution with hysteresis. Acknowledgement records that
the user saw an alert and never resolves its condition. Resolved history,
occurrences, evidence, state transitions, explanations, and diagnostic or
preventive recommendations remain stored.

The thresholds, severity rules, cooldowns, and timing policies are
SmartOps-specific engineering decisions that require labelled real-world
validation. The system performs no remediation or external notification.

> SmartOps alerts indicate observed operational evidence that may require
> attention. They are not guaranteed predictions of failure, confirmed
> hardware diagnoses, or calibrated probabilities of a future crash.

All future analytics must remain local-first unless the project owner
explicitly changes that requirement.

## Phase 5B labelled predictive validation

Run:

```powershell
.\.venv\Scripts\python.exe -m analytics.validation --status
.\.venv\Scripts\python.exe -m analytics.validation --evaluate
.\.venv\Scripts\python.exe -m analytics.validation --backfill
.\.venv\Scripts\python.exe -m analytics.validation --summary
.\.venv\Scripts\python.exe -m analytics.validation --list-incidents
.\.venv\Scripts\python.exe -m analytics.validation --list-unverified-alerts
```

For a read-only experimental/PPT classification report, run:

```powershell
.\.venv\Scripts\python.exe -m analytics.evaluation_report
.\.venv\Scripts\python.exe -m analytics.evaluation_report --json
```

The report uses Class 0 = Normal / No Failure Risk and Class 1 = Failure
Risk. It reports the window-level TP/TN/FP/FN matrix, class counts, accuracy,
balanced accuracy, precision, recall, specificity, F1, FPR, FNR, and class
imbalance only when the latest stored run is current and meets the validation
publication policy. Zero eligible evidence is never displayed as 0%
performance. Newly evaluated windows preserve their prediction, independent
label, UTC time, workload, Risk Evidence Index, baseline/rule versions, and
source alert/incident IDs in the existing validation decision audit. Windows
used for baseline fitting are excluded.

SmartOps does not currently emit a calibrated Class 0/Class 1 probability.
Risk Evidence Index and evidence confidence are not prediction confidence, so
Average Prediction Confidence remains `Not currently measurable` until a
probabilistic output is calibrated and tested on independent labelled data.

Phase 5B uses explicit user-reported or externally verified incidents, alert
outcomes, confirmed links, and closed observation periods. It never treats
missing feedback as a negative label. Uncertain, withdrawn, short-horizon,
preventive-action-confounded, incomplete, low-coverage, and out-of-period
evidence is stored with exclusion reasons but does not enter headline metrics.

Automatic alert-to-incident matching uses versioned category compatibility,
time distance, and available workload context. It creates only probable,
possible, or rejected candidates. A confirmed link always requires an explicit
manual action. Alert first-active time is preserved for warning lead-time
calculation, including negative late-detection values.

Precision is alert-level, recall is verified-incident-level, and
accuracy/balanced accuracy is completed-observation-window-level. Default
publication minimums are 20 eligible alerts, 10 eligible incidents, 100
eligible windows over seven days, and five confirmed lead-time matches.
Insufficient denominators produce a null metric with
`insufficient_labeled_evidence`, never a fabricated zero. Supported stratified
results are stored by category, severity, workload, and source alert
algorithm/configuration cohort.

The stored formulas are `TP alerts / (TP alerts + FP alerts)`,
`detected incidents / (detected incidents + missed incidents)`,
`(TP windows + TN windows) / eligible windows`, and the mean of window
sensitivity and specificity. Lead time is incident start minus alert
first-supported observation; negative results are `late_detection`. Median,
minimum, maximum, late/no-warning counts, feedback completion, observation
coverage, and raw outcome counts are separate reconstructable results.
Validation confidence is separately labelled high, moderate, limited, or
insufficient from label source, timestamp precision, coverage, sample count,
and distinct days.

Evaluation runs contain an evidence signature, source/matching versions,
included and excluded evidence, TP/FP/FN/TN reconstruction counts, and
per-match lead times. They are transactional and idempotent for unchanged
evidence. User corrections are append-only revisions and withdrawals remain
auditable.

> SmartOps validation results are based on available user-reported or
> externally verified outcomes. They do not by themselves establish guaranteed
> failure prediction, hardware diagnosis, or universally validated accuracy.

The initial matching weights, observation horizon, label policy, and
publication minimums in `validation_config.py` are SmartOps engineering
choices. They are implemented and synthetically tested, but predictive
accuracy, calibration, and generalizability still require a larger
representative dataset and independent external validation.

## Post-calibration PC Quality taxonomy (schema 18)

The user-facing name for this 30-profile result is **Current Workload
Headroom**. It describes operating headroom during the most recent qualifying
five-minute observed workload period. It is not the separate hardware
capability assessment, benchmark performance, prediction accuracy, failure
probability, or a guarantee of future performance. An exact score of `100.0`
means only that all measured factors in that observed period stayed within
their expected ranges. The semantic identifier is
`current-workload-headroom-semantics-v1`; the existing mathematical output and
stored historical precision are unchanged.

The API reports **Current Evidence Quality** for the selected period separately
from factual **History Depth**. History includes qualifying observation count,
distinct observation days, latest qualifying time, a one/few/multiple summary,
and the derivable independent post-activation count. The selected evidence is
also classified as baseline-training evidence, independent post-calibration
evidence, or historical evidence whose independence is unavailable. A
deterministically available qualifying post-activation result is preferred,
without rewriting older assessments.

This taxonomy is separate from the immutable v2 workload taxonomy. Detection
uses executable names from foreground samples; background/support processes
can corroborate but cannot prove a profile. At least two foreground samples
and 20% of the expected five-minute samples must match. Foreground matches win
by matched count, then proportion, then documented catalogue order. URLs,
titles, filenames, browser history, typed text, and content are never used.
Ambiguous evidence falls back to the stored broad v2 workload without changing
that source label.

Weights below are percentages in the order CPU / RAM / swap / disk-capacity /
CPU-variability / serious events. The event weight is internally divided 60%
Critical and 40% Error. Thresholds, executable allowlists, explanations, and
missing-data policy are versioned in `profile_quality_catalogue.py`.

| Profile | Parent v2 context | Foreground detection examples | Weights |
|---|---|---|---|
| Video conferencing and online classes | Browser/Media | Zoom, Webex | 25/24/8/12/16/15 |
| Word processing | Office Productivity | Word, WordPad, LibreOffice | 18/30/10/17/15/10 |
| Spreadsheet analysis | Office Productivity | Excel, Calc | 28/30/10/12/12/8 |
| Presentation creation | Office Productivity | PowerPoint, Impress | 20/28/8/14/18/12 |
| Email and calendar | Office Productivity | Outlook, Thunderbird | 16/32/10/14/16/12 |
| PDF and document reading | Interactive Light | Acrobat, SumatraPDF, Foxit | 12/28/8/17/20/15 |
| Terminal and scripting | Development | Windows Terminal, PowerShell, cmd | 30/23/10/12/15/10 |
| Software build, compilation and testing | Development | MSBuild, compilers, Cargo, pytest | 38/27/9/10/10/6 |
| Data science and notebooks | Development | Jupyter, Spyder, RStudio | 32/34/10/8/10/6 |
| Graphic design and photo editing | Compute Intensive | Photoshop, Lightroom, GIMP | 27/28/8/10/17/10 |
| Video editing and rendering | Compute Intensive | Premiere, Resolve, After Effects, HandBrake | 34/28/8/12/10/8 |
| Audio production | Compute Intensive | Audacity, FL Studio, Ableton, Reaper | 24/27/8/10/23/8 |
| CAD, engineering and 3D modelling | Gaming/3D | AutoCAD, SolidWorks, Fusion, Blender, Maya | 33/28/8/10/13/8 |
| Virtual machines and containers | Compute Intensive | VMware, VirtualBox, Docker Desktop | 28/38/10/9/9/6 |
| File compression, backup and large transfers | Background Activity | 7-Zip, WinRAR, Robocopy, FreeFileSync | 27/18/8/25/12/10 |
| Remote desktop and remote support | Interactive Light | mstsc, TeamViewer, AnyDesk, RustDesk | 20/25/8/12/22/13 |
| Communication and chat | Interactive Light | Slack, Discord, Teams, WhatsApp, Telegram | 16/34/8/12/18/12 |
| Security scanning and system maintenance | Background Activity | Windows Security UI, MRT, Disk Cleanup | 30/22/10/18/12/8 |
| Local media playback | Browser/Media | VLC, mpv, Windows Media Player | 18/24/8/12/24/14 |
| Online learning and research | Browser/Media | No content-based inference | 20/28/8/12/18/14 |

Online learning/research deliberately remains Not observed unless future safe
local evidence can distinguish it without inspecting content. A browser alone
does not establish this profile. The broad Device, Gaming/3D, Development,
Guided Development, Browser/Media, Office Productivity, Interactive Light,
Compute Intensive, Background Activity, and Idle profiles remain available.

For metric `m`, let `R_m` be the greater of the configured recommended upper
bound and the applicable active-v2 parent-profile 95th percentile, and `L_m`
the greater of the configured limit and `R_m + 1`. The deterministic metric
score is:

`score_m = 100` when `x_m <= R_m`, otherwise
`max(0, 100 * (1 - (x_m - R_m) / (L_m - R_m)))`.

The profile score is `sum(w_m * score_m) / sum(w_m)` over available applicable
metrics. Required CPU, RAM, or disk-capacity evidence missing means Not
evaluated. Optional missing evidence is excluded, never set to zero. Current
Evidence Quality is `45*coverage + 0.25*detection_confidence +
20*available_weight_ratio + 10*baseline_available`, capped at 100. These
thresholds and weights are SmartOps engineering adaptations requiring labelled
validation, not universal application requirements or benchmark guarantees.

## Immutable alert explanation, confidence, and accuracy

New durable alert occurrences receive one immutable explanation snapshot with
the feature window, source count/coverage, workload, baseline version, rule and
configuration versions, triggering values, baseline ranges, thresholds,
deviations, capped contributors, unavailable evidence, and the risk/health
references actually used. Existing alerts are not reconstructed from current
data.

Alert Confidence is a weighted mean over available components:

| Component | Weight |
|---|---:|
| Completed-window evidence coverage | 25% |
| Referenced active-baseline adequacy | 20% |
| Required-metric availability | 15% |
| Authoritative sampling continuity | 15% |
| Margin beyond the documented trigger boundary | 15% |
| Agreement of independent contributing indicators | 5% |
| Evidence freshness | 5% |

Low is below 50, Moderate is 50–74.999, and High is 75–100. Required
confidence evidence missing yields Confidence unavailable. This is evidence
quality/consistency, not an event probability and not method accuracy.

Method validation is version- and workload-matched. For TP, TN, FP, and FN:

- accuracy = `(TP + TN) / (TP + TN + FP + FN)`;
- precision = `TP / (TP + FP)`;
- recall/sensitivity = `TP / (TP + FN)`;
- specificity = `TN / (TN + FP)`;
- F1 = `2 * precision * recall / (precision + recall)`;
- false-positive rate = `FP / (TN + FP)`.

The registry distinguishes real-world labelled, controlled experimental,
synthetic, and not-yet-validated records. Synthetic results are always labelled
Synthetic validation. Production currently has no inserted synthetic
validation record; missing applicable labelled evidence displays Not yet
validated. Pending/Confirmed/False positive/Inconclusive outcomes are
append-only user labels and do not retrain or change baseline, thresholds,
severity, health, risk, RCA, or notifications.

Research support and boundary:

- [IBM PC workload characterization](https://research.ibm.com/publications/pc-workload-characterization)
  supports trace-based, resource-aware workload characterization.
- [Runtime resource-usage characterization](https://doi.org/10.1016/j.asoc.2017.09.013)
  supports using CPU, memory, and I/O behaviour to distinguish runtime demand.
- [Lundberg and Lee](https://doi.org/10.48550/arXiv.1705.07874) supports
  exposing feature-level contributions; SmartOps uses a deterministic additive
  explanation rather than claiming to implement SHAP.
- [Saito and Rehmsmeier](https://doi.org/10.1371/journal.pone.0118432) supports
  reporting precision and recall for imbalanced outcomes rather than accuracy
  alone.
- [Powers](https://fac.flinders.edu.au/items/90ac6613-2b25-4c9a-887a-7194da37e79c)
  defines the relationships among precision, recall, specificity, F-score, and
  related evaluation measures.
- [Brier](https://doi.org/10.1175/1520-0493%281950%29078%3C0001%3AVOFEIT%3E2.0.CO%3B2)
  supports evaluating probabilistic forecasts against outcomes; SmartOps does
  not claim calibrated probabilities in this release.

The executable taxonomy, weights, thresholds, bands, and confidence formula
are SmartOps-specific engineering adaptations. They are implemented and tested
but not yet demonstrated to improve predictive accuracy on a representative
labelled real-world dataset.
