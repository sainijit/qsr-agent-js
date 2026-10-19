export interface TenantInfo {
  tenant_id: string;
  business_name: string;
  city: string;
  connected_lines_count: number;
  whatsapp_lines: WhatsAppLineInfo[];
}

export interface WhatsAppLineInfo {
  number_id: string;
  meta_phone_number_id: string;
  display_phone_number: string;
  assigned_worker: string;
  worker_id: string | null;
  status: string;
}

export interface WorkerSummary {
  worker_id: string;
  worker_name: string;
  designation: string;
  phone_number: string;
  meta_phone_number_id: string;
  total_messages_today: number;
  active_conversations_count: number;
}

export interface DashboardMessageItem {
  id: string;
  wamid: string;
  direction: 'INBOUND' | 'OUTBOUND';
  sender_phone: string;
  recipient_phone: string;
  message_type: string;
  content: string;
  parsed_intent: string | null;
  confidence_score: number | null;
  worker_id: string | null;
  worker_name: string;
  worker_phone: string | null;
  customer_id: string | null;
  customer_name: string;
  customer_phone: string;
  created_at: string;
  ai_status?: 'PENDING' | 'AI_PENDING' | 'EXTRACTED' | 'NEEDS_REVIEW' | 'SKIPPED' | string;
  status?: string;
  extraction?: Record<string, unknown> | null;
}

export interface DashboardMetrics {
  tenant_id: string;
  business_name: string;
  total_messages_today: number;
  total_inbound: number;
  total_outbound: number;
  active_workers_count: number;
  active_customers_count: number;
  pending_approvals_count: number;
}

export interface DashboardFeedResponse {
  metrics: DashboardMetrics;
  workers: WorkerSummary[];
  messages: DashboardMessageItem[];
  filter_applied: {
    worker_id: string | null;
    customer_id: string | null;
    customer_phone: string | null;
    limit: number;
    offset: number;
  };
}

export interface CustomerRecord {
  id: string;
  company_name: string;
  contact_person: string | null;
  primary_phone: string;
  credit_limit: number;
  current_outstanding: number;
  is_blocked: boolean;
  created_at: string | null;
}

export interface HealthStatus {
  status: string;
  app_name: string;
  version: string;
  environment: string;
  components: {
    database: string;
    redis_cache: string;
    meta_cloud_api: string;
    sse_clients?: number;
  };
}

export * from './phase1';
