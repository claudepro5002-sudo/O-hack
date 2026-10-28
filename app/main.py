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


@app.middleware("http")
async def no_stale_ui(request, call_next):
    """Интерфейс всегда сверяется с сервером (ETag), чтобы после обновления не показывался старый CSS/JS из кэша."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response

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
    label: str | None = None  # как показать действие (кнопку) в ленте чата


FEED_KINDS = ("plan", "cart", "destinations")


def feed_kind(m: dict) -> str:
    return next((b["type"] for b in m.get("blocks") or [] if b.get("type") in FEED_KINDS), "")


def record_feed(sid: str, user_text: str | None, out: dict, silent: bool) -> None:
    """Лента чата для переключения между чатами: то же, что видит пользователь.
    Тихая настройка (тариф, «что важнее») заменяет последнюю карточку того же вида, как и в интерфейсе."""
    feed = store.get_feed(sid)
    kind = feed_kind(out)
    if silent:
        if kind and out.get("text"):
            for i in range(len(feed) - 1, -1, -1):
                if feed[i]["role"] == "agent" and feed_kind(feed[i]["m"]) == kind:
                    feed[i] = {"role": "agent", "m": out}
                    break
    else:
        if user_text:
            feed.append({"role": "user", "text": user_text})
        feed.append({"role": "agent", "m": out})
    store.save_feed(sid, feed)


def own_session(sid: str, user_id: str) -> SessionState:
    s = _sessions.get(sid) or store.load_session(sid)
    if not s or s.user_id != user_id:
        raise HTTPException(404, "чат не найден")
    return s


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
    store.save_feed(s.id, [{"role": "agent", "m": w}])
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
        silent = bool(body.action and body.action.get("silent"))
        record_feed(s.id, ((body.label or body.message or "").strip()[:2000] or None), out, silent)
    return {"session_id": s.id, "user_id": s.user_id, "messages": [out], "summary": agent.summary(s), "trace": out.get("trace", [])}


@app.get("/api/session/{sid}")
def session_info(sid: str):
    s = get_session(sid, "anon")
    return {"session_id": s.id, "summary": agent.summary(s), "history": s.history[-50:], "stage": s.stage}


@app.get("/api/chats")
def chats(user_id: str, current: str | None = None):
    """Список чатов пользователя: пустые (без сообщений пользователя) не показываем, кроме текущего."""
    items = []
    for s, updated in store.user_sessions(user_id):
        if s.turns == 0 and s.id != current:
            continue
        items.append({"id": s.id, "title": agent.chat_title(s), "stage": agent.STAGE_LABELS.get(s.stage, ""),
                      "updated": updated})
    return items


@app.get("/api/session/{sid}/feed")
def session_feed(sid: str, user_id: str):
    s = own_session(sid, user_id)
    _sessions[s.id] = s
    feed = store.get_feed(s.id) or [  # чаты из старых версий: восстанавливаем хотя бы текст переписки
        {"role": "user", "text": h["text"]} if h["role"] == "user" else {"role": "agent", "m": {"text": h["text"]}}
        for h in s.history[-store.FEED_LIMIT:]]
    return {"session_id": s.id, "feed": feed, "summary": agent.summary(s)}


@app.delete("/api/session/{sid}")
def delete_chat(sid: str, user_id: str):
    own_session(sid, user_id)
    store.delete_session(sid)
    _sessions.pop(sid, None)
    return {"status": "deleted"}


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
