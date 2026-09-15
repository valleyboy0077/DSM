/**
 * Compile-time contract fixtures for the cache-first dashboard telemetry API.
 * This file is included by tsconfig and is checked with `tsc --noEmit`.
 */
import type {
  DashboardSnapshot,
  DashboardServerSnapshot,
  PollCycleStatus,
  TelemetryFreshness,
} from './types';

type Equal<Actual, Expected> =
  (<Type>() => Type extends Actual ? 1 : 2) extends
  (<Type>() => Type extends Expected ? 1 : 2)
    ? true
    : false;
type Assert<Condition extends true> = Condition;

type _TelemetrySourcesAreHonest = Assert<
  Equal<TelemetryFreshness['source'], 'memory' | 'database' | 'unavailable'>
>;
type _ServerRevisionsAreNumeric = Assert<Equal<DashboardServerSnapshot['revision'], number>>;
type _ServerCycleIdsAreNumeric = Assert<Equal<DashboardServerSnapshot['cycle_id'], number>>;
type _PollLifecycleIsExplicit = Assert<
  Equal<PollCycleStatus['status'], 'idle' | 'running' | 'completed' | 'failed'>
>;

const memorySnapshot = {
  servers: [{
    server_id: 1,
    server_name: 'server-1',
    status: 'online',
    revision: 7,
    cycle_id: 12,
    freshness: {
      source: 'memory',
      captured_at: '2026-09-15T23:25:41Z',
      age_seconds: 8,
      stale: false,
      last_error: null,
      last_attempt_at: '2026-09-15T23:25:41Z',
    },
    readings: [{
      label: 'CPU 1 Temp',
      type: 'cpu',
      value: 42,
      timestamp: '2026-09-15T23:25:41Z',
    }],
    fans: [{
      name: 'Fan 1',
      member_id: 'Fan.1',
      rpm: 5400,
      percent: 35,
      percent_source: 'pwm',
      health: 'OK',
      source: 'redfish',
    }],
  }],
  poll_cycle: {
    cycle_id: 12,
    status: 'completed',
    started_at: '2026-09-15T23:25:33Z',
    completed_at: '2026-09-15T23:25:41Z',
    duration_ms: 8000,
    next_poll_at: '2026-09-15T23:25:49Z',
  },
} satisfies DashboardSnapshot;

const unavailableServer = {
  server_id: 2,
  server_name: 'server-2',
  status: 'unreachable',
  revision: 8,
  cycle_id: 12,
  freshness: {
    source: 'unavailable',
    captured_at: null,
    age_seconds: null,
    stale: true,
    last_error: 'poll timed out',
    last_attempt_at: '2026-09-15T23:25:41Z',
  },
  readings: [],
  fans: [],
} satisfies DashboardServerSnapshot;

void memorySnapshot;
void unavailableServer;
