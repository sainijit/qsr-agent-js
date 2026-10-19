"""Gemini adapters: embeddings, text generation (answer / chunk splitting) and
an optional listwise reranker. Replaces the upstream OpenVINO/OVMS components."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import re
from typing import Awaitable, Callable, Sequence, TypeVar

logger = logging.getLogger(__name__)
T = TypeVar("T")


class EmbeddingError(RuntimeError):
    """Embedding response unusable (wrong count, dimension, NaN/inf, zero vector). Never retried."""


def _status(exc: Exception):
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    return code if isinstance(code, int) else None


def _transport(exc: Exception) -> bool:
    try:
        import httpx
        if isinstance(exc, httpx.TransportError):
            return True
    except ImportError:  # pragma: no cover
        pass
    return isinstance(exc, (ConnectionError, OSError))


def classify(exc: BaseException) -> str:
    """Stable, low-cardinality failure category (metrics label, index_error prefix, API error)."""
    if isinstance(exc, EmbeddingError):
        return "invalid_embedding"
    if isinstance(exc, asyncio.TimeoutError):
        return "timeout"
    code = _status(exc) if isinstance(exc, Exception) else None
    if code == 429:
        return "rate_limited"
    if code is not None and code >= 500:
        return "server_error"
    if code is not None and code >= 400:
        return "client_error"
    if isinstance(exc, Exception) and _transport(exc):
        return "network"
    return "internal"


def _retryable(exc: Exception) -> bool:
    return classify(exc) in ("timeout", "rate_limited", "server_error", "network")


async def with_retries(call: Callable[[], Awaitable[T]], attempts: int, timeout: float,
                       base_delay: float = 1.0, max_delay: float = 8.0) -> T:
    """Bounded retries for transient failures with full-jitter exponential backoff."""
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return await asyncio.wait_for(call(), timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            if attempt >= attempts or not _retryable(exc):
                raise
            logger.warning("gemini call failed (attempt %d/%d): %s", attempt, attempts, classify(exc))
            await asyncio.sleep(random.uniform(0, min(max_delay, base_delay * 2 ** (attempt - 1))))
    raise RuntimeError("unreachable")


def validate_vectors(vectors: Sequence[Sequence[float]], expected_count: int, dim: int) -> None:
    if len(vectors) != expected_count:
        raise EmbeddingError(f"expected {expected_count} embeddings, got {len(vectors)}")
    for i, v in enumerate(vectors):
        if len(v) != dim:
            raise EmbeddingError(f"embedding {i} has dimension {len(v)}, expected {dim}")
        sq = 0.0
        for x in v:
            if not math.isfinite(x):
                raise EmbeddingError(f"embedding {i} has a non-finite value")
            sq += x * x
        if sq == 0.0:
            raise EmbeddingError(f"embedding {i} is a zero vector")


def l2_normalize(v: Sequence[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class GeminiEmbedder:
    """Async embedder. Reduced dimensions (MRL) are not unit-length, so vectors
    are L2-normalised here; cosine distance in pgvector then equals 1 - dot."""

    def __init__(self, api_key: str, model: str, dimensions: int, batch_size: int = 50,
                 timeout: float = 20.0, attempts: int = 3):
        from google import genai

        self._types = genai.types
        self._client = genai.Client(api_key=api_key)
        self.model, self.dimensions = model, dimensions
        self.batch_size, self.timeout, self.attempts = batch_size, timeout, attempts

    @property
    def model_id(self) -> str:
        return f"{self.model}@{self.dimensions}"

    async def _embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        cfg = self._types.EmbedContentConfig(task_type=task_type, output_dimensionality=self.dimensions)
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i:i + self.batch_size]
            resp = await with_retries(
                lambda: self._client.aio.models.embed_content(model=self.model, contents=batch, config=cfg),
                self.attempts, self.timeout)
            raw = [list(e.values or []) for e in (resp.embeddings or [])]
            validate_vectors(raw, len(batch), self.dimensions)
            out.extend(l2_normalize(v) for v in raw)
        return out

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._embed(texts, "RETRIEVAL_DOCUMENT") if texts else []

    async def embed_query(self, text: str) -> list[float]:
        return (await self._embed([text], "RETRIEVAL_QUERY"))[0]


class GeminiText:
    def __init__(self, api_key: str, model: str, timeout: float = 20.0, attempts: int = 3):
        from google import genai

        self._types = genai.types
        self._client = genai.Client(api_key=api_key)
        self.model, self.timeout, self.attempts = model, timeout, attempts

    async def generate(self, prompt: str, *, system: str | None = None, max_tokens: int = 300,
                       temperature: float = 0.2, json_mode: bool = False) -> str:
        cfg = self._types.GenerateContentConfig(
            system_instruction=system, max_output_tokens=max_tokens, temperature=temperature,
            response_mime_type="application/json" if json_mode else None)
        resp = await with_retries(
            lambda: self._client.aio.models.generate_content(model=self.model, contents=prompt, config=cfg),
            self.attempts, self.timeout)
        return (resp.text or "").strip()

    def sync_generator(self) -> Callable[[str, int | None, float | None], str]:
        """Blocking callable with the upstream chunker's signature (used from a worker thread)."""
        types = self._types

        def gen(prompt: str, max_new_tokens: int | None = None, temperature: float | None = None) -> str:
            resp = self._client.models.generate_content(
                model=self.model, contents=prompt,
                config=types.GenerateContentConfig(max_output_tokens=max_new_tokens or 512, temperature=temperature or 0.0))
            return (resp.text or "").strip()

        return gen


class GeminiListwiseReranker:
    """One Gemini call scores every candidate 0-10 for the question."""

    def __init__(self, text: GeminiText):
        self.text = text

    async def score(self, query: str, passages: list[str]) -> list[float]:
        listing = "\n\n".join(f"[{i}] {p[:1200]}" for i, p in enumerate(passages))
        prompt = (f"QUESTION:\n{query}\n\nPASSAGES:\n{listing}\n\n"
                  "Score how well each passage answers the question, 0 (unrelated) to 10 (directly answers). "
                  'Return JSON: {"scores": [{"i": <index>, "score": <0-10>}]} covering every passage.')
        raw = await self.text.generate(prompt, max_tokens=50 + 20 * len(passages), temperature=0.0, json_mode=True)
        scores = [0.0] * len(passages)
        try:
            for item in json.loads(raw).get("scores", []):
                i = int(item["i"])
                if 0 <= i < len(passages):
                    scores[i] = float(item["score"])
        except Exception:  # noqa: BLE001 - malformed output => keep fusion order
            logger.warning("rerank parse failed: %r", raw[:200])
            return [float(len(passages) - i) for i in range(len(passages))]
        return scores


_WORD = re.compile(r"[a-z0-9\u0900-\u097f]+")


class HashingEmbedder:
    """Local bag-of-words embedder (no network). Used by the chunker's small-chunk
    merger and as a deterministic embedder in tests."""

    def __init__(self, dimensions: int = 256):
        self.dimensions = dimensions

    @property
    def model_id(self) -> str:
        return f"hashing@{self.dimensions}"

    def _vec(self, text: str) -> list[float]:
        import hashlib

        v = [0.0] * self.dimensions
        for w in _WORD.findall(text.lower()):
            h = int.from_bytes(hashlib.blake2b(w.encode(), digest_size=4).digest(), "big")
            v[h % self.dimensions] += 1.0
        return l2_normalize(v)

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]


class AsyncHashingEmbedder(HashingEmbedder):
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:  # type: ignore[override]
        return [self._vec(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:  # type: ignore[override]
        return self._vec(text)
