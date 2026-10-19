import React, { useEffect, useMemo, useState } from 'react';
import { Search, Phone, Megaphone, UserCheck, CalendarDays, Clock, History, MessageSquare, Save } from 'lucide-react';
import { Lead, WorkerRef } from '../../types';
import { fetchLead, fetchLeads, patchLead } from '../../services/api';
import { Drawer, Empty, ErrorBox, Loading, Pill, fmtDateTime, fmtDay, fmtInr, pretty, stageTone, statusTone, useAsync, useOnTick } from './ui';

interface Props {
  tick: number;
  canManage: boolean;
  workers: WorkerRef[];
}

export const LeadsView: React.FC<Props> = ({ tick, canManage, workers }) => {
  const [q, setQ] = useState('');
  const [debouncedQ, setDebouncedQ] = useState('');
  const [source, setSource] = useState('');
  const [openOnly, setOpenOnly] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [knownSources, setKnownSources] = useState<string[]>([]);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q.trim()), 300);
    return () => clearTimeout(t);
  }, [q]);

  const leads = useAsync(() => fetchLeads({ q: debouncedQ || undefined, source: source || undefined, open_only: openOnly }), [debouncedQ, source, openOnly]);
  useOnTick(tick, () => leads.reload(true));

  useEffect(() => {
    if (!leads.data) return;
    setKnownSources((prev) => Array.from(new Set([...prev, ...leads.data!.items.map((l) => l.source || '').filter(Boolean)])).sort());
  }, [leads.data]);

  const stages = useMemo(() => Array.from(new Set(leads.data?.stages || [])), [leads.data]);
  const byStage = useMemo(() => {
    const map: Record<string, Lead[]> = {};
    stages.forEach((s) => (map[s] = []));
    (leads.data?.items || []).forEach((l) => (map[l.stage] ||= []).push(l));
    return map;
  }, [leads.data, stages]);
  const columns = Object.keys(byStage);

  return (
    <div className="space-y-4">
      <div className="card flex flex-wrap items-center gap-3 p-3">
        <div className="relative min-w-[220px] flex-1">
          <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
          <input className="field pl-9" placeholder="Search name, phone or interest" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <select className="field w-auto" value={source} onChange={(e) => setSource(e.target.value)} aria-label="Source">
          <option value="">All sources</option>
          {knownSources.map((s) => (
            <option key={s} value={s}>{pretty(s)}</option>
          ))}
        </select>
        <label className="inline-flex items-center gap-2 text-sm text-slate-600">
          <input type="checkbox" checked={openOnly} onChange={(e) => setOpenOnly(e.target.checked)} className="h-4 w-4 rounded border-slate-300 text-brand-600" />
          Open only
        </label>
        <span className="ml-auto text-xs text-slate-500">{leads.data ? `${leads.data.total} leads` : ''}</span>
      </div>

      {leads.error && <ErrorBox message={leads.error} onRetry={() => leads.reload()} />}
      {leads.loading && !leads.data ? (
        <Loading />
      ) : leads.data && leads.data.items.length === 0 ? (
        <div className="card"><Empty title="No leads match these filters" hint="Leads appear as soon as customers message a WhatsApp line." /></div>
      ) : (
        <div className="flex gap-3 overflow-x-auto pb-2">
          {columns.map((stage) => (
            <div key={stage} className="flex w-64 shrink-0 flex-col rounded-xl border border-slate-200 bg-slate-50/70">
              <div className="flex items-center justify-between px-3 py-2.5">
                <Pill tone={stageTone(stage)}>{pretty(stage)}</Pill>
                <span className="text-xs font-medium text-slate-500">{byStage[stage].length}</span>
              </div>
              <div className="flex max-h-[calc(100vh-17rem)] min-h-[120px] flex-col gap-2 overflow-y-auto px-2 pb-2">
                {byStage[stage].map((l) => (
                  <button
                    key={l.id}
                    onClick={() => setSelected(l.id)}
                    className="card w-full p-3 text-left transition hover:border-brand-300 hover:shadow-pop"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-sm font-medium text-slate-900">{l.customer_name || l.customer_phone}</span>
                      <span className="text-xs tabular-nums text-slate-500">{l.score ?? 0}</span>
                    </div>
                    <p className="mt-0.5 truncate text-xs text-slate-500">{l.interest || 'Interest unknown'}</p>
                    <div className="mt-2 flex flex-wrap items-center gap-1">
                      {l.source && <Pill>{pretty(l.source)}</Pill>}
                      {l.assigned_worker_name && <Pill tone="bg-brand-50 text-brand-700">{l.assigned_worker_name}</Pill>}
                      {l.opted_out && <Pill tone="bg-rose-50 text-rose-700">Opted out</Pill>}
                    </div>
                    <p className="mt-2 text-[11px] text-slate-400">Updated {fmtDateTime(l.updated_at)}</p>
                  </button>
                ))}
                {byStage[stage].length === 0 && <p className="px-2 py-4 text-center text-xs text-slate-400">No leads</p>}
              </div>
            </div>
          ))}
        </div>
      )}

      <LeadDrawer
        leadId={selected}
        stages={stages}
        tick={tick}
        canManage={canManage}
        workers={workers}
        onClose={() => setSelected(null)}
        onChanged={() => leads.reload(true)}
      />
    </div>
  );
};

interface DrawerProps {
  leadId: string | null;
  stages: string[];
  tick: number;
  canManage: boolean;
  workers: WorkerRef[];
  onClose: () => void;
  onChanged: () => void;
}

export const LeadDrawer: React.FC<DrawerProps> = ({ leadId, stages, tick, canManage, workers, onClose, onChanged }) => {
  const detail = useAsync(() => (leadId ? fetchLead(leadId) : Promise.resolve(null)), [leadId]);
  useOnTick(tick, () => leadId && detail.reload(true));
  const [stage, setStage] = useState('');
  const [lostReason, setLostReason] = useState('');
  const [interest, setInterest] = useState('');
  const [worker, setWorker] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  const lead = detail.data?.lead;
  useEffect(() => {
    if (!lead) return;
    setStage(lead.stage);
    setInterest(lead.interest || '');
    setWorker(lead.assigned_worker_id || '');
    setLostReason(lead.lost_reason || '');
  }, [lead?.id, lead?.stage, lead?.interest, lead?.assigned_worker_id]);
  useEffect(() => {
    setErr(null);
    setOk(null);
  }, [leadId]);

  const apply = async (body: Parameters<typeof patchLead>[1], label: string) => {
    if (!leadId) return;
    setBusy(true);
    setErr(null);
    setOk(null);
    try {
      await patchLead(leadId, body);
      setOk(label);
      await detail.reload(true);
      onChanged();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  const saveStage = () => {
    if (stage === 'LOST' && !lostReason.trim()) {
      setErr('A lost reason is required to mark a lead as LOST.');
      return;
    }
    apply(stage === 'LOST' ? { stage, lost_reason: lostReason.trim() } : { stage }, `Stage set to ${pretty(stage)}`);
  };

  const stageOptions = Array.from(new Set([...stages, 'WON', 'LOST', 'DORMANT', ...(lead ? [lead.stage] : [])]));

  return (
    <Drawer
      open={!!leadId}
      onClose={onClose}
      title={
        lead ? (
          <div>
            <div className="flex items-center gap-2">
              <h2 className="truncate text-base font-semibold text-slate-900">{lead.customer_name || lead.customer_phone}</h2>
              <Pill tone={stageTone(lead.stage)}>{pretty(lead.stage)}</Pill>
            </div>
            <p className="mt-0.5 flex items-center gap-1 text-xs text-slate-500">
              <Phone className="h-3 w-3" /> {lead.customer_phone} · score {lead.score ?? 0}
              {lead.opted_out && <span className="ml-1 text-rose-600">· opted out</span>}
            </p>
          </div>
        ) : (
          <span className="text-sm text-slate-500">Lead</span>
        )
      }
    >
      {detail.loading && !detail.data ? (
        <Loading />
      ) : detail.error ? (
        <div className="p-5"><ErrorBox message={detail.error} onRetry={() => detail.reload()} /></div>
      ) : detail.data && lead ? (
        <div className="space-y-6 p-5">
          {err && <ErrorBox message={err} />}
          {ok && <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-2 text-sm text-emerald-800">{ok}</div>}

          <section className="space-y-3">
            <h3 className="eyebrow">Manage</h3>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div>
                <label className="label" htmlFor="lead-stage">Stage</label>
                <div className="flex gap-2">
                  <select id="lead-stage" className="field" value={stage} onChange={(e) => setStage(e.target.value)}>
                    {stageOptions.map((s) => (
                      <option key={s} value={s}>{pretty(s)}</option>
                    ))}
                  </select>
                  <button className="btn-primary" disabled={busy || stage === lead.stage} onClick={saveStage}>Set</button>
                </div>
              </div>
              {canManage && (
                <div>
                  <label className="label" htmlFor="lead-worker">Assigned to</label>
                  <div className="flex gap-2">
                    <select id="lead-worker" className="field" value={worker} onChange={(e) => setWorker(e.target.value)}>
                      <option value="" disabled>Unassigned</option>
                      {workers.map((w) => (
                        <option key={w.id} value={w.id}>{w.name} · {pretty(w.role)}</option>
                      ))}
                    </select>
                    <button
                      className="btn-secondary"
                      disabled={busy || !worker || worker === lead.assigned_worker_id}
                      onClick={() => apply({ assigned_worker_id: worker }, 'Lead reassigned')}
                    >
                      Assign
                    </button>
                  </div>
                </div>
              )}
            </div>
            {stage === 'LOST' && stage !== lead.stage && (
              <div>
                <label className="label" htmlFor="lost-reason">Lost reason (required)</label>
                <input id="lost-reason" className="field" maxLength={255} value={lostReason} onChange={(e) => setLostReason(e.target.value)} placeholder="e.g. bought elsewhere, price too high" />
              </div>
            )}
            <div>
              <label className="label" htmlFor="lead-interest">Interest</label>
              <div className="flex gap-2">
                <input id="lead-interest" className="field" maxLength={255} value={interest} onChange={(e) => setInterest(e.target.value)} />
                <button className="btn-secondary" disabled={busy || interest === (lead.interest || '')} onClick={() => apply({ interest }, 'Interest updated')}>
                  <Save className="h-4 w-4" />
                </button>
              </div>
            </div>
          </section>

          <section>
            <h3 className="eyebrow mb-2">Details</h3>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
              <Field label="Source" value={pretty(lead.source)} />
              <Field label="Assigned" value={lead.assigned_worker_name || '—'} />
              <Field label="Budget" value={lead.budget_min || lead.budget_max ? `${fmtInr(lead.budget_min)} – ${fmtInr(lead.budget_max)}` : '—'} />
              <Field label="Timeline" value={lead.timeline_text || '—'} />
              <Field label="Location" value={lead.location || '—'} />
              <Field label="Next follow-up" value={fmtDateTime(lead.next_followup_at)} />
              <Field label="First contact" value={fmtDateTime(lead.first_contact_at)} />
              <Field label="First response" value={fmtDateTime(lead.first_response_at)} />
              {lead.lost_reason && <Field label="Lost reason" value={lead.lost_reason} />}
            </dl>
            {detail.data.campaign && (
              <div className="mt-3 flex items-center gap-2 rounded-lg bg-slate-50 px-3 py-2 text-sm">
                <Megaphone className="h-4 w-4 text-brand-600" />
                <span className="font-medium text-slate-800">{detail.data.campaign.name || detail.data.campaign.external_id}</span>
                <span className="text-xs text-slate-500">{pretty(detail.data.campaign.platform)}</span>
              </div>
            )}
            {detail.data.referral && Object.keys(detail.data.referral).length > 0 && (
              <details className="mt-2 text-xs text-slate-500">
                <summary className="cursor-pointer">Ad referral</summary>
                <pre className="mt-1 overflow-x-auto rounded-lg bg-slate-50 p-2 font-mono">{JSON.stringify(detail.data.referral, null, 2)}</pre>
              </details>
            )}
          </section>

          <section>
            <h3 className="eyebrow mb-2 flex items-center gap-1.5"><CalendarDays className="h-3.5 w-3.5" /> Visits</h3>
            {detail.data.visits.length === 0 ? (
              <p className="text-sm text-slate-400">No visits</p>
            ) : (
              <ul className="space-y-2">
                {detail.data.visits.map((v) => (
                  <li key={v.id} className="flex items-center justify-between rounded-lg border border-slate-100 px-3 py-2 text-sm">
                    <span>{fmtDay(v.date)}{v.time_window ? ` · ${v.time_window}` : ''}</span>
                    <Pill tone={statusTone(v.status)}>{pretty(v.status)}</Pill>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section>
            <h3 className="eyebrow mb-2 flex items-center gap-1.5"><Clock className="h-3.5 w-3.5" /> Follow-ups</h3>
            {detail.data.followups.length === 0 ? (
              <p className="text-sm text-slate-400">No follow-ups</p>
            ) : (
              <ul className="space-y-2">
                {detail.data.followups.map((t) => (
                  <li key={t.id} className="flex items-center justify-between gap-2 rounded-lg border border-slate-100 px-3 py-2 text-sm">
                    <span className="min-w-0 truncate">{pretty(t.kind)} · {fmtDateTime(t.due_at)}{t.skip_reason ? ` · ${t.skip_reason}` : ''}</span>
                    <Pill tone={statusTone(t.status)}>{pretty(t.status)}</Pill>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section>
            <h3 className="eyebrow mb-2 flex items-center gap-1.5"><MessageSquare className="h-3.5 w-3.5" /> Conversation</h3>
            {detail.data.messages.length === 0 ? (
              <p className="text-sm text-slate-400">No messages</p>
            ) : (
              <ul className="space-y-2">
                {detail.data.messages.map((m) => {
                  const inbound = m.direction === 'INBOUND';
                  return (
                    <li key={m.id} className={`flex ${inbound ? 'justify-start' : 'justify-end'}`}>
                      <div className={`max-w-[85%] rounded-xl px-3 py-2 text-sm ${inbound ? 'bg-slate-100 text-slate-800' : 'bg-brand-600 text-white'}`}>
                        <p className="whitespace-pre-wrap break-words">{m.body}</p>
                        <p className={`mt-1 text-[10px] ${inbound ? 'text-slate-500' : 'text-brand-100'}`}>
                          {fmtDateTime(m.created_at)}{m.sent_by ? ` · ${m.sent_by}` : ''}{m.intent ? ` · ${m.intent}` : ''}
                        </p>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          <section>
            <h3 className="eyebrow mb-2 flex items-center gap-1.5"><History className="h-3.5 w-3.5" /> Timeline</h3>
            {detail.data.events.length === 0 ? (
              <p className="text-sm text-slate-400">No events</p>
            ) : (
              <ol className="relative space-y-3 border-l border-slate-200 pl-4">
                {detail.data.events.map((e) => (
                  <li key={e.id} className="relative text-sm">
                    <span className="absolute -left-[21.5px] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-white bg-brand-500" />
                    <p className="text-slate-800">
                      <span className="font-medium">{pretty(e.type)}</span>
                      {(e.from || e.to) && <span className="text-slate-500"> {e.from || '∅'} → {e.to || '∅'}</span>}
                    </p>
                    <p className="text-xs text-slate-400">
                      {fmtDateTime(e.created_at)}{e.actor ? ` · ${e.actor}` : ''}
                    </p>
                  </li>
                ))}
              </ol>
            )}
          </section>
          {!canManage && (
            <p className="flex items-center gap-1.5 text-xs text-slate-400"><UserCheck className="h-3.5 w-3.5" /> Only owners and managers can reassign leads.</p>
          )}
        </div>
      ) : null}
    </Drawer>
  );
};

const Field: React.FC<{ label: string; value: React.ReactNode }> = ({ label, value }) => (
  <div>
    <dt className="text-xs text-slate-500">{label}</dt>
    <dd className="truncate text-slate-800">{value}</dd>
  </div>
);
