import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AlertCircle, Inbox as InboxIcon, Loader2, X } from 'lucide-react';

/** Loads data on mount and whenever `deps` change; `reload(true)` refetches silently (used for SSE refreshes). */
export function useAsync<T>(fn: () => Promise<T>, deps: React.DependencyList) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);
  const run = useCallback(fn, deps);

  const reload = useCallback(
    async (silent = false) => {
      const id = ++seq.current;
      if (!silent) setLoading(true);
      try {
        const d = await run();
        if (id === seq.current) {
          setData(d);
          setError(null);
        }
      } catch (e: any) {
        if (id === seq.current) setError(e?.message || 'Request failed');
      } finally {
        if (id === seq.current) setLoading(false);
      }
    },
    [run],
  );

  useEffect(() => {
    reload();
  }, [reload]);

  return { data, setData, error, loading, reload };
}

/** Re-run `cb` (debounced) whenever `tick` changes after mount. */
export function useOnTick(tick: number, cb: () => void, delay = 300) {
  const prev = useRef(tick);
  const cbRef = useRef(cb);
  cbRef.current = cb;
  useEffect(() => {
    if (prev.current === tick) return;
    prev.current = tick;
    const t = setTimeout(() => cbRef.current(), delay);
    return () => clearTimeout(t);
  }, [tick, delay]);
}

export const Loading: React.FC<{ label?: string }> = ({ label = 'Loading…' }) => (
  <div className="flex items-center justify-center gap-2 px-4 py-12 text-sm text-slate-500">
    <Loader2 className="h-4 w-4 animate-spin text-brand-600" />
    {label}
  </div>
);

export const ErrorBox: React.FC<{ message: string; onRetry?: () => void }> = ({ message, onRetry }) => (
  <div role="alert" className="flex items-center gap-2 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
    <AlertCircle className="h-4 w-4 shrink-0 text-rose-600" />
    <span className="flex-1">{message}</span>
    {onRetry && (
      <button onClick={onRetry} className="text-xs font-semibold text-rose-700 underline-offset-2 hover:underline">
        Retry
      </button>
    )}
  </div>
);

export const Empty: React.FC<{ title: string; hint?: string }> = ({ title, hint }) => (
  <div className="flex flex-col items-center justify-center px-4 py-12 text-center">
    <div className="flex h-10 w-10 items-center justify-center rounded-full bg-slate-100 text-slate-400">
      <InboxIcon className="h-5 w-5" />
    </div>
    <p className="mt-3 text-sm font-medium text-slate-700">{title}</p>
    {hint && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
  </div>
);

export const Pill: React.FC<{ tone?: string; children: React.ReactNode; title?: string }> = ({ tone = 'bg-slate-100 text-slate-700', children, title }) => (
  <span title={title} className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-medium ${tone}`}>
    {children}
  </span>
);

const STAGE_TONES: Record<string, string> = {
  NEW: 'bg-sky-50 text-sky-700 ring-1 ring-sky-200',
  ENGAGED: 'bg-indigo-50 text-indigo-700 ring-1 ring-indigo-200',
  QUALIFIED: 'bg-violet-50 text-violet-700 ring-1 ring-violet-200',
  VISIT_PLANNED: 'bg-amber-50 text-amber-700 ring-1 ring-amber-200',
  VISITED: 'bg-teal-50 text-teal-700 ring-1 ring-teal-200',
  WON: 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200',
  LOST: 'bg-rose-50 text-rose-700 ring-1 ring-rose-200',
  DORMANT: 'bg-slate-100 text-slate-600 ring-1 ring-slate-200',
};
export const stageTone = (s: string | null | undefined) => STAGE_TONES[s || ''] || 'bg-slate-100 text-slate-700 ring-1 ring-slate-200';

const STATUS_TONES: Record<string, string> = {
  PLANNED: 'bg-amber-50 text-amber-700',
  CONFIRMED: 'bg-brand-50 text-brand-700',
  VISITED: 'bg-emerald-50 text-emerald-700',
  NO_SHOW: 'bg-rose-50 text-rose-700',
  CANCELLED: 'bg-slate-100 text-slate-600',
  RESCHEDULED: 'bg-violet-50 text-violet-700',
  SCHEDULED: 'bg-sky-50 text-sky-700',
  QUEUED: 'bg-brand-50 text-brand-700',
  SENT: 'bg-emerald-50 text-emerald-700',
  DONE: 'bg-emerald-50 text-emerald-700',
  NEEDS_ACTION: 'bg-amber-50 text-amber-800',
  AWAITING_APPROVAL: 'bg-violet-50 text-violet-700',
  FAILED: 'bg-rose-50 text-rose-700',
  SKIPPED: 'bg-slate-100 text-slate-600',
  PENDING: 'bg-amber-50 text-amber-800',
  APPROVED: 'bg-emerald-50 text-emerald-700',
  EDITED: 'bg-brand-50 text-brand-700',
  REJECTED: 'bg-rose-50 text-rose-700',
  SUPERSEDED: 'bg-slate-100 text-slate-600',
};
export const statusTone = (s: string | null | undefined) => STATUS_TONES[s || ''] || 'bg-slate-100 text-slate-700';

export const pretty = (s: string | null | undefined) =>
  s ? s.replace(/_/g, ' ').toLowerCase().replace(/^\w/, (c) => c.toUpperCase()) : '—';

export const fmtDateTime = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '—';

export const fmtDay = (isoDay: string | null | undefined) =>
  isoDay ? new Date(`${isoDay}T00:00:00`).toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric', month: 'short' }) : 'Unscheduled';

export const fmtInr = (v: number | null | undefined) =>
  v === null || v === undefined ? '—' : `₹${v.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;

export const fmtPct = (v: number | null | undefined) => (v === null || v === undefined ? '—' : `${Math.round(v * 100)}%`);

/** Local YYYY-MM-DD (the API speaks tenant-local dates). */
export const isoDate = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

export const addDays = (isoDay: string, n: number) => {
  const d = new Date(`${isoDay}T00:00:00`);
  d.setDate(d.getDate() + n);
  return isoDate(d);
};

export const Drawer: React.FC<{ open: boolean; onClose: () => void; title: React.ReactNode; children: React.ReactNode }> = ({
  open,
  onClose,
  title,
  children,
}) => {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-slate-900/30" onClick={onClose} />
      <aside role="dialog" aria-modal="true" className="relative flex h-full w-full max-w-xl flex-col bg-white shadow-pop">
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
          <div className="min-w-0 flex-1">{title}</div>
          <button onClick={onClose} className="btn-icon" aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto">{children}</div>
      </aside>
    </div>
  );
};

export const SectionCard: React.FC<{ title: React.ReactNode; actions?: React.ReactNode; children: React.ReactNode; className?: string }> = ({
  title,
  actions,
  children,
  className = '',
}) => (
  <section className={`card overflow-hidden ${className}`}>
    <div className="flex items-center justify-between gap-3 border-b border-slate-100 px-5 py-3">
      <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
      {actions}
    </div>
    {children}
  </section>
);

export const Kpi: React.FC<{ label: string; value: React.ReactNode; icon: React.ElementType; tone: string; footer?: React.ReactNode }> = ({
  label,
  value,
  icon: Icon,
  tone,
  footer,
}) => (
  <div className="card p-5">
    <div className="flex items-start justify-between">
      <p className="text-sm font-medium text-slate-500">{label}</p>
      <div className={`flex h-9 w-9 items-center justify-center rounded-lg ${tone}`}>
        <Icon className="h-[18px] w-[18px]" />
      </div>
    </div>
    <div className="mt-1 text-[28px] font-semibold leading-tight tracking-tight text-slate-900 tabular-nums">{value}</div>
    {footer && <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-slate-500">{footer}</div>}
  </div>
);
