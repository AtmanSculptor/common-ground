"""Thin client for the Qloo Insights API (hackathon environment).

Every call here is a documented Qloo endpoint. Invalid parameters are silently
ignored by Qloo, so keep parameter names exactly as in the docs.
"""
from __future__ import annotations

import os
import time
from typing import Any

import requests

BASE = os.environ.get("QLOO_BASE_URL", "https://hackathon.api.qloo.com")

ENTITY_TYPES = [
    "urn:entity:artist",
    "urn:entity:book",
    "urn:entity:brand",
    "urn:entity:destination",
    "urn:entity:movie",
    "urn:entity:person",
    "urn:entity:place",
    "urn:entity:podcast",
    "urn:entity:tv_show",
    "urn:entity:video_game",
]

AUDIENCE_PARENTS = [
    "urn:audience:communities",
    "urn:audience:global_issues",
    "urn:audience:hobbies_and_interests",
    "urn:audience:investing_interests",
    "urn:audience:leisure",
    "urn:audience:life_stage",
    "urn:audience:lifestyle_preferences_beliefs",
    "urn:audience:political_preferences",
    "urn:audience:professional_area",
    "urn:audience:spending_habits",
]


class QlooError(RuntimeError):
    pass


class Qloo:
    def __init__(self, api_key: str | None = None, base: str = BASE, timeout: int = 60):
        self.key = api_key or os.environ.get("QLOO_API_KEY") or _key_from_dotenv()
        if not self.key:
            raise QlooError("QLOO_API_KEY not set")
        self.base = base
        self.timeout = timeout
        self.calls = 0

    # ---- low level -------------------------------------------------------
    def _get(self, path: str, params: dict[str, Any]) -> dict:
        self.calls += 1
        for attempt in range(3):
            r = requests.get(self.base + path, headers={"X-Api-Key": self.key},
                             params=params, timeout=self.timeout)
            if r.status_code == 429:
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code >= 400:
                raise QlooError(f"{r.status_code} {path}: {r.text[:200]}")
            return r.json()
        raise QlooError(f"rate limited on {path}")

    # ---- lookups ---------------------------------------------------------
    def audiences(self, parent_type: str, take: int = 50) -> list[dict]:
        j = self._get("/v2/audiences", {"filter.parents.types": parent_type, "take": take})
        return j.get("results", {}).get("audiences", [])

    def all_audiences(self) -> list[dict]:
        out = []
        for p in AUDIENCE_PARENTS:
            for a in self.audiences(p):
                out.append({"id": a.get("id"), "name": a.get("name"), "parent": p})
        return out

    def search(self, query: str, types: str | None = None, take: int = 5) -> list[dict]:
        p: dict[str, Any] = {"query": query, "take": take}
        if types:
            p["types"] = types
        return self._get("/search", p).get("results", [])

    def tags(self, query: str, take: int = 20) -> list[dict]:
        return self._get("/v2/tags", {"filter.query": query, "take": take}).get("results", {}).get("tags", [])

    # ---- insights --------------------------------------------------------
    def insights(self, entity_type: str, *, audiences: list[str] | None = None,
                 entities: list[str] | None = None, tags: list[str] | None = None,
                 location_query: str | None = None, result_entities: list[str] | None = None,
                 popularity_min: float | None = None, take: int = 50,
                 explain: bool = False) -> list[dict]:
        p: dict[str, Any] = {"filter.type": entity_type, "take": take}
        if audiences:
            p["signal.demographics.audiences"] = ",".join(audiences)
        if entities:
            p["signal.interests.entities"] = ",".join(entities)
        if tags:
            p["signal.interests.tags"] = ",".join(tags)
        if location_query:
            p["filter.location.query"] = location_query
        if result_entities:
            p["filter.results.entities"] = ",".join(result_entities)
        if popularity_min is not None:
            p["filter.popularity.min"] = popularity_min
        if explain:
            p["feature.explainability"] = "true"
        return self._get("/v2/insights", p).get("results", {}).get("entities", [])

    def compare(self, a_entities: list[str], b_entities: list[str], entity_type: str | None = None,
                take: int = 30) -> dict:
        p: dict[str, Any] = {"a.signal.interests.entities": ",".join(a_entities),
                             "b.signal.interests.entities": ",".join(b_entities), "take": take}
        if entity_type:
            p["filter.type"] = entity_type
        return self._get("/v2/analysis/compare", p)


def _key_from_dotenv() -> str | None:
    """Read QLOO_API_KEY from a .env file in the current directory or the project root."""
    import pathlib
    for d in [pathlib.Path.cwd(), pathlib.Path(__file__).resolve().parents[1]]:
        f = d / ".env"
        if f.exists():
            for line in f.read_text().splitlines():
                if line.startswith("QLOO_API_KEY="):
                    return line.split("=", 1)[1].strip()
    return None


def affinity(e: dict) -> float:
    return float(e.get("query", {}).get("affinity", 0.0) or 0.0)


def slim(e: dict) -> dict:
    """Keep what the agent and the interface need from a Qloo entity."""
    props = e.get("properties", {}) or {}
    return {
        "id": e.get("entity_id"),
        "name": e.get("name"),
        "type": e.get("subtype") or e.get("type"),
        "affinity": round(affinity(e), 4),
        "popularity": round(float(e.get("popularity", 0) or 0), 4),
        "tags": [t.get("name") for t in (e.get("tags") or [])[:8]],
        "description": (props.get("description") or props.get("short_description") or "")[:300],
        "image": (props.get("image") or {}).get("url") if isinstance(props.get("image"), dict) else None,
    }
