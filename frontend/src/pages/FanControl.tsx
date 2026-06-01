import { useState, useEffect, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import { api } from '../api';
import type { Server } from '../types';

export default function FanControl() {
  const { server_id } = useParams();
  const serverId = parseInt(server_id || '0');
  const [server, setServer] = useState<Server | null>(null);
  const [config, setConfig] = useState<any>(null);
  const [lastResult, setLastResult] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [manualSpeed, setManualSpeed] = useState(50);

  const fetchData = useCallback(async () => {
    try {
      const [s, c] = await Promise.all([
        api.getServer(serverId),
        api.getFanConfig(serverId),
      ]);
      setServer(s);
      setConfig(c);
    } catch (err) {
      console.error(err);
    }
  }, [serverId]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleControl = async (action: string, speed?: number) => {
    setLoading(true);
    try {
      const result = await api.controlFans(serverId, action, speed);
      setLastResult(result);
      fetchData();
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
      setConfig(updated);
      setLastResult({ message: 'Fan config saved' });
    } catch (err: any) {
      setLastResult({ error: err.message });
    }
    setLoading(false);
  };

  if (!server) {
    return <div className="container"><p>Loading...</p></div>;
  }

  return (
    <div>
      <div className="toolbar">
        <h2 style={{fontSize: 18}}>Fan Control — {server.name}</h2>
        <button className="btn btn-sm" onClick={fetchData} disabled={loading}>↻ Refresh</button>
      </div>

      {/* Current Status */}
      <div className="grid grid-2" style={{marginBottom: 16}}>
        <div className="card">
          <div className="card-title">Current Fan Config</div>
          {config && (
            <div style={{fontSize: 13}}>
              <p><strong>Mode:</strong> {config.mode}</p>
              <p><strong>CPU Range:</strong> {config.cpu_temp_min}°C — {config.cpu_temp_max}°C</p>
              <p><strong>Disk Range:</strong> {config.disk_temp_min}°C — {config.disk_temp_max}°C</p>
              <p><strong>Manual Speed:</strong> {config.manual_speed}%</p>
              <p><strong>Auto Control:</strong> {config.auto_control ? 'Enabled' : 'Disabled'}</p>
            </div>
          )}
        </div>
        <div className="card">
          <div className="card-title">Quick Actions</div>
          <div style={{display: 'flex', gap: 8, flexWrap: 'wrap'}}>
            <button className="btn btn-sm btn-primary" onClick={() => handleControl('run_cycle')} disabled={loading}>
              Run Control Cycle
            </button>
            <button className="btn btn-sm" onClick={() => handleControl('set_auto')} disabled={loading}>
              Set Auto Mode
            </button>
            <button className="btn btn-sm" onClick={() => handleControl('reset')} disabled={loading}>
              Reset to Default
            </button>
          </div>
        </div>
      </div>

      {/* Edit Config */}
      <div className="card" style={{marginBottom: 16}}>
        <div className="card-title">Edit Fan Configuration</div>
        {config && (
          <div className="grid grid-2">
            <div className="form-group">
              <label>CPU Temp Min (°C)</label>
              <input type="number" value={config.cpu_temp_min} onChange={e => setConfig({...config, cpu_temp_min: parseFloat(e.target.value)})} />
            </div>
            <div className="form-group">
              <label>CPU Temp Max (°C)</label>
              <input type="number" value={config.cpu_temp_max} onChange={e => setConfig({...config, cpu_temp_max: parseFloat(e.target.value)})} />
            </div>
            <div className="form-group">
              <label>Disk Temp Min (°C)</label>
              <input type="number" value={config.disk_temp_min} onChange={e => setConfig({...config, disk_temp_min: parseFloat(e.target.value)})} />
            </div>
            <div className="form-group">
              <label>Disk Temp Max (°C)</label>
              <input type="number" value={config.disk_temp_max} onChange={e => setConfig({...config, disk_temp_max: parseFloat(e.target.value)})} />
            </div>
            <div className="form-group">
              <label>Manual Fan Speed (%)</label>
              <input type="number" value={config.manual_speed} onChange={e => setConfig({...config, manual_speed: parseInt(e.target.value)})} min={1} max={100} />
            </div>
            <div className="form-group">
              <label>Mode</label>
              <select value={config.mode} onChange={e => setConfig({...config, mode: e.target.value})}>
                <option value="auto">Auto</option>
                <option value="manual">Manual</option>
                <option value="profile">Profile</option>
              </select>
            </div>
            <div style={{gridColumn: '1 / -1'}}>
              <label style={{display: 'flex', alignItems: 'center', gap: 8}}>
                <input type="checkbox" checked={config.auto_control} onChange={e => setConfig({...config, auto_control: e.target.checked})} />
                Enable auto fan control
              </label>
            </div>
            <div style={{gridColumn: '1 / -1'}}>
              <button className="btn btn-primary" onClick={handleSaveConfig} disabled={loading}>Save Config</button>
            </div>
          </div>
        )}
      </div>

      {/* Manual Override */}
      <div className="card" style={{marginBottom: 16}}>
        <div className="card-title">Manual Fan Speed Override</div>
        <div style={{display: 'flex', gap: 12, alignItems: 'center'}}>
          <input
            type="range"
            min={1}
            max={100}
            value={manualSpeed}
            onChange={e => setManualSpeed(parseInt(e.target.value))}
            style={{flex: 1}}
          />
          <span className="mono" style={{fontSize: 18, fontWeight: 600}}>{manualSpeed}%</span>
          <button
            className="btn btn-primary"
            onClick={() => handleControl('set_manual', manualSpeed)}
            disabled={loading}
          >
            Apply
          </button>
        </div>
      </div>

      {/* Last Result */}
      {lastResult && (
        <div className="card">
          <div className="card-title">Last Result</div>
          {lastResult.error ? (
            <p style={{color: 'var(--red)'}}>Error: {lastResult.error}</p>
          ) : (
            <pre style={{fontSize: 12, whiteSpace: 'pre-wrap'}}>
              {JSON.stringify(lastResult, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
