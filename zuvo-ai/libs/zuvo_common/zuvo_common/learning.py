"""Learning-from-approvals gate (no fine-tuning).

Every human decision on an agent proposal is appended to the topic's rolling window. A topic is
auto-enabled once it has enough approved-unchanged answers at a high enough rate; a rejection
demotes it immediately (when configured).
"""
from __future__ import annotations

from dataclasses import dataclass

from .bundle import LearningCfg

APPROVED, EDITED, REJECTED = "A", "E", "R"


@dataclass
class AutonomyState:
    recent: list[str]
    approved_unchanged: int
    edited: int
    rejected: int
    auto_enabled: bool


def record_outcome(state: AutonomyState, outcome: str, cfg: LearningCfg) -> AutonomyState:
    recent = (state.recent + [outcome])[-cfg.window:]
    a = state.approved_unchanged + (outcome == APPROVED)
    e = state.edited + (outcome == EDITED)
    r = state.rejected + (outcome == REJECTED)
    enabled = evaluate(recent, cfg)
    if outcome == REJECTED and cfg.demote_on_reject:
        enabled = False
    return AutonomyState(recent, a, e, r, enabled)


def evaluate(recent: list[str], cfg: LearningCfg) -> bool:
    if not cfg.enabled or not recent:
        return False
    approved = recent.count(APPROVED)
    if approved < cfg.min_examples_before_auto:
        return False
    if cfg.demote_on_reject and recent[-1] == REJECTED:
        return False
    return approved / len(recent) >= cfg.auto_promote_rate


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance (bounded inputs: WhatsApp replies)."""
    a, b = a or "", b or ""
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]
