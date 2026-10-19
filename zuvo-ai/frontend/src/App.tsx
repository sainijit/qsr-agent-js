import React, { useCallback, useEffect, useState } from 'react';
import {
  CalendarDays,
  CheckSquare,
  Clock,
  Inbox,
  LayoutDashboard,
  LineChart,
  Settings as SettingsIcon,
  Users,
} from 'lucide-react';
import {
  fetchApprovals,
  fetchHealth,
  fetchSettingsConfig,
  fetchTenants,
  getSession,
  clearSession,
  openEventStream,
  switchTenant,
  Session,
} from './services/api';
import { HealthStatus, StreamKind, WorkerRef } from './types';
import { LoginScreen } from './components/LoginScreen';
import { Header } from './components/Header';
import { InboxView } from './components/InboxView';
import { TodayView } from './components/phase1/TodayView';
import { LeadsView } from './components/phase1/LeadsView';
import { VisitsView } from './components/phase1/VisitsView';
import { FollowupsView } from './components/phase1/FollowupsView';
import { ApprovalsView } from './components/phase1/ApprovalsView';
import { InsightsView } from './components/phase1/InsightsView';
import { SettingsView } from './components/phase1/SettingsView';
import { ErrorBox } from './components/phase1/ui';

export const App: React.FC = () => {
  const [session, setSession] = useState<Session | null>(getSession());

  useEffect(() => {
    const onLogout = () => setSession(null);
    window.addEventListener('zuvo:logout', onLogout);
    return () => window.removeEventListener('zuvo:logout', onLogout);
  }, []);

  if (!session) return <LoginScreen onLoggedIn={setSession} />;
  return <Shell key={session.tenant_id} session={session} onSession={setSession} onLogout={clearSession} />;
};

type TabId = 'today' | 'inbox' | 'leads' | 'visits' | 'followups' | 'approvals' | 'insights' | 'settings';
const TABS: { id: TabId; label: string; icon: React.ElementType; managerOnly?: boolean }[] = [
  { id: 'today', label: 'Today', icon: LayoutDashboard },
  { id: 'inbox', label: 'Inbox', icon: Inbox },
  { id: 'leads', label: 'Leads', icon: Users },
  { id: 'visits', label: 'Visits', icon: CalendarDays },
  { id: 'followups', label: 'Follow-ups', icon: Clock },
  { id: 'approvals', label: 'Approvals', icon: CheckSquare },
  { id: 'insights', label: 'Insights', icon: LineChart, managerOnly: true },
  { id: 'settings', label: 'Settings', icon: SettingsIcon, managerOnly: true },
];

const tabFromHash = (): TabId => {
  const h = window.location.hash.replace('#', '') as TabId;
  return TABS.some((t) => t.id === h) ? h : 'today';
};

type Ticks = Record<StreamKind, number>;

const Shell: React.FC<{ session: Session; onSession: (s: Session) => void; onLogout: () => void }> = ({ session, onSession, onLogout }) => {
  const canManage = session.role === 'OWNER' || session.role === 'MANAGER';
  const tabs = TABS.filter((t) => canManage || !t.managerOnly);
  const [tab, setTabState] = useState<TabId>(() => {
    const t = tabFromHash();
    return tabs.some((x) => x.id === t) ? t : 'today';
  });
  const [ticks, setTicks] = useState<Ticks>({ message: 0, lead: 0, visit: 0, approval: 0 });
  const [live, setLive] = useState(false);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [tenantName, setTenantName] = useState('');
  const [pending, setPending] = useState<number | null>(null);
  const [workers, setWorkers] = useState<WorkerRef[]>([]);
  const [switching, setSwitching] = useState(false);
  const [switchError, setSwitchError] = useState<string | null>(null);

  const setTab = (t: TabId) => {
    setTabState(t);
    window.history.replaceState(null, '', `#${t}`);
  };

  useEffect(() => {
    const onHash = () => setTabState(tabFromHash());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  useEffect(() => {
    fetchHealth().then(setHealth).catch(() => undefined);
    fetchTenants()
      .then((t) => setTenantName(t[0]?.business_name || ''))
      .catch(() => undefined);
    if (canManage) fetchSettingsConfig().then((c) => setWorkers(c.workers)).catch(() => undefined);
  }, [canManage]);

  useEffect(() => {
    const es = openEventStream((kind) => setTicks((t) => ({ ...t, [kind]: t[kind] + 1 })));
    if (!es) return;
    es.onopen = () => setLive(true);
    es.onerror = () => setLive(es.readyState === EventSource.OPEN);
    return () => es.close();
  }, []);

  const refreshPending = useCallback(() => {
    fetchApprovals('PENDING')
      .then((r) => setPending(r.pending))
      .catch(() => setPending(null));
  }, []);
  useEffect(() => {
    const t = setTimeout(refreshPending, ticks.approval ? 300 : 0);
    return () => clearTimeout(t);
  }, [ticks.approval, refreshPending]);

  const onSwitchTenant = async (tenantId: string) => {
    if (tenantId === session.tenant_id) return;
    setSwitching(true);
    setSwitchError(null);
    try {
      onSession(await switchTenant(tenantId));
    } catch (e: any) {
      setSwitchError(e.message);
      setSwitching(false);
    }
  };

  const any = ticks.lead + ticks.visit + ticks.approval + ticks.message;
  const tenantLabel = tenantName || session.tenants?.find((t) => t.tenant_id === session.tenant_id)?.name || 'Dashboard';
  const userLabel = session.display_name || session.email || session.role.charAt(0) + session.role.slice(1).toLowerCase().replace(/_/g, ' ');

  return (
    <div className="flex min-h-screen flex-col bg-canvas selection:bg-brand-100 selection:text-brand-900">
      <Header
        tenantName={tenantLabel}
        tenants={session.tenants || []}
        selectedTenantId={session.tenant_id}
        onSelectTenant={onSwitchTenant}
        switching={switching}
        health={health}
        live={live}
        userLabel={userLabel}
        onLogout={onLogout}
      />

      <nav className="sticky top-16 z-30 border-b border-slate-200 bg-white/95 backdrop-blur">
        <div className="scrollbar-none mx-auto flex max-w-[1440px] gap-1 overflow-x-auto px-4 lg:px-8" role="tablist">
          {tabs.map((t) => {
            const active = tab === t.id;
            return (
              <button
                key={t.id}
                role="tab"
                aria-selected={active}
                onClick={() => setTab(t.id)}
                className={`relative inline-flex shrink-0 items-center gap-2 border-b-2 px-3 py-3 text-sm font-medium transition ${
                  active ? 'border-brand-600 text-brand-700' : 'border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-800'
                }`}
              >
                <t.icon className="h-4 w-4" />
                {t.label}
                {t.id === 'approvals' && pending !== null && pending > 0 && (
                  <span className="ml-0.5 inline-flex min-w-[20px] items-center justify-center rounded-full bg-rose-500 px-1.5 text-[11px] font-semibold leading-5 text-white">
                    {pending}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </nav>

      <main className="mx-auto w-full max-w-[1440px] flex-1 space-y-6 px-4 py-6 lg:px-8 lg:py-8">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">{TABS.find((t) => t.id === tab)?.label}</h1>
          <p className="mt-1 text-sm text-slate-500">{tenantLabel}</p>
        </div>
        {switchError && <ErrorBox message={`Could not switch business: ${switchError}`} />}

        {tab === 'today' && <TodayView tick={any} onOpenTab={setTab} />}
        {tab === 'inbox' && <InboxView messageTick={ticks.message} />}
        {tab === 'leads' && <LeadsView tick={ticks.lead + ticks.visit} canManage={canManage} workers={workers} />}
        {tab === 'visits' && <VisitsView tick={ticks.visit + ticks.lead} />}
        {tab === 'followups' && <FollowupsView tick={ticks.lead + ticks.visit + ticks.approval} />}
        {tab === 'approvals' && <ApprovalsView tick={ticks.approval} onPendingChange={setPending} />}
        {tab === 'insights' && canManage && <InsightsView tick={ticks.lead + ticks.approval} />}
        {tab === 'settings' && canManage && <SettingsView />}
      </main>
    </div>
  );
};
