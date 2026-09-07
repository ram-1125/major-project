"""Command-line entry point for the SmartOps telemetry agent."""

from __future__ import annotations

import argparse
from collections import deque
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agent.collector import CounterRateCalculator, collect_metrics
from agent.config import get_database_path, get_sampling_interval
from agent.device import get_or_create_device_id
from agent.enhanced import (
    EnhancedRunContext,
    collect_enhanced_evidence,
    finish_enhanced_collection,
)
from agent.events import CHANNELS, poll_channel
from agent.notifications import dispatch_pending_notifications
from analytics.aggregate import aggregate_database
from analytics.alerts import maybe_evaluate_alerts
from analytics.baseline import maybe_maintain_baselines
from analytics.health import maybe_evaluate_health
from analytics.quality import maybe_maintain_quality
from analytics.risk import maybe_evaluate_risk
from analytics.workload import classify_workload
from backend.database import (
    database_connection,
    initialize_database,
    run_write_transaction,
    writer_priority,
)
from backend.enhanced_repository import mark_running_collection_runs
from backend.phase2b_repository import get_event_checkpoint, store_event_poll
from backend.repository import insert_metric
from backend.phase7b1_repository import (
    claim_agent_session,
    close_agent_session,
    close_orphaned_agent_sessions,
    heartbeat_agent_session,
)
from backend.phase7b2_repository import (
    complete_cycle,
    prune_cycle_audit,
    record_cycle_outcome,
    record_cycle_start,
    update_cycle_enhanced,
)
from backend.postcalibration_repository import update_pipeline_stage


LOGGER = logging.getLogger("smartops.agent")
MAINTENANCE_INTERVAL_SECONDS = 300


def _pipeline_update(
    path: Path,
    stage: str,
    state: str,
    *,
    attempted_at: str | None = None,
    successful_at: str | None = None,
    duration_ms: float | None = None,
    reason: str | None = None,
    session_id: str | None = None,
    priority: str = "maintenance",
) -> None:
    """Persist compact operational truth without changing pipeline logic."""
    run_write_transaction(
        path,
        lambda connection: update_pipeline_stage(
            connection, stage, state,
            attempted_at_utc=attempted_at,
            successful_at_utc=successful_at,
            duration_ms=duration_ms,
            reason=reason,
            runtime_session_id=session_id,
        ),
        priority=priority,
    )


def _run_pipeline_operation(path: Path, stage: str, operation) -> None:
    started = datetime.now(timezone.utc)
    _pipeline_update(path, stage, "processing", attempted_at=started.isoformat())
    try:
        operation(path)
    except Exception as error:
        _pipeline_update(
            path, stage, "failed", attempted_at=started.isoformat(),
            duration_ms=(datetime.now(timezone.utc) - started).total_seconds() * 1000,
            reason=f"{type(error).__name__}:operation_failed",
        )
        raise
    completed = datetime.now(timezone.utc)
    _pipeline_update(
        path, stage, "successfully_waiting", attempted_at=started.isoformat(),
        successful_at=completed.isoformat(),
        duration_ms=(completed - started).total_seconds() * 1000,
    )


def _run_downstream_maintenance(path: Path) -> None:
    """Run slower local collectors and analytics in one ordered owner.

    This function intentionally remains sequential: event checkpoints, derived
    analytics, alert lifecycle changes, and automatic notifications each have
    exactly one owner.  The continuous sampler calls it through one coalescing
    worker so a slow analytical pass cannot delay raw telemetry collection.
    """
    try:
        with database_connection(path) as connection:
            row = connection.execute(
                "SELECT device_id FROM metrics ORDER BY timestamp_utc DESC, id DESC LIMIT 1"
            ).fetchone()
            device_id = row[0] if row else get_or_create_device_id()
            for channel in CHANNELS:
                checkpoint = get_event_checkpoint(connection, channel)
                result = poll_channel(channel, device_id, checkpoint)
                inserted = store_event_poll(connection, result)
                if result.available:
                    LOGGER.info(
                        "%s Event Log available; stored %s new events.",
                        channel,
                        inserted,
                    )
                else:
                    LOGGER.warning(
                        "%s Event Log access unavailable or permission-restricted.",
                        channel,
                    )
    except Exception:
        LOGGER.exception("Event collection failed; raw telemetry was still stored.")

    try:
        # Twenty minutes includes the active and several recently closed
        # windows while avoiding a full-history scan on every routine pass.
        _run_pipeline_operation(
            path,
            "feature_window_generation",
            lambda target: aggregate_database(
                target, include_partial=False, recent_minutes=20
            ),
        )
    except Exception:
        LOGGER.exception("Feature aggregation failed; raw telemetry was still stored.")
    for operation, failure_message, pipeline_stage in (
        (maybe_maintain_baselines, "Baseline maintenance failed", None),
        (maybe_evaluate_risk, "Risk-evidence evaluation failed", "risk_evaluation"),
        (maybe_evaluate_health, "Health evaluation failed", "health_evaluation"),
        (maybe_maintain_quality, "PC Quality Check maintenance failed", None),
        (maybe_evaluate_alerts, "Predictive-alert evaluation failed", "predictive_alert_evaluation"),
        (dispatch_pending_notifications, "Windows notification dispatch failed", None),
    ):
        try:
            if pipeline_stage:
                _run_pipeline_operation(path, pipeline_stage, operation)
            else:
                operation(path)
        except Exception:
            LOGGER.exception("%s; raw telemetry was still stored.", failure_message)
    try:
        run_write_transaction(
            path, lambda connection: prune_cycle_audit(connection),
            priority="maintenance",
        )
    except Exception:
        LOGGER.exception("Operational-audit retention failed; telemetry is unaffected.")


class MaintenanceWorker:
    """One coalescing maintenance thread owned by the telemetry agent."""

    def __init__(self, path: Path, callback=_run_downstream_maintenance) -> None:
        self._path = path
        self._callback = callback
        self._condition = threading.Condition()
        self._pending = False
        self._running = False
        self._stopping = False
        self._thread = threading.Thread(
            target=self._run,
            name="smartops-maintenance",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def request(self) -> None:
        """Queue at most one follow-up pass while another pass is running."""
        with self._condition:
            if self._stopping or self._running or self._pending:
                return
            self._pending = True
            self._condition.notify()

    def _run(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._pending or self._stopping)
                if self._stopping and not self._pending:
                    return
                self._pending = False
                self._running = True
            try:
                with writer_priority("maintenance"):
                    self._callback(self._path)
            except Exception:
                # Each normal operation is already isolated, but keep this
                # outer guard so an unexpected defect cannot terminate the
                # owner and silently stop future maintenance.
                LOGGER.exception("Unexpected downstream maintenance failure.")
            finally:
                with self._condition:
                    self._running = False
                    self._condition.notify_all()

    def stop(self, timeout_seconds: float = 10.0) -> bool:
        """Request shutdown and return whether the bounded join completed."""
        with self._condition:
            self._pending = False
            self._stopping = True
            self._condition.notify_all()
        self._thread.join(timeout=max(0.0, timeout_seconds))
        return not self._thread.is_alive()


class EnhancedCollectionWorker:
    """Exactly one coalescing owner for slow optional enhanced collection."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._condition = threading.Condition()
        self._pending: tuple[dict[str, Any], datetime | None, str | None, int] | None = None
        # Raw sampling must never wait for an enhanced-evidence database write.
        # Coalesced audit IDs are therefore handed back to this worker and
        # persisted after the current slow collector finishes.  The deque is
        # deliberately bounded so a broken optional provider cannot build an
        # unbounded backlog.
        self._coalesced_cycle_ids: deque[int] = deque(maxlen=100)
        self._running = False
        self._stopping = False
        self._thread = threading.Thread(
            target=self._run, name="smartops-enhanced", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def request(
        self,
        sample: dict[str, Any],
        scheduled_at_utc: datetime | None,
        agent_session_id: str | None,
        cycle_audit_id: int,
    ) -> bool:
        with self._condition:
            if self._stopping or self._running or self._pending is not None:
                self._coalesced_cycle_ids.append(cycle_audit_id)
                return False
            self._pending = (
                dict(sample), scheduled_at_utc, agent_session_id, cycle_audit_id
            )
            self._condition.notify()
            return True

    def _run(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._pending is not None or self._stopping)
                if self._stopping and self._pending is None:
                    return
                request = self._pending
                self._pending = None
                self._running = True
            assert request is not None
            sample, scheduled_at_utc, session_id, cycle_id = request
            context: EnhancedRunContext | None = None
            try:
                run_write_transaction(
                    self._path,
                    lambda connection: update_cycle_enhanced(connection, cycle_id, "running"),
                    priority="maintenance",
                )
                context = collect_enhanced_evidence(
                    self._path,
                    sample,
                    scheduled_at_utc=scheduled_at_utc,
                    agent_session_id=session_id,
                )
                finish_enhanced_collection(self._path, context)
                status = "partial" if context.failed_collectors else "completed"
                run_write_transaction(
                    self._path,
                    lambda connection: update_cycle_enhanced(
                        connection, cycle_id, status, enhanced_run_id=context.run_id
                    ),
                    priority="maintenance",
                )
            except BaseException as error:
                if isinstance(error, (KeyboardInterrupt, SystemExit)):
                    raise
                LOGGER.exception("Enhanced worker failed; raw telemetry remains active.")
                try:
                    run_write_transaction(
                        self._path,
                        lambda connection: update_cycle_enhanced(
                            connection, cycle_id, "failed",
                            enhanced_run_id=context.run_id if context else None,
                            reason_code="enhanced_collection_failure",
                        ),
                        priority="maintenance",
                    )
                except Exception:
                    LOGGER.exception("Could not persist enhanced-worker failure audit.")
            finally:
                with self._condition:
                    coalesced = list(self._coalesced_cycle_ids)
                    self._coalesced_cycle_ids.clear()
                if coalesced:
                    try:
                        run_write_transaction(
                            self._path,
                            lambda connection: [
                                update_cycle_enhanced(
                                    connection,
                                    coalesced_cycle_id,
                                    "coalesced",
                                    reason_code="enhanced_worker_busy_no_backlog",
                                )
                                for coalesced_cycle_id in coalesced
                            ],
                            priority="maintenance",
                        )
                    except Exception:
                        LOGGER.exception(
                            "Could not persist coalesced enhanced-cycle audit."
                        )
                with self._condition:
                    self._running = False
                    self._condition.notify_all()

    def stop(self, timeout_seconds: float = 30.0) -> bool:
        with self._condition:
            self._pending = None
            self._stopping = True
            self._condition.notify_all()
        self._thread.join(timeout=max(0.0, timeout_seconds))
        return not self._thread.is_alive()


def collect_and_store(
    database_path: Path | None = None,
    rate_calculator: CounterRateCalculator | None = None,
    scheduled_at_utc: datetime | None = None,
    agent_session_id: str | None = None,
    run_maintenance: bool = True,
    enhanced_worker: EnhancedCollectionWorker | None = None,
) -> dict[str, Any]:
    """Collect and persist one sample, then return it for terminal feedback."""
    cycle_started_monotonic = time.monotonic()
    process_cpu_started = time.process_time()
    enhanced_context: EnhancedRunContext | None = None
    actual_started = datetime.now(timezone.utc)
    path = database_path or get_database_path()
    initialize_database(path)
    try:
        _pipeline_update(
            path, "telemetry_collection", "processing",
            attempted_at=actual_started.isoformat(), session_id=agent_session_id,
            priority="raw",
        )
    except Exception:
        LOGGER.exception("Could not publish telemetry processing state.")
    device_id = get_or_create_device_id()
    sample = collect_metrics(
        device_id,
        rate_calculator=rate_calculator,
    )
    workload = classify_workload(sample)
    sample.update(
        {
            "workload_class": workload.workload_class,
            "workload_confidence": workload.confidence,
            "workload_reasons": workload.reason_codes,
            # getattr keeps custom test/plugin classifiers written for the
            # older three-field contract compatible with Phase 7B.1.
            "user_activity_state": getattr(workload, "user_activity_state", None),
            "system_activity_state": getattr(workload, "system_activity_state", None),
            "workload_rule_version": getattr(workload, "rule_version", None),
            "workload_provenance": getattr(workload, "provenance", None),
        }
    )
    cycle_audit_id: int | None = None
    metric_id: int

    def persist_raw(connection):
        nonlocal cycle_audit_id
        scheduled = scheduled_at_utc or actual_started
        if agent_session_id is not None:
            cycle_audit_id = record_cycle_start(
                connection,
                runtime_session_id=agent_session_id,
                scheduled_at_utc=scheduled.isoformat(),
                started_at_utc=actual_started.isoformat(),
                delay_seconds=max(0.0, (actual_started - scheduled).total_seconds()),
            )
        stored_metric_id = insert_metric(connection, sample, manage_transaction=False)
        completed = datetime.now(timezone.utc)
        if cycle_audit_id is not None:
            complete_cycle(
                connection, cycle_audit_id, metric_id=stored_metric_id,
                completed_at_utc=completed.isoformat(),
                duration_ms=(time.monotonic() - cycle_started_monotonic) * 1000.0,
                enhanced_status="pending",
            )
        return stored_metric_id

    metric_id = run_write_transaction(path, persist_raw, priority="raw")
    completed_at = datetime.now(timezone.utc)
    try:
        _pipeline_update(
            path, "telemetry_collection", "successfully_waiting",
            attempted_at=actual_started.isoformat(),
            successful_at=completed_at.isoformat(),
            duration_ms=(time.monotonic() - cycle_started_monotonic) * 1000.0,
            session_id=agent_session_id, priority="raw",
        )
    except Exception:
        LOGGER.exception("Could not publish telemetry success state.")

    # Enhanced counters run immediately after the raw insert. Event-log and
    # analytical maintenance can be slower and must not add avoidable delay to
    # the lower-frequency counter schedule.
    if enhanced_worker is not None:
        assert cycle_audit_id is not None
        enhanced_worker.request(
            sample, scheduled_at_utc, agent_session_id, cycle_audit_id
        )
    else:
        try:
            enhanced_context = collect_enhanced_evidence(
                path,
                sample,
                cycle_started_monotonic=cycle_started_monotonic,
                process_cpu_started=process_cpu_started,
                scheduled_at_utc=scheduled_at_utc,
                agent_session_id=agent_session_id,
            )
        except BaseException as error:
            # Ctrl+C can arrive while a bounded PowerShell query is active. Close
            # the run metadata before allowing KeyboardInterrupt to stop the loop.
            try:
                with database_connection(path) as connection:
                    mark_running_collection_runs(
                        connection,
                        finished_at_utc=datetime.now(timezone.utc).isoformat(),
                        error_code=(
                            "collection_interrupted"
                            if isinstance(error, (KeyboardInterrupt, SystemExit))
                            else "unhandled_enhanced_collector_failure"
                        ),
                    )
            except Exception:
                LOGGER.exception("Could not close interrupted enhanced run metadata.")
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            LOGGER.exception(
                "Enhanced shadow-evidence collection failed; raw telemetry and "
                "existing analytics remain unaffected."
            )

    if enhanced_worker is None and enhanced_context is not None:
        try:
            finish_enhanced_collection(path, enhanced_context)
        except Exception:
            LOGGER.exception(
                "Enhanced collection-overhead recording failed; telemetry "
                "and existing analytics were still stored."
            )
        if cycle_audit_id is not None:
            run_write_transaction(
                path,
                lambda connection: update_cycle_enhanced(
                    connection, cycle_audit_id,
                    "partial" if enhanced_context.failed_collectors else "completed",
                    enhanced_run_id=enhanced_context.run_id,
                ),
                priority="maintenance",
            )
    if run_maintenance:
        _run_downstream_maintenance(path)
    return sample


def run_forever(interval_seconds: int, database_path: Path | None = None) -> None:
    """Collect synchronously until Ctrl+C, logging cycle failures and continuing."""
    stop_event = threading.Event()
    rate_calculator = CounterRateCalculator()
    path = database_path or get_database_path()
    initialize_database(path)
    session_id = str(uuid.uuid4())
    session_started = datetime.now(timezone.utc)
    with database_connection(path) as connection:
        with connection:
            claim_agent_session(
                connection,
                session_id=session_id,
                process_id=os.getpid(),
                started_at_utc=session_started,
            )
    next_collection = time.monotonic()
    next_collection_utc = session_started
    next_maintenance = next_collection
    maintenance_worker = MaintenanceWorker(path)
    enhanced_worker = EnhancedCollectionWorker(path)
    maintenance_worker.start()
    enhanced_worker.start()
    pending_failure_audits: deque[dict[str, Any]] = deque(maxlen=100)
    print(
        f"SmartOps agent is running every {interval_seconds} seconds. "
        "Collection cycles never overlap. Press Ctrl+C to stop.",
        flush=True,
    )
    try:
        while not stop_event.is_set():
            wait_seconds = max(0.0, next_collection - time.monotonic())
            if stop_event.wait(wait_seconds):
                break

            try:
                sample = collect_and_store(
                    path,
                    rate_calculator,
                    scheduled_at_utc=next_collection_utc,
                    agent_session_id=session_id,
                    run_maintenance=False,
                    enhanced_worker=enhanced_worker,
                )
                if pending_failure_audits:
                    pending = list(pending_failure_audits)
                    run_write_transaction(
                        path,
                        lambda connection: [record_cycle_outcome(connection, **item)
                                            for item in pending],
                        priority="raw",
                    )
                    pending_failure_audits.clear()
                now_monotonic = time.monotonic()
                if now_monotonic >= next_maintenance:
                    maintenance_worker.request()
                    elapsed_intervals = max(
                        1,
                        int(
                            (now_monotonic - next_maintenance)
                            // MAINTENANCE_INTERVAL_SECONDS
                        ) + 1,
                    )
                    next_maintenance += (
                        elapsed_intervals * MAINTENANCE_INTERVAL_SECONDS
                    )
                with database_connection(path) as connection:
                    with connection:
                        heartbeat_agent_session(
                            connection, session_id, datetime.now(timezone.utc)
                        )
                print(
                    f"Stored SmartOps sample at {sample['timestamp_utc']}",
                    flush=True,
                )
            except Exception as error:
                LOGGER.exception(
                    "Collection cycle failed; the agent will retry at the next interval."
                )
                message = str(error).casefold()
                locked = "locked" in message or "busy" in message
                pending_failure_audits.append({
                    "runtime_session_id": session_id,
                    "scheduled_at_utc": next_collection_utc.isoformat(),
                    "status": "failed",
                    "reason_code": (
                        "sqlite_busy_retries_exhausted" if locked
                        else "raw_collection_cycle_failure"
                    ),
                    "failure_category": "sqlite" if locked else "collector",
                    "sqlite_retry_count": 4 if locked else 0,
                    "lock_category": "busy_or_locked" if locked else None,
                })
                try:
                    _pipeline_update(
                        path, "telemetry_collection", "failed",
                        attempted_at=datetime.now(timezone.utc).isoformat(),
                        reason=(
                            "sqlite_busy_retries_exhausted" if locked
                            else "raw_collection_cycle_failure"
                        ),
                        session_id=session_id,
                        priority="raw",
                    )
                except Exception:
                    LOGGER.exception("Could not publish telemetry failure state.")

            next_collection = next_collection + interval_seconds
            next_collection_utc = next_collection_utc + timedelta(
                seconds=interval_seconds
            )
            now_monotonic = time.monotonic()
            if next_collection <= now_monotonic:
                missed = int((now_monotonic - next_collection) // interval_seconds) + 1
                LOGGER.warning(
                    "Raw collection overran the schedule; skipping %s elapsed slot(s) "
                    "without fabricating samples.",
                    missed,
                )
                first_skipped = next_collection_utc
                for index in range(missed):
                    pending_failure_audits.append({
                        "runtime_session_id": session_id,
                        "scheduled_at_utc": (
                            first_skipped + timedelta(seconds=index * interval_seconds)
                        ).isoformat(),
                        "status": "skipped",
                        "reason_code": "raw_schedule_overrun_elapsed_slot",
                        "failure_category": "scheduler_overrun",
                    })
                next_collection += missed * interval_seconds
                next_collection_utc += timedelta(seconds=missed * interval_seconds)
    except KeyboardInterrupt:
        stop_event.set()
        print("\nSmartOps agent stopped cleanly.")
    finally:
        if not enhanced_worker.stop():
            LOGGER.warning("Enhanced collection exceeded the bounded shutdown wait.")
        if not maintenance_worker.stop():
            LOGGER.warning(
                "Maintenance exceeded the shutdown wait; the daemon worker will "
                "end with the agent process."
            )
        with database_connection(path) as connection:
            with connection:
                close_agent_session(
                    connection,
                    session_id,
                    datetime.now(timezone.utc),
                    "ctrl_c_or_normal_exit",
                )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SmartOps local telemetry agent")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Collect one sample and exit.",
    )
    parser.add_argument(
        "--cleanup-orphaned-sessions",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser


def main() -> None:
    logging.Formatter.converter = time.gmtime
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s UTC %(levelname)s %(message)s",
    )
    args = build_parser().parse_args()
    if args.cleanup_orphaned_sessions:
        path = get_database_path()
        initialize_database(path)
        with database_connection(path) as connection, connection:
            closed = close_orphaned_agent_sessions(
                connection, datetime.now(timezone.utc)
            )
        print(f"Closed {closed} orphaned SmartOps agent session(s).")
        return
    if args.once:
        path = get_database_path()
        initialize_database(path)
        session_id = str(uuid.uuid4())
        started = datetime.now(timezone.utc)
        with database_connection(path) as connection, connection:
            claim_agent_session(
                connection,
                session_id=session_id,
                process_id=os.getpid(),
                started_at_utc=started,
            )
        try:
            sample = collect_and_store(
                path,
                scheduled_at_utc=started,
                agent_session_id=session_id,
            )
            print(f"Stored one SmartOps sample at {sample['timestamp_utc']}")
        finally:
            with database_connection(path) as connection, connection:
                close_agent_session(
                    connection,
                    session_id,
                    datetime.now(timezone.utc),
                    "one_shot_complete",
                )
        return

    run_forever(get_sampling_interval())


if __name__ == "__main__":
    main()
