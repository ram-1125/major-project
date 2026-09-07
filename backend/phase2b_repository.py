"""Phase 2B event, workload, and feature-window queries."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from agent.events import ChannelPollResult


def get_event_checkpoint(connection: sqlite3.Connection, channel: str) -> int:
    row = connection.execute(
        "SELECT last_record_id FROM event_channel_checkpoints WHERE channel = ?",
        (channel,),
    ).fetchone()
    return int(row[0]) if row else 0


def store_event_poll(connection: sqlite3.Connection, result: ChannelPollResult) -> int:
    inserted = 0
    updated_at = (
        result.events[-1]["collected_at_utc"]
        if result.events
        else datetime.now(timezone.utc).isoformat()
    )
    with connection:
        for event in result.events:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO windows_events (
                    device_id, event_timestamp_utc, channel, provider_name,
                    event_id, record_id, event_level, smartops_category,
                    safe_summary, collected_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(
                    event[name]
                    for name in (
                        "device_id", "event_timestamp_utc", "channel",
                        "provider_name", "event_id", "record_id", "event_level",
                        "smartops_category", "safe_summary", "collected_at_utc",
                    )
                ),
            )
            inserted += cursor.rowcount
        connection.execute(
            """
            INSERT INTO event_channel_checkpoints (
                channel, last_record_id, available, status, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(channel) DO UPDATE SET
                last_record_id=excluded.last_record_id,
                available=excluded.available,
                status=excluded.status,
                updated_at_utc=excluded.updated_at_utc
            """,
            (
                result.channel,
                result.newest_record_id,
                result.available,
                result.status,
                updated_at,
            ),
        )
    return inserted


def _filters(
    start: str | None,
    end: str | None,
    values: dict[str, str | None],
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    parameters: list[Any] = []
    if start:
        clauses.append("event_timestamp_utc >= ?")
        parameters.append(start)
    if end:
        clauses.append("event_timestamp_utc <= ?")
        parameters.append(end)
    for column, value in values.items():
        if value:
            clauses.append(f"{column} = ?")
            parameters.append(value)
    return ("WHERE " + " AND ".join(clauses) if clauses else ""), parameters


def get_events(
    connection: sqlite3.Connection,
    limit: int,
    offset: int,
    start: str | None = None,
    end: str | None = None,
    sort: str = "newest",
    channel: str | None = None,
    level: str | None = None,
    category: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    where, parameters = _filters(
        start, end,
        {"channel": channel, "event_level": level, "smartops_category": category},
    )
    total = int(connection.execute(
        f"SELECT COUNT(*) FROM windows_events {where}", parameters
    ).fetchone()[0])
    direction = "ASC" if sort == "oldest" else "DESC"
    rows = connection.execute(
        f"""SELECT * FROM windows_events {where}
        ORDER BY event_timestamp_utc {direction}, id {direction}
        LIMIT ? OFFSET ?""",
        [*parameters, limit, offset],
    ).fetchall()
    return [dict(row) for row in rows], total


def get_event_summary(connection: sqlite3.Connection) -> dict[str, Any]:
    severity = {
        row["event_level"]: row["count"]
        for row in connection.execute(
            "SELECT event_level, COUNT(*) count FROM windows_events GROUP BY event_level"
        )
    }
    categories = {
        row["smartops_category"]: row["count"]
        for row in connection.execute(
            "SELECT smartops_category, COUNT(*) count FROM windows_events GROUP BY smartops_category"
        )
    }
    channels = [
        {
            **dict(row),
            "available": bool(row["available"]),
        }
        for row in connection.execute(
            "SELECT channel, available, status, last_record_id, updated_at_utc FROM event_channel_checkpoints ORDER BY channel"
        )
    ]
    return {"severity": severity, "categories": categories, "channels": channels}


def get_workload_history(
    connection: sqlite3.Connection,
    limit: int,
    offset: int,
    start: str | None = None,
    end: str | None = None,
    sort: str = "newest",
) -> tuple[list[dict[str, Any]], int]:
    clauses = ["workload_class IS NOT NULL"]
    params: list[Any] = []
    if start:
        clauses.append("timestamp_utc >= ?")
        params.append(start)
    if end:
        clauses.append("timestamp_utc <= ?")
        params.append(end)
    where = "WHERE " + " AND ".join(clauses)
    total = int(connection.execute(
        f"SELECT COUNT(*) FROM metrics {where}", params
    ).fetchone()[0])
    direction = "ASC" if sort == "oldest" else "DESC"
    rows = connection.execute(
        f"""SELECT id, timestamp_utc, workload_class, workload_confidence,
        workload_reasons_json, user_activity_state, system_activity_state,
        workload_rule_version, workload_provenance_json FROM metrics {where}
        ORDER BY timestamp_utc {direction}, id {direction} LIMIT ? OFFSET ?""",
        [*params, limit, offset],
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        raw = item.pop("workload_reasons_json")
        item["reason_codes"] = json.loads(raw) if raw else []
        provenance = item.pop("workload_provenance_json", None)
        item["provenance"] = json.loads(provenance) if provenance else None
        items.append(item)
    return items, total


def get_feature_history(
    connection: sqlite3.Connection,
    limit: int,
    offset: int,
    start: str | None = None,
    end: str | None = None,
    sort: str = "newest",
) -> tuple[list[dict[str, Any]], int]:
    clauses: list[str] = []
    params: list[Any] = []
    if start:
        clauses.append("window_start_utc >= ?")
        params.append(start)
    if end:
        clauses.append("window_end_utc <= ?")
        params.append(end)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    total = int(connection.execute(
        f"SELECT COUNT(*) FROM feature_windows {where}", params
    ).fetchone()[0])
    direction = "ASC" if sort == "oldest" else "DESC"
    rows = connection.execute(
        f"""SELECT * FROM feature_windows {where}
        ORDER BY window_start_utc {direction}, id {direction} LIMIT ? OFFSET ?""",
        [*params, limit, offset],
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["is_complete"] = bool(item["is_complete"])
        item["missing_indicators"] = json.loads(item.pop("missing_indicators_json"))
        distribution = item.pop("workload_distribution_json", None)
        item["workload_distribution"] = (
            json.loads(distribution) if distribution else None
        )
        composition = item.pop("workload_composition_json", None)
        item["workload_composition"] = (
            json.loads(composition) if composition else None
        )
        secondary_reasons = item.pop(
            "secondary_workload_reason_codes_json", None
        )
        item["secondary_workload_reason_codes"] = (
            json.loads(secondary_reasons) if secondary_reasons else []
        )
        items.append(item)
    return items, total
