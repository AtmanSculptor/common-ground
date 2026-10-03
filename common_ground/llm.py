"""Small OpenRouter chat client. No framework, one function."""
from __future__ import annotations

import json
import os
import pathlib
import re

import requests

DEFAULT_MODEL = os.environ.get("CG_LLM_MODEL", "google/gemini-3.5-flash-lite")


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
                               "X-Title": "Common Ground"},
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
