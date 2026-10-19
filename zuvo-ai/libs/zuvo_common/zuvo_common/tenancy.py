"""Tenant discovery: `config/tenant-routing.yaml` (number -> tenant/worker) and
per-tenant policy `qsr/<slug>/skill.yaml`. Both hot-reload on file change."""
from __future__ import annotations

import os
import threading
import uuid
from enum import Enum
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from .phone import normalize_phone

_NS = uuid.UUID("6f1d2b1e-6c55-4c3a-9a51-3a7e8c1c2d00")


def worker_uuid(tenant_id: str, worker_key: str) -> str:
    return str(uuid.uuid5(_NS, f"worker:{tenant_id}:{worker_key}"))


def number_uuid(tenant_id: str, meta_phone_number_id: str) -> str:
    return str(uuid.uuid5(_NS, f"number:{tenant_id}:{meta_phone_number_id}"))


class Role(str, Enum):
    OWNER = "OWNER"
    MANAGER = "MANAGER"
    SALES_WORKER = "SALES_WORKER"


class NumberStatus(str, Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    REVOKED = "REVOKED"


class NumberEntry(BaseModel):
    meta_phone_number_id: str
    whatsapp_number: str
    worker_id: str
    worker_name: Optional[str] = None
    role: Role = Role.SALES_WORKER
    status: NumberStatus = NumberStatus.ACTIVE

    @field_validator("whatsapp_number")
    @classmethod
    def _norm(cls, v: str) -> str:
        return normalize_phone(v)


class TenantEntry(BaseModel):
    tenant_id: uuid.UUID
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    name: str
    city: Optional[str] = None
    currency: str = "INR"
    skill_file: str
    credit_default_days: int = 30
    numbers: list[NumberEntry] = Field(default_factory=list)


class TenantRoutingConfig(BaseModel):
    version: int
    tenants: list[TenantEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> "TenantRoutingConfig":
        ids, slugs, pnids = set(), set(), set()
        for t in self.tenants:
            if t.tenant_id in ids or t.slug in slugs:
                raise ValueError(f"duplicate tenant {t.slug}")
            ids.add(t.tenant_id)
            slugs.add(t.slug)
            for n in t.numbers:
                if n.meta_phone_number_id in pnids:
                    raise ValueError(f"phone_number_id {n.meta_phone_number_id} mapped twice")
                pnids.add(n.meta_phone_number_id)
        return self


class ResolvedLine(BaseModel):
    tenant_id: str
    tenant_slug: str
    tenant_name: str
    skill_file: str
    worker_key: str
    worker_id: str
    worker_name: str
    role: Role
    whatsapp_number_id: str
    meta_phone_number_id: str
    whatsapp_number: str
    status: NumberStatus


class _Reloading:
    def __init__(self, path: str):
        self.path = Path(path)
        self._mtime = -1.0
        self._lock = threading.Lock()

    @property
    def version(self) -> float:
        return self._mtime

    def _changed(self) -> bool:
        try:
            m = os.stat(self.path).st_mtime
        except FileNotFoundError:
            return False
        if m != self._mtime:
            self._mtime = m
            return True
        return False


def bundle_tenant_entry(bundle) -> TenantEntry:
    """Projects a tenant bundle (tenants/<slug>/config.yaml) onto the routing model."""
    c = bundle.config
    workers = {w.key: w for w in c.workers}
    return TenantEntry(
        tenant_id=c.tenant.id,
        slug=c.tenant.slug,
        name=c.tenant.name,
        city=c.tenant.city,
        currency=c.tenant.currency,
        skill_file=f"tenants/{c.tenant.slug}/config.yaml",
        numbers=[
            NumberEntry(
                meta_phone_number_id=n.meta_phone_number_id,
                whatsapp_number=n.display,
                worker_id=n.default_worker,
                worker_name=workers[n.default_worker].name,
                role=Role(workers[n.default_worker].role),
                status=NumberStatus(n.status),
            )
            for n in c.numbers
        ],
    )


class TenantRouting(_Reloading):
    """Number -> tenant resolver over `tenant-routing.yaml` (legacy) + `tenants/*/config.yaml` bundles.
    Keeps the last good config if a reload fails validation."""

    def __init__(self, path: str, bundles_dir: Optional[str] = None):
        from .bundle import BundleRegistry, default_bundles_dir

        super().__init__(path)
        self.bundles = BundleRegistry(bundles_dir or default_bundles_dir(path))
        self._bundles_version = -2.0
        self._config: Optional[TenantRoutingConfig] = None
        self._by_pnid: dict[str, ResolvedLine] = {}
        self._by_number: dict[str, ResolvedLine] = {}
        self.reload(force=True)

    def reload(self, force: bool = False) -> None:
        with self._lock:
            routing_changed = self._changed()
            self.bundles.reload()
            if not routing_changed and not force and self.bundles.version == self._bundles_version:
                return
            self._bundles_version = self.bundles.version
            raw = yaml.safe_load(self.path.read_text()) if self.path.exists() else {"version": 2}
            raw = raw or {"version": 2}
            legacy = list(raw.get("tenants") or [])
            bundled = [bundle_tenant_entry(b).model_dump(mode="json") for b in self.bundles.all()]
            bundled_ids = {b["tenant_id"] for b in bundled}
            raw["tenants"] = [t for t in legacy if str(t.get("tenant_id")) not in bundled_ids] + bundled
            config = TenantRoutingConfig.model_validate(raw)
            by_pnid, by_number = {}, {}
            for t in config.tenants:
                tid = str(t.tenant_id)
                for n in t.numbers:
                    line = ResolvedLine(
                        tenant_id=tid,
                        tenant_slug=t.slug,
                        tenant_name=t.name,
                        skill_file=t.skill_file,
                        worker_key=n.worker_id,
                        worker_id=worker_uuid(tid, n.worker_id),
                        worker_name=n.worker_name or n.worker_id,
                        role=n.role,
                        whatsapp_number_id=number_uuid(tid, n.meta_phone_number_id),
                        meta_phone_number_id=n.meta_phone_number_id,
                        whatsapp_number=n.whatsapp_number,
                        status=n.status,
                    )
                    by_pnid[n.meta_phone_number_id] = line
                    by_number[n.whatsapp_number] = line
            self._config, self._by_pnid, self._by_number = config, by_pnid, by_number

    def _maybe_reload(self) -> None:
        try:
            self.reload()
        except Exception:
            # keep serving the last valid config
            pass

    @property
    def config(self) -> TenantRoutingConfig:
        self._maybe_reload()
        assert self._config
        return self._config

    def resolve(self, meta_phone_number_id: Optional[str], display_number: Optional[str] = None) -> Optional[ResolvedLine]:
        self._maybe_reload()
        line = self._by_pnid.get(meta_phone_number_id or "")
        if not line and display_number:
            try:
                line = self._by_number.get(normalize_phone(display_number))
            except ValueError:
                line = None
        if line and line.status != NumberStatus.ACTIVE:
            return None
        return line

    def tenant(self, tenant_id: str) -> Optional[TenantEntry]:
        return next((t for t in self.config.tenants if str(t.tenant_id) == tenant_id), None)

    def lines_for_tenant(self, tenant_id: str) -> list[ResolvedLine]:
        self._maybe_reload()
        return [l for l in self._by_pnid.values() if l.tenant_id == tenant_id]

    def line_by_id(self, whatsapp_number_id: str) -> Optional[ResolvedLine]:
        self._maybe_reload()
        return next((l for l in self._by_pnid.values() if l.whatsapp_number_id == whatsapp_number_id), None)


# ------------------------------------------------------------- tenant policy


class IntentsPolicy(BaseModel):
    enabled: list[str]
    confidence_threshold: float = Field(0.80, ge=0, le=1)


class CreditPolicy(BaseModel):
    formula: str = "outstanding + draft_orders + new_order > credit_limit"
    on_breach: str = "PENDING_APPROVAL"


class GuardrailsPolicy(BaseModel):
    max_auto_discount_pct: float = 5
    credit: CreditPolicy = Field(default_factory=CreditPolicy)


class CatalogPolicy(BaseModel):
    source_tool: str = "search_item_catalog"
    clarify_before_quote: list[str] = Field(default_factory=list)


class AiRoutingPolicy(BaseModel):
    tier0_keywords: list[str] = Field(default_factory=list)
    tier1_model: Optional[str] = None
    tier2_model: Optional[str] = None
    tier3_model: Optional[str] = None


class DailyPulsePolicy(BaseModel):
    time: str = "20:00"
    timezone: str = "Asia/Kolkata"


class TenantPolicy(BaseModel):
    tenant_id: uuid.UUID
    language: list[str] = Field(default_factory=lambda: ["hinglish", "hindi", "english"])
    persona: str = "Polite trade assistant"
    intents: IntentsPolicy
    guardrails: GuardrailsPolicy = Field(default_factory=GuardrailsPolicy)
    catalog: CatalogPolicy = Field(default_factory=CatalogPolicy)
    ai_routing: AiRoutingPolicy = Field(default_factory=AiRoutingPolicy)
    daily_pulse: DailyPulsePolicy = Field(default_factory=DailyPulsePolicy)
    # bundle-only context for the extractor prompt
    business_name: Optional[str] = None
    industry: Optional[str] = None
    interests: list[str] = Field(default_factory=list)
    product_names: list[str] = Field(default_factory=list)


class TenantPolicyLoader:
    """Loads `skill_file` paths relative to `root`; refuses paths escaping the root."""

    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self._cache: dict[str, tuple[float, TenantPolicy]] = {}

    def load(self, skill_file: str, expected_tenant_id: str) -> TenantPolicy:
        path = (self.root / skill_file).resolve()
        if self.root not in path.parents:
            raise ValueError("skill_file escapes policy root")
        mtime = os.stat(path).st_mtime
        cached = self._cache.get(str(path))
        if cached and cached[0] == mtime:
            policy = cached[1]
        else:
            raw = yaml.safe_load(path.read_text())
            policy = policy_from_bundle_config(raw) if isinstance(raw, dict) and "tenant" in raw else TenantPolicy.model_validate(raw)
            self._cache[str(path)] = (mtime, policy)
        if str(policy.tenant_id) != expected_tenant_id:
            raise ValueError("skill file tenant_id does not match routing tenant")
        return policy


def policy_from_bundle_config(raw: dict) -> TenantPolicy:
    from .bundle import TenantConfig

    c = TenantConfig.model_validate(raw)
    return TenantPolicy(
        tenant_id=c.tenant.id,
        language=c.tenant.languages,
        persona=c.ai.persona,
        intents=IntentsPolicy(enabled=c.ai.intents_enabled, confidence_threshold=c.ai.confidence_threshold),
        guardrails=GuardrailsPolicy(),
        ai_routing=AiRoutingPolicy(tier0_keywords=c.ai.tier0_keywords),
        daily_pulse=DailyPulsePolicy(timezone=c.tenant.timezone),
        business_name=c.tenant.name,
        industry=c.tenant.industry,
        interests=list(c.lead.interests),
    )
