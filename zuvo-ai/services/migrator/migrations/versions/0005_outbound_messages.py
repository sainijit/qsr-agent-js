"""Safe outbound delivery: durable per-request send state (Phase 7).

Revision ID: 0005
Revises: 0004

Additive only. `messages` stays the conversation view; `outbound_messages` is the
delivery ledger that decides whether a Meta send may (still) be attempted.
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

APP_ROLE = "zuvo_app"
CURRENT_TENANT = "NULLIF(current_setting('app.current_tenant', true), '')::uuid"


def upgrade():
    op.execute(
        """
        CREATE TABLE outbound_messages (
          id                  UUID PRIMARY KEY,               -- uuid5(tenant, request_id); sent to Meta as biz_opaque_callback_data
          tenant_id           UUID NOT NULL REFERENCES tenants(id),
          request_id          VARCHAR(64) NOT NULL,
          whatsapp_number_id  UUID NOT NULL,
          customer_id         UUID,
          conversation_id     UUID,
          recipient_phone     VARCHAR(32) NOT NULL,
          kind                VARCHAR(16) NOT NULL,            -- text | template
          template_name       VARCHAR(128),
          sent_by             VARCHAR(16) NOT NULL,
          followup_task_id    UUID,
          status              VARCHAR(16) NOT NULL,            -- PENDING | SENDING | ACCEPTED | FAILED | UNKNOWN
          attempts            INTEGER NOT NULL DEFAULT 0,
          last_error_category VARCHAR(32),                     -- policy | not_sent | rejected | ambiguous | interrupted | retry_exhausted | meta_failed | manual
          last_error          VARCHAR(200),
          next_attempt_at     TIMESTAMPTZ,
          request             JSONB,                           -- retry input; cleared once terminal (data minimisation)
          wamid               VARCHAR(255),
          delivery_status     VARCHAR(16),                     -- sent | delivered | read | failed (Meta webhooks)
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          sending_at          TIMESTAMPTZ,
          accepted_at         TIMESTAMPTZ,
          delivered_at        TIMESTAMPTZ,
          read_at             TIMESTAMPTZ,
          failed_at           TIMESTAMPTZ,
          reconciled_at       TIMESTAMPTZ,
          reconciled_by       VARCHAR(128),
          CONSTRAINT uq_outbound_request UNIQUE (tenant_id, request_id),
          CONSTRAINT ck_outbound_status CHECK (status IN ('PENDING','SENDING','ACCEPTED','FAILED','UNKNOWN'))
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_outbound_wamid ON outbound_messages (tenant_id, wamid) WHERE wamid IS NOT NULL")
    op.execute("CREATE INDEX ix_outbound_open ON outbound_messages (tenant_id, status, next_attempt_at) "
               "WHERE status IN ('PENDING','SENDING','UNKNOWN')")
    op.execute("ALTER TABLE outbound_messages ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE outbound_messages FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON outbound_messages "
        f"USING (tenant_id = {CURRENT_TENANT}) WITH CHECK (tenant_id = {CURRENT_TENANT})"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON outbound_messages TO {APP_ROLE}")  # audit history: no DELETE


def downgrade():
    op.execute("DROP TABLE IF EXISTS outbound_messages")
