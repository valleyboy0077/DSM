import { useState, useEffect, useCallback } from 'react';
import type { Server, FanConfig, SensorSummary } from './types';
import { api } from './api';

function StatusBadge({ status }: { status: string }) {
  const cls = status === 'online' ? 'badge-green' : status === 'degraded' ? 'badge-yellow' : 'badge-red';
  return <span className={`badge ${cls}`}>{status}</span>;
}

function AddServerModal({ onClose, onAdded }: { onClose: () => void; onAdded: () => void }) {
  const [form, setForm] = useState({ name: '', ipmi_ip: '', ipmi_user: 'ned', ipmi_password: '' });
  const [error, setError] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      setError('');
      await api.addServer(form);
      onAdded();
      onClose();
    } catch (err: any) {
      setError(err.message);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <h2>Add Server</h2>
        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label>Server Name</label>
            <input value={form.name} onChange={e => setForm({...form, name: e.target.value})} placeholder="e.g. R730xd-Prod" required />
          </div>
          <div className="form-group">
            <label>iDRAC IP Address</label>
            <input value={form.ipmi_ip} onChange={e => setForm({...form, ipmi_ip: e.target.value})} placeholder="10.1.1.109" required />
          </div>
          <div className="form-group">
            <label>Username</label>
            <input value={form.ipmi_user} onChange={e => setForm({...form, ipmi_user: e.target.value})} required />
          </div>
          <div className="form-group">
            <label>Password</label>
            <input type="password" value={form.ipmi_password} onChange={e => setForm({...form, ipmi_password: e.target.value})} required />
          </div>
          {error && <div style={{color: 'var(--red)', fontSize: 12, marginBottom: 8}}>{error}</div>}
          <div className="form-actions">
            <button type="button" className="btn" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn btn-primary">Add Server</button>
          </div>
        </form>
      </div>
    </div>
  );
}

function FanPanel({ serverId, refresh }: { serverId: number; refresh: () => void }) {
  const [config, setConfig] = useState<FanConfig | null>(null);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<string>('');

  useEffect(() => {
    api.getFanConfig(serverId).then(setConfig).catch(() => {});
  }, [serverId]);

  const runCycle = async () => {
    setLoading(true);
    setResult('');
    try {
      const r = await api.controlFans(serverId, 'run_cycle');
      setResult(`Fan: ${r.fan_percent}% | CPU: ${r.cpu_temp}°C | Disk: ${r.disk_temp ?? 'N/A'}°C | ${r.action} - ${r.reason}`);
      refresh();
    } catch (e: any) {
      setResult(`Error: ${e.message}`);
    }
    setLoading(false);
  };

  const setManual = async (speed: number) => {
    setLoading(true);
    try {
      await api.controlFans(serverId, 'set_manual', speed);
      setResult(`Manual speed set to ${speed}%`);
      api.getFanConfig(serverId).then(setConfig).catch(() => {});
    } catch (e: any) {
      setResult(`Error: ${e.message}`);
    }
    setLoading(false);
  };

  const setAuto = async () => {
    setLoading(true);
    try {
      await api.controlFans(serverId, 'set_auto');
      setResult('Auto mode enabled');
    } catch (e: any) {
      setResult(`Error: ${e.message}`);
    }
    setLoading(false);
  };

  const resetDefault = async () => {
    setLoading(true);
    try {
      await api.controlFans(serverId, 'reset');
      setResult('Reset to iDRAC default');
    } catch (e: any) {
      setResult(`Error: ${e.message}`);
    }
    setLoading(false);
  };

  if (!config) return <div className="card"><div className="card-title">Loading fan config...</div></div>;

  return (
    <div className="card">
      <div className="card-title">Fan Control</div>
      <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12}}>
        <div>
          <div style={{fontSize: 11, color: 'var(--text-secondary)'}}>CPU Range</div>
          <div className="mono" style={{fontSize: 14}}>{config.cpu_temp_min}°C — {config.cpu_temp_max}°C</div>
        </div>
        <div>
          <div style={{fontSize: 11, color: 'var(--text-secondary)'}}>Disk Range</div>
          <div className="mono" style={{fontSize: 14}}>{config.disk_temp_min}°C — {config.disk_temp_max}°C</div>
        </div>
        <div>
          <div style={{fontSize: 11, color: 'var(--text-secondary)'}}>Mode</div>
          <div className="mono">{config.mode}</div>
        </div>
        <div>
          <div style={{fontSize: 11, color: 'var(--text-secondary)'}}>Manual Speed</div>
          <div className="mono">{config.manual_speed}%</div>
        </div>
      </div>
      <div style={{display: 'flex', gap: 8, flexWrap: 'wrap'}}>
        <button className="btn btn-sm" onClick={runCycle} disabled={loading}>Run PID Cycle</button>
        <button className="btn btn-sm" onClick={setAuto} disabled={loading}>Auto Mode</button>
        <button className="btn btn-sm" onClick={resetDefault} disabled={loading}>Reset Default</button>
        <button className="btn btn-sm" onClick={() => setManual(50)} disabled={loading}>Manual 50%</button>
        <button className="btn btn-sm" onClick={() => setManual(75)} disabled={loading}>Manual 75%</button>
        <button className="btn btn-sm" onClick={() => setManual(100)} disabled={loading}>Manual 100%</button>
      </div>
      {result && <div style={{marginTop: 12, fontSize: 12, fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)'}}>{result}</div>}
    </div>
  );
}

function PowerPanel({ serverId }: { serverId: number }) {
  const [loading, setLoading] = useState<string | null>(null);

  const doPower = async (action: string) => {
    setLoading(action);
    try {
      await api.powerServer(serverId, action);
    } catch (e: any) {
      alert(e.message);
    }
    setLoading(null);
  };

  return (
    <div className="card">
      <div className="card-title">Power Control</div>
      <div style={{display: 'flex', gap: 8}}>
        <button className="btn btn-sm btn-primary" onClick={() => doPower('on')} disabled={loading !== null}>
          {loading === 'on' ? '...' : 'Power On'}
        </button>
        <button className="btn btn-sm btn-danger" onClick={() => doPower('off')} disabled={loading !== null}>
          {loading === 'off' ? '...' : 'Power Off'}
        </button>
        <button className="btn btn-sm" onClick={() => doPower('restart')} disabled={loading !== null}>
          {loading === 'restart' ? '...' : 'Restart'}
        </button>
        <button className="btn btn-sm" onClick={() => doPower('shutdown')} disabled={loading !== null}>
          {loading === 'shutdown' ? '...' : 'Shutdown'}
        </button>
      </div>
    </div>
  );
}

function ServerDetail({ server, onBack }: { server: Server; onBack: () => void }) {
  const [summary, setSummary] = useState<SensorSummary | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchSummary = useCallback(async () => {
    try {
      const data = await api.getSensorSummary(server.id);
      setSummary(data);
    } catch (e) {
      console.error('Failed to fetch summary:', e);
    }
    setLoading(false);
  }, [server.id]);

  useEffect(() => { fetchSummary(); }, [fetchSummary]);

  useEffect(() => {
    const interval = setInterval(fetchSummary, 15000);
    return () => clearInterval(interval);
  }, [fetchSummary]);

  // Categorize sensors
  const cpuSensors = summary?.sensors.filter(s => s.type === 'cpu') || [];
  const diskSensors = summary?.sensors.filter(s => s.type === 'disk') || [];
  const ambientSensors = summary?.sensors.filter(s => s.type === 'ambient') || [];
  const otherSensors = summary?.sensors.filter(s => s.type === 'other' || !['cpu', 'disk', 'ambient'].includes(s.type)) || [];

  const tempColor = (value: number, type: string) => {
    if (type === 'cpu') return value > 70 ? 'var(--red)' : value > 50 ? 'var(--yellow)' : 'var(--green)';
    if (type === 'disk') return value > 45 ? 'var(--red)' : value > 35 ? 'var(--yellow)' : 'var(--green)';
    return 'var(--green)';
  };

  return (
    <div>
      <div className="toolbar">
        <button className="btn" onClick={onBack}>← Back to Servers</button>
        <button className="btn btn-sm" onClick={fetchSummary} disabled={loading}>↻ Refresh</button>
      </div>

      <div style={{display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20}}>
        <h2 style={{fontSize: 20}}>{server.name}</h2>
        <StatusBadge status={server.status || 'unknown'} />
        {server.model && <span className="badge badge-blue">{server.model}</span>}
        {server.serial && <span className="badge badge-blue">ST: {server.serial}</span>}
        {server.drac_version && <span className="badge badge-blue">{server.drac_version}</span>}
        <span style={{fontSize: 11, color: 'var(--text-secondary)'}} className="mono">{server.ipmi_ip}</span>
      </div>

      {/* Quick stats */}
      <div className="grid grid-4" style={{marginBottom: 24}}>
        {cpuSensors.length > 0 && (
          <div className="card">
            <div className="card-title">CPU Temp</div>
            <div className="card-value" style={{color: tempColor(cpuSensors[0].value, 'cpu')}}>
              {cpuSensors[0].value.toFixed(1)}°C
            </div>
            <div className="card-subtitle">{cpuSensors[0].label}</div>
          </div>
        )}
        {diskSensors.length > 0 && (
          <div className="card">
            <div className="card-title">Disk Temp</div>
            <div className="card-value" style={{color: tempColor(diskSensors[0].value, 'disk')}}>
              {diskSensors[0].value.toFixed(1)}°C
            </div>
            <div className="card-subtitle">{diskSensors[0].label}</div>
          </div>
        )}
        {ambientSensors.length > 0 && (
          <div className="card">
            <div className="card-title">Ambient</div>
            <div className="card-value" style={{color: tempColor(ambientSensors[0].value, 'ambient')}}>
              {ambientSensors[0].value.toFixed(1)}°C
            </div>
            <div className="card-subtitle">{ambientSensors[0].label}</div>
          </div>
        )}
        <div className="card">
          <div className="card-title">Sensors</div>
          <div className="card-value">{summary?.sensors.length || 0}</div>
          <div className="card-subtitle">Active readings</div>
        </div>
      </div>

      {/* Controls */}
      <div className="grid grid-2" style={{marginBottom: 24}}>
        <PowerPanel serverId={server.id} />
        <FanPanel serverId={server.id} refresh={fetchSummary} />
      </div>

      {/* All sensors table */}
      <div className="card">
        <div className="card-title">All Sensors</div>
        {loading ? <div style={{padding: 20, textAlign: 'center', color: 'var(--text-secondary)'}}>Loading...</div> : (
          <table>
            <thead>
              <tr>
                <th>Sensor</th>
                <th>Type</th>
                <th>Temperature</th>
                <th>Last Reading</th>
              </tr>
            </thead>
            <tbody>
              {([...cpuSensors, ...diskSensors, ...ambientSensors, ...otherSensors]).map((s, i) => (
                <tr key={i}>
                  <td className="mono">{s.label}</td>
                  <td><span className={`badge ${s.type === 'cpu' ? 'badge-red' : s.type === 'disk' ? 'badge-yellow' : 'badge-blue'}`}>{s.type}</span></td>
                  <td className="mono" style={{color: tempColor(s.value, s.type)}}>{s.value.toFixed(1)}°C</td>
                  <td style={{fontSize: 11, color: 'var(--text-secondary)'}}>
                    {s.timestamp ? new Date(s.timestamp).toLocaleTimeString() : '—'}
                  </td>
                </tr>
              ))}
              {summary && summary.sensors.length === 0 && (
                <tr><td colSpan={4} style={{textAlign: 'center', padding: 20, color: 'var(--text-secondary)'}}>No sensor data yet</td></tr>
              )}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

export default function App() {
  const [servers, setServers] = useState<Server[]>([]);
  const [selectedServer, setSelectedServer] = useState<Server | null>(null);
  const [showAddModal, setShowAddModal] = useState(false);
  const [loading, setLoading] = useState(true);

  const fetchServers = useCallback(async () => {
    try {
      const data = await api.listServers();
      setServers(data);
    } catch (e) {
      console.error('Failed to fetch servers:', e);
    }
    setLoading(false);
  }, []);

  useEffect(() => { fetchServers(); }, [fetchServers]);

  const deleteServer = async (id: number) => {
    if (!confirm('Remove this server?')) return;
    try {
      await api.deleteServer(id);
      fetchServers();
      if (selectedServer?.id === id) setSelectedServer(null);
    } catch (e: any) {
      alert(e.message);
    }
  };

  if (selectedServer) {
    return (
      <div className="app">
        <header className="header">
          <h1><span className="logo">⬡</span> Dell Server Manager</h1>
          <div className="header-info">
            <span className="mono">{selectedServer.ipmi_ip}</span>
          </div>
        </header>
        <div className="container">
          <ServerDetail server={selectedServer} onBack={() => setSelectedServer(null)} />
        </div>
      </div>
    );
  }

  return (
    <div className="app">
      <header className="header">
        <h1><span className="logo">⬡</span> Dell Server Manager</h1>
        <div className="header-info">
          <span>{servers.length} server{servers.length !== 1 ? 's' : ''}</span>
          <span className="status-dot online"></span>
        </div>
      </header>
      <div className="container">
        <div className="toolbar">
          <div className="section-title" style={{marginBottom: 0, border: 'none', padding: 0}}>Managed Servers</div>
          <button className="btn btn-primary" onClick={() => setShowAddModal(true)}>+ Add Server</button>
        </div>

        {loading ? (
          <div className="empty-state"><p>Loading servers...</p></div>
        ) : servers.length === 0 ? (
          <div className="empty-state">
            <p>No servers configured yet</p>
            <button className="btn btn-primary" onClick={() => setShowAddModal(true)}>Add Your First Server</button>
          </div>
        ) : (
          <div className="grid grid-3">
            {servers.map(server => (
              <div key={server.id} className="server-card" onClick={() => setSelectedServer(server)}>
                <div className="server-header">
                  <span className="server-name">{server.name}</span>
                  <StatusBadge status={server.status} />
                </div>
                <div className="server-meta">
                  {server.model || 'Unknown model'} {server.serial ? `· ST:${server.serial}` : ''} · {server.ipmi_ip}
                </div>
                <div className="server-meta" style={{marginTop: 4}}>
                  {server.drac_version} · {server.last_seen ? `Seen: ${new Date(server.last_seen).toLocaleTimeString()}` : 'Never polled'}
                </div>
                <div style={{display: 'flex', gap: 8, marginTop: 12}}>
                  <button className="btn btn-sm" onClick={e => { e.stopPropagation(); setSelectedServer(server); }}>View Details</button>
                  <button className="btn btn-sm btn-danger" onClick={e => { e.stopPropagation(); deleteServer(server.id); }}>Remove</button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {showAddModal && (
        <AddServerModal onClose={() => setShowAddModal(false)} onAdded={fetchServers} />
      )}
    </div>
  );
}
