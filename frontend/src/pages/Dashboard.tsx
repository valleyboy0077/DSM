import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';
import type { Server } from '../types';

export default function Dashboard() {
  const [servers, setServers] = useState<Server[]>([]);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  const fetch = useCallback(async () => {
    try {
      const data = await api.listServers();
      setServers(data);
    } catch {}
    setLoading(false);
  }, []);

  useEffect(() => { fetch(); }, [fetch]);

  const online = servers.filter(s => s.status === 'online').length;
  const degraded = servers.filter(s => s.status === 'degraded').length;
  const offline = servers.filter(s => s.status === 'offline').length;

  return (
    <div>
      <div className="toolbar">
        <h2 style={{fontSize: 18}}>Dashboard</h2>
      </div>

      {/* Stats */}
      <div className="grid grid-4" style={{marginBottom: 24}}>
        <div className="card">
          <div className="card-title">Total Servers</div>
          <div className="card-value">{servers.length}</div>
        </div>
        <div className="card">
          <div className="card-title">Online</div>
          <div className="card-value" style={{color: 'var(--green)'}}>{online}</div>
        </div>
        <div className="card">
          <div className="card-title">Degraded</div>
          <div className="card-value" style={{color: 'var(--yellow)'}}>{degraded}</div>
        </div>
        <div className="card">
          <div className="card-title">Offline</div>
          <div className="card-value" style={{color: 'var(--red)'}}>{offline}</div>
        </div>
      </div>

      {/* Server list */}
      <div className="section-title">Servers</div>
      {loading ? <p style={{color: 'var(--text-secondary)'}}>Loading...</p> : (
        <div className="grid grid-3">
          {servers.map(s => (
            <div key={s.id} className="card server-card" onClick={() => navigate(`/server/${s.id}`)}>
              <div className="server-header">
                <span className="server-name">{s.name}</span>
                <span className={`status-dot ${s.status}`}></span>
              </div>
              <div className="server-meta">
                {s.model || 'Unknown model'} {s.serial ? `· ST:${s.serial}` : ''}
              </div>
              <div className="server-meta" style={{marginTop: 4}}>
                {s.ipmi_ip} · {s.drac_version || 'unknown'}
              </div>
              <div className="server-meta" style={{marginTop: 4}}>
                Last seen: {s.last_seen ? new Date(s.last_seen).toLocaleTimeString() : 'never'}
              </div>
            </div>
          ))}
          {servers.length === 0 && (
            <div className="empty-state" style={{gridColumn: '1 / -1'}}>
              <p>No servers added yet</p>
              <button className="btn btn-primary" onClick={() => navigate('/inventory')} style={{marginTop: 12}}>
                Add Server
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
