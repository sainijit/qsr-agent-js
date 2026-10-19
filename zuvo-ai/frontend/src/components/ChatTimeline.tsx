import React from 'react';
import { CheckCheck, Copy, Check, MessagesSquare, Smartphone } from 'lucide-react';
import { DashboardMessageItem } from '../types';
import { avatarTone, initialsOf } from './ConversationsList';

interface ChatTimelineProps {
  messages: DashboardMessageItem[];
  selectedCustomerPhone: string | null;
}

const INTENTS: Record<string, { label: string; tone: string }> = {
  CREATE_ORDER: { label: 'Order', tone: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20' },
  PAYMENT_COMMITMENT: { label: 'Payment promise', tone: 'bg-brand-50 text-brand-700 ring-brand-600/20' },
  INQUIRY_PRICE_STOCK: { label: 'Price / stock inquiry', tone: 'bg-violet-50 text-violet-700 ring-violet-600/20' },
  COMPLAINT_RETURN: { label: 'Complaint / return', tone: 'bg-rose-50 text-rose-700 ring-rose-600/20' },
  GENERAL_CHAT: { label: 'General', tone: 'bg-slate-100 text-slate-600 ring-slate-500/20' },
};

const pill = 'inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset';

const timeOf = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
};

const dayOf = (iso: string) => {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return 'Today';
  if (d.toDateString() === yesterday.toDateString()) return 'Yesterday';
  return d.toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' });
};

export const ChatTimeline: React.FC<ChatTimelineProps> = ({ messages, selectedCustomerPhone }) => {
  const [copiedId, setCopiedId] = React.useState<string | null>(null);
  const bottomRef = React.useRef<HTMLDivElement>(null);

  const display = selectedCustomerPhone
    ? messages.filter((m) => (m.customer_phone || (m.direction === 'INBOUND' ? m.sender_phone : m.recipient_phone)) === selectedCustomerPhone)
    : messages;
  const sorted = [...display].reverse();
  const title = selectedCustomerPhone ? sorted[0]?.customer_name || selectedCustomerPhone : 'All conversations';

  React.useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' });
  }, [sorted.length, selectedCustomerPhone]);

  const copyWamid = (wamid: string) => {
    navigator.clipboard?.writeText(wamid);
    setCopiedId(wamid);
    setTimeout(() => setCopiedId(null), 1500);
  };

  let lastDay = '';

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-3 border-b border-slate-200 px-5 py-3">
        {selectedCustomerPhone ? (
          <div className={`flex h-9 w-9 items-center justify-center rounded-full text-sm font-semibold ${avatarTone(title)}`}>
            {initialsOf(title)}
          </div>
        ) : (
          <div className="flex h-9 w-9 items-center justify-center rounded-full bg-slate-100 text-slate-500">
            <MessagesSquare className="h-[18px] w-[18px]" />
          </div>
        )}
        <div className="min-w-0">
          <h2 className="truncate text-sm font-semibold text-slate-900">{title}</h2>
          <p className="text-xs text-slate-500">
            {selectedCustomerPhone && <span className="font-mono">+{selectedCustomerPhone} · </span>}
            {sorted.length} message{sorted.length !== 1 ? 's' : ''}
          </p>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto bg-[#f4f6f9] px-5 py-4">
        {sorted.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-white shadow-card">
              <MessagesSquare className="h-6 w-6 text-slate-400" />
            </div>
            <p className="text-sm font-medium text-slate-700">No messages yet</p>
            <p className="max-w-xs text-sm text-slate-500">
              Messages appear here as customers write in. Use <span className="font-medium text-slate-700">Simulate message</span> to test.
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {sorted.map((m) => {
              const inbound = m.direction === 'INBOUND';
              const day = dayOf(m.created_at);
              const showDay = day !== lastDay;
              lastDay = day;
              const intent = m.parsed_intent ? INTENTS[m.parsed_intent] ?? { label: m.parsed_intent, tone: INTENTS.GENERAL_CHAT.tone } : null;
              const review = inbound && (m.ai_status === 'NEEDS_REVIEW' || m.ai_status === 'AI_PENDING');

              return (
                <React.Fragment key={m.id}>
                  {showDay && (
                    <div className="flex justify-center py-1">
                      <span className="rounded-full bg-white px-3 py-1 text-xs font-medium text-slate-500 shadow-card">{day}</span>
                    </div>
                  )}
                  <div className={`flex ${inbound ? 'justify-start' : 'justify-end'}`}>
                    <div className={`group max-w-[80%] ${inbound ? '' : 'text-right'}`}>
                      <div className={`mb-1 flex items-center gap-2 text-xs text-slate-500 ${inbound ? '' : 'justify-end'}`}>
                        <span className="font-medium text-slate-700">{inbound ? m.customer_name || m.sender_phone : 'You'}</span>
                        <span className="inline-flex items-center gap-1">
                          <Smartphone className="h-3 w-3" />
                          {m.worker_name}
                        </span>
                      </div>
                      <div
                        className={`inline-block rounded-2xl px-4 py-2.5 text-left text-sm leading-relaxed shadow-card ${
                          inbound
                            ? 'rounded-tl-md border border-slate-200 bg-white text-slate-800'
                            : 'rounded-tr-md bg-brand-600 text-white'
                        }`}
                      >
                        <p className="whitespace-pre-wrap break-words">{m.content}</p>
                        <div className={`mt-1 flex items-center justify-end gap-1 text-[11px] ${inbound ? 'text-slate-400' : 'text-brand-100'}`}>
                          {timeOf(m.created_at)}
                          {!inbound && <CheckCheck className="h-3.5 w-3.5" />}
                        </div>
                      </div>

                      {(intent || review) && (
                        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                          {intent && <span className={`${pill} ${intent.tone}`}>{intent.label}</span>}
                          {intent && m.confidence_score !== null && (
                            <span className="text-xs text-slate-500 tabular-nums">{(m.confidence_score * 100).toFixed(0)}% confidence</span>
                          )}
                          {review && (
                            <span className={`${pill} bg-amber-50 text-amber-700 ring-amber-600/20`}>
                              {m.ai_status === 'NEEDS_REVIEW' ? 'Needs review' : 'AI processing'}
                            </span>
                          )}
                        </div>
                      )}

                      <button
                        onClick={() => copyWamid(m.wamid)}
                        title={`Copy message ID ${m.wamid}`}
                        className={`mt-1 inline-flex items-center gap-1 font-mono text-[11px] text-slate-400 opacity-0 transition hover:text-slate-600 group-hover:opacity-100 ${inbound ? '' : 'flex-row-reverse'}`}
                      >
                        {copiedId === m.wamid ? <Check className="h-3 w-3 text-emerald-600" /> : <Copy className="h-3 w-3" />}
                        {m.wamid.slice(0, 18)}…
                      </button>
                    </div>
                  </div>
                </React.Fragment>
              );
            })}
            <div ref={bottomRef} />
          </div>
        )}
      </div>
    </div>
  );
};
