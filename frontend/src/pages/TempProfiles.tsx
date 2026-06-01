import { useState, useEffect, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../api';
import type { Server, TempProfile, TempRange } from '../types';

const DEFAULT_RANGES: TempRange[] = [
  { id: 0, component_type: 'cpu', component_label: 'CPU 1', temp_min: 45, temp_max: 70, warning_min: null, warning_max: 60, critical_min: null, critical_max: 85 },
  { id: 1, component_type: 'disk', component_label: 'Disk Bay', temp_min: 32, temp_max: 45, warning_min: null, warning_max: 50, critical_min: null, critical_max: 60 },
  { id: 2, component_type: 'ambient', component_label: 'Inlet', temp_min: 10, temp_max: 35, warning_min: null, warning_max: 40, critical_min: null, critical_max: 45 },
  { id: 3, component_type: 'vrm', component_label: 'VRM', temp_min: 40, temp_max: 80, warning_min: null, warning_max: 90, critical_min: null, critical_max: 105 },
  { id: 4, component_type: 'psu', component_label: 'PSU', temp_min: 20, temp_max: 55, warning_min: null, warning_max: 60, critical_min: null, critical_max: 70 },
];

export default function TempProfiles() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [servers, setServers] = useState<Server[]>([]);
  const [selectedServerId, setSelectedServerId] = useState<number | null>(null);
  const serverId = selectedServerId || parseInt(searchParams.get('server_id') || '', 10) || null;
  const [profiles, setProfiles] = useState<TempProfile[]>([]);
  const [editingProfile, setEditingProfile] = useState<TempProfile | null>(null);
  const [ranges, setRanges] = useState<TempRange[]>(DEFAULT_RANGES);
  const [loading, setLoading] = useState(true);

  const fetchServers = useCallback(async () => {
    try {
      const list = await api.listServers();
      setServers(list);
    } catch { /* ignore */ }
  }, []);

  const fetchProfiles = useCallback(async () => {
    if (!serverId) {
      setProfiles([]);
      setLoading(false);
      return;
    }
    try {
      setLoading(true);
      const list = await api.listProfiles(serverId);
      setProfiles(list);
    } catch { /* ignore */ } finally {
      setLoading(false);
    }
  }, [serverId]);

  useEffect(() => {
    fetchServers();
  }, [fetchServers]);

  useEffect(() => {
    fetchProfiles();
  }, [fetchProfiles]);

  useEffect(() => {
    // When servers load, auto-select the first one if no selection yet
    if (servers.length > 0 && !serverId) {
      setSelectedServerId(servers[0].id);
      setSearchParams({ server_id: String(servers[0].id) });
    }
  }, [servers, serverId, setSearchParams]);

  const handleServerChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const id = parseInt(e.target.value, 10);
    setSelectedServerId(id);
    setSearchParams({ server_id: String(id) });
  };

  const handleSave = async () => {
    if (!editingProfile || !serverId) return;
    try {
      const data = { server_id: serverId, name: editingProfile.name, is_default: editingProfile.is_default, ranges };
      if (editingProfile.id) {
        await api.updateProfile(editingProfile.id, data);
      } else {
        await api.createProfile(serverId, data);
      }
      setEditingProfile(null);
      setRanges(DEFAULT_RANGES);
      fetchProfiles();
    } catch (err) {
      console.error('Failed to save profile:', err);
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm('Delete this profile?')) return;
    try {
      await api.deleteProfile(id);
      fetchProfiles();
    } catch (err) {
      console.error('Failed to delete profile:', err);
    }
  };

  if (servers.length === 0) {
    return (
      <div className="container">
        <div className="card" style={{textAlign: 'center', padding: 40}}>
          <h2 style={{fontSize: 18, marginBottom: 12}}>No Servers Available</h2>
          <p style={{color: 'var(--text-secondary)'}}>
            Add a server to Inventory first, then come back to manage temperature profiles.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="toolbar" style={{marginBottom: 16}}>
        <h2 style={{fontSize: 18, margin: 0}}>Temperature Profiles</h2>
        <div style={{flex: 1}} />
        <label style={{color: 'var(--text-secondary)', fontSize: 13}}>Server:</label>
        <select
          value={serverId || ''}
          onChange={handleServerChange}
          style={{
            padding: '4px 8px',
            borderRadius: 4,
            border: '1px solid var(--border)',
            background: 'var(--bg-card)',
            color: 'var(--text-primary)',
          }}
        >
          {servers.map(s => (
            <option key={s.id} value={s.id}>{s.name} ({s.ipmi_ip})</option>
          ))}
        </select>
        <button
          className="btn btn-primary"
          onClick={() => {
            setEditingProfile({
              id: 0,
              server_id: serverId!,
              server_name: servers.find(s => s.id === serverId)?.name || '',
              is_default: false,
              name: 'New Profile',
              ranges: [],
              created_at: null,
              updated_at: null,
            } as TempProfile);
            setRanges(DEFAULT_RANGES);
          }}
        >
          + New Profile
        </button>
      </div>

      {loading && <p style={{color: 'var(--text-secondary)'}}>Loading profiles...</p>}

      {editingProfile ? (
        <div className="card">
          <div className="form-group">
            <label>Profile Name</label>
            <input value={editingProfile.name} onChange={e => setEditingProfile({...editingProfile, name: e.target.value})} />
          </div>

          <table>
            <thead>
              <tr><th>Component</th><th>Label</th><th>Min (°C)</th><th>Max (°C)</th><th>Warn Max (°C)</th><th>Crit Max (°C)</th></tr>
            </thead>
            <tbody>
              {ranges.map((r, i) => (
                <tr key={i}>
                  <td><span className="badge">{r.component_type}</span></td>
                  <td><input value={r.component_label || ''} onChange={e => { const nr = [...ranges]; nr[i].component_label = e.target.value; setRanges(nr); }} style={{width: 120}} /></td>
                  <td><input type="number" value={r.temp_min ?? ''} onChange={e => { const nr = [...ranges]; nr[i].temp_min = parseFloat(e.target.value) || null; setRanges(nr); }} style={{width: 70}} /></td>
                  <td><input type="number" value={r.temp_max ?? ''} onChange={e => { const nr = [...ranges]; nr[i].temp_max = parseFloat(e.target.value) || null; setRanges(nr); }} style={{width: 70}} /></td>
                  <td><input type="number" value={r.warning_max ?? ''} onChange={e => { const nr = [...ranges]; nr[i].warning_max = parseFloat(e.target.value) || null; setRanges(nr); }} style={{width: 70}} /></td>
                  <td><input type="number" value={r.critical_max ?? ''} onChange={e => { const nr = [...ranges]; nr[i].critical_max = parseFloat(e.target.value) || null; setRanges(nr); }} style={{width: 70}} /></td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="form-actions">
            <button className="btn" onClick={() => setEditingProfile(null)}>Cancel</button>
            <button className="btn btn-primary" onClick={handleSave}>Save Profile</button>
          </div>
        </div>
      ) : (
        <div className="grid grid-2">
          {profiles.map(p => (
            <div key={p.id} className="card">
              <div style={{display: 'flex', justifyContent: 'space-between', marginBottom: 8}}>
                <strong>{p.name}</strong>
                {p.is_default && <span className="badge badge-green">Default</span>}
              </div>
              <div style={{fontSize: 12, color: 'var(--text-secondary)', marginBottom: 8}}>
                {p.ranges.length} component thresholds
              </div>
              {p.ranges.map((r, i) => (
                <div key={i} style={{display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '4px 0', borderBottom: '1px solid var(--border)'}}>
                  <span>{r.component_type}: {r.component_label || ''}</span>
                  <span className="mono">{r.temp_min ?? '-'}°C — {r.temp_max ?? '-'}°C</span>
                </div>
              ))}
              <div style={{display: 'flex', gap: 4, marginTop: 12}}>
                <button className="btn btn-sm" onClick={() => { setEditingProfile(p); setRanges(p.ranges); }}>Edit</button>
                <button className="btn btn-sm btn-danger" onClick={() => handleDelete(p.id)}>Delete</button>
              </div>
            </div>
          ))}
          {profiles.length === 0 && !loading && (
            <div className="empty-state" style={{gridColumn: '1 / -1'}}>
              <p>No profiles yet — click "+ New Profile" to create one.</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
