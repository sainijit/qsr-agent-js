import React, { useEffect, useState } from 'react';
import { MessageCircle, ChevronRight, ChevronDown, AlertCircle, LogIn, Loader2 } from 'lucide-react';
import { DevIdentity, Session, devLogin, fetchDevIdentities, passwordLogin } from '../services/api';
import { avatarTone, initialsOf, prettyRole } from './ConversationsList';

interface LoginScreenProps {
  onLoggedIn: (session: Session) => void;
}

/** Email/password sign-in, with the dev identity picker (AUTH_DEV_LOGIN=true) as a collapsible fallback. */
export const LoginScreen: React.FC<LoginScreenProps> = ({ onLoggedIn }) => {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loginError, setLoginError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const [showDev, setShowDev] = useState(false);
  const [identities, setIdentities] = useState<DevIdentity[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    if (!showDev || identities !== null) return;
    fetchDevIdentities().then(setIdentities).catch((e) => {
      setIdentities([]);
      setError(e.message);
    });
  }, [showDev, identities]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setLoginError(null);
    try {
      onLoggedIn(await passwordLogin(email.trim(), password));
    } catch (err: any) {
      setLoginError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  const byTenant = (identities || []).reduce<Record<string, DevIdentity[]>>((acc, i) => {
    (acc[i.tenant_name] ||= []).push(i);
    return acc;
  }, {});

  const login = async (i: DevIdentity) => {
    setBusy(`${i.tenant_id}:${i.worker_key}`);
    try {
      onLoggedIn(await devLogin(i.tenant_id, i.worker_key));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-b from-brand-50/60 via-canvas to-canvas p-6">
      <div className="w-full max-w-md">
        <div className="mb-8 flex flex-col items-center text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-brand-600 text-white shadow-sm">
            <MessageCircle className="h-6 w-6" strokeWidth={2.25} />
          </div>
          <h1 className="mt-4 text-2xl font-semibold tracking-tight text-slate-900">Sign in to ZuvoCloud</h1>
          <p className="mt-1 text-sm text-slate-500">Use your business account email and password</p>
        </div>

        <form onSubmit={submit} className="card space-y-4 p-6">
          {loginError && (
            <div role="alert" className="flex items-center gap-2 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-800">
              <AlertCircle className="h-4 w-4 text-rose-600" />
              {loginError}
            </div>
          )}
          <div>
            <label className="label" htmlFor="login-email">Email</label>
            <input
              id="login-email"
              type="email"
              autoComplete="username"
              required
              className="field"
              placeholder="owner@business.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div>
            <label className="label" htmlFor="login-password">Password</label>
            <input
              id="login-password"
              type="password"
              autoComplete="current-password"
              required
              className="field"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          <button type="submit" className="btn-primary w-full" disabled={submitting || !email || !password}>
            {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <LogIn className="h-4 w-4" />}
            Sign in
          </button>
        </form>

        <div className="mt-4">
          <button
            onClick={() => setShowDev((v) => !v)}
            aria-expanded={showDev}
            className="mx-auto flex items-center gap-1 text-xs font-medium text-slate-500 hover:text-slate-800"
          >
            <ChevronDown className={`h-3.5 w-3.5 transition ${showDev ? 'rotate-180' : ''}`} />
            Development sign-in (worker identities)
          </button>
          {showDev && (
            <div className="card mt-3 divide-y divide-slate-100 overflow-hidden">
              {error && (
                <div className="flex items-center gap-2 bg-rose-50 px-5 py-3 text-sm text-rose-800">
                  <AlertCircle className="h-4 w-4 text-rose-600" />
                  {error}
                </div>
              )}
              {identities === null && <div className="px-5 py-8 text-center text-sm text-slate-500">Loading identities…</div>}
              {Object.entries(byTenant).map(([tenant, list]) => (
                <div key={tenant} className="py-2">
                  <h2 className="eyebrow px-5 py-2">{tenant}</h2>
                  <ul>
                    {list.map((i) => {
                      const key = `${i.tenant_id}:${i.worker_key}`;
                      return (
                        <li key={i.worker_key}>
                          <button
                            onClick={() => login(i)}
                            disabled={busy !== null}
                            className="group flex w-full items-center gap-3 px-5 py-2.5 text-left transition hover:bg-slate-50 disabled:opacity-60"
                          >
                            <span className={`flex h-9 w-9 items-center justify-center rounded-full text-sm font-semibold ${avatarTone(i.worker_name)}`}>
                              {initialsOf(i.worker_name)}
                            </span>
                            <span className="flex-1">
                              <span className="block text-sm font-medium text-slate-900">{i.worker_name}</span>
                              <span className="block text-xs text-slate-500">{prettyRole(i.role)}</span>
                            </span>
                            <ChevronRight className={`h-4 w-4 text-slate-300 transition group-hover:translate-x-0.5 group-hover:text-slate-500 ${busy === key ? 'animate-pulse' : ''}`} />
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              ))}
              <p className="px-5 py-3 text-center text-xs text-slate-400">Identities from config/tenant-routing.yaml</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
