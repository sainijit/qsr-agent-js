import React from 'react';
import { Search, X, ChevronDown } from 'lucide-react';
import { WorkerSummary, CustomerRecord } from '../types';
import { prettyRole } from './ConversationsList';

interface GateG6FilterBarProps {
  workers: WorkerSummary[];
  customers: CustomerRecord[];
  selectedWorkerId: string | null;
  onSelectWorker: (workerId: string | null) => void;
  selectedCustomerId: string | null;
  onSelectCustomer: (customerId: string | null) => void;
  searchPhone: string;
  onSearchPhoneChange: (phone: string) => void;
  onResetFilters: () => void;
}

export const GateG6FilterBar: React.FC<GateG6FilterBarProps> = ({
  workers,
  customers,
  selectedWorkerId,
  onSelectWorker,
  selectedCustomerId,
  onSelectCustomer,
  searchPhone,
  onSearchPhoneChange,
  onResetFilters,
}) => {
  const isFiltered = Boolean(selectedWorkerId || selectedCustomerId || searchPhone);

  const tab = (active: boolean) =>
    `inline-flex items-center gap-2 whitespace-nowrap rounded-md px-3 py-1.5 text-sm font-medium transition ${
      active ? 'bg-white text-slate-900 shadow-sm ring-1 ring-slate-200' : 'text-slate-600 hover:text-slate-900'
    }`;

  return (
    <div className="card flex flex-col gap-3 p-3 lg:flex-row lg:items-center">
      <div className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto rounded-lg bg-slate-100 p-1 scrollbar-none">
        <button onClick={() => onSelectWorker(null)} className={tab(selectedWorkerId === null)}>
          All lines
          <span className="rounded-full bg-slate-200/80 px-1.5 text-xs text-slate-600">{workers.length}</span>
        </button>
        {workers.map((w) => (
          <button
            key={w.worker_id}
            onClick={() => onSelectWorker(w.worker_id)}
            className={tab(selectedWorkerId === w.worker_id)}
            title={`${w.worker_name} · ${w.designation} · ${w.phone_number}`}
          >
            {w.worker_name.split(' ')[0]}
            <span className="hidden text-xs font-normal text-slate-400 sm:inline">{prettyRole(w.designation)}</span>
          </button>
        ))}
      </div>

      <div className="flex items-center gap-2">
        <div className="relative w-full lg:w-64">
          <select
            value={selectedCustomerId || ''}
            onChange={(e) => onSelectCustomer(e.target.value || null)}
            aria-label="Customer"
            className="field cursor-pointer appearance-none pr-9"
          >
            <option value="">All customers</option>
            {customers.map((c) => (
              <option key={c.id} value={c.id}>
                {c.company_name}
              </option>
            ))}
          </select>
          <ChevronDown className="pointer-events-none absolute right-3 top-2.5 h-4 w-4 text-slate-400" />
        </div>
        <div className="relative w-full lg:w-52">
          <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
          <input
            type="text"
            placeholder="Search phone"
            value={searchPhone}
            onChange={(e) => onSearchPhoneChange(e.target.value)}
            className="field pl-9"
          />
        </div>
        {isFiltered && (
          <button onClick={onResetFilters} className="btn-ghost whitespace-nowrap" title="Clear filters">
            <X className="h-4 w-4" />
            Clear
          </button>
        )}
      </div>
    </div>
  );
};
