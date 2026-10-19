import React from 'react';
import { Building2, Phone, User, ShieldAlert, ShieldCheck, Smartphone } from 'lucide-react';
import { CustomerRecord, DashboardMessageItem } from '../types';
import { avatarTone, initialsOf } from './ConversationsList';

interface Customer360DrawerProps {
  customer: CustomerRecord | null;
  messages: DashboardMessageItem[];
}

const inr = (v: number) => `₹${v.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;

export const Customer360Drawer: React.FC<Customer360DrawerProps> = ({ customer, messages }) => {
  if (!customer) {
    return (
      <section className="card flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-slate-100">
          <Building2 className="h-6 w-6 text-slate-400" />
        </div>
        <p className="text-sm font-medium text-slate-800">No customer selected</p>
        <p className="max-w-[16rem] text-sm text-slate-500">
          Choose a conversation to see account details, credit standing and staff touchpoints.
        </p>
      </section>
    );
  }

  const limit = customer.credit_limit || 0;
  const outstanding = customer.current_outstanding || 0;
  const available = Math.max(0, limit - outstanding);
  const pct = limit > 0 ? Math.min(100, Math.round((outstanding / limit) * 100)) : 0;
  const breached = limit > 0 && outstanding > limit;
  const high = pct >= 80;
  const barTone = breached ? 'bg-rose-500' : high ? 'bg-amber-500' : 'bg-emerald-500';

  const custMsgs = messages.filter(
    (m) => m.customer_id === customer.id || m.sender_phone === customer.primary_phone || m.recipient_phone === customer.primary_phone
  );
  const touchpoints = Object.entries(
    custMsgs.reduce<Record<string, number>>((acc, m) => {
      if (m.worker_name) acc[m.worker_name] = (acc[m.worker_name] || 0) + 1;
      return acc;
    }, {})
  ).sort((a, b) => b[1] - a[1]);

  const status = customer.is_blocked
    ? { label: 'Blocked', tone: 'bg-rose-50 text-rose-700 ring-rose-600/20', icon: ShieldAlert }
    : breached
    ? { label: 'Over limit', tone: 'bg-rose-50 text-rose-700 ring-rose-600/20', icon: ShieldAlert }
    : { label: 'In good standing', tone: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20', icon: ShieldCheck };
  const StatusIcon = status.icon;

  return (
    <section className="card flex h-full flex-col overflow-hidden">
      <div className="border-b border-slate-200 px-4 py-3.5">
        <h2 className="text-sm font-semibold text-slate-900">Customer</h2>
      </div>

      <div className="flex-1 space-y-6 overflow-y-auto p-5">
        <div className="flex flex-col items-center text-center">
          <div className={`flex h-14 w-14 items-center justify-center rounded-full text-lg font-semibold ${avatarTone(customer.company_name)}`}>
            {initialsOf(customer.company_name)}
          </div>
          <h3 className="mt-3 text-base font-semibold text-slate-900">{customer.company_name}</h3>
          <span className={`mt-2 inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${status.tone}`}>
            <StatusIcon className="h-3.5 w-3.5" />
            {status.label}
          </span>
        </div>

        <dl className="space-y-3 text-sm">
          <div className="flex items-center gap-3">
            <User className="h-4 w-4 text-slate-400" />
            <dt className="sr-only">Contact</dt>
            <dd className="text-slate-700">{customer.contact_person || '—'}</dd>
          </div>
          <div className="flex items-center gap-3">
            <Phone className="h-4 w-4 text-slate-400" />
            <dt className="sr-only">Phone</dt>
            <dd className="font-mono text-slate-700">+{customer.primary_phone}</dd>
          </div>
        </dl>

        <div>
          <h4 className="eyebrow mb-3">Credit</h4>
          <div className="rounded-lg border border-slate-200 p-4">
            <div className="flex items-baseline justify-between">
              <span className="text-sm text-slate-500">Outstanding</span>
              <span className={`text-lg font-semibold tabular-nums ${breached ? 'text-rose-600' : 'text-slate-900'}`}>{inr(outstanding)}</span>
            </div>
            <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-slate-100">
              <div className={`h-full rounded-full transition-all ${barTone}`} style={{ width: `${pct}%` }} />
            </div>
            <div className="mt-2 flex justify-between text-xs text-slate-500">
              <span>{pct}% used</span>
              <span>Limit {inr(limit)}</span>
            </div>
            <div className="mt-4 flex items-center justify-between border-t border-slate-100 pt-3 text-sm">
              <span className="text-slate-500">Available</span>
              <span className="font-medium tabular-nums text-slate-900">{inr(available)}</span>
            </div>
          </div>
        </div>

        <div>
          <h4 className="eyebrow mb-3">Staff touchpoints</h4>
          {touchpoints.length === 0 ? (
            <p className="text-sm text-slate-500">No interactions recorded yet.</p>
          ) : (
            <ul className="space-y-2">
              {touchpoints.map(([name, count]) => (
                <li key={name} className="flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2 text-sm">
                  <span className="flex items-center gap-2 text-slate-700">
                    <Smartphone className="h-4 w-4 text-slate-400" />
                    {name}
                  </span>
                  <span className="text-xs tabular-nums text-slate-500">
                    {count} msg{count !== 1 ? 's' : ''}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
};
