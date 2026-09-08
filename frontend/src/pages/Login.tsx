import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../auth';
import { THEMES } from '../types';

export default function LoginPage() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      await login(username, password);
      navigate('/');
    } catch (err: any) {
      setError(err.message || 'Login failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-container">
      <div className="login-card">
        <div className="login-logo">DSM</div>
        <h1>Dell Server Manager</h1>
        <p className="login-subtitle">iDRAC infrastructure control plane</p>
        <form onSubmit={handleSubmit}>
          {error && <div className="login-error">{error}</div>}
          <div className="form-group">
            <label>Username</label>
            <input value={username} onChange={e => setUsername(e.target.value)} placeholder="DSM username" required autoFocus />
          </div>
          <div className="form-group">
            <label>Password</label>
            <input type="password" value={password} onChange={e => setPassword(e.target.value)} placeholder="••••••••" required />
          </div>
          <button type="submit" className="btn btn-primary btn-block" disabled={loading}>
            {loading ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
        <div style={{ marginTop: 22, textAlign: 'center', fontSize: 12, color: 'var(--text-secondary)' }}>
          <div style={{ marginBottom: 9 }}>Preview theme</div>
          <div className="theme-selector" style={{ justifyContent: 'center' }}>
            {THEMES.map(t => (
              <button key={t.id} type="button" className="theme-dot" style={{ backgroundColor: t.color }} title={t.label} onClick={() => document.documentElement.setAttribute('data-theme', t.id)} />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
