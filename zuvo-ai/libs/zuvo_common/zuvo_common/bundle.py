"""Tenant bundles: `tenants/<slug>/{config.yaml, skill.md, pricing.csv, knowledge/}`.

A bundle is the only per-business artefact. It is validated here, applied to the DB by the
onboarding job (`zuvo_common.onboarding`), and read live (hot-reload on mtime) by services
for routing, policy, follow-up rules and agent behaviour.
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
import uuid
from datetime import time as dtime
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from .phone import normalize_phone

DEFAULT_STAGES = ["NEW", "ENGAGED", "QUALIFIED", "VISIT_PLANNED", "VISITED", "WON", "LOST", "DORMANT"]
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
KNOWLEDGE_SUFFIXES = {".md", ".txt"}
_DURATION = re.compile(r"^(\d+)\s*([mhd])$")


def parse_duration_minutes(value: str) -> int:
    m = _DURATION.match(value.strip().lower())
    if not m:
        raise ValueError(f"bad duration {value!r}; use e.g. 90m, 24h, 30d")
    n, unit = int(m.group(1)), m.group(2)
    return n * {"m": 1, "h": 60, "d": 1440}[unit]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class TenantMeta(BaseModel):
    id: uuid.UUID
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    name: str
    city: Optional[str] = None
    industry: str = "general"
    timezone: str = "Asia/Kolkata"
    currency: str = "INR"
    languages: list[str] = Field(default_factory=lambda: ["hinglish", "hindi", "english"])
    plan: str = "pilot"
    monthly_ai_budget_inr: float = 500.0
    status: Literal["ONBOARDING", "ACTIVE", "PAUSED"] = "ACTIVE"


class WorkerCfg(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,99}$")
    name: str
    role: Literal["OWNER", "MANAGER", "SALES_WORKER"] = "SALES_WORKER"
    whatsapp: Optional[str] = None
    email: Optional[str] = None

    @field_validator("whatsapp")
    @classmethod
    def _norm(cls, v: Optional[str]) -> Optional[str]:
        return normalize_phone(v) if v else None


class NumberCfg(BaseModel):
    meta_phone_number_id: str
    display: str
    label: str = "Sales"
    purpose: Literal["sales", "service", "support"] = "sales"
    default_worker: str
    waba_id: Optional[str] = None
    status: Literal["ACTIVE", "PAUSED", "REVOKED"] = "ACTIVE"

    @field_validator("display")
    @classmethod
    def _norm(cls, v: str) -> str:
        return normalize_phone(v)


class BusinessHours(BaseModel):
    days: list[str] = Field(default_factory=lambda: ["mon", "tue", "wed", "thu", "fri", "sat"])
    open: str = "10:00"
    close: str = "19:00"

    @field_validator("days", mode="before")
    @classmethod
    def _days(cls, v):
        if isinstance(v, str):
            v = v.strip().lower()
            if "-" in v:
                a, b = (p.strip()[:3] for p in v.split("-", 1))
                i, j = WEEKDAYS.index(a), WEEKDAYS.index(b)
                return WEEKDAYS[i : j + 1] if i <= j else WEEKDAYS[i:] + WEEKDAYS[: j + 1]
            v = [p.strip() for p in v.split(",")]
        return [d.strip().lower()[:3] for d in v]

    @field_validator("open", "close")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        dtime.fromisoformat(v)
        return v

    @property
    def open_time(self) -> dtime:
        return dtime.fromisoformat(self.open)

    @property
    def close_time(self) -> dtime:
        return dtime.fromisoformat(self.close)


class AiCfg(BaseModel):
    persona: str = "Polite, helpful sales assistant"
    intents_enabled: list[str] = Field(
        default_factory=lambda: ["CREATE_ORDER", "INQUIRY_PRICE_STOCK", "PAYMENT_COMMITMENT", "COMPLAINT_RETURN",
                                 "GENERAL_CHAT", "VISIT_PLAN", "FAQ"]
    )
    confidence_threshold: float = Field(0.75, ge=0, le=1)
    tier0_keywords: list[str] = Field(default_factory=lambda: ["hi", "hello", "ok", "okay", "thanks", "thank you", "👍"])


class LeadCfg(BaseModel):
    stages: list[str] = Field(default_factory=lambda: list(DEFAULT_STAGES))
    qualify_fields: list[str] = Field(default_factory=lambda: ["interest", "budget", "timeline", "location"])
    interests: list[str] = Field(default_factory=list)
    dormant_after_days: int = Field(14, ge=1)


FollowupWhen = Literal["no_reply", "visit_planned", "visit_passed", "visited", "dormant"]


class FollowupRule(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_]{2,64}$")
    when: FollowupWhen
    after: Optional[str] = None
    before: Optional[str] = None
    max: int = Field(1, ge=1, le=10)
    mode: Literal["auto", "draft", "manual"] = "draft"
    template: Optional[str] = None
    text: str = ""
    enabled: bool = True

    @model_validator(mode="after")
    def _offset(self) -> "FollowupRule":
        if bool(self.after) == bool(self.before):
            raise ValueError(f"followup {self.id}: set exactly one of after/before")
        parse_duration_minutes(self.after or self.before or "")
        if self.before and self.when != "visit_planned":
            raise ValueError(f"followup {self.id}: 'before' is only valid for when=visit_planned")
        return self

    @property
    def offset_minutes(self) -> int:
        """Signed offset: negative = before the anchor time."""
        if self.before:
            return -parse_duration_minutes(self.before)
        return parse_duration_minutes(self.after or "0m")


class PricingCfg(BaseModel):
    source: str = "pricing.csv"
    answer_mode: Literal["auto", "draft"] = "draft"
    confidence_auto_min: float = Field(0.85, ge=0, le=1)
    always_approval_if: list[Literal["discount_request", "price_missing", "multiple_matches", "stale_price"]] = Field(
        default_factory=lambda: ["discount_request", "price_missing", "multiple_matches"]
    )
    disclaimer: str = ""
    max_variants_in_reply: int = Field(4, ge=1, le=10)


TaskMode = Literal["auto", "draft", "off"]


class AgentCfg(BaseModel):
    replies: dict[Literal["price", "faq", "visit_ack", "greeting", "general"], TaskMode] = Field(
        default_factory=lambda: {"price": "draft", "faq": "draft", "visit_ack": "draft", "greeting": "draft", "general": "off"}
    )
    faq_confidence_auto_min: float = Field(0.8, ge=0, le=1)
    holding_reply: str = "Thank you! Our team will confirm the details shortly."
    greeting_text: str = "Hello! Thank you for contacting {business}. How can we help you today?"
    visit_ack_text: str = "Great, we have noted your visit on {visit_when}. Our address: {address}. See you soon!"
    address: str = ""
    use_llm_drafting: bool = True

    @field_validator("replies", mode="before")
    @classmethod
    def _yaml_off(cls, v):
        # YAML 1.1 parses bare off/on as booleans
        if isinstance(v, dict):
            return {k: ("off" if x is False else "auto" if x is True else x) for k, x in v.items()}
        return v


class KnowledgeCfg(BaseModel):
    # lexical: in-process word matching over kb_chunks. rag: zuvo-ai-rag semantic retrieval
    # (falls back to lexical when the service is unavailable).
    retrieval: Literal["lexical", "rag"] = "lexical"
    min_similarity: Optional[float] = Field(None, ge=0, le=1)  # None = zuvo-ai-rag default
    top_k: int = Field(3, ge=1, le=10)


class ApprovalsCfg(BaseModel):
    sla_minutes: int = Field(15, ge=1)
    on_timeout: Literal["send_holding_reply", "none"] = "send_holding_reply"
    notify_owner_whatsapp: bool = False


class LearningCfg(BaseModel):
    enabled: bool = True
    min_examples_before_auto: int = Field(5, ge=1)
    auto_promote_rate: float = Field(0.9, ge=0, le=1)
    window: int = Field(20, ge=5, le=200)
    demote_on_reject: bool = True


class MetaTemplate(BaseModel):
    name: str
    language: str = "en_US"
    category: Literal["UTILITY", "MARKETING", "AUTHENTICATION"] = "UTILITY"
    params: list[str] = Field(default_factory=list)  # e.g. ["customer.name", "visit.when"]
    approved: bool = False  # set true once Meta approves it; unapproved templates fall back to drafts


class TenantConfig(BaseModel):
    tenant: TenantMeta
    numbers: list[NumberCfg] = Field(default_factory=list)
    workers: list[WorkerCfg] = Field(default_factory=list)
    business_hours: BusinessHours = Field(default_factory=BusinessHours)
    ai: AiCfg = Field(default_factory=AiCfg)
    lead: LeadCfg = Field(default_factory=LeadCfg)
    followups: list[FollowupRule] = Field(default_factory=list)
    pricing: PricingCfg = Field(default_factory=PricingCfg)
    agent: AgentCfg = Field(default_factory=AgentCfg)
    knowledge: KnowledgeCfg = Field(default_factory=KnowledgeCfg)
    approvals: ApprovalsCfg = Field(default_factory=ApprovalsCfg)
    learning: LearningCfg = Field(default_factory=LearningCfg)
    meta_templates: list[MetaTemplate] = Field(default_factory=list)
    opt_out_keywords: list[str] = Field(default_factory=lambda: ["stop", "unsubscribe", "band karo", "mat bhejo"])

    @model_validator(mode="after")
    def _refs(self) -> "TenantConfig":
        keys = [w.key for w in self.workers]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate worker key")
        pnids = [n.meta_phone_number_id for n in self.numbers]
        if len(pnids) != len(set(pnids)):
            raise ValueError("duplicate meta_phone_number_id")
        for n in self.numbers:
            if n.default_worker not in keys:
                raise ValueError(f"number {n.meta_phone_number_id}: unknown default_worker {n.default_worker}")
        ids = [r.id for r in self.followups]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate followup id")
        templates = {t.name for t in self.meta_templates}
        for r in self.followups:
            if r.template and r.template not in templates:
                raise ValueError(f"followup {r.id}: template {r.template} not declared in meta_templates")
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(self.tenant.timezone)
        except Exception as exc:
            raise ValueError(f"unknown timezone {self.tenant.timezone}") from exc
        return self

    def template(self, name: Optional[str]) -> Optional[MetaTemplate]:
        return next((t for t in self.meta_templates if t.name == name), None) if name else None

    def rule(self, rule_id: str) -> Optional[FollowupRule]:
        return next((r for r in self.followups if r.id == rule_id), None)

    def owner(self) -> Optional[WorkerCfg]:
        return next((w for w in self.workers if w.role == "OWNER"), None)

    def worker(self, key: str) -> Optional[WorkerCfg]:
        return next((w for w in self.workers if w.key == key), None)


class KnowledgeDoc(BaseModel):
    title: str
    source_path: str
    text: str
    checksum: str


class TenantBundle(BaseModel):
    path: str
    config: TenantConfig
    config_text: str
    skill_md: str = ""
    pricing_text: Optional[str] = None
    knowledge: list[KnowledgeDoc] = Field(default_factory=list)

    @property
    def tenant_id(self) -> str:
        return str(self.config.tenant.id)

    @property
    def slug(self) -> str:
        return self.config.tenant.slug

    def checksums(self) -> dict[str, str]:
        out = {"config": sha256_text(self.config_text), "skill": sha256_text(self.skill_md)}
        if self.pricing_text is not None:
            out["pricing"] = sha256_text(self.pricing_text)
        out["knowledge"] = sha256_text("|".join(sorted(d.checksum for d in self.knowledge)))
        return out


class BundleError(ValueError):
    pass


def load_bundle(path: str | Path) -> TenantBundle:
    root = Path(path)
    cfg_path = root / "config.yaml"
    if not cfg_path.is_file():
        raise BundleError(f"{root}: missing config.yaml")
    config_text = cfg_path.read_text()
    try:
        config = TenantConfig.model_validate(yaml.safe_load(config_text))
    except Exception as exc:
        raise BundleError(f"{root}/config.yaml: {exc}") from exc
    if config.tenant.slug != root.name:
        raise BundleError(f"{root}: folder name must equal tenant.slug ({config.tenant.slug})")
    skill = root / "skill.md"
    pricing = root / config.pricing.source
    docs: list[KnowledgeDoc] = []
    kdir = root / "knowledge"
    if kdir.is_dir():
        for f in sorted(kdir.rglob("*")):
            if f.is_file() and f.suffix.lower() in KNOWLEDGE_SUFFIXES:
                text = f.read_text(errors="replace")
                title = next((l.lstrip("# ").strip() for l in text.splitlines() if l.strip()), f.stem)
                docs.append(KnowledgeDoc(title=title[:200], source_path=str(f.relative_to(root)), text=text, checksum=sha256_text(text)))
    return TenantBundle(
        path=str(root),
        config=config,
        config_text=config_text,
        skill_md=skill.read_text() if skill.is_file() else "",
        pricing_text=pricing.read_text() if pricing.is_file() else None,
        knowledge=docs,
    )


def _tree_mtime(root: Path) -> float:
    latest = 0.0
    if not root.is_dir():
        return latest
    for dirpath, _dirs, files in os.walk(root):
        latest = max(latest, os.stat(dirpath).st_mtime)
        for f in files:
            try:
                latest = max(latest, os.stat(os.path.join(dirpath, f)).st_mtime)
            except FileNotFoundError:
                pass
    return latest


class BundleRegistry:
    """Hot-reloading view of every bundle under `root` (folders starting with '_' are templates)."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._lock = threading.Lock()
        self._mtime = -1.0
        self._by_id: dict[str, TenantBundle] = {}
        self.errors: dict[str, str] = {}
        self.reload(force=True)

    def reload(self, force: bool = False) -> None:
        with self._lock:
            m = _tree_mtime(self.root)
            if not force and m == self._mtime:
                return
            by_id, errors = {}, {}
            if self.root.is_dir():
                for d in sorted(self.root.iterdir()):
                    if not d.is_dir() or d.name.startswith(("_", ".")):
                        continue
                    try:
                        b = load_bundle(d)
                    except Exception as exc:
                        prev = next((x for x in self._by_id.values() if x.slug == d.name), None)
                        errors[d.name] = str(exc)[:500]
                        if prev:  # keep serving the last valid bundle
                            by_id[prev.tenant_id] = prev
                        continue
                    by_id[b.tenant_id] = b
            self._by_id, self.errors, self._mtime = by_id, errors, m

    @property
    def version(self) -> float:
        return self._mtime

    def _maybe(self) -> None:
        try:
            self.reload()
        except Exception:
            pass

    def all(self) -> list[TenantBundle]:
        self._maybe()
        return list(self._by_id.values())

    def get(self, tenant_id: str) -> Optional[TenantBundle]:
        self._maybe()
        return self._by_id.get(str(tenant_id))

    def config(self, tenant_id: str) -> Optional[TenantConfig]:
        b = self.get(tenant_id)
        return b.config if b else None


def default_bundles_dir(routing_path: str | Path) -> str:
    env = os.environ.get("TENANT_BUNDLES_DIR")
    if env:
        return env
    return str(Path(routing_path).resolve().parent.parent / "tenants")
