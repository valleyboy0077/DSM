import type { CSSProperties } from 'react';
import { useState, useEffect, useCallback, useRef } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import type { DashboardServerSnapshot, FanTelemetryResponse, SensorSummary, Server } from '../types';
import { describeController, describeControllerSource } from '../serverMetadata';

const statusClass = (status: string) => status === 'online' ? 'badge-green' : status === 'degraded' ? 'badge-yellow' : 'badge-red';

type DashboardTelemetry = DashboardServerSnapshot;
type StreamState = 'connecting' | 'live' | 'reconnecting' | 'offline';

type HardwareInventory = {
  model?: string | null;
  service_tag?: string | null;
  memory_total_mb?: string | number | null;
  memory_total_gib?: number | null;
  cpu_count?: number | null;
  power_supply_count?: number | null;
  system?: Record<string, unknown> | null;
  processors?: Array<Record<string, unknown>>;
  power_supplies?: Array<Record<string, unknown>>;
};

type GaugeBand = {
  from: number;
  to: number;
  color: string;
};

type GaugeValue = {
  value: number | null;
  label: string;
  unit: string;
  placeholder?: string;
};

const FAN_BANDS: GaugeBand[] = [
  { from: 0, to: 15, color: 'var(--accent)' },
  { from: 15, to: 30, color: 'var(--green)' },
  { from: 30, to: 55, color: 'var(--yellow)' },
  { from: 55, to: 75, color: 'var(--orange)' },
  { from: 75, to: 100, color: 'var(--red)' },
];

const CPU_TEMP_BANDS: GaugeBand[] = [
  { from: 0, to: 20, color: 'var(--accent)' },
  { from: 21, to: 50, color: 'var(--green)' },
  { from: 51, to: 65, color: 'var(--yellow)' },
  { from: 66, to: 78, color: 'var(--orange)' },
  { from: 79, to: 100, color: 'var(--red)' },
];

const HDD_TEMP_BANDS: GaugeBand[] = [
  { from: 0, to: 24, color: 'var(--accent)' },
  { from: 25, to: 39, color: 'var(--green)' },
  { from: 40, to: 55, color: 'var(--yellow)' },
  { from: 56, to: 67, color: 'var(--orange)' },
  { from: 68, to: 100, color: 'var(--red)' },
];

function lastSeen(value: string | null) {
  if (!value) return 'Never seen';
  return new Date(value).toLocaleString([], { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function telemetryStatus(telemetry: DashboardTelemetry | undefined, fallback: string) {
  const status = telemetry?.status;
  return status === 'error' || status === 'unreachable' ? 'offline' : status || fallback;
}

function telemetryLabel(telemetry: DashboardTelemetry | undefined, streamState: StreamState) {
  if (!telemetry) return 'Telemetry unavailable';
  const freshness = telemetry.freshness;
  const source = freshness.source === 'memory' ? 'Live cache' : freshness.source === 'database' ? 'Saved cache' : 'Unavailable';
  const age = freshness.age_seconds === null ? null : `${Math.round(freshness.age_seconds)}s old`;
  const state = freshness.stale ? 'stale' : streamState === 'live' ? 'streaming' : streamState;
  return [source, age, state].filter(Boolean).join(' · ');
}

function average(values: number[]) {
  if (!values.length) return null;
  return Math.round((values.reduce((sum, value) => sum + value, 0) / values.length) * 10) / 10;
}

function polarToCartesian(cx: number, cy: number, radius: number, angleDeg: number) {
  const angleRad = (angleDeg * Math.PI) / 180;
  return {
    x: cx + radius * Math.cos(angleRad),
    y: cy + radius * Math.sin(angleRad),
  };
}

function describeArc(cx: number, cy: number, radius: number, startAngle: number, endAngle: number) {
  const start = polarToCartesian(cx, cy, radius, startAngle);
  const end = polarToCartesian(cx, cy, radius, endAngle);
  const largeArcFlag = endAngle - startAngle <= 180 ? 0 : 1;
  return `M ${start.x} ${start.y} A ${radius} ${radius} 0 ${largeArcFlag} 1 ${end.x} ${end.y}`;
}

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

function getBandForValue(bands: GaugeBand[], value: number) {
  let fallback = bands[0] ?? null;

  for (const band of bands) {
    if (value >= band.from) fallback = band;
    if (value >= band.from && value < band.to) return band;
  }

  return fallback;
}

function getBandColor(bands: GaugeBand[], value: number) {
  return getBandForValue(bands, value)?.color ?? 'var(--accent)';
}

function asNumber(value: unknown) {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string') {
    const parsed = Number(value.trim());
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function cleanProcessorModel(value: unknown) {
  if (typeof value !== 'string' || !value.trim()) return 'CPU inventory pending';
  return value
    .replace(/Intel\(R\)\s*/gi, '')
    .replace(/Xeon\(R\)\s*CPU\s*/gi, 'Xeon ')
    .replace(/CPU\s*/gi, '')
    .replace(/@.*$/i, '')
    .replace(/\s+/g, ' ')
    .trim();
}

function formatProcessorSummary(hardware?: HardwareInventory | null) {
  const processors = hardware?.processors ?? [];
  const system = hardware?.system ?? {};
  const installedCount = asNumber(system.PopulatedCPUSockets) ?? (processors.length > 0 ? processors.length : null) ?? asNumber(hardware?.cpu_count) ?? 0;
  const cpuModel = cleanProcessorModel(processors[0]?.Model);
  const cores = asNumber(processors[0]?.NumberOfProcessorCores) ?? asNumber(processors[0]?.NumberOfEnabledCores);

  if (!installedCount) return 'CPU inventory pending';
  return `${installedCount} x ${cpuModel}${cores ? ` (${cores}-core)` : ''}`;
}

function getInstalledCpuCount(hardware?: HardwareInventory | null) {
  const processors = hardware?.processors ?? [];
  const system = hardware?.system ?? {};
  return asNumber(system.PopulatedCPUSockets) ?? (processors.length > 0 ? processors.length : null) ?? asNumber(hardware?.cpu_count) ?? 0;
}

function formatMemorySummary(hardware?: HardwareInventory | null) {
  const totalGiB = asNumber(hardware?.memory_total_gib);
  if (totalGiB) return `${Math.round(totalGiB)} GB RAM`;

  const totalMb = asNumber(hardware?.memory_total_mb);
  if (totalMb) {
    const totalGb = totalMb / 1024;
    return `${Number.isInteger(totalGb) ? totalGb : Math.round(totalGb * 10) / 10} GB RAM`;
  }

  return 'RAM inventory pending';
}

function isActivePsu(psu: Record<string, unknown>) {
  const inputVoltage = asNumber(psu.InputVoltage);
  const primaryStatus = String(psu.PrimaryStatus ?? '');
  const detailedState = String(psu.DetailedState ?? '').toLowerCase();
  return Boolean(
    (inputVoltage !== null && inputVoltage > 0) ||
    primaryStatus === '1' ||
    detailedState.includes('presence detected') ||
    detailedState.includes('online')
  );
}

function formatPsuSummary(hardware?: HardwareInventory | null) {
  const powerSupplies = hardware?.power_supplies ?? [];
  const installedCount = powerSupplies.length || asNumber(hardware?.power_supply_count) || 0;
  if (!installedCount) return 'PSU inventory pending';

  const activePowerSupplies = powerSupplies.filter(isActivePsu);
  const activeCount = activePowerSupplies.length || installedCount;
  const wattages = (activePowerSupplies.length ? activePowerSupplies : powerSupplies)
    .map(psu => asNumber(psu.TotalOutputPower) ?? asNumber(psu.Range1MaxInputPower))
    .filter((value): value is number => value !== null && value > 0);
  const wattage = wattages.length ? Math.max(...wattages) : null;

  if (wattage && activeCount === installedCount) return `${activeCount} x ${wattage}W PSU`;
  if (wattage) return `${activeCount} of ${installedCount} active • ${wattage}W PSU`;
  if (activeCount === installedCount) return `${activeCount} PSU installed`;
  return `${activeCount} of ${installedCount} PSU active`;
}

function GaugeCard({ title, bands, min, max, gauge }: { title: string; bands: GaugeBand[]; min: number; max: number; gauge: GaugeValue }) {
  const startAngle = 135;
  const sweep = 270;
  const cx = 74;
  const cy = 80;
  const radius = 46;
  const segmentValueSize = 3;
  const segmentGapAngle = 1.9;

  const normalizedValue = gauge.value === null ? null : clamp(gauge.value, min, max);
  const gaugeReadingColor = normalizedValue === null
    ? 'var(--text-primary)'
    : getBandColor(bands, normalizedValue >= max ? max - 0.001 : normalizedValue);

  let badgeClass = 'badge-blue';
  if (normalizedValue !== null) {
    const activeBand = getBandForValue(bands, normalizedValue >= max ? max - 0.001 : normalizedValue);
    if (activeBand?.color.includes('accent')) badgeClass = 'badge-blue';
    else if (activeBand?.color.includes('green')) badgeClass = 'badge-green';
    else if (activeBand?.color.includes('yellow')) badgeClass = 'badge-yellow';
    else if (activeBand?.color.includes('orange')) badgeClass = 'badge-yellow';
    else if (activeBand?.color.includes('red')) badgeClass = 'badge-red';
  }

  const totalRange = max - min;
  const segmentCount = Math.ceil(totalRange / segmentValueSize);
  const segments = Array.from({ length: segmentCount }, (_, index) => {
    const rawSegmentStart = min + index * segmentValueSize;
    const rawSegmentEnd = Math.min(rawSegmentStart + segmentValueSize, max);
    const segmentStartRatio = (rawSegmentStart - min) / totalRange;
    const segmentEndRatio = (rawSegmentEnd - min) / totalRange;
    const rawStartAngle = startAngle + segmentStartRatio * sweep;
    const rawEndAngle = startAngle + segmentEndRatio * sweep;
    const segmentStartAngle = rawStartAngle + segmentGapAngle / 2;
    const segmentEndAngle = rawEndAngle - segmentGapAngle / 2;
    const isFilled = normalizedValue !== null && normalizedValue >= rawSegmentEnd;
    const segmentMidpoint = rawSegmentStart + (rawSegmentEnd - rawSegmentStart) / 2;

    return {
      key: `${title}-segment-${index}`,
      d: describeArc(cx, cy, radius, segmentStartAngle, segmentEndAngle),
      stroke: isFilled ? getBandColor(bands, segmentMidpoint) : undefined,
      filled: isFilled,
    };
  });

  return (
    <div className="telemetry-gauge-card">
      <div className="telemetry-gauge-header">
        <div className="meta-label">{title}</div>
        <div className={`badge ${badgeClass}`}>{gauge.label}</div>
      </div>

      <div
        className="telemetry-gauge-shell"
        style={{ '--gauge-reading-color': gaugeReadingColor } as CSSProperties}
      >
        <svg viewBox="0 0 148 108" className="telemetry-gauge-svg" role="img" aria-label={`${title} gauge`}>
          {segments.map((segment) => (
            <path key={`${segment.key}-track`} d={segment.d} className="telemetry-gauge-track" />
          ))}

          {segments.map((segment) =>
            segment.filled ? (
              <path
                key={segment.key}
                d={segment.d}
                className="telemetry-gauge-band-filled"
                stroke={segment.stroke}
                style={{ '--gauge-glow': segment.stroke } as CSSProperties}
              />
            ) : null
          )}
        </svg>

        <div className="telemetry-gauge-reading" aria-hidden="true">
          {normalizedValue === null ? (
            '—'
          ) : (
            <>
              {Math.round(normalizedValue * 10) / 10}
              <span>{gauge.unit}</span>
            </>
          )}
        </div>

        <div className="telemetry-gauge-range mono">
          <span>{min}{gauge.unit}</span>
          <span>{max}{gauge.unit}</span>
        </div>
        {normalizedValue === null && <div className="telemetry-gauge-placeholder">{gauge.placeholder || 'Data source pending'}</div>}
      </div>
    </div>
  );
}

function getCpuTemps(summary: SensorSummary | null) {
  if (!summary) return { cpu1: null, cpu2: null };

  const cpuSensors = summary.sensors.filter(sensor => sensor.type === 'cpu');
  const cpu1 = cpuSensors.find(sensor => /cpu\s*1|cpu1/i.test(sensor.label))?.value ?? cpuSensors[0]?.value ?? null;
  const cpu2 = cpuSensors.find(sensor => /cpu\s*2|cpu2/i.test(sensor.label))?.value ?? cpuSensors[1]?.value ?? null;

  return { cpu1, cpu2 };
}

function getAverageFanPercent(fanTelemetry: FanTelemetryResponse | null) {
  if (!fanTelemetry) return null;
  return average(fanTelemetry.fans.map(fan => fan.percent).filter((value): value is number => value !== null));
}

function getAverageSsdTemp(summary: SensorSummary | null) {
  if (!summary) return null;
  const ssdSensors = summary.sensors.filter(sensor => /ssd|nvme/i.test(sensor.label));
  return average(ssdSensors.map(sensor => sensor.value).filter((v): v is number => v !== null));
}

function getAverageHddTemp(summary: SensorSummary | null) {
  if (!summary) return null;
  const hddSensors = summary.sensors.filter(sensor => /hdd|hard\s*drive|disk/i.test(sensor.label) && !/ssd|nvme/i.test(sensor.label));
  return average(hddSensors.map(sensor => sensor.value).filter((v): v is number => v !== null));
}

function getSensorTempByLabel(summary: SensorSummary | null, pattern: RegExp) {
  if (!summary) return null;
  return summary.sensors.find(sensor => pattern.test(sensor.label))?.value ?? null;
}

function ServerTelemetry({ server, telemetry, hardware, streamState }: { server: Server; telemetry: DashboardTelemetry | undefined; hardware?: HardwareInventory | null; streamState: StreamState }) {
  const summary = telemetry ? { sensors: telemetry.readings } as SensorSummary : null;
  const fanTelemetry = telemetry ? { fans: telemetry.fans } as FanTelemetryResponse : null;
  const fanPercent = getAverageFanPercent(fanTelemetry);
  const { cpu1, cpu2 } = getCpuTemps(summary);
  const inletTemp = getSensorTempByLabel(summary, /inlet|ambient/i);
  const outletTemp = getSensorTempByLabel(summary, /outlet|exhaust/i);
  const ssdTemp = getAverageSsdTemp(summary);
  const hddTemp = getAverageHddTemp(summary);
  const cpuCount = getInstalledCpuCount(hardware);
  const showCpu2 = cpuCount > 1 || cpu2 !== null;

  return (
    <div className="telemetry-layout">
      <div className="telemetry-gauge-grid">
        <GaugeCard
          title="CPU1 temp"
          min={0}
          max={100}
          bands={CPU_TEMP_BANDS}
          gauge={{
            value: cpu1,
            label: cpu1 !== null ? 'CPU1' : 'Awaiting',
            unit: '°C',
            placeholder: 'CPU1 data pending',
          }}
        />
        {showCpu2 && (
          <GaugeCard
            title="CPU2 temp"
            min={0}
            max={100}
            bands={CPU_TEMP_BANDS}
            gauge={{
              value: cpu2,
              label: cpu2 !== null ? 'CPU2' : 'Awaiting',
              unit: '°C',
              placeholder: 'CPU2 data pending',
            }}
          />
        )}
        <GaugeCard
          title="Inlet temp"
          min={0}
          max={100}
          bands={CPU_TEMP_BANDS}
          gauge={{
            value: inletTemp,
            label: inletTemp !== null ? 'Inlet' : 'Awaiting',
            unit: '°C',
            placeholder: 'Inlet data pending',
          }}
        />
        <GaugeCard
          title="Outlet temp"
          min={0}
          max={100}
          bands={CPU_TEMP_BANDS}
          gauge={{
            value: outletTemp,
            label: outletTemp !== null ? 'Outlet' : 'Awaiting',
            unit: '°C',
            placeholder: 'Outlet data pending',
          }}
        />
        <GaugeCard
          title="Avg SSD temp"
          min={0}
          max={100}
          bands={CPU_TEMP_BANDS}
          gauge={{
            value: ssdTemp,
            label: ssdTemp !== null ? 'SSD' : 'Placeholder',
            unit: '°C',
            placeholder: 'SSD source pending',
          }}
        />
        <GaugeCard
          title="Avg HDD temp"
          min={0}
          max={100}
          bands={HDD_TEMP_BANDS}
          gauge={{
            value: hddTemp,
            label: hddTemp !== null ? 'HDD' : 'Placeholder',
            unit: '°C',
            placeholder: 'HDD source pending',
          }}
        />
        <GaugeCard
          title="Avg fan speed"
          min={0}
          max={100}
          bands={FAN_BANDS}
          gauge={{
            value: fanPercent,
            label: fanPercent !== null ? 'Cooling' : 'Awaiting',
            unit: '%',
            placeholder: 'Fan telemetry pending',
          }}
        />
      </div>

      <div className="telemetry-footnote">
        <span>Server profile: {server.model || 'PowerEdge platform'}</span>
        <span title={telemetry?.freshness.last_error || undefined}>{telemetryLabel(telemetry, streamState)}</span>
        {telemetry?.freshness.last_error && <span className="telemetry-error">Last poll error: {telemetry.freshness.last_error}</span>}
      </div>
    </div>
  );
}

export default function Dashboard() {
  const [servers, setServers] = useState<Server[]>([]);
  const [telemetryByServer, setTelemetryByServer] = useState<Record<number, DashboardTelemetry>>({});
  const [hardwareByServer, setHardwareByServer] = useState<Record<number, HardwareInventory | null>>({});
  const [loading, setLoading] = useState(true);
  const [snapshotError, setSnapshotError] = useState<string | null>(null);
  const [streamState, setStreamState] = useState<StreamState>('connecting');
  const snapshotRefreshInFlight = useRef(false);

  const hydrate = useCallback(async () => {
    setLoading(true);
    try {
      const [nextServers, snapshot] = await Promise.all([api.listServers(), api.getDashboardSnapshot()]);
      setServers(nextServers);
      setTelemetryByServer(previous => {
        const next = { ...previous };
        for (const incoming of snapshot.servers) {
          if ((next[incoming.server_id]?.revision ?? -1) <= incoming.revision) next[incoming.server_id] = incoming;
        }
        return next;
      });
      setSnapshotError(null);
      setHardwareByServer(previous => Object.fromEntries(nextServers.map(server => [server.id, previous[server.id] ?? null])));

      void (async () => {
        const hardwareEntries = await Promise.all(
          nextServers.map(async (server) => {
            try {
              const hardware = await api.getHardware(server.id);
              return [server.id, hardware as HardwareInventory] as const;
            } catch {
              return [server.id, null] as const;
            }
          })
        );

        setHardwareByServer(previous => ({ ...previous, ...Object.fromEntries(hardwareEntries) }));
      })();
    } catch (error) {
      // A failed refresh must not erase the last known-good fleet state.
      setSnapshotError(error instanceof Error ? error.message : 'Unable to refresh dashboard telemetry');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void hydrate(); }, [hydrate]);

  useEffect(() => {
    let disposed = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: number | null = null;
    let attempt = 0;
    let openedOnce = false;

    const refreshSnapshot = async () => {
      if (snapshotRefreshInFlight.current) return;
      snapshotRefreshInFlight.current = true;
      try {
        const snapshot = await api.getDashboardSnapshot();
        if (!disposed) {
          setTelemetryByServer(previous => {
            const next = { ...previous };
            for (const incoming of snapshot.servers) {
              if ((next[incoming.server_id]?.revision ?? -1) <= incoming.revision) next[incoming.server_id] = incoming;
            }
            return next;
          });
          setSnapshotError(null);
        }
      } catch (error) {
        if (!disposed) setSnapshotError(error instanceof Error ? error.message : 'Unable to refresh dashboard telemetry');
      } finally {
        snapshotRefreshInFlight.current = false;
      }
    };

    const connect = () => {
      const url = api.dashboardWebSocketUrl();
      if (!url) {
        setStreamState('offline');
        return;
      }
      setStreamState(attempt ? 'reconnecting' : 'connecting');
      socket = new WebSocket(url);
      socket.onopen = () => {
        if (disposed) return;
        setStreamState('live');
        attempt = 0;
        if (openedOnce) void refreshSnapshot();
        openedOnce = true;
      };
      socket.onmessage = event => {
        try {
          const message = JSON.parse(event.data) as { event?: string; server_id?: number; revision?: number; data?: DashboardTelemetry };
          if (message.event !== 'server.telemetry.updated' || !message.data || typeof message.server_id !== 'number' || typeof message.revision !== 'number') return;
          setTelemetryByServer(previous => {
            const current = previous[message.server_id!];
            return current && message.revision! <= current.revision ? previous : { ...previous, [message.server_id!]: message.data! };
          });
        } catch {
          // Ignore malformed stream frames and preserve the current telemetry.
        }
      };
      socket.onclose = () => {
        if (disposed) return;
        const delay = Math.min(30_000, 1_000 * 2 ** attempt);
        attempt += 1;
        setStreamState('reconnecting');
        reconnectTimer = window.setTimeout(connect, delay);
      };
      socket.onerror = () => socket?.close();
    };

    connect();
    return () => {
      disposed = true;
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, []);

  const online = servers.filter(s => telemetryStatus(telemetryByServer[s.id], s.status) === 'online').length;
  const degraded = servers.filter(s => telemetryStatus(telemetryByServer[s.id], s.status) === 'degraded').length;
  const offline = servers.filter(s => telemetryStatus(telemetryByServer[s.id], s.status) === 'offline').length;
  const health = servers.length ? Math.round((online / servers.length) * 100) : 0;

  return (
    <div>
      <div className="grid grid-4">
        <div className="card metric-card">
          <div className="metric-row">
            <div>
              <div className="card-title">Fleet health</div>
              <div className="card-value">{health}%</div>
              <div className="card-subtitle">{online} of {servers.length} online</div>
            </div>
            <div className="metric-icon">HLTH</div>
          </div>
        </div>
        <div className="card metric-card">
          <div className="metric-row">
            <div>
              <div className="card-title">Total servers</div>
              <div className="card-value">{servers.length}</div>
              <div className="card-subtitle">iDRAC endpoints in DSM</div>
            </div>
            <div className="metric-icon">SRV</div>
          </div>
        </div>
        <div className="card metric-card">
          <div className="metric-row">
            <div>
              <div className="card-title">Degraded</div>
              <div className="card-value" style={{ color: 'var(--yellow)' }}>{degraded}</div>
              <div className="card-subtitle">Needs attention</div>
            </div>
            <div className="metric-icon">WARN</div>
          </div>
        </div>
        <div className="card metric-card">
          <div className="metric-row">
            <div>
              <div className="card-title">Offline</div>
              <div className="card-value" style={{ color: 'var(--red)' }}>{offline}</div>
              <div className="card-subtitle">No recent telemetry</div>
            </div>
            <div className="metric-icon">DOWN</div>
          </div>
        </div>
      </div>

      <div className="section-title">Managed servers</div>
      {snapshotError && <div className="inline-alert danger">Telemetry refresh failed: {snapshotError}. Showing last known telemetry.</div>}
      {loading ? (
        <div className="grid grid-3"><div className="skeleton" /><div className="skeleton" /><div className="skeleton" /></div>
      ) : servers.length === 0 ? (
        <div className="empty-state">
          <strong>No servers added yet</strong>
          <p>Add your first Dell iDRAC endpoint from Inventory.</p>
          <Link to="/inventory" className="btn btn-primary" style={{ marginTop: 14 }}>Open Inventory</Link>
        </div>
      ) : (
        <div className="grid dashboard-server-grid">
          {servers.map(s => {
            const status = telemetryStatus(telemetryByServer[s.id], s.status);
            return (
            <article key={s.id} className={`card server-card ${status}`}>
              <div className="server-header">
                <div>
                  <div className="server-name">{s.name}</div>
                  <div className="server-meta">{hardwareByServer[s.id]?.model || s.model || 'Unknown Dell platform'}</div>
                </div>
                <span className={`badge ${statusClass(status)}`}><span className={`status-dot ${status}`} />{status}</span>
              </div>

              <div className="server-card-layout">
                <div className="server-overview-stack">
                  <div className="server-meta-grid server-meta-grid-top">
                    <div><span className="meta-label">IP</span><span className="meta-value mono">{s.ipmi_ip}</span></div>
                    <div>
                      <span className="meta-label">Controller</span>
                      <span className="meta-value mono" title={describeControllerSource(s)}>{describeController(s)}</span>
                    </div>
                    <div><span className="meta-label">Service tag</span><span className="meta-value mono">{hardwareByServer[s.id]?.service_tag || s.serial || '—'}</span></div>
                    <div><span className="meta-label">CPU</span><span className="meta-value">{formatProcessorSummary(hardwareByServer[s.id])}</span></div>
                    <div><span className="meta-label">RAM</span><span className="meta-value">{formatMemorySummary(hardwareByServer[s.id])}</span></div>
                    <div><span className="meta-label">PSU</span><span className="meta-value">{formatPsuSummary(hardwareByServer[s.id])}</span></div>
                    <div><span className="meta-label">Last seen</span><span className="meta-value">{lastSeen(s.last_seen)}</span></div>
                  </div>
                </div>

                <div className="server-telemetry-panel">
                  <ServerTelemetry server={s} telemetry={telemetryByServer[s.id]} hardware={hardwareByServer[s.id]} streamState={streamState} />
                </div>
              </div>

              <div className="card-actions">
                <Link to={`/temp-profiles?server_id=${s.id}`} className="btn btn-sm">Temps</Link>
                <Link to={`/server/${s.id}/fan-control`} className="btn btn-sm">Fans</Link>
                <Link to={`/server/${s.id}/settings`} className="btn btn-sm btn-primary">Settings</Link>
              </div>
            </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
