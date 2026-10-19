import React, { useState } from 'react';
import { Send, SkipForward, PenLine, Zap } from 'lucide-react';
import { FollowupTask } from '../../types';
import { fetchFollowups, sendFollowupNow, skipFollowup } from '../../services/api';
import { Empty, ErrorBox, Loading, Pill, fmtDateTime, pretty, stageTone, statusTone, useAsync, useOnTick } from './ui';

const FILTERS: { id: string; label: string }[] = [
  { id: 'SCHEDULED,QUEUED,NEEDS_ACTION,AWAITING_APPROVAL,FAILED', label: 'Active' },
  { id: 'NEEDS_ACTION', label: 'Needs action' },
  { id: 'SCHEDULED', label: 'Scheduled' },
  { id: 'AWAITING_APPROVAL', label: 'Awaiting approval' },
  { id: 'FAILED', label: 'Failed' },
  { id: 'SENT,DONE', label: 'Sent' },
  { id: 'SKIPPED,CANCELLED', label: 'Skipped / cancelled' },
  { id: '', label: 'All' },
];
const SKIPPABLE = new Set(['SCHEDULED', 'NEEDS_ACTION', 'AWAITING_APPROVAL', 'FAILED']);
const RERUNNABLE = new Set(['SCHEDULED', 'FAILED']);
const WRITABLE = new Set(['SCHEDULED', 'NEEDS_ACTION', 'FAILED', 'AWAITING_APPROVAL']);

export const FollowupsView: React.FC<{ tick: number }> = ({ tick }) => {
  const [filter, setFilter] = useState(FILTERS[0].id);
  const list = useAsync(() => fetchFollowups(filter || undefined), [filter]);
  useOnTick(tick, () => list.reload(true));
  const counts = list.data?.counts || {};
  const countFor = (id: string) => (id ? id.split(',').reduce((s, k) => s + (counts[k] || 0), 0) : Object.values(counts).reduce((a, b) => a + b, 0));

  return (
    <div className="space-y-4">
      <div className="card flex flex-wrap items-center gap-1.5 p-3">
        {FILTERS.map((f) => (
          <button
            key={f.label}
            onClick={() => setFilter(f.id)}
            className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition ${
              filter === f.id ? 'bg-brand-50 text-brand-700 ring-1 ring-brand-200' : 'text-slate-600 hover:bg-slate-100'
            }`}
          >
            {f.label}
            {list.data && <span className="rounded-full bg-white/80 px-1.5 text-[11px] text-slate-500 ring-1 ring-slate-200">{countFor(f.id)}</span>}
          </button>
        ))}
      </div>

      {list.error && <ErrorBox message={list.error} onRetry={() => list.reload()} />}
      {list.loading && !list.data ? (
        <Loading />
      ) : !list.data || list.data.items.length === 0 ? (
        <div className="card"><Empty title="No follow-ups here" /></div>
      ) : (
        <div className="card divide-y divide-slate-100 overflow-hidden">
          {list.data.items.map((t) => (
            <FollowupRow key={t.id} task={t} onDone={() => list.reload(true)} />
          ))}
        </div>
      )}
    </div>
  );
};

const FollowupRow: React.FC<{ task: FollowupTask; onDone: () => void }> = ({ task: t, onDone }) => {
  const [writing, setWriting] = useState(t.status === 'NEEDS_ACTION');
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const overdue = t.due_at && new Date(t.due_at) < new Date() && ['SCHEDULED', 'NEEDS_ACTION'].includes(t.status);

  // a previous send with unknown outcome may already have reached the customer; resending needs explicit confirmation
  const sendNow = async (body?: string) => {
    try {
      return await sendFollowupNow(t.id, body);
    } catch (e: any) {
      if (!/outcome unknown/i.test(e?.message || '')) throw e;
      if (!window.confirm('The previous send may already have reached the customer (outcome unknown). Check WhatsApp first. Send again anyway?')) return;
      return sendFollowupNow(t.id, body, true);
    }
  };

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setErr(null);
    try {
      await fn();
      setText('');
      onDone();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="px-5 py-4">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-[220px] flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium text-slate-900">{t.customer_name || t.customer_phone}</span>
            <Pill tone={statusTone(t.status)}>{pretty(t.status)}</Pill>
            {t.lead_stage && <Pill tone={stageTone(t.lead_stage)}>{pretty(t.lead_stage)}</Pill>}
            <Pill>{t.mode}</Pill>
          </div>
          <p className="mt-0.5 text-xs text-slate-500">
            {pretty(t.kind)}
            {t.rule_id ? ` · rule ${t.rule_id}` : ''} · attempt {t.attempt}
            {t.interest ? ` · ${t.interest}` : ''}
            {t.template_name ? ` · template ${t.template_name}` : ''}
          </p>
          <p className={`mt-0.5 text-xs ${overdue ? 'font-medium text-rose-600' : 'text-slate-500'}`}>
            Due {fmtDateTime(t.due_at)}
            {overdue ? ' · overdue' : ''}
            {t.completed_at ? ` · completed ${fmtDateTime(t.completed_at)}` : ''}
            {t.skip_reason ? ` · ${t.skip_reason}` : ''}
          </p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {RERUNNABLE.has(t.status) && (
            <button className="btn-secondary px-2.5 py-1.5 text-xs" disabled={busy} onClick={() => run(() => sendNow())} title="Run the automated follow-up now">
              <Zap className="h-3.5 w-3.5" /> Run now
            </button>
          )}
          {WRITABLE.has(t.status) && t.status !== 'NEEDS_ACTION' && (
            <button className="btn-secondary px-2.5 py-1.5 text-xs" disabled={busy} onClick={() => setWriting((w) => !w)}>
              <PenLine className="h-3.5 w-3.5" /> Write
            </button>
          )}
          {SKIPPABLE.has(t.status) && (
            <button className="btn-ghost px-2.5 py-1.5 text-xs" disabled={busy} onClick={() => run(() => skipFollowup(t.id))}>
              <SkipForward className="h-3.5 w-3.5" /> Skip
            </button>
          )}
        </div>
      </div>
      {writing && WRITABLE.has(t.status) && (
        <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-end">
          <textarea
            className="field min-h-[72px] flex-1"
            placeholder={t.status === 'NEEDS_ACTION' ? 'This follow-up needs a human message — write it here…' : 'Write the follow-up message…'}
            maxLength={4096}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <button className="btn-primary" disabled={busy || !text.trim()} onClick={() => run(() => sendNow(text))}>
            <Send className="h-4 w-4" /> Send now
          </button>
        </div>
      )}
      {err && <div className="mt-2"><ErrorBox message={err} /></div>}
    </div>
  );
};
