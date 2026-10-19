"""Kafka reliability: transactional outbox + durable processed-event ledger (Phase 6).

Revision ID: 0004
Revises: 0003

Additive only: new tables, no change to existing ones, so services built before this
revision keep working while the migration runs.
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

APP_ROLE = "zuvo_app"
CURRENT_TENANT = "NULLIF(current_setting('app.current_tenant', true), '')::uuid"
TABLES = ("outbox_events", "processed_events")


def upgrade():
    op.execute(
        """
        CREATE TABLE outbox_events (
          id              VARCHAR(64) PRIMARY KEY,           -- = envelope.event_id (stable -> enqueue is idempotent)
          tenant_id       UUID NOT NULL REFERENCES tenants(id),
          topic           VARCHAR(128) NOT NULL,
          msg_key         VARCHAR(255),
          payload         TEXT NOT NULL,                     -- exact envelope JSON, published byte-for-byte
          headers         JSONB NOT NULL DEFAULT '[]'::jsonb,-- [[name, value], ...] captured at enqueue (incl. trace context)
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          attempts        INTEGER NOT NULL DEFAULT 0,
          last_error      TEXT,
          published_at    TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX ix_outbox_pending ON outbox_events (tenant_id, created_at) WHERE published_at IS NULL")
    op.execute("CREATE INDEX ix_outbox_published ON outbox_events (published_at) WHERE published_at IS NOT NULL")
    op.execute(
        """
        CREATE TABLE processed_events (
          tenant_id      UUID NOT NULL,
          consumer_group VARCHAR(64) NOT NULL,
          event_id       VARCHAR(64) NOT NULL,
          event_type     VARCHAR(128),
          processed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, consumer_group, event_id)
        )
        """
    )
    op.execute("CREATE INDEX ix_processed_events_at ON processed_events (processed_at)")
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = {CURRENT_TENANT}) WITH CHECK (tenant_id = {CURRENT_TENANT})"
        )
        # DELETE is needed only for retention cleanup (published outbox rows, expired ledger rows)
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}")


def downgrade():
    for table in TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")
