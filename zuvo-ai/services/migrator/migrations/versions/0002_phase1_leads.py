"""Phase 1: leads, visits, follow-ups, pricing/KB, agent runs, approvals, learning, metrics.

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

APP_ROLE = "zuvo_app"
CURRENT_TENANT = "NULLIF(current_setting('app.current_tenant', true), '')::uuid"

NEW_TENANT_TABLES = [
    "tenant_config_versions", "price_lists", "price_items", "kb_documents", "kb_chunks", "campaigns",
    "leads", "lead_events", "visit_plans", "followup_tasks", "agent_runs", "approvals",
    "learned_examples", "autonomy_stats", "metrics_daily", "ai_usage_daily",
]


def _execute_script(sql: str) -> None:
    for statement in (part.strip() for part in sql.split(";")):
        if statement:
            op.execute(statement)


def upgrade():
    _execute_script(
        """
        ALTER TABLE tenants
          ADD COLUMN industry VARCHAR(64),
          ADD COLUMN timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Kolkata',
          ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ONBOARDING','ACTIVE','PAUSED')),
          ADD COLUMN plan VARCHAR(32),
          ADD COLUMN monthly_ai_budget_inr NUMERIC(12,2) NOT NULL DEFAULT 500,
          ADD COLUMN onboarded_at TIMESTAMPTZ;

        ALTER TABLE workers ADD COLUMN whatsapp_phone VARCHAR(20);

        ALTER TABLE whatsapp_numbers DROP CONSTRAINT IF EXISTS whatsapp_numbers_meta_phone_number_id_key;
        ALTER TABLE whatsapp_numbers
          ADD COLUMN waba_id VARCHAR(64),
          ADD COLUMN label VARCHAR(100),
          ADD COLUMN purpose VARCHAR(16),
          ADD CONSTRAINT uq_numbers_tenant_pnid UNIQUE (tenant_id, meta_phone_number_id);

        ALTER TABLE customers
          ADD COLUMN source VARCHAR(32),
          ADD COLUMN language VARCHAR(20),
          ADD COLUMN tags TEXT[] NOT NULL DEFAULT '{}',
          ADD COLUMN opted_out BOOLEAN NOT NULL DEFAULT false,
          ADD COLUMN opted_out_at TIMESTAMPTZ,
          ADD COLUMN consent_at TIMESTAMPTZ;

        ALTER TABLE messages
          ADD COLUMN lead_id UUID,
          ADD COLUMN agent_run_id UUID,
          ADD COLUMN sent_by VARCHAR(16),
          ADD COLUMN referral JSONB,
          ADD COLUMN delivery_status VARCHAR(16),
          ADD COLUMN delivered_at TIMESTAMPTZ,
          ADD COLUMN read_at TIMESTAMPTZ;
        CREATE INDEX ix_messages_tenant_lead ON messages (tenant_id, lead_id, created_at);

        CREATE TABLE tenant_config_versions (
          id          UUID PRIMARY KEY,
          tenant_id   UUID NOT NULL REFERENCES tenants(id),
          kind        VARCHAR(16) NOT NULL CHECK (kind IN ('config','skill','pricing','knowledge')),
          version     INTEGER NOT NULL,
          checksum    VARCHAR(64) NOT NULL,
          content     TEXT,
          status      VARCHAR(16) NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('DRAFT','ACTIVE','ARCHIVED')),
          created_by  VARCHAR(128) NOT NULL DEFAULT 'onboarding',
          created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, kind, version)
        );

        CREATE TABLE users (
          id                 UUID PRIMARY KEY,
          email              VARCHAR(255) NOT NULL UNIQUE,
          display_name       VARCHAR(255),
          password_hash      VARCHAR(255),
          is_platform_admin  BOOLEAN NOT NULL DEFAULT false,
          is_active          BOOLEAN NOT NULL DEFAULT true,
          created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE memberships (
          id          UUID PRIMARY KEY,
          user_id     UUID NOT NULL REFERENCES users(id),
          tenant_id   UUID NOT NULL REFERENCES tenants(id),
          role        VARCHAR(32) NOT NULL CHECK (role IN ('OWNER','MANAGER','SALES_WORKER')),
          worker_id   UUID,
          created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (user_id, tenant_id)
        );

        CREATE TABLE price_lists (
          id           UUID PRIMARY KEY,
          tenant_id    UUID NOT NULL REFERENCES tenants(id),
          version      INTEGER NOT NULL,
          source_file  VARCHAR(255) NOT NULL,
          checksum     VARCHAR(64) NOT NULL,
          row_count    INTEGER NOT NULL,
          is_active    BOOLEAN NOT NULL DEFAULT true,
          imported_by  VARCHAR(128) NOT NULL DEFAULT 'onboarding',
          imported_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, version)
        );

        CREATE TABLE price_items (
          id             UUID PRIMARY KEY,
          tenant_id      UUID NOT NULL REFERENCES tenants(id),
          price_list_id  UUID NOT NULL REFERENCES price_lists(id),
          sku            VARCHAR(100) NOT NULL,
          name           VARCHAR(255) NOT NULL,
          category       VARCHAR(100),
          attrs          JSONB NOT NULL DEFAULT '{}'::jsonb,
          price          NUMERIC(14,2),
          price_min      NUMERIC(14,2),
          price_max      NUMERIC(14,2),
          currency       VARCHAR(8) NOT NULL DEFAULT 'INR',
          unit           VARCHAR(32) NOT NULL DEFAULT 'unit',
          tax_included   BOOLEAN NOT NULL DEFAULT false,
          valid_until    DATE,
          aliases        TEXT[] NOT NULL DEFAULT '{}',
          notes          TEXT,
          UNIQUE (price_list_id, sku)
        );
        CREATE INDEX ix_price_items_tenant_list ON price_items (tenant_id, price_list_id);

        CREATE TABLE kb_documents (
          id           UUID PRIMARY KEY,
          tenant_id    UUID NOT NULL REFERENCES tenants(id),
          title        VARCHAR(255) NOT NULL,
          source_path  VARCHAR(255) NOT NULL,
          version      INTEGER NOT NULL DEFAULT 1,
          checksum     VARCHAR(64) NOT NULL,
          is_active    BOOLEAN NOT NULL DEFAULT true,
          updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, source_path)
        );

        CREATE TABLE kb_chunks (
          id           UUID PRIMARY KEY,
          tenant_id    UUID NOT NULL REFERENCES tenants(id),
          document_id  UUID NOT NULL REFERENCES kb_documents(id),
          ordinal      INTEGER NOT NULL,
          text         TEXT NOT NULL
        );
        CREATE INDEX ix_kb_chunks_tenant_doc ON kb_chunks (tenant_id, document_id);

        CREATE TABLE campaigns (
          id           UUID PRIMARY KEY,
          tenant_id    UUID NOT NULL REFERENCES tenants(id),
          platform     VARCHAR(32) NOT NULL,
          external_id  VARCHAR(255) NOT NULL,
          name         VARCHAR(255),
          started_at   TIMESTAMPTZ,
          spend_inr    NUMERIC(14,2),
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, platform, external_id)
        );

        CREATE TABLE leads (
          id                    UUID PRIMARY KEY,
          tenant_id             UUID NOT NULL REFERENCES tenants(id),
          customer_id           UUID NOT NULL REFERENCES customers(id),
          whatsapp_number_id    UUID REFERENCES whatsapp_numbers(id),
          assigned_worker_id    UUID REFERENCES workers(id),
          campaign_id           UUID REFERENCES campaigns(id),
          source                VARCHAR(32),
          referral              JSONB,
          interest              VARCHAR(255),
          budget_min            NUMERIC(14,2),
          budget_max            NUMERIC(14,2),
          timeline_text         VARCHAR(255),
          location              VARCHAR(255),
          stage                 VARCHAR(32) NOT NULL DEFAULT 'NEW',
          score                 INTEGER NOT NULL DEFAULT 0,
          lost_reason           VARCHAR(255),
          first_contact_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          first_response_at     TIMESTAMPTZ,
          last_customer_msg_at  TIMESTAMPTZ,
          last_business_msg_at  TIMESTAMPTZ,
          next_followup_at      TIMESTAMPTZ,
          closed_at             TIMESTAMPTZ,
          created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX uq_leads_open_per_customer ON leads (tenant_id, customer_id) WHERE closed_at IS NULL;
        CREATE INDEX ix_leads_tenant_stage ON leads (tenant_id, stage, updated_at DESC);
        CREATE INDEX ix_leads_tenant_worker ON leads (tenant_id, assigned_worker_id);

        CREATE TABLE lead_events (
          id          UUID PRIMARY KEY,
          tenant_id   UUID NOT NULL REFERENCES tenants(id),
          lead_id     UUID NOT NULL REFERENCES leads(id),
          type        VARCHAR(32) NOT NULL,
          from_value  VARCHAR(255),
          to_value    VARCHAR(255),
          actor       VARCHAR(128) NOT NULL,
          message_id  UUID,
          details     JSONB NOT NULL DEFAULT '{}'::jsonb,
          created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_lead_events_tenant_lead ON lead_events (tenant_id, lead_id, created_at);
        CREATE INDEX ix_lead_events_tenant_type ON lead_events (tenant_id, type, created_at);

        CREATE TABLE visit_plans (
          id                    UUID PRIMARY KEY,
          tenant_id             UUID NOT NULL REFERENCES tenants(id),
          lead_id               UUID NOT NULL REFERENCES leads(id),
          source_message_id     UUID,
          raw_text              TEXT NOT NULL DEFAULT '',
          resolved_date         DATE NOT NULL,
          resolved_time_window  VARCHAR(16),
          precision             VARCHAR(16) NOT NULL CHECK (precision IN ('EXACT','WEEKDAY','WEEK','MONTH')),
          confidence            DOUBLE PRECISION NOT NULL DEFAULT 0,
          status                VARCHAR(16) NOT NULL DEFAULT 'PLANNED'
                                CHECK (status IN ('PLANNED','CONFIRMED','VISITED','NO_SHOW','RESCHEDULED','CANCELLED')),
          rescheduled_from_id   UUID,
          outcome_by            VARCHAR(128),
          outcome_at            TIMESTAMPTZ,
          created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_visits_tenant_date ON visit_plans (tenant_id, resolved_date, status);
        CREATE INDEX ix_visits_tenant_lead ON visit_plans (tenant_id, lead_id);

        CREATE TABLE followup_tasks (
          id                 UUID PRIMARY KEY,
          tenant_id          UUID NOT NULL REFERENCES tenants(id),
          lead_id            UUID NOT NULL REFERENCES leads(id),
          visit_plan_id      UUID,
          rule_id            VARCHAR(64) NOT NULL,
          kind               VARCHAR(32) NOT NULL,
          due_at             TIMESTAMPTZ NOT NULL,
          mode               VARCHAR(16) NOT NULL CHECK (mode IN ('auto','draft','manual')),
          channel            VARCHAR(16) NOT NULL DEFAULT 'whatsapp',
          template_name      VARCHAR(128),
          status             VARCHAR(20) NOT NULL DEFAULT 'SCHEDULED'
                             CHECK (status IN ('SCHEDULED','QUEUED','AWAITING_APPROVAL','NEEDS_ACTION','SENT','SKIPPED','DONE','FAILED','CANCELLED')),
          attempt            INTEGER NOT NULL DEFAULT 1,
          skip_reason        VARCHAR(128),
          result_message_id  VARCHAR(255),
          created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
          completed_at       TIMESTAMPTZ
        );
        CREATE INDEX ix_followups_tenant_due ON followup_tasks (tenant_id, status, due_at);
        CREATE INDEX ix_followups_tenant_lead ON followup_tasks (tenant_id, lead_id);

        CREATE TABLE agent_runs (
          id                UUID PRIMARY KEY,
          tenant_id         UUID NOT NULL REFERENCES tenants(id),
          message_id        UUID,
          lead_id           UUID,
          followup_task_id  UUID,
          intent            VARCHAR(50),
          task              VARCHAR(32) NOT NULL,
          topic             VARCHAR(128),
          question_text     TEXT,
          retrieved         JSONB NOT NULL DEFAULT '{}'::jsonb,
          proposed_text     TEXT,
          confidence        DOUBLE PRECISION NOT NULL DEFAULT 0,
          decision          VARCHAR(20) NOT NULL CHECK (decision IN ('AUTO_SENT','PENDING_APPROVAL','HANDOFF','SKIPPED')),
          decision_reasons  TEXT[] NOT NULL DEFAULT '{}',
          model             VARCHAR(64),
          tokens_in         INTEGER NOT NULL DEFAULT 0,
          tokens_out        INTEGER NOT NULL DEFAULT 0,
          cost_inr          NUMERIC(12,4) NOT NULL DEFAULT 0,
          latency_ms        INTEGER NOT NULL DEFAULT 0,
          config_checksum   VARCHAR(64),
          created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_agent_runs_tenant_created ON agent_runs (tenant_id, created_at DESC);
        CREATE UNIQUE INDEX uq_agent_runs_message_task ON agent_runs (tenant_id, message_id, task) WHERE message_id IS NOT NULL;

        CREATE TABLE approvals (
          id                  UUID PRIMARY KEY,
          tenant_id           UUID NOT NULL REFERENCES tenants(id),
          agent_run_id        UUID,
          lead_id             UUID,
          customer_id         UUID,
          whatsapp_number_id  UUID,
          followup_task_id    UUID,
          kind                VARCHAR(16) NOT NULL CHECK (kind IN ('reply','price','discount','followup','faq')),
          topic               VARCHAR(128),
          status              VARCHAR(16) NOT NULL DEFAULT 'PENDING'
                              CHECK (status IN ('PENDING','APPROVED','EDITED','REJECTED','EXPIRED','SUPERSEDED')),
          question_text       TEXT,
          proposed_text       TEXT NOT NULL,
          final_text          TEXT,
          template_name       VARCHAR(128),
          template_params     JSONB,
          edit_distance       INTEGER,
          reject_reason       TEXT,
          reviewer            VARCHAR(128),
          sla_due_at          TIMESTAMPTZ,
          holding_sent_at     TIMESTAMPTZ,
          decided_at          TIMESTAMPTZ,
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_approvals_tenant_status ON approvals (tenant_id, status, created_at DESC);

        CREATE TABLE learned_examples (
          id                  UUID PRIMARY KEY,
          tenant_id           UUID NOT NULL REFERENCES tenants(id),
          task                VARCHAR(32) NOT NULL,
          topic               VARCHAR(128) NOT NULL,
          question_text       TEXT NOT NULL,
          answer_text         TEXT,
          reject_reason       TEXT,
          price_item_skus     TEXT[] NOT NULL DEFAULT '{}',
          source_approval_id  UUID,
          outcome             VARCHAR(16) NOT NULL CHECK (outcome IN ('APPROVED','EDITED','REJECTED')),
          weight              DOUBLE PRECISION NOT NULL DEFAULT 1,
          active              BOOLEAN NOT NULL DEFAULT true,
          use_count           INTEGER NOT NULL DEFAULT 0,
          last_used_at        TIMESTAMPTZ,
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_learned_tenant_task ON learned_examples (tenant_id, task, topic) WHERE active;

        CREATE TABLE autonomy_stats (
          tenant_id           UUID NOT NULL REFERENCES tenants(id),
          task                VARCHAR(32) NOT NULL,
          topic               VARCHAR(128) NOT NULL,
          recent              JSONB NOT NULL DEFAULT '[]'::jsonb,
          approved_unchanged  INTEGER NOT NULL DEFAULT 0,
          edited              INTEGER NOT NULL DEFAULT 0,
          rejected            INTEGER NOT NULL DEFAULT 0,
          auto_enabled        BOOLEAN NOT NULL DEFAULT false,
          updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, task, topic)
        );

        CREATE TABLE metrics_daily (
          tenant_id   UUID NOT NULL REFERENCES tenants(id),
          date        DATE NOT NULL,
          data        JSONB NOT NULL DEFAULT '{}'::jsonb,
          updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, date)
        );

        CREATE TABLE ai_usage_daily (
          tenant_id   UUID NOT NULL REFERENCES tenants(id),
          date        DATE NOT NULL,
          service     VARCHAR(32) NOT NULL,
          model       VARCHAR(64) NOT NULL,
          calls       INTEGER NOT NULL DEFAULT 0,
          tokens_in   INTEGER NOT NULL DEFAULT 0,
          tokens_out  INTEGER NOT NULL DEFAULT 0,
          cost_inr    NUMERIC(12,4) NOT NULL DEFAULT 0,
          PRIMARY KEY (tenant_id, date, service, model)
        )
        """
    )

    for table in NEW_TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = {CURRENT_TENANT}) WITH CHECK (tenant_id = {CURRENT_TENANT})"
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO {APP_ROLE}")
    # chunks are replaced wholesale when a KB document changes
    op.execute(f"GRANT DELETE ON kb_chunks TO {APP_ROLE}")
    # platform-level identity tables (no tenant scope; memberships carry the tenant)
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON users, memberships TO {APP_ROLE}")


def downgrade():
    for table in ["ai_usage_daily", "metrics_daily", "autonomy_stats", "learned_examples", "approvals", "agent_runs",
                  "followup_tasks", "visit_plans", "lead_events", "leads", "campaigns", "kb_chunks", "kb_documents",
                  "price_items", "price_lists", "memberships", "users", "tenant_config_versions"]:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    _execute_script(
        """
        ALTER TABLE messages DROP COLUMN lead_id, DROP COLUMN agent_run_id, DROP COLUMN sent_by, DROP COLUMN referral,
          DROP COLUMN delivery_status, DROP COLUMN delivered_at, DROP COLUMN read_at;
        ALTER TABLE customers DROP COLUMN source, DROP COLUMN language, DROP COLUMN tags, DROP COLUMN opted_out,
          DROP COLUMN opted_out_at, DROP COLUMN consent_at;
        ALTER TABLE whatsapp_numbers DROP CONSTRAINT uq_numbers_tenant_pnid, DROP COLUMN waba_id, DROP COLUMN label, DROP COLUMN purpose;
        ALTER TABLE workers DROP COLUMN whatsapp_phone;
        ALTER TABLE tenants DROP COLUMN industry, DROP COLUMN timezone, DROP COLUMN status, DROP COLUMN plan,
          DROP COLUMN monthly_ai_budget_inr, DROP COLUMN onboarded_at
        """
    )
