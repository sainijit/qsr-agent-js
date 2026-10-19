"""Tenant onboarding: validate a bundle, then apply it idempotently to the database.

apply = upsert tenant/workers/numbers -> activate changed config versions -> import pricing (if
changed) -> (re)chunk changed KB documents -> ensure dashboard users + memberships.
Re-running with unchanged files is a no-op apart from timestamps.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable, Optional

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .bundle import TenantBundle, sha256_text
from .db import tenant_session
from .models import (KbChunk, KbDocument, Membership, PriceItem, PriceList, Tenant, TenantConfigVersion, User,
                     WhatsAppNumber, Worker)
from .passwords import hash_password
from .pricing import blocking_errors, chunk_text, parse_pricing_csv
from .tenancy import number_uuid, worker_uuid

_NS = uuid.UUID("0c3c8f8e-3d55-4a77-8d0b-6a1f5e0b7c11")


def user_uuid(email: str) -> str:
    return str(uuid.uuid5(_NS, f"user:{email.lower()}"))


def validate_bundle(bundle: TenantBundle, others: Iterable[TenantBundle] = (), legacy_pnids: dict[str, str] | None = None) -> list[str]:
    errors: list[str] = []
    c = bundle.config
    taken: dict[str, str] = dict(legacy_pnids or {})
    for o in others:
        if o.tenant_id == bundle.tenant_id:
            continue
        if o.slug == bundle.slug:
            errors.append(f"slug {bundle.slug} already used by tenant {o.tenant_id}")
        for n in o.config.numbers:
            taken[n.meta_phone_number_id] = o.slug
    for n in c.numbers:
        if n.meta_phone_number_id in taken and taken[n.meta_phone_number_id] != bundle.slug:
            errors.append(f"phone_number_id {n.meta_phone_number_id} already belongs to {taken[n.meta_phone_number_id]}")
    if not c.numbers:
        errors.append("at least one WhatsApp number is required")
    if not c.owner():
        errors.append("an OWNER worker is required (receives approvals)")
    if bundle.pricing_text is not None:
        rows, perr = parse_pricing_csv(bundle.pricing_text)
        errors += [f"{c.pricing.source}: {e}" for e in blocking_errors(perr)]
        if not rows and not perr:
            errors.append(f"{c.pricing.source}: no rows")
        for r in rows:
            if r.price is None and r.price_min is None:
                pass  # allowed: the agent will route "price on request" items to approval
    if not bundle.skill_md.strip():
        errors.append("skill.md is empty")
    for r in c.followups:
        if r.mode == "auto" and r.when in ("no_reply", "dormant") and not r.template and not r.text:
            errors.append(f"followup {r.id}: auto mode needs text (inside 24h) or a template (outside 24h)")
    return errors


async def _activate_version(session: AsyncSession, tenant_id: str, kind: str, checksum: str, content: Optional[str], actor: str) -> bool:
    current = (await session.execute(
        select(TenantConfigVersion).where(TenantConfigVersion.tenant_id == tenant_id, TenantConfigVersion.kind == kind,
                                          TenantConfigVersion.status == "ACTIVE")
    )).scalars().first()
    if current and current.checksum == checksum:
        return False
    version = (await session.scalar(
        select(TenantConfigVersion.version).where(TenantConfigVersion.tenant_id == tenant_id, TenantConfigVersion.kind == kind)
        .order_by(TenantConfigVersion.version.desc()).limit(1)
    )) or 0
    if current:
        current.status = "ARCHIVED"
    session.add(TenantConfigVersion(id=str(uuid.uuid4()), tenant_id=tenant_id, kind=kind, version=version + 1,
                                    checksum=checksum, content=content, status="ACTIVE", created_by=actor))
    return True


async def import_pricing(session: AsyncSession, tenant_id: str, csv_text: str, source_file: str, actor: str,
                         currency: str = "INR") -> dict:
    """Replaces the active price list if the CSV changed. Raises ValueError on blocking errors."""
    rows, errors = parse_pricing_csv(csv_text)
    if blocking_errors(errors):
        raise ValueError("; ".join(blocking_errors(errors)[:10]))
    checksum = sha256_text(csv_text)
    active = (await session.execute(
        select(PriceList).where(PriceList.tenant_id == tenant_id, PriceList.is_active.is_(True))
    )).scalars().first()
    if active and active.checksum == checksum:
        return {"changed": False, "version": active.version, "rows": active.row_count}
    version = (await session.scalar(
        select(PriceList.version).where(PriceList.tenant_id == tenant_id).order_by(PriceList.version.desc()).limit(1)
    )) or 0
    await session.execute(update(PriceList).where(PriceList.tenant_id == tenant_id).values(is_active=False))
    pl = PriceList(id=str(uuid.uuid4()), tenant_id=tenant_id, version=version + 1, source_file=source_file,
                   checksum=checksum, row_count=len(rows), is_active=True, imported_by=actor)
    session.add(pl)
    await session.flush()
    for r in rows:
        session.add(PriceItem(
            id=str(uuid.uuid4()), tenant_id=tenant_id, price_list_id=pl.id, sku=r.sku, name=r.name, category=r.category or None,
            attrs=r.attrs, price=_dec(r.price), price_min=_dec(r.price_min), price_max=_dec(r.price_max), currency=currency,
            unit=r.unit, tax_included=r.tax_included, valid_until=r.valid_until, aliases=r.aliases, notes=r.notes or None,
        ))
    await _activate_version(session, tenant_id, "pricing", checksum, csv_text, actor)
    return {"changed": True, "version": pl.version, "rows": len(rows), "warnings": errors}


def _dec(v: Optional[float]) -> Optional[Decimal]:
    return Decimal(str(v)) if v is not None else None


async def sync_knowledge(session: AsyncSession, tenant_id: str, bundle: TenantBundle) -> dict:
    existing = {d.source_path: d for d in (await session.execute(
        select(KbDocument).where(KbDocument.tenant_id == tenant_id))).scalars().all()}
    changed = 0
    seen = set()
    for doc in bundle.knowledge:
        seen.add(doc.source_path)
        row = existing.get(doc.source_path)
        if row and row.checksum == doc.checksum and row.is_active:
            if row.body is None:
                row.body = doc.text  # backfill for zuvo-ai-rag; chunks unchanged
            continue
        if row:
            # replace only the lexical chunks; the embedded chunks of the previous version keep serving RAG until
            # zuvo-ai-rag has fully indexed the new version and swaps them in one transaction
            await session.execute(text("DELETE FROM kb_chunks WHERE document_id = :d AND embed_model IS NULL"), {"d": row.id})
            await session.execute(text(
                "UPDATE kb_documents SET indexed_checksum = NULL, index_error = NULL, index_error_at = NULL, "
                "index_state = 'pending', index_attempts = 0 WHERE id = :d"), {"d": row.id})
            row.title, row.checksum, row.version, row.is_active = doc.title, doc.checksum, row.version + 1, True
            row.body = doc.text
            row.updated_at = datetime.now(timezone.utc)
        else:
            row = KbDocument(id=str(uuid.uuid4()), tenant_id=tenant_id, title=doc.title, source_path=doc.source_path,
                             version=1, checksum=doc.checksum, is_active=True, body=doc.text)
            session.add(row)
            await session.flush()
        # lexical chunks (fallback); zuvo-ai-rag replaces them with embedded chunks when it indexes the document
        for i, chunk in enumerate(chunk_text(doc.text)):
            session.add(KbChunk(id=str(uuid.uuid4()), tenant_id=tenant_id, document_id=row.id, ordinal=i, text=chunk))
        changed += 1
    for path, row in existing.items():
        # only bundle documents are managed here; dashboard uploads (uploads/...) are left alone
        if path.startswith("knowledge/") and path not in seen and row.is_active:
            row.is_active = False
            changed += 1
    return {"documents": len(bundle.knowledge), "changed": changed}


async def ensure_user(session: AsyncSession, tenant_id: str, email: str, name: str, role: str, worker_id: Optional[str],
                      password: Optional[str]) -> str:
    uid = user_uuid(email)
    values = {"id": uid, "email": email.lower(), "display_name": name}
    if password:
        values["password_hash"] = hash_password(password)
    set_ = {"display_name": name}
    if password:
        set_["password_hash"] = values["password_hash"]
    await session.execute(insert(User).values(**values).on_conflict_do_update(index_elements=[User.email], set_=set_))
    await session.execute(
        insert(Membership).values(id=str(uuid.uuid5(_NS, f"m:{uid}:{tenant_id}")), user_id=uid, tenant_id=tenant_id, role=role, worker_id=worker_id)
        .on_conflict_do_update(index_elements=[Membership.user_id, Membership.tenant_id], set_={"role": role, "worker_id": worker_id})
    )
    return uid


async def apply_bundle(sm: async_sessionmaker[AsyncSession], bundle: TenantBundle, actor: str = "onboarding",
                       passwords: Optional[dict[str, str]] = None) -> dict:
    """passwords: email -> plaintext (only set/reset for listed emails)."""
    c = bundle.config
    tid = bundle.tenant_id
    passwords = {k.lower(): v for k, v in (passwords or {}).items()}
    default_pw = os.environ.get("ZUVO_ONBOARD_DEFAULT_PASSWORD")
    summary: dict = {"tenant": c.tenant.slug, "tenant_id": tid}
    async with tenant_session(sm, tid) as s:
        await s.execute(
            insert(Tenant).values(
                id=tid, slug=c.tenant.slug, name=c.tenant.name, city=c.tenant.city, currency=c.tenant.currency,
                industry=c.tenant.industry, timezone=c.tenant.timezone, status=c.tenant.status, plan=c.tenant.plan,
                monthly_ai_budget_inr=Decimal(str(c.tenant.monthly_ai_budget_inr)), onboarded_at=datetime.now(timezone.utc),
            ).on_conflict_do_update(index_elements=[Tenant.id], set_={
                "slug": c.tenant.slug, "name": c.tenant.name, "city": c.tenant.city, "currency": c.tenant.currency,
                "industry": c.tenant.industry, "timezone": c.tenant.timezone, "status": c.tenant.status, "plan": c.tenant.plan,
                "monthly_ai_budget_inr": Decimal(str(c.tenant.monthly_ai_budget_inr)),
            })
        )
        for w in c.workers:
            wid = worker_uuid(tid, w.key)
            await s.execute(
                insert(Worker).values(id=wid, tenant_id=tid, worker_key=w.key, display_name=w.name, role=w.role, whatsapp_phone=w.whatsapp)
                .on_conflict_do_update(index_elements=[Worker.id], set_={"display_name": w.name, "role": w.role, "whatsapp_phone": w.whatsapp, "is_active": True})
            )
        keep = []
        for n in c.numbers:
            nid = number_uuid(tid, n.meta_phone_number_id)
            keep.append(nid)
            vals = dict(worker_id=worker_uuid(tid, n.default_worker), display_phone_number=n.display, status=n.status,
                        waba_id=n.waba_id, label=n.label, purpose=n.purpose)
            await s.execute(
                insert(WhatsAppNumber).values(id=nid, tenant_id=tid, meta_phone_number_id=n.meta_phone_number_id, **vals)
                .on_conflict_do_update(index_elements=[WhatsAppNumber.id], set_=vals)
            )
        await s.execute(update(WhatsAppNumber).where(WhatsAppNumber.tenant_id == tid, WhatsAppNumber.id.not_in(keep))
                        .values(status="REVOKED"))
        summary["numbers"] = len(c.numbers)
        summary["workers"] = len(c.workers)

        sums = bundle.checksums()
        summary["config_changed"] = await _activate_version(s, tid, "config", sums["config"], bundle.config_text, actor)
        summary["skill_changed"] = await _activate_version(s, tid, "skill", sums["skill"], bundle.skill_md, actor)
        summary["knowledge"] = await sync_knowledge(s, tid, bundle)
        await _activate_version(s, tid, "knowledge", sums["knowledge"], None, actor)
        if bundle.pricing_text is not None:
            summary["pricing"] = await import_pricing(s, tid, bundle.pricing_text, c.pricing.source, actor, c.tenant.currency)

        users = []
        for w in c.workers:
            if not w.email:
                continue
            pw = passwords.get(w.email.lower())
            if pw is None and default_pw:
                exists = await s.scalar(select(User.password_hash).where(User.email == w.email.lower()))
                pw = None if exists else default_pw
            await ensure_user(s, tid, w.email, w.name, w.role, worker_uuid(tid, w.key), pw)
            users.append(w.email.lower())
        summary["users"] = users
    return summary
