"""Initial multi-tenant schema with Row-Level Security.

Revision ID: 0001
Revises:
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

APP_ROLE = "zuvo_app"
TENANT_TABLES = ["workers", "whatsapp_numbers", "customers", "conversations", "messages", "audit_logs"]
CURRENT_TENANT = "NULLIF(current_setting('app.current_tenant', true), '')::uuid"


def _execute_script(sql: str) -> None:
    # asyncpg runs one statement per call; the DDL below has no ';' inside literals
    for statement in (part.strip() for part in sql.split(";")):
        if statement:
            op.execute(statement)


def upgrade():
    _execute_script(
        """
        CREATE TABLE tenants (
          id          UUID PRIMARY KEY,
          slug        VARCHAR(64) NOT NULL UNIQUE,
          name        VARCHAR(255) NOT NULL,
          city        VARCHAR(100),
          currency    VARCHAR(8) NOT NULL DEFAULT 'INR',
          created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE workers (
          id            UUID PRIMARY KEY,
          tenant_id     UUID NOT NULL REFERENCES tenants(id),
          worker_key    VARCHAR(100) NOT NULL,
          display_name  VARCHAR(255) NOT NULL,
          role          VARCHAR(32) NOT NULL CHECK (role IN ('OWNER','MANAGER','SALES_WORKER')),
          is_active     BOOLEAN NOT NULL DEFAULT true,
          created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, worker_key)
        );

        CREATE TABLE whatsapp_numbers (
          id                    UUID PRIMARY KEY,
          tenant_id             UUID NOT NULL REFERENCES tenants(id),
          worker_id             UUID NOT NULL REFERENCES workers(id),
          meta_phone_number_id  VARCHAR(64) NOT NULL UNIQUE,
          display_phone_number  VARCHAR(32) NOT NULL,
          status                VARCHAR(16) NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','PAUSED','REVOKED'))
        );

        CREATE TABLE customers (
          id            UUID PRIMARY KEY,
          tenant_id     UUID NOT NULL REFERENCES tenants(id),
          phone         VARCHAR(20) NOT NULL,
          display_name  VARCHAR(255),
          account_name  VARCHAR(255),
          credit_limit  NUMERIC(14,2) NOT NULL DEFAULT 50000,
          outstanding   NUMERIC(14,2) NOT NULL DEFAULT 0,
          is_blocked    BOOLEAN NOT NULL DEFAULT false,
          created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, phone)
        );

        CREATE TABLE conversations (
          id                  UUID PRIMARY KEY,
          tenant_id           UUID NOT NULL REFERENCES tenants(id),
          whatsapp_number_id  UUID NOT NULL REFERENCES whatsapp_numbers(id),
          worker_id           UUID NOT NULL REFERENCES workers(id),
          customer_id         UUID NOT NULL REFERENCES customers(id),
          last_message_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
          last_inbound_at     TIMESTAMPTZ,
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, whatsapp_number_id, customer_id)
        );

        CREATE TABLE messages (
          id                  UUID PRIMARY KEY,
          tenant_id           UUID NOT NULL REFERENCES tenants(id),
          conversation_id     UUID NOT NULL REFERENCES conversations(id),
          whatsapp_number_id  UUID NOT NULL REFERENCES whatsapp_numbers(id),
          worker_id           UUID NOT NULL REFERENCES workers(id),
          customer_id         UUID NOT NULL REFERENCES customers(id),
          wamid               VARCHAR(255) NOT NULL,
          direction           VARCHAR(10) NOT NULL CHECK (direction IN ('INBOUND','OUTBOUND')),
          message_type        VARCHAR(20) NOT NULL DEFAULT 'text',
          body                TEXT NOT NULL DEFAULT '',
          media_id            VARCHAR(255),
          lang                VARCHAR(20),
          parsed_intent       VARCHAR(50),
          confidence          DOUBLE PRECISION,
          extraction          JSONB,
          ai_status           VARCHAR(20) NOT NULL DEFAULT 'PENDING',
          status              VARCHAR(20) NOT NULL DEFAULT 'RECEIVED',
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, wamid)
        );
        CREATE INDEX ix_messages_tenant_created ON messages (tenant_id, created_at DESC);
        CREATE INDEX ix_messages_tenant_worker ON messages (tenant_id, worker_id);
        CREATE INDEX ix_messages_tenant_customer ON messages (tenant_id, customer_id, created_at);

        CREATE TABLE audit_logs (
          id           UUID PRIMARY KEY,
          tenant_id    UUID NOT NULL REFERENCES tenants(id),
          event_type   VARCHAR(64) NOT NULL,
          actor        VARCHAR(128) NOT NULL,
          entity_type  VARCHAR(64),
          entity_id    VARCHAR(255),
          details      JSONB NOT NULL DEFAULT '{}'::jsonb,
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_audit_tenant_created ON audit_logs (tenant_id, created_at DESC);
        """
    )

    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenants FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON tenants USING (id = {CURRENT_TENANT}) WITH CHECK (id = {CURRENT_TENANT})")
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = {CURRENT_TENANT}) WITH CHECK (tenant_id = {CURRENT_TENANT})"
        )

    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    for table in ["tenants", "workers", "whatsapp_numbers", "customers", "conversations", "messages"]:
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO {APP_ROLE}")
    # G7: audit log is append-only for the application role
    op.execute(f"GRANT SELECT, INSERT ON audit_logs TO {APP_ROLE}")


def downgrade():
    for table in ["audit_logs", "messages", "conversations", "customers", "whatsapp_numbers", "workers", "tenants"]:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
