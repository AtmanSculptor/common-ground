"""Our Song: the web app. One process, one API.

Routes
  /  /play     the game: two phones, one room, Q-Lou in the jukebox
  /organize    the older organizer (what do these audiences share? a plan with Qloo citations)
  /deck        the cards, as a gallery
  /stats       results so far
  /api/...     JSON for the pages above

Run: uvicorn app:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from common_ground import guard, matrix, stats
from common_ground import rooms as rooms_mod
from common_ground.agent import DOMAINS, run
from common_ground.engine import Group
from common_ground.llm import budget_status
from common_ground.qloo import Qloo
from common_ground.rooms import Room, Rooms

ROOT = Path(__file__).resolve().parent

# Small hosts cap virtual memory (ulimit -v). Every thread costs its stack plus a glibc arena in
# address space, so keep threads cheap and few: 1 MB stacks (the app is shallow) and a dozen
# request workers instead of anyio's forty. MALLOC_ARENA_MAX is set by the launcher.
THREAD_STACK = 1 << 20
REQUEST_WORKERS = 12
threading.stack_size(THREAD_STACK)


def resources() -> dict:
    """Threads and memory, for /api/health: the numbers that explain a 'cannot allocate memory'."""
    out: dict = {"threads": threading.active_count()}
    try:
        import resource
        soft, _ = resource.getrlimit(resource.RLIMIT_AS)
        out["vm_limit_mb"] = None if soft == resource.RLIM_INFINITY else soft // (1 << 20)
    except Exception:
        pass
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith(("VmSize:", "VmRSS:")):
                key, val = line.split(":", 1)
                out[key.lower() + "_mb"] = int(val.split()[0]) // 1024
    except Exception:
        pass
    return out


REFRESH_EVERY = int(os.environ.get("CG_REFRESH_CHECK_MINUTES", "60"))   # 0 turns the keeper off
# The venue's own line on the playing field. In a jukebox app the bar sets this (TouchTunes already
# sells venues promotion space); here it comes from the environment. Empty name turns it off.
VENUE = {"name": os.environ.get("CG_VENUE", "Q-Lou's Lounge"), "line": os.environ.get("CG_VENUE_LINE", "Happy hour till 7"),
         # a second slot: one line a night from the bar or a sponsor, shown while Q-Lou thinks. "|" separates lines;
         # each room gets one, the same on both phones.
         "quips": [q.strip() for q in os.environ.get("CG_VENUE_QUIPS", "Tip your bartender. She knows your song.|Kitchen's open till 10.|Trivia Thursdays. Bring the whole table.").split("|") if q.strip()]}


def venue_for(code: str) -> dict | None:
    if not VENUE["name"]:
        return None
    quips = VENUE["quips"]
    return {"name": VENUE["name"], "line": VENUE["line"], "quip": quips[sum(map(ord, code)) % len(quips)] if quips else ""}


def _keeper() -> None:
    """Keeps the matrix current without anyone remembering to: extends from the outside list every
    week, rebuilds every month in the quiet hours. Never runs over a build someone started by hand."""
    time.sleep(120)
    while True:
        try:
            m = matrix.load()
            new_cards = matrix.missing_cards(m) if m else []
            need = "cards" if new_cards else matrix.due(m)
            if need and _matrix_job["status"] != "running":
                _matrix_job.update({"status": "running", "progress": [], "started": time.time(), "by": "keeper", "what": need})
                q = Qloo(timeout=60)
                if need == "cards":
                    out = matrix.add_cards(q, new_cards, on_progress=lambda d: _matrix_job["progress"].append(d))
                    _matrix_job.update({"status": "done", "cards": out["cards"], "calls": out["calls"]})
                elif need == "build":
                    m = matrix.build(q, on_progress=lambda d: _matrix_job["progress"].append(d))
                    _matrix_job.update({"status": "done", "artists": len(m["entities"]), "cards": m["cards"], "calls": m["calls"]})
                else:
                    out = matrix.extend(q, matrix.seed_names(), on_progress=lambda d: _matrix_job["progress"].append(d))
                    _matrix_job.update({"status": "done", "artists": out["artists"], "added": len(out["added"]), "calls": q.calls})
        except Exception as e:
            _matrix_job.update({"status": "error", "error": str(e)[:200]})
        time.sleep(REFRESH_EVERY * 60)


@asynccontextmanager
async def _lifespan(_: FastAPI):
    anyio.to_thread.current_default_thread_limiter().total_tokens = REQUEST_WORKERS
    threading.Thread(target=audiences, daemon=True).start()  # warm the audience list for the organizer
    if REFRESH_EVERY:
        threading.Thread(target=_keeper, daemon=True, name="matrix-keeper").start()
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
@app.get("/play")
def play_page():
    """The game is the front door."""
    return page("play.html")


@app.get("/screen/{code}")
def screen_page(code: str):
    """The venue's vertical screen: watches one room, read-only."""
    return page("screen.html")


@app.get("/screen")
def screen_redirect(code: str = ""):
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"screen/{code.upper()}" if code else "screen/NONE", status_code=302)


@app.get("/organize")
def organize_page():
    """The older organizer tool: what do N audience segments share."""
    return page("index.html")


@app.get("/deck")
def deck_page():
    return page("deck.html")


@app.get("/stats")
def stats_page():
    return page("stats.html")


@app.get("/api/health")
def health():
    return {"ok": True, "qloo_calls": _q.calls, "rooms": len(ROOMS.rooms), "llm": budget_status(), "tables_today": guard.tables_today(), "matrix": matrix.available(),
            "resources": resources()}


@app.get("/api/stats")
def stats_api():
    return stats.summary()


# ============================ maintenance (loopback only) ============================
# The card matrix is built by the running app because only it holds the Qloo key. The route
# answers only to the server's own loopback with no proxy headers, so the public cannot
# trigger a 300-call build.
_matrix_job: dict = {"status": "idle", "progress": []}


def _local_only(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in ("127.0.0.1", "::1") or request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip"):
        raise HTTPException(404, "not found")


@app.post("/api/admin/matrix")
def matrix_build(request: Request):
    _local_only(request)
    if _matrix_job["status"] == "running":
        return {"ok": False, "status": "running"}
    _matrix_job.update({"status": "running", "progress": [], "started": time.time()})

    def work():
        try:
            m = matrix.build(Qloo(timeout=60), on_progress=lambda d: _matrix_job["progress"].append(d))
            _matrix_job.update({"status": "done", "artists": len(m["entities"]), "cards": m["cards"], "calls": m["calls"]})
        except Exception as e:
            _matrix_job.update({"status": "error", "error": str(e)})

    threading.Thread(target=work, daemon=True).start()
    return {"ok": True, "status": "running"}


@app.post("/api/admin/matrix/cards")
def matrix_cards(request: Request):
    """Score the cards the matrix has no column for yet (new art landed). Loopback only."""
    _local_only(request)
    new_cards = matrix.missing_cards(matrix.load())
    if not new_cards:
        return {"ok": True, "status": "nothing to do"}
    if _matrix_job["status"] == "running":
        return {"ok": False, "status": "running"}
    _matrix_job.update({"status": "running", "progress": [], "started": time.time(), "by": "admin", "what": "cards"})

    def work():
        try:
            out = matrix.add_cards(Qloo(timeout=60), new_cards, on_progress=lambda d: _matrix_job["progress"].append(d))
            _matrix_job.update({"status": "done", "cards": out["cards"], "calls": out["calls"]})
        except Exception as e:
            _matrix_job.update({"status": "error", "error": str(e)[:200]})

    threading.Thread(target=work, daemon=True).start()
    return {"ok": True, "status": "running", "cards": [c["name"] for c in new_cards]}


@app.get("/api/admin/search")
def admin_search(request: Request, q: str, types: str | None = "urn:entity:artist", take: int = 5):
    """Maintenance: what Qloo's /search returns for a name (to tune the seed resolver). Loopback only."""
    _local_only(request)
    hits = _q.search(q, types=types or None, take=take)
    return {"query": q, "resolved": matrix._resolve_seed(_q, q),
            "hits": [{"name": h.get("name"), "id": h.get("entity_id"), "types": h.get("types"), "popularity": h.get("popularity")} for h in hits]}


class ExtendIn(BaseModel):
    names: list[str] = Field(default_factory=list)


@app.post("/api/admin/matrix/extend")
def matrix_extend(request: Request, body: ExtendIn):
    """Maintenance: add outside names to the built matrix without a rebuild. Loopback only, synchronous
    (one call per card plus one search per name: under a minute for a few dozen names)."""
    _local_only(request)
    if _matrix_job["status"] == "running":
        raise HTTPException(409, "a build is running")
    log: list[dict] = []
    out = matrix.extend(Qloo(timeout=60), body.names, on_progress=log.append)
    return {**out, "log": log[-3:]}


@app.post("/api/admin/stats/archive")
def stats_archive(request: Request, label: str = ""):
    """Maintenance: move the results log aside so a pub night starts at zero. Loopback only."""
    _local_only(request)
    return {"archived": stats.archive(label)}


@app.get("/api/admin/probe")
def admin_probe(request: Request, what: str, a: str = "", b: str = "", q: str = ""):
    """Maintenance: try a Qloo endpoint and see its raw shape. Loopback only."""
    _local_only(request)
    try:
        if what == "compare":
            return _q.compare(a.split(","), b.split(","), entity_type="urn:entity:artist", take=10)
        if what == "trending":
            return _q._get("/v2/trending", {"filter.type": "urn:entity:artist", "signal.interests.entities": q, "take": 5})
        if what == "trend2":
            return _q._get("/v2/trending", {"filter.type": "urn:entity:artist", "signal.interests.entities": q,
                                            "filter.start_date": a, "filter.end_date": b, "take": 5})
        if what == "trending_id":
            return _q._get("/v2/trending", {"filter.type": "urn:entity:artist", "filter.entities": q, "take": 5})
        if what == "signal":      # a candidate card's signal: audiences in a, tags in b -> its top artists
            ents = _q.insights("urn:entity:artist", audiences=[x for x in a.split(",") if x] or None,
                               tags=[x for x in b.split(",") if x] or None, take=8, popularity_min=0.9)
            return {"artists": [{"id": e.get("entity_id"), "name": e.get("name"), "popularity": e.get("popularity")} for e in ents]}
        if what == "tags":
            return {"tags": [{"id": t.get("id"), "name": t.get("name"), "type": t.get("type")} for t in _q.tags(q, take=20)]}
        return {"error": "unknown probe"}
    except Exception as e:
        return {"error": str(e)[:400]}


@app.get("/api/admin/matrix")
def matrix_status(request: Request):
    _local_only(request)
    return {**{k: v for k, v in _matrix_job.items() if k != "progress"}, "progress": _matrix_job["progress"][-6:],
            "steps": len(_matrix_job["progress"]), "available": matrix.available()}


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
    icon: int = 0


class JoinGame(BaseModel):
    name: str = "guest"
    icon: int = 0


class Cards(BaseModel):
    who: str
    cards: list[int]


class Card(BaseModel):
    who: str
    card: int


class Emote(BaseModel):
    who: str
    emote: str


class Vote(BaseModel):
    who: str
    up: bool


class Leave(BaseModel):
    who: str


def _room(code: str, who: str | None = None) -> Room:
    r = ROOMS.get(code)
    if not r or (who is not None and who not in r.sides):
        raise HTTPException(404, "no such room")
    return r


def _think(room: Room) -> None:
    """Q-Lou works in a background thread so the phones keep polling and see the steps. The
    phase moves to the jukebox by itself when the pick is ready, but not before the table has
    had its moment after the third flip (INTERLUDE_MIN)."""
    def work():
        try:
            p = room.think(Qloo(timeout=25), recent=stats.recent_ids(rooms_mod.ROTATION))
            hold = room.revealed_at + rooms_mod.INTERLUDE_MIN - time.time()
            if hold > 0 and len(room.offers) <= 1:
                time.sleep(hold)
            with room.lock:
                if p is None:
                    if room.phase == "thinking":
                        room.finish()
                        stats.record(room.code, room.result, room.sides["guest"].house)
                elif room.phase == "thinking":
                    room.phase = "jukebox"
        except Exception as e:
            with room.lock:
                room.log.append(f"error: {e}")
                room.finish()
        ROOMS.save()
    threading.Thread(target=work, daemon=True).start()


# ============================ the venue: the room tonight ============================
from common_ground import venue as venue_mod
VENUE_ROOM = venue_mod.Venue(lambda: ROOMS)


class Here(BaseModel):
    name: str = ""
    icon: int = 0


class Invite(BaseModel):
    name: str = ""
    icon: int = 0
    to: str = ""


class Answer(BaseModel):
    name: str = ""
    icon: int = 0
    yes: bool = True


@app.get("/api/venue")
def venue_snapshot(me: str = ""):
    return VENUE_ROOM.snapshot(me)


def _table_or_full(request: Request) -> None:
    """A new table costs Qloo calls; the door counts them per address and per day."""
    if not guard.allow_table(guard.client_ip(request)):
        raise HTTPException(429, guard.FULL)


@app.post("/api/venue/here")
def venue_here(body: Here):
    body.name = guard.clean_name(body.name)
    VENUE_ROOM.heartbeat(body.name, body.icon)
    return {"ok": True, **VENUE_ROOM.poll(body.name)}


@app.post("/api/venue/next")
def venue_next(body: Here, request: Request):
    body.name = guard.clean_name(body.name)
    if not body.name.strip():
        raise HTTPException(400, "say a name first")
    _table_or_full(request)
    return VENUE_ROOM.next_person(body.name, body.icon)


@app.post("/api/venue/leave")
def venue_leave(body: Here):
    VENUE_ROOM.leave_queue(body.name)
    return {"ok": True}


@app.post("/api/venue/invite")
def venue_invite(body: Invite, request: Request):
    body.name = guard.clean_name(body.name)
    if not body.name.strip() or not body.to.strip():
        raise HTTPException(400, "who?")
    _table_or_full(request)
    return VENUE_ROOM.invite(body.name, body.icon, body.to)


@app.post("/api/venue/answer")
def venue_answer(body: Answer, request: Request):
    body.name = guard.clean_name(body.name)
    if body.yes:
        _table_or_full(request)
    return VENUE_ROOM.answer(body.name, body.icon, body.yes)


@app.get("/api/profile")
def profile_lookup(name: str = ""):
    """The venue log for a nickname: games, songs, level. The lobby shows it beside the name as you type."""
    name = (name or "").strip()[:24]
    out = dict(stats.profile(name)) if name else {"games": 0, "songs": 0, "level": 0, "tonight": 0, "cards": []}
    out["title"] = venue_mod.title_for(out.get("games", 0))
    pic = VENUE_ROOM.pic_for(name) if name else None
    if pic is not None:
        out["icon"] = pic
    return out


@app.post("/api/game/new")
def game_new(body: NewGame, request: Request):
    ROOMS.sweep()
    _table_or_full(request)
    r = ROOMS.create(guard.clean_name(body.name) or "You", house=body.house, icon=body.icon)
    return {"code": r.code, "who": "host", "house": r.sides["guest"].name if body.house else None}


@app.post("/api/game/{code}/join")
def game_join(code: str, body: JoinGame):
    r = ROOMS.join(code, guard.clean_name(body.name) or "Partner", icon=body.icon)
    if not r:
        raise HTTPException(404, "no such room, or it is full")
    return {"code": r.code, "who": "guest"}


def _settle(r: Room) -> str | None:
    """Both votes are in (or not): play it, send Q-Lou back for another, or call it no match."""
    outcome = r.settled()
    if outcome == "again":
        r.phase = "thinking"
    elif outcome in ("play", "nothing"):
        r.finish()
        stats.record(r.code, r.result, r.sides["guest"].house)
    return outcome


@app.get("/api/game/{code}")
def game_state(code: str, who: str = "host"):
    r = _room(code)
    moved = None
    if r.sides["guest"].house:
        with r.lock:
            moved = r.tick(ROOMS.rng)
            if moved == "vote":
                moved = _settle(r)
        if moved:
            ROOMS.save()
        if moved in ("reveal", "again"):
            _think(r)
    st = r.public(who)
    st["venue"] = venue_for(code)
    return st


@app.post("/api/game/{code}/toss")
def game_toss(code: str, body: Cards):
    """The deal: toss any of your cards (or none) and lock. Fresh cards come back in the state."""
    r = _room(code, body.who)
    with r.lock:
        ok = r.toss(body.who, body.cards, ROOMS.rng)
    ROOMS.save()
    if not ok:
        raise HTTPException(400, "toss cards from your hand, once")
    return {"ok": True, "phase": r.phase, "hand": r.public(body.who)["sides"][body.who]["hand"]}


@app.post("/api/game/{code}/play")
def game_play(code: str, body: Card):
    """One card for the round being played. When both are down the pair flips; after the third
    flip Q-Lou starts at once, so the interlude hides most of the thinking time."""
    r = _room(code, body.who)
    with r.lock:
        ok = r.play(body.who, body.card, ROOMS.rng)
        think = ok and r.phase == "thinking"
    ROOMS.save()
    if not ok:
        raise HTTPException(400, "one card from your hand, once per round")
    if think:
        _think(r)
    return {"ok": True, "rank": r.rank, "phase": r.phase, "pairs": r.pairs}


@app.post("/api/game/{code}/emote")
def game_emote(code: str, body: Emote):
    """A nudge across the table; the partner's next poll carries it."""
    r = _room(code, body.who)
    with r.lock:
        ok = r.emote(body.who, body.emote)
    return {"ok": ok}


@app.post("/api/game/{code}/leave")
def game_leave(code: str, body: Leave):
    """A phone left a live table (Back to the bar). The partner's next poll says so, instead of waiting forever."""
    r = _room(code, body.who)
    with r.lock:
        if r.phase != "done" and not r.left:
            r.left = body.who
            r.log.append(f"{body.who} left")
    ROOMS.save()
    return {"ok": True, "left": r.left}


@app.post("/api/game/{code}/vote")
def game_vote(code: str, body: Vote):
    r = _room(code, body.who)
    if r.phase != "jukebox" or not r.pick or r.pick["status"] != "open":
        raise HTTPException(400, "no song to vote on right now")
    with r.lock:
        r.vote(body.who, body.up)
        r.house_will_vote(ROOMS.rng)
        r.tick(ROOMS.rng)            # a house with no pause answers now; otherwise on a later poll
        outcome = _settle(r)
    ROOMS.save()
    if outcome == "again":
        _think(r)
    return {"ok": True, "outcome": outcome}
