"""Safe, bounded Windows Event Log polling through the native wevtutil tool."""

from __future__ import annotations

import subprocess
import sys
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


CHANNELS = ("System", "Application")
LEVEL_NAMES = {1: "Critical", 2: "Error", 3: "Warning"}
EVENT_NS = {"e": "http://schemas.microsoft.com/win/2004/08/events/event"}
GENERIC_WARNING_PROVIDER_MARKERS = (
    "kernel",
    "eventlog",
    "diagnostics-performance",
    "driverframeworks",
    "device",
    "application popup",
)


@dataclass(frozen=True)
class ChannelPollResult:
    channel: str
    available: bool
    status: str
    events: list[dict[str, Any]]
    newest_record_id: int


def categorize_event(channel: str, provider: str, event_id: int) -> tuple[str, str]:
    """Return a privacy-safe category and summary from known metadata only."""
    name = provider.casefold()
    if "kernel-power" in name or event_id in {41, 6008}:
        return "power", "Windows reported an unexpected power or shutdown event."
    if "whea" in name:
        return "hardware", "Windows reported a hardware reliability event."
    if any(value in name for value in ("disk", "ntfs", "stor", "volmgr")):
        return "storage", "Windows reported a disk or storage subsystem event."
    if "resource-exhaustion" in name or event_id == 2004:
        return "resource_exhaustion", "Windows reported resource exhaustion."
    if "service control manager" in name and event_id in {
        7000, 7001, 7009, 7011, 7023, 7024, 7031, 7034
    }:
        return "service_failure", "Windows reported a service start or termination failure."
    if (
        "application error" in name
        or "application hang" in name
        or "windows error reporting" in name
        or event_id in {1000, 1001, 1002}
    ):
        return "application_crash", "Windows reported an application crash or hang."
    category = "system_warning" if channel == "System" else "application_warning"
    return category, f"Windows reported a relevant {channel.lower()} event."


def is_relevant_event(event: dict[str, Any]) -> bool:
    """Keep all errors/critical events and only operationally useful warnings."""
    if event["event_level"] in {"Critical", "Error"}:
        return True
    if event["smartops_category"] not in {
        "system_warning",
        "application_warning",
    }:
        return True
    provider = event["provider_name"].casefold()
    return any(marker in provider for marker in GENERIC_WARNING_PROVIDER_MARKERS)


def parse_event_xml(xml_text: str, device_id: str, collected_at: str) -> dict[str, Any]:
    root = ET.fromstring(xml_text)
    system = root.find("e:System", EVENT_NS)
    if system is None:
        raise ValueError("Event XML has no System metadata.")
    provider_node = system.find("e:Provider", EVENT_NS)
    time_node = system.find("e:TimeCreated", EVENT_NS)
    provider = provider_node.get("Name", "Unknown") if provider_node is not None else "Unknown"
    channel = system.findtext("e:Channel", default="Unknown", namespaces=EVENT_NS)
    event_id = int(system.findtext("e:EventID", default="0", namespaces=EVENT_NS))
    record_id = int(system.findtext("e:EventRecordID", default="0", namespaces=EVENT_NS))
    level_number = int(system.findtext("e:Level", default="3", namespaces=EVENT_NS))
    category, summary = categorize_event(channel, provider, event_id)
    timestamp = time_node.get("SystemTime") if time_node is not None else collected_at
    # Normalize Event Log's trailing-Z format to the same timezone-aware ISO
    # representation used by raw metrics. This keeps date filtering predictable.
    try:
        timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        timestamp = timestamp.astimezone(timezone.utc).isoformat()
    except (AttributeError, ValueError):
        timestamp = collected_at
    return {
        "device_id": device_id,
        "event_timestamp_utc": timestamp,
        "channel": channel,
        "provider_name": provider,
        "event_id": event_id,
        "record_id": record_id,
        "event_level": LEVEL_NAMES.get(level_number, "Warning"),
        "smartops_category": category,
        "safe_summary": summary,
        "collected_at_utc": collected_at,
    }


def poll_channel(
    channel: str,
    device_id: str,
    last_record_id: int,
    maximum_events: int = 200,
) -> ChannelPollResult:
    if sys.platform != "win32":
        return ChannelPollResult(channel, False, "unsupported_platform", [], last_record_id)
    collected_at = datetime.now(timezone.utc).isoformat()
    try:
        result = subprocess.run(
            [
                "wevtutil", "qe", channel, "/rd:true", f"/c:{maximum_events}",
                "/f:xml", "/q:*[System[(Level=1 or Level=2 or Level=3)]]",
            ],
            capture_output=True,
            text=True,
            timeout=8,
            check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        fragments = re.sub(r"<\?xml[^>]*\?>", "", result.stdout).strip()
        if not fragments:
            return ChannelPollResult(channel, True, "available", [], last_record_id)
        container = ET.fromstring(f"<SmartOpsEvents>{fragments}</SmartOpsEvents>")
        roots = [
            node
            for node in container.iter()
            if node.tag == "Event" or node.tag.endswith("}Event")
        ]
        parsed = [
            parse_event_xml(
                ET.tostring(node, encoding="unicode"), device_id, collected_at
            )
            for node in roots
        ]
        newest = max((event["record_id"] for event in parsed), default=last_record_id)
        # A cleared/rolled log can restart record IDs. Database uniqueness still
        # prevents duplicates from a normal poll.
        checkpoint = 0 if parsed and newest < last_record_id else last_record_id
        new_events = [
            event
            for event in parsed
            if event["record_id"] > checkpoint and is_relevant_event(event)
        ]
        new_events.sort(key=lambda event: event["record_id"])
        return ChannelPollResult(channel, True, "available", new_events, newest)
    except (OSError, subprocess.SubprocessError, ET.ParseError, ValueError):
        return ChannelPollResult(
            channel,
            False,
            "unavailable_or_permission_restricted",
            [],
            last_record_id,
        )
