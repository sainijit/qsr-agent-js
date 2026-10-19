import React, { useState } from 'react';
import { GraduationCap, ToggleLeft, ToggleRight } from 'lucide-react';
import { InsightsResponse } from '../../types';
import { fetchInsights, fetchLearnedExamples, toggleLearnedExample } from '../../services/api';
import { Empty, ErrorBox, Loading, Pill, SectionCard, fmtDateTime, pretty, stageTone, statusTone, useAsync, useOnTick } from './ui';

const FUNNEL_ORDER = ['NEW', 'ENGAGED', 'QUALIFIED', 'VISIT_PLANNED', 'VISITED', 'WON', 'LOST', 'DORMANT'];
const SERIES: { key: string; label: string; color: string }[] = [
  { key: 'leads_new', label: 'New leads', color: 'bg-brand-500' },
  { key: 'messages_in', label: 'Messages in', color: 'bg-sky-500' },
  { key: 'visits_created', label: 'Visits planned', color: 'bg-amber-500' },
  { key: 'agent_drafted', label: 'AI drafts', color: 'bg-violet-500' },
  { key: 'followups_sent', label: 'Follow-ups sent', color: 'bg-teal-500' },
];

export const InsightsView: React.FC<{ tick: number }> = ({ tick }) => {
  const [days, setDays] = useState(7);
  const [metric, setMetric] = useState(SERIES[0].key);
  const ins = useAsync(() => fetchInsights(days), [days]);
  const ex = useAsync(fetchLearnedExamples, []);
  const [exErr, setExErr] = useState<string | null>(null);
  useOnTick(tick, () => {
    ins.reload(true);
    ex.reload(true);
  }, 1500);

  const toggle = async (id: string) => {
    setExErr(null);
    try {
      const r = await toggleLearnedExample(id);
      ex.setData((list) => (list ? list.map((e) => (e.id === id ? { ...e, active: r.active } : e)) : list));
    } catch (e: any) {
      setExErr(e.message);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <div className="inline-flex rounded-lg border border-slate-200 bg-white p-0.5 shadow-sm">
          {[7, 30].map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`rounded-md px-3 py-1.5 text-sm font-medium transition ${days === d ? 'bg-brand-600 text-white' : 'text-slate-600 hover:bg-slate-100'}`}
            >
              Last {d} days
            </button>
          ))}
        </div>
      </div>

      {ins.error && <ErrorBox message={ins.error} onRetry={() => ins.reload()} />}
      {ins.loading && !ins.data ? <Loading /> : ins.data && <InsightsBody data={ins.data} metric={metric} setMetric={setMetric} />}

      <SectionCard title={<span className="inline-flex items-center gap-1.5"><GraduationCap className="h-4 w-4 text-brand-600" /> Learned examples</span>}>
        {exErr && <div className="p-4"><ErrorBox message={exErr} /></div>}
        {ex.error ? (
          <div className="p-4"><ErrorBox message={ex.error} onRetry={() => ex.reload()} /></div>
        ) : ex.loading && !ex.data ? (
          <Loading />
        ) : !ex.data || ex.data.length === 0 ? (
          <Empty title="No learned examples yet" hint="Edited and rejected drafts become examples the agent learns from." />
        ) : (
          <ul className="divide-y divide-slate-100">
            {ex.data.map((e) => (
              <li key={e.id} className={`flex gap-4 px-5 py-4 ${e.active ? '' : 'opacity-60'}`}>
                <div className="min-w-0 flex-1 space-y-1.5">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Pill tone={statusTone(e.outcome)}>{pretty(e.outcome)}</Pill>
                    <Pill>{e.task}</Pill>
                    {e.topic && <Pill tone="bg-brand-50 text-brand-700">{e.topic}</Pill>}
                    <span className="text-xs text-slate-400">weight {e.weight ?? '—'} · used {e.use_count}× · {fmtDateTime(e.created_at)}</span>
                  </div>
                  {e.question && <p className="text-sm text-slate-600"><span className="font-medium text-slate-800">Q:</span> {e.question}</p>}
                  {e.answer && <p className="whitespace-pre-wrap text-sm text-slate-600"><span className="font-medium text-slate-800">A:</span> {e.answer}</p>}
                  {e.reject_reason && <p className="text-sm text-rose-700">Rejected: {e.reject_reason}</p>}
                </div>
                <button
                  className="btn-ghost shrink-0 self-start px-2"
                  onClick={() => toggle(e.id)}
                  disabled={e.outcome === 'REJECTED'}
                  title={e.outcome === 'REJECTED' ? 'Rejected examples are kept as negative examples' : e.active ? 'Disable' : 'Enable'}
                >
                  {e.active ? <ToggleRight className="h-6 w-6 text-brand-600" /> : <ToggleLeft className="h-6 w-6 text-slate-400" />}
                </button>
              </li>
            ))}
          </ul>
        )}
      </SectionCard>
    </div>
  );
};

const InsightsBody: React.FC<{ data: InsightsResponse; metric: string; setMetric: (k: string) => void }> = ({ data, metric, setMetric }) => {
  const funnelStages = [...FUNNEL_ORDER.filter((s) => s in data.funnel), ...Object.keys(data.funnel).filter((s) => !FUNNEL_ORDER.includes(s))];
  const funnelMax = Math.max(1, ...Object.values(data.funnel));
  const seriesDef = SERIES.find((s) => s.key === metric) || SERIES[0];
  const values = data.series.map((p) => ({ date: p.date, v: Number(p[metric] ?? 0) || 0 }));
  const sMax = Math.max(1, ...values.map((x) => x.v));
  const sourceMax = Math.max(1, ...Object.values(data.by_source));
  const decided = Object.entries(data.approvals);
  const decidedTotal = decided.reduce((s, [, n]) => s + n, 0);

  return (
    <>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <SectionCard title="Lead funnel (created in period)">
          {funnelStages.length === 0 ? (
            <Empty title="No leads in this period" />
          ) : (
            <div className="space-y-2 p-5">
              {funnelStages.map((s) => (
                <div key={s} className="flex items-center gap-3">
                  <span className="w-28 shrink-0"><Pill tone={stageTone(s)}>{pretty(s)}</Pill></span>
                  <div className="h-6 flex-1 rounded-md bg-slate-100">
                    <div className="h-6 rounded-md bg-brand-500/80" style={{ width: `${(data.funnel[s] / funnelMax) * 100}%` }} />
                  </div>
                  <span className="w-8 text-right text-sm tabular-nums text-slate-700">{data.funnel[s]}</span>
                </div>
              ))}
            </div>
          )}
        </SectionCard>

        <SectionCard
          title="Daily activity"
          actions={
            <select className="field w-auto py-1 text-xs" value={metric} onChange={(e) => setMetric(e.target.value)} aria-label="Series">
              {SERIES.map((s) => (
                <option key={s.key} value={s.key}>{s.label}</option>
              ))}
            </select>
          }
        >
          {values.length === 0 ? (
            <Empty title="No daily metrics yet" hint="Daily rollups are computed by the scheduler." />
          ) : (
            <div className="p-5">
              <div className="flex h-40 items-end gap-1">
                {values.map((x) => (
                  <div key={x.date} className="group relative flex h-full flex-1 flex-col justify-end" title={`${x.date}: ${x.v}`}>
                    <div className={`${seriesDef.color} min-h-[2px] rounded-t`} style={{ height: `${(x.v / sMax) * 100}%` }} />
                    <span className="pointer-events-none absolute -top-5 left-1/2 hidden -translate-x-1/2 rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-white group-hover:block">
                      {x.v}
                    </span>
                  </div>
                ))}
              </div>
              <div className="mt-1 flex justify-between text-[10px] text-slate-400">
                <span>{values[0].date}</span>
                <span>{values[values.length - 1].date}</span>
              </div>
            </div>
          )}
        </SectionCard>

        <SectionCard title="Leads by source">
          {Object.keys(data.by_source).length === 0 ? (
            <Empty title="No leads in this period" />
          ) : (
            <div className="space-y-2 p-5">
              {Object.entries(data.by_source).map(([s, n]) => (
                <div key={s} className="flex items-center gap-3 text-sm">
                  <span className="w-28 shrink-0 truncate text-slate-600">{pretty(s)}</span>
                  <div className="h-4 flex-1 rounded bg-slate-100">
                    <div className="h-4 rounded bg-sky-500/80" style={{ width: `${(n / sourceMax) * 100}%` }} />
                  </div>
                  <span className="w-8 text-right tabular-nums">{n}</span>
                </div>
              ))}
            </div>
          )}
        </SectionCard>

        <SectionCard title="Approval decisions">
          {decided.length === 0 ? (
            <Empty title="No decisions in this period" />
          ) : (
            <div className="space-y-3 p-5">
              <div className="flex h-3 overflow-hidden rounded-full bg-slate-100">
                {decided.map(([s, n]) => (
                  <div
                    key={s}
                    className={s === 'APPROVED' ? 'bg-emerald-500' : s === 'EDITED' ? 'bg-brand-500' : s === 'REJECTED' ? 'bg-rose-500' : 'bg-slate-400'}
                    style={{ width: `${(n / decidedTotal) * 100}%` }}
                  />
                ))}
              </div>
              <div className="flex flex-wrap gap-2">
                {decided.map(([s, n]) => (
                  <Pill key={s} tone={statusTone(s)}>{pretty(s)} · {n}</Pill>
                ))}
              </div>
              {Object.keys(data.learning.examples).length > 0 && (
                <p className="text-xs text-slate-500">
                  Learned examples: {Object.entries(data.learning.examples).map(([k, v]) => `${pretty(k)} ${v}`).join(' · ')}
                </p>
              )}
            </div>
          )}
        </SectionCard>
      </div>

      <SectionCard title="Campaigns">
        {data.campaigns.length === 0 ? (
          <Empty title="No campaigns tracked" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-5 py-2 font-semibold">Campaign</th>
                  <th className="px-5 py-2 font-semibold">Platform</th>
                  <th className="px-5 py-2 text-right font-semibold">Leads</th>
                  <th className="px-5 py-2 text-right font-semibold">Visits</th>
                  <th className="px-5 py-2 text-right font-semibold">Won</th>
                  <th className="px-5 py-2 text-right font-semibold">Visit rate</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {data.campaigns.map((c) => (
                  <tr key={c.id}>
                    <td className="px-5 py-2.5 font-medium text-slate-900">{c.name || c.id}</td>
                    <td className="px-5 py-2.5 text-slate-600">{pretty(c.platform)}</td>
                    <td className="px-5 py-2.5 text-right tabular-nums">{c.leads}</td>
                    <td className="px-5 py-2.5 text-right tabular-nums">{c.visits}</td>
                    <td className="px-5 py-2.5 text-right tabular-nums">{c.won}</td>
                    <td className="px-5 py-2.5 text-right tabular-nums">{c.leads ? `${Math.round((c.visits / c.leads) * 100)}%` : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>

      <SectionCard title="Agent autonomy by topic">
        {data.learning.autonomy.length === 0 ? (
          <Empty title="No autonomy stats yet" hint="Stats accumulate as drafts are approved, edited or rejected." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-5 py-2 font-semibold">Task</th>
                  <th className="px-5 py-2 font-semibold">Topic</th>
                  <th className="px-5 py-2 text-right font-semibold">Approved</th>
                  <th className="px-5 py-2 text-right font-semibold">Edited</th>
                  <th className="px-5 py-2 text-right font-semibold">Rejected</th>
                  <th className="px-5 py-2 text-right font-semibold">Recent</th>
                  <th className="px-5 py-2 font-semibold">Mode</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {data.learning.autonomy.map((a) => (
                  <tr key={`${a.task}:${a.topic}`}>
                    <td className="px-5 py-2.5 text-slate-700">{a.task}</td>
                    <td className="px-5 py-2.5 font-medium text-slate-900">{a.topic}</td>
                    <td className="px-5 py-2.5 text-right tabular-nums">{a.approved}</td>
                    <td className="px-5 py-2.5 text-right tabular-nums">{a.edited}</td>
                    <td className="px-5 py-2.5 text-right tabular-nums">{a.rejected}</td>
                    <td className="px-5 py-2.5 text-right tabular-nums">{a.recent}</td>
                    <td className="px-5 py-2.5">
                      {a.auto_enabled ? <Pill tone="bg-emerald-50 text-emerald-700">Auto-send</Pill> : <Pill>Needs approval</Pill>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>
    </>
  );
};
