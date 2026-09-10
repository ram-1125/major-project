"""Strict request models for user-entered Phase 5B evidence."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator


IncidentCategory = Literal[
    "system_crash",
    "unexpected_restart",
    "application_failure",
    "system_freeze",
    "severe_slowdown",
    "memory_exhaustion",
    "disk_capacity_issue",
    "disk_io_issue",
    "thermal_shutdown_or_throttling",
    "driver_or_device_issue",
    "repeated_serious_event",
    "other_operational_issue",
]
IncidentSeverity = Literal["minor", "moderate", "serious", "critical"]
VerificationStatus = Literal[
    "user_reported", "externally_verified", "uncertain", "withdrawn"
]


def utc_string(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


class ConfirmedWrite(BaseModel):
    confirmation: bool

    @model_validator(mode="after")
    def require_confirmation(self) -> "ConfirmedWrite":
        if not self.confirmation:
            raise ValueError("Explicit confirmation is required before saving.")
        return self


class ObservationPeriodCreate(ConfirmedWrite):
    device_id: str = Field(min_length=1, max_length=200)
    start_utc: datetime
    interruption_notes: str | None = Field(default=None, max_length=1000)


class ObservationPeriodClose(ConfirmedWrite):
    end_utc: datetime
    state: Literal["completed", "incomplete", "withdrawn"]
    incident_reporting_complete: bool
    declared_outcome: Literal["no_meaningful_issue", "issue_occurred"]
    related_incident_id: int | None = Field(default=None, gt=0)
    missing_intervals: list[dict[str, str]] = Field(default_factory=list, max_length=100)
    interruption_notes: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def valid_declared_outcome(self) -> "ObservationPeriodClose":
        if self.declared_outcome == "issue_occurred" and self.related_incident_id is None:
            raise ValueError("An issue outcome requires the related Incident ID.")
        return self


class IncidentBase(BaseModel):
    category: IncidentCategory
    severity: IncidentSeverity
    start_utc: datetime
    end_utc: datetime | None = None
    timestamp_precision: Literal["exact", "approximate"]
    verification_status: VerificationStatus
    verification_source: str | None = Field(default=None, max_length=200)
    workload_context: str | None = Field(default=None, max_length=100)
    symptoms_text: str = Field(min_length=3, max_length=1500)
    windows_event_ids: list[int] = Field(default_factory=list, max_length=100)
    action_taken: str | None = Field(default=None, max_length=1000)
    observed_outcome: str | None = Field(default=None, max_length=1000)
    data_confidence: float = Field(default=0.75, ge=0, le=1)

    @model_validator(mode="after")
    def valid_incident(self) -> "IncidentBase":
        if self.end_utc is not None and self.end_utc < self.start_utc:
            raise ValueError("Incident end must not be before its start.")
        if (
            self.verification_status == "externally_verified"
            and not self.verification_source
        ):
            raise ValueError("Externally verified incidents require a source.")
        return self


class IncidentCreate(IncidentBase, ConfirmedWrite):
    device_id: str = Field(min_length=1, max_length=200)


class IncidentRevision(ConfirmedWrite):
    revision_reason: str = Field(min_length=3, max_length=500)
    category: IncidentCategory | None = None
    severity: IncidentSeverity | None = None
    start_utc: datetime | None = None
    end_utc: datetime | None = None
    timestamp_precision: Literal["exact", "approximate"] | None = None
    verification_status: VerificationStatus | None = None
    verification_source: str | None = Field(default=None, max_length=200)
    workload_context: str | None = Field(default=None, max_length=100)
    symptoms_text: str | None = Field(default=None, min_length=3, max_length=1500)
    windows_event_ids: list[int] | None = Field(default=None, max_length=100)
    action_taken: str | None = Field(default=None, max_length=1000)
    observed_outcome: str | None = Field(default=None, max_length=1000)
    data_confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def require_a_change(self) -> "IncidentRevision":
        changed = self.model_fields_set - {"confirmation", "revision_reason"}
        if not changed:
            raise ValueError("At least one incident field must be corrected.")
        if (
            self.verification_status == "externally_verified"
            and "verification_source" in self.model_fields_set
            and not self.verification_source
        ):
            raise ValueError("Externally verified incidents require a source.")
        return self


class WithdrawRequest(ConfirmedWrite):
    revision_reason: str = Field(min_length=3, max_length=500)


class AlertFeedbackCreate(ConfirmedWrite):
    outcome: Literal[
        "confirmed_related_issue",
        "likely_related_issue",
        "no_issue_observed",
        "preventive_action_taken",
        "uncertain",
        "not_yet_verified",
        "incorrect_category",
        "withdrawn",
    ]
    observation_horizon_seconds: float | None = Field(default=None, ge=0)
    verification_status: VerificationStatus
    verification_source: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=1500)
    user_reason_codes: list[str] = Field(default_factory=list, max_length=30)
    structured_action_taken: str | None = Field(default=None, max_length=500)
    condition_state: Literal["continued", "recovered", "unclear"] = "unclear"
    preventive_action_taken: bool = False
    data_confidence: float = Field(default=0.75, ge=0, le=1)

    @model_validator(mode="after")
    def valid_feedback(self) -> "AlertFeedbackCreate":
        if self.outcome == "no_issue_observed" and self.observation_horizon_seconds is None:
            raise ValueError("No-issue feedback requires an observation horizon.")
        if (
            self.verification_status == "externally_verified"
            and not self.verification_source
        ):
            raise ValueError("Externally verified feedback requires a source.")
        return self


class AlertFeedbackRevision(AlertFeedbackCreate):
    revision_reason: str = Field(min_length=3, max_length=500)


class AlertIncidentLinkCreate(ConfirmedWrite):
    alert_id: int = Field(gt=0)
    match_type: Literal[
        "confirmed_match", "probable_match", "possible_match", "rejected_match",
        "unmatched",
    ]
    reason: str = Field(min_length=3, max_length=500)


class IncidentMatchConfirmation(ConfirmedWrite):
    alert_id: int = Field(gt=0)
    accept: bool
