import { useState, useEffect, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import { api } from '../api';

type Tab = 'firmware' | 'hardware' | 'storage' | 'power' | 'network' | 'bios' | 'security' | 'alerts' | 'sel' | 'idrac-users';
type TabCache = Partial<Record<Tab, any>>;
type TabTimestamps = Partial<Record<Tab, string>>;

type HardwareSection = {
  title: string;
  subtitle: string;
  rows: any[];
  preferredKeys: string[];
};

const TABS: { id: Tab; label: string; hint: string }[] = [
  { id: 'firmware', label: 'Firmware', hint: 'versions' },
  { id: 'hardware', label: 'Hardware', hint: 'system' },
  { id: 'storage', label: 'Storage', hint: 'drives' },
  { id: 'power', label: 'Power', hint: 'PSU' },
  { id: 'network', label: 'Network', hint: 'NICs' },
  { id: 'bios', label: 'BIOS', hint: 'settings' },
  { id: 'security', label: 'Security', hint: 'state' },
  { id: 'alerts', label: 'Alerts', hint: 'rules' },
  { id: 'sel', label: 'Event Log', hint: 'SEL' },
  { id: 'idrac-users', label: 'iDRAC Users', hint: 'accounts' },
];

function entries(data: any): [string, any][] {
  if (!data || typeof data !== 'object') return [];
  if (Array.isArray(data)) return data.map((value, index) => [`#${index + 1}`, value]);
  return Object.entries(data).filter(([, value]) => typeof value !== 'object' || value === null).slice(0, 12);
}

function objectList(data: any): any[] {
  if (Array.isArray(data)) return data;
  if (!data || typeof data !== 'object') return [];
  for (const value of Object.values(data)) {
    if (Array.isArray(value)) return value;
  }
  return [];
}

function titleCase(input: string): string {
  return input
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\b\w/g, char => char.toUpperCase());
}

function isEmptyValue(value: any): boolean {
  if (value === null || value === undefined || value === '') return true;
  if (typeof value === 'string' && ['unknown', 'n/a', 'na', 'none', 'not applicable'].includes(value.trim().toLowerCase())) return true;
  return false;
}

function isTruthyHardwareValue(value: any): boolean {
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === 'object' && value !== null) return Object.keys(value).length > 0;
  return !isEmptyValue(value);
}

function formatBytes(value: number): string {
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  let amount = value;
  let unitIndex = 0;
  while (amount >= 1024 && unitIndex < units.length - 1) {
    amount /= 1024;
    unitIndex += 1;
  }
  return `${amount >= 100 ? amount.toFixed(0) : amount >= 10 ? amount.toFixed(1) : amount.toFixed(2)} ${units[unitIndex]}`;
}

function formatMegabytes(value: any): string {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) return '—';
  if (numeric >= 1024 * 1024) return `${(numeric / (1024 * 1024)).toFixed(2)} TB`;
  if (numeric >= 1024) return `${(numeric / 1024).toFixed(numeric >= 16384 ? 0 : 1)} GB`;
  return `${numeric.toFixed(0)} MB`;
}

function decodeEnum(value: any, key = ''): string | null {
  const normalized = String(value ?? '').trim();
  if (!normalized) return null;
  const lowerKey = key.toLowerCase();

  if (lowerKey === 'mediatype') {
    const mapping: Record<string, string> = {
      '0': 'HDD',
      '1': 'SSD',
      '2': 'Tape',
      '3': 'Optical',
      '4': 'Flash',
    };
    return mapping[normalized] ?? `Type ${normalized}`;
  }

  if (lowerKey === 'raidstatus') {
    const mapping: Record<string, string> = {
      '0': 'Unknown',
      '1': 'Ready',
      '2': 'Online',
      '3': 'Foreign',
      '4': 'Offline',
      '5': 'Blocked',
      '6': 'Failed',
      '7': 'Degraded',
      '8': 'Non-RAID',
    };
    return mapping[normalized] ?? `Code ${normalized}`;
  }

  if (lowerKey === 'autonegotiation') {
    const mapping: Record<string, string> = {
      '0': 'Off',
      '1': 'On',
      '2': 'Unknown',
      '3': 'Auto',
    };
    return mapping[normalized] ?? `Code ${normalized}`;
  }

  if (lowerKey === 'linkstatus') {
    const mapping: Record<string, string> = {
      '0': 'Down',
      '1': 'Up',
      '2': 'Unknown',
      '3': 'Disabled',
    };
    return mapping[normalized] ?? `Code ${normalized}`;
  }

  if (/(status|health)$/i.test(lowerKey)) {
    const mapping: Record<string, string> = {
      '0': 'Unknown',
      '1': 'OK',
      '2': 'Degraded',
      '3': 'Critical',
      '4': 'Non-Recoverable',
    };
    return mapping[normalized] ?? null;
  }

  return null;
}

function formatValue(value: any, key = ''): string {
  if (value === null || value === undefined || value === '') return '—';
  const decoded = decodeEnum(value, key);
  if (decoded) return decoded;
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value === 'number') {
    const lowerKey = key.toLowerCase();
    if (lowerKey.includes('sizeinbytes') || lowerKey.endsWith('_bytes')) return formatBytes(value);
    if (lowerKey.includes('memory_total_mb') || lowerKey === 'size' || lowerKey.endsWith('sizemb') || lowerKey === 'cachesizeinmb') return formatMegabytes(value);
    if (/(clockspeed|linkspeed|currentspeed)/i.test(lowerKey)) return `${value.toLocaleString()} MHz`;
    return Number.isInteger(value) ? value.toLocaleString() : value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }
  if (Array.isArray(value)) return value.length ? `${value.length} items` : '—';
  if (typeof value === 'object') return '—';
  const raw = String(value).trim();
  if (!raw) return '—';
  if (/^(size|memory_total_mb|.*sizemb|cachesizeinmb)$/i.test(key) && /^\d+(\.\d+)?$/.test(raw)) return formatMegabytes(raw);
  if (/(sizeinbytes|_bytes)$/i.test(key) && /^\d+(\.\d+)?$/.test(raw)) return formatBytes(Number(raw));
  if (/(clockspeed|linkspeed|currentspeed)/i.test(key) && /^\d+(\.\d+)?$/.test(raw)) return `${Number(raw).toLocaleString()} MHz`;
  return raw;
}

function healthTone(value: any): 'green' | 'yellow' | 'red' | 'blue' {
  const normalized = String(value ?? '').toLowerCase();
  if (!normalized || normalized === 'unknown') return 'blue';
  if (/(ok|good|online|up|ready|enabled|on)/.test(normalized)) return 'green';
  if (/(warning|noncritical|degraded)/.test(normalized)) return 'yellow';
  if (/(critical|error|failed|offline|down|absent)/.test(normalized)) return 'red';
  return 'blue';
}

function StatusBadge({ value, valueKey = '' }: { value: any; valueKey?: string }) {
  const text = formatValue(value, valueKey);
  if (text === '—') return <span className="meta-value">—</span>;
  return <span className={`badge badge-${healthTone(text)}`}>{text}</span>;
}

function getColumnLabel(key: string): string {
  const labels: Record<string, string> = {
    fqdd: 'FQDD',
    macaddress: 'MAC Address',
    currentmacaddress: 'Current MAC',
    permanentmacaddress: 'Permanent MAC',
    ipaddress: 'IP Address',
    primarystatus: 'Health',
    rollupstatus: 'Rollup',
    productname: 'Product',
    devicedescription: 'Component',
    bios_version: 'BIOS',
    idrac_firmware: 'iDRAC Firmware',
    cpu_count: 'CPUs',
    nic_count: 'NICs',
    disk_count: 'Disks',
    system_health: 'System Health',
    cpu_health: 'CPU Health',
    memory_health: 'Memory Health',
    storage_health: 'Storage Health',
    fan_health: 'Fan Health',
    power_health: 'Power Health',
    service_tag: 'Service Tag',
    power_state: 'Power State',
    sizeinbytes: 'Capacity',
    mediasize: 'Capacity',
    raidstatus: 'RAID Status',
    serialnumber: 'Serial Number',
    partnumber: 'Part Number',
    currentlinkspeed: 'Link Speed',
    currentspeed: 'Speed',
    maxclockspeed: 'Max Clock',
    currentclockspeed: 'Current Clock',
    numberofenabledcores: 'Cores',
    numberofthreads: 'Threads',
    cachesizeinmb: 'Cache',
    controllerfirmwareversion: 'Firmware',
    model: 'Model',
  };
  return labels[key.toLowerCase()] ?? titleCase(key);
}

function chooseColumns(rows: any[], preferred: string[]): string[] {
  const available = new Set(rows.flatMap(row => Object.keys(row).filter(key => isTruthyHardwareValue(row[key]) && typeof row[key] !== 'object')));
  const ordered = preferred.filter(key => available.has(key));
  const fallback = Array.from(available)
    .filter(key => !ordered.includes(key) && !/(instanceid|lastupdated|id|url|messageid|odata|__)/i.test(key))
    .slice(0, Math.max(0, 7 - ordered.length));
  return [...ordered, ...fallback].slice(0, 7);
}

function keyCellClass(key: string): string {
  return /ip|mac|id|version|serial|name|fqdd|model|part|service/i.test(key) ? 'mono' : '';
}

function formatCell(row: any, key: string) {
  const lowerKey = key.toLowerCase();
  if (/(status|health|state)$/i.test(lowerKey)) return <StatusBadge value={row[key]} valueKey={key} />;
  return formatValue(row[key], key);
}

function inferIdracAccessValue(user: any, field: 'lan_privilege' | 'serial_privilege' | 'access'): string {
  const current = String(user?.[field] ?? '').trim();
  if (current) return current;

  const role = String(user?.role_name || user?.role || '').trim();
  if (!role) return '—';

  const normalized = role.toLowerCase();
  if (normalized.includes('admin')) return 'Administrator';
  if (normalized.includes('operator')) return 'Operator';
  if (normalized.includes('readonly') || normalized.includes('read only')) return 'User';

  return role;
}

function IdracUsersTable({ data }: { data: any }) {
  const rows = (Array.isArray(data) ? data : [])
    .filter(row => row && typeof row === 'object')
    .filter(row => String(row.username ?? '').trim().length > 0);

  if (!rows.length) {
    return <div className="empty-state"><strong>No iDRAC users found</strong><p>This server did not return any populated account slots.</p></div>;
  }

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Slot</th>
            <th>Username</th>
            <th>Role</th>
            <th>LAN Access</th>
            <th>Serial Access</th>
            <th>iDRAC Access</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, idx) => {
            const role = String(row.role_name || row.role || '').trim() || '—';
            return (
              <tr key={`${row.id || idx}-${row.username}`}>
                <td className="mono">{formatValue(row.id, 'id')}</td>
                <td className="mono"><strong>{formatValue(row.username, 'username')}</strong></td>
                <td>{role}</td>
                <td>{inferIdracAccessValue(row, 'lan_privilege')}</td>
                <td>{inferIdracAccessValue(row, 'serial_privilege')}</td>
                <td>{inferIdracAccessValue(row, 'access')}</td>
                <td><StatusBadge value={row.enabled ? 'Enabled' : 'Disabled'} valueKey="enabled" /></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function JsonViewer({ data }: { data: any }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="json-panel">
      <button className="btn btn-sm" onClick={() => setExpanded(!expanded)}>{expanded ? 'Hide raw response' : 'Show raw response'}</button>
      {expanded && <pre>{JSON.stringify(data, null, 2)}</pre>}
    </div>
  );
}

function SummaryGrid({ data }: { data: any }) {
  const flat = entries(data).filter(([key]) => !['system', 'processors', 'memory_modules', 'storage_controllers', 'disks', 'ethernet_interfaces', 'power_supplies'].includes(key));
  if (!flat.length) return <div className="empty-state"><strong>No structured summary available</strong><p>Use the raw response section below for full details.</p></div>;
  return (
    <div className="detail-grid">
      {flat.map(([key, value]) => (
        <div className="detail-card" key={key}>
          <span className="meta-label">{getColumnLabel(key)}</span>
          {/(status|health|state)$/i.test(key) ? <StatusBadge value={value} valueKey={key} /> : <span className={`meta-value ${keyCellClass(key)}`}>{formatValue(value, key)}</span>}
        </div>
      ))}
    </div>
  );
}

function ListTable({ data }: { data: any }) {
  const rows = objectList(data).filter(row => row && typeof row === 'object');
  if (!rows.length) return null;
  const keys = Array.from(new Set(rows.flatMap(row => Object.keys(row).filter(key => typeof row[key] !== 'object')))).slice(0, 7);
  if (!keys.length) return null;
  return (
    <div className="table-wrap" style={{ marginTop: 16 }}>
      <table>
        <thead><tr>{keys.map(key => <th key={key}>{getColumnLabel(key)}</th>)}</tr></thead>
        <tbody>
          {rows.slice(0, 50).map((row, idx) => (
            <tr key={idx}>{keys.map(key => <td key={key} className={keyCellClass(key)}>{formatCell(row, key)}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function HardwareOverview({ data }: { data: any }) {
  const healthPills = [
    { label: 'Power', value: data?.power_state },
    { label: 'System', value: data?.system_health },
    { label: 'CPU', value: data?.cpu_health },
    { label: 'Memory', value: data?.memory_health },
    { label: 'Storage', value: data?.storage_health },
    { label: 'Power Supplies', value: data?.power_health },
  ].filter(item => isTruthyHardwareValue(item.value));

  const inventory = [
    { label: 'Platform', value: data?.model },
    { label: 'Service Tag', value: data?.service_tag },
    { label: 'BIOS', value: data?.bios_version },
    { label: 'iDRAC Firmware', value: data?.idrac_firmware },
    { label: 'CPUs', value: data?.cpu_count },
    { label: 'Memory Installed', value: data?.memory_total_mb, formatter: formatMegabytes },
    { label: 'DIMMs', value: data?.memory_module_count },
    { label: 'Controllers', value: data?.storage_controller_count },
    { label: 'Disks', value: data?.disk_count },
    { label: 'NICs', value: data?.nic_count },
    { label: 'PSUs', value: data?.power_supply_count },
  ].filter(item => isTruthyHardwareValue(item.value));

  return (
    <div className="hardware-hero">
      <div className="hardware-hero-copy">
        <div className="card-title">Hardware overview</div>
        <h3>{formatValue(data?.model)} inventory</h3>
        <p>
          This view is optimized for humans first. DSM is using <strong>{formatValue(data?.protocol)}</strong>
          {isTruthyHardwareValue(data?.source) ? ` (${formatValue(data?.source)})` : ''} for collection, then presenting the most important health and inventory signals up front.
        </p>
        <div className="hardware-pill-row">
          {healthPills.map(item => (
            <span key={item.label} className={`badge badge-${healthTone(item.value)}`}>
              <strong>{item.label}:</strong> {formatValue(item.value)}
            </span>
          ))}
        </div>
      </div>
      <div className="hardware-kpi-grid">
        {inventory.map(item => (
          <div className="hardware-kpi-card" key={item.label}>
            <span className="meta-label">{item.label}</span>
            <span className={`hardware-kpi-value ${/platform|tag|bios|firmware/i.test(item.label) ? 'mono' : ''}`}>
              {item.formatter ? item.formatter(item.value) : formatValue(item.value, item.label)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function SectionTable({ title, subtitle, rows, preferredKeys }: HardwareSection) {
  const filtered = rows.filter(row => row && typeof row === 'object');
  if (!filtered.length) return null;
  const keys = chooseColumns(filtered, preferredKeys);
  if (!keys.length) return null;
  return (
    <div className="hardware-section-card">
      <div className="hardware-section-header">
        <div>
          <div className="card-title">{title}</div>
          <p>{subtitle}</p>
        </div>
        <div className="hardware-section-meta">{filtered.length} detected</div>
      </div>
      <div className="table-wrap" style={{ marginTop: 12 }}>
        <table>
          <thead><tr>{keys.map(key => <th key={key}>{getColumnLabel(key)}</th>)}</tr></thead>
          <tbody>
            {filtered.slice(0, 50).map((row, idx) => (
              <tr key={idx}>{keys.map(key => <td key={key} className={keyCellClass(key)}>{formatCell(row, key)}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function TabContent({ tab, data }: { tab: Tab; data: any }) {
  const list = objectList(data);
  const hardwareSections: HardwareSection[] = [
    {
      title: 'Processors',
      subtitle: 'Installed CPU packages, model names, clocks, and health.',
      rows: Array.isArray(data?.processors) ? data.processors : [],
      preferredKeys: ['DeviceDescription', 'Model', 'Manufacturer', 'NumberOfEnabledCores', 'CurrentClockSpeed', 'MaxClockSpeed', 'PrimaryStatus'],
    },
    {
      title: 'Memory modules',
      subtitle: 'Populated DIMMs with size, speed, vendor, and slot health.',
      rows: Array.isArray(data?.memory_modules) ? data.memory_modules : [],
      preferredKeys: ['DeviceDescription', 'BankLabel', 'Size', 'Speed', 'Manufacturer', 'SerialNumber', 'PrimaryStatus'],
    },
    {
      title: 'Storage controllers',
      subtitle: 'RAID or HBA controllers, firmware, cache, and overall state.',
      rows: Array.isArray(data?.storage_controllers) ? data.storage_controllers : [],
      preferredKeys: ['DeviceDescription', 'ProductName', 'ControllerFirmwareVersion', 'CacheSizeInMB', 'RollupStatus', 'PrimaryStatus', 'FQDD'],
    },
    {
      title: 'Disks',
      subtitle: 'Physical drive inventory with type, capacity, serial, and RAID state.',
      rows: Array.isArray(data?.disks) ? data.disks : [],
      preferredKeys: ['DeviceDescription', 'MediaType', 'SizeInBytes', 'RaidStatus', 'Manufacturer', 'SerialNumber', 'PrimaryStatus'],
    },
    {
      title: 'Network interfaces',
      subtitle: 'NIC cards and ports with identifiers, addresses, speed, and link health.',
      rows: Array.isArray(data?.ethernet_interfaces) ? data.ethernet_interfaces : [],
      preferredKeys: ['ProductName', 'DeviceDescription', 'CurrentMACAddress', 'PermanentMACAddress', 'CurrentLinkSpeed', 'LinkStatus', 'PrimaryStatus'],
    },
    {
      title: 'Power supplies',
      subtitle: 'PSU redundancy members with vendor, firmware, and present health.',
      rows: Array.isArray(data?.power_supplies) ? data.power_supplies : [],
      preferredKeys: ['DeviceDescription', 'Model', 'Manufacturer', 'FirmwareVersion', 'PartNumber', 'SerialNumber', 'PrimaryStatus'],
    },
  ];
  const hasHardwareSections = hardwareSections.some(section => section.rows.length > 0);

  return (
    <>
      {data?.error ? (
        <div className="inline-alert danger">Error: {data.error}</div>
      ) : tab === 'idrac-users' ? (
        <>
          <div className="card-title">Configured iDRAC users</div>
          <p style={{ marginTop: 0, color: 'var(--text-muted)' }}>
            Populated account slots returned by iDRAC. Access columns fall back to the detected role when older controllers omit those fields.
          </p>
          <IdracUsersTable data={data} />
        </>
      ) : tab === 'hardware' ? (
        <>
          <HardwareOverview data={data} />
          <div className="card-title" style={{ marginTop: 24 }}>Hardware summary</div>
          <SummaryGrid data={data} />
          {hasHardwareSections ? (
            hardwareSections.map(section => <SectionTable key={section.title} {...section} />)
          ) : (
            <div className="inline-alert" style={{ marginTop: 16 }}>DSM could not infer hardware component tables yet. The raw response is still available below.</div>
          )}
        </>
      ) : (
        <>
          <div className="card-title">{TABS.find(t => t.id === tab)?.label} summary</div>
          <SummaryGrid data={data} />
          {list.length > 0 && <ListTable data={data} />}
          {list.length === 0 && tab !== 'firmware' && <div className="inline-alert" style={{ marginTop: 16 }}>DSM could not infer a table from this response yet. The raw response is still available below.</div>}
        </>
      )}
      <JsonViewer data={data} />
    </>
  );
}

export default function IdracSettings() {
  const { server_id } = useParams();
  const serverId = parseInt(server_id || '0');
  const [activeTab, setActiveTab] = useState<Tab>('firmware');
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [server, setServer] = useState<any>(null);
  const [cache, setCache] = useState<TabCache>({});
  const [loadedAt, setLoadedAt] = useState<TabTimestamps>({});

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const srv = await api.getServer(serverId);
      setServer(srv);
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
      setCache(prev => ({ ...prev, [activeTab]: result }));
      setLoadedAt(prev => ({ ...prev, [activeTab]: new Date().toISOString() }));
    } catch (err: any) {
      setData({ error: err.message });
    } finally {
      setLoading(false);
    }
  }, [serverId, activeTab]);

  useEffect(() => {
    if (cache[activeTab]) {
      setData(cache[activeTab]);
    } else {
      setData(null);
    }
  }, [activeTab, cache]);

  useEffect(() => { fetchData(); }, [fetchData]);

  return (
    <div>
      <div className="page-toolbar">
        <div>
          <h2 className="page-title">iDRAC settings · {server?.name || 'Server'}</h2>
          <p className="page-subtitle"><span className="mono">{server?.ipmi_ip || ''}</span> · {server?.model || 'Loading platform'} · {server?.serial || ''}</p>
        </div>
        <button className="btn" onClick={fetchData} disabled={loading}>Refresh</button>
      </div>

      <div className="settings-layout">
        <div className="settings-nav">
          {TABS.map(t => (
            <button key={t.id} className={`settings-nav-item ${activeTab === t.id ? 'active' : ''}`} onClick={() => setActiveTab(t.id)}>
              <strong>{t.label}</strong><span className="side-nav-hint">{t.hint}</span>
            </button>
          ))}
        </div>

        <div className="card settings-content-card">
          {loading && (
            <div className="inline-alert warning" style={{ marginBottom: 16 }}>
              Querying iDRAC for <strong>{TABS.find(t => t.id === activeTab)?.label}</strong> data. Older iDRAC pages can take 10–20 seconds to update.
            </div>
          )}
          {!loading && loadedAt[activeTab] && (
            <div className="inline-alert" style={{ marginBottom: 16 }}>
              Last updated {new Date(loadedAt[activeTab] as string).toLocaleTimeString()}.
            </div>
          )}
          {loading && !data ? <div className="skeleton" /> : data ? <TabContent tab={activeTab} data={data} /> : <div className="empty-state"><strong>No data</strong><p>Refresh this tab to query iDRAC.</p></div>}
        </div>
      </div>
    </div>
  );
}
