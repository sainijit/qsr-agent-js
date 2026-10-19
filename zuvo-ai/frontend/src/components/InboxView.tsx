import React, { useState, useEffect, useCallback } from 'react';
import { RefreshCw, Send } from 'lucide-react';
import {
  fetchTenants,
  fetchCustomers,
  fetchDashboardFeed,
  sendOutboundMessage,
  simulateInboundMessage,
} from '../services/api';
import { TenantInfo, DashboardFeedResponse, CustomerRecord } from '../types';
import { MetricsCards } from './MetricsCards';
import { GateG6FilterBar } from './GateG6FilterBar';
import { ConversationsList } from './ConversationsList';
import { ChatTimeline } from './ChatTimeline';
import { ReplyComposer } from './ReplyComposer';
import { Customer360Drawer } from './Customer360Drawer';
import { SimulationModal } from './SimulationModal';

/** The original WhatsApp inbox (feed + filters + composer). `messageTick` changes on every SSE `message` event. */
export const InboxView: React.FC<{ messageTick: number }> = ({ messageTick }) => {
  const [tenants, setTenants] = useState<TenantInfo[]>([]);
  const [selectedTenantId, setSelectedTenantId] = useState<string>('');
  const [customers, setCustomers] = useState<CustomerRecord[]>([]);
  const [feedData, setFeedData] = useState<DashboardFeedResponse | null>(null);

  // Gate G6 Filter State
  const [selectedWorkerId, setSelectedWorkerId] = useState<string | null>(null);
  const [selectedCustomerId, setSelectedCustomerId] = useState<string | null>(null);
  const [searchPhone, setSearchPhone] = useState<string>('');

  // Active Thread / Customer Selection in UI
  const [activeCustomerPhone, setActiveCustomerPhone] = useState<string | null>(null);

  const [autoRefresh, setAutoRefresh] = useState<boolean>(true);
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false);
  const [isSimulationOpen, setIsSimulationOpen] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchTenants()
      .then((list) => {
        setTenants(list);
        if (list.length > 0) setSelectedTenantId(list[0].tenant_id);
      })
      .catch((err: any) => setError(err.message || 'Failed to initialize ZuvoCloud platform.'));
  }, []);

  useEffect(() => {
    if (!selectedTenantId) return;
    fetchCustomers(selectedTenantId)
      .then(setCustomers)
      .catch((err) => console.error('Failed to load customers:', err));
  }, [selectedTenantId]);

  const loadFeed = useCallback(async (showSpinner = false) => {
    if (!selectedTenantId) return;
    if (showSpinner) setIsRefreshing(true);

    try {
      const data = await fetchDashboardFeed({
        tenantId: selectedTenantId,
        workerId: selectedWorkerId,
        customerId: selectedCustomerId,
        customerPhone: searchPhone || undefined,
        limit: 100,
      });
      setFeedData(data);
      setError(null);

      if (!activeCustomerPhone && data.messages.length > 0) {
        const firstPhone = data.messages[0].customer_phone || data.messages[0].sender_phone;
        setActiveCustomerPhone(firstPhone);
      }
    } catch (err: any) {
      console.error('Feed fetch error:', err);
    } finally {
      if (showSpinner) setIsRefreshing(false);
    }
  }, [selectedTenantId, selectedWorkerId, selectedCustomerId, searchPhone, activeCustomerPhone]);

  useEffect(() => {
    loadFeed(true);
  }, [loadFeed]);

  // Live updates: SSE message events trigger a debounced refetch; slow poll as a safety net
  useEffect(() => {
    if (!autoRefresh || messageTick === 0) return;
    const timer = setTimeout(() => loadFeed(false), 250);
    return () => clearTimeout(timer);
  }, [messageTick]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!autoRefresh) return;
    const interval = setInterval(() => loadFeed(false), 30000);
    return () => clearInterval(interval);
  }, [autoRefresh, loadFeed]);

  useEffect(() => {
    if (!selectedTenantId || !feedData) return;
    fetchCustomers(selectedTenantId).then(setCustomers).catch(() => undefined);
  }, [feedData?.metrics.total_messages_today]);

  const handleSelectCustomerFilter = (customerId: string | null) => {
    setSelectedCustomerId(customerId);
    if (customerId) {
      const cust = customers.find((c) => c.id === customerId);
      if (cust) setActiveCustomerPhone(cust.primary_phone);
    }
  };

  const handleResetFilters = () => {
    setSelectedWorkerId(null);
    setSelectedCustomerId(null);
    setSearchPhone('');
  };

  const handleSendOutbound = async (payload: { recipient_phone: string; message_text: string; sender_number_id?: string }) => {
    await sendOutboundMessage(payload);
    await loadFeed(false);
  };

  const handleSimulate = async (payload: { phone_number_id: string; sender_phone: string; sender_name: string; message_text: string }) => {
    await simulateInboundMessage(payload);
    await loadFeed(false);
  };

  const currentCustomer = customers.find(
    (c) => c.primary_phone === activeCustomerPhone || c.id === selectedCustomerId
  ) || null;

  const currentTenant = tenants.find((t) => t.tenant_id === selectedTenantId);
  const activeLines = currentTenant?.whatsapp_lines || [];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-slate-500">
          WhatsApp activity across all staff lines{currentTenant?.city ? ` · ${currentTenant.city}` : ''}
        </p>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setAutoRefresh(!autoRefresh)}
            aria-pressed={autoRefresh}
            title="Toggle live updates"
            className={`inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium transition ${
              autoRefresh
                ? 'border-brand-200 bg-brand-50 text-brand-700 hover:bg-brand-100'
                : 'border-slate-300 bg-white text-slate-600 hover:bg-slate-50'
            }`}
          >
            <span className="relative flex h-2 w-2">
              {autoRefresh && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-brand-400 opacity-60" />}
              <span className={`relative inline-flex h-2 w-2 rounded-full ${autoRefresh ? 'bg-brand-600' : 'bg-slate-400'}`} />
            </span>
            {autoRefresh ? 'Live' : 'Paused'}
          </button>
          <button onClick={() => loadFeed(true)} disabled={isRefreshing} className="btn-icon" title="Refresh now" aria-label="Refresh">
            <RefreshCw className={`h-4 w-4 ${isRefreshing ? 'animate-spin text-brand-600' : ''}`} />
          </button>
          <button onClick={() => setIsSimulationOpen(true)} className="btn-primary">
            <Send className="h-4 w-4" />
            <span className="hidden sm:inline">Simulate message</span>
          </button>
        </div>
      </div>

      {error && (
        <div role="alert" className="flex items-center gap-2 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
          <span className="h-1.5 w-1.5 rounded-full bg-rose-500" />
          {error}
        </div>
      )}

      <MetricsCards metrics={feedData?.metrics || null} customers={customers} />

      <GateG6FilterBar
        workers={feedData?.workers || []}
        customers={customers}
        selectedWorkerId={selectedWorkerId}
        onSelectWorker={setSelectedWorkerId}
        selectedCustomerId={selectedCustomerId}
        onSelectCustomer={handleSelectCustomerFilter}
        searchPhone={searchPhone}
        onSearchPhoneChange={setSearchPhone}
        onResetFilters={handleResetFilters}
      />

      <div className="grid grid-cols-1 gap-4 lg:h-[calc(100vh-8rem)] lg:min-h-[600px] lg:max-h-[860px] lg:grid-cols-12">
        <div className="h-[420px] lg:col-span-3 lg:h-auto lg:min-h-0">
          <ConversationsList
            messages={feedData?.messages || []}
            selectedCustomerPhone={activeCustomerPhone}
            onSelectCustomerPhone={(phone) => {
              setActiveCustomerPhone(phone);
              const matched = customers.find((c) => c.primary_phone === phone);
              if (matched) setSelectedCustomerId(matched.id);
            }}
          />
        </div>

        <section className="card flex h-[640px] flex-col overflow-hidden lg:col-span-6 lg:h-auto lg:min-h-0">
          <ChatTimeline messages={feedData?.messages || []} selectedCustomerPhone={activeCustomerPhone} />
          <ReplyComposer lines={activeLines} recipientPhone={activeCustomerPhone || ''} onSendMessage={handleSendOutbound} />
        </section>

        <div className="lg:col-span-3 lg:min-h-0">
          <Customer360Drawer customer={currentCustomer} messages={feedData?.messages || []} />
        </div>
      </div>

      <SimulationModal
        isOpen={isSimulationOpen}
        onClose={() => setIsSimulationOpen(false)}
        lines={activeLines}
        onSimulate={handleSimulate}
      />
    </div>
  );
};
