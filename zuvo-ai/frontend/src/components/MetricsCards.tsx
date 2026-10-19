import React from 'react';
import { MessageSquareText, Building2, Smartphone, IndianRupee, ArrowDownLeft, ArrowUpRight } from 'lucide-react';
import { DashboardMetrics, CustomerRecord } from '../types';

interface MetricsCardsProps {
  metrics: DashboardMetrics | null;
  customers: CustomerRecord[];
}

const inr = (v: number) =>
  v >= 100000 ? `₹${(v / 100000).toFixed(2)} L` : `₹${v.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;

interface KpiProps {
  label: string;
  value: React.ReactNode;
  icon: React.ElementType;
  tone: string;
  footer: React.ReactNode;
}

const Kpi: React.FC<KpiProps> = ({ label, value, icon: Icon, tone, footer }) => (
  <div className="card p-5">
    <div className="flex items-start justify-between">
      <p className="text-sm font-medium text-slate-500">{label}</p>
      <div className={`flex h-9 w-9 items-center justify-center rounded-lg ${tone}`}>
        <Icon className="h-[18px] w-[18px]" />
      </div>
    </div>
    <div className="mt-1 text-[28px] font-semibold leading-tight tracking-tight text-slate-900 tabular-nums">{value}</div>
    <div className="mt-3 flex items-center gap-3 text-xs text-slate-500">{footer}</div>
  </div>
);

export const MetricsCards: React.FC<MetricsCardsProps> = ({ metrics, customers }) => {
  const totalOutstanding = customers.reduce((s, c) => s + (c.current_outstanding || 0), 0);
  const totalLimit = customers.reduce((s, c) => s + (c.credit_limit || 0), 0);
  const utilisation = totalLimit > 0 ? Math.min(100, (totalOutstanding / totalLimit) * 100) : 0;

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <Kpi
        label="Messages today"
        value={metrics?.total_messages_today ?? 0}
        icon={MessageSquareText}
        tone="bg-brand-50 text-brand-600"
        footer={
          <>
            <span className="inline-flex items-center gap-1">
              <ArrowDownLeft className="h-3.5 w-3.5 text-emerald-600" />
              {metrics?.total_inbound ?? 0} inbound
            </span>
            <span className="inline-flex items-center gap-1">
              <ArrowUpRight className="h-3.5 w-3.5 text-brand-600" />
              {metrics?.total_outbound ?? 0} outbound
            </span>
          </>
        }
      />
      <Kpi
        label="Active accounts"
        value={customers.length || metrics?.active_customers_count || 0}
        icon={Building2}
        tone="bg-violet-50 text-violet-600"
        footer={<span>B2B customers with a profile</span>}
      />
      <Kpi
        label="WhatsApp lines"
        value={metrics?.active_workers_count ?? 0}
        icon={Smartphone}
        tone="bg-emerald-50 text-emerald-600"
        footer={<span>Staff numbers connected</span>}
      />
      <Kpi
        label="Outstanding credit"
        value={inr(totalOutstanding)}
        icon={IndianRupee}
        tone="bg-amber-50 text-amber-600"
        footer={
          <div className="w-full">
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
              <div
                className={`h-full rounded-full ${utilisation > 80 ? 'bg-rose-500' : utilisation > 60 ? 'bg-amber-500' : 'bg-emerald-500'}`}
                style={{ width: `${utilisation}%` }}
              />
            </div>
            <div className="mt-1.5">
              {utilisation.toFixed(0)}% of {inr(totalLimit)} limit
            </div>
          </div>
        }
      />
    </div>
  );
};
