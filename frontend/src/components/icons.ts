import {
  Activity,
  BadgeCheck,
  BatteryCharging,
  BellRing,
  BrainCircuit,
  CircleCheckBig,
  Cpu,
  Database,
  Expand,
  Filter,
  FlaskConical,
  FileSearch,
  Gauge,
  HardDrive,
  HeartPulse,
  Info,
  LayoutDashboard,
  MemoryStick,
  Network,
  OctagonAlert,
  ScanSearch,
  Search,
  Settings,
  ShieldCheck,
  Thermometer,
  TriangleAlert,
  Workflow,
  type LucideIcon,
} from "lucide-react";

import type { AppRoute } from "../navigation";

export const ROUTE_ICONS: Record<AppRoute, LucideIcon> = {
  overview: LayoutDashboard,
  "live-monitoring": Activity,
  "predictive-alerts": BellRing,
  "root-cause-analysis": ScanSearch,
  "system-health": HeartPulse,
  "pc-quality-check": BadgeCheck,
  "research-validation": FlaskConical,
  "technical-evidence": FileSearch,
  settings: Settings,
};

export const PIPELINE_ICONS: Record<string, LucideIcon> = {
  telemetry_collection: Database,
  feature_window_generation: Workflow,
  active_baseline: BrainCircuit,
  risk_evaluation: ScanSearch,
  health_evaluation: HeartPulse,
  predictive_alert_evaluation: BellRing,
};

export const METRIC_ICONS = {
  cpu: Cpu,
  memory: MemoryStick,
  storage: HardDrive,
  network: Network,
  temperature: Thermometer,
  battery: BatteryCharging,
  workload: Gauge,
} as const;

export const ACTION_ICONS = { search: Search, expand: Expand, filter: Filter } as const;
export const STATUS_ICONS = {
  information: Info,
  success: CircleCheckBig,
  warning: TriangleAlert,
  critical: OctagonAlert,
  privacy: ShieldCheck,
} as const;
