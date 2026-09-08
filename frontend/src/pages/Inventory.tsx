import { useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import type { Server, ServerGroup } from '../types';
import { describeController, describeControllerSource } from '../serverMetadata';

const statusBadge = (status: string) => status === 'online' ? 'badge-green' : status === 'degraded' ? 'badge-yellow' : 'badge-red';

export default function Inventory() {
  const [servers, setServers] = useState<Server[]>([]);
  const [groups, setGroups] = useState<ServerGroup[]>([]);
  const [showAdd, setShowAdd] = useState(false);
  const [showGroup, setShowGroup] = useState(false);
  const [form, setForm] = useState({ name: '', ipmi_ip: '', ipmi_user: 'root', ipmi_password: '', drac_version: 'unknown' });
  const [groupForm, setGroupForm] = useState({ name: '', description: '', color: '#38bdf8' });
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');

  const fetchAll = useCallback(async () => {
    try {
      const [s, g] = await Promise.all([api.listServers(), api.listGroups()]);
      setServers(s);
      setGroups(g);
    } catch {}
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const filtered = servers.filter(s => [s.name, s.ipmi_ip, s.model, s.serial, s.status, s.drac_version, s.controller_profile, s.controller_label].join(' ').toLowerCase().includes(query.toLowerCase()));

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    try {
      await api.addServer(form);
      setForm({ name: '', ipmi_ip: '', ipmi_user: 'root', ipmi_password: '', drac_version: 'unknown' });
      setShowAdd(false);
      fetchAll();
    } catch (err: any) {
      setError(err.message);
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm('Remove this server from DSM inventory?')) return;
    try { await api.deleteServer(id); fetchAll(); } catch {}
  };

  const handleAddGroup = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api.createGroup(groupForm);
      setGroupForm({ name: '', description: '', color: '#38bdf8' });
      setShowGroup(false);
      fetchAll();
    } catch {}
  };

  return (
    <div>
      <div className="page-toolbar">
        <div>
          <h2 className="page-title">Server inventory</h2>
          <p className="page-subtitle">Dell servers, iDRAC addresses, platform metadata, and operational links.</p>
        </div>
        <div className="row-actions">
          <button className="btn" onClick={() => setShowGroup(true)}>Create Group</button>
          <button className="btn btn-primary" onClick={() => setShowAdd(true)}>Add Server</button>
        </div>
      </div>

      {groups.length > 0 && (
        <div className="card" style={{ marginBottom: 18 }}>
          <div className="card-title">Groups</div>
          <div className="row-actions">
            {groups.map(g => (
              <span key={g.id} className="badge" style={{ fontSize: 12, padding: '7px 12px' }}>
                <span style={{ width: 8, height: 8, borderRadius: '50%', backgroundColor: g.color, display: 'inline-block' }} />
                {g.name} · {g.server_count}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="card" style={{ marginBottom: 18 }}>
        <div className="inline-form-row">
          <div style={{ flex: 1, minWidth: 260 }}>
            <input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search by name, IP, serial, model, status…" />
          </div>
          <span className="badge badge-blue">{filtered.length} shown</span>
          <span className="badge">{servers.length} total</span>
        </div>
      </div>

      {servers.length === 0 ? (
        <div className="empty-state"><strong>No servers yet</strong><p>Add the first iDRAC endpoint to start polling hardware telemetry.</p></div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th><th>iDRAC IP</th><th>Model</th><th>Service Tag</th><th>Controller</th><th>Status</th><th>Last Seen</th><th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(s => (
                <tr key={s.id}>
                  <td><strong>{s.name}</strong></td>
                  <td className="mono">{s.ipmi_ip}</td>
                  <td>{s.model || '—'}</td>
                  <td className="mono">{s.serial || '—'}</td>
                  <td className="mono" title={describeControllerSource(s)}>{describeController(s)}</td>
                  <td><span className={`badge ${statusBadge(s.status)}`}><span className={`status-dot ${s.status}`} />{s.status}</span></td>
                  <td>{s.last_seen ? new Date(s.last_seen).toLocaleString() : '—'}</td>
                  <td>
                    <div className="row-actions">
                      <Link to={`/temp-profiles?server_id=${s.id}`} className="btn btn-sm">Temps</Link>
                      <Link to={`/server/${s.id}/fan-control`} className="btn btn-sm">Fans</Link>
                      <Link to={`/server/${s.id}/settings`} className="btn btn-sm btn-primary">Settings</Link>
                      <button className="btn btn-sm btn-danger" onClick={() => handleDelete(s.id)}>Remove</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showAdd && (
        <div className="modal-overlay" onClick={() => setShowAdd(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h2>Add server</h2>
            <div className="inline-alert warning" style={{ marginBottom: 16 }}>Credentials are submitted to DSM and stored encrypted. They are never displayed back in the UI.</div>
            <form onSubmit={handleAdd}>
              <div className="form-group"><label>Server name</label><input value={form.name} onChange={e => setForm({...form, name: e.target.value})} placeholder="R730xd" required /></div>
              <div className="form-group"><label>iDRAC IP address</label><input value={form.ipmi_ip} onChange={e => setForm({...form, ipmi_ip: e.target.value})} placeholder="10.x.x.x" required /></div>
              <div className="form-group"><label>Username</label><input value={form.ipmi_user} onChange={e => setForm({...form, ipmi_user: e.target.value})} required /></div>
              <div className="form-group"><label>Password</label><input type="password" value={form.ipmi_password} onChange={e => setForm({...form, ipmi_password: e.target.value})} required /></div>
              <div className="form-group">
                <label>DSM control profile</label>
                <select value={form.drac_version} onChange={e => setForm({...form, drac_version: e.target.value})}>
                  <option value="unknown">Auto-detect / unknown</option>
                  <option value="idrac7">Legacy Dell profile</option>
                  <option value="idrac8">Redfish-first profile</option>
                </select>
                <small style={{ color: 'var(--text-secondary)' }}>This setting controls DSM compatibility behavior. The displayed controller generation is inferred separately from detected hardware metadata.</small>
              </div>
              {error && <div className="inline-alert danger" style={{ marginBottom: 10 }}>{error}</div>}
              <div className="form-actions"><button type="button" className="btn" onClick={() => setShowAdd(false)}>Cancel</button><button type="submit" className="btn btn-primary">Add Server</button></div>
            </form>
          </div>
        </div>
      )}

      {showGroup && (
        <div className="modal-overlay" onClick={() => setShowGroup(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h2>Create group</h2>
            <form onSubmit={handleAddGroup}>
              <div className="form-group"><label>Group name</label><input value={groupForm.name} onChange={e => setGroupForm({...groupForm, name: e.target.value})} required /></div>
              <div className="form-group"><label>Description</label><input value={groupForm.description} onChange={e => setGroupForm({...groupForm, description: e.target.value})} /></div>
              <div className="form-group"><label>Color</label><input type="color" value={groupForm.color} onChange={e => setGroupForm({...groupForm, color: e.target.value})} /></div>
              <div className="form-actions"><button type="button" className="btn" onClick={() => setShowGroup(false)}>Cancel</button><button type="submit" className="btn btn-primary">Create</button></div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
