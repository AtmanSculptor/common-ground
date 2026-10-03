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
