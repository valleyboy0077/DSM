export interface Server {
  id: number;
  name: string;
  ipmi_ip: string;
  ipmi_user: string;
  drac_version: string | null; // legacy/internal DSM control profile
  controller_profile?: string | null; // preferred alias for the stored profile
  controller_label?: string | null; // user-facing hardware/controller label
  controller_source?: string | null;
  model: string | null;
  serial: string | null;
  status: string;
  last_seen: string | null;
  added_at: string;
}

export interface SensorReading {
  id: number;
  server_id: number;
  sensor_type: string;
  sensor_label: string;
  value: number;
  timestamp: string | null;
}

export interface FanConfig {
  id: number;
  server_id: number;
  mode: string;
  cpu_temp_min: number;
  cpu_temp_max: number;
  disk_temp_min: number;
  disk_temp_max: number;
  manual_speed: number;
  polling_seconds: number;
  auto_control: boolean;
  current_target_percent: number | null;
  updated_at: string | null;
}

export interface FanControlResult {
  status: string;
  fan_percent: number;
  cpu_temp: number;
  disk_temp: number | null;
  ambient_temp: number;
  action: string;
  reason: string;
}

export interface FanTelemetryItem {
  name: string;
  member_id: string;
  rpm: number;
  percent: number | null;
  percent_source: 'pwm' | 'controller_percentage' | 'rpm_estimate' | 'unavailable';
  health: string;
  source: string;
}

export interface FanTelemetryResponse {
  server_id: number;
  server_name: string;
  collected_at: string;
  source: string;
  fans: FanTelemetryItem[];
}

export interface SensorSummary {
  server_id: number;
  server_name: string;
  status: string;
  last_seen: string | null;
  sensors: Array<{
    label: string;
    type: string;
    value: number;
    timestamp: string | null;
  }>;
}

/** Where the dashboard obtained the currently displayed telemetry. */
export type TelemetrySource = 'memory' | 'database' | 'unavailable';

/**
 * Freshness metadata that travels with every dashboard telemetry block.
 * `captured_at` is null only when no successful telemetry is available.
 */
export interface TelemetryFreshness {
  source: TelemetrySource;
  captured_at: string | null;
  age_seconds: number | null;
  stale: boolean;
  last_error: string | null;
  last_attempt_at: string | null;
}

/** A single server's cache-first dashboard telemetry payload. */
export interface DashboardServerSnapshot {
  server_id: number;
  server_name: string;
  status: string;
  revision: number;
  cycle_id: number;
  freshness: TelemetryFreshness;
  readings: SensorSummary['sensors'];
  fans: FanTelemetryItem[];
}

/** Lifecycle metadata for the poll cycle that produced dashboard telemetry. */
export interface PollCycleStatus {
  cycle_id: number;
  status: 'idle' | 'running' | 'completed' | 'failed';
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  next_poll_at: string | null;
}

/** Complete cache-first payload used to hydrate the dashboard. */
export interface DashboardSnapshot {
  servers: DashboardServerSnapshot[];
  poll_cycle: PollCycleStatus;
}

export interface User {
  id: number;
  username: string;
  email: string | null;
  is_active: boolean;
  is_superuser: boolean;
  roles: string[];
  theme: string;
  created_at: string | null;
}

export interface ServerGroup {
  id: number;
  name: string;
  description: string | null;
  color: string;
  server_count: number;
  server_ids: number[];
}

export interface TempRange {
  id: number;
  component_type: string;
  component_label: string | null;
  temp_min: number | null;
  temp_max: number | null;
  warning_min: number | null;
  warning_max: number | null;
  critical_min: number | null;
  critical_max: number | null;
}

export interface TempProfile {
  id: number;
  server_id: number;
  server_name: string | null;
  is_default: boolean;
  name: string;
  ranges: TempRange[];
  created_at: string | null;
  updated_at: string | null;
}

export const THEMES = [
  { id: 'dark', label: 'Dark', color: '#0d1117' },
  { id: 'light', label: 'Light', color: '#f6f8fa' },
  { id: 'blue', label: 'Blue', color: '#0a1929' },
  { id: 'green', label: 'Green', color: '#0a1a0f' },
  { id: 'high-contrast', label: 'Hi-Contrast', color: '#000000' },
  { id: 'sepia', label: 'Sepia', color: '#f4ecd8' },
];
