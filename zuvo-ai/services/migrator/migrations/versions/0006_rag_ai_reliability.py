"""RAG / AI execution reliability: explicit index state and agent-run diagnostics (Phase 8).

Revision ID: 0006
Revises: 0005

Additive only.
* kb_documents.index_state: pending -> processing -> indexed | failed. The previous indexed chunks stay
  searchable until a new version's chunks replace them in one transaction, so a partially indexed version is never active.
* agent_runs.outcome / diagnostics: success-or-failure category and privacy-conscious decision metadata
  (ids, scores, models, latencies). No prompts, no customer text, no model reasoning.
"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        ALTER TABLE kb_documents
          ADD COLUMN index_state      VARCHAR(16) NOT NULL DEFAULT 'pending',
          ADD COLUMN index_attempts   INTEGER NOT NULL DEFAULT 0,
          ADD COLUMN index_started_at TIMESTAMPTZ,
          ADD COLUMN indexed_version  INTEGER,
          ADD COLUMN chunk_count      INTEGER,
          ADD CONSTRAINT ck_kb_documents_index_state CHECK (index_state IN ('pending','processing','indexed','failed'))
        """
    )
    op.execute(
        """
        UPDATE kb_documents d SET
          index_state = CASE WHEN d.indexed_checksum = d.checksum THEN 'indexed'
                             WHEN d.index_error IS NOT NULL THEN 'failed' ELSE 'pending' END,
          index_attempts = CASE WHEN d.index_error IS NOT NULL THEN 1 ELSE 0 END,
          indexed_version = CASE WHEN d.indexed_checksum = d.checksum THEN d.version END,
          chunk_count = (SELECT count(*) FROM kb_chunks c WHERE c.document_id = d.id)
        """
    )
    op.execute(
        """
        ALTER TABLE agent_runs
          ADD COLUMN outcome     VARCHAR(32),
          ADD COLUMN diagnostics JSONB NOT NULL DEFAULT '{}'::jsonb
        """
    )
    op.execute("CREATE INDEX ix_agent_runs_outcome ON agent_runs (tenant_id, created_at) WHERE outcome IS NOT NULL AND outcome <> 'ok'")


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_agent_runs_outcome")
    op.execute("ALTER TABLE agent_runs DROP COLUMN diagnostics, DROP COLUMN outcome")
    op.execute("ALTER TABLE kb_documents DROP CONSTRAINT ck_kb_documents_index_state, DROP COLUMN chunk_count, "
               "DROP COLUMN indexed_version, DROP COLUMN index_started_at, DROP COLUMN index_attempts, DROP COLUMN index_state")
