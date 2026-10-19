import {
  TenantInfo,
  DashboardFeedResponse,
  CustomerRecord,
  HealthStatus,
  AuthTenant,
  LoginResponse,
  TodayResponse,
  LeadsResponse,
  LeadDetail,
  Lead,
  LeadPatch,
  VisitsResponse,
  Visit,
  VisitOutcomeStatus,
  FollowupsResponse,
  FollowupTask,
  ApprovalsResponse,
  Approval,
  InsightsResponse,
  LearnedExample,
  SettingsConfig,
  PricingUploadResult,
  StreamKind,
} from '../types';

const API_BASE = '/api/v1';
const TOKEN_KEY = 'zuvo.access_token';

export interface Session {
  access_token: string;
  tenant_id: string;
  role: 'OWNER' | 'MANAGER' | 'SALES_WORKER';
  worker_id: string | null;
  email?: string;
  display_name?: string | null;
  tenants?: AuthTenant[];
}

export interface DevIdentity {
  tenant_id: string;
  tenant_name: string;
  worker_key: string;
  worker_name: string;
  role: Session['role'];
}

export function getSession(): Session | null {
  const raw = sessionStorage.getItem(TOKEN_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as Session;
  } catch {
    return null;
  }
}

export function clearSession(): void {
  sessionStorage.removeItem(TOKEN_KEY);
  window.dispatchEvent(new Event('zuvo:logout'));
}

async function authFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const session = getSession();
  const headers = new Headers(init.headers);
  if (session) headers.set('Authorization', `Bearer ${session.access_token}`);
  const res = await fetch(url, { ...init, headers });
  if (res.status === 401) clearSession();
  return res;
}

export async function fetchDevIdentities(): Promise<DevIdentity[]> {
  const res = await fetch(`${API_BASE}/auth/dev-identities`);
  if (!res.ok) throw new Error('Dev login is disabled on this server.');
  return res.json();
}

export async function devLogin(tenant_id: string, worker_key: string): Promise<Session> {
  const res = await fetch(`${API_BASE}/auth/dev-login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tenant_id, worker_key }),
  });
  if (!res.ok) throw new Error(`Login failed: ${res.statusText}`);
  const session = (await res.json()) as Session;
  sessionStorage.setItem(TOKEN_KEY, JSON.stringify(session));
  return session;
}

/** Server-sent events from dashboard-api; each event means "something changed, refetch". */
export function openFeedStream(onChange: () => void): EventSource | null {
  const session = getSession();
  if (!session) return null;
  const es = new EventSource(`${API_BASE}/dashboard/stream?access_token=${encodeURIComponent(session.access_token)}`);
  es.addEventListener('message', onChange);
  return es;
}

export async function fetchHealth(): Promise<HealthStatus> {
  const res = await authFetch(`${API_BASE}/health`);
  if (!res.ok) throw new Error(`Health check failed: ${res.statusText}`);
  return res.json();
}

export async function fetchTenants(): Promise<TenantInfo[]> {
  const res = await authFetch(`${API_BASE}/dashboard/tenants`);
  if (!res.ok) throw new Error(`Failed to fetch tenants: ${res.statusText}`);
  return res.json();
}

export async function fetchCustomers(tenantId: string): Promise<CustomerRecord[]> {
  const res = await authFetch(`${API_BASE}/dashboard/customers?tenant_id=${tenantId}`);
  if (!res.ok) throw new Error(`Failed to fetch customers: ${res.statusText}`);
  return res.json();
}

export async function fetchDashboardFeed(params: {
  tenantId: string;
  workerId?: string | null;
  customerId?: string | null;
  customerPhone?: string | null;
  limit?: number;
  offset?: number;
}): Promise<DashboardFeedResponse> {
  const searchParams = new URLSearchParams();
  searchParams.append('tenant_id', params.tenantId);
  if (params.workerId) searchParams.append('worker_id', params.workerId);
  if (params.customerId) searchParams.append('customer_id', params.customerId);
  if (params.customerPhone) searchParams.append('customer_phone', params.customerPhone);
  if (params.limit) searchParams.append('limit', params.limit.toString());
  if (params.offset) searchParams.append('offset', params.offset.toString());

  const res = await authFetch(`${API_BASE}/dashboard/feed?${searchParams.toString()}`);
  if (!res.ok) throw new Error(`Failed to fetch feed: ${res.statusText}`);
  return res.json();
}

export async function sendOutboundMessage(payload: {
  recipient_phone: string;
  message_text: string;
  sender_number_id?: string;
}): Promise<{ status: string; request_id?: string }> {
  const res = await authFetch(`${API_BASE}/messages/send`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    let msg = errorData.detail || `Failed to send message: ${res.statusText}`;
    if (typeof msg === 'object') msg = JSON.stringify(msg);
    if (msg.includes('Recipient phone number not in allowed list')) {
      msg = `Meta Cloud API Error: Recipient ${payload.recipient_phone} is not in your Meta Tester allowed list. Add it in Meta API Setup or test with your verified number.`;
    }
    throw new Error(msg);
  }
  return res.json();
}

export async function simulateInboundMessage(payload: {
  phone_number_id: string;
  sender_phone: string;
  sender_name: string;
  message_text: string;
}): Promise<{ status: string; wamid?: string }> {
  const res = await authFetch(`${API_BASE}/messages/simulate-inbound`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    let msg = errorData.detail || `Failed to simulate message: ${res.statusText}`;
    if (typeof msg === 'object') msg = JSON.stringify(msg);
    throw new Error(msg);
  }
  return res.json();
}

// ---------------------------------------------------------------- Phase 1

function saveSession(session: Session): Session {
  sessionStorage.setItem(TOKEN_KEY, JSON.stringify(session));
  return session;
}

/** fetch + JSON with FastAPI `detail` surfaced as the Error message. */
async function apiJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const res = await authFetch(`${API_BASE}${path}`, { ...init, headers });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    let msg: unknown = data.detail ?? `${res.status} ${res.statusText}`;
    if (Array.isArray(msg)) msg = msg.map((d: any) => d?.msg ?? JSON.stringify(d)).join('; ');
    else if (typeof msg === 'object') msg = JSON.stringify(msg);
    const err = new Error(String(msg)) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }
  return res.json() as Promise<T>;
}

function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const sp = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '' && v !== false) sp.append(k, String(v));
  });
  const str = sp.toString();
  return str ? `?${str}` : '';
}

export async function passwordLogin(email: string, password: string): Promise<Session> {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(typeof data.detail === 'string' ? data.detail : `Login failed: ${res.statusText}`);
  }
  const r = (await res.json()) as LoginResponse;
  return saveSession({
    access_token: r.access_token, tenant_id: r.tenant_id, role: r.role, worker_id: r.worker_id,
    email: r.email, display_name: r.display_name, tenants: r.tenants || [],
  });
}

export const fetchAuthTenants = () => apiJson<AuthTenant[]>('/auth/tenants');

export async function switchTenant(tenantId: string): Promise<Session> {
  const prev = getSession();
  const r = await apiJson<LoginResponse>('/auth/switch-tenant', { method: 'POST', body: JSON.stringify({ tenant_id: tenantId }) });
  return saveSession({
    access_token: r.access_token, tenant_id: r.tenant_id, role: r.role, worker_id: r.worker_id,
    email: r.email, display_name: r.display_name, tenants: prev?.tenants || [],
  });
}

export const fetchToday = () => apiJson<TodayResponse>('/dashboard/today');

export const fetchLeads = (p: { stage?: string; q?: string; source?: string; open_only?: boolean; limit?: number } = {}) =>
  apiJson<LeadsResponse>(`/leads${qs({ limit: 500, ...p })}`);

export const fetchLead = (id: string) => apiJson<LeadDetail>(`/leads/${encodeURIComponent(id)}`);

export const patchLead = (id: string, body: LeadPatch) =>
  apiJson<Lead>(`/leads/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(body) });

export const fetchVisits = (p: { from?: string; to?: string; status?: string } = {}) => apiJson<VisitsResponse>(`/visits${qs(p)}`);

export const setVisitOutcome = (id: string, status: VisitOutcomeStatus) =>
  apiJson<Visit>(`/visits/${encodeURIComponent(id)}/outcome`, { method: 'POST', body: JSON.stringify({ status }) });

export const fetchFollowups = (status?: string) => apiJson<FollowupsResponse>(`/followups${qs({ status })}`);

export const skipFollowup = (id: string) =>
  apiJson<FollowupTask>(`/followups/${encodeURIComponent(id)}/skip`, { method: 'POST' });

export const sendFollowupNow = (id: string, text?: string, confirmUnknown = false) =>
  apiJson<FollowupTask>(`/followups/${encodeURIComponent(id)}/send-now`, {
    method: 'POST',
    body: JSON.stringify({
      ...(text && text.trim() ? { text: text.trim() } : {}),
      ...(confirmUnknown ? { confirm_unknown: true } : {}),
    }),
  });

export const fetchApprovals = (status = 'PENDING') => apiJson<ApprovalsResponse>(`/approvals${qs({ status })}`);

export const decideApproval = (id: string, body: { decision: 'approve' | 'edit' | 'reject'; text?: string; reason?: string }) =>
  apiJson<Approval>(`/approvals/${encodeURIComponent(id)}/decide`, { method: 'POST', body: JSON.stringify(body) });

export const fetchInsights = (days: number) => apiJson<InsightsResponse>(`/insights${qs({ days })}`);

export const fetchLearnedExamples = () => apiJson<LearnedExample[]>('/learning/examples');

export const toggleLearnedExample = (id: string) =>
  apiJson<{ id: string; active: boolean }>(`/learning/examples/${encodeURIComponent(id)}/toggle`, { method: 'POST' });

export const fetchSettingsConfig = () => apiJson<SettingsConfig>('/settings/config');

export const uploadPricing = (csv_text: string, source_file: string, dry_run: boolean) =>
  apiJson<PricingUploadResult>('/settings/pricing', { method: 'POST', body: JSON.stringify({ csv_text, source_file, dry_run }) });

/** Shared SSE stream for Phase 1 tabs; `onEvent` receives the event kind. */
export function openEventStream(onEvent: (kind: StreamKind) => void): EventSource | null {
  const session = getSession();
  if (!session) return null;
  const es = new EventSource(`${API_BASE}/dashboard/stream?access_token=${encodeURIComponent(session.access_token)}`);
  (['message', 'lead', 'visit', 'approval'] as StreamKind[]).forEach((k) => es.addEventListener(k, () => onEvent(k)));
  return es;
}
