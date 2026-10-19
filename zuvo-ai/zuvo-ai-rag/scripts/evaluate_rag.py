"""Retrieval evaluation: zuvo-ai-rag (Gemini + pgvector hybrid) vs the lexical baseline.

Runs inside the zuvo-ai-rag container (needs DB + GEMINI_API_KEY):
    docker compose exec zuvo-ai-rag python scripts/evaluate_rag.py [--rerank] [--json out.json]

For every golden question it records the top-1 section and cosine similarity (floor disabled),
then sweeps min_similarity floors and reports:
  in-domain  top-1 accuracy / recall@3 (answer section retrieved and above the floor)
  out-of-domain rejection (nothing above the floor)
The lexical baseline uses the agent-worker's rank_texts over the same kb_chunks.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from main import build_state  # noqa: E402
from components.gemini import GeminiListwiseReranker  # noqa: E402
from sqlalchemy import text  # noqa: E402
from zuvo_common.db import tenant_session  # noqa: E402
from zuvo_common.pricing import rank_texts  # noqa: E402
from zuvo_common.tenancy import TenantRouting  # noqa: E402
from zuvo_common.settings import get_settings  # noqa: E402

FLOORS = [0.50, 0.55, 0.58, 0.60, 0.62, 0.65, 0.70]


async def lexical(sm, tid: str, q: str) -> list[tuple[str, float]]:
    async with tenant_session(sm, tid) as s:
        rows = (await s.execute(text(
            "SELECT c.section, c.text FROM kb_chunks c JOIN kb_documents d ON d.id = c.document_id WHERE d.is_active"))).all()
    return [(ref, sc) for ref, sc in rank_texts(q, [(r[0], r[1]) for r in rows], limit=3)]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", default=str(ROOT / "eval" / "golden.yaml"))
    ap.add_argument("--rerank", action="store_true", help="enable Gemini listwise rerank for this run")
    ap.add_argument("--json")
    a = ap.parse_args()
    golden = yaml.safe_load(Path(a.golden).read_text())
    st = build_state()
    if a.rerank:
        st.retriever.reranker = GeminiListwiseReranker(st.text)
    slugs = {b.slug: b.tenant_id for b in TenantRouting(get_settings().tenant_routing_path).bundles.all()}
    rows = []
    for slug, cases in golden.items():
        tid = slugs[slug]
        for c in cases:
            res = await st.retriever.retrieve(tid, c["q"], top_k=3, min_similarity=0.0)
            lex = await lexical(st.sm, tid, c["q"])
            rows.append({"tenant": slug, "q": c["q"], "expect": c["expect"],
                         "rag": [(h["section"], h["similarity"]) for h in res.chunks],
                         "lex": lex, "ms": res.latency_ms["total"]})
    ind = [r for r in rows if r["expect"]]
    ood = [r for r in rows if not r["expect"]]

    def rag_scores(floor: float):
        top1 = sum(1 for r in ind if r["rag"] and r["rag"][0][0] == r["expect"] and r["rag"][0][1] >= floor)
        r3 = sum(1 for r in ind if any(s == r["expect"] and sim >= floor for s, sim in r["rag"]))
        rej = sum(1 for r in ood if not r["rag"] or r["rag"][0][1] < floor)
        return top1, r3, rej

    print(f"\n{'tenant':18} {'question':44} {'expect':12} rag-top1 (sim)                 lexical-top1")
    for r in rows:
        rt = f"{(r['rag'][0][0] or '')[:22]} ({r['rag'][0][1]:.3f})" if r["rag"] else "-"
        lt = f"{(r['lex'][0][0] or '')[:22]} ({r['lex'][0][1]:.2f})" if r["lex"] else "-"
        mark = "✔" if (r["rag"] and r["rag"][0][0] == r["expect"]) else ("·" if not r["expect"] else "✘")
        print(f"{r['tenant']:18} {r['q'][:44]:44} {(r['expect'] or 'OOD')[:12]:12} {mark} {rt:31} {lt}")

    lex_top1 = sum(1 for r in ind if r["lex"] and r["lex"][0][0] == r["expect"])
    lex_rej = sum(1 for r in ood if not r["lex"])
    print(f"\nin-domain={len(ind)}  out-of-domain={len(ood)}  "
          f"median latency={sorted(r['ms'] for r in rows)[len(rows) // 2]} ms  rerank={'on' if a.rerank else 'off'}")
    print(f"lexical baseline : top1 {lex_top1}/{len(ind)}  OOD rejected {lex_rej}/{len(ood)}")
    best = None
    for f in FLOORS:
        t1, r3, rej = rag_scores(f)
        score = t1 + rej  # balanced: correct answers + correct refusals
        best = max(best or (score, f), (score, f))
        print(f"rag floor {f:.2f}   : top1 {t1}/{len(ind)}  recall@3 {r3}/{len(ind)}  OOD rejected {rej}/{len(ood)}")
    print(f"\nsuggested min_similarity: {best[1]:.2f}")
    if a.json:
        Path(a.json).write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
