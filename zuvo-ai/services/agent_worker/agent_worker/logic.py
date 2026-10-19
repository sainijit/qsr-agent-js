"""Pure agent decision logic (no I/O): task selection, grounded proposals, and the auto/draft gate."""
from __future__ import annotations

import re
import string
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional, Sequence

from zuvo_common.bundle import TenantConfig
from zuvo_common.events import Extraction, Intent
from zuvo_common.pricing import match_prices, price_text, rank_texts, similarity, staleness_factor

MIN_PRICE_SCORE = 0.5
CLOSE_MARGIN = 0.08
EXAMPLE_REUSE_MIN = 0.85


@dataclass
class Proposal:
    task: str
    topic: str
    text: str
    confidence: float
    reasons: list[str] = field(default_factory=list)
    retrieved: dict[str, Any] = field(default_factory=dict)
    facts: list[str] = field(default_factory=list)  # grounded lines the LLM drafter may rephrase
    allowed_texts: list[str] = field(default_factory=list)


class _Blank(dict):
    def __missing__(self, key: str) -> str:
        return ""


def render(text: str, ctx: dict[str, Any]) -> str:
    """str.format with unknown placeholders rendered empty (config text is owner-authored)."""
    try:
        out = string.Formatter().vformat(text or "", (), _Blank({k: ("" if v is None else v) for k, v in ctx.items()}))
    except (ValueError, IndexError):
        out = text or ""
    return re.sub(r"[ \t]{2,}", " ", out).replace(" ,", ",").strip()


def choose_task(x: Optional[Extraction], tier: str) -> Optional[str]:
    if x is None:
        return None
    if x.intent == Intent.INQUIRY_PRICE_STOCK or (x.price_query and x.price_query.is_direct):
        return "price"
    if x.intent == Intent.FAQ:
        return "faq"
    if x.intent == Intent.VISIT_PLAN:
        return None  # acknowledged on visit.planned once the lead-service has resolved the date
    if x.intent == Intent.GENERAL_CHAT:
        return "greeting" if tier == "tier0" else "general"
    return None  # orders, payments and complaints stay with humans


def mode_for(cfg: TenantConfig, task: str) -> str:
    mode = cfg.agent.replies.get(task, "off")
    if task == "price" and mode == "auto" and cfg.pricing.answer_mode == "draft":
        mode = "draft"
    return mode


def threshold_for(cfg: TenantConfig, task: str) -> float:
    if task == "price":
        return cfg.pricing.confidence_auto_min
    if task == "faq":
        return cfg.agent.faq_confidence_auto_min
    return cfg.ai.confidence_threshold


def best_example(question: str, topic_prefix: str, examples: Sequence[dict]) -> Optional[tuple[dict, float]]:
    best = None
    for ex in examples:
        if not ex.get("answer_text") or not str(ex.get("topic", "")).startswith(topic_prefix):
            continue
        s = similarity(question, ex.get("question_text", ""))
        s = min(1.0, s * (1.05 if ex.get("weight", 1) > 1 else 1.0))
        if best is None or s > best[1]:
            best = (ex, s)
    return best


def _valid_until_text(items: Sequence[dict]) -> str:
    dates = sorted(i["valid_until"] for i in items if i.get("valid_until"))
    if not dates:
        return ""
    return f" (valid till {date.fromisoformat(str(dates[0])).strftime('%d %b %Y')})"


def price_proposal(cfg: TenantConfig, items: Sequence[dict], x: Extraction, message_text: str, today: date,
                   examples: Sequence[dict] = ()) -> Proposal:
    pq = x.price_query
    query = (pq.item_text if pq and pq.item_text else message_text) or ""
    extra = list(pq.attributes) if pq else []
    matches = match_prices(query, items, extra)
    good = [m for m in matches if m.score >= MIN_PRICE_SCORE]
    reasons: list[str] = []
    rules = set(cfg.pricing.always_approval_if)
    if ((pq and pq.discount_requested) or (x.requested_discount_pct or 0) > 0) and "discount_request" in rules:
        reasons.append("discount_request")
    cur = cfg.tenant.currency
    if not good:
        reasons.append("price_missing")  # nothing grounded to send: always a human decision
        return Proposal("price", "price:unknown", cfg.agent.holding_reply, 0.0, reasons,
                        {"query": query, "matches": [m.item.get("sku") for m in matches[:3]]})
    top = good[0]
    close = [m for m in good if top.score - m.score < CLOSE_MARGIN]
    categories = {(m.item.get("category") or "").lower() for m in close}
    if len(close) > cfg.pricing.max_variants_in_reply or len(categories) > 1:
        if "multiple_matches" in rules:
            reasons.append("multiple_matches")
        close = close[: cfg.pricing.max_variants_in_reply]
    shown = close if len(close) > 1 else [top]
    missing = [m for m in shown if price_text(m.item, cur) is None]
    if missing:
        reasons.append("price_missing")
        shown = [m for m in shown if m not in missing]
    stale = min((staleness_factor(m.item.get("valid_until"), today) for m in shown), default=0.0)
    if stale == 0.0:
        reasons.append("stale_price")
    lines = [f"{m.item['name']}: {price_text(m.item, cur)}" + (f" per {m.item['unit']}" if m.item.get("unit") not in (None, "", "unit", "piece") else "")
             for m in shown]
    disclaimer = render(cfg.pricing.disclaimer, {"valid_until_text": _valid_until_text([m.item for m in shown])})
    if not lines:
        text = cfg.agent.holding_reply
    else:
        head = "Here is the price:" if len(lines) == 1 else "Here are the prices:"
        text = "\n".join([head, *[f"• {l}" for l in lines]] + ([disclaimer] if disclaimer else []) +
                         ["Would you like to visit us to see it in person?"])
    category = (top.item.get("category") or "general").lower().replace(" ", "_")
    conf = round(x.confidence * top.score * stale * (1.0 if lines else 0.0), 3)
    p = Proposal("price", f"price:{category}", text, conf, reasons,
                 {"query": query, "skus": [m.item["sku"] for m in shown], "scores": [m.score for m in shown]},
                 facts=lines + ([disclaimer] if disclaimer else []), allowed_texts=[disclaimer])
    ex = best_example(message_text, "price:", examples)
    if ex and ex[1] >= EXAMPLE_REUSE_MIN and set(ex[0].get("price_item_skus") or []) == set(p.retrieved["skus"]):
        # an owner-approved answer to the same question about the same items: reuse its wording
        p.text = ex[0]["answer_text"]
        p.retrieved["example_id"] = ex[0].get("id")
    return p


RAG_SIM_LOW, RAG_SIM_HIGH = 0.55, 0.75  # cosine similarity -> confidence ramp for zuvo-ai-rag hits


def _best_sentences(question: str, chunk_text: str, n: int = 2) -> str:
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", chunk_text) if s.strip() and not s.strip().startswith("#")]
    best = sorted(sentences, key=lambda s: similarity(question, s), reverse=True)[:n]
    best.sort(key=sentences.index)
    return " ".join(best)


def _faq_topic(title: Optional[str]) -> str:
    return "faq:" + re.sub(r"[^a-z0-9]+", "_", (title or "general").lower()).strip("_")[:100]


def _rag_faq(cfg: TenantConfig, rag: dict, x: Extraction, question: str) -> Proposal:
    meta = {"question": question, "strategy": rag.get("strategy"), "embed_model": rag.get("embed_model"),
            "top_similarity": rag.get("top_similarity"), "latency_ms": (rag.get("latency_ms") or {}).get("total"),
            "chunk_ids": [c.get("chunk_id") for c in rag.get("chunks") or []],
            "scores": [c.get("similarity") for c in rag.get("chunks") or []]}
    chunks = rag.get("chunks") or []
    if rag.get("below_threshold") or not chunks:
        return Proposal("faq", "faq:unknown", cfg.agent.holding_reply, 0.0, ["no_knowledge"], meta)
    top = chunks[0]
    sim = float(top.get("similarity") or 0.0)
    ramp = max(0.0, min(1.0, (sim - RAG_SIM_LOW) / (RAG_SIM_HIGH - RAG_SIM_LOW)))
    texts = [c["text"] for c in chunks if c.get("text")]
    text = _best_sentences(question, top["text"]) or cfg.agent.holding_reply
    return Proposal("faq", _faq_topic(top.get("title")), text, round(x.confidence * ramp, 3), [], meta,
                    facts=texts, allowed_texts=texts)


def faq_proposal(cfg: TenantConfig, chunks: Sequence[tuple[dict, str]], x: Extraction, message_text: str,
                 examples: Sequence[dict] = (), rag: Optional[dict] = None) -> Proposal:
    """rag: a zuvo-ai-rag /retrieve response; when given it replaces lexical ranking over ``chunks``."""
    question = x.question or message_text
    ex = best_example(question, "faq:", examples)
    if ex and ex[1] >= EXAMPLE_REUSE_MIN:
        return Proposal("faq", ex[0]["topic"], ex[0]["answer_text"], round(x.confidence * ex[1], 3), [],
                        {"example_id": ex[0].get("id"), "similarity": ex[1]}, facts=[ex[0]["answer_text"]],
                        allowed_texts=[ex[0]["answer_text"]])
    if rag is not None:
        return _rag_faq(cfg, rag, x, question)
    ranked = rank_texts(question, chunks, limit=2)
    if not ranked:
        return Proposal("faq", "faq:unknown", cfg.agent.holding_reply, 0.0, ["no_knowledge"], {"question": question})
    ref, score = ranked[0]
    chunk_text = next(t for r, t in chunks if r is ref)
    text = _best_sentences(question, chunk_text) or cfg.agent.holding_reply
    topic = _faq_topic(ref.get("title"))
    conf = round(x.confidence * min(1.0, score * 1.6), 3)
    return Proposal("faq", topic, text, conf, [], {"question": question, "strategy": "lexical",
                                                     "chunk_ids": [r.get("id") for r, _ in ranked],
                                                     "scores": [s for _, s in ranked]},
                    facts=[chunk_text], allowed_texts=[chunk_text])


def decide(cfg: TenantConfig, p: Proposal, mode: str, *, flagged: bool, topic_auto_enabled: bool) -> tuple[str, list[str]]:
    """Returns (decision, reasons): AUTO_SENT | PENDING_APPROVAL | SKIPPED."""
    if mode == "off":
        return "SKIPPED", ["mode_off"]
    reasons = list(p.reasons)
    if flagged:
        reasons.append("low_confidence")
    if p.confidence < threshold_for(cfg, p.task):
        reasons.append(f"confidence<{threshold_for(cfg, p.task)}")
    if mode == "draft":
        reasons.append("mode_draft")
    if cfg.learning.enabled and not topic_auto_enabled:
        reasons.append("learning_gate")
    return ("PENDING_APPROVAL" if reasons else "AUTO_SENT"), reasons


# ------------------------------------------------------------ run outcome / diagnostics

def run_outcome(decision: str, reasons: Sequence[str], *, rag_error: Optional[str] = None,
                draft_error: Optional[str] = None) -> str:
    """Primary reason a run did or did not produce a confident grounded reply (most actionable first)."""
    if decision == "SKIPPED":
        return "skipped"
    if rag_error:
        return "rag_unavailable"
    if "no_knowledge" in reasons:
        return "no_knowledge"
    if "ai_budget_exhausted" in reasons:
        return "ai_budget_exhausted"
    if "draft_guard" in reasons:
        return "draft_rejected"
    if draft_error:
        return "draft_failed"
    if "low_confidence" in reasons or any(r.startswith("confidence<") for r in reasons):
        return "low_confidence"
    return "ok"


def run_diagnostics(p: Proposal, *, extraction: Optional[dict] = None, rag: Optional[dict] = None,
                    rag_error: Optional[str] = None, usage: Optional[dict] = None) -> dict:
    """Ids, versions, scores, models and latencies only: no prompts, customer text, chunk text or model reasoning."""
    out: dict[str, Any] = {}
    if extraction:
        out["extraction"] = {k: v for k, v in extraction.items() if v is not None}
    if p.task == "faq":
        r: dict[str, Any] = {"strategy": p.retrieved.get("strategy") or ("example" if p.retrieved.get("example_id") else None)}
        if rag is not None:
            lat = rag.get("latency_ms") or {}
            r.update(embed_model=rag.get("embed_model"), top_similarity=rag.get("top_similarity"),
                     below_threshold=bool(rag.get("below_threshold")), latency_ms=lat.get("total"),
                     query_cache_hit=lat.get("query_cache_hit"),
                     documents=[{"document_id": c.get("document_id"), "document_version": c.get("document_version"),
                                 "chunk_id": c.get("chunk_id"), "similarity": c.get("similarity")}
                                for c in rag.get("chunks") or []])
        if rag_error:
            r["rag_error"] = rag_error
        if p.retrieved.get("example_id"):
            r["example_id"] = p.retrieved["example_id"]
        out["retrieval"] = {k: v for k, v in r.items() if v is not None}
    if usage and (usage.get("model") or usage.get("error")):
        out["draft"] = {k: usage.get(k) for k in ("model", "latency_ms", "tokens_in", "tokens_out", "error")
                        if usage.get(k) is not None}
    return out
