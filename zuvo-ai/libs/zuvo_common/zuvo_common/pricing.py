"""Pricing CSV contract, price matching and lexical retrieval (KB chunks, learned examples).

Catalogues and KBs per tenant are small (hundreds of rows), so matching runs in Python over the
tenant's active rows. Retrieval is deterministic and explainable; embeddings can be added later
behind the same functions.
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from typing import Any, Iterable, Optional, Sequence

PRICING_COLUMNS = ["sku", "name", "category", "attrs", "price", "price_min", "price_max", "unit",
                   "tax_included", "valid_until", "aliases", "notes"]
REQUIRED_COLUMNS = {"sku", "name"}

STOPWORDS = {
    "price", "prices", "pricing", "rate", "rates", "cost", "kitna", "kitne", "kitni", "kya", "hai", "h", "he",
    "ka", "ki", "ke", "ko", "se", "me", "mein", "the", "a", "an", "of", "for", "is", "are", "what", "whats",
    "how", "much", "please", "pls", "plz", "bhai", "sir", "ji", "batao", "bataiye", "bata", "do", "dijiye",
    "and", "or", "with", "tell", "me", "i", "want", "need", "chahiye", "your", "you", "on", "road", "best",
    "final", "quote", "aap", "apka", "aapka", "hum", "mujhe", "kaun", "sa", "wala", "wale", "wali", "in",
    "at", "to", "it", "this", "that", "my", "can", "get", "about", "details", "detail", "info", "available",
    "rs", "inr", "₹", "abhi", "now", "pcs", "nos", "item", "items", "product", "products", "set", "sets",
}


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in STOPWORDS]


def _fuzzy_in(tok: str, pool: set[str]) -> float:
    if tok in pool:
        return 1.0
    if len(tok) < 4:
        return 0.0
    best = max((SequenceMatcher(None, tok, p).ratio() for p in pool if abs(len(p) - len(tok)) <= 2), default=0.0)
    return best if best >= 0.84 else 0.0


# ------------------------------------------------------------------ pricing CSV


@dataclass
class PriceRow:
    sku: str
    name: str
    category: str = ""
    attrs: dict[str, Any] = field(default_factory=dict)
    price: Optional[float] = None
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    unit: str = "unit"
    tax_included: bool = False
    valid_until: Optional[date] = None
    aliases: list[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["valid_until"] = self.valid_until.isoformat() if self.valid_until else None
        return d


def _num(v: str, col: str, line: int, errors: list[str]) -> Optional[float]:
    v = (v or "").strip().replace(",", "")
    if not v:
        return None
    try:
        x = float(v)
    except ValueError:
        errors.append(f"line {line}: {col} is not a number ({v!r})")
        return None
    if x < 0:
        errors.append(f"line {line}: {col} is negative")
    return x


def parse_pricing_csv(text: str) -> tuple[list[PriceRow], list[str]]:
    errors: list[str] = []
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    cols = {c.strip() for c in (reader.fieldnames or [])}
    missing = REQUIRED_COLUMNS - cols
    if missing:
        return [], [f"missing columns: {', '.join(sorted(missing))}"]
    unknown = cols - set(PRICING_COLUMNS)
    if unknown:
        errors.append(f"unknown columns ignored: {', '.join(sorted(unknown))}")
    rows: list[PriceRow] = []
    seen: set[str] = set()
    for i, raw in enumerate(reader, start=2):
        r = {k.strip(): (v or "").strip() for k, v in raw.items() if k}
        if not any(r.values()):
            continue
        sku = r.get("sku", "")
        if not sku or not r.get("name"):
            errors.append(f"line {i}: sku and name are required")
            continue
        if sku in seen:
            errors.append(f"line {i}: duplicate sku {sku}")
            continue
        seen.add(sku)
        attrs: dict[str, Any] = {}
        if r.get("attrs"):
            try:
                attrs = json.loads(r["attrs"])
                if not isinstance(attrs, dict):
                    raise ValueError
            except ValueError:
                errors.append(f"line {i}: attrs must be a JSON object")
        price = _num(r.get("price", ""), "price", i, errors)
        pmin = _num(r.get("price_min", ""), "price_min", i, errors)
        pmax = _num(r.get("price_max", ""), "price_max", i, errors)
        if pmin is not None and pmax is not None and pmin > pmax:
            errors.append(f"line {i}: price_min > price_max")
        valid_until = None
        if r.get("valid_until"):
            try:
                valid_until = date.fromisoformat(r["valid_until"])
            except ValueError:
                errors.append(f"line {i}: valid_until must be YYYY-MM-DD")
        rows.append(PriceRow(
            sku=sku, name=r["name"], category=r.get("category", ""), attrs=attrs, price=price,
            price_min=pmin, price_max=pmax, unit=r.get("unit") or "unit",
            tax_included=r.get("tax_included", "").lower() in ("1", "true", "yes", "y"),
            valid_until=valid_until,
            aliases=[a.strip() for a in r.get("aliases", "").split("|") if a.strip()],
            notes=r.get("notes", ""),
        ))
    return rows, errors


def blocking_errors(errors: list[str]) -> list[str]:
    return [e for e in errors if not e.startswith("unknown columns")]


def diff_price_rows(old: Sequence[dict], new: Sequence[PriceRow]) -> dict[str, list]:
    old_by = {o["sku"]: o for o in old}
    new_by = {n.sku: n for n in new}
    changed = []
    for sku, n in new_by.items():
        o = old_by.get(sku)
        if o and (o.get("price"), o.get("price_min"), o.get("price_max"), o.get("name")) != (n.price, n.price_min, n.price_max, n.name):
            changed.append({"sku": sku, "old": {k: o.get(k) for k in ("name", "price", "price_min", "price_max")},
                            "new": {"name": n.name, "price": n.price, "price_min": n.price_min, "price_max": n.price_max}})
    return {
        "added": sorted(set(new_by) - set(old_by)),
        "removed": sorted(set(old_by) - set(new_by)),
        "changed": changed,
    }


# ------------------------------------------------------------------ matching


@dataclass
class PriceMatch:
    item: dict[str, Any]
    score: float


def _item_pool(item: dict) -> tuple[set[str], set[str], list[str]]:
    name_toks = set(tokens(item.get("name", "")))
    attrs = item.get("attrs") or {}
    pool = set(name_toks) | set(tokens(item.get("sku", ""))) | set(tokens(item.get("category", "")))
    for v in attrs.values():
        pool |= set(tokens(str(v)))
    aliases = [a.lower() for a in (item.get("aliases") or [])]
    for a in aliases:
        pool |= set(tokens(a))
    return pool, name_toks, aliases


def match_prices(query: str, items: Iterable[dict], extra_terms: Sequence[str] = (), limit: int = 8) -> list[PriceMatch]:
    q = tokens(" ".join([query or "", *extra_terms]))
    if not q:
        return []
    qtext = " ".join(q)
    items = list(items)
    pools = [_item_pool(item) for item in items]
    # words the catalog has never heard of ("set", "latest", "showroom") must not dilute the match
    vocab = set().union(*(p[0] for p in pools)) if pools else set()
    known = [t for t in q if _fuzzy_in(t, vocab) > 0] or q
    out: list[PriceMatch] = []
    for item, (pool, name_toks, aliases) in zip(items, pools):
        hits = [_fuzzy_in(t, pool) for t in known]
        coverage = sum(hits) / len(known)
        if coverage == 0:
            continue
        name_cov = (sum(_fuzzy_in(t, set(q)) for t in name_toks) / len(name_toks)) if name_toks else 0
        score = 0.65 * coverage + 0.35 * name_cov
        for a in aliases:
            a_t = " ".join(tokens(a))
            if a_t and re.search(rf"\b{re.escape(a_t)}\b", qtext):
                score = max(score, 0.9 + 0.1 * name_cov)
        if item.get("sku", "").lower() in (query or "").lower().split():
            score = 1.0
        out.append(PriceMatch(item=item, score=round(min(score, 1.0), 3)))
    out.sort(key=lambda m: m.score, reverse=True)
    return out[:limit]


def is_ambiguous(matches: Sequence[PriceMatch], min_score: float = 0.5, margin: float = 0.08) -> bool:
    good = [m for m in matches if m.score >= min_score]
    return len(good) >= 2 and (good[0].score - good[1].score) < margin


def staleness_factor(valid_until: Optional[str | date], today: date) -> float:
    if not valid_until:
        return 1.0
    vu = date.fromisoformat(valid_until) if isinstance(valid_until, str) else valid_until
    if vu < today:
        return 0.0
    if (vu - today).days <= 7:
        return 0.85
    return 1.0


def format_money(v: float, currency: str = "INR") -> str:
    if currency != "INR":
        return f"{currency} {v:,.0f}"
    s = f"{int(round(v))}"
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups) + "," + tail
    return f"₹{s}"


def price_text(item: dict, currency: str = "INR") -> Optional[str]:
    if item.get("price") is not None:
        return format_money(float(item["price"]), currency)
    lo, hi = item.get("price_min"), item.get("price_max")
    if lo is not None and hi is not None:
        return f"{format_money(float(lo), currency)} – {format_money(float(hi), currency)}"
    if lo is not None:
        return f"from {format_money(float(lo), currency)}"
    return None


def allowed_numbers(items: Iterable[dict], extra_texts: Iterable[str] = ()) -> set[str]:
    """Every numeric token the agent may legitimately mention."""
    out: set[str] = set()
    for it in items:
        for k in ("price", "price_min", "price_max"):
            if it.get(k) is not None:
                v = float(it[k])
                out |= {str(int(v)), f"{v:.2f}", format_money(v).lstrip("₹").replace(",", "")}
                if v >= 100000:  # "14.5 lakh" style
                    lakh = v / 100000
                    out |= {f"{lakh:g}", f"{lakh:.1f}", f"{lakh:.2f}"}
        for k in ("valid_until",):
            if it.get(k):
                out |= set(re.findall(r"\d+", str(it[k])))
        out |= set(re.findall(r"\d+(?:\.\d+)?", " ".join([it.get("name", ""), it.get("sku", ""), it.get("notes", ""), json.dumps(it.get("attrs") or {})])))
    for t in extra_texts:
        out |= set(re.findall(r"\d+(?:\.\d+)?", (t or "").replace(",", "")))
    return out


def unsupported_numbers(text: str, allowed: set[str]) -> list[str]:
    """Numbers in a draft that are not backed by retrieved facts (hallucination guard)."""
    bad = []
    for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text or ""):
        clean = n.replace(",", "").rstrip(".")
        if not clean or len(clean.replace(".", "")) <= 1:
            continue  # single digits (e.g. "3 seater", "1 day") are low-risk
        if clean in allowed or clean.split(".")[0] in allowed:
            continue
        bad.append(n)
    return bad


# ------------------------------------------------------------------ text retrieval


def chunk_text(text: str, max_chars: int = 700) -> list[str]:
    """Paragraph-aware chunking; headings stay with their paragraph."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        if len(cur) + len(p) + 2 <= max_chars or not cur:
            cur = f"{cur}\n\n{p}".strip()
        else:
            chunks.append(cur)
            cur = p
        while len(cur) > max_chars * 1.5:
            chunks.append(cur[:max_chars])
            cur = cur[max_chars:]
    if cur:
        chunks.append(cur)
    return chunks


def similarity(a: str, b: str) -> float:
    ta, tb = set(tokens(a)), set(tokens(b))
    if not ta or not tb:
        return 0.0
    hits = sum(_fuzzy_in(t, tb) for t in ta)
    return round(hits / len(ta | tb) * 0.5 + hits / len(ta) * 0.5, 3)


def rank_texts(query: str, docs: Iterable[tuple[Any, str]], limit: int = 3, min_score: float = 0.15) -> list[tuple[Any, float]]:
    scored = [(ref, similarity(query, text)) for ref, text in docs]
    scored = [s for s in scored if s[1] >= min_score]
    scored.sort(key=lambda s: s[1], reverse=True)
    return scored[:limit]
