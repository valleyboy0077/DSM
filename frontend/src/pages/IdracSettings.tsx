import { useState, useEffect, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import { api } from '../api';

type Tab = 'network' | 'sel' | 'hardware' | 'power' | 'storage' | 'bios' | 'security' | 'firmware' | 'alerts' | 'idrac-users';

const TABS: { id: Tab; label: string; icon: string }[] = [
  { id: 'network', label: 'Network', icon: '🌐' },
  { id: 'sel', label: 'Event Log', icon: '📋' },
  { id: 'hardware', label: 'Hardware', icon: '🖥️' },
  { id: 'power', label: 'Power', icon: '⚡' },
  { id: 'storage', label: 'Storage', icon: '💾' },
  { id: 'bios', label: 'BIOS', icon: '⚙️' },
  { id: 'security', label: 'Security', icon: '🔒' },
  { id: 'firmware', label: 'Firmware', icon: '📦' },
  { id: 'alerts', label: 'Alerts', icon: '🔔' },
  { id: 'idrac-users', label: 'iDRAC Users', icon: '👥' },
];

function JsonViewer({ data }: { data: any }) {
  const [expanded, setExpanded] = useState(true);
  return (
    <div>
      <button className="btn btn-sm" onClick={() => setExpanded(!expanded)} style={{marginBottom: 4}}>
        {expanded ? '▼ Collapse' : '▶ Expand'}
      </button>
      {expanded && (
        <pre style={{
          background: 'var(--bg-primary)', padding: 12, borderRadius: 6,
          fontSize: 11, overflow: 'auto', maxHeight: 600, whiteSpace: 'pre-wrap',
          fontFamily: 'var(--font-mono)',
        }}>
          {JSON.stringify(data, null, 2)}
        </pre>
      )}
    </div>
  );
}

export default function IdracSettings() {
  const { server_id } = useParams();
  const serverId = parseInt(server_id || '0');
  const [activeTab, setActiveTab] = useState<Tab>('hardware');
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [serverName, setServerName] = useState('');

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const server = await api.getServer(serverId);
      setServerName(server.name);
      let result: any;
      switch (activeTab) {
        case 'network': result = await api.getNetwork(serverId); break;
        case 'sel': result = await api.getSEL(serverId); break;
        case 'hardware': result = await api.getHardware(serverId); break;
        case 'power': result = await api.getPower(serverId); break;
        case 'storage': result = await api.getStorage(serverId); break;
        case 'bios': result = await api.getBIOS(serverId); break;
        case 'security': result = await api.getSecurity(serverId); break;
        case 'firmware': result = await api.getFirmware(serverId); break;
        case 'alerts': result = await api.getAlerts(serverId); break;
        case 'idrac-users': result = await api.getIdracUsers(serverId); break;
        default: result = {};
      }
      setData(result);
    } catch (err: any) {
      setData({ error: err.message });
    }
    setLoading(false);
  }, [serverId, activeTab]);

  useEffect(() => { fetchData(); }, [fetchData]);

  return (
    <div>
      <div className="toolbar">
        <h2 style={{fontSize: 18}}>iDRAC Settings — {serverName}</h2>
        <button className="btn btn-sm" onClick={fetchData} disabled={loading}>↻ Refresh</button>
      </div>

      <div className="settings-layout">
        {/* Sidebar */}
        <div className="settings-nav">
          {TABS.map(t => (
            <div
              key={t.id}
              className={`settings-nav-item ${activeTab === t.id ? 'active' : ''}`}
              onClick={() => setActiveTab(t.id)}
            >
              <span style={{marginRight: 6}}>{t.icon}</span> {t.label}
            </div>
          ))}
        </div>

        {/* Content */}
        <div className="card">
          <div className="card-title">{TABS.find(t => t.id === activeTab)?.label}</div>

          {loading && <div style={{padding: 20, textAlign: 'center', color: 'var(--text-secondary)'}}>Loading...</div>}

          {!loading && data && (data as any).error ? (
            <div style={{padding: 20, color: 'var(--red)'}}>
              Error: {(data as any).error}
            </div>
          ) : (
            data && <JsonViewer data={data} />
          )}
        </div>
      </div>
    </div>
  );
}
