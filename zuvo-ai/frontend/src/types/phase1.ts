// Phase 1 API shapes (services/dashboard_api/dashboard_api/phase1.py).

export type Role = 'OWNER' | 'MANAGER' | 'SALES_WORKER';

export interface AuthTenant {
  tenant_id: string;
  name?: string;
  role: Role | string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  tenant_id: string;
  role: Role;
  worker_id: string | null;
  email: string;
  display_name: string | null;
  tenants?: AuthTenant[];
}

export interface Lead {
  id: string;
  customer_id: string;
  stage: string;
  score: number | null;
  source: string | null;
  interest: string | null;
  budget_min: number | null;
  budget_max: number | null;
  timeline_text: string | null;
  location: string | null;
  lost_reason: string | null;
  campaign_id: string | null;
  whatsapp_number_id: string | null;
  assigned_worker_id: string | null;
  assigned_worker_name: string | null;
  customer_name: string | null;
  customer_phone: string | null;
  opted_out: boolean;
  first_contact_at: string | null;
  first_response_at: string | null;
  last_customer_msg_at: string | null;
  last_business_msg_at: string | null;
  next_followup_at: string | null;
  closed_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface LeadsResponse {
  total: number;
  stages: string[];
  items: Lead[];
}

export type VisitStatus = 'PLANNED' | 'CONFIRMED' | 'VISITED' | 'NO_SHOW' | 'CANCELLED' | 'RESCHEDULED';
export type VisitOutcomeStatus = 'CONFIRMED' | 'VISITED' | 'NO_SHOW' | 'CANCELLED';

export interface Visit {
  id: string;
  lead_id: string;
  date: string | null;
  time_window: string | null;
  precision: string | null;
  confidence: number | null;
  status: VisitStatus | string;
  raw_text: string | null;
  outcome_by: string | null;
  outcome_at: string | null;
  created_at: string | null;
  lead_stage: string | null;
  interest: string | null;
  customer_name: string | null;
  customer_phone: string | null;
}

export interface VisitsResponse {
  from: string;
  to: string;
  today: string;
  by_day: Record<string, number>;
  items: Visit[];
}

export interface FollowupTask {
  id: string;
  lead_id: string;
  visit_plan_id: string | null;
  rule_id: string | null;
  kind: string;
  due_at: string | null;
  mode: string;
  status: string;
  attempt: number;
  skip_reason: string | null;
  template_name: string | null;
  completed_at: string | null;
  created_at: string | null;
  lead_stage: string | null;
  interest: string | null;
  customer_name: string | null;
  customer_phone: string | null;
}

export interface FollowupsResponse {
  counts: Record<string, number>;
  items: FollowupTask[];
}

export interface Approval {
  id: string;
  kind: string;
  topic: string | null;
  status: string;
  lead_id: string | null;
  customer_id: string | null;
  followup_task_id: string | null;
  question_text: string | null;
  proposed_text: string | null;
  final_text: string | null;
  template_name: string | null;
  edit_distance: number | null;
  reject_reason: string | null;
  reviewer: string | null;
  sla_due_at: string | null;
  holding_sent_at: string | null;
  decided_at: string | null;
  created_at: string | null;
  confidence: number | null;
  reasons: string[];
  retrieved: unknown;
  task: string | null;
  customer_name: string | null;
  customer_phone: string | null;
}

export interface ApprovalsResponse {
  pending: number;
  items: Approval[];
}

export interface DayMetrics {
  leads_new?: number;
  leads_new_by_source?: Record<string, number>;
  leads_open_by_stage?: Record<string, number>;
  leads_won?: number;
  leads_lost?: number;
  first_response_median_min?: number | null;
  visits_planned_for_day?: number;
  visits_created?: number;
  visits_done?: number;
  visits_no_show?: number;
  visits_next_7d?: number;
  followups_due?: number;
  followups_sent?: number;
  followups_overdue?: number;
  followups_needs_action?: number;
  agent_auto_sent?: number;
  agent_drafted?: number;
  approvals_pending?: number;
  approvals_decided?: number;
  messages_in?: number;
  messages_out?: number;
  ai_cost_inr?: number;
  [key: string]: unknown;
}

export interface TodayResponse {
  tenant: { id: string; name: string; slug: string; industry: string; timezone: string };
  date: string;
  metrics: DayMetrics;
  visits_upcoming: Visit[];
  followups_attention: FollowupTask[];
  hot_leads: Lead[];
}

export interface LeadEvent {
  id: string;
  type: string;
  from: string | null;
  to: string | null;
  actor: string | null;
  details: Record<string, unknown> | null;
  created_at: string | null;
}

export interface LeadMessage {
  id: string;
  direction: string;
  body: string | null;
  intent: string | null;
  status: string | null;
  sent_by: string | null;
  created_at: string | null;
}

export interface LeadDetail {
  lead: Lead;
  campaign: { id: string; platform: string | null; name: string | null; external_id: string | null } | null;
  referral: Record<string, unknown> | null;
  events: LeadEvent[];
  visits: Visit[];
  followups: FollowupTask[];
  approvals: Approval[];
  messages: LeadMessage[];
}

export interface LeadPatch {
  stage?: string;
  assigned_worker_id?: string;
  lost_reason?: string;
  interest?: string;
}

export interface InsightsResponse {
  days: number;
  series: (DayMetrics & { date: string })[];
  funnel: Record<string, number>;
  by_source: Record<string, number>;
  campaigns: { id: string; platform: string | null; name: string | null; leads: number; visits: number; won: number }[];
  approvals: Record<string, number>;
  learning: {
    examples: Record<string, number>;
    autonomy: { task: string; topic: string; approved: number; edited: number; rejected: number; auto_enabled: boolean; recent: number }[];
  };
}

export interface LearnedExample {
  id: string;
  task: string;
  topic: string | null;
  question: string | null;
  answer: string | null;
  reject_reason: string | null;
  outcome: string;
  weight: number | null;
  active: boolean;
  use_count: number;
  created_at: string | null;
}

export interface WorkerRef {
  id: string;
  name: string;
  role: string;
}

export interface PriceItem {
  sku: string;
  name: string;
  category: string | null;
  price: number | null;
  price_min: number | null;
  price_max: number | null;
  unit: string | null;
  valid_until: string | null;
  aliases: string[] | null;
}

export interface SettingsConfig {
  slug: string;
  checksums: Record<string, string>;
  config_yaml: string;
  skill_md: string;
  config: Record<string, unknown>;
  knowledge: { path: string; title: string; chars: number }[];
  workers: WorkerRef[];
  price_list: { version: number; rows: number; source_file: string | null; imported_by: string | null; imported_at: string | null } | null;
  price_items: PriceItem[];
}

export interface PriceSnapshot {
  name: string;
  price: number | null;
  price_min: number | null;
  price_max: number | null;
}

export interface PricingUploadResult {
  dry_run: boolean;
  rows: number;
  errors: string[];
  warnings: string[];
  diff: {
    added: string[];
    removed: string[];
    changed: { sku: string; old: PriceSnapshot; new: PriceSnapshot }[];
  };
  import?: Record<string, unknown>;
}

export type StreamKind = 'message' | 'lead' | 'visit' | 'approval';
