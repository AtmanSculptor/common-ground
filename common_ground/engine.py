"""Common Ground engine: what do N groups both love, and what divides them.

Method (found by probing the hackathon API, Oct 2 2026):
  1. Top-N lists per group do not overlap; each group's list is its own long tail.
     So we build a candidate pool instead: the combined-audience call plus each
     group's own list.
  2. Every candidate is scored against every group one at a time with
     filter.results.entities. That is the verification primitive.
  3. Raw affinities are compressed near 1.0 and some groups skew high, so each
     group's scores are graded on a curve (percentile rank within that group's
     scores over the pool). Common ground = the weakest group's curved score.
     Divides = spread between the strongest and weakest group.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

from .qloo import Qloo, affinity, slim

NO_SIGNAL = 0.765  # value Qloo returns when it has no audience signal for an entity


@dataclass
class Group:
    """A group is either Qloo audience ids, or a set of entity ids (someone's favorites)."""
    label: str
    audiences: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)

    def signal(self) -> dict[str, list[str]]:
        if self.audiences:
            return {"audiences": self.audiences}
        if self.entities:
            return {"entities": self.entities}
        raise ValueError(f"group {self.label} has no audiences or entities")


@dataclass
class Scored:
    entity: dict
    raw: dict[str, float]       # group label -> affinity
    curved: dict[str, float]    # group label -> percentile rank within group (0..1)
    common: float               # min curved score across groups
    divide: float               # max curved minus min curved
    leans: str                  # label of the group with the highest curved score

    def to_dict(self) -> dict:
        return asdict(self)


def _minmax(values: dict[str, float]) -> dict[str, float]:
    """Scale each group's affinities to 0..1 within the candidate pool.
    Qloo affinities sit near 1.0 and some groups skew high, so the raw numbers
    are not comparable across groups; the spread inside a group is."""
    lo, hi = min(values.values()), max(values.values())
    if hi - lo < 1e-9:
        return {k: 1.0 for k in values}
    return {k: (v - lo) / (hi - lo) for k, v in values.items()}


def score_groups(q: Qloo, groups: list[Group], entity_type: str, *, location: str | None = None,
                 pool_per_group: int = 25, pool_combined: int = 50, popularity_min: float | None = 0.9,
                 extra_candidates: list[str] | None = None) -> list[Scored]:
    """Score a candidate pool of one entity type against every group."""
    if len(groups) < 2:
        raise ValueError("need at least two groups")

    # 1. candidate pool
    pool: dict[str, dict] = {}

    def add(ents: list[dict]) -> None:
        for e in ents:
            if e.get("entity_id"):
                pool.setdefault(e["entity_id"], e)

    for g in groups:
        add(q.insights(entity_type, take=pool_per_group, location_query=location,
                       popularity_min=popularity_min, **g.signal()))
    all_aud = [a for g in groups for a in g.audiences]
    if all_aud and len(all_aud) == sum(1 for g in groups if g.audiences):
        add(q.insights(entity_type, audiences=all_aud, take=pool_combined, location_query=location,
                       popularity_min=popularity_min))
    if extra_candidates:
        add(q.insights(entity_type, result_entities=extra_candidates, take=len(extra_candidates)))
    if not pool:
        return []

    # 2. score every candidate against every group (batches of 50 ids)
    ids = list(pool)
    raw: dict[str, dict[str, float]] = {i: {} for i in ids}
    for g in groups:
        for start in range(0, len(ids), 50):
            batch = ids[start:start + 50]
            ents = q.insights(entity_type, result_entities=batch, take=len(batch),
                              location_query=location, **g.signal())
            for e in ents:
                raw[e["entity_id"]][g.label] = affinity(e)

    # keep candidates scored by every group; 0.765 is Qloo's no-signal filler, not a score
    labels = [g.label for g in groups]
    kept = [i for i in ids if all(l in raw[i] and abs(raw[i][l] - NO_SIGNAL) > 1e-6 for l in labels)]

    # 3. curve per group
    curved: dict[str, dict[str, float]] = {i: {} for i in kept}
    for l in labels:
        scaled = _minmax({i: raw[i][l] for i in kept})
        for i, r in scaled.items():
            curved[i][l] = round(r, 3)

    out: list[Scored] = []
    for i in kept:
        c = curved[i]
        lo, hi = min(c.values()), max(c.values())
        out.append(Scored(entity=slim(pool[i]), raw={l: round(v, 4) for l, v in raw[i].items()},
                          curved=c, common=round(lo, 3), divide=round(hi - lo, 3),
                          leans=max(c, key=c.get)))
    out.sort(key=lambda s: (-s.common, s.divide))
    return out


def summarize(scored: list[Scored], top: int = 10) -> dict[str, Any]:
    """Split a scored pool into what unites and what divides."""
    unites = [s for s in scored if s.divide <= 0.35][:top]
    # divides: big spread, but only things with real reach (popularity) so the list is legible
    divides = sorted((s for s in scored if s.entity.get("popularity", 0) >= 0.95),
                     key=lambda s: -s.divide)[:top]
    return {"unites": [s.to_dict() for s in unites], "divides": [s.to_dict() for s in divides],
            "pool_size": len(scored)}
