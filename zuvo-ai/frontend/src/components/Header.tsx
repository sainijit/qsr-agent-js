import React from 'react';
import { LogOut, ChevronDown, MessageCircle } from 'lucide-react';
import { AuthTenant, HealthStatus } from '../types';

interface HeaderProps {
  tenantName: string;
  tenants: AuthTenant[];
  selectedTenantId: string;
  onSelectTenant: (tenantId: string) => void;
  switching?: boolean;
  health: HealthStatus | null;
  live: boolean;
  userLabel?: string;
  onLogout?: () => void;
}

export const Header: React.FC<HeaderProps> = ({
  tenantName,
  tenants,
  selectedTenantId,
  onSelectTenant,
  switching,
  health,
  live,
  userLabel,
  onLogout,
}) => {
  const healthy = !health || health.status === 'ok' || health.status === 'healthy';
  const initials = (userLabel || 'U').trim().charAt(0).toUpperCase();

  return (
    <header className="sticky top-0 z-40 border-b border-slate-200 bg-white/95 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-[1440px] items-center gap-4 px-4 lg:px-8">
        <div className="flex items-center gap-2.5">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-600 text-white shadow-sm">
            <MessageCircle className="h-5 w-5" strokeWidth={2.25} />
          </div>
          <div className="leading-tight">
            <div className="text-[15px] font-semibold text-slate-900">ZuvoCloud</div>
            <div className="text-xs text-slate-500">Operations Console</div>
          </div>
        </div>

        <div className="mx-2 hidden h-8 w-px bg-slate-200 md:block" />
        {tenants.length > 1 ? (
          <div className="relative hidden md:block">
            <select
              value={selectedTenantId}
              onChange={(e) => onSelectTenant(e.target.value)}
              disabled={switching}
              aria-label="Business"
              className="cursor-pointer appearance-none rounded-lg border border-slate-200 bg-slate-50 py-2 pl-3 pr-9 text-sm font-medium text-slate-800 hover:bg-slate-100 focus:border-brand-500 focus:outline-none focus:ring-4 focus:ring-brand-500/10 disabled:opacity-60"
            >
              {tenants.map((t) => (
                <option key={t.tenant_id} value={t.tenant_id}>
                  {t.name || t.tenant_id} · {String(t.role).toLowerCase().replace(/_/g, ' ')}
                </option>
              ))}
            </select>
            <ChevronDown className="pointer-events-none absolute right-3 top-2.5 h-4 w-4 text-slate-400" />
          </div>
        ) : (
          <span className="hidden truncate text-sm font-medium text-slate-800 md:inline">{tenantName}</span>
        )}

        <div className="ml-auto flex items-center gap-2">
          <span
            className={`hidden items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium lg:inline-flex ${
              healthy ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'
            }`}
            title={`Database: ${health?.components?.database ?? 'unknown'}`}
          >
            <span className={`h-1.5 w-1.5 rounded-full ${healthy ? 'bg-emerald-500' : 'bg-amber-500'}`} />
            {healthy ? 'All systems operational' : 'Degraded'}
          </span>
          <span
            className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${live ? 'bg-brand-50 text-brand-700' : 'bg-slate-100 text-slate-500'}`}
            title="Live event stream"
          >
            <span className="relative flex h-2 w-2">
              {live && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-brand-400 opacity-60" />}
              <span className={`relative inline-flex h-2 w-2 rounded-full ${live ? 'bg-brand-600' : 'bg-slate-400'}`} />
            </span>
            {live ? 'Live' : 'Offline'}
          </span>

          {onLogout && (
            <>
              <div className="mx-1 hidden h-8 w-px bg-slate-200 sm:block" />
              <div className="flex items-center gap-2">
                <div className="flex h-8 w-8 items-center justify-center rounded-full bg-slate-100 text-sm font-semibold text-slate-700 ring-1 ring-slate-200">
                  {initials}
                </div>
                <span className="hidden text-sm font-medium text-slate-700 xl:inline">{userLabel}</span>
                <button onClick={onLogout} className="btn-icon" title="Sign out" aria-label="Sign out">
                  <LogOut className="h-4 w-4" />
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </header>
  );
};
