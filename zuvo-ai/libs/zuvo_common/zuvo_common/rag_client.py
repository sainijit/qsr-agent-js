"""Client for zuvo-ai-rag (internal retrieval service)."""
from __future__ import annotations

from typing import Optional

import httpx


class RagUnavailable(Exception):
    """category: network | timeout | unauthorized | unknown_tenant | embedding_unavailable | store_unavailable |
    http_<status>. Never contains query text."""

    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


class RagClient:
    def __init__(self, base_url: str, token: str, timeout_seconds: float = 2.0,
                 transport: Optional[httpx.AsyncBaseTransport] = None):
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout_seconds, transport=transport,
                                         headers={"Authorization": f"Bearer {token}"})

    async def retrieve(self, tenant_id: str, query: str, top_k: int = 3,
                       min_similarity: Optional[float] = None) -> dict:
        body: dict = {"query": query[:2000], "top_k": top_k}
        if min_similarity is not None:
            body["min_similarity"] = min_similarity
        try:
            r = await self._client.post(f"/v1/tenants/{tenant_id}/retrieve", json=body)
        except httpx.TimeoutException as exc:
            raise RagUnavailable("timeout") from exc
        except httpx.HTTPError as exc:
            raise RagUnavailable("network") from exc
        if r.status_code == 200:
            return r.json()
        category = {401: "unauthorized", 404: "unknown_tenant"}.get(r.status_code, f"http_{r.status_code}")
        if r.status_code == 503:
            try:
                category = str(r.json().get("error") or category)[:40]
            except ValueError:
                pass
        raise RagUnavailable(category)

    async def aclose(self) -> None:
        await self._client.aclose()
