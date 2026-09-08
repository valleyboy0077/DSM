import { useState, useEffect, useCallback } from 'react';
import type { CSSProperties } from 'react';
import { useParams } from 'react-router-dom';
import { api } from '../api';
import type { FanConfig, FanTelemetryItem, FanTelemetryResponse, Server } from '../types';
import { describeController } from '../serverMetadata';

function fanSpinDuration(percent: number | null): string {
  if (!percent || percent <= 0) return '2.8s';
  const normalized = Math.max(0, Math.min(100, percent));
  const seconds = 2.55 - normalized * 0.02;
  return `${Math.max(0.35, seconds).toFixed(2)}s`;
}

function formatUpdated(value?: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString();
}

function formatPercent(value: number | null): string {
  return value == null ? '—' : `${value}%`;
}

function formatRpm(value: number): string {
  return value > 0 ? `${value.toLocaleString()} RPM` : '—';
}

function fanPercentLabel(fan: FanTelemetryItem): string {
  if (fan.percent_source === 'rpm_estimate') return 'RPM estimate';
  if (fan.percent_source === 'controller_percentage') return 'Reported %';
  return 'PWM';
}

const DEFAULT_POLLING_SECONDS = 20;

function fanHealthClass(health: string): string {
  if (health === 'OK') return 'healthy';
  if (health === 'Critical') return 'offline';
  return 'warning';
}

function fanGlowClass(fan: FanTelemetryItem): string {
  if (fan.health === 'Critical') return 'fan-glow-critical';
  if (fan.health !== 'OK') return 'fan-glow-warning';
  const percent = fan.percent ?? 0;
  if (percent >= 35) return 'fan-glow-fast';
  if (percent >= 20) return 'fan-glow-medium';
  return 'fan-glow-low';
}

function deriveFanSlotLabel(fan: FanTelemetryItem, index: number): string {
  const candidates = [fan.member_id, fan.name];
  for (const candidate of candidates) {
    const slotMatch = candidate?.match(/(?:fan|slot|systemboard\.fan)\D*(\d+)/i);
    if (slotMatch) return `Fan ${slotMatch[1]}`;
    const trailingMatch = candidate?.match(/(\d+)$/);
    if (trailingMatch) return `Fan ${trailingMatch[1]}`;
  }
  return `Fan ${index + 1}`;
}

function extractFanSlotNumber(fan: FanTelemetryItem, index: number): string {
  const label = deriveFanSlotLabel(fan, index);
  const match = label.match(/(\d+)/);
  return match?.[1] || String(index + 1);
}

function summarizeFans(fans: FanTelemetryItem[]) {
  if (fans.length === 0) {
    return {
      avgPercent: null as number | null,
      avgRpm: 0,
      minRpm: 0,
      maxRpm: 0,
      highestPercent: null as number | null,
    };
  }

  const rpms = fans.map((fan) => fan.rpm).filter((rpm) => rpm > 0);
  const percents = fans.map((fan) => fan.percent).filter((percent): percent is number => percent != null);

  return {
    avgPercent: percents.length ? Math.round(percents.reduce((sum, value) => sum + value, 0) / percents.length) : null,
    avgRpm: rpms.length ? Math.round(rpms.reduce((sum, value) => sum + value, 0) / rpms.length) : 0,
    minRpm: rpms.length ? Math.min(...rpms) : 0,
    maxRpm: rpms.length ? Math.max(...rpms) : 0,
    highestPercent: percents.length ? Math.max(...percents) : null,
  };
}

export default function FanControl() {
  const { server_id } = useParams();
  const serverId = parseInt(server_id || '0');
  const [server, setServer] = useState<Server | null>(null);
  const [config, setConfig] = useState<FanConfig | null>(null);
  const [telemetry, setTelemetry] = useState<FanTelemetryResponse | null>(null);
  const [lastResult, setLastResult] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [telemetryLoading, setTelemetryLoading] = useState(false);
  const [telemetryError, setTelemetryError] = useState<string | null>(null);
  const [manualSpeed, setManualSpeed] = useState(25);
  const fanSummary = summarizeFans(telemetry?.fans ?? []);
  const pollingSeconds = config?.polling_seconds ?? DEFAULT_POLLING_SECONDS;
  const manualCommandActive = config?.mode === 'manual' && !config.auto_control;

  const fetchData = useCallback(async () => {
    setTelemetryLoading(true);
    setTelemetryError(null);
    try {
      const [s, c, t] = await Promise.all([
        api.getServer(serverId),
        api.getFanConfig(serverId),
        api.getFanTelemetry(serverId),
      ]);
      setServer(s);
      setConfig(c ? { ...c, polling_seconds: c.polling_seconds ?? DEFAULT_POLLING_SECONDS } : c);
      setManualSpeed(c?.manual_speed || 25);
      setTelemetry(t);
    } catch (err: any) {
      console.error(err);
      const message = err?.message || 'Failed to load fan control data';
      setTelemetryError(message);
      setLastResult((previous: any) => previous?.error ? previous : { error: message });
    } finally {
      setTelemetryLoading(false);
    }
  }, [serverId]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleControl = async (action: string, speed?: number) => {
    setLoading(true);
    try {
      const result = await api.controlFans(serverId, action, speed);
      setLastResult(result);
      await fetchData();
    } catch (err: any) {
      setLastResult({ error: err.message });
    }
    setLoading(false);
  };

  const handleSaveConfig = async () => {
    if (!config) return;
    setLoading(true);
    try {
      const updated = await api.updateFanConfig(serverId, config);
      setConfig({ ...updated, polling_seconds: updated.polling_seconds ?? DEFAULT_POLLING_SECONDS });
      setLastResult({ message: 'Fan config saved' });
      await fetchData();
    } catch (err: any) {
      setLastResult({ error: err.message });
    }
    setLoading(false);
  };

  if (!server) return <div className="skeleton" />;

  return (
    <div>
      <div className="page-toolbar">
        <div>
          <h2 className="page-title">Fan control · {server.name}</h2>
          <p className="page-subtitle"><span className="mono">{server.ipmi_ip}</span> · {server.model || 'Unknown model'} · {server.serial || 'no service tag'}</p>
          <p className="page-subtitle">Controller: {describeController(server)}</p>
        </div>
        <button className="btn" onClick={fetchData} disabled={loading || telemetryLoading}>Refresh</button>
      </div>

      <div className="inline-alert warning" style={{ marginBottom: 18 }}>
        Fan changes can affect hardware temperature. Use manual override only while monitoring system thermals.
      </div>

      <div className="grid grid-4" style={{ marginBottom: 18 }}>
        <div className="card metric-card"><div className="card-title">Mode</div><div className="card-value" style={{ fontSize: 28 }}>{config?.mode || '—'}</div><div className="card-subtitle">Current control policy</div></div>
        <div className="card metric-card"><div className="card-title">Manual target</div><div className="card-value">{config?.manual_speed ?? manualSpeed}%</div><div className="card-subtitle">Stored fan override</div></div>
        <div className="card metric-card"><div className="card-title">{manualCommandActive ? 'Manual command' : 'DSM auto target'}</div><div className="card-value">{formatPercent(manualCommandActive ? (config?.current_target_percent ?? config?.manual_speed ?? null) : (config?.current_target_percent ?? null))}</div><div className="card-subtitle">{manualCommandActive ? 'Last manual duty command' : 'Last target commanded by automatic control'}</div></div>
        <div className="card metric-card"><div className="card-title">Auto control</div><div className="card-value" style={{ fontSize: 28, color: config?.auto_control ? 'var(--green)' : 'var(--yellow)' }}>{config?.auto_control ? 'ON' : 'OFF'}</div><div className="card-subtitle">Backend control loop</div></div>
        <div className="card metric-card"><div className="card-title">Polling</div><div className="card-value">{pollingSeconds}s</div><div className="card-subtitle">Control loop interval</div></div>
        <div className="card metric-card"><div className="card-title">Detected fans</div><div className="card-value">{telemetry?.fans.length ?? 0}</div><div className="card-subtitle">Live chassis fan inventory</div></div>
      </div>

      <div className="card" style={{ marginBottom: 18 }}>
        <div className="hardware-section-header" style={{ marginBottom: 12 }}>
          <div>
            <div className="card-title">Live fan telemetry</div>
            <p style={{ margin: '6px 0 0', color: 'var(--text-secondary)' }}>
              Showing each detected chassis fan with live RPM and controller-reported duty where available. Otherwise, the displayed percentage is an RPM-derived estimate.
            </p>
          </div>
          <div className="hardware-section-meta">
            Source: {telemetry?.source?.toUpperCase() || '—'} · Updated {formatUpdated(telemetry?.collected_at)}
          </div>
        </div>

        {telemetryLoading && <div className="inline-alert">Loading live fan telemetry…</div>}
        {!telemetryLoading && telemetryError && <div className="inline-alert danger">Unable to load live fan telemetry: {telemetryError}</div>}
        {!telemetryLoading && telemetry?.source === 'wsman' && (
          <div className="inline-alert warning" style={{ marginBottom: 12 }}>
            WS-Man may report a stale PWM percentage. PWM below is always the controller-reported value; compare it with the requested target and confirm changes with live RPM.
          </div>
        )}
        {!telemetryLoading && telemetry?.fans.some((fan) => fan.percent_source === 'rpm_estimate') && (
          <div className="inline-alert warning" style={{ marginBottom: 12 }}>
            RPM-derived percentages are display estimates, not controller duty, and DSM does not use them for automatic fan control.
          </div>
        )}

        {!telemetryLoading && telemetry && telemetry.fans.length > 0 && (
          <>
            <div className="fan-summary-grid">
              <div className="fan-summary-card">
                <span className="meta-label">Average displayed %</span>
                <strong>{formatPercent(fanSummary.avgPercent)}</strong>
                <span className="card-subtitle">See each fan for PWM, reported %, or estimate</span>
              </div>
              <div className="fan-summary-card">
                <span className="meta-label">Average RPM</span>
                <strong>{formatRpm(fanSummary.avgRpm)}</strong>
                <span className="card-subtitle">Current fleet average</span>
              </div>
              <div className="fan-summary-card">
                <span className="meta-label">RPM range</span>
                <strong>{fanSummary.minRpm > 0 ? `${fanSummary.minRpm.toLocaleString()} – ${fanSummary.maxRpm.toLocaleString()}` : '—'}</strong>
                <span className="card-subtitle">Lowest to highest live RPM</span>
              </div>
              <div className="fan-summary-card">
                <span className="meta-label">Peak displayed %</span>
                <strong>{formatPercent(fanSummary.highestPercent)}</strong>
                <span className="card-subtitle">Highest displayed fan percentage</span>
              </div>
            </div>

            <div className="fan-slot-strip" aria-label={`Detected ${telemetry.fans.length} fans`}>
              {telemetry.fans.map((fan, index) => {
                const slotLabel = deriveFanSlotLabel(fan, index);
                const slotNumber = extractFanSlotNumber(fan, index);
                const displayPercent = fan.percent;
                const spinDuration = fanSpinDuration(displayPercent);
                const glowClass = fanGlowClass(fan);
                return (
                  <div className={`fan-slot-card ${glowClass}`} key={fan.member_id || fan.name}>
                    <div className="fan-slot-card-header">
                      <span className="fan-slot-badge">Slot {slotNumber}</span>
                      <span className={`status-pill ${fanHealthClass(fan.health)}`}>
                        {fan.health}
                      </span>
                    </div>

                    <div className="fan-slot-title">{slotLabel}</div>
                    <div className="fan-slot-subtitle">{fan.name}</div>

                    <div className="fan-case-frame">
                      <div className="fan-photo-graphic" style={{ '--fan-spin-duration': spinDuration } as CSSProperties}>
                        <svg viewBox="0 0 160 160" className="fan-photo-svg" aria-hidden="true" focusable="false">
                          <defs>
                            <radialGradient id="fanHubGlow" cx="50%" cy="42%" r="70%">
                              <stop offset="0%" stopColor="rgba(255,255,255,.16)" />
                              <stop offset="38%" stopColor="rgba(255,255,255,.03)" />
                              <stop offset="100%" stopColor="rgba(0,0,0,0)" />
                            </radialGradient>
                            <linearGradient id="fanFrameSheen" x1="0%" y1="0%" x2="100%" y2="100%">
                              <stop offset="0%" stopColor="rgba(255,255,255,.12)" />
                              <stop offset="22%" stopColor="rgba(255,255,255,.03)" />
                              <stop offset="100%" stopColor="rgba(0,0,0,.18)" />
                            </linearGradient>
                            <radialGradient id="fanShroudShade" cx="50%" cy="48%" r="60%">
                              <stop offset="0%" stopColor="#1c2128" />
                              <stop offset="58%" stopColor="#151a20" />
                              <stop offset="100%" stopColor="#0f1318" />
                            </radialGradient>
                            <linearGradient id="fanBladeShade" x1="36%" y1="18%" x2="76%" y2="82%">
                              <stop offset="0%" stopColor="#2a3037" />
                              <stop offset="18%" stopColor="#1f252c" />
                              <stop offset="52%" stopColor="#151a20" />
                              <stop offset="100%" stopColor="#0d1116" />
                            </linearGradient>
                          </defs>

                          <rect x="12" y="12" width="136" height="136" rx="22" className="fan-frame-outer" />
                          <rect x="22" y="22" width="116" height="116" rx="19" className="fan-frame-inner" />
                          <rect x="12" y="12" width="136" height="136" rx="22" className="fan-frame-sheen" />

                          <g className="fan-corner-details">
                            <path d="M25 25 L46 25 L25 46 Z" className="fan-corner-bevel" />
                            <path d="M135 25 L114 25 L135 46 Z" className="fan-corner-bevel" />
                            <path d="M25 135 L46 135 L25 114 Z" className="fan-corner-bevel" />
                            <path d="M135 135 L114 135 L135 114 Z" className="fan-corner-bevel" />
                          </g>

                          <g className="fan-mount-holes">
                            <circle cx="28" cy="28" r="7.25" className="fan-mount-ring" />
                            <circle cx="132" cy="28" r="7.25" className="fan-mount-ring" />
                            <circle cx="28" cy="132" r="7.25" className="fan-mount-ring" />
                            <circle cx="132" cy="132" r="7.25" className="fan-mount-ring" />
                            <circle cx="28" cy="28" r="2.65" className="fan-mount-core" />
                            <circle cx="132" cy="28" r="2.65" className="fan-mount-core" />
                            <circle cx="28" cy="132" r="2.65" className="fan-mount-core" />
                            <circle cx="132" cy="132" r="2.65" className="fan-mount-core" />
                          </g>

                          <circle cx="80" cy="80" r="47" className="fan-shroud-ring" />
                          <circle cx="80" cy="80" r="42" className="fan-shroud-inner" />
                          <circle cx="80" cy="80" r="42" className="fan-shroud-glow" />

                          <g className="fan-rotor" style={{ transformOrigin: '80px 80px', transformBox: 'view-box' }}>
                            <g className="fan-blade-shape">
                              <path d="M80 80 C84 69, 93 58, 106 47 C116 39, 124 36, 128 39 C132 42, 131 49, 128 59 C124 70, 118 82, 109 90 C100 98, 90 101, 84 97 C80 94, 78 88, 80 80 Z" />
                            </g>
                            <g className="fan-blade-shape" transform="rotate(51.4286 80 80)">
                              <path d="M80 80 C84 69, 93 58, 106 47 C116 39, 124 36, 128 39 C132 42, 131 49, 128 59 C124 70, 118 82, 109 90 C100 98, 90 101, 84 97 C80 94, 78 88, 80 80 Z" />
                            </g>
                            <g className="fan-blade-shape" transform="rotate(102.8572 80 80)">
                              <path d="M80 80 C84 69, 93 58, 106 47 C116 39, 124 36, 128 39 C132 42, 131 49, 128 59 C124 70, 118 82, 109 90 C100 98, 90 101, 84 97 C80 94, 78 88, 80 80 Z" />
                            </g>
                            <g className="fan-blade-shape" transform="rotate(154.2858 80 80)">
                              <path d="M80 80 C84 69, 93 58, 106 47 C116 39, 124 36, 128 39 C132 42, 131 49, 128 59 C124 70, 118 82, 109 90 C100 98, 90 101, 84 97 C80 94, 78 88, 80 80 Z" />
                            </g>
                            <g className="fan-blade-shape" transform="rotate(205.7144 80 80)">
                              <path d="M80 80 C84 69, 93 58, 106 47 C116 39, 124 36, 128 39 C132 42, 131 49, 128 59 C124 70, 118 82, 109 90 C100 98, 90 101, 84 97 C80 94, 78 88, 80 80 Z" />
                            </g>
                            <g className="fan-blade-shape" transform="rotate(257.143 80 80)">
                              <path d="M80 80 C84 69, 93 58, 106 47 C116 39, 124 36, 128 39 C132 42, 131 49, 128 59 C124 70, 118 82, 109 90 C100 98, 90 101, 84 97 C80 94, 78 88, 80 80 Z" />
                            </g>
                            <g className="fan-blade-shape" transform="rotate(308.5716 80 80)">
                              <path d="M80 80 C84 69, 93 58, 106 47 C116 39, 124 36, 128 39 C132 42, 131 49, 128 59 C124 70, 118 82, 109 90 C100 98, 90 101, 84 97 C80 94, 78 88, 80 80 Z" />
                            </g>
                            <circle cx="80" cy="80" r="15" className="fan-hub-outer" />
                            <circle cx="80" cy="80" r="10.25" className="fan-hub-core" />
                            <circle cx="80" cy="80" r="15" className="fan-hub-glow" />
                            <circle cx="74.4" cy="79" r="1.2" className="fan-hub-mark" />
                            <circle cx="80.6" cy="80.1" r="1.05" className="fan-hub-mark fan-hub-mark-center" />
                          </g>
                        </svg>
                      </div>
                    </div>

                    <div className="fan-slot-metrics">
                      <div className="fan-metric-box">
                        <span className="meta-label">{fanPercentLabel(fan)}</span>
                        <strong>{formatPercent(displayPercent ?? null)}</strong>
                      </div>
                      <div className="fan-metric-box">
                        <span className="meta-label">RPM</span>
                        <strong>{formatRpm(fan.rpm)}</strong>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </>
        )}

        {!telemetryLoading && telemetry && telemetry.fans.length === 0 && !telemetryError && (
          <div className="inline-alert">No live fan telemetry was returned by this controller.</div>
        )}
      </div>

      <div className="grid grid-2" style={{ marginBottom: 18 }}>
        <div className="card">
          <div className="card-title">Quick actions</div>
          <div className="row-actions">
            <button className="btn btn-primary" onClick={() => handleControl('run_cycle')} disabled={loading}>Run Control Cycle</button>
            <button className="btn" onClick={() => handleControl('set_auto')} disabled={loading}>Set Auto Mode</button>
            <button className="btn" onClick={() => handleControl('reset')} disabled={loading}>Reset Defaults</button>
          </div>
        </div>
        <div className="card">
          <div className="card-title">Temperature range policy</div>
          {config ? <div className="detail-grid">
            <div className="detail-card"><span className="meta-label">CPU range</span><span className="meta-value mono">{config.cpu_temp_min}°C — {config.cpu_temp_max}°C</span></div>
            <div className="detail-card"><span className="meta-label">Disk range</span><span className="meta-value mono">{config.disk_temp_min}°C — {config.disk_temp_max}°C</span></div>
          </div> : <div className="skeleton" />}
        </div>
      </div>

      <div className="card" style={{ marginBottom: 18 }}>
        <div className="card-title">Edit fan configuration</div>
        {config && (
          <div className="grid grid-2">
            <div className="form-group"><label>CPU Temp Min (°C)</label><input type="number" value={config.cpu_temp_min} onChange={e => setConfig({...config, cpu_temp_min: parseFloat(e.target.value)})} /></div>
            <div className="form-group"><label>CPU Temp Max (°C)</label><input type="number" value={config.cpu_temp_max} onChange={e => setConfig({...config, cpu_temp_max: parseFloat(e.target.value)})} /></div>
            <div className="form-group"><label>Disk Temp Min (°C)</label><input type="number" value={config.disk_temp_min} onChange={e => setConfig({...config, disk_temp_min: parseFloat(e.target.value)})} /></div>
            <div className="form-group"><label>Disk Temp Max (°C)</label><input type="number" value={config.disk_temp_max} onChange={e => setConfig({...config, disk_temp_max: parseFloat(e.target.value)})} /></div>
            <div className="form-group"><label>Manual Fan Speed (%)</label><input type="number" value={config.manual_speed} onChange={e => setConfig({...config, manual_speed: parseInt(e.target.value)})} min={1} max={100} /></div>
            <div className="form-group"><label>Polling Interval (seconds)</label><input type="number" value={pollingSeconds} onChange={e => setConfig({...config, polling_seconds: parseInt(e.target.value)})} min={5} max={3600} /></div>
            <div className="form-group"><label>Mode</label><select value={config.mode} onChange={e => setConfig({...config, mode: e.target.value})}><option value="auto">Auto</option><option value="manual">Manual</option><option value="profile">Profile</option></select></div>
            <label className="checkbox-row" style={{ gridColumn: '1 / -1' }}><input type="checkbox" checked={config.auto_control} onChange={e => setConfig({...config, auto_control: e.target.checked})} /> Enable auto fan control</label>
            <div style={{ gridColumn: '1 / -1' }}><button className="btn btn-primary" onClick={handleSaveConfig} disabled={loading}>Save Config</button></div>
          </div>
        )}
      </div>

      <div className="card" style={{ marginBottom: 18 }}>
        <div className="card-title">Manual fan speed override</div>
        <div className="range-wrap">
          <input type="range" min={1} max={100} value={manualSpeed} onChange={e => setManualSpeed(parseInt(e.target.value))} />
          <span className="big-readout">{manualSpeed}%</span>
          <button className="btn btn-primary" onClick={() => handleControl('set_manual', manualSpeed)} disabled={loading}>Apply Override</button>
        </div>
      </div>

      {lastResult && (
        <div className={`inline-alert ${lastResult.error ? 'danger' : ''}`}>
          {lastResult.error ? `Error: ${lastResult.error}` : (lastResult.message || 'Fan command completed')}
          <div className="json-panel"><pre>{JSON.stringify(lastResult, null, 2)}</pre></div>
        </div>
      )}
    </div>
  );
}
