import React, { useMemo, useState } from 'react';
import { CheckCircle2, XCircle, UserX, BadgeCheck, MapPin } from 'lucide-react';
import { Visit, VisitOutcomeStatus } from '../../types';
import { fetchVisits, setVisitOutcome } from '../../services/api';
import { Empty, ErrorBox, Loading, Pill, addDays, fmtDay, fmtPct, isoDate, pretty, stageTone, statusTone, useAsync, useOnTick } from './ui';

type Range = 'next7' | 'next14' | 'past14';
const RANGES: { id: Range; label: string }[] = [
  { id: 'next7', label: 'Next 7 days' },
  { id: 'next14', label: 'Next 14 days' },
  { id: 'past14', label: 'Past 14 days' },
];
const STATUSES = ['', 'PLANNED', 'CONFIRMED', 'VISITED', 'NO_SHOW', 'CANCELLED', 'RESCHEDULED'];
const OUTCOMES: { status: VisitOutcomeStatus; label: string; icon: React.ElementType; cls: string }[] = [
  { status: 'CONFIRMED', label: 'Confirm', icon: BadgeCheck, cls: 'btn-secondary' },
  { status: 'VISITED', label: 'Visited', icon: CheckCircle2, cls: 'btn-primary' },
  { status: 'NO_SHOW', label: 'No-show', icon: UserX, cls: 'btn-secondary' },
  { status: 'CANCELLED', label: 'Cancel', icon: XCircle, cls: 'btn-ghost' },
];
const FINAL = new Set(['VISITED', 'CANCELLED', 'RESCHEDULED']);

export const VisitsView: React.FC<{ tick: number }> = ({ tick }) => {
  const [range, setRange] = useState<Range>('next7');
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const { from, to } = useMemo(() => {
    const today = isoDate(new Date());
    if (range === 'past14') return { from: addDays(today, -14), to: addDays(today, -1) };
    return { from: today, to: addDays(today, range === 'next7' ? 6 : 13) };
  }, [range]);

  const visits = useAsync(() => fetchVisits({ from, to, status: status || undefined }), [from, to, status]);
  useOnTick(tick, () => visits.reload(true));

  const grouped = useMemo(() => {
    const g: Record<string, Visit[]> = {};
    (visits.data?.items || []).forEach((v) => (g[v.date || ''] ||= []).push(v));
    const days = Object.keys(g).sort();
    return (range === 'past14' ? days.reverse() : days).map((d) => [d, g[d]] as const);
  }, [visits.data, range]);

  const outcome = async (v: Visit, s: VisitOutcomeStatus) => {
    setBusy(v.id + s);
    setErr(null);
    try {
      await setVisitOutcome(v.id, s);
      await visits.reload(true);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(null);
    }
  };

  const today = visits.data?.today;

  return (
    <div className="space-y-4">
      <div className="card flex flex-wrap items-center gap-3 p-3">
        <div className="inline-flex rounded-lg border border-slate-200 bg-slate-50 p-0.5">
          {RANGES.map((r) => (
            <button
              key={r.id}
              onClick={() => setRange(r.id)}
              className={`rounded-md px-3 py-1.5 text-sm font-medium transition ${range === r.id ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-800'}`}
            >
              {r.label}
            </button>
          ))}
        </div>
        <select className="field w-auto" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Visit status">
          {STATUSES.map((s) => (
            <option key={s} value={s}>{s ? pretty(s) : 'All active statuses'}</option>
          ))}
        </select>
        <span className="ml-auto text-xs text-slate-500">
          {visits.data ? `${visits.data.items.length} visits · ${fmtDay(visits.data.from)} – ${fmtDay(visits.data.to)}` : ''}
        </span>
      </div>

      {err && <ErrorBox message={err} />}
      {visits.error && <ErrorBox message={visits.error} onRetry={() => visits.reload()} />}
      {visits.loading && !visits.data ? (
        <Loading />
      ) : grouped.length === 0 ? (
        <div className="card"><Empty title="No visits in this range" /></div>
      ) : (
        <div className="space-y-4">
          {grouped.map(([day, list]) => (
            <section key={day} className="card overflow-hidden">
              <div className="flex items-center justify-between border-b border-slate-100 bg-slate-50/60 px-5 py-2.5">
                <h2 className="text-sm font-semibold text-slate-900">
                  {fmtDay(day)} {day === today && <Pill tone="bg-brand-600 text-white">Today</Pill>}
                </h2>
                <span className="text-xs text-slate-500">{list.length} visit{list.length === 1 ? '' : 's'}</span>
              </div>
              <ul className="divide-y divide-slate-100">
                {list.map((v) => (
                  <li key={v.id} className="flex flex-wrap items-center gap-3 px-5 py-3">
                    <div className="min-w-[200px] flex-1">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium text-slate-900">{v.customer_name || v.customer_phone}</span>
                        <Pill tone={statusTone(v.status)}>{pretty(v.status)}</Pill>
                        {v.lead_stage && <Pill tone={stageTone(v.lead_stage)}>{pretty(v.lead_stage)}</Pill>}
                      </div>
                      <p className="mt-0.5 text-xs text-slate-500">
                        {v.customer_phone}
                        {v.time_window ? ` · ${v.time_window}` : ''}
                        {v.interest ? ` · ${v.interest}` : ''}
                        {v.precision ? ` · ${pretty(v.precision)} (${fmtPct(v.confidence)})` : ''}
                      </p>
                      {v.raw_text && (
                        <p className="mt-1 flex items-start gap-1 text-xs italic text-slate-500">
                          <MapPin className="mt-0.5 h-3 w-3 shrink-0" />“{v.raw_text}”
                        </p>
                      )}
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {OUTCOMES.map((o) => (
                        <button
                          key={o.status}
                          className={`${o.cls} px-2.5 py-1.5 text-xs`}
                          disabled={busy !== null || v.status === o.status || FINAL.has(v.status)}
                          onClick={() => outcome(v, o.status)}
                        >
                          <o.icon className="h-3.5 w-3.5" />
                          {o.label}
                        </button>
                      ))}
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </div>
  );
};
