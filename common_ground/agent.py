"""Common Ground agent.

Loop:
  1. plan     - from the goal, choose which taste domains to probe
  2. guess    - ask a plain LLM the same question cold (the baseline)
  3. probe    - score every candidate against every group with Qloo (engine)
  4. check    - score the LLM's own guesses with Qloo, so we can show which held up
  5. compose  - write the bridge using ONLY entities Qloo returned; every pick
                must cite a Qloo entity id or it is dropped
Every step's inputs and outputs are kept in the trace so the interface can show the work.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any

from .engine import Group, score_groups, summarize, Scored, NO_SIGNAL
from .llm import chat, chat_json
from .qloo import Qloo, affinity

DOMAINS = {
    "artist": "urn:entity:artist",
    "movie": "urn:entity:movie",
    "tv_show": "urn:entity:tv_show",
    "podcast": "urn:entity:podcast",
    "book": "urn:entity:book",
    "place": "urn:entity:place",
    "brand": "urn:entity:brand",
    "video_game": "urn:entity:video_game",
    "destination": "urn:entity:destination",
    "person": "urn:entity:person",
}

PLAN_SYSTEM = """You plan research for Common Ground, a tool that finds what two or more groups of people both love, using measured taste data (Qloo), not guesses.
Given the groups and the user's goal, choose which taste domains to probe. Available domains: artist, movie, tv_show, podcast, book, place, brand, video_game, destination, person.
Rules: pick 2 to 4 domains that serve the goal. 'place' only makes sense when a location is given. Reply as JSON: {"domains": [...], "why": "one sentence"}."""

GUESS_SYSTEM = """You are a helpful assistant answering from general knowledge only. Do not hedge. Answer as JSON."""

COMPOSE_SYSTEM = """You write the Common Ground brief: a short, warm, concrete plan that brings the groups together around things the data shows they ALL love.
Hard rules:
- Use ONLY entities from the CANDIDATES list. Refer to each by its exact name and include its id in the picks. Never invent a title, artist, show, place or brand.
- Prefer candidates with higher 'common' and lower 'divide'. Mention a divide only to explain what to avoid.
- Choose 5 to 8 picks spread across the domains, and 2 to 4 things to avoid.
- Keep it under 250 words of prose, plain language, no bullet symbols in the prose, no em-dashes.
Reply as JSON:
{"title": "...", "brief": "...prose...", "picks": [{"id": "...", "name": "...", "domain": "...", "why": "one line"}], "avoid": [{"id": "...", "name": "...", "why": "one line"}]}"""


@dataclass
class Trace:
    steps: list[dict] = field(default_factory=list)

    def add(self, name: str, **data: Any) -> None:
        self.steps.append({"step": name, "t": round(time.time(), 2), **data})


def _group_text(groups: list[Group]) -> str:
    return " vs ".join(g.label for g in groups)


def plan(groups: list[Group], goal: str, location: str | None, trace: Trace) -> list[str]:
    user = f"Groups: {_group_text(groups)}\nGoal: {goal}\nLocation: {location or 'none'}"
    try:
        j = chat_json(PLAN_SYSTEM, user, temperature=0.2, max_tokens=1000)
        doms = [d for d in j.get("domains", []) if d in DOMAINS]
        why = j.get("why", "")
    except Exception as e:  # fall back to a sane default
        from .llm import LAST_RAW
        doms, why = [], f"planner failed: {e} | raw: {LAST_RAW['text'][:200]!r}"
    if not doms:
        doms = ["artist", "movie", "podcast"]
    if "place" in doms and not location:
        doms.remove("place")
    trace.add("plan", domains=doms, why=why)
    return doms


def guess(groups: list[Group], goal: str, domains: list[str], trace: Trace) -> dict[str, list[str]]:
    """The plain-LLM baseline: what would a model say with no data?"""
    user = (f"Groups: {_group_text(groups)}. Goal: {goal}.\n"
            f"For each domain in {domains}, list 6 specific named things that BOTH/ALL of these groups love. "
            f"Reply as JSON: {{\"<domain>\": [\"name\", ...], ...}}")
    try:
        j = chat_json(GUESS_SYSTEM, user, temperature=0.3, max_tokens=2000)
        out = {d: [str(x) for x in j.get(d, [])][:6] for d in domains}
    except Exception as e:
        from .llm import LAST_RAW
        out = {d: [] for d in domains}
        trace.add("guess_error", error=str(e), raw=LAST_RAW["text"][:300])
    trace.add("guess", guesses=out)
    return out


def check_guesses(q: Qloo, groups: list[Group], domain: str, names: list[str],
                  location: str | None) -> list[dict]:
    """Resolve the LLM's guesses to Qloo entities and score them per group."""
    etype = DOMAINS[domain]
    found: dict[str, str] = {}
    for n in names:
        try:
            res = q.search(n, types=etype, take=1)
        except Exception:
            res = []
        if res and res[0].get("entity_id"):
            found[res[0]["entity_id"]] = res[0].get("name", n)
    if not found:
        return [{"guess": n, "found": False} for n in names]
    raw: dict[str, dict[str, float]] = {i: {} for i in found}
    for g in groups:
        try:
            ents = q.insights(etype, result_entities=list(found), take=len(found),
                              location_query=location, **g.signal())
        except Exception:
            ents = []
        for e in ents:
            raw[e["entity_id"]][g.label] = round(affinity(e), 4)
    out = []
    for i, name in found.items():
        r = raw[i]
        no_data = any(abs(v - NO_SIGNAL) < 1e-6 for v in r.values())
        if r and len(r) == len(groups) and not no_data:
            verdict = "holds" if (max(r.values()) - min(r.values())) < 0.03 else "splits"
        else:
            verdict = "no data"
        out.append({"guess": name, "found": True, "id": i, "raw": r, "verdict": verdict,
                    "scored_by_all": len(r) == len(groups) and not no_data})
    missing = [n for n in names if n not in found.values()]
    out += [{"guess": n, "found": False} for n in missing]
    return out


def run(groups: list[Group], goal: str = "find what they both love", location: str | None = None,
        *, q: Qloo | None = None, domains: list[str] | None = None, top: int = 8,
        exclude: list[str] | None = None, feedback: list[str] | None = None,
        on_step=None) -> dict:
    """exclude: entity ids the user rejected; feedback: their reasons, fed to the composer."""
    q = q or Qloo()
    trace = Trace()
    if on_step:
        _orig_add = trace.add
        def _add(name, **data):
            _orig_add(name, **data)
            try:
                on_step(trace.steps[-1])
            except Exception:
                pass
        trace.add = _add  # type: ignore
    exclude_set = set(exclude or [])
    t0 = time.time()
    doms = domains or plan(groups, goal, location, trace)
    guesses = guess(groups, goal, doms, trace)

    results: dict[str, dict] = {}
    for d in doms:
        try:
            scored = score_groups(q, groups, DOMAINS[d], location=location, exclude=exclude_set)
        except Exception as e:
            trace.add("probe_error", domain=d, error=str(e))
            continue
        s = summarize(scored, top=top)
        checked = check_guesses(q, groups, d, guesses.get(d, []), location)
        results[d] = {"unites": s["unites"], "divides": s["divides"], "pool_size": s["pool_size"],
                      "llm_guesses_checked": checked}
        trace.add("probe", domain=d, pool=s["pool_size"], unites=len(s["unites"]),
                  guesses_found=sum(1 for c in checked if c.get("found")))

    # compose from Qloo-returned candidates only
    candidates = []
    for d, r in results.items():
        for s in r["unites"]:
            candidates.append({"id": s["entity"]["id"], "name": s["entity"]["name"], "domain": d,
                               "common": s["common"], "divide": s["divide"],
                               "tags": s["entity"].get("tags", [])[:5]})
    avoid_pool = []
    for d, r in results.items():
        for s in r["divides"][:3]:
            avoid_pool.append({"id": s["entity"]["id"], "name": s["entity"]["name"], "domain": d,
                               "leans": s["leans"], "divide": s["divide"]})
    allowed = {c["id"] for c in candidates} | {a["id"] for a in avoid_pool}
    brief = {"title": "", "brief": "", "picks": [], "avoid": []}
    if candidates:
        fb = ("\n\nUSER FEEDBACK on earlier picks (respect it): " + "; ".join(feedback)) if feedback else ""
        user = (f"Groups: {_group_text(groups)}\nGoal: {goal}\nLocation: {location or 'none'}{fb}\n\n"
                f"CANDIDATES (what the data says they all love):\n{candidates}\n\n"
                f"DIVIDES (what to avoid, with which group it leans to):\n{avoid_pool}")
        try:
            brief = chat_json(COMPOSE_SYSTEM, user, temperature=0.5, max_tokens=4000)
        except Exception as e:
            from .llm import LAST_RAW
            trace.add("compose_error", error=str(e), raw=LAST_RAW["text"][:300])
        # citation guard: drop any pick whose id Qloo did not return
        kept, dropped = [], []
        for p in brief.get("picks", []):
            (kept if p.get("id") in allowed else dropped).append(p)
        brief["picks"] = kept
        brief["avoid"] = [a for a in brief.get("avoid", []) if a.get("id") in allowed]
        trace.add("compose", picks=len(kept), dropped_uncited=len(dropped))

    return {
        "groups": [asdict(g) for g in groups],
        "goal": goal,
        "location": location,
        "excluded": sorted(exclude_set),
        "feedback": feedback or [],
        "domains": doms,
        "results": results,
        "brief": brief,
        "trace": trace.steps,
        "qloo_calls": q.calls,
        "seconds": round(time.time() - t0, 1),
    }
