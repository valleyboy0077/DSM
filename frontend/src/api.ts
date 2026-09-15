import type { DashboardSnapshot, Server, SensorReading, FanConfig, FanControlResult, FanTelemetryResponse, SensorSummary, User, ServerGroup, TempProfile } from './types';

const API = '';

function dashboardWebSocketUrl(): string | null {
  const token = localStorage.getItem('dsm_token');
  if (!token) return null;

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = new URL('/sensors/ws/dashboard', `${protocol}//${window.location.host}`);
  // Native browser WebSockets cannot set the API Authorization header. The
  // server validates this short-lived login token before accepting the socket.
  url.searchParams.set('token', token);
  return url.toString();
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const token = localStorage.getItem('dsm_token');
  const baseHeaders: Record<string, string> = { 'Accept': 'application/json' };
  if (token) baseHeaders['Authorization'] = `Bearer ${token}`;
  const requestHeaders = {
    ...baseHeaders,
    ...(options?.headers || {}),
  };

  const resp = await fetch(`${API}${path}`, {
    ...options,
    headers: requestHeaders,
  });

  if (resp.status === 401) {
    localStorage.removeItem('dsm_token');
    window.location.href = '/login';
  }

  if (!resp.ok) {
    const error = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(error.detail || `HTTP ${resp.status}`);
  }
  if (resp.status === 204) return undefined as T;
  return resp.json();
}

export const api = {
  // Auth
  login: (username: string, password: string) =>
    request<any>('/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ username, password }),
    }),

  // Health
  health: () => request<{ status: string }>('/health'),

  // Servers
  listServers: () => request<Server[]>('/servers/'),
  addServer: (data: { name: string; ipmi_ip: string; ipmi_user: string; ipmi_password: string; drac_version?: string }) =>
    request<Server>('/servers/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),
  getServer: (id: number) => request<Server>(`/servers/${id}`),
  updateServer: (id: number, data: Partial<Server>) =>
    request<Server>(`/servers/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),
  deleteServer: (id: number) =>
    request<void>(`/servers/${id}`, { method: 'DELETE' }),
  powerServer: (id: number, action: string) =>
    request<any>(`/servers/${id}/power`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    }),

  // Sensors
  getSensors: (params?: { server_id?: number; sensor_type?: string; hours?: number }) => {
    const qs = new URLSearchParams();
    if (params?.server_id) qs.set('server_id', String(params.server_id));
    if (params?.sensor_type) qs.set('sensor_type', params.sensor_type);
    if (params?.hours) qs.set('hours', String(params.hours));
    return request<any>(`/sensors/?${qs}`);
  },
  getSensorSummary: (serverId: number) =>
    request<SensorSummary>(`/sensors/summary/${serverId}`),
  getDashboardSnapshot: () => request<DashboardSnapshot>('/sensors/dashboard-snapshot'),
  dashboardWebSocketUrl,

  // Fans
  getFanConfig: (serverId: number) => request<FanConfig>(`/fans/${serverId}`),
  getFanTelemetry: (serverId: number) => request<FanTelemetryResponse>(`/fans/${serverId}/telemetry`),
  updateFanConfig: (serverId: number, config: Partial<FanConfig>) =>
    request<FanConfig>(`/fans/${serverId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    }),
  controlFans: (serverId: number, action: string, speed?: number) =>
    request<FanControlResult>(`/fans/${serverId}/control`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, speed }),
    }),

  // Users
  listUsers: () => request<User[]>('/users/'),
  createUser: (data: { username: string; password: string; role: string; email?: string; propagate_to_idrac?: boolean }) =>
    request<any>('/users/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),
  updateUser: (id: number, data: any) =>
    request<any>(`/users/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),
  deleteUser: (id: number) =>
    request<any>(`/users/${id}`, { method: 'DELETE' }),
  propagateUser: (id: number) =>
    request<any>(`/users/${id}/propagate`, { method: 'POST' }),

  // Groups
  listGroups: () => request<ServerGroup[]>('/groups/'),
  createGroup: (data: { name: string; description?: string; color?: string }) =>
    request<ServerGroup>('/groups/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),
  deleteGroup: (id: number) =>
    request<void>(`/groups/${id}`, { method: 'DELETE' }),
  addServerToGroup: (groupId: number, serverId: number) =>
    request<void>(`/groups/${groupId}/servers/${serverId}`, { method: 'POST' }),
  removeServerFromGroup: (groupId: number, serverId: number) =>
    request<void>(`/groups/${groupId}/servers/${serverId}`, { method: 'DELETE' }),

  // Temp Profiles
  listProfiles: (serverId: number) => request<TempProfile[]>(`/temp-profiles/server/${serverId}`),
  createProfile: (serverId: number, data: any) =>
    request<TempProfile>(`/temp-profiles/server/${serverId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),
  updateProfile: (profileId: number, data: any) =>
    request<TempProfile>(`/temp-profiles/${profileId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),
  deleteProfile: (profileId: number) =>
    request<void>(`/temp-profiles/${profileId}`, { method: 'DELETE' }),
  getEffectiveThresholds: (serverId: number) =>
    request<any>(`/temp-profiles/effective/${serverId}`),

  // Settings (iDRAC)
  getNetwork: (serverId: number) => request<any>(`/settings/network/${serverId}`),
  getSEL: (serverId: number, clear?: boolean) => request<any>(`/settings/sel/${serverId}?clear=${clear || 'false'}`),
  getHardware: (serverId: number) => request<any>(`/settings/hardware/${serverId}`),
  getPower: (serverId: number) => request<any>(`/settings/power/${serverId}`),
  getStorage: (serverId: number) => request<any>(`/settings/storage/${serverId}`),
  getBIOS: (serverId: number) => request<any>(`/settings/bios/${serverId}`),
  getSecurity: (serverId: number) => request<any>(`/settings/security/${serverId}`),
  getFirmware: (serverId: number) => request<any>(`/settings/firmware/${serverId}`),
  getAlerts: (serverId: number) => request<any>(`/settings/alerts/${serverId}`),
  getVirtualMedia: (serverId: number) => request<any>(`/settings/virtual-media/${serverId}`),
  getIdracUsers: (serverId: number) => request<any[]>(`/settings/idrac-users/${serverId}`),
};
