"""Small OpenRouter chat client. No framework, one function, and a daily spend cap.

The cap (CG_LLM_DAILY_CAP calls per UTC day, default 150) is the only thing standing
between a public demo and an empty OpenRouter balance. Callers catch BudgetExceeded and
fall back to templated text; the game never depends on the LLM to finish.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
import re
import threading

import requests

DEFAULT_MODEL = os.environ.get("CG_LLM_MODEL", "google/gemini-3.5-flash-lite")
DAILY_CAP = int(os.environ.get("CG_LLM_DAILY_CAP", "150"))
BUDGET_FILE = pathlib.Path(os.environ.get("CG_DATA_DIR", str(pathlib.Path(__file__).resolve().parents[1] / "data"))) / "llm_budget.json"
_budget_lock = threading.Lock()


class BudgetExceeded(RuntimeError):
    pass


def _spend_one() -> int:
    """Count one call against today's cap. Raises BudgetExceeded at the cap."""
    today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    with _budget_lock:
        try:
            d = json.loads(BUDGET_FILE.read_text())
        except Exception:
            d = {}
        n = d.get("calls", 0) if d.get("day") == today else 0
        if n >= DAILY_CAP:
            raise BudgetExceeded(f"LLM daily cap of {DAILY_CAP} reached")
        n += 1
        try:
            BUDGET_FILE.parent.mkdir(parents=True, exist_ok=True)
            BUDGET_FILE.write_text(json.dumps({"day": today, "calls": n}))
        except Exception:
            pass
        return n


def budget_status() -> dict:
    try:
        d = json.loads(BUDGET_FILE.read_text())
    except Exception:
        d = {}
    today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    return {"day": today, "calls": d.get("calls", 0) if d.get("day") == today else 0, "cap": DAILY_CAP}


def _key() -> str:
    k = os.environ.get("OPENROUTER_API_KEY")
    if k:
        return k
    for d in [pathlib.Path.cwd(), pathlib.Path(__file__).resolve().parents[1]]:
        f = d / ".env"
        if f.exists():
            for line in f.read_text().splitlines():
                if line.startswith("OPENROUTER_API_KEY="):
                    return line.split("=", 1)[1].strip()
    raise RuntimeError("OPENROUTER_API_KEY not set")


def chat(system: str, user: str, *, model: str = DEFAULT_MODEL, temperature: float = 0.4,
         max_tokens: int = 1500, json_mode: bool = False) -> str:
    _spend_one()
    body = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    # keep hidden reasoning from eating the output budget on thinking models
    body["reasoning"] = {"effort": "low"}
    r = requests.post("https://openrouter.ai/api/v1/chat/completions",
                      headers={"Authorization": f"Bearer {_key()}",
                               "HTTP-Referer": "https://github.com/AtmanSculptor/common-ground",
                               "X-Title": "Our Song"},
                      json=body, timeout=120)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


LAST_RAW = {"text": ""}


def _parse_json(text: str) -> dict:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", t, re.S)
    if m:
        frag = m.group(0)
        try:
            return json.loads(frag)
        except json.JSONDecodeError:
            import ast
            try:
                v = ast.literal_eval(frag)
                if isinstance(v, dict):
                    return v
            except Exception:
                pass
    # A reply cut off mid-value ('{"artist": "Manu Chao') still carries its string fields.
    pairs = re.findall(r'"([A-Za-z_][\w]*)"\s*:\s*"([^"\n]*)', t)
    if pairs:
        return {k: v.strip() for k, v in pairs}
    raise ValueError(f"not JSON: {t[:120]!r}")


def chat_json(system: str, user: str, **kw) -> dict:
    """Ask for JSON, parse defensively, retry once with a stricter instruction."""
    text = chat(system, user, json_mode=True, **kw)
    LAST_RAW["text"] = text
    try:
        return _parse_json(text)
    except ValueError:
        text2 = chat(system + "\nReturn ONLY a single JSON object. No prose, no code fences, double quotes only.",
                     user, json_mode=False, **kw)
        LAST_RAW["text"] = text2
        return _parse_json(text2)
