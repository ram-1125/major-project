"""FastAPI application serving telemetry from the local SQLite database."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agent.config import (
    DEFAULT_SAMPLING_INTERVAL_SECONDS,
    get_dashboard_url,
    get_database_path,
    get_sampling_interval,
)
from agent.notifications import (
    NotificationProvider,
    build_enabled_confirmation_message,
    build_test_message,
    create_notification_provider,
)
from analytics.aggregate import WINDOW_MINUTES
from analytics.baseline import baseline_status
from analytics.recalibration import (
    activate_candidate,
    baseline_management_status,
    cancel_recalibration,
    continue_recalibration,
    pause_recalibration,
    resume_recalibration,
    rollback_baseline,
    start_recalibration,
)
from analytics.alert_catalogue import INTERPRETATION as ALERT_INTERPRETATION
from analytics.alert_catalogue import (
    ALGORITHM_VERSION as ALERT_ALGORITHM_VERSION,
    CATALOGUE_VERSION as ALERT_CATALOGUE_VERSION,
    CONFIGURATION_VERSION as ALERT_CONFIGURATION_VERSION,
)
from analytics.alerts import acknowledge_alert
from analytics.validation import validation_run, validation_status
from analytics.validation_config import (
    CATEGORY_COMPATIBILITY,
    INTERPRETATION as VALIDATION_INTERPRETATION,
    MATCHING_VERSION as VALIDATION_MATCHING_VERSION,
)
from analytics.health import health_status
from analytics.health_config import INTERPRETATION as HEALTH_INTERPRETATION
from analytics.quality import quality_status
from analytics.quality import PRIVACY_EXCLUDED_FIELDS
from analytics.quality_catalogue import (
    INTERPRETATION as QUALITY_INTERPRETATION,
    WORKLOAD_PROFILES,
)
from analytics.profile_quality import profile_quality_status
from analytics.risk import risk_status
from backend.database import (
    database_connection,
    initialize_database,
    run_write_transaction,
)
from backend.repository import (
    count_metrics,
    get_latest_metric,
    get_latest_process_snapshots,
    get_metric_history,
)
from backend.phase2b_repository import (
    get_event_summary,
    get_events,
    get_feature_history,
    get_workload_history,
)
from backend.phase3_repository import (
    get_deviation_for_window,
    get_deviations,
    get_profiles,
)
from backend.schemas import (
    CountResponse,
    HistoryResponse,
    MetricResponse,
    ProcessSnapshotsResponse,
    StatusResponse,
)
from backend.phase3b_repository import (
    get_risk_assessments,
    get_risk_for_window,
    get_root_causes,
    get_root_causes_for_window,
)
from backend.phase4a_repository import (
    get_health_assessments,
    get_health_for_window,
)
from backend.phase4b_repository import (
    get_latest_inventory,
    get_quality_assessment,
    get_quality_assessments,
)
from backend.phase5a_repository import (
    get_alert,
    get_alert_status,
    get_alerts,
    get_latest_alerts,
)
from backend.phase5b_repository import (
    close_observation_period,
    create_feedback,
    create_incident,
    create_observation_period,
    dumps as validation_json,
    expand as expand_validation_row,
    get_feedback,
    get_incident,
    get_observation_period,
    list_incidents,
    list_feedback,
    list_observation_periods,
    list_unverified_alerts,
    list_validation_runs,
    revise_feedback,
    revise_incident,
    upsert_alert_incident_link,
)
from backend.phase5b_schemas import (
    AlertFeedbackCreate,
    AlertFeedbackRevision,
    AlertIncidentLinkCreate,
    IncidentCreate,
    IncidentRevision,
    ObservationPeriodClose,
    ObservationPeriodCreate,
    WithdrawRequest,
    utc_string,
)
from backend.phase7a_repository import (
    ensure_notification_preferences,
    get_notification_delivery_status,
    get_notification_preferences,
    update_notification_preferences,
)
from backend.phase7a_schemas import NotificationPreferencesUpdate
from backend.enhanced_repository import (
    get_enhanced_events,
    get_enhanced_status,
    get_overhead_history,
    get_signal_history,
)
from backend.postcalibration_repository import (
    append_outcome,
    get_pipeline_status,
    list_validations,
)


LOGGER = logging.getLogger("smartops.api")


class AlertOutcomeUpdate(BaseModel):
    outcome: Literal["pending", "confirmed", "false_positive", "inconclusive"]
    note: str | None = Field(default=None, max_length=1000)


def _as_utc_string(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _validated_range(
    start: datetime | None,
    end: datetime | None,
) -> tuple[str | None, str | None]:
    start_utc = _as_utc_string(start)
    end_utc = _as_utc_string(end)
    if start_utc is not None and end_utc is not None and start_utc > end_utc:
        raise HTTPException(
            status_code=422,
            detail="The start timestamp must be before or equal to the end timestamp.",
        )
    return start_utc, end_utc


def create_app(
    database_path: Path | None = None,
    notification_provider: NotificationProvider | None = None,
) -> FastAPI:
    """Build an app; accepting a path makes the API straightforward to test."""
    path = database_path or get_database_path()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        initialize_database(path)
        app.state.database_path = path
        with database_connection(path) as connection:
            with connection:
                ensure_notification_preferences(connection)
        app.state.notification_provider = (
            notification_provider or create_notification_provider()
        )
        yield

    local_app = FastAPI(
        title="SmartOps Local API",
        version="0.12.0",
        description=(
            f"{HEALTH_INTERPRETATION}\n\n"
            f"{QUALITY_INTERPRETATION}\n\n"
            f"{ALERT_INTERPRETATION}\n\n"
            f"{VALIDATION_INTERPRETATION}\n\n"
            "The Deviation Index describes difference from a learned local "
            "pattern. The Risk Evidence Index describes accumulated operational "
            "evidence, not failure probability. These outputs remain separate "
            "from PC Quality Check workload suitability.\n\n"
            "Phase 7B enhanced evidence is collected locally in Shadow Mode. "
            "It does not change Risk Evidence, System Health, root-cause "
            "ranking, or predictive-alert severity."
        ),
        lifespan=lifespan,
    )
    local_app.state.database_path = path
    local_app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["*"],
    )

    @local_app.get("/api/status", response_model=StatusResponse)
    def status(request: Request) -> StatusResponse:
        with database_connection(request.app.state.database_path) as connection:
            connection.execute("SELECT 1").fetchone()
        return StatusResponse(status="ok", database="connected")

    @local_app.get("/api/runtime/status")
    def runtime_status(request: Request) -> dict[str, object]:
        """Expose the genuine agent heartbeat used by live-only UI effects."""
        now = datetime.now(timezone.utc)
        with database_connection(request.app.state.database_path) as connection:
            row = connection.execute(
                """SELECT session_id, process_id, started_at_utc,
                heartbeat_at_utc, stopped_at_utc, status, stop_reason
                FROM agent_runtime_sessions
                ORDER BY heartbeat_at_utc DESC, session_id DESC LIMIT 1"""
            ).fetchone()
        if row is None:
            return {
                "state": "offline",
                "agent_running": False,
                "heartbeat_at_utc": None,
                "heartbeat_age_seconds": None,
                "stale_after_seconds": 90,
                "reason": "no_agent_session_recorded",
            }
        heartbeat = datetime.fromisoformat(row["heartbeat_at_utc"])
        age = max(0.0, (now - heartbeat).total_seconds())
        is_running = row["status"] == "running"
        state = "live" if is_running and age <= 90 else (
            "stale" if is_running else "paused"
        )
        return {
            "state": state,
            "agent_running": is_running,
            "heartbeat_at_utc": row["heartbeat_at_utc"],
            "heartbeat_age_seconds": age,
            "stale_after_seconds": 90,
            "started_at_utc": row["started_at_utc"],
            "stopped_at_utc": row["stopped_at_utc"],
            "stop_reason": row["stop_reason"],
        }

    @local_app.get("/api/pipeline/status")
    def pipeline_status(request: Request) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            return get_pipeline_status(connection)

    @local_app.get("/api/settings/status")
    def settings_status(request: Request) -> dict[str, object]:
        """Return safe, read-only runtime facts for the centralized Settings page."""
        with database_connection(request.app.state.database_path) as connection:
            schema_version = int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            )
            foreign_keys_enabled = bool(
                connection.execute("PRAGMA foreign_keys").fetchone()[0]
            )
            latest = connection.execute(
                "SELECT timestamp_utc FROM metrics ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return {
            "schema_version": schema_version,
            "database_status": "connected",
            "integrity_status": "available_for_manual_verification",
            "integrity_explanation": (
                "Startup verifies the schema. Full SQLite integrity and "
                "foreign-key checks are intentionally run as maintenance "
                "checks rather than on every dashboard refresh."
            ),
            "foreign_keys_enabled": foreign_keys_enabled,
            "telemetry_sampling_seconds": get_sampling_interval(),
            "production_sampling_seconds": DEFAULT_SAMPLING_INTERVAL_SECONDS,
            "feature_window_minutes": WINDOW_MINUTES,
            "processing_mode": "local_only",
            "last_collection_timestamp_utc": latest[0] if latest else None,
            "api_version": local_app.version,
        }

    @local_app.get("/api/enhanced-evidence/status")
    def enhanced_evidence_status(request: Request) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            return get_enhanced_status(connection)

    @local_app.get("/api/enhanced-evidence/history")
    def enhanced_evidence_history(
        request: Request,
        signal: str = Query(min_length=1, max_length=100, pattern=r"^[a-z0-9_]+$"),
        limit: int = Query(default=240, ge=1, le=5000),
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict[str, object]:
        start_utc, end_utc = _validated_range(start, end)
        with database_connection(request.app.state.database_path) as connection:
            items = get_signal_history(
                connection,
                signal,
                limit,
                start_utc,
                end_utc,
            )
        return {
            "items": items,
            "signal": signal,
            "limit": limit,
            "shadow_mode": True,
        }

    @local_app.get("/api/enhanced-evidence/events")
    def enhanced_evidence_events(
        request: Request,
        limit: int = Query(default=50, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
        evidence_type: str | None = Query(default=None, max_length=100),
    ) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            items, total = get_enhanced_events(
                connection,
                limit,
                offset,
                evidence_type,
            )
        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "shadow_mode": True,
        }

    @local_app.get("/api/enhanced-evidence/overhead")
    def enhanced_evidence_overhead(
        request: Request,
        limit: int = Query(default=50, ge=1, le=1000),
    ) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            items = get_overhead_history(connection, limit)
        return {"items": items, "limit": limit, "shadow_mode": True}

    @local_app.get(
        "/api/metrics/latest",
        response_model=MetricResponse | None,
    )
    def latest(request: Request) -> dict[str, object] | None:
        with database_connection(request.app.state.database_path) as connection:
            return get_latest_metric(connection)

    @local_app.get(
        "/api/metrics/history",
        response_model=HistoryResponse,
    )
    def history(
        request: Request,
        limit: int = Query(default=50, ge=1, le=5000),
        offset: int = Query(default=0, ge=0),
        start: datetime | None = Query(default=None),
        end: datetime | None = Query(default=None),
        sort: Literal["oldest", "newest"] = Query(default="newest"),
    ) -> HistoryResponse:
        start_utc, end_utc = _validated_range(start, end)
        with database_connection(request.app.state.database_path) as connection:
            items, total = get_metric_history(
                connection,
                limit=limit,
                offset=offset,
                start_timestamp=start_utc,
                end_timestamp=end_utc,
                sort_order=sort,
            )
        return HistoryResponse(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
            sort=sort,
            start=start_utc,
            end=end_utc,
        )

    @local_app.get("/api/metrics/count", response_model=CountResponse)
    def metric_count(
        request: Request,
        start: datetime | None = Query(default=None),
        end: datetime | None = Query(default=None),
    ) -> CountResponse:
        start_utc, end_utc = _validated_range(start, end)
        with database_connection(request.app.state.database_path) as connection:
            count = count_metrics(connection, start_utc, end_utc)
        return CountResponse(count=count)

    @local_app.get(
        "/api/processes/latest",
        response_model=ProcessSnapshotsResponse | None,
    )
    def latest_processes(request: Request) -> dict[str, object] | None:
        with database_connection(request.app.state.database_path) as connection:
            return get_latest_process_snapshots(connection)

    @local_app.get("/api/events")
    def events(
        request: Request,
        limit: int = Query(default=50, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
        start: datetime | None = None,
        end: datetime | None = None,
        sort: Literal["oldest", "newest"] = "newest",
        channel: str | None = None,
        level: str | None = None,
        category: str | None = None,
        severity: str | None = None,
    ) -> dict[str, object]:
        start_utc, end_utc = _validated_range(start, end)
        with database_connection(request.app.state.database_path) as connection:
            items, total = get_events(
                connection, limit, offset, start_utc, end_utc, sort,
                channel, level, category,
            )
        return {"items": items, "total": total, "limit": limit, "offset": offset, "sort": sort}

    @local_app.get("/api/events/summary")
    def event_summary(request: Request) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            return get_event_summary(connection)

    @local_app.get("/api/workload/latest")
    def workload_latest(request: Request) -> dict[str, object] | None:
        with database_connection(request.app.state.database_path) as connection:
            items, _ = get_workload_history(connection, 1, 0)
        return items[0] if items else None

    @local_app.get("/api/workload/history")
    def workload_history(
        request: Request,
        limit: int = Query(default=50, ge=1, le=5000),
        offset: int = Query(default=0, ge=0),
        start: datetime | None = None,
        end: datetime | None = None,
        sort: Literal["oldest", "newest"] = "newest",
    ) -> dict[str, object]:
        start_utc, end_utc = _validated_range(start, end)
        with database_connection(request.app.state.database_path) as connection:
            items, total = get_workload_history(
                connection, limit, offset, start_utc, end_utc, sort
            )
        return {"items": items, "total": total, "limit": limit, "offset": offset, "sort": sort}

    @local_app.get("/api/features/latest")
    def features_latest(request: Request) -> dict[str, object] | None:
        with database_connection(request.app.state.database_path) as connection:
            items, _ = get_feature_history(connection, 1, 0)
        return items[0] if items else None

    @local_app.get("/api/features/history")
    def features_history(
        request: Request,
        limit: int = Query(default=50, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
        start: datetime | None = None,
        end: datetime | None = None,
        sort: Literal["oldest", "newest"] = "newest",
    ) -> dict[str, object]:
        start_utc, end_utc = _validated_range(start, end)
        with database_connection(request.app.state.database_path) as connection:
            items, total = get_feature_history(
                connection, limit, offset, start_utc, end_utc, sort
            )
        return {"items": items, "total": total, "limit": limit, "offset": offset, "sort": sort}

    @local_app.get("/api/features/count")
    def features_count(
        request: Request,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict[str, int]:
        start_utc, end_utc = _validated_range(start, end)
        with database_connection(request.app.state.database_path) as connection:
            _, total = get_feature_history(
                connection,
                1,
                0,
                start=start_utc,
                end=end_utc,
            )
        return {"count": total}

    @local_app.get("/api/baseline/status")
    def phase3_baseline_status(
        request: Request,
        device: str | None = None,
    ) -> dict[str, object]:
        return baseline_status(request.app.state.database_path, device_id=device)

    @local_app.get("/api/baseline/profiles")
    def baseline_profiles(
        request: Request,
        device: str | None = None,
        workload: str | None = None,
    ) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            items = get_profiles(connection, device, workload)
        return {"items": items, "total": len(items)}

    @local_app.get("/api/baseline-management/status")
    def baseline_management(
        request: Request,
        device: str | None = None,
    ) -> dict[str, object]:
        return baseline_management_status(
            request.app.state.database_path, device_id=device
        )

    @local_app.post("/api/baseline-management/{action}")
    def baseline_management_action(
        request: Request,
        action: Literal[
            "start", "pause", "resume", "continue", "cancel", "activate", "rollback"
        ],
        device: str | None = None,
    ) -> dict[str, object]:
        status = baseline_management_status(
            request.app.state.database_path, device_id=device
        )
        resolved = status["device_id"]
        if not resolved:
            raise HTTPException(
                status_code=409,
                detail="No local device telemetry is available for baseline management.",
            )
        handlers = {
            "start": start_recalibration,
            "pause": pause_recalibration,
            "resume": resume_recalibration,
            "continue": continue_recalibration,
            "cancel": cancel_recalibration,
            "activate": activate_candidate,
            "rollback": rollback_baseline,
        }
        try:
            return handlers[action](
                request.app.state.database_path, device_id=str(resolved)
            )
        except ValueError as error:
            friendly_messages = {
                "start": "A new calibration could not be started. Your active personal baseline remains in use.",
                "pause": "The new calibration could not be paused. Monitoring continues normally.",
                "resume": "The new calibration could not be resumed. Monitoring continues normally.",
                "continue": "The new calibration could not continue. Its existing progress is preserved.",
                "cancel": "The new calibration could not be cancelled. Your active baseline was not changed.",
                "activate": "The new calibration could not be activated. Your current baseline remains active.",
                "rollback": "The previous baseline could not be restored. Your current baseline remains active.",
            }
            raise HTTPException(
                status_code=409,
                detail={
                    "message": friendly_messages[action],
                    "technical_detail": str(error),
                    "action": action,
                },
            ) from error

    @local_app.get("/api/deviations/latest")
    def deviations_latest(
        request: Request,
        device: str | None = None,
        workload: str | None = None,
    ) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            items, _ = get_deviations(
                connection, 1, 0, device_id=device, workload=workload
            )
        if items:
            return {"status": "evaluated", "assessment": items[0]}
        status = baseline_status(request.app.state.database_path, device_id=device)
        return {
            "status": "not_evaluated",
            "assessment": None,
            "reason_code": (
                "insufficient_history"
                if status["state"] in {"collecting_data", "provisional"}
                else "no_eligible_target_window"
            ),
            "baseline_state": status["state"],
        }

    @local_app.get("/api/deviations/history")
    def deviations_history(
        request: Request,
        limit: int = Query(default=50, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
        start: datetime | None = None,
        end: datetime | None = None,
        sort: Literal["oldest", "newest"] = "newest",
        device: str | None = None,
        workload: str | None = None,
    ) -> dict[str, object]:
        start_utc, end_utc = _validated_range(start, end)
        with database_connection(request.app.state.database_path) as connection:
            items, total = get_deviations(
                connection,
                limit,
                offset,
                start_utc,
                end_utc,
                sort,
                device,
                workload,
            )
        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "sort": sort,
            "start": start_utc,
            "end": end_utc,
        }

    @local_app.get("/api/deviations/count")
    def deviations_count(
        request: Request,
        start: datetime | None = None,
        end: datetime | None = None,
        device: str | None = None,
        workload: str | None = None,
    ) -> dict[str, int]:
        start_utc, end_utc = _validated_range(start, end)
        with database_connection(request.app.state.database_path) as connection:
            _, total = get_deviations(
                connection,
                1,
                0,
                start_utc,
                end_utc,
                device_id=device,
                workload=workload,
            )
        return {"count": total}

    @local_app.get("/api/deviations/{window_id}")
    def deviation_by_window(
        request: Request,
        window_id: int,
    ) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            item = get_deviation_for_window(connection, window_id)
        if item is None:
            raise HTTPException(
                status_code=404,
                detail="No deviation assessment exists for this feature window.",
            )
        return item

    @local_app.get("/api/risk/status")
    def phase3b_risk_status(
        request: Request,
        device: str | None = None,
    ) -> dict[str, object]:
        return risk_status(request.app.state.database_path, device_id=device)

    @local_app.get("/api/risk/latest")
    def risk_latest(
        request: Request,
        device: str | None = None,
        workload: str | None = None,
        evidence_level: Literal[
            "low", "guarded", "elevated", "high", "critical_evidence"
        ] | None = None,
    ) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            items, _ = get_risk_assessments(
                connection,
                1,
                0,
                device_id=device,
                workload=workload,
                evidence_level=evidence_level,
            )
        if items:
            return {"status": "evaluated", "assessment": items[0]}
        status = risk_status(request.app.state.database_path, device_id=device)
        return {
            "status": "not_evaluated",
            "assessment": None,
            "reason_code": status["reason_code"],
            "baseline_state": status["baseline_state"],
            "prerequisites": status["prerequisites"],
        }

    @local_app.get("/api/risk/history")
    def risk_history(
        request: Request,
        limit: int = Query(default=50, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
        start: datetime | None = None,
        end: datetime | None = None,
        sort: Literal["oldest", "newest"] = "newest",
        device: str | None = None,
        workload: str | None = None,
        evidence_level: Literal[
            "low", "guarded", "elevated", "high", "critical_evidence"
        ] | None = None,
    ) -> dict[str, object]:
        start_utc, end_utc = _validated_range(start, end)
        with database_connection(request.app.state.database_path) as connection:
            items, total = get_risk_assessments(
                connection,
                limit,
                offset,
                start_utc,
                end_utc,
                sort,
                device,
                workload,
                evidence_level,
            )
        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "sort": sort,
            "start": start_utc,
            "end": end_utc,
        }

    @local_app.get("/api/risk/count")
    def risk_count(
        request: Request,
        start: datetime | None = None,
        end: datetime | None = None,
        device: str | None = None,
        workload: str | None = None,
        evidence_level: Literal[
            "low", "guarded", "elevated", "high", "critical_evidence"
        ] | None = None,
    ) -> dict[str, int]:
        start_utc, end_utc = _validated_range(start, end)
        with database_connection(request.app.state.database_path) as connection:
            _, total = get_risk_assessments(
                connection,
                1,
                0,
                start_utc,
                end_utc,
                device_id=device,
                workload=workload,
                evidence_level=evidence_level,
            )
        return {"count": total}

    @local_app.get("/api/risk/{window_id}")
    def risk_by_window(
        request: Request,
        window_id: int,
    ) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            item = get_risk_for_window(connection, window_id)
        if item:
            return {"status": "evaluated", "assessment": item}
        status = risk_status(request.app.state.database_path)
        return {
            "status": "not_evaluated",
            "assessment": None,
            "feature_window_id": window_id,
            "reason_code": status["reason_code"],
            "baseline_state": status["baseline_state"],
        }

    @local_app.get("/api/health/status")
    def phase4a_health_status(
        request: Request,
        device: str | None = None,
    ) -> dict[str, object]:
        return health_status(request.app.state.database_path, device_id=device)

    @local_app.get("/api/health/latest")
    def health_latest(
        request: Request,
        device: str | None = None,
        workload: str | None = None,
        health_band: Literal[
            "good", "stable", "attention", "degraded",
            "critical_condition", "not_evaluated",
        ] | None = None,
        evaluation_state: Literal[
            "not_evaluated", "provisional", "established"
        ] | None = None,
    ) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            items, _ = get_health_assessments(
                connection,
                1,
                0,
                device_id=device,
                workload=workload,
                health_band=health_band,
                evaluation_state=evaluation_state,
            )
        if items:
            return {
                "status": items[0]["evaluation_state"],
                "assessment": items[0],
                "interpretation": HEALTH_INTERPRETATION,
            }
        status = health_status(request.app.state.database_path, device_id=device)
        return {
            "status": "not_evaluated",
            "assessment": None,
            "reason_codes": status["reason_codes"] or ["no_health_assessment"],
            "interpretation": HEALTH_INTERPRETATION,
        }

    @local_app.get("/api/health/history")
    def health_history(
        request: Request,
        limit: int = Query(default=50, ge=1, le=5000),
        offset: int = Query(default=0, ge=0),
        summary: bool = Query(
            default=False,
            description=(
                "Return assessment-level fields without normalized detail "
                "collections. The default full response is unchanged."
            ),
        ),
        start: datetime | None = None,
        end: datetime | None = None,
        sort: Literal["oldest", "newest"] = "newest",
        device: str | None = None,
        workload: str | None = None,
        health_band: Literal[
            "good", "stable", "attention", "degraded",
            "critical_condition", "not_evaluated",
        ] | None = None,
        evaluation_state: Literal[
            "not_evaluated", "provisional", "established"
        ] | None = None,
    ) -> dict[str, object]:
        start_utc, end_utc = _validated_range(start, end)
        with database_connection(request.app.state.database_path) as connection:
            items, total = get_health_assessments(
                connection,
                limit,
                offset,
                start_utc,
                end_utc,
                sort,
                device,
                workload,
                health_band,
                evaluation_state,
                full=not summary,
            )
        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "sort": sort,
            "start": start_utc,
            "end": end_utc,
            "interpretation": HEALTH_INTERPRETATION,
        }

    @local_app.get("/api/health/count")
    def health_count(
        request: Request,
        start: datetime | None = None,
        end: datetime | None = None,
        device: str | None = None,
        workload: str | None = None,
        health_band: Literal[
            "good", "stable", "attention", "degraded",
            "critical_condition", "not_evaluated",
        ] | None = None,
        evaluation_state: Literal[
            "not_evaluated", "provisional", "established"
        ] | None = None,
    ) -> dict[str, int]:
        start_utc, end_utc = _validated_range(start, end)
        with database_connection(request.app.state.database_path) as connection:
            _, total = get_health_assessments(
                connection,
                1,
                0,
                start_utc,
                end_utc,
                "newest",
                device,
                workload,
                health_band,
                evaluation_state,
                full=False,
            )
        return {"count": total}

    @local_app.get("/api/health/{window_id}")
    def health_by_window(
        request: Request,
        window_id: int,
    ) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            item = get_health_for_window(connection, window_id)
        if item:
            return {
                "status": item["evaluation_state"],
                "assessment": item,
                "interpretation": HEALTH_INTERPRETATION,
            }
        return {
            "status": "not_evaluated",
            "assessment": None,
            "feature_window_id": window_id,
            "reason_codes": ["health_assessment_not_run_for_window"],
            "interpretation": HEALTH_INTERPRETATION,
        }

    @local_app.get("/api/quality/status")
    def phase4b_quality_status(
        request: Request,
        device: str | None = None,
    ) -> dict[str, object]:
        return quality_status(
            request.app.state.database_path,
            device_id=device,
            ensure_initialized=False,
        )

    @local_app.get("/api/quality/profiles")
    def quality_profiles() -> dict[str, object]:
        return {
            "profiles": list(WORKLOAD_PROFILES.values()),
            "interpretation": QUALITY_INTERPRETATION,
        }

    @local_app.get("/api/quality/profile-taxonomy")
    def quality_profile_taxonomy(request: Request) -> dict[str, object]:
        """Return additive fine profiles; GET never evaluates or backfills."""
        return profile_quality_status(request.app.state.database_path)

    @local_app.get("/api/quality/profile-scores")
    def quality_profile_scores(request: Request) -> dict[str, object]:
        return profile_quality_status(request.app.state.database_path)

    @local_app.get("/api/quality/inventory/latest")
    def quality_inventory_latest(
        request: Request,
        device: str | None = None,
    ) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            inventory = get_latest_inventory(connection, device)
        return {
            "status": "available" if inventory else "not_evaluated",
            "inventory": inventory,
            "reason_codes": [] if inventory else ["inventory_not_collected"],
            "interpretation": QUALITY_INTERPRETATION,
            "privacy_boundary": (
                "Only an allowlisted hardware and Windows capability inventory "
                "is stored. Serial numbers, user names, network identifiers, "
                "installed-software lists, and file data are excluded."
            ),
            "privacy_excluded_inputs": [
                {
                    "field_name": name,
                    "availability_status": "excluded_for_privacy",
                }
                for name in PRIVACY_EXCLUDED_FIELDS
            ],
        }

    @local_app.get("/api/quality/latest")
    def quality_latest(
        request: Request,
        device: str | None = None,
        profile: str | None = None,
        suitability_result: Literal[
            "well_suited", "suitable", "suitable_with_limits",
            "upgrade_recommended", "insufficient", "not_evaluated",
        ] | None = None,
        evaluation_state: Literal[
            "assessed", "provisional", "not_evaluated"
        ] | None = None,
    ) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            items, _ = get_quality_assessments(
                connection,
                1,
                0,
                device_id=device,
                profile_key=profile,
                suitability_result=suitability_result,
                evaluation_state=evaluation_state,
            )
        return {
            "status": (
                items[0]["evaluation_state"] if items else "not_evaluated"
            ),
            "assessment": items[0] if items else None,
            "reason_codes": [] if items else ["quality_assessment_not_run"],
            "interpretation": QUALITY_INTERPRETATION,
        }

    @local_app.get("/api/quality/history")
    def quality_history(
        request: Request,
        limit: int = Query(default=50, ge=1, le=5000),
        offset: int = Query(default=0, ge=0),
        start: datetime | None = None,
        end: datetime | None = None,
        sort: Literal["oldest", "newest"] = "newest",
        device: str | None = None,
        profile: str | None = None,
        suitability_result: Literal[
            "well_suited", "suitable", "suitable_with_limits",
            "upgrade_recommended", "insufficient", "not_evaluated",
        ] | None = None,
        evaluation_state: Literal[
            "assessed", "provisional", "not_evaluated"
        ] | None = None,
    ) -> dict[str, object]:
        start_utc, end_utc = _validated_range(start, end)
        with database_connection(request.app.state.database_path) as connection:
            items, total = get_quality_assessments(
                connection,
                limit,
                offset,
                start_utc,
                end_utc,
                sort,
                device,
                profile,
                suitability_result,
                evaluation_state,
            )
        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "sort": sort,
            "start": start_utc,
            "end": end_utc,
            "interpretation": QUALITY_INTERPRETATION,
        }

    @local_app.get("/api/quality/count")
    def quality_count(
        request: Request,
        start: datetime | None = None,
        end: datetime | None = None,
        device: str | None = None,
        profile: str | None = None,
        suitability_result: Literal[
            "well_suited", "suitable", "suitable_with_limits",
            "upgrade_recommended", "insufficient", "not_evaluated",
        ] | None = None,
        evaluation_state: Literal[
            "assessed", "provisional", "not_evaluated"
        ] | None = None,
    ) -> dict[str, int]:
        start_utc, end_utc = _validated_range(start, end)
        with database_connection(request.app.state.database_path) as connection:
            _, total = get_quality_assessments(
                connection,
                1,
                0,
                start_utc,
                end_utc,
                "newest",
                device,
                profile,
                suitability_result,
                evaluation_state,
                full=False,
            )
        return {"count": total}

    @local_app.get("/api/quality/{assessment_id}")
    def quality_by_assessment(
        request: Request,
        assessment_id: int,
    ) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            item = get_quality_assessment(connection, assessment_id)
        if item:
            return {
                "status": item["evaluation_state"],
                "assessment": item,
                "interpretation": QUALITY_INTERPRETATION,
            }
        return {
            "status": "not_evaluated",
            "assessment": None,
            "assessment_id": assessment_id,
            "reason_codes": ["quality_assessment_not_found"],
            "interpretation": QUALITY_INTERPRETATION,
        }

    @local_app.get("/api/root-causes/latest")
    def root_causes_latest(
        request: Request,
        device: str | None = None,
        workload: str | None = None,
        evidence_level: Literal[
            "low", "guarded", "elevated", "high", "critical_evidence"
        ] | None = None,
    ) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            items, _ = get_root_causes(
                connection,
                25,
                0,
                device_id=device,
                workload=workload,
                evidence_level=evidence_level,
            )
        if items:
            newest_window = items[0]["feature_window_id"]
            candidates = [
                item for item in items
                if item["feature_window_id"] == newest_window
            ]
            return {
                "status": "evaluated",
                "feature_window_id": newest_window,
                "candidates": candidates,
            }
        status = risk_status(request.app.state.database_path, device_id=device)
        if status["status"] == "evaluated":
            return {
                "status": "evaluated",
                "feature_window_id": status["latest_assessment"][
                    "feature_window_id"
                ],
                "candidates": [],
            }
        return {
            "status": "not_evaluated",
            "candidates": [],
            "reason_code": status["reason_code"],
            "baseline_state": status["baseline_state"],
        }

    @local_app.get("/api/root-causes/history")
    def root_causes_history(
        request: Request,
        limit: int = Query(default=50, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
        start: datetime | None = None,
        end: datetime | None = None,
        sort: Literal["oldest", "newest"] = "newest",
        device: str | None = None,
        workload: str | None = None,
        evidence_level: Literal[
            "low", "guarded", "elevated", "high", "critical_evidence"
        ] | None = None,
    ) -> dict[str, object]:
        start_utc, end_utc = _validated_range(start, end)
        with database_connection(request.app.state.database_path) as connection:
            items, total = get_root_causes(
                connection,
                limit,
                offset,
                start_utc,
                end_utc,
                sort,
                device,
                workload,
                evidence_level,
            )
        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "sort": sort,
            "start": start_utc,
            "end": end_utc,
        }

    @local_app.get("/api/root-causes/{window_id}")
    def root_causes_by_window(
        request: Request,
        window_id: int,
    ) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            items = get_root_causes_for_window(connection, window_id)
            risk_item = get_risk_for_window(connection, window_id)
        if items:
            return {
                "status": "evaluated",
                "feature_window_id": window_id,
                "candidates": items,
            }
        if risk_item:
            return {
                "status": "evaluated",
                "feature_window_id": window_id,
                "candidates": [],
            }
        status = risk_status(request.app.state.database_path)
        return {
            "status": "not_evaluated",
            "feature_window_id": window_id,
            "candidates": [],
            "reason_code": status["reason_code"],
            "baseline_state": status["baseline_state"],
        }

    @local_app.get("/api/alerts/status")
    def alerts_status(
        request: Request,
        device: str | None = None,
    ) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            result = get_alert_status(connection, device)
        return {
            **result,
            "algorithm_version": ALERT_ALGORITHM_VERSION,
            "configuration_version": ALERT_CONFIGURATION_VERSION,
            "catalogue_version": ALERT_CATALOGUE_VERSION,
            "interpretation": ALERT_INTERPRETATION,
        }

    @local_app.get("/api/alerts/latest")
    def alerts_latest(
        request: Request,
        device: str | None = None,
    ) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            items = get_latest_alerts(connection, device)
        return {
            "status": "evaluated" if items else "no_active_alerts",
            "items": items,
            "total": len(items),
            "interpretation": ALERT_INTERPRETATION,
        }

    @local_app.get("/api/alerts/history")
    def alerts_history(
        request: Request,
        limit: int = Query(default=50, ge=1, le=5000),
        offset: int = Query(default=0, ge=0),
        summary: bool = False,
        start: datetime | None = None,
        end: datetime | None = None,
        sort: Literal["oldest", "newest"] = "newest",
        device: str | None = None,
        category: Literal[
            "resource_pressure", "memory_and_swap_pressure",
            "disk_capacity_pressure", "disk_io_pressure", "thermal_evidence",
            "repeated_serious_event", "system_stability",
            "increasing_risk_evidence", "degraded_system_health",
            "data_quality_limitation",
        ] | None = None,
        severity: Literal[
            "informational", "advisory", "warning", "urgent"
        ] | None = None,
        state: Literal[
            "open", "acknowledged", "recovering", "resolved"
        ] | None = None,
        workload: str | None = None,
    ) -> dict[str, object]:
        start_utc, end_utc = _validated_range(start, end)
        with database_connection(request.app.state.database_path) as connection:
            items, total = get_alerts(
                connection, limit, offset, start_utc, end_utc, sort, device,
                category, severity, state, workload, full=not summary,
            )
        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "summary": summary,
            "sort": sort,
            "start": start_utc,
            "end": end_utc,
            "interpretation": ALERT_INTERPRETATION,
        }

    @local_app.get("/api/alerts/count")
    def alerts_count(
        request: Request,
        start: datetime | None = None,
        end: datetime | None = None,
        device: str | None = None,
        category: str | None = None,
        severity: str | None = None,
        state: str | None = None,
        workload: str | None = None,
    ) -> dict[str, int]:
        start_utc, end_utc = _validated_range(start, end)
        with database_connection(request.app.state.database_path) as connection:
            _, total = get_alerts(
                connection, 1, 0, start_utc, end_utc, "newest", device,
                category, severity, state, workload, full=False,
            )
        return {"count": total}

    @local_app.get("/api/alerts/{alert_id}")
    def alert_by_id(
        request: Request,
        alert_id: int,
    ) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            item = get_alert(connection, alert_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Alert not found.")
        return {"alert": item, "interpretation": ALERT_INTERPRETATION}

    @local_app.post("/api/alerts/{alert_id}/outcome")
    def alert_outcome(
        request: Request,
        alert_id: int,
        update: AlertOutcomeUpdate,
    ) -> dict[str, object]:
        """Append a label for academic validation without changing analytics."""
        def persist(connection):
            occurrence = connection.execute(
                """SELECT id FROM alert_occurrences WHERE alert_id=?
                ORDER BY observed_at_utc DESC,id DESC LIMIT 1""",
                (alert_id,),
            ).fetchone()
            if occurrence is None:
                raise ValueError("The alert has no occurrence to label.")
            return append_outcome(
                connection,
                int(occurrence["id"]),
                update.outcome,
                note=update.note,
                reason="user_supplied_academic_validation_label",
            )
        try:
            result = run_write_transaction(
                request.app.state.database_path, persist, priority="api"
            )
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {
            "outcome": result,
            "effect": "label_only_no_retraining_or_scoring_change",
        }

    @local_app.get("/api/notifications/status")
    def notifications_status(request: Request) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            preferences = get_notification_preferences(connection)
            delivery = get_notification_delivery_status(connection)
        if preferences is None:
            raise HTTPException(
                status_code=503,
                detail="Notification preferences are not initialized.",
            )
        provider = request.app.state.notification_provider
        return {
            **preferences,
            **provider.support_status(),
            **delivery,
            "semantic_severity_mapping": {
                "informational": "Low",
                "advisory": "Elevated",
                "warning": "High",
                "urgent": "Critical Evidence",
            },
            "notification_category_mapping": {
                "advisory": "Advisory",
                "warning": "Warning",
                "urgent": "Urgent",
            },
            "dashboard_url": get_dashboard_url(),
            "delivery_meaning": (
                "Delivered means the native Windows provider accepted the "
                "notification; it does not prove that a person saw it."
            ),
            "interpretation": ALERT_INTERPRETATION,
        }

    @local_app.put("/api/notifications/preferences")
    def notification_preferences_update(
        request: Request,
        body: NotificationPreferencesUpdate,
    ) -> dict[str, object]:
        def save_preferences(connection):
            previous = get_notification_preferences(connection)
            if previous is None:
                raise HTTPException(
                    status_code=503,
                    detail="Notification preferences are not initialized.",
                )
            preferences = update_notification_preferences(
                connection,
                enabled=body.enabled,
                eligible_categories=body.eligible_categories,
                eligible_severities=body.eligible_severities,
            )
            return previous, preferences

        try:
            previous, preferences = run_write_transaction(
                request.app.state.database_path,
                save_preferences,
                priority="api",
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

        changed_to_enabled = not bool(previous["enabled"]) and bool(
            preferences["enabled"]
        )
        changed_to_disabled = bool(previous["enabled"]) and not bool(
            preferences["enabled"]
        )
        confirmation = {
            "attempted": False,
            "delivered": False,
            "status": "not_applicable",
            "user_message": None,
        }
        if changed_to_enabled:
            provider = request.app.state.notification_provider
            result = provider.send(build_enabled_confirmation_message())
            confirmation = {
                "attempted": True,
                "delivered": result.delivered,
                "status": "submitted" if result.delivered else "failed",
                "user_message": (
                    "Windows accepted the notification. Focus Assist or an "
                    "organizational policy may still suppress its display."
                    if result.delivered
                    else (
                        "Notifications are enabled, but Windows could not "
                        "accept the confirmation notification."
                    )
                ),
            }
            if not result.delivered:
                LOGGER.warning(
                    "Notification enable-confirmation submission failed: %s",
                    result.failure_reason,
                )

        if changed_to_enabled:
            transition = "enabled"
            message = "Notifications have been enabled."
        elif changed_to_disabled:
            transition = "disabled"
            message = "Notifications have been disabled."
        else:
            transition = "unchanged"
            message = (
                "Notification preferences have been saved."
                if previous["eligible_categories"]
                != preferences["eligible_categories"]
                else (
                    "Notifications are already enabled."
                    if preferences["enabled"]
                    else "Notifications are already disabled."
                )
            )
        return {
            "preferences": preferences,
            "transition": transition,
            "message": message,
            "confirmation_notification": confirmation,
        }

    @local_app.post("/api/notifications/test")
    def notification_test(request: Request) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            preferences = get_notification_preferences(connection)
        if preferences is None:
            raise HTTPException(
                status_code=503,
                detail="Notification preferences are not initialized.",
            )
        if not preferences["enabled"]:
            return {
                "delivered": False,
                "status": "disabled",
                "reason": "Enable local Windows notifications before testing.",
                "test_only": True,
            }
        provider = request.app.state.notification_provider
        result = provider.send(build_test_message())
        if not result.delivered:
            LOGGER.warning(
                "Explicit test-notification submission failed: %s",
                result.failure_reason,
            )
        return {
            "delivered": result.delivered,
            "status": "submitted" if result.delivered else "failed",
            "reason": (
                None
                if result.delivered
                else (
                    "Windows could not accept the test notification. Check "
                    "native-notification support, Focus Assist, and policy."
                )
            ),
            "provider_name": result.provider_name,
            "test_only": True,
            "message": (
                "This test did not create an alert, incident, feedback record, "
                "risk assessment, or notification-delivery record."
            ),
        }

    @local_app.post("/api/alerts/{alert_id}/acknowledge")
    def alert_acknowledge(
        request: Request,
        alert_id: int,
    ) -> dict[str, object]:
        result = acknowledge_alert(request.app.state.database_path, alert_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Alert not found.")
        return {**result, "interpretation": ALERT_INTERPRETATION}

    @local_app.get("/api/validation/status")
    def phase5b_validation_status(
        request: Request,
        device: str | None = None,
    ) -> dict[str, object]:
        return validation_status(request.app.state.database_path, device=device)

    @local_app.get("/api/validation/registry")
    def validation_registry(request: Request) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            items = list_validations(connection)
        return {
            "items": items,
            "total": len(items),
            "default_display": "Not yet validated" if not items else None,
            "individual_alert_accuracy": None,
            "interpretation": (
                "Accuracy is a method-level labelled-outcome statistic. Alert "
                "confidence describes evidence quality and is not failure probability."
            ),
        }

    @local_app.get("/api/validation/summary")
    def validation_summary(
        request: Request,
        device: str | None = None,
    ) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            row = connection.execute(
                """SELECT id FROM validation_evaluation_runs
                WHERE (? IS NULL OR device_id = ?)
                ORDER BY finished_at_utc DESC, id DESC LIMIT 1""",
                (device, device),
            ).fetchone()
            item = validation_run(connection, int(row["id"])) if row else None
        return {
            "status": item["status"] if item else "not_evaluated",
            "result": item,
            "interpretation": VALIDATION_INTERPRETATION,
        }

    @local_app.get("/api/validation/history")
    def validation_history(
        request: Request,
        limit: int = Query(default=50, ge=1, le=5000),
        offset: int = Query(default=0, ge=0),
        device: str | None = None,
    ) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            items, total = list_validation_runs(
                connection, limit=limit, offset=offset, device_id=device
            )
        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "interpretation": VALIDATION_INTERPRETATION,
        }

    @local_app.get("/api/validation/metrics")
    def validation_metrics(
        request: Request,
        run_id: int | None = None,
        limit: int = Query(default=100, ge=1, le=5000),
        offset: int = Query(default=0, ge=0),
        scope_type: str | None = None,
        scope_value: str | None = None,
        metric_name: str | None = None,
        evaluation_state: Literal[
            "evaluated", "insufficient_labeled_evidence"
        ] | None = None,
    ) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            resolved_run = run_id
            if resolved_run is None:
                row = connection.execute(
                    """SELECT id FROM validation_evaluation_runs
                    ORDER BY finished_at_utc DESC, id DESC LIMIT 1"""
                ).fetchone()
                resolved_run = int(row["id"]) if row else None
            if resolved_run is None:
                return {
                    "items": [], "total": 0, "limit": limit, "offset": offset,
                    "run_id": None, "interpretation": VALIDATION_INTERPRETATION,
                }
            clauses = ["evaluation_run_id = ?"]
            values: list[Any] = [resolved_run]
            for clause, value in (
                ("scope_type = ?", scope_type),
                ("scope_value = ?", scope_value),
                ("metric_name = ?", metric_name),
                ("evaluation_state = ?", evaluation_state),
            ):
                if value is not None:
                    clauses.append(clause)
                    values.append(value)
            where = " AND ".join(clauses)
            total = int(connection.execute(
                f"SELECT COUNT(*) FROM validation_metric_results WHERE {where}",
                values,
            ).fetchone()[0])
            items = [
                expand_validation_row(row) or {}
                for row in connection.execute(
                    f"""SELECT * FROM validation_metric_results WHERE {where}
                    ORDER BY scope_type, scope_value, metric_name
                    LIMIT ? OFFSET ?""",
                    (*values, limit, offset),
                )
            ]
        return {
            "items": items, "total": total, "limit": limit, "offset": offset,
            "run_id": resolved_run, "interpretation": VALIDATION_INTERPRETATION,
        }

    @local_app.get("/api/validation/lead-times")
    def validation_lead_times(
        request: Request,
        run_id: int | None = None,
        limit: int = Query(default=100, ge=1, le=5000),
        offset: int = Query(default=0, ge=0),
        timing_state: Literal["advance_warning", "late_detection"] | None = None,
    ) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            resolved_run = run_id
            if resolved_run is None:
                row = connection.execute(
                    """SELECT id FROM validation_evaluation_runs
                    ORDER BY finished_at_utc DESC, id DESC LIMIT 1"""
                ).fetchone()
                resolved_run = int(row["id"]) if row else None
            if resolved_run is None:
                return {
                    "items": [], "total": 0, "limit": limit, "offset": offset,
                    "run_id": None, "interpretation": VALIDATION_INTERPRETATION,
                }
            clauses = ["evaluation_run_id = ?"]
            values: list[Any] = [resolved_run]
            if timing_state is not None:
                clauses.append("timing_state = ?")
                values.append(timing_state)
            where = " AND ".join(clauses)
            total = int(connection.execute(
                f"SELECT COUNT(*) FROM warning_lead_time_results WHERE {where}",
                values,
            ).fetchone()[0])
            items = [
                expand_validation_row(row) or {}
                for row in connection.execute(
                    f"""SELECT * FROM warning_lead_time_results WHERE {where}
                    ORDER BY incident_start_utc DESC, id DESC
                    LIMIT ? OFFSET ?""",
                    (*values, limit, offset),
                )
            ]
        return {
            "items": items, "total": total, "limit": limit, "offset": offset,
            "run_id": resolved_run, "interpretation": VALIDATION_INTERPRETATION,
        }

    @local_app.get("/api/validation/unverified-alerts")
    def validation_unverified_alerts(
        request: Request,
        device: str | None = None,
        limit: int = Query(default=100, ge=1, le=5000),
    ) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            items = list_unverified_alerts(
                connection, device_id=device, limit=limit
            )
        return {
            "items": items,
            "total": len(items),
            "interpretation": VALIDATION_INTERPRETATION,
        }

    @local_app.get("/api/validation/feedback")
    def validation_feedback_history(
        request: Request,
        limit: int = Query(default=50, ge=1, le=5000),
        offset: int = Query(default=0, ge=0),
        start: datetime | None = None,
        end: datetime | None = None,
        device: str | None = None,
        outcome: str | None = None,
        verification_status: str | None = None,
        minimum_confidence: float | None = Query(default=None, ge=0, le=1),
    ) -> dict[str, object]:
        start_utc, end_utc = _validated_range(start, end)
        with database_connection(request.app.state.database_path) as connection:
            items, total = list_feedback(
                connection,
                limit=limit,
                offset=offset,
                start=start_utc,
                end=end_utc,
                device_id=device,
                outcome=outcome,
                verification_status=verification_status,
                minimum_confidence=minimum_confidence,
            )
        return {
            "items": items, "total": total, "limit": limit, "offset": offset,
            "start": start_utc, "end": end_utc,
            "interpretation": VALIDATION_INTERPRETATION,
        }

    @local_app.get("/api/incidents/history")
    def incident_history(
        request: Request,
        limit: int = Query(default=50, ge=1, le=5000),
        offset: int = Query(default=0, ge=0),
        start: datetime | None = None,
        end: datetime | None = None,
        sort: Literal["oldest", "newest"] = "newest",
        device: str | None = None,
        category: str | None = None,
        severity: str | None = None,
        verification_status: str | None = None,
        status: Literal["active", "withdrawn"] | None = None,
        minimum_confidence: float | None = Query(default=None, ge=0, le=1),
        maximum_confidence: float | None = Query(default=None, ge=0, le=1),
    ) -> dict[str, object]:
        start_utc, end_utc = _validated_range(start, end)
        with database_connection(request.app.state.database_path) as connection:
            items, total = list_incidents(
                connection,
                limit=limit,
                offset=offset,
                start=start_utc,
                end=end_utc,
                sort=sort,
                device_id=device,
                category=category,
                severity=severity,
                verification_status=verification_status,
                status=status,
                minimum_confidence=minimum_confidence,
                maximum_confidence=maximum_confidence,
            )
        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "sort": sort,
        }

    @local_app.get("/api/incidents")
    def incidents_alias(
        request: Request,
        limit: int = Query(default=50, ge=1, le=5000),
        offset: int = Query(default=0, ge=0),
        start: datetime | None = None,
        end: datetime | None = None,
        sort: Literal["oldest", "newest"] = "newest",
        device: str | None = None,
        category: str | None = None,
        severity: str | None = None,
        verification_status: str | None = None,
        status: Literal["active", "withdrawn"] | None = None,
        minimum_confidence: float | None = Query(default=None, ge=0, le=1),
        maximum_confidence: float | None = Query(default=None, ge=0, le=1),
    ) -> dict[str, object]:
        start_utc, end_utc = _validated_range(start, end)
        with database_connection(request.app.state.database_path) as connection:
            items, total = list_incidents(
                connection,
                limit=limit,
                offset=offset,
                start=start_utc,
                end=end_utc,
                sort=sort,
                device_id=device,
                category=category,
                severity=severity,
                verification_status=verification_status,
                status=status,
                minimum_confidence=minimum_confidence,
                maximum_confidence=maximum_confidence,
            )
        return {
            "items": items, "total": total, "limit": limit, "offset": offset,
            "sort": sort, "start": start_utc, "end": end_utc,
        }

    @local_app.get("/api/incidents/{incident_id}")
    def incident_by_id(
        request: Request,
        incident_id: int,
    ) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            item = get_incident(connection, incident_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Incident not found.")
        return {"incident": item, "interpretation": VALIDATION_INTERPRETATION}

    @local_app.post("/api/incidents")
    def incident_create(
        request: Request,
        payload: IncidentCreate,
    ) -> dict[str, object]:
        values = {
            "category": payload.category,
            "severity": payload.severity,
            "start_utc": utc_string(payload.start_utc),
            "end_utc": utc_string(payload.end_utc) if payload.end_utc else None,
            "timestamp_precision": payload.timestamp_precision,
            "verification_status": payload.verification_status,
            "verification_source": payload.verification_source,
            "workload_context": payload.workload_context,
            "symptoms_text": payload.symptoms_text,
            "windows_event_ids_json": validation_json(payload.windows_event_ids),
            "action_taken": payload.action_taken,
            "observed_outcome": payload.observed_outcome,
            "status": (
                "withdrawn"
                if payload.verification_status == "withdrawn"
                else "active"
            ),
            "data_confidence": payload.data_confidence,
            "reason_codes_json": "[]",
            "limitations_json": "[]",
        }
        with database_connection(request.app.state.database_path) as connection:
            with connection:
                item = create_incident(
                    connection, device_id=payload.device_id, values=values
                )
        return {"incident": item, "interpretation": VALIDATION_INTERPRETATION}

    @local_app.post("/api/incidents/{incident_id}/revise")
    def incident_revise(
        request: Request,
        incident_id: int,
        payload: IncidentRevision,
    ) -> dict[str, object]:
        raw = payload.model_dump(
            exclude={"confirmation", "revision_reason"},
            exclude_unset=True,
        )
        updates: dict[str, Any] = {}
        for key, value in raw.items():
            if key in {"start_utc", "end_utc"} and value is not None:
                updates[key] = utc_string(value)
            elif key == "windows_event_ids":
                updates["windows_event_ids_json"] = validation_json(value or [])
            else:
                updates[key] = value
        if updates.get("verification_status") == "withdrawn":
            updates["status"] = "withdrawn"
        with database_connection(request.app.state.database_path) as connection:
            with connection:
                item = revise_incident(
                    connection,
                    incident_id,
                    updates=updates,
                    revision_reason=payload.revision_reason,
                )
        if item is None:
            raise HTTPException(status_code=404, detail="Incident not found.")
        return {"incident": item, "interpretation": VALIDATION_INTERPRETATION}

    @local_app.post("/api/incidents/{incident_id}/withdraw")
    def incident_withdraw(
        request: Request,
        incident_id: int,
        payload: WithdrawRequest,
    ) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            with connection:
                item = revise_incident(
                    connection,
                    incident_id,
                    updates={
                        "status": "withdrawn",
                        "reason_codes_json": validation_json(["withdrawn_by_user"]),
                    },
                    revision_reason=payload.revision_reason,
                )
        if item is None:
            raise HTTPException(status_code=404, detail="Incident not found.")
        return {"incident": item, "interpretation": VALIDATION_INTERPRETATION}

    @local_app.post("/api/incidents/{incident_id}/link-alert")
    def incident_link_alert(
        request: Request,
        incident_id: int,
        payload: AlertIncidentLinkCreate,
    ) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            incident = connection.execute(
                "SELECT * FROM incident_reports WHERE id = ?", (incident_id,)
            ).fetchone()
            alert = connection.execute(
                "SELECT * FROM alerts WHERE id = ?", (payload.alert_id,)
            ).fetchone()
            if incident is None:
                raise HTTPException(status_code=404, detail="Incident not found.")
            if alert is None:
                raise HTTPException(status_code=404, detail="Alert not found.")
            if incident["device_id"] != alert["device_id"]:
                raise HTTPException(
                    status_code=422,
                    detail="Alert and incident must belong to the same device.",
                )
            difference = (
                datetime.fromisoformat(
                    incident["start_utc"].replace("Z", "+00:00")
                )
                - datetime.fromisoformat(
                    alert["first_observed_utc"].replace("Z", "+00:00")
                )
            ).total_seconds()
            compatible = incident["category"] in CATEGORY_COMPATIBILITY.get(
                alert["category"], set()
            )
            with connection:
                item = upsert_alert_incident_link(
                    connection,
                    alert_id=payload.alert_id,
                    incident_id=incident_id,
                    match_type=payload.match_type,
                    origin="manual",
                    confirmed_by_user=payload.match_type == "confirmed_match",
                    matching_score=1.0 if payload.match_type == "confirmed_match" else 0.5,
                    time_difference_seconds=difference,
                    category_compatible=compatible,
                    matching_rule="explicit_user_link",
                    supporting_evidence=[payload.reason],
                    contradictory_evidence=[] if compatible else [
                        "category_not_explicitly_compatible"
                    ],
                    reason_codes=["explicit_user_classification"],
                )
        return {"link": item, "interpretation": VALIDATION_INTERPRETATION}

    @local_app.get("/api/alerts/{alert_id}/feedback")
    def alert_feedback_get(
        request: Request,
        alert_id: int,
    ) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            alert_exists = connection.execute(
                "SELECT 1 FROM alerts WHERE id = ?", (alert_id,)
            ).fetchone()
            if not alert_exists:
                raise HTTPException(status_code=404, detail="Alert not found.")
            item = get_feedback(connection, alert_id)
        return {
            "status": "available" if item else "unverified",
            "feedback": item,
            "interpretation": VALIDATION_INTERPRETATION,
        }

    @local_app.post("/api/alerts/{alert_id}/feedback")
    def alert_feedback_create(
        request: Request,
        alert_id: int,
        payload: AlertFeedbackCreate,
    ) -> dict[str, object]:
        values = {
            "outcome": payload.outcome,
            "observation_horizon_seconds": payload.observation_horizon_seconds,
            "verification_status": payload.verification_status,
            "verification_timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "verification_source": payload.verification_source,
            "notes": payload.notes,
            "user_reason_codes_json": validation_json(payload.user_reason_codes),
            "structured_action_taken": payload.structured_action_taken,
            "condition_state": payload.condition_state,
            "preventive_action_taken": int(payload.preventive_action_taken),
            "status": "withdrawn" if payload.outcome == "withdrawn" else "active",
            "data_confidence": payload.data_confidence,
            "reason_codes_json": "[]",
            "limitations_json": "[]",
        }
        with database_connection(request.app.state.database_path) as connection:
            if not connection.execute(
                "SELECT 1 FROM alerts WHERE id = ?", (alert_id,)
            ).fetchone():
                raise HTTPException(status_code=404, detail="Alert not found.")
            if get_feedback(connection, alert_id):
                raise HTTPException(
                    status_code=409,
                    detail="Feedback already exists; create a revision instead.",
                )
            with connection:
                item = create_feedback(
                    connection, alert_id=alert_id, values=values
                )
        return {"feedback": item, "interpretation": VALIDATION_INTERPRETATION}

    @local_app.post("/api/alerts/{alert_id}/feedback/revise")
    def alert_feedback_revise(
        request: Request,
        alert_id: int,
        payload: AlertFeedbackRevision,
    ) -> dict[str, object]:
        values = {
            "outcome": payload.outcome,
            "observation_horizon_seconds": payload.observation_horizon_seconds,
            "verification_status": payload.verification_status,
            "verification_timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "verification_source": payload.verification_source,
            "notes": payload.notes,
            "user_reason_codes_json": validation_json(payload.user_reason_codes),
            "structured_action_taken": payload.structured_action_taken,
            "condition_state": payload.condition_state,
            "preventive_action_taken": int(payload.preventive_action_taken),
            "status": "withdrawn" if payload.outcome == "withdrawn" else "active",
            "data_confidence": payload.data_confidence,
            "reason_codes_json": "[]",
            "limitations_json": "[]",
        }
        with database_connection(request.app.state.database_path) as connection:
            with connection:
                item = revise_feedback(
                    connection,
                    alert_id,
                    updates=values,
                    revision_reason=payload.revision_reason,
                )
        if item is None:
            raise HTTPException(status_code=404, detail="Feedback not found.")
        return {"feedback": item, "interpretation": VALIDATION_INTERPRETATION}

    @local_app.get("/api/validation/observation-periods")
    def observation_period_history(
        request: Request,
        device: str | None = None,
        limit: int = Query(default=100, ge=1, le=5000),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            items, total = list_observation_periods(
                connection,
                device_id=device,
                limit=limit,
                offset=offset,
            )
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @local_app.get("/api/validation/periods")
    def observation_period_alias(
        request: Request,
        device: str | None = None,
        limit: int = Query(default=100, ge=1, le=5000),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            items, total = list_observation_periods(
                connection, device_id=device, limit=limit, offset=offset
            )
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @local_app.post("/api/validation/observation-periods")
    def observation_period_create(
        request: Request,
        payload: ObservationPeriodCreate,
    ) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            with connection:
                item = create_observation_period(
                    connection,
                    device_id=payload.device_id,
                    start_utc=utc_string(payload.start_utc),
                    interruption_notes=payload.interruption_notes,
                )
        return {"observation_period": item}

    @local_app.post("/api/validation/periods")
    def observation_period_create_alias(
        request: Request,
        payload: ObservationPeriodCreate,
    ) -> dict[str, object]:
        return observation_period_create(request, payload)

    @local_app.post("/api/validation/observation-periods/{period_id}/close")
    def observation_period_close(
        request: Request,
        period_id: int,
        payload: ObservationPeriodClose,
    ) -> dict[str, object]:
        with database_connection(request.app.state.database_path) as connection:
            period = get_observation_period(connection, period_id)
            if period is None:
                raise HTTPException(
                    status_code=404, detail="Observation period not found."
                )
            end_utc = utc_string(payload.end_utc)
            if end_utc < period["start_utc"]:
                raise HTTPException(
                    status_code=422,
                    detail="Observation period end must not be before its start.",
                )
            with connection:
                item = close_observation_period(
                    connection,
                    period_id,
                    end_utc=end_utc,
                    incident_reporting_complete=payload.incident_reporting_complete,
                    state=payload.state,
                    missing_intervals=payload.missing_intervals,
                    interruption_notes=payload.interruption_notes,
                )
        if item is None:
            raise HTTPException(
                status_code=409,
                detail="Only an open observation period can be closed.",
            )
        return {"observation_period": item}

    @local_app.post("/api/validation/periods/{period_id}/close")
    def observation_period_close_alias(
        request: Request,
        period_id: int,
        payload: ObservationPeriodClose,
    ) -> dict[str, object]:
        return observation_period_close(request, period_id, payload)

    return local_app


app = create_app()
