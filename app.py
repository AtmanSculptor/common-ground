"""Our Song: the web app. One process, one API.

Routes
  /            the organizer (what do these audiences share? a plan with Qloo citations)
  /play        the game: two phones, one room, Q-Lou in the jukebox
  /deck        the cards, as a gallery
  /stats       results so far
  /api/...     JSON for the pages above

Run: uvicorn app:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from common_ground import stats
from common_ground.agent import DOMAINS, run
from common_ground.engine import Group
from common_ground.llm import budget_status
from common_ground.qloo import Qloo
from common_ground.rooms import Room, Rooms

ROOT = Path(__file__).resolve().parent


@asynccontextmanager
async def _lifespan(_: FastAPI):
    threading.Thread(target=audiences, daemon=True).start()  # warm the audience list for the organizer
    yield


app = FastAPI(title="Our Song", docs_url=None, redoc_url=None, lifespan=_lifespan)
# Pages may run under a CSP sandbox (null origin) behind a path prefix: open CORS, no credentials, no cookies.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")

_q = Qloo()
_audiences_cache: dict = {}
_jobs: dict[str, dict] = {}
ROOMS = Rooms()


def page(name: str) -> FileResponse:
    return FileResponse(ROOT / "static" / name)


# ============================ pages ============================
@app.get("/")
def index():
    return page("index.html")


@app.get("/play")
def play_page():
    return page("play.html")


@app.get("/deck")
def deck_page():
    return page("deck.html")


@app.get("/stats")
def stats_page():
    return page("stats.html")


@app.get("/api/health")
def health():
    return {"ok": True, "qloo_calls": _q.calls, "rooms": len(ROOMS.rooms), "llm": budget_status()}


@app.get("/api/stats")
def stats_api():
    return stats.summary()


# ============================ the organizer ============================
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
            job["result"] = run(groups, body.goal, body.location or None, q=Qloo(), domains=body.domains or None,
                                exclude=body.exclude, feedback=body.feedback, on_step=lambda s: job["steps"].append(s))
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


# ============================ the game ============================
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


class Play(BaseModel):
    who: str
    cards: list[int]


class Vote(BaseModel):
    who: str
    up: bool


def _room(code: str, who: str | None = None) -> Room:
    r = ROOMS.get(code)
    if not r or (who is not None and who not in r.sides):
        raise HTTPException(404, "no such room")
    return r


def _think(room: Room) -> None:
    """Q-Lou works in a background thread so the phones keep polling and see the steps."""
    def work():
        with room.lock:
            try:
                if room.think(Qloo(timeout=25)) is None:
                    room.finish()
                    stats.record(room.code, room.result, room.sides["guest"].house)
            except Exception as e:
                room.log.append(f"error: {e}")
                room.finish()
            ROOMS.save()
    threading.Thread(target=work, daemon=True).start()


@app.post("/api/game/new")
def game_new(body: NewGame):
    ROOMS.sweep()
    r = ROOMS.create(body.name.strip()[:24] or "You", house=body.house)
    return {"code": r.code, "who": "host", "house": r.sides["guest"].name if body.house else None}


@app.post("/api/game/{code}/join")
def game_join(code: str, body: JoinGame):
    r = ROOMS.join(code, body.name.strip()[:24] or "Partner")
    if not r:
        raise HTTPException(404, "no such room, or it is full")
    return {"code": r.code, "who": "guest"}


@app.get("/api/game/{code}")
def game_state(code: str, who: str = "host"):
    r = _room(code)
    st = r.public(who)
    if r.phase == "quiz" and who in r.sides:
        st["question"] = r.question(who)
    return st


@app.post("/api/game/{code}/answer")
def game_answer(code: str, body: Answer):
    r = _room(code, body.who)
    with r.lock:
        r.answer(body.who, body.a, body.b, body.chose)
        if r.phase == "quiz" and r.both_quizzed():
            r.deal(ROOMS.rng)
    ROOMS.save()
    return {"ok": True}


@app.post("/api/game/{code}/play")
def game_play(code: str, body: Play):
    r = _room(code, body.who)
    with r.lock:
        ok = r.play(body.who, body.cards)
        if ok and r.both_locked():
            r.reveal(ROOMS.rng)
    ROOMS.save()
    if not ok:
        raise HTTPException(400, "play exactly two cards from your hand")
    return {"ok": True}


@app.post("/api/game/{code}/next")
def game_next(code: str):
    """After a reveal: the next round of cards, or Q-Lou starts thinking."""
    r = _room(code)
    think = False
    with r.lock:
        if r.phase == "reveal":
            if r.rounds_left():
                r.deal(ROOMS.rng)
            else:
                r.phase = "thinking"
                think = True
    ROOMS.save()
    if think:
        _think(r)
    return {"ok": True, "phase": r.phase}


@app.post("/api/game/{code}/vote")
def game_vote(code: str, body: Vote):
    r = _room(code, body.who)
    if r.phase != "jukebox" or not r.pick:
        raise HTTPException(400, "no song to vote on right now")
    with r.lock:
        r.vote(body.who, body.up)
        if r.sides["guest"].house and "guest" not in r.pick["votes"]:
            r.vote("guest", True)  # the house always says yes to a shared song
        outcome = r.settled()
        if outcome == "redeal":
            r.phase = "thinking"
        elif outcome in ("play", "skip"):
            r.finish()
            stats.record(r.code, r.result, r.sides["guest"].house)
    ROOMS.save()
    if outcome == "redeal":
        _think(r)
    return {"ok": True, "outcome": outcome}
