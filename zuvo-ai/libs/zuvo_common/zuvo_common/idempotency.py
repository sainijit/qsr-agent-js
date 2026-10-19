"""Idempotency / dedupe store. Redis in every deployed service; in-memory only for tests."""
from __future__ import annotations

import time
from abc import ABC, abstractmethod

import redis.asyncio as aioredis


class IdempotencyStore(ABC):
    @abstractmethod
    async def claim(self, key: str, ttl_seconds: int) -> bool:
        """Atomically claims `key`. Returns False if already claimed."""

    @abstractmethod
    async def release(self, key: str) -> None: ...

    @abstractmethod
    async def seen(self, key: str) -> bool: ...

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        return None


class RedisIdempotencyStore(IdempotencyStore):
    def __init__(self, url: str):
        self._redis = aioredis.from_url(url, decode_responses=True)

    async def claim(self, key: str, ttl_seconds: int) -> bool:
        return bool(await self._redis.set(key, "1", nx=True, ex=ttl_seconds))

    async def release(self, key: str) -> None:
        await self._redis.delete(key)

    async def seen(self, key: str) -> bool:
        return bool(await self._redis.exists(key))

    async def ping(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except Exception:
            return False

    async def close(self) -> None:
        await self._redis.aclose()


class InMemoryIdempotencyStore(IdempotencyStore):
    def __init__(self) -> None:
        self._keys: dict[str, float] = {}

    async def claim(self, key: str, ttl_seconds: int) -> bool:
        now = time.monotonic()
        exp = self._keys.get(key)
        if exp and exp > now:
            return False
        self._keys[key] = now + ttl_seconds
        return True

    async def release(self, key: str) -> None:
        self._keys.pop(key, None)

    async def seen(self, key: str) -> bool:
        exp = self._keys.get(key)
        return bool(exp and exp > time.monotonic())
