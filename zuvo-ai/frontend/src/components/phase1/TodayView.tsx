import React from 'react';
import { CalendarCheck, CheckCircle2, MessageSquareText, Sparkles, UserPlus, Clock, Flame } from 'lucide-react';
import { fetchApprovals, fetchToday } from '../../services/api';
import { Empty, ErrorBox, Kpi, Loading, Pill, SectionCard, fmtDateTime, fmtDay, fmtPct, pretty, stageTone, statusTone, useAsync, useOnTick } from './ui';

interface Props {
  tick: number;
  onOpenTab: (tab: 'leads' | 'visits' | 'followups' | 'approvals') => void;
}

export const TodayView: React.FC<Props> = ({ tick, onOpenTab }) => {
  const today = useAsync(fetchToday, []);
  const approvals = useAsync(() => fetchApprovals('PENDING'), []);
  useOnTick(tick, () => {
    today.reload(true);
    approvals.reload(true);
  });

  if (today.loading && !today.data) return <Loading />;
  if (today.error && !today.data) return <ErrorBox message={today.error} onRetry={() => today.reload()} />;
  const d = today.data!;
  const m = d.metrics;
  const sources = Object.entries(m.leads_new_by_source || {});
  const openStages = Object.entries(m.leads_open_by_stage || {});

  return (
    <div className="space-y-6">
      {today.error && <ErrorBox message={today.error} />}
      <p className="text-sm text-slate-500">
        {fmtDay(d.date)} · {d.tenant.timezone}
      </p>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi
          label="New leads"
          value={m.leads_new ?? 0}
          icon={UserPlus}
          tone="bg-brand-50 text-brand-600"
          footer={sources.length ? sources.map(([s, n]) => <span key={s}>{pretty(s)}: {n}</span>) : <span>No new leads yet</span>}
        />
        <Kpi
          label="Visits next 7 days"
          value={m.visits_next_7d ?? 0}
          icon={CalendarCheck}
          tone="bg-amber-50 text-amber-600"
          footer={
            <>
              <span>{m.visits_planned_for_day ?? 0} today</span>
              <span>{m.visits_done ?? 0} done</span>
              <span>{m.visits_no_show ?? 0} no-show</span>
            </>
          }
        />
        <Kpi
          label="Follow-ups"
          value={m.followups_due ?? 0}
          icon={Clock}
          tone="bg-violet-50 text-violet-600"
          footer={
            <>
              <span>{m.followups_sent ?? 0} sent</span>
              <span className={m.followups_overdue ? 'text-rose-600' : ''}>{m.followups_overdue ?? 0} overdue</span>
              <span className={m.followups_needs_action ? 'text-amber-700' : ''}>{m.followups_needs_action ?? 0} need action</span>
            </>
          }
        />
        <Kpi
          label="Pending approvals"
          value={approvals.data?.pending ?? m.approvals_pending ?? 0}
          icon={Sparkles}
          tone="bg-emerald-50 text-emerald-600"
          footer={
            <>
              <span>{m.agent_drafted ?? 0} drafted</span>
              <span>{m.agent_auto_sent ?? 0} auto-sent</span>
              <span>{m.approvals_decided ?? 0} decided</span>
            </>
          }
        />
        <Kpi
          label="Messages"
          value={(m.messages_in ?? 0) + (m.messages_out ?? 0)}
          icon={MessageSquareText}
          tone="bg-sky-50 text-sky-600"
          footer={
            <>
              <span>{m.messages_in ?? 0} in</span>
              <span>{m.messages_out ?? 0} out</span>
              <span>
                First reply {m.first_response_median_min === null || m.first_response_median_min === undefined ? '—' : `${m.first_response_median_min} min`}
              </span>
            </>
          }
        />
        <Kpi
          label="Won / lost"
          value={`${m.leads_won ?? 0} / ${m.leads_lost ?? 0}`}
          icon={CheckCircle2}
          tone="bg-teal-50 text-teal-600"
          footer={<span>AI cost ₹{(m.ai_cost_inr ?? 0).toFixed(2)}</span>}
        />
        <div className="card p-5 sm:col-span-2">
          <p className="text-sm font-medium text-slate-500">Open leads by stage</p>
          <div className="mt-3 flex flex-wrap gap-2">
            {openStages.length === 0 && <span className="text-sm text-slate-400">No open leads</span>}
            {openStages.map(([s, n]) => (
              <Pill key={s} tone={stageTone(s)}>
                {pretty(s)} · {n}
              </Pill>
            ))}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <SectionCard
          title="Upcoming visits"
          actions={<button className="btn-ghost px-2 py-1 text-xs" onClick={() => onOpenTab('visits')}>View all</button>}
        >
          {d.visits_upcoming.length === 0 ? (
            <Empty title="No visits planned this week" />
          ) : (
            <ul className="divide-y divide-slate-100">
              {d.visits_upcoming.map((v) => (
                <li key={v.id} className="flex items-center justify-between gap-3 px-5 py-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-slate-900">{v.customer_name || v.customer_phone}</p>
                    <p className="truncate text-xs text-slate-500">
                      {fmtDay(v.date)}
                      {v.time_window ? ` · ${v.time_window}` : ''}
                      {v.interest ? ` · ${v.interest}` : ''}
                    </p>
                  </div>
                  <Pill tone={statusTone(v.status)}>{pretty(v.status)}</Pill>
                </li>
              ))}
            </ul>
          )}
        </SectionCard>

        <SectionCard
          title="Pending approvals"
          actions={<button className="btn-ghost px-2 py-1 text-xs" onClick={() => onOpenTab('approvals')}>Review</button>}
        >
          {approvals.error ? (
            <div className="p-4"><ErrorBox message={approvals.error} /></div>
          ) : !approvals.data ? (
            <Loading />
          ) : approvals.data.items.length === 0 ? (
            <Empty title="Nothing waiting for approval" />
          ) : (
            <ul className="divide-y divide-slate-100">
              {approvals.data.items.slice(0, 8).map((a) => (
                <li key={a.id} className="px-5 py-3">
                  <div className="flex items-center justify-between gap-2">
                    <p className="truncate text-sm font-medium text-slate-900">{a.customer_name || a.customer_phone || 'Customer'}</p>
                    <span className="text-xs text-slate-500">{fmtPct(a.confidence)} · {pretty(a.task || a.kind)}</span>
                  </div>
                  <p className="mt-0.5 line-clamp-2 text-xs text-slate-500">{a.proposed_text}</p>
                </li>
              ))}
            </ul>
          )}
        </SectionCard>

        <SectionCard
          title="Follow-ups needing attention"
          actions={<button className="btn-ghost px-2 py-1 text-xs" onClick={() => onOpenTab('followups')}>View all</button>}
        >
          {d.followups_attention.length === 0 ? (
            <Empty title="No follow-ups need attention" />
          ) : (
            <ul className="divide-y divide-slate-100">
              {d.followups_attention.map((t) => (
                <li key={t.id} className="flex items-center justify-between gap-3 px-5 py-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-slate-900">{t.customer_name || t.customer_phone}</p>
                    <p className="truncate text-xs text-slate-500">{pretty(t.kind)} · due {fmtDateTime(t.due_at)}</p>
                  </div>
                  <Pill tone={statusTone(t.status)}>{pretty(t.status)}</Pill>
                </li>
              ))}
            </ul>
          )}
        </SectionCard>

        <SectionCard
          title={<span className="inline-flex items-center gap-1.5"><Flame className="h-4 w-4 text-orange-500" /> Hot leads</span>}
          actions={<button className="btn-ghost px-2 py-1 text-xs" onClick={() => onOpenTab('leads')}>Pipeline</button>}
        >
          {d.hot_leads.length === 0 ? (
            <Empty title="No open leads" />
          ) : (
            <ul className="divide-y divide-slate-100">
              {d.hot_leads.map((l) => (
                <li key={l.id} className="flex items-center justify-between gap-3 px-5 py-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-slate-900">{l.customer_name || l.customer_phone}</p>
                    <p className="truncate text-xs text-slate-500">{l.interest || 'Interest unknown'} · score {l.score ?? 0}</p>
                  </div>
                  <Pill tone={stageTone(l.stage)}>{pretty(l.stage)}</Pill>
                </li>
              ))}
            </ul>
          )}
        </SectionCard>
      </div>
    </div>
  );
};
