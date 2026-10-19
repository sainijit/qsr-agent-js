"""zuvo-ai-rag: pgvector embeddings + full-text on kb_chunks, document bodies and index state.

Revision ID: 0003
Revises: 0002
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

EMBED_DIM = 768  # must match zuvo-ai-rag config gemini.embed_dimensions


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        ALTER TABLE kb_documents
          ADD COLUMN body             TEXT,
          ADD COLUMN indexed_checksum VARCHAR(64),
          ADD COLUMN indexed_model    VARCHAR(64),
          ADD COLUMN indexed_at       TIMESTAMPTZ,
          ADD COLUMN index_error      TEXT,
          ADD COLUMN index_error_at   TIMESTAMPTZ
        """
    )
    op.execute(
        f"""
        ALTER TABLE kb_chunks
          ADD COLUMN section     TEXT,
          ADD COLUMN char_count  INTEGER,
          ADD COLUMN embedding   vector({EMBED_DIM}),
          ADD COLUMN embed_model VARCHAR(64),
          ADD COLUMN created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
          ADD COLUMN tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', coalesce(section, '') || ' ' || text)) STORED
        """
    )
    op.execute("CREATE INDEX ix_kb_chunks_embedding ON kb_chunks USING hnsw (embedding vector_cosine_ops)")
    op.execute("CREATE INDEX ix_kb_chunks_tsv ON kb_chunks USING gin (tsv)")
    op.execute("CREATE INDEX ix_kb_documents_pending ON kb_documents (tenant_id) WHERE is_active AND body IS NOT NULL")


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_kb_documents_pending")
    op.execute("DROP INDEX IF EXISTS ix_kb_chunks_tsv")
    op.execute("DROP INDEX IF EXISTS ix_kb_chunks_embedding")
    op.execute("ALTER TABLE kb_chunks DROP COLUMN tsv, DROP COLUMN created_at, DROP COLUMN embed_model, "
               "DROP COLUMN embedding, DROP COLUMN char_count, DROP COLUMN section")
    op.execute("ALTER TABLE kb_documents DROP COLUMN index_error_at, DROP COLUMN index_error, DROP COLUMN indexed_at, "
               "DROP COLUMN indexed_model, DROP COLUMN indexed_checksum, DROP COLUMN body")
