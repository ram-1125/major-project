import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { LiveMonitoringSummary } from "./LiveMonitoringSummary";

const metric = {
  timestamp_utc: "2026-09-03T12:00:00Z",
  cpu_percent: 42.5, cpu_frequency_mhz: 3200, cpu_logical_cores: 8,
  ram_percent: 61.2, ram_used_bytes: 8_000_000_000, ram_total_bytes: 16_000_000_000,
  disk_percent: 55, disk_used_bytes: 500_000_000_000, disk_total_bytes: 1_000_000_000_000,
  disk_read_bytes_per_second: 2_000_000, disk_write_bytes_per_second: 1_000_000,
  network_download_bytes_per_second: 5_000_000, network_upload_bytes_per_second: 500_000,
  battery_percent: null, battery_charging: null, ac_power_connected: null,
  cpu_temperature_celsius: null, gpu_temperature_celsius: null, uptime_seconds: 3600,
};

const common = {
  latest: metric,
  workload: { workload_class: "development", workload_confidence: .9, reason_codes: ["recognised_development_foreground"] },
  totalSamples: 1200,
  sampleAgeLabel: "12 seconds",
  formattedTimestamp: "03 Sep 2026, 5:30 PM",
  isRefreshing: false,
  onRefresh: () => undefined,
};

afterEach(cleanup);

describe("LiveMonitoringSummary", () => {
  it.each([
    ["live", false, "Live"],
    ["live", true, "Stale"],
    ["offline", false, "Offline"],
  ] as const)("shows truthful %s collection status", (liveState, isStale, label) => {
    render(<LiveMonitoringSummary {...common} liveState={liveState} isStale={isStale} />);
    expect(screen.getByText(label)).toBeVisible();
  });

  it("preserves unavailable and non-battery states without fake zeroes", () => {
    render(<LiveMonitoringSummary {...common} liveState="live" isStale={false} />);
    expect(screen.getByText("CPU Temperature Unavailable")).toBeVisible();
    expect(screen.getByText("GPU Metrics Unavailable")).toBeVisible();
    expect(screen.getByText("Not applicable")).toBeVisible();
    expect(screen.queryByText("0 °C")).not.toBeInTheDocument();
    expect(screen.getByText(/not failure probability/i)).toBeVisible();
  });
});
