"""Private web app: ``uvicorn psai.server:app``.

Set ANTHROPIC_API_KEY. Set PSAI_ACCESS_CODE to require a code (sent as the
``X-Access-Code`` header by the page) so only people you share it with can ask.
"""

from __future__ import annotations

import hmac
import os
import re
import time
from collections import OrderedDict
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .answer import ask
from .store import connect

app = FastAPI(title="Ask Pope Shenouda (AI)", docs_url=None, redoc_url=None)
WEB = Path(__file__).parent / "web"
_con = None
_cache: "OrderedDict[str, dict]" = OrderedDict()   # repeat questions cost nothing
_CACHE_MAX = 500
_day = {"date": "", "n": 0}
DAILY_LIMIT = int(os.environ.get("PSAI_DAILY_LIMIT", "200"))  # hard cap on model calls per day


class Question(BaseModel):
    question: str = Field(min_length=2, max_length=1000)


def _check(code: str | None) -> None:
    want = os.environ.get("PSAI_ACCESS_CODE")
    if want and not hmac.compare_digest(code or "", want):
        raise HTTPException(401, "access code required")


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/status")
def status(x_access_code: str | None = Header(default=None)):
    _check(x_access_code)
    con = connect()
    n, c = con.execute("SELECT COUNT(*), COALESCE(SUM(n_pages),0) FROM books").fetchone()
    return {"books": n, "pages": c, "needs_code": bool(os.environ.get("PSAI_ACCESS_CODE"))}


@app.post("/api/ask")
def ask_endpoint(q: Question, x_access_code: str | None = Header(default=None)):
    _check(x_access_code)
    global _con
    _con = _con or connect()
    key = re.sub(r"\s+", " ", q.question.strip().lower())
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    today = time.strftime("%Y-%m-%d")
    if _day["date"] != today:
        _day.update(date=today, n=0)
    if _day["n"] >= DAILY_LIMIT:
        return JSONResponse({"error": "Daily question limit reached. Please try again tomorrow."}, 429)
    _day["n"] += 1
    try:
        result = ask(q.question.strip(), con=_con)
    except Exception as exc:  # never leak internals to the page
        return JSONResponse({"error": "The service could not answer right now.", "detail": type(exc).__name__}, 503)
    out = result.to_dict()
    if not result.notes or result.found:
        _cache[key] = out
        if len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return out
