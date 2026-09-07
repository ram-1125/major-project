import {
  Activity,
  BatteryCharging,
  Clock3,
  Cpu,
  Database,
  HardDrive,
  MemoryStick,
  Network,
  RefreshCw,
  Thermometer,
  Workflow,
  type LucideIcon,
} from "lucide-react";

import type { LiveState } from "./InteractiveLineChart";

type LiveMetric = {
  timestamp_utc: string;
  cpu_percent: number | null;
  cpu_frequency_mhz: number | null;
  cpu_logical_cores: number | null;
  ram_percent: number | null;
  ram_used_bytes: number | null;
  ram_total_bytes: number | null;
  disk_percent: number | null;
  disk_used_bytes: number | null;
  disk_total_bytes: number | null;
  disk_read_bytes_per_second: number | null;
  disk_write_bytes_per_second: number | null;
  network_download_bytes_per_second: number | null;
  network_upload_bytes_per_second: number | null;
  battery_percent: number | null;
  battery_charging: boolean | null;
  ac_power_connected: boolean | null;
  cpu_temperature_celsius: number | null;
  gpu_temperature_celsius: number | null;
  uptime_seconds: number | null;
};

type Workload = {
  workload_class: string;
  workload_confidence: number | null;
  reason_codes: string[];
} | null;

type ResourceCardProps = {
  icon: LucideIcon;
  label: string;
  value: string;
  detail: string;
  timestamp: string;
  availability?: "available" | "unavailable" | "not-applicable";
};

function words(value: string | null | undefined): string {
  if (!value) return "Unavailable";
  return value.replaceAll("_", " ").replace(/\b\w/g, (character) => character.toUpperCase());
}

function percent(value: number | null): string {
  return value == null ? "Unavailable" : `${value.toFixed(1)}%`;
}

function bytes(value: number | null): string {
  if (value == null) return "Unavailable";
  if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(1)} GB`;
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)} MB`;
  return `${value.toLocaleString()} B`;
}

function rate(value: number | null): string {
  return value == null ? "Unavailable" : `${(value / 1_000_000).toFixed(value >= 10_000_000 ? 1 : 2)} MB/s`;
}

function elapsed(seconds: number | null): string {
  if (seconds == null) return "Unavailable";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`;
}

function ResourceCard({ icon: Icon, label, value, detail, timestamp, availability = "available" }: ResourceCardProps) {
  return (
    <article className={`live-resource-card live-resource-card--${availability}`}>
      <header>
        <span className="live-resource-card__icon" aria-hidden="true"><Icon size={19} /></span>
        <div><span>{label}</span><small>{availability === "available" ? "Current reading" : words(availability)}</small></div>
      </header>
      <strong>{value}</strong>
      <p>{detail}</p>
      <time dateTime={timestamp}>{new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(timestamp))}</time>
    </article>
  );
}

export function LiveMonitoringSummary({
  latest,
  workload,
  totalSamples,
  sampleAgeLabel,
  formattedTimestamp,
  liveState,
  isStale,
  isRefreshing,
  onRefresh,
}: {
  latest: LiveMetric;
  workload: Workload;
  totalSamples: number;
  sampleAgeLabel: string;
  formattedTimestamp: string;
  liveState: LiveState;
  isStale: boolean;
  isRefreshing: boolean;
  onRefresh: () => void;
}) {
  const displayState = isStale ? "Stale" : liveState === "live" ? "Live" : liveState === "paused" ? "Paused" : "Offline";
  const stateClass = displayState.toLowerCase();
  const temperatureAvailable = latest.cpu_temperature_celsius != null
    || latest.gpu_temperature_celsius != null;
  const temperatureDetail = latest.gpu_temperature_celsius != null
    ? `GPU temperature ${latest.gpu_temperature_celsius.toFixed(1)} °C`
    : "GPU Metrics Unavailable";

  return (
    <section className="live-command" aria-label="Current live monitoring state">
      <header className="live-command__status">
        <div>
          <span className={`live-state live-state--${stateClass}`}><i aria-hidden="true" />{displayState}</span>
          <h2>Current operating measurements</h2>
          <p>Local system readings and collection context from the latest stored sample.</p>
        </div>
        <button type="button" onClick={onRefresh} disabled={isRefreshing}>
          <RefreshCw aria-hidden="true" size={16} />{isRefreshing ? "Refreshing…" : "Refresh measurements"}
        </button>
      </header>

      <div className="live-context-strip">
        <div><Clock3 aria-hidden="true" /><span>Last successful update</span><strong>{formattedTimestamp}</strong><small>{sampleAgeLabel} old</small></div>
        <div><Workflow aria-hidden="true" /><span>Current workload</span><strong>{words(workload?.workload_class)}</strong><small>{workload?.workload_confidence == null ? "Confidence unavailable" : `${(workload.workload_confidence * 100).toFixed(0)}% classification confidence`}</small></div>
        <div><Database aria-hidden="true" /><span>Stored locally</span><strong>{totalSamples.toLocaleString()} samples</strong><small>System uptime {elapsed(latest.uptime_seconds)}</small></div>
        <div><Activity aria-hidden="true" /><span>Classification basis</span><strong>{workload?.reason_codes.length ? words(workload.reason_codes[0]) : "Limited context"}</strong><small>Confidence describes classification evidence, not failure probability.</small></div>
      </div>

      <div className="live-resource-grid">
        <ResourceCard icon={Cpu} label="CPU utilisation" value={percent(latest.cpu_percent)} detail={latest.cpu_frequency_mhz == null ? "Clock frequency unavailable" : `${(latest.cpu_frequency_mhz / 1000).toFixed(2)} GHz · ${latest.cpu_logical_cores ?? "?"} logical cores`} timestamp={latest.timestamp_utc} availability={latest.cpu_percent == null ? "unavailable" : "available"} />
        <ResourceCard icon={MemoryStick} label="Memory utilisation" value={percent(latest.ram_percent)} detail={`${bytes(latest.ram_used_bytes)} used of ${bytes(latest.ram_total_bytes)}`} timestamp={latest.timestamp_utc} availability={latest.ram_percent == null ? "unavailable" : "available"} />
        <ResourceCard icon={HardDrive} label="Storage capacity" value={percent(latest.disk_percent)} detail={`${bytes(latest.disk_used_bytes)} used of ${bytes(latest.disk_total_bytes)}`} timestamp={latest.timestamp_utc} availability={latest.disk_percent == null ? "unavailable" : "available"} />
        <ResourceCard icon={Activity} label="Disk throughput" value={`${rate(latest.disk_read_bytes_per_second)} read`} detail={`${rate(latest.disk_write_bytes_per_second)} write`} timestamp={latest.timestamp_utc} availability={latest.disk_read_bytes_per_second == null && latest.disk_write_bytes_per_second == null ? "unavailable" : "available"} />
        <ResourceCard icon={Network} label="Network transfer" value={`${rate(latest.network_download_bytes_per_second)} down`} detail={`${rate(latest.network_upload_bytes_per_second)} up`} timestamp={latest.timestamp_utc} availability={latest.network_download_bytes_per_second == null && latest.network_upload_bytes_per_second == null ? "unavailable" : "available"} />
        <ResourceCard icon={BatteryCharging} label="Battery and power" value={latest.battery_percent == null ? "Not applicable" : percent(latest.battery_percent)} detail={latest.battery_percent == null ? "No battery is exposed by this device" : latest.battery_charging ? "Charging" : latest.ac_power_connected ? "AC power connected" : "Running on battery"} timestamp={latest.timestamp_utc} availability={latest.battery_percent == null ? "not-applicable" : "available"} />
        <ResourceCard icon={Thermometer} label="Temperature" value={latest.cpu_temperature_celsius == null ? "CPU Temperature Unavailable" : `${latest.cpu_temperature_celsius.toFixed(1)} °C`} detail={temperatureDetail} timestamp={latest.timestamp_utc} availability={temperatureAvailable ? "available" : "unavailable"} />
      </div>
      <p className="live-resource-note">A current reading is not automatically classified as healthy. SmartOps applies workload-aware thresholds only in its separate evidence and health evaluations.</p>
    </section>
  );
}
