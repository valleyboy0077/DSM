import { Routes, Route, Navigate, Link, useLocation, useNavigate } from 'react-router-dom';
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
  if (loading) return <div style={{padding: 40, textAlign: 'center', color: 'var(--text-secondary)'}}>Loading...</div>;
  if (!user) return <Navigate to="/login" replace />;
  return children;
}

function ThemeSelector() {
  const { user, setTheme } = useAuth();
  return (
    <div className="theme-selector">
      {THEMES.map(t => (
        <div
          key={t.id}
          className={`theme-dot ${user?.theme === t.id ? 'active' : ''}`}
          style={{backgroundColor: t.color}}
          title={t.label}
          onClick={() => setTheme(t.id)}
        />
      ))}
    </div>
  );
}

function Header() {
  const { user, logout } = useAuth();
  const location = useLocation();
  const nav = [
    { path: '/', label: 'Dashboard' },
    { path: '/inventory', label: 'Inventory' },
    { path: '/users', label: 'Users' },
    { path: '/temp-profiles', label: 'Temp Profiles' },
  ];

  return (
    <>
      <header className="header">
        <h1>
          <Link to="/" style={{color: 'inherit', textDecoration: 'none'}}>
            <span className="logo">⬡</span> Dell Server Manager
          </Link>
        </h1>
        <div className="header-info">
          {user && (
            <span>
              {user.username} ({user.is_superuser ? 'Superadmin' : user.roles.join(', ')})
            </span>
          )}
          <ThemeSelector />
          {user && (
            <button className="btn btn-sm" onClick={logout}>Logout</button>
          )}
        </div>
      </header>

      <nav className="nav">
        {nav.map(n => {
          const active = location.pathname === n.path || (n.path !== '/' && location.pathname.startsWith(n.path));
          return (
            <Link key={n.path} to={n.path} className={`nav-link ${active ? 'active' : ''}`}>
              {n.label}
            </Link>
          );
        })}
      </nav>
    </>
  );
}

export default function App() {
  const { user, loading } = useAuth();

  if (loading) {
    return <div style={{display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh', color: 'var(--text-secondary)'}}>Loading DSM...</div>;
  }

  return (
    <Routes>
      <Route path="/login" element={!user ? <LoginPage /> : <Navigate to="/" />} />

      <Route path="/*" element={
        <RequireAuth>
          <div className="app">
            <Header />
            <main className="main-content">
              <div className="container">
                <Routes>
                  <Route path="/" element={<Dashboard />} />
                  <Route path="/inventory" element={<Inventory />} />
                  <Route path="/users" element={<Users />} />
                  <Route path="/temp-profiles" element={<TempProfiles />} />
                  <Route path="/server/:server_id/fan-control" element={<FanControl />} />
                  <Route path="/server/:server_id/settings" element={<IdracSettings />} />
                  <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
              </div>
            </main>
          </div>
        </RequireAuth>
      } />
    </Routes>
  );
}
