import React, { useState } from 'react';
import { Check, PenLine, X, MessageCircleQuestion, Bot, Timer } from 'lucide-react';
import { Approval } from '../../types';
import { decideApproval, fetchApprovals } from '../../services/api';
import { Empty, ErrorBox, Loading, Pill, fmtDateTime, fmtPct, pretty, statusTone, useAsync, useOnTick } from './ui';

interface Props {
  tick: number;
  onPendingChange: (n: number) => void;
}

export const ApprovalsView: React.FC<Props> = ({ tick, onPendingChange }) => {
  const [showDecided, setShowDecided] = useState(false);
  const list = useAsync(async () => {
    const r = await fetchApprovals(showDecided ? 'APPROVED,EDITED,REJECTED,EXPIRED,SUPERSEDED' : 'PENDING');
    onPendingChange(r.pending);
    return r;
  }, [showDecided]);
  useOnTick(tick, () => list.reload(true));

  const remove = (id: string) => {
    list.setData((d) => (d ? { ...d, pending: Math.max(0, d.pending - 1), items: d.items.filter((a) => a.id !== id) } : d));
    if (list.data) onPendingChange(Math.max(0, list.data.pending - 1));
  };

  return (
    <div className="space-y-4">
      <div className="card flex flex-wrap items-center gap-3 p-3">
        <div className="inline-flex rounded-lg border border-slate-200 bg-slate-50 p-0.5">
          {[false, true].map((d) => (
            <button
              key={String(d)}
              onClick={() => setShowDecided(d)}
              className={`rounded-md px-3 py-1.5 text-sm font-medium transition ${showDecided === d ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-800'}`}
            >
              {d ? 'Recently decided' : `Pending${list.data ? ` (${list.data.pending})` : ''}`}
            </button>
          ))}
        </div>
        <p className="text-xs text-slate-500">AI drafts wait here until a human approves, edits or rejects them. Decisions teach the agent.</p>
      </div>

      {list.error && <ErrorBox message={list.error} onRetry={() => list.reload()} />}
      {list.loading && !list.data ? (
        <Loading />
      ) : !list.data || list.data.items.length === 0 ? (
        <div className="card"><Empty title={showDecided ? 'No decided approvals' : 'Inbox zero — nothing to approve'} /></div>
      ) : (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          {list.data.items.map((a) => (
            <ApprovalCard key={a.id} approval={a} readOnly={showDecided} onDecided={() => remove(a.id)} />
          ))}
        </div>
      )}
    </div>
  );
};

const ApprovalCard: React.FC<{ approval: Approval; readOnly: boolean; onDecided: () => void }> = ({ approval: a, readOnly, onDecided }) => {
  const [mode, setMode] = useState<'view' | 'edit' | 'reject'>('view');
  const [text, setText] = useState(a.proposed_text || '');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const conf = a.confidence ?? null;
  const confTone = conf === null ? 'bg-slate-100 text-slate-600' : conf >= 0.8 ? 'bg-emerald-50 text-emerald-700' : conf >= 0.6 ? 'bg-amber-50 text-amber-800' : 'bg-rose-50 text-rose-700';
  const slaPassed = a.sla_due_at && new Date(a.sla_due_at) < new Date();

  const decide = async (body: Parameters<typeof decideApproval>[1]) => {
    setBusy(true);
    setErr(null);
    try {
      await decideApproval(a.id, body);
      onDecided();
    } catch (e: any) {
      setErr(e.message);
      setBusy(false);
    }
  };

  return (
    <article className="card flex flex-col">
      <header className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-5 py-3">
        <span className="text-sm font-semibold text-slate-900">{a.customer_name || a.customer_phone || 'Customer'}</span>
        {a.customer_phone && <span className="text-xs text-slate-500">{a.customer_phone}</span>}
        <span className="ml-auto flex flex-wrap items-center gap-1.5">
          <Pill>{pretty(a.task || a.kind)}</Pill>
          {a.topic && <Pill tone="bg-brand-50 text-brand-700">{a.topic}</Pill>}
          <Pill tone={confTone} title="Agent confidence">{fmtPct(conf)}</Pill>
          {readOnly && <Pill tone={statusTone(a.status)}>{pretty(a.status)}</Pill>}
        </span>
      </header>
      <div className="flex-1 space-y-3 px-5 py-4">
        {a.question_text && (
          <div className="flex gap-2">
            <MessageCircleQuestion className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
            <p className="rounded-xl bg-slate-100 px-3 py-2 text-sm text-slate-800">{a.question_text}</p>
          </div>
        )}
        <div className="flex gap-2">
          <Bot className="mt-0.5 h-4 w-4 shrink-0 text-brand-500" />
          {mode === 'edit' ? (
            <textarea className="field min-h-[110px]" maxLength={4096} value={text} onChange={(e) => setText(e.target.value)} />
          ) : (
            <p className="whitespace-pre-wrap rounded-xl bg-brand-50 px-3 py-2 text-sm text-slate-800 ring-1 ring-brand-100">
              {readOnly && a.final_text ? a.final_text : a.proposed_text}
            </p>
          )}
        </div>
        {a.reasons.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {a.reasons.map((r) => (
              <Pill key={r} tone="bg-amber-50 text-amber-800 ring-1 ring-amber-200">{pretty(r)}</Pill>
            ))}
          </div>
        )}
        <p className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
          <Timer className="h-3.5 w-3.5" /> Created {fmtDateTime(a.created_at)}
          {a.sla_due_at && <span className={slaPassed ? 'text-rose-600' : ''}>· SLA {fmtDateTime(a.sla_due_at)}</span>}
          {a.holding_sent_at && <span>· holding reply sent</span>}
          {a.template_name && <span>· template {a.template_name}</span>}
          {readOnly && a.reviewer && <span>· by {a.reviewer} {fmtDateTime(a.decided_at)}</span>}
          {readOnly && a.reject_reason && <span>· “{a.reject_reason}”</span>}
        </p>
        {mode === 'reject' && (
          <input className="field" placeholder="Why is this draft wrong? (required)" maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} autoFocus />
        )}
        {err && <ErrorBox message={err} />}
      </div>
      {!readOnly && (
        <footer className="flex flex-wrap items-center justify-end gap-2 border-t border-slate-100 px-5 py-3">
          {mode === 'view' && (
            <>
              <button className="btn-ghost text-rose-700 hover:bg-rose-50" disabled={busy} onClick={() => setMode('reject')}>
                <X className="h-4 w-4" /> Reject
              </button>
              <button className="btn-secondary" disabled={busy} onClick={() => setMode('edit')}>
                <PenLine className="h-4 w-4" /> Edit
              </button>
              <button className="btn-primary" disabled={busy} onClick={() => decide({ decision: 'approve' })}>
                <Check className="h-4 w-4" /> Approve &amp; send
              </button>
            </>
          )}
          {mode === 'edit' && (
            <>
              <button className="btn-ghost" disabled={busy} onClick={() => { setMode('view'); setText(a.proposed_text || ''); setErr(null); }}>Cancel</button>
              <button className="btn-primary" disabled={busy || !text.trim()} onClick={() => decide({ decision: 'edit', text: text.trim() })}>
                <Check className="h-4 w-4" /> Send edited
              </button>
            </>
          )}
          {mode === 'reject' && (
            <>
              <button className="btn-ghost" disabled={busy} onClick={() => { setMode('view'); setErr(null); }}>Cancel</button>
              <button className="btn bg-rose-600 text-white hover:bg-rose-700" disabled={busy || !reason.trim()} onClick={() => decide({ decision: 'reject', reason: reason.trim() })}>
                <X className="h-4 w-4" /> Reject draft
              </button>
            </>
          )}
        </footer>
      )}
    </article>
  );
};
