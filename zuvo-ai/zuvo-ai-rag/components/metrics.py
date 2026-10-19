"""Minimal in-process Prometheus metrics (text exposition 0.0.4) for zuvo-ai-rag.

Labels are low-cardinality enums only (outcome, category, operation) - never tenant ids,
document ids or query text."""
from __future__ import annotations

import threading
from collections import defaultdict

_LAT_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)


class Registry:
    def __init__(self):
        self._lock = threading.Lock()
        self._counters: dict[str, dict[tuple, float]] = defaultdict(lambda: defaultdict(float))
        self._hists: dict[str, dict[tuple, list]] = defaultdict(dict)
        self._help: dict[str, tuple[str, str, tuple[str, ...]]] = {}

    def describe(self, name: str, kind: str, help_: str, labels: tuple[str, ...] = ()) -> None:
        self._help[name] = (kind, help_, labels)

    def inc(self, name: str, value: float = 1.0, **labels: str) -> None:
        key = tuple(labels.get(k, "") for k in self._help[name][2])
        with self._lock:
            self._counters[name][key] += value

    def observe(self, name: str, seconds: float, **labels: str) -> None:
        key = tuple(labels.get(k, "") for k in self._help[name][2])
        with self._lock:
            h = self._hists[name].setdefault(key, [[0] * len(_LAT_BUCKETS), 0.0, 0])
            for i, b in enumerate(_LAT_BUCKETS):
                if seconds <= b:
                    h[0][i] += 1
            h[1] += seconds
            h[2] += 1

    def value(self, name: str, **labels: str) -> float:
        key = tuple(labels.get(k, "") for k in self._help[name][2])
        return self._counters[name].get(key, 0.0)

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._hists.clear()

    def render(self) -> str:
        out: list[str] = []
        with self._lock:
            for name, (kind, help_, labels) in sorted(self._help.items()):
                out += [f"# HELP {name} {help_}", f"# TYPE {name} {kind}"]
                if kind == "counter":
                    for key, v in sorted(self._counters.get(name, {}).items()):
                        out.append(f"{name}{_fmt(labels, key)} {v:g}")
                else:
                    for key, (buckets, total, count) in sorted(self._hists.get(name, {}).items()):
                        for b, c in zip(_LAT_BUCKETS, buckets):
                            out.append(f"{name}_bucket{_fmt(labels + ('le',), key + (f'{b:g}',))} {c}")
                        out.append(f"{name}_bucket{_fmt(labels + ('le',), key + ('+Inf',))} {count}")
                        out.append(f"{name}_sum{_fmt(labels, key)} {total:.6f}")
                        out.append(f"{name}_count{_fmt(labels, key)} {count}")
        return "\n".join(out) + "\n"


def _fmt(labels: tuple[str, ...], key: tuple) -> str:
    if not labels:
        return ""
    return "{" + ",".join(f'{k}="{v}"' for k, v in zip(labels, key)) + "}"


REGISTRY = Registry()
REGISTRY.describe("zuvo_rag_retrieve_total", "counter", "Retrieval requests by outcome (hit|below_threshold|empty|error)",
                  ("outcome",))
REGISTRY.describe("zuvo_rag_retrieve_errors_total", "counter", "Retrieval failures by category", ("category",))
REGISTRY.describe("zuvo_rag_retrieve_seconds", "histogram", "Retrieval latency (embed + search + rerank)")
REGISTRY.describe("zuvo_rag_embed_failures_total", "counter", "Embedding API failures by operation and category",
                  ("operation", "category"))
REGISTRY.describe("zuvo_rag_index_total", "counter", "Document indexing attempts by outcome (indexed|failed|superseded)",
                  ("outcome",))
REGISTRY.describe("zuvo_rag_index_errors_total", "counter", "Document indexing failures by category", ("category",))
REGISTRY.describe("zuvo_rag_index_seconds", "histogram", "Per-document indexing latency (chunk + embed + swap)")
REGISTRY.describe("zuvo_rag_index_chunks_total", "counter", "Chunks written by the indexer")
