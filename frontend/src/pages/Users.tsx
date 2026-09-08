import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../auth';
import { api } from '../api';
import type { User } from '../types';

export default function Users() {
  const { user, hasRole } = useAuth();
  const [users, setUsers] = useState<User[]>([]);
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState({ username: '', email: '', password: '', role: 'operator', propagate_to_idrac: true });
  const [error, setError] = useState('');
  const [propagating, setPropagating] = useState<number | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const fetchUsers = useCallback(async () => {
    try { setUsers(await api.listUsers()); } catch {}
  }, []);

  useEffect(() => { fetchUsers(); }, [fetchUsers]);

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    try {
      await api.createUser(form);
      setShowAdd(false);
      setForm({ username: '', email: '', password: '', role: 'operator', propagate_to_idrac: true });
      fetchUsers();
    } catch (err: any) {
      setError(err.message);
    }
  };

  const handlePropagate = async (id: number) => {
    setPropagating(id);
    setActionMessage(null);
    setActionError(null);
    try {
      const result = await api.propagateUser(id);
      await fetchUsers();
      const failed = Number(result?.failed || 0);
      const success = Number(result?.success || 0);
      const skipped = Number(result?.skipped || 0);
      const detail = Array.isArray(result?.results)
        ? result.results
            .filter((r: any) => r?.error_message)
            .slice(0, 2)
            .map((r: any) => `${r.server_name}: ${r.error_message}`)
            .join(' | ')
        : '';

      if (failed > 0) {
        setActionError(`Propagation completed with ${failed} failed, ${success} successful, ${skipped} skipped.${detail ? ` ${detail}` : ''}`);
      } else {
        setActionMessage(`Propagation completed successfully across ${success} server${success === 1 ? '' : 's'}.${skipped ? ` ${skipped} skipped.` : ''}`);
      }
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to propagate user.');
    } finally {
      setPropagating(null);
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm('Delete this DSM user and remove matching iDRAC accounts where supported?')) return;
    try { await api.deleteUser(id); fetchUsers(); } catch {}
  };

  if (!hasRole('admin')) {
    return <div className="inline-alert danger">Admin access required.</div>;
  }

  return (
    <div>
      <div className="page-toolbar">
        <div>
          <h2 className="page-title">DSM users</h2>
          <p className="page-subtitle">Role-based access and optional account propagation to managed iDRAC endpoints.</p>
        </div>
        <button className="btn btn-primary" onClick={() => setShowAdd(true)}>Add User</button>
      </div>

      <div className="inline-alert warning" style={{ marginBottom: 18 }}>
        User passwords stay masked in the UI. Propagation creates or updates iDRAC users using DSM backend credentials; DSM never displays stored passwords.
      </div>
      {actionMessage && <div className="inline-alert success" style={{ marginBottom: 18 }}>{actionMessage}</div>}
      {actionError && <div className="inline-alert danger" style={{ marginBottom: 18 }}>{actionError}</div>}

      <div className="table-wrap">
        <table>
          <thead><tr><th>Username</th><th>Email</th><th>Roles</th><th>Status</th><th>Theme</th><th>Created</th><th>Actions</th></tr></thead>
          <tbody>
            {users.map(u => (
              <tr key={u.id}>
                <td><strong className="mono">{u.username}</strong></td>
                <td>{u.email || '—'}</td>
                <td>{u.is_superuser && <span className="badge badge-blue" style={{ marginRight: 4 }}>superuser</span>}{u.roles.map(r => <span key={r} className="badge" style={{ marginRight: 4 }}>{r}</span>)}</td>
                <td><span className={`badge ${u.is_active ? 'badge-green' : 'badge-red'}`}>{u.is_active ? 'Active' : 'Disabled'}</span></td>
                <td className="mono">{u.theme}</td>
                <td>{u.created_at ? new Date(u.created_at).toLocaleDateString() : '—'}</td>
                <td>
                  <div className="row-actions">
                    <button className="btn btn-sm" onClick={() => handlePropagate(u.id)} disabled={propagating === u.id}>{propagating === u.id ? 'Propagating…' : 'Propagate'}</button>
                    {u.id !== user?.user_id && <button className="btn btn-sm btn-danger" onClick={() => handleDelete(u.id)}>Delete</button>}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {showAdd && (
        <div className="modal-overlay" onClick={() => setShowAdd(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h2>Add DSM user</h2>
            <form onSubmit={handleAdd}>
              <div className="form-group"><label>Username</label><input value={form.username} onChange={e => setForm({...form, username: e.target.value})} required /></div>
              <div className="form-group"><label>Email</label><input type="email" value={form.email} onChange={e => setForm({...form, email: e.target.value})} /></div>
              <div className="form-group"><label>Password</label><input type="password" value={form.password} onChange={e => setForm({...form, password: e.target.value})} required minLength={8} /></div>
              <div className="form-group"><label>Role</label><select value={form.role} onChange={e => setForm({...form, role: e.target.value})}><option value="admin">Admin</option><option value="operator">Operator</option><option value="viewer">Viewer</option></select></div>
              <label className="checkbox-row"><input type="checkbox" checked={form.propagate_to_idrac} onChange={e => setForm({...form, propagate_to_idrac: e.target.checked})} /> Propagate to all iDRAC servers</label>
              {error && <div className="inline-alert danger" style={{ marginTop: 12 }}>{error}</div>}
              <div className="form-actions"><button type="button" className="btn" onClick={() => setShowAdd(false)}>Cancel</button><button type="submit" className="btn btn-primary">Create User</button></div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
