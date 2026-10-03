"""Common Ground web app. One page, one API. Run: uvicorn app:app --host 0.0.0.0 --port 8080"""
from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from common_ground.agent import run, DOMAINS
from common_ground.engine import Group
from common_ground.qloo import Qloo

app = FastAPI(title="Common Ground")

# The pages may run under a CSP sandbox (null origin) and behind a path prefix, so: open CORS, no credentials.
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])
ROOT = Path(__file__).resolve().parent
_q = Qloo()
_audiences_cache: dict = {}
_jobs: dict[str, dict] = {}


class GroupIn(BaseModel):
    label: str
    audiences: list[str] = Field(default_factory=list)


class RunIn(BaseModel):
    groups: list[GroupIn]
    goal: str = "find what they both love"
    location: str | None = None
    domains: list[str] | None = None
    exclude: list[str] = Field(default_factory=list)
    feedback: list[str] = Field(default_factory=list)


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/audiences")
def audiences():
    global _audiences_cache
    if not _audiences_cache or time.time() - _audiences_cache.get("_t", 0) > 86400:
        fams: dict[str, list] = {}
        for a in _q.all_audiences():
            fam = a["parent"].split(":")[-1].replace("_", " ")
            fams.setdefault(fam, []).append({"id": a["id"], "name": a["name"]})
        _audiences_cache = {"families": fams, "_t": time.time()}
    return {"families": _audiences_cache["families"], "domains": list(DOMAINS)}


@app.post("/api/run")
def start_run(body: RunIn):
    if len(body.groups) < 2:
        raise HTTPException(400, "need at least two groups")
    for g in body.groups:
        if not g.audiences:
            raise HTTPException(400, f"group '{g.label}' has no audiences")
    job_id = uuid.uuid4().hex[:10]
    job = {"id": job_id, "status": "running", "steps": [], "result": None, "error": None, "started": time.time()}
    _jobs[job_id] = job

    def work():
        try:
            groups = [Group(g.label, g.audiences) for g in body.groups]
            job["result"] = run(groups, body.goal, body.location or None, q=Qloo(),
                                domains=body.domains or None, exclude=body.exclude,
                                feedback=body.feedback, on_step=lambda s: job["steps"].append(s))
            job["status"] = "done"
        except Exception as e:  # surface, do not hide
            job["error"] = str(e)
            job["status"] = "error"

    threading.Thread(target=work, daemon=True).start()
    return {"job": job_id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "no such job")
    return JSONResponse({"status": job["status"], "steps": job["steps"], "result": job["result"],
                         "error": job["error"], "elapsed": round(time.time() - job["started"], 1)})


@app.on_event("startup")
def _warm():
    threading.Thread(target=audiences, daemon=True).start()


@app.get("/api/health")
def health():
    return {"ok": True, "qloo_calls": _q.calls}


# ============================ the game ============================
from common_ground.game import Games, Room, ROUND_DOMAINS, label as _label
from common_ground.engine import score_groups as _score_groups, summarize as _summarize
from common_ground.llm import chat_json as _chat_json

GAMES = Games()
_game_lock = threading.Lock()


class NewGame(BaseModel):
    name: str = "host"
    house: bool = False


class JoinGame(BaseModel):
    name: str = "guest"


class Answer(BaseModel):
    who: str
    a: str
    b: str
    chose: str | None = None


class Favorite(BaseModel):
    who: str
    query: str


class Rate(BaseModel):
    who: str
    stars: float


class Vote(BaseModel):
    who: str
    up: bool


def _advance(room: Room) -> None:
    """Deal the next card or finish, in the background so phones keep polling."""
    def work():
        with room.lock:
            try:
                if room.phase == "done" and room.result is None:
                    room.result = _finish(room)
                    return
                if room.phase == "jukebox":
                    if room.deal_jukebox(Qloo()) is None:
                        room.phase = "done"
                        room.result = _finish(room)
                    return
                room.phase = "dealing"
                if not room.deck:
                    room.build_deck(Qloo())
                card = room.deal(Qloo())
                if card is None:
                    room.phase = "jukebox"
                    if room.deal_jukebox(Qloo()) is None:
                        room.phase = "done"
                        room.result = _finish(room)
            except Exception as e:
                room.log.append(f"error: {e}")
                room.phase = "done"
                room.result = room.result or {"error": str(e)}
            GAMES.save()
    threading.Thread(target=work, daemon=True).start()


def _finish(room: Room) -> dict:
    sc = room.score()
    q = Qloo()
    groups = room.groups()
    playlist = []
    try:
        s = _summarize(_score_groups(q, groups, "urn:entity:artist", exclude=set(room.excluded), pool_per_group=20, pool_combined=30), top=8)
        playlist = [{"id": x["entity"]["id"], "name": x["entity"]["name"], "common": x["common"]} for x in s["unites"]]
    except Exception as e:
        room.log.append(f"playlist error: {e}")
    names = {k: (v.name or k) for k, v in room.sides.items()}
    profiles = {names[k]: [_label(x) for x in v.profile] for k, v in room.sides.items()}
    rounds = [{"card": c.entity["name"], "domain": c.domain.split(":")[-1],
               "ratings": {names[k]: v.ratings.get(c.round) for k, v in room.sides.items()}} for c in room.cards]
    brief = {}
    try:
        brief = _chat_json(
            "You write a warm, short note (under 120 words, plain prose, no bullet symbols, no em-dashes) telling two people what they share, "
            "based ONLY on the facts given: their profiles, the cards they both rated well, and the playlist. Mention specific names from the data. "
            "Never invent titles. Reply as JSON: {\"note\": \"...\"}",
            f"Score {sc['score']}/100, title '{sc['title']}'.\nProfiles: {profiles}\nRounds: {rounds}\nShared playlist: {[p['name'] for p in playlist]}",
            temperature=0.6, max_tokens=800)
    except Exception as e:
        room.log.append(f"note error: {e}")
    jb = room.jukebox or {}
    return {**sc, "note": brief.get("note", ""), "playlist": playlist, "profiles": profiles, "rounds": rounds,
            "jukebox": {"name": jb.get("entity", {}).get("name"), "played": jb.get("played")} if jb else None}


@app.get("/game")
def game_page():
    return FileResponse(ROOT / "static" / "game.html")


@app.post("/api/game/new")
def game_new(body: NewGame):
    GAMES.sweep()
    r = GAMES.create(body.name.strip()[:24] or "host", house=body.house)
    return {"code": r.code, "who": "host", "house": r.sides["guest"].name if body.house else None}


@app.post("/api/game/{code}/join")
def game_join(code: str, body: JoinGame):
    r = GAMES.join(code, body.name.strip()[:24] or "guest")
    if not r:
        raise HTTPException(404, "no such room, or it is full")
    return {"code": r.code, "who": "guest"}


@app.get("/api/game/{code}")
def game_state(code: str, who: str = "host"):
    r = GAMES.get(code)
    if not r:
        raise HTTPException(404, "no such room")
    st = r.public(who)
    if r.phase == "quiz" and who in r.sides:
        st["question"] = r.next_question(who)
    return st


@app.post("/api/game/{code}/answer")
def game_answer(code: str, body: Answer):
    r = GAMES.get(code)
    if not r or body.who not in r.sides:
        raise HTTPException(404, "no such room")
    with r.lock:
        r.answer(body.who, body.a, body.b, body.chose)
        start = r.both_profiled() and r.phase == "quiz"
    GAMES.save()
    if start:
        _advance(r)
    return {"ok": True}


@app.post("/api/game/{code}/favorite")
def game_favorite(code: str, body: Favorite):
    r = GAMES.get(code)
    if not r or body.who not in r.sides:
        raise HTTPException(404, "no such room")
    res = _q.search(body.query, take=1)
    if not res:
        return {"ok": False, "found": None}
    r.sides[body.who].favorite = {"id": res[0].get("entity_id"), "name": res[0].get("name")}
    return {"ok": True, "found": r.sides[body.who].favorite}


@app.post("/api/game/{code}/rate")
def game_rate(code: str, body: Rate):
    r = GAMES.get(code)
    if not r or body.who not in r.sides or r.phase != "rating":
        raise HTTPException(400, "not rating right now")
    with r.lock:
        r.rate(body.who, body.stars)
        both = r.both_rated()
        if both:
            r.after_rating()
    GAMES.save()
    if both:
        _advance(r)
    return {"ok": True}


@app.post("/api/game/{code}/jukebox")
def game_jukebox(code: str, body: Vote):
    r = GAMES.get(code)
    if not r or body.who not in r.sides or r.phase != "jukebox" or not r.jukebox:
        raise HTTPException(400, "no jukebox vote right now")
    with r.lock:
        r.vote_jukebox(body.who, body.up)
        outcome = r.jukebox_settled()
        if outcome in ("play", "skip"):
            r.phase = "done"
    GAMES.save()
    if outcome == "redeal":
        _advance(r)
    elif outcome in ("play", "skip"):
        _advance(r)
    return {"ok": True, "outcome": outcome}
