import asyncio
import json
import math

from components.gemini import AsyncHashingEmbedder, GeminiListwiseReranker, HashingEmbedder, l2_normalize
from services.indexer import build_chunker, split_chunk
from services.store import ts_query, vector_literal


def test_ts_query_drops_stopwords_and_dedupes():
    q = ts_query("What is the warranty of the warranty for ceramic coating?")
    terms = q.split(" | ")
    assert "warranty" in terms and "ceramic" in terms and "coating" in terms
    assert terms.count("warranty") == 1
    assert "the" not in terms and "is" not in terms


def test_ts_query_empty_and_injection_safe():
    assert ts_query("") == ""
    q = ts_query("oil & change | ! :* ' ) (")
    assert all(ch not in q.replace(" | ", "") for ch in "&|!:*'()")


def test_vector_literal():
    assert vector_literal([1, 0.5]) == "[1.0000000,0.5000000]"


def test_l2_normalize():
    v = l2_normalize([3, 4])
    assert math.isclose(sum(x * x for x in v), 1.0)
    assert l2_normalize([0, 0]) == [0.0, 0.0]


def test_hashing_embedder_similarity():
    e = HashingEmbedder(256)
    a, b, c = e.embed_documents(["car service oil change", "oil change car service", "pizza delivery"])
    dot = lambda x, y: sum(i * j for i, j in zip(x, y))  # noqa: E731
    assert dot(a, b) > 0.99 and dot(a, c) < 0.3
    assert asyncio.run(AsyncHashingEmbedder(256).embed_query("x")) == e.embed_query("x")


def test_split_chunk_strips_context_prefix():
    section, display, embed = split_chunk("[Context: Services > Oil]\n\nOil change costs 1500.", {})
    assert section == "Services > Oil" and display == "Oil change costs 1500."
    assert embed.startswith("[Context:")
    assert split_chunk("plain", {"section": "S"}) == ("S", "plain", "plain")


def test_chunker_markdown_without_llm():
    md = "# Workshop\n\n## Timings\n\n" + "Open 9 to 7 every day. " * 20 + "\n\n## Services\n\n" + "We do oil change. " * 20
    recs = build_chunker().chunk_text(md, "knowledge/workshop.md")
    assert recs and all(r.text.strip() for r in recs)
    assert any("Timings" in r.text for r in recs) and any("Services" in r.text for r in recs)


class _Text:
    def __init__(self, raw):
        self.raw = raw

    async def generate(self, *a, **k):
        return self.raw


def test_rerank_parses_scores_and_falls_back():
    r = GeminiListwiseReranker(_Text(json.dumps({"scores": [{"i": 1, "score": 9}, {"i": 0, "score": 2}, {"i": 7, "score": 5}]})))
    assert asyncio.run(r.score("q", ["a", "b"])) == [2.0, 9.0]
    bad = GeminiListwiseReranker(_Text("not json"))
    assert asyncio.run(bad.score("q", ["a", "b", "c"])) == [3.0, 2.0, 1.0]
