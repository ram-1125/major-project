"""Pydantic response models for the local SmartOps API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class StatusResponse(BaseModel):
    status: str
    database: str


class DiskPartitionResponse(BaseModel):
    mountpoint: str
    percent: float | None = None
    used_bytes: int | None = None
    free_bytes: int | None = None
    total_bytes: int | None = None


class MetricResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp_utc: str
    device_id: str
    cpu_percent: float | None = None
    cpu_per_core_percent: list[float] | None = None
    cpu_physical_cores: int | None = None
    cpu_logical_cores: int | None = None
    cpu_frequency_mhz: float | None = None
    process_count: int | None = None
    thread_count: int | None = None
    ram_percent: float | None = None
    ram_used_bytes: int | None = None
    ram_available_bytes: int | None = None
    ram_total_bytes: int | None = None
    swap_percent: float | None = None
    swap_used_bytes: int | None = None
    swap_total_bytes: int | None = None
    disk_percent: float | None = None
    disk_used_bytes: int | None = None
    disk_free_bytes: int | None = None
    disk_total_bytes: int | None = None
    disk_partitions: list[DiskPartitionResponse] | None = None
    disk_read_bytes_per_second: float | None = None
    disk_write_bytes_per_second: float | None = None
    disk_read_ops_per_second: float | None = None
    disk_write_ops_per_second: float | None = None
    disk_read_bytes_total: int | None = None
    disk_write_bytes_total: int | None = None
    disk_read_ops_total: int | None = None
    disk_write_ops_total: int | None = None
    network_upload_bytes_per_second: float | None = None
    network_download_bytes_per_second: float | None = None
    network_packets_sent_per_second: float | None = None
    network_packets_received_per_second: float | None = None
    network_bytes_sent_total: int | None = None
    network_bytes_received_total: int | None = None
    network_packets_sent_total: int | None = None
    network_packets_received_total: int | None = None
    network_interface_available: bool | None = None
    battery_percent: float | None = None
    battery_charging: bool | None = None
    ac_power_connected: bool | None = None
    battery_seconds_remaining: float | None = None
    uptime_seconds: float | None = None
    boot_timestamp_utc: str | None = None
    user_idle_seconds: float | None = None
    user_state: Literal["active", "idle"] | None = None
    foreground_process_name: str | None = None
    cpu_temperature_celsius: float | None = None
    gpu_utilization_percent: float | None = None
    gpu_memory_percent: float | None = None
    gpu_temperature_celsius: float | None = None
    workload_class: str | None = None
    workload_confidence: float | None = None
    workload_reasons: list[str] | None = None
    user_activity_state: Literal["active", "idle"] | None = None
    system_activity_state: Literal["quiescent", "background", "busy"] | None = None
    workload_rule_version: str | None = None
    workload_provenance: dict[str, object] | None = None


class HistoryResponse(BaseModel):
    items: list[MetricResponse]
    total: int
    limit: int
    offset: int
    sort: Literal["oldest", "newest"]
    start: str | None = None
    end: str | None = None


class CountResponse(BaseModel):
    count: int


class ProcessResponse(BaseModel):
    rank: int
    pid: int
    process_name: str
    cpu_percent: float | None = None
    memory_percent: float | None = None


class ProcessSnapshotsResponse(BaseModel):
    metric_id: int
    timestamp_utc: str
    cpu: list[ProcessResponse]
    memory: list[ProcessResponse]
