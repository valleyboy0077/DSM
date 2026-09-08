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
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  const fetchServers = useCallback(async () => { try { setServers(await api.listServers()); } catch {} }, []);
  const fetchProfiles = useCallback(async () => {
    if (!serverId) { setProfiles([]); setLoading(false); return; }
    try { setLoading(true); setProfiles(await api.listProfiles(serverId)); } catch { setProfiles([]); } finally { setLoading(false); }
  }, [serverId]);

  useEffect(() => { fetchServers(); }, [fetchServers]);
  useEffect(() => { fetchProfiles(); }, [fetchProfiles]);
  useEffect(() => {
    if (servers.length > 0 && !serverId) { setSelectedServerId(servers[0].id); setSearchParams({ server_id: String(servers[0].id) }); }
  }, [servers, serverId, setSearchParams]);

  const handleServerChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const id = parseInt(e.target.value, 10);
    setSelectedServerId(id);
    setSaveMessage(null);
    setSaveError(null);
    setSearchParams({ server_id: String(id) });
  };

  const handleSave = async () => {
    if (!editingProfile || !serverId) return;
    setSaveMessage(null);
    setSaveError(null);

    try {
      const data = { server_id: serverId, name: editingProfile.name, is_default: editingProfile.is_default, ranges };
      const savedProfile = editingProfile.id
        ? await api.updateProfile(editingProfile.id, data)
        : await api.createProfile(serverId, data);

      await fetchProfiles();
      setEditingProfile(null);
      setRanges(DEFAULT_RANGES);

      const rangeCount = Array.isArray(savedProfile?.ranges) ? savedProfile.ranges.length : 0;
      if (rangeCount === 0 && ranges.length > 0) {
        setSaveError('Profile saved, but the server returned 0 ranges. Refreshing the list may show incomplete profile data.');
      } else {
        setSaveMessage(`Profile saved successfully with ${rangeCount} range${rangeCount === 1 ? '' : 's'}.`);
      }
    } catch (err) {
      console.error('Failed to save profile:', err);
      setSaveError(err instanceof Error ? err.message : 'Failed to save profile.');
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm('Delete this temperature profile?')) return;
    try { await api.deleteProfile(id); fetchProfiles(); } catch (err) { console.error('Failed to delete profile:', err); }
  };

  if (servers.length === 0) {
    return <div className="empty-state"><strong>No servers available</strong><p>Add a server to Inventory first, then manage temperature profiles.</p></div>;
  }

  const selectedServer = servers.find(s => s.id === serverId);

  return (
    <div>
      <div className="page-toolbar">
        <div>
          <h2 className="page-title">Thermal profiles</h2>
          <p className="page-subtitle">Threshold ranges used by cooling policy and alert decisions.</p>
        </div>
        <div className="inline-form-row">
          <label style={{ color: 'var(--text-secondary)', fontSize: 13 }}>Server</label>
          <select value={serverId || ''} onChange={handleServerChange} style={{ minWidth: 240 }}>
            {servers.map(s => <option key={s.id} value={s.id}>{s.name} ({s.ipmi_ip})</option>)}
          </select>
          <button className="btn btn-primary" onClick={() => {
            setSaveMessage(null);
            setSaveError(null);
            setEditingProfile({ id: 0, server_id: serverId!, server_name: selectedServer?.name || '', is_default: false, name: 'New Profile', ranges: [], created_at: null, updated_at: null } as TempProfile);
            setRanges(DEFAULT_RANGES);
          }}>New Profile</button>
        </div>
      </div>

      {selectedServer && <div className="inline-alert" style={{ marginBottom: 18 }}>Editing profiles for <strong>{selectedServer.name}</strong> at <span className="mono">{selectedServer.ipmi_ip}</span>.</div>}
      {saveMessage && <div className="inline-alert success" style={{ marginBottom: 18 }}>{saveMessage}</div>}
      {saveError && <div className="inline-alert danger" style={{ marginBottom: 18 }}>{saveError}</div>}
      {loading && <div className="skeleton" />}

      {editingProfile ? (
        <div className="card">
          <div className="page-toolbar" style={{ marginBottom: 12 }}>
            <div><h2 className="page-title">Profile editor</h2><p className="page-subtitle">Set preferred, warning, and critical temperature ranges.</p></div>
            <span className="badge badge-blue">{ranges.length} components</span>
          </div>
          <div className="form-group"><label>Profile Name</label><input value={editingProfile.name} onChange={e => setEditingProfile({...editingProfile, name: e.target.value})} /></div>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Component</th><th>Label</th><th>Min °C</th><th>Max °C</th><th>Warn Max °C</th><th>Crit Max °C</th></tr></thead>
              <tbody>
                {ranges.map((r, i) => (
                  <tr key={i}>
                    <td><span className="badge">{r.component_type}</span></td>
                    <td><input value={r.component_label || ''} onChange={e => { const nr = [...ranges]; nr[i].component_label = e.target.value; setRanges(nr); }} /></td>
                    <td><input type="number" value={r.temp_min ?? ''} onChange={e => { const nr = [...ranges]; nr[i].temp_min = parseFloat(e.target.value) || null; setRanges(nr); }} /></td>
                    <td><input type="number" value={r.temp_max ?? ''} onChange={e => { const nr = [...ranges]; nr[i].temp_max = parseFloat(e.target.value) || null; setRanges(nr); }} /></td>
                    <td><input type="number" value={r.warning_max ?? ''} onChange={e => { const nr = [...ranges]; nr[i].warning_max = parseFloat(e.target.value) || null; setRanges(nr); }} /></td>
                    <td><input type="number" value={r.critical_max ?? ''} onChange={e => { const nr = [...ranges]; nr[i].critical_max = parseFloat(e.target.value) || null; setRanges(nr); }} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="form-actions"><button className="btn" onClick={() => setEditingProfile(null)}>Cancel</button><button className="btn btn-primary" onClick={handleSave}>Save Profile</button></div>
        </div>
      ) : !loading && (
        <div className="grid grid-2">
          {profiles.map(p => (
            <article key={p.id} className="card server-card online">
              <div className="server-header"><div><div className="server-name">{p.name}</div><div className="server-meta">{p.ranges.length} component thresholds</div></div>{p.is_default && <span className="badge badge-green">Default</span>}</div>
              <div className="detail-grid">
                {p.ranges.slice(0, 4).map((r, i) => <div className="detail-card" key={i}><span className="meta-label">{r.component_type} · {r.component_label || 'sensor'}</span><span className="meta-value mono">{r.temp_min ?? '-'}°C — {r.temp_max ?? '-'}°C</span></div>)}
              </div>
              <div className="card-actions"><button className="btn btn-sm" onClick={() => { setEditingProfile(p); setRanges(p.ranges); }}>Edit</button><button className="btn btn-sm btn-danger" onClick={() => handleDelete(p.id)}>Delete</button></div>
            </article>
          ))}
          {profiles.length === 0 && <div className="empty-state" style={{ gridColumn: '1 / -1' }}><strong>No profiles yet</strong><p>Create a profile to define thermal thresholds for this server.</p></div>}
        </div>
      )}
    </div>
  );
}
