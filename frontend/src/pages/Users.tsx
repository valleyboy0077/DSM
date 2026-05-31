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

  const fetchUsers = useCallback(async () => {
    try { setUsers(await api.listUsers()); } catch {}
  }, []);

  useEffect(() => { fetchUsers(); }, [fetchUsers]);

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    try {
      const result = await api.createUser(form);
      setShowAdd(false);
      setForm({ username: '', email: '', password: '', role: 'operator', propagate_to_idrac: true });
      fetchUsers();
    } catch (err: any) {
      setError(err.message);
    }
  };

  const handlePropagate = async (id: number) => {
    setPropagating(id);
    try {
      await api.propagateUser(id);
      fetchUsers();
    } catch {}
    setPropagating(null);
  };

  const handleDelete = async (id: number) => {
    if (!confirm('Delete this user and remove from all iDRACs?')) return;
    try {
      await api.deleteUser(id);
      fetchUsers();
    } catch {}
  };

  if (!hasRole('admin')) {
    return <div className="container"><p style={{color: 'var(--red)'}}>Admin access required</p></div>;
  }

  return (
    <div>
      <div className="toolbar">
        <h2 style={{fontSize: 18}}>User Management</h2>
        <button className="btn btn-primary" onClick={() => setShowAdd(true)}>+ Add User</button>
      </div>

      <div className="card" style={{marginBottom: 16, padding: '10px 16px', fontSize: 13, color: 'var(--text-secondary)'}}>
        When a DSM user is added, their account is automatically propagated to all iDRAC servers in inventory with matching privileges (admin→Administrator, operator→Operator, viewer→ReadOnly). Passwords are encrypted at rest.
      </div>

      <table>
        <thead>
          <tr><th>Username</th><th>Email</th><th>Roles</th><th>Status</th><th>Theme</th><th>Created</th><th>Actions</th></tr>
        </thead>
        <tbody>
          {users.map(u => (
            <tr key={u.id}>
              <td className="mono" style={{fontWeight: 600}}>{u.username}</td>
              <td>{u.email || '—'}</td>
              <td>{u.roles.map(r => <span key={r} className="badge" style={{marginRight: 4}}>{r}</span>)}</td>
              <td><span className={`badge ${u.is_active ? 'badge-green' : 'badge-red'}`}>{u.is_active ? 'Active' : 'Disabled'}</span></td>
              <td>{u.theme}</td>
              <td style={{fontSize: 11}}>{u.created_at ? new Date(u.created_at).toLocaleDateString() : '—'}</td>
              <td style={{display: 'flex', gap: 4}}>
                <button className="btn btn-sm" onClick={() => handlePropagate(u.id)} disabled={propagating === u.id}>
                  {propagating === u.id ? '...' : 'Propagate'}
                </button>
                {u.id !== user?.user_id && (
                  <button className="btn btn-sm btn-danger" onClick={() => handleDelete(u.id)}>Delete</button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {showAdd && (
        <div className="modal-overlay" onClick={() => setShowAdd(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h2>Add User</h2>
            <form onSubmit={handleAdd}>
              <div className="form-group">
                <label>Username</label>
                <input value={form.username} onChange={e => setForm({...form, username: e.target.value})} required />
              </div>
              <div className="form-group">
                <label>Email</label>
                <input value={form.email} onChange={e => setForm({...form, email: e.target.value})} />
              </div>
              <div className="form-group">
                <label>Password</label>
                <input type="password" value={form.password} onChange={e => setForm({...form, password: e.target.value})} required minLength={8} />
              </div>
              <div className="form-group">
                <label>Role</label>
                <select value={form.role} onChange={e => setForm({...form, role: e.target.value})}>
                  <option value="admin">Admin</option>
                  <option value="operator">Operator</option>
                  <option value="viewer">Viewer</option>
                </select>
              </div>
              <div className="form-group">
                <label>
                  <input type="checkbox" checked={form.propagate_to_idrac} onChange={e => setForm({...form, propagate_to_idrac: e.target.checked})} style={{marginRight: 6}} />
                  Propagate to all iDRAC servers
                </label>
              </div>
              {error && <div style={{color: 'var(--red)', fontSize: 12, marginBottom: 8}}>{error}</div>}
              <div className="form-actions">
                <button type="button" className="btn" onClick={() => setShowAdd(false)}>Cancel</button>
                <button type="submit" className="btn btn-primary">Create User</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
