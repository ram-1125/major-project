"""Database queries kept separate from API and collection logic."""

from __future__ import annotations

import json
import sqlite3
from contextlib import nullcontext
from typing import Any, Mapping, Sequence


METRIC_COLUMNS = (
    "timestamp_utc",
    "device_id",
    "cpu_percent",
    "cpu_per_core_json",
    "cpu_physical_cores",
    "cpu_logical_cores",
    "cpu_frequency_mhz",
    "process_count",
    "thread_count",
    "ram_percent",
    "ram_used_bytes",
    "ram_available_bytes",
    "ram_total_bytes",
    "swap_percent",
    "swap_used_bytes",
    "swap_total_bytes",
    "disk_percent",
    "disk_used_bytes",
    "disk_free_bytes",
    "disk_total_bytes",
    "disk_partitions_json",
    "disk_read_bytes_per_second",
    "disk_write_bytes_per_second",
    "disk_read_ops_per_second",
    "disk_write_ops_per_second",
    "disk_read_bytes_total",
    "disk_write_bytes_total",
    "disk_read_ops_total",
    "disk_write_ops_total",
    "network_upload_bytes_per_second",
    "network_download_bytes_per_second",
    "network_packets_sent_per_second",
    "network_packets_received_per_second",
    "network_bytes_sent_total",
    "network_bytes_received_total",
    "network_packets_sent_total",
    "network_packets_received_total",
    "network_interface_available",
    "battery_percent",
    "battery_charging",
    "ac_power_connected",
    "battery_seconds_remaining",
    "uptime_seconds",
    "boot_timestamp_utc",
    "user_idle_seconds",
    "user_state",
    "foreground_process_name",
    "cpu_temperature_celsius",
    "gpu_utilization_percent",
    "gpu_memory_percent",
    "gpu_temperature_celsius",
    "workload_class",
    "workload_confidence",
    "workload_reasons_json",
    "user_activity_state",
    "system_activity_state",
    "workload_rule_version",
    "workload_provenance_json",
)

JSON_COLUMN_SOURCES = {
    "cpu_per_core_json": "cpu_per_core_percent",
    "disk_partitions_json": "disk_partitions",
    "workload_reasons_json": "workload_reasons",
    "workload_provenance_json": "workload_provenance",
}

BOOLEAN_COLUMNS = (
    "network_interface_available",
    "battery_charging",
    "ac_power_connected",
)


def _metric_values(sample: Mapping[str, Any]) -> tuple[Any, ...]:
    values: list[Any] = []
    for column in METRIC_COLUMNS:
        if column in JSON_COLUMN_SOURCES:
            raw_value = sample.get(JSON_COLUMN_SOURCES[column])
            values.append(json.dumps(raw_value) if raw_value is not None else None)
        else:
            values.append(sample.get(column))
    return tuple(values)


def _deserialize_metric(row: sqlite3.Row) -> dict[str, Any]:
    metric = dict(row)
    cpu_json = metric.pop("cpu_per_core_json", None)
    partitions_json = metric.pop("disk_partitions_json", None)
    workload_reasons_json = metric.pop("workload_reasons_json", None)
    workload_provenance_json = metric.pop("workload_provenance_json", None)
    metric["cpu_per_core_percent"] = json.loads(cpu_json) if cpu_json else None
    metric["disk_partitions"] = (
        json.loads(partitions_json) if partitions_json else None
    )
    metric["workload_reasons"] = (
        json.loads(workload_reasons_json) if workload_reasons_json else None
    )
    metric["workload_provenance"] = (
        json.loads(workload_provenance_json) if workload_provenance_json else None
    )
    for column in BOOLEAN_COLUMNS:
        if metric.get(column) is not None:
            metric[column] = bool(metric[column])
    return metric


def insert_metric(
    connection: sqlite3.Connection,
    sample: Mapping[str, Any],
    process_snapshots: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    *,
    manage_transaction: bool = True,
) -> int:
    """Insert a metric and its top-process rows in one transaction."""
    placeholders = ", ".join("?" for _ in METRIC_COLUMNS)
    columns = ", ".join(METRIC_COLUMNS)
    snapshots = process_snapshots or sample.get("process_snapshots") or {}

    transaction = connection if manage_transaction else nullcontext(connection)
    with transaction:
        # The device/timestamp pair is the stable identity of one genuine raw
        # collection. This makes a whole-transaction retry safe even if the
        # caller did not receive an already committed result.
        existing = connection.execute(
            "SELECT id FROM metrics WHERE device_id = ? AND timestamp_utc = ?",
            (sample.get("device_id"), sample.get("timestamp_utc")),
        ).fetchone()
        if existing is not None:
            return int(existing[0])
        cursor = connection.execute(
            f"INSERT INTO metrics ({columns}) VALUES ({placeholders})",
            _metric_values(sample),
        )
        metric_id = int(cursor.lastrowid)

        for category in ("cpu", "memory"):
            for rank, process in enumerate(snapshots.get(category, [])[:5], start=1):
                connection.execute(
                    """
                    INSERT INTO process_snapshots (
                        metric_id,
                        category,
                        rank,
                        pid,
                        process_name,
                        cpu_percent,
                        memory_percent
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        metric_id,
                        category,
                        rank,
                        process["pid"],
                        process["process_name"],
                        process.get("cpu_percent"),
                        process.get("memory_percent"),
                    ),
                )
    return metric_id


def get_latest_metric(connection: sqlite3.Connection) -> dict[str, Any] | None:
    """Return the newest sample, including nullable Phase 2A/2B fields."""
    row = connection.execute(
        "SELECT * FROM metrics ORDER BY timestamp_utc DESC, id DESC LIMIT 1"
    ).fetchone()
    return _deserialize_metric(row) if row else None


def _history_filter(
    start_timestamp: str | None,
    end_timestamp: str | None,
) -> tuple[str, list[Any]]:
    conditions: list[str] = []
    parameters: list[Any] = []
    if start_timestamp is not None:
        conditions.append("timestamp_utc >= ?")
        parameters.append(start_timestamp)
    if end_timestamp is not None:
        conditions.append("timestamp_utc <= ?")
        parameters.append(end_timestamp)
    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    return where_clause, parameters


def count_metrics(
    connection: sqlite3.Connection,
    start_timestamp: str | None = None,
    end_timestamp: str | None = None,
) -> int:
    """Count all samples matching the optional UTC range."""
    where_clause, parameters = _history_filter(start_timestamp, end_timestamp)
    row = connection.execute(
        f"SELECT COUNT(*) FROM metrics {where_clause}",
        parameters,
    ).fetchone()
    return int(row[0])


def get_metric_history(
    connection: sqlite3.Connection,
    limit: int = 50,
    offset: int = 0,
    start_timestamp: str | None = None,
    end_timestamp: str | None = None,
    sort_order: str = "newest",
) -> tuple[list[dict[str, Any]], int]:
    """Return a filtered page plus the total matching record count."""
    where_clause, parameters = _history_filter(start_timestamp, end_timestamp)
    direction = "ASC" if sort_order == "oldest" else "DESC"
    total = count_metrics(connection, start_timestamp, end_timestamp)
    rows = connection.execute(
        f"""
        SELECT *
        FROM metrics
        {where_clause}
        ORDER BY timestamp_utc {direction}, id {direction}
        LIMIT ? OFFSET ?
        """,
        [*parameters, limit, offset],
    ).fetchall()
    return [_deserialize_metric(row) for row in rows], total


def get_latest_process_snapshots(
    connection: sqlite3.Connection,
) -> dict[str, Any] | None:
    """Return CPU and memory top-five lists associated with the latest metric."""
    metric = connection.execute(
        "SELECT id, timestamp_utc FROM metrics ORDER BY timestamp_utc DESC, id DESC LIMIT 1"
    ).fetchone()
    if metric is None:
        return None

    rows = connection.execute(
        """
        SELECT category, rank, pid, process_name, cpu_percent, memory_percent
        FROM process_snapshots
        WHERE metric_id = ?
        ORDER BY category, rank
        """,
        (metric["id"],),
    ).fetchall()
    result: dict[str, Any] = {
        "metric_id": metric["id"],
        "timestamp_utc": metric["timestamp_utc"],
        "cpu": [],
        "memory": [],
    }
    for row in rows:
        process = dict(row)
        category = process.pop("category")
        result[category].append(process)
    return result
