import React from 'react';
import { Inbox } from 'lucide-react';
import { DashboardMessageItem } from '../types';

interface ConversationsListProps {
  messages: DashboardMessageItem[];
  selectedCustomerPhone: string | null;
  onSelectCustomerPhone: (phone: string) => void;
}

const AVATAR_TONES = [
  'bg-brand-100 text-brand-700',
  'bg-violet-100 text-violet-700',
  'bg-emerald-100 text-emerald-700',
  'bg-amber-100 text-amber-700',
  'bg-rose-100 text-rose-700',
  'bg-cyan-100 text-cyan-700',
];

export const avatarTone = (seed: string) => {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) | 0;
  return AVATAR_TONES[Math.abs(h) % AVATAR_TONES.length];
};

export const initialsOf = (name: string) =>
  name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]!.toUpperCase())
    .join('') || '?';

const formatTime = (iso: string) => {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const today = new Date().toDateString() === d.toDateString();
  return today
    ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : d.toLocaleDateString([], { day: '2-digit', month: 'short' });
};

export const ConversationsList: React.FC<ConversationsListProps> = ({
  messages,
  selectedCustomerPhone,
  onSelectCustomerPhone,
}) => {
  const threadMap = new Map<string, { customerName: string; customerPhone: string; lastMessage: DashboardMessageItem; count: number }>();

  for (const m of messages) {
    const phone = m.customer_phone || (m.direction === 'INBOUND' ? m.sender_phone : m.recipient_phone);
    const entry = threadMap.get(phone);
    if (!entry) {
      threadMap.set(phone, { customerName: m.customer_name || 'Unknown contact', customerPhone: phone, lastMessage: m, count: 1 });
    } else {
      entry.count += 1;
    }
  }
  const threads = Array.from(threadMap.values());

  return (
    <section className="card flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3.5">
        <h2 className="text-sm font-semibold text-slate-900">Conversations</h2>
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">{threads.length}</span>
      </div>

      <div className="flex-1 overflow-y-auto">
        {threads.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
            <Inbox className="h-8 w-8 text-slate-300" />
            <p className="text-sm text-slate-500">No conversations match the current filters.</p>
          </div>
        ) : (
          <ul className="divide-y divide-slate-100">
            {threads.map((t) => {
              const selected = selectedCustomerPhone === t.customerPhone;
              const pending = t.lastMessage.ai_status === 'NEEDS_REVIEW';
              return (
                <li key={t.customerPhone}>
                  <button
                    onClick={() => onSelectCustomerPhone(t.customerPhone)}
                    className={`relative flex w-full gap-3 px-4 py-3 text-left transition ${
                      selected ? 'bg-brand-50/70' : 'hover:bg-slate-50'
                    }`}
                  >
                    {selected && <span className="absolute inset-y-0 left-0 w-0.5 bg-brand-600" />}
                    <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-sm font-semibold ${avatarTone(t.customerName)}`}>
                      {initialsOf(t.customerName)}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-baseline justify-between gap-2">
                        <span className={`truncate text-sm ${selected ? 'font-semibold text-slate-900' : 'font-medium text-slate-800'}`}>
                          {t.customerName}
                        </span>
                        <span className="shrink-0 text-xs text-slate-400">{formatTime(t.lastMessage.created_at)}</span>
                      </div>
                      <p className="mt-0.5 truncate text-sm text-slate-500">
                        {t.lastMessage.direction === 'OUTBOUND' && <span className="text-slate-400">You: </span>}
                        {t.lastMessage.content}
                      </p>
                      <div className="mt-1 flex items-center gap-2 text-xs text-slate-400">
                        <span className="truncate">via {t.lastMessage.worker_name}</span>
                        {pending && <span className="rounded bg-amber-50 px-1.5 py-px font-medium text-amber-700">Review</span>}
                        <span className="ml-auto rounded-full bg-slate-100 px-1.5 text-slate-500">{t.count}</span>
                      </div>
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </section>
  );
};

export const prettyRole = (role: string) => {
  const s = role.replace(/_/g, ' ').toLowerCase();
  return s.charAt(0).toUpperCase() + s.slice(1);
};
