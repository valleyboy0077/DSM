import { useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import type { Server, ServerGroup } from '../types';

export default function Inventory() {
  const [servers, setServers] = useState<Server[]>([]);
  const [groups, setGroups] = useState<ServerGroup[]>([]);
  const [showAdd, setShowAdd] = useState(false);
  const [showGroup, setShowGroup] = useState(false);
  const [form, setForm] = useState({ name: '', ipmi_ip: '', ipmi_user: 'root', ipmi_password: '' });
  const [groupForm, setGroupForm] = useState({ name: '', description: '', color: '#4a90d9' });
  const [error, setError] = useState('');

  const fetchAll = useCallback(async () => {
    try {
      const [s, g] = await Promise.all([api.listServers(), api.listGroups()]);
      setServers(s);
      setGroups(g);
    } catch {}
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    try {
      await api.addServer(form);
      setForm({ name: '', ipmi_ip: '', ipmi_user: 'root', ipmi_password: '' });
      setShowAdd(false);
      fetchAll();
    } catch (err: any) {
      setError(err.message);
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm('Remove this server?')) return;
    try {
      await api.deleteServer(id);
      fetchAll();
    } catch {}
  };

  const handleAddGroup = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api.createGroup(groupForm);
      setGroupForm({ name: '', description: '', color: '#4a90d9' });
      setShowGroup(false);
      fetchAll();
    } catch {}
  };

  return (
    <div>
      <div className="toolbar">
        <h2 style={{fontSize: 18}}>Inventory</h2>
        <div style={{display: 'flex', gap: 8}}>
          <button className="btn" onClick={() => setShowGroup(true)}>+ Group</button>
          <button className="btn btn-primary" onClick={() => setShowAdd(true)}>+ Add Server</button>
        </div>
      </div>

      {/* Groups */}
      {groups.length > 0 && (
        <div style={{marginBottom: 24}}>
          <div className="section-title">Groups</div>
          <div style={{display: 'flex', gap: 8, flexWrap: 'wrap'}}>
            {groups.map(g => (
              <div key={g.id} className="badge" style={{fontSize: 13, padding: '6px 14px'}}>
                <span style={{display: 'inline-block', width: 8, height: 8, borderRadius: '50%', backgroundColor: g.color, marginRight: 6}}></span>
                {g.name} ({g.server_count})
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Servers */}
      <div className="section-title">Servers ({servers.length})</div>
      {servers.length === 0 ? (
        <div className="empty-state"><p>No servers yet</p></div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Name</th><th>iDRAC IP</th><th>Model</th><th>Serial</th><th>Version</th><th>Status</th><th>Last Seen</th><th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {servers.map(s => (
              <tr key={s.id}>
                <td className="mono" style={{fontWeight: 600}}>{s.name}</td>
                <td className="mono">{s.ipmi_ip}</td>
                <td>{s.model || '—'}</td>
                <td>{s.serial || '—'}</td>
                <td>{s.drac_version || '—'}</td>
                <td><span className={`badge ${s.status === 'online' ? 'badge-green' : s.status === 'degraded' ? 'badge-yellow' : 'badge-red'}`}>{s.status}</span></td>
                <td style={{fontSize: 11}}>{s.last_seen ? new Date(s.last_seen).toLocaleString() : '—'}</td>
                <td>
                  <div style={{display: 'flex', gap: 4}}>
                    <Link to={`/temp-profiles?server_id=${s.id}`} className="btn btn-sm">Temps</Link>
                    <Link to={`/server/${s.id}/fan-control`} className="btn btn-sm">Fans</Link>
                    <Link to={`/server/${s.id}/settings`} className="btn btn-sm">Settings</Link>
                    <button className="btn btn-sm btn-danger" onClick={() => handleDelete(s.id)}>Remove</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* Add Server Modal */}
      {showAdd && (
        <div className="modal-overlay" onClick={() => setShowAdd(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h2>Add Server</h2>
            <form onSubmit={handleAdd}>
              <div className="form-group">
                <label>Server Name</label>
                <input value={form.name} onChange={e => setForm({...form, name: e.target.value})} placeholder="R730xd-Prod" required />
              </div>
              <div className="form-group">
                <label>iDRAC IP</label>
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
                <button type="button" className="btn" onClick={() => setShowAdd(false)}>Cancel</button>
                <button type="submit" className="btn btn-primary">Add Server</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Add Group Modal */}
      {showGroup && (
        <div className="modal-overlay" onClick={() => setShowGroup(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h2>Add Group</h2>
            <form onSubmit={handleAddGroup}>
              <div className="form-group">
                <label>Group Name</label>
                <input value={groupForm.name} onChange={e => setGroupForm({...groupForm, name: e.target.value})} required />
              </div>
              <div className="form-group">
                <label>Description</label>
                <input value={groupForm.description} onChange={e => setGroupForm({...groupForm, description: e.target.value})} />
              </div>
              <div className="form-group">
                <label>Color</label>
                <input type="color" value={groupForm.color} onChange={e => setGroupForm({...groupForm, color: e.target.value})} />
              </div>
              <div className="form-actions">
                <button type="button" className="btn" onClick={() => setShowGroup(false)}>Cancel</button>
                <button type="submit" className="btn btn-primary">Create</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
