"""HTTP API и раздача веб-интерфейса."""
from __future__ import annotations

import logging
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import env, load_dotenv

load_dotenv()

from . import agent, llm, metrics, store  # noqa: E402 — после чтения .env
from .state import SessionState  # noqa: E402

logging.basicConfig(level=env("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    store.conn()
    log.info("Trip agent started. LLM: %s", f"on ({llm.model_name()})" if llm.enabled() else "off (rules only)")
    if env("TELEGRAM_BOT_TOKEN"):
        from .telegram_bot import start_in_background
        start_in_background()
    yield


app = FastAPI(title="Ostrovok Trip Agent", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

_sessions: dict[str, SessionState] = {}
_locks: dict[str, threading.Lock] = {}
_glock = threading.Lock()


def _lock(sid: str) -> threading.Lock:
    with _glock:
        return _locks.setdefault(sid, threading.Lock())


def get_session(sid: str | None, user_id: str) -> SessionState:
    if sid:
        s = _sessions.get(sid) or store.load_session(sid)
        if s:
            _sessions[sid] = s
            return s
    s = SessionState(user_id=user_id)
    _sessions[s.id] = s
    return s


class NewSession(BaseModel):
    user_id: str | None = None


class ChatIn(BaseModel):
    session_id: str | None = None
    user_id: str | None = None
    message: str | None = None
    action: dict | None = None


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/config")
def config():
    return {"llm": llm.enabled(), "model": llm.model_name(),
            "mock_data": True}


@app.post("/api/session")
def new_session(body: NewSession):
    uid = body.user_id or "u-" + uuid.uuid4().hex[:10]
    s = SessionState(user_id=uid)
    _sessions[s.id] = s
    w = agent.welcome(s)
    s.history.append({"role": "agent", "text": w["text"]})
    store.save_session(s)
    return {"session_id": s.id, "user_id": uid, "messages": [w], "summary": agent.summary(s)}


@app.post("/api/chat")
def chat(body: ChatIn):
    if not body.message and not body.action:
        raise HTTPException(400, "message or action required")
    uid = body.user_id or "anon"
    s = get_session(body.session_id, uid)
    with _lock(s.id):
        out = agent.handle(s, (body.message or "").strip()[:2000] or None, body.action)
        store.save_session(s)
    return {"session_id": s.id, "user_id": s.user_id, "messages": [out], "summary": agent.summary(s), "trace": out.get("trace", [])}


@app.get("/api/session/{sid}")
def session_info(sid: str):
    s = get_session(sid, "anon")
    return {"session_id": s.id, "summary": agent.summary(s), "history": s.history[-50:], "stage": s.stage}


@app.get("/api/profile/{user_id}")
def profile(user_id: str):
    return store.get_profile(user_id)


@app.delete("/api/profile/{user_id}")
def forget(user_id: str):
    store.delete_profile(user_id)
    return {"status": "deleted"}


@app.get("/api/bookings")
def bookings(user_id: str):
    return [agent.booking_view(b) for b in store.user_bookings(user_id)]


@app.get("/api/bookings/{bid}/ics")
def booking_ics(bid: str):
    b = store.get_booking(bid)
    if not b:
        raise HTTPException(404)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//ostrovok-trip-agent//RU"]
    for i, r in enumerate(b.get("reminders", [])):
        t = datetime.fromisoformat(r["at"])
        lines += ["BEGIN:VEVENT", f"UID:{bid}-{i}@trip-agent", f"DTSTAMP:{datetime.utcnow():%Y%m%dT%H%M%SZ}",
                  f"DTSTART:{t:%Y%m%dT%H%M00}", f"DTEND:{t + timedelta(minutes=30):%Y%m%dT%H%M00}",
                  f"SUMMARY:{r['text']}", f"DESCRIPTION:Бронь {bid}, {b['dest_name']}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return Response("\r\n".join(lines), media_type="text/calendar",
                    headers={"Content-Disposition": f'attachment; filename="{bid}.ics"'})


@app.get("/api/metrics")
def get_metrics():
    return {"live": metrics.aggregate(), "eval": metrics.aggregate(prefix="eval")}


@app.post("/api/eval/run")
def run_eval():
    from eval.run_eval import run_all
    return JSONResponse(run_all(verbose=False))
