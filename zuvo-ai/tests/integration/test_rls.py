"""Tenant-isolation tests against a real PostgreSQL (Phase 4).

Opt-in and non-destructive: seeds two synthetic tenants with random ids, asserts
as the application role, and deletes only those tenants' rows afterwards.

    ZUVO_RLS_APP_URL=postgresql+asyncpg://zuvo_app:...@HOST:5432/zuvo_db \
    ZUVO_RLS_OWNER_URL=postgresql+asyncpg://zuvo_owner:...@HOST:5432/zuvo_db \
    pytest tests/integration/test_rls.py

Refuses to run with APP_ENV=production. `scripts/test_rls.sh` wires this up for the local compose stack.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from zuvo_common.db import tenant_session
from zuvo_common.events import Envelope

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "zuvo-ai-rag"))
from services.store import PgVectorStore  # noqa: E402

APP_URL = os.environ.get("ZUVO_RLS_APP_URL")
OWNER_URL = os.environ.get("ZUVO_RLS_OWNER_URL")

pytestmark = [
    pytest.mark.skipif(not (APP_URL and OWNER_URL), reason="set ZUVO_RLS_APP_URL and ZUVO_RLS_OWNER_URL"),
    pytest.mark.skipif(os.environ.get("APP_ENV", "").lower() == "production", reason="never against production"),
]

MODEL = "rls-test-model"
DIM = 768

# intentionally outside RLS (login runs before a tenant is known); everything else must be protected
PLATFORM_TABLES = {"users", "memberships", "alembic_version"}
SEEDED = ["workers", "whatsapp_numbers", "customers", "conversations", "messages", "leads", "followup_tasks",
          "agent_runs", "approvals", "audit_logs", "kb_documents", "kb_chunks"]

SEED_SQL = [
    "INSERT INTO tenants (id, slug, name) VALUES (:tenant, :slug, :name)",
    "INSERT INTO workers (id, tenant_id, worker_key, display_name, role) VALUES (:worker, :tenant, 'w1', 'Worker', 'OWNER')",
    """INSERT INTO whatsapp_numbers (id, tenant_id, worker_id, meta_phone_number_id, display_phone_number)
       VALUES (:number, :tenant, :worker, :phone_id, '+910000000000')""",
    "INSERT INTO customers (id, tenant_id, phone, opted_out) VALUES (:customer, :tenant, :phone, false)",
    """INSERT INTO conversations (id, tenant_id, whatsapp_number_id, worker_id, customer_id)
       VALUES (:conv, :tenant, :number, :worker, :customer)""",
    """INSERT INTO messages (id, tenant_id, conversation_id, whatsapp_number_id, worker_id, customer_id, wamid, direction)
       VALUES (:msg, :tenant, :conv, :number, :worker, :customer, :wamid, 'INBOUND')""",
    "INSERT INTO leads (id, tenant_id, customer_id) VALUES (:lead, :tenant, :customer)",
    """INSERT INTO followup_tasks (id, tenant_id, lead_id, rule_id, kind, due_at, mode, status)
       VALUES (:task, :tenant, :lead, 'rls-test', 'reminder', now() - interval '1 minute', 'auto', 'SCHEDULED')""",
    "INSERT INTO agent_runs (id, tenant_id, task, decision) VALUES (:run, :tenant, 'faq', 'SKIPPED')",
    "INSERT INTO approvals (id, tenant_id, kind, proposed_text) VALUES (:approval, :tenant, 'reply', 'secret draft')",
    "INSERT INTO audit_logs (id, tenant_id, event_type, actor) VALUES (:audit, :tenant, 'RLS_TEST', 'test')",
    """INSERT INTO kb_documents (id, tenant_id, title, source_path, checksum, body)
       VALUES (:doc, :tenant, :doc_title, :doc_path, 'c1', :doc_body)""",
    """INSERT INTO kb_chunks (id, tenant_id, document_id, ordinal, text, embedding, embed_model)
       VALUES (:chunk, :tenant, :doc, 0, :doc_body, CAST(:vec AS vector), :model)""",
]


def _vec(hot: int) -> list[float]:
    v = [0.0] * DIM
    v[hot] = 1.0
    return v


def _ids() -> dict:
    return {k: str(uuid.uuid4()) for k in
            ("tenant", "worker", "number", "customer", "conv", "msg", "lead", "task", "run", "approval", "audit", "doc", "chunk")}


def _params(i: dict, tag: str, hot: int) -> dict:
    return {**i, "slug": f"rls-test-{i['tenant'][:8]}", "name": f"RLS test {tag}", "phone_id": f"rls-{i['tenant'][:12]}",
            "phone": f"+91999{hot:07d}", "wamid": f"wamid.rls.{i['msg']}", "doc_title": f"{tag} price list",
            "doc_path": f"knowledge/{tag}.md", "doc_body": f"{tag} confidential pricing zebracorn {tag}",
            "vec": "[" + ",".join(f"{x:g}" for x in _vec(hot)) + "]", "model": MODEL}


# written by the code under test (outbox / ledger), never seeded
PRODUCED = ["outbox_events", "processed_events", "outbound_messages"]


async def _cleanup(owner_sm, tenant_ids: list[str]) -> None:
    async with owner_sm.begin() as s:
        for table in PRODUCED:
            await s.execute(text(f"DELETE FROM {table} WHERE tenant_id = ANY(CAST(:t AS uuid[]))"), {"t": tenant_ids})
        for table in reversed(SEEDED):
            await s.execute(text(f"DELETE FROM {table} WHERE tenant_id = ANY(CAST(:t AS uuid[]))"), {"t": tenant_ids})
        await s.execute(text("DELETE FROM tenants WHERE id = ANY(CAST(:t AS uuid[]))"), {"t": tenant_ids})


@pytest_asyncio.fixture
async def world():
    owner = create_async_engine(OWNER_URL, pool_size=1, max_overflow=0)
    app = create_async_engine(APP_URL, pool_size=1, max_overflow=0)  # one connection => every session reuses it
    owner_sm = async_sessionmaker(owner, expire_on_commit=False)
    app_sm = async_sessionmaker(app, expire_on_commit=False)
    a, b = _ids(), _ids()
    try:
        async with owner_sm.begin() as s:
            for i, tag, hot in ((a, "alpha", 1), (b, "bravo", 2)):
                p = _params(i, tag, hot)
                for sql in SEED_SQL:
                    await s.execute(text(sql), p)
        yield SimpleNamespace(a=a, b=b, sm=app_sm, owner_sm=owner_sm)
    finally:
        await _cleanup(owner_sm, [a["tenant"], b["tenant"]])
        await app.dispose()
        await owner.dispose()


# ---------------------------------------------------------------- privileges & catalogue
@pytest.mark.asyncio
async def test_app_role_cannot_bypass_rls(world):
    async with world.sm() as s:
        row = (await s.execute(text(
            """SELECT r.rolsuper, r.rolbypassrls, r.rolcreaterole, r.rolcreatedb,
                      (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = 'public' AND c.relowner = r.oid)
               FROM pg_roles r WHERE r.rolname = current_user"""))).one()
    assert tuple(row) == (False, False, False, False, 0), "app role must be non-super, NOBYPASSRLS and own no tables"


@pytest.mark.asyncio
async def test_every_tenant_table_has_forced_rls(world):
    async with world.owner_sm() as s:
        rows = (await s.execute(text(
            """SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                      EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid)
               FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname = 'public' AND c.relkind = 'r'"""))).all()
    unprotected = [r[0] for r in rows if r[0] not in PLATFORM_TABLES and not (r[1] and r[2] and r[3])]
    assert unprotected == []


# ---------------------------------------------------------------- 1. reads
@pytest.mark.asyncio
@pytest.mark.parametrize("table", SEEDED + ["tenants"])
async def test_tenant_cannot_read_other_tenant(world, table):
    col = "id" if table == "tenants" else "tenant_id"
    async with tenant_session(world.sm, world.a["tenant"]) as s:
        other = (await s.execute(text(f"SELECT count(*) FROM {table} WHERE {col} = :b"), {"b": world.b["tenant"]})).scalar()
        visible = {str(x) for x in (await s.execute(text(f"SELECT DISTINCT {col} FROM {table}"))).scalars()}
    assert other == 0
    assert visible == {world.a["tenant"]}


# ---------------------------------------------------------------- 2. writes
@pytest.mark.asyncio
async def test_tenant_cannot_update_or_delete_other_tenant(world):
    async with tenant_session(world.sm, world.a["tenant"]) as s:
        upd = await s.execute(text("UPDATE leads SET lost_reason = 'pwned' WHERE id = :id"), {"id": world.b["lead"]})
        appr = await s.execute(text("UPDATE approvals SET proposed_text = 'pwned' WHERE id = :id"), {"id": world.b["approval"]})
        dele = await s.execute(text("DELETE FROM kb_chunks WHERE id = :id"), {"id": world.b["chunk"]})
    assert (upd.rowcount, appr.rowcount, dele.rowcount) == (0, 0, 0)
    async with world.owner_sm() as s:
        assert (await s.execute(text("SELECT proposed_text FROM approvals WHERE id = :id"),
                                {"id": world.b["approval"]})).scalar() == "secret draft"
        assert (await s.execute(text("SELECT count(*) FROM kb_chunks WHERE id = :id"), {"id": world.b["chunk"]})).scalar() == 1


@pytest.mark.asyncio
async def test_tenant_cannot_insert_into_other_tenant(world):
    with pytest.raises(DBAPIError, match="row-level security"):
        async with tenant_session(world.sm, world.a["tenant"]) as s:
            await s.execute(text("INSERT INTO audit_logs (id, tenant_id, event_type, actor) VALUES (:id, :b, 'X', 'x')"),
                            {"id": str(uuid.uuid4()), "b": world.b["tenant"]})


@pytest.mark.asyncio
async def test_tenant_cannot_move_row_to_other_tenant(world):
    with pytest.raises(DBAPIError, match="row-level security"):
        async with tenant_session(world.sm, world.a["tenant"]) as s:
            await s.execute(text("UPDATE leads SET tenant_id = :b WHERE id = :id"), {"b": world.b["tenant"], "id": world.a["lead"]})


@pytest.mark.asyncio
async def test_app_role_has_no_delete_on_business_tables(world):
    with pytest.raises(DBAPIError, match="permission denied"):
        async with tenant_session(world.sm, world.a["tenant"]) as s:
            await s.execute(text("DELETE FROM messages WHERE id = :id"), {"id": world.a["msg"]})


# ---------------------------------------------------------------- 3. no context fails closed
@pytest.mark.asyncio
@pytest.mark.parametrize("table", SEEDED + ["tenants"])
async def test_missing_context_sees_nothing(world, table):
    async with world.sm() as s:
        assert (await s.execute(text(f"SELECT count(*) FROM {table}"))).scalar() == 0


@pytest.mark.asyncio
async def test_missing_context_cannot_insert(world):
    with pytest.raises(DBAPIError, match="row-level security"):
        async with world.sm.begin() as s:
            await s.execute(text("INSERT INTO audit_logs (id, tenant_id, event_type, actor) VALUES (:id, :a, 'X', 'x')"),
                            {"id": str(uuid.uuid4()), "a": world.a["tenant"]})


@pytest.mark.asyncio
async def test_empty_context_sees_nothing(world):
    async with world.sm.begin() as s:
        await s.execute(text("SELECT set_config('app.current_tenant', '', true)"))
        assert (await s.execute(text("SELECT count(*) FROM leads"))).scalar() == 0


# ---------------------------------------------------------------- 4. background jobs
class _Bus:
    def __init__(self, fail: bool = False):
        self.events = []
        self.fail = fail

    async def publish(self, topic, env):
        self.events.append((topic, env))

    async def publish_raw(self, topic, value, key, headers):
        if self.fail:
            raise ConnectionError("kafka down")
        self.events.append((topic, Envelope.model_validate_json(value)))


@pytest.mark.asyncio
async def test_scheduler_claims_only_its_tenant(world):
    from followup_scheduler.main import Scheduler

    bus = _Bus()
    sched = Scheduler(world.sm, bus, routing=None)
    n = await sched.claim_due(SimpleNamespace(tenant_id=world.a["tenant"]))
    assert n == 1
    assert [e.tenant_id for _, e in bus.events] == [world.a["tenant"]]
    assert [e.payload["task_id"] for _, e in bus.events] == [world.a["task"]]
    async with world.owner_sm() as s:
        st = dict((await s.execute(text("SELECT id::text, status FROM followup_tasks WHERE id IN (:a, :b)"),
                                    {"a": world.a["task"], "b": world.b["task"]})).all())
    assert st == {world.a["task"]: "QUEUED", world.b["task"]: "SCHEDULED"}


@pytest.mark.asyncio
async def test_rag_indexer_queue_is_tenant_scoped(world):
    store = PgVectorStore()
    async with tenant_session(world.sm, world.a["tenant"]) as s:
        docs = await store.pending(s, "some-other-model", limit=100)
    assert [d.id for d in docs] == [world.a["doc"]]


# ---------------------------------------------------------------- 5. RAG retrieval
@pytest.mark.asyncio
async def test_rag_search_never_returns_other_tenant(world):
    store = PgVectorStore()
    # query = tenant B's exact embedding *and* keywords: the strongest possible cross-tenant match
    async with tenant_session(world.sm, world.a["tenant"]) as s:
        hits = await store.search(s, _vec(2), "bravo confidential pricing zebracorn", MODEL, fetch_k=50, rrf_k=60)
    assert hits, "tenant A should still retrieve its own chunk"
    assert {h.document_id for h in hits} == {world.a["doc"]}
    assert all("bravo" not in h.text for h in hits)


# ---------------------------------------------------------------- 6. connection reuse
@pytest.mark.asyncio
async def test_pooled_connection_does_not_leak_tenant_context(world):
    async with tenant_session(world.sm, world.a["tenant"]) as s:
        pid_a = (await s.execute(text("SELECT pg_backend_pid()"))).scalar()
        assert (await s.execute(text("SELECT count(*) FROM leads"))).scalar() == 1
    async with world.sm() as s:  # same physical connection (pool_size=1), no tenant set
        pid = (await s.execute(text("SELECT pg_backend_pid()"))).scalar()
        ctx = (await s.execute(text("SELECT current_setting('app.current_tenant', true)"))).scalar()
        leads = (await s.execute(text("SELECT count(*) FROM leads"))).scalar()
    assert pid == pid_a
    assert ctx in (None, "")
    assert leads == 0
    async with tenant_session(world.sm, world.b["tenant"]) as s:
        pid_b = (await s.execute(text("SELECT pg_backend_pid()"))).scalar()
        ids = {str(x) for x in (await s.execute(text("SELECT id FROM leads"))).scalars()}
    assert pid_b == pid_a and ids == {world.b["lead"]}


@pytest.mark.asyncio
async def test_context_does_not_survive_rollback(world):
    with pytest.raises(RuntimeError):
        async with tenant_session(world.sm, world.a["tenant"]) as s:
            await s.execute(text("SELECT 1"))
            raise RuntimeError("boom")
    async with world.sm() as s:
        assert (await s.execute(text("SELECT count(*) FROM leads"))).scalar() == 0
