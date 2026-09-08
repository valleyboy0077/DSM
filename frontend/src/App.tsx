import { Routes, Route, Navigate, NavLink, Link, useLocation } from 'react-router-dom';
import { useAuth } from './auth';
import { THEMES } from './types';

import LoginPage from './pages/Login';
import Dashboard from './pages/Dashboard';
import Inventory from './pages/Inventory';
import Users from './pages/Users';
import TempProfiles from './pages/TempProfiles';
import FanControl from './pages/FanControl';
import IdracSettings from './pages/IdracSettings';

function RequireAuth({ children }: { children: JSX.Element }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="loading-screen">Loading DSM...</div>;
  if (!user) return <Navigate to="/login" replace />;
  return children;
}

function ThemeSelector() {
  const { user, setTheme } = useAuth();
  return (
    <div className="theme-selector" aria-label="Theme selector">
      {THEMES.map(t => (
        <button
          key={t.id}
          type="button"
          className={`theme-dot ${user?.theme === t.id ? 'active' : ''}`}
          style={{ backgroundColor: t.color }}
          title={t.label}
          aria-label={t.label}
          onClick={() => setTheme(t.id)}
        />
      ))}
    </div>
  );
}

const nav = [
  { path: '/', label: 'Command Center', hint: 'Fleet health' },
  { path: '/inventory', label: 'Infrastructure', hint: 'iDRAC endpoints' },
  { path: '/temp-profiles', label: 'Thermal Policy', hint: 'Cooling profiles' },
  { path: '/users', label: 'Access', hint: 'Users & roles' },
];

const pageMeta: Record<string, { title: string; subtitle: string }> = {
  '/': { title: 'Fleet Overview', subtitle: 'Dell iDRAC health, server state, and quick operations.' },
  '/inventory': { title: 'Inventory', subtitle: 'Manage Dell servers, iDRAC addresses, and inventory groups.' },
  '/temp-profiles': { title: 'Temperature Profiles', subtitle: 'Define thermal thresholds and cooling policies per server.' },
  '/users': { title: 'User Management', subtitle: 'DSM users, roles, and iDRAC account propagation.' },
};

function getPageMeta(pathname: string) {
  if (pathname.includes('/fan-control')) return { title: 'Fan Control', subtitle: 'Operational cooling control with safety-aware manual override.' };
  if (pathname.includes('/settings')) return { title: 'iDRAC Settings', subtitle: 'Firmware, hardware, storage, power, logs, and iDRAC users.' };
  return pageMeta[pathname] || pageMeta['/'];
}

function AppShell({ children }: { children: JSX.Element }) {
  const { user, logout } = useAuth();
  const location = useLocation();
  const meta = getPageMeta(location.pathname);
  const roleLabel = user?.is_superuser ? 'Superadmin' : user?.roles?.join(', ') || 'User';

  return (
    <div className="app-shell">
      <div className="ambient ambient-a" />
      <div className="ambient ambient-b" />
      <aside className="sidebar">
        <Link to="/" className="brand">
          <span className="brand-mark"><span>DSM</span></span>
          <span>
            <span className="brand-title">Dell Server Manager</span>
            <span className="brand-subtitle">iDRAC command suite</span>
          </span>
        </Link>

        <nav className="side-nav" aria-label="Primary navigation">
          {nav.map(n => (
            <NavLink
              key={n.path}
              to={n.path}
              end={n.path === '/'}
              className={({ isActive }) => `side-nav-link ${isActive ? 'active' : ''}`}
            >
              <span className="side-nav-label">{n.label}</span>
              <span className="side-nav-hint">{n.hint}</span>
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-footer">
          <div className="sidebar-user">
            <div className="user-avatar">{user?.username?.slice(0, 2).toUpperCase() || 'U'}</div>
            <div>
              <div className="sidebar-username">{user?.username}</div>
              <div className="sidebar-role">{roleLabel}</div>
            </div>
          </div>
          <div className="theme-block">
            <span className="theme-label">Theme</span>
            <ThemeSelector />
          </div>
          <button className="btn btn-block" onClick={logout}>Logout</button>
        </div>
      </aside>

      <div className="workspace">
        <header className="topbar">
          <div>
            <div className="eyebrow">DSM Command Suite</div>
            <h1>{meta.title}</h1>
            <p>{meta.subtitle}</p>
          </div>
          <div className="topbar-cluster">
            <div className="topbar-status">
              <span className="live-dot" />
              Localhost lab
            </div>
            <div className="session-pill">
              <span className="session-kicker">Signed in</span>
              <strong>{user?.username}</strong>
            </div>
          </div>
        </header>
        <main className="main-content">
          {children}
        </main>
      </div>
    </div>
  );
}

export default function App() {
  const { user, loading } = useAuth();

  if (loading) return <div className="loading-screen">Loading DSM...</div>;

  return (
    <Routes>
      <Route path="/login" element={!user ? <LoginPage /> : <Navigate to="/" />} />
      <Route path="/*" element={
        <RequireAuth>
          <AppShell>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/inventory" element={<Inventory />} />
              <Route path="/users" element={<Users />} />
              <Route path="/temp-profiles" element={<TempProfiles />} />
              <Route path="/server/:server_id/fan-control" element={<FanControl />} />
              <Route path="/server/:server_id/settings" element={<IdracSettings />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </AppShell>
        </RequireAuth>
      } />
    </Routes>
  );
}
