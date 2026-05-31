import { createContext, useContext, useState, useEffect, ReactNode } from 'react';

interface AuthState {
  token: string | null;
  user: {
    user_id: number;
    username: string;
    is_superuser: boolean;
    roles: string[];
    theme: string;
  } | null;
  loading: boolean;
}

interface AuthContextType extends AuthState {
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  setTheme: (theme: string) => Promise<void>;
  hasRole: (role: string) => boolean;
}

const AuthContext = createContext<AuthContextType | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({
    token: localStorage.getItem('dsm_token'),
    user: null,
    loading: true,
  });

  // Restore user on mount
  useEffect(() => {
    if (state.token) {
      fetchMe();
    } else {
      setState(s => ({ ...s, loading: false }));
    }
  }, []);

  // Apply theme
  useEffect(() => {
    if (state.user?.theme) {
      document.documentElement.setAttribute('data-theme', state.user.theme);
    }
  }, [state.user?.theme]);

  async function fetchMe() {
    try {
      const resp = await fetch('/auth/me', {
        headers: { 'Authorization': `Bearer ${state.token}` },
      });
      if (resp.ok) {
        const data = await resp.json();
        setState(s => ({
          ...s,
          user: {
            user_id: data.id,
            username: data.username,
            is_superuser: data.is_superuser,
            roles: data.roles,
            theme: data.theme || 'dark',
          },
          loading: false,
        }));
      } else {
        logout();
      }
    } catch {
      logout();
    }
  }

  async function login(username: string, password: string) {
    const resp = await fetch('/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || 'Login failed');
    }
    const data = await resp.json();
    localStorage.setItem('dsm_token', data.access_token);
    setState({
      token: data.access_token,
      user: {
        user_id: data.user_id,
        username: data.username,
        is_superuser: data.is_superuser,
        roles: data.roles,
        theme: data.theme || 'dark',
      },
      loading: false,
    });
  }

  function logout() {
    localStorage.removeItem('dsm_token');
    setState({ token: null, user: null, loading: false });
  }

  async function setTheme(theme: string) {
    if (!state.token) return;
    try {
      await fetch('/auth/theme', {
        method: 'PUT',
        headers: {
          'Authorization': `Bearer ${state.token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(theme),
      });
      setState(s => ({ ...s, user: s.user ? { ...s.user, theme } : null }));
    } catch (e) {
      console.error('Failed to set theme:', e);
    }
  }

  function hasRole(role: string): boolean {
    return state.user?.is_superuser || state.user?.roles.includes(role) || false;
  }

  return (
    <AuthContext.Provider value={{ ...state, login, logout, setTheme, hasRole }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
