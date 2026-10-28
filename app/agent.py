"""Оркестратор агента: понимает сообщение, решает, что делать, вызывает инструменты
планировщика и формирует ответ (текст + структурированные блоки для UI).

Цикл одного хода:
  1. parse      — правила (nlu.py) + LLM (если подключена) → слоты и намерение
  2. update     — применяем изменения к запросу, фиксируем что поменялось
  3. decide     — уточнить / вдохновить / собрать план / пересобрать / корзина / оплата / пост-продажа
  4. act        — вызываем инструменты (поиск, сборка, проверка состыковок)
  5. explain    — объясняем выбор, компромиссы, риски; предлагаем следующие шаги
"""
from __future__ import annotations

import contextvars
import random
import re
import time
from datetime import date, datetime, timedelta

from . import dynamic, llm, nlu, planner, store
from . import inventory as inv
from .data.destinations import DEST_BY_ID, GATEWAYS, MONTH_LOC, MONTH_NAMES
from .data.geo import ORIGIN_BY_CODE, ORIGINS
from .planner import date_range_text, ddmm, deg, hours, rub
from .state import PREF_LABELS, PRIORITY_LABELS, SessionState, TripRequest, today

REPLAN_KEYS = {"date_from", "date_to", "month", "nights", "adults", "children_ages", "children_pending", "pets", "budget",
               "budget_level", "prefs", "meal", "free_cancel", "direct_only", "max_flight_h", "transport", "stars_min",
               "beach_close", "center_close", "priority", "flight_time_pref", "origin", "accessible", "visa_free"}
# «Тихий» ход: пользователь переключил настройку (приоритет, тариф, допы) — карточку
# обновляем на месте, без реплики в чате и без сообщения «я переключил».
SILENT: contextvars.ContextVar[bool] = contextvars.ContextVar("silent", default=False)

POSTSALE = {"early_checkin", "late_checkout", "reminders", "cancel_booking", "change_booking", "add_transfer"}


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    return one if n % 10 == 1 else few if 2 <= n % 10 <= 4 else many


def reply(text: str, blocks: list | None = None, chips: list | None = None) -> dict:
    return {"text": text, "blocks": blocks or [], "chips": chips or []}


def chip(label: str, message: str | None = None, action: dict | None = None) -> dict:
    c = {"label": label}
    if action:
        c["action"] = action
    else:
        c["message"] = message or label
    return c


class Turn:
    """Контекст одного хода: трассировка рассуждений и вызовов инструментов."""

    def __init__(self):
        self.trace: list[dict] = []
        self.notes: list[str] = []  # пояснения, которые нужно показать пользователю в начале ответа
        self.t0 = time.time()

    def think(self, text: str):
        self.trace.append({"kind": "think", "text": text})

    def tool(self, name: str, args: dict, result: str):
        self.trace.append({"kind": "tool", "tool": name, "args": args, "result": result})


# ====================================================================== память

def apply_memory(state: SessionState, profile: dict) -> list[str]:
    notes = []
    if not profile:
        return notes
    if profile.get("origin") and not state.req.origin:
        state.req.origin = profile["origin"]
        state.req.origin_source = "memory"
        notes.append(f"вылет из {ORIGIN_BY_CODE[profile['origin']].name_gen}")
    if profile.get("free_cancel"):
        state.req.free_cancel = True
        notes.append("предпочитаете тарифы с бесплатной отменой")
    if profile.get("priority") and profile["priority"] != "balance":
        state.req.priority = profile["priority"]
        notes.append(PRIORITY_LABELS[profile["priority"]].lower())
    state.memory_applied = notes
    return notes


def update_profile(state: SessionState, booking: dict | None = None) -> None:
    p = store.get_profile(state.user_id)
    r = state.req
    if r.origin and r.origin_source == "user":
        p["origin"] = r.origin
    if r.adults and "adults" not in r.assumptions:
        p["adults"] = r.adults
    if r.children_ages and "children" not in r.assumptions:
        p["children"] = [{"age": a, "year": today().year} for a in r.children_ages]
    if r.pets:
        p["pets"] = r.pets
    if r.free_cancel is not None:
        p["free_cancel"] = r.free_cancel
    if r.meal:
        p["meal"] = r.meal
    if r.priority != "balance":
        p["priority"] = r.priority
    if r.budget_level:
        p["budget_level"] = r.budget_level
    if booking:
        counts = p.get("prefs", {})
        for x in r.prefs:
            counts[x] = counts.get(x, 0) + 1
        p["prefs"] = counts
        trips = p.get("trips", [])
        trips.append({"dest_id": booking["dest_id"], "dest_name": booking["dest_name"], "dates": booking["dates_text"],
                      "booking_id": booking["id"]})
        p["trips"] = trips[-10:]
        p["travelers"] = booking["travelers"]
        p["email"] = booking.get("email")
    store.save_profile(state.user_id, p)


def memory_kids(profile: dict) -> list[int]:
    out = []
    for c in profile.get("children", []):
        out.append(c["age"] + (today().year - c.get("year", today().year)))
    return [a for a in out if a < 18]


def welcome(state: SessionState) -> dict:
    profile = store.get_profile(state.user_id)
    notes = apply_memory(state, profile)
    text = ("Привет! Я трип-агент. Опишите поездку своими словами — куда, когда, с кем и что важно. "
            "Я задам только нужные вопросы, подберу направление, отель, билеты и трансфер, проверю состыковки и доведу до оплаты.")
    if profile.get("trips") or notes:
        bits = []
        if profile.get("trips"):
            bits.append(f"в прошлый раз вы ездили {DEST_BY_ID[profile['trips'][-1]['dest_id']].name_acc}")
        bits += notes
        kids = memory_kids(profile)
        if kids:
            bits.append("с вами ребёнок " + ", ".join(f"{a} лет" for a in kids))
        text = "С возвращением! Помню: " + "; ".join(bits) + ".\n\n" + text
    chips = [
        chip("К тёплому морю в ноябре с ребёнком 5 лет, недорого, без долгих перелётов"),
        chip("Романтические выходные в городе с вкусной едой из Питера"),
        chip("Горы и природа в июле вдвоём, бюджет 150 тысяч"),
        chip("На новогодние в Стамбул на 5 ночей из Казани"),
        chip("Едем с собакой на море в сентябре, поездом"),
    ]
    steps = [("💬", "Пойму запрос", "явные и скрытые требования, 1 уточнение максимум"),
             ("🧭", "Предложу направления", "погода, цена под ключ, время в пути"),
             ("⚖️", "Соберу и сравню", "перелёт/поезд + отель + трансфер, 3 пакета"),
             ("🔗", "Проверю состыковки", "прилёт, заселение, трансфер, документы"),
             ("🛒", "Оформлю", "корзина и имитация оплаты"),
             ("🛎", "Сопровожу", "ранний заезд, перенос дат, напоминания")]
    return reply(text, [{"type": "intro", "steps": [{"icon": i, "title": t, "text": x} for i, t, x in steps]}], chips)


# ====================================================================== разбор

def mentioned(dest_id: str, text: str) -> bool:
    """Упомянуто ли направление каталога в тексте (защита от «Антарктида» → «Анталья» у LLM)."""
    d = DEST_BY_ID.get(dest_id)
    if d is None:
        return False
    t = nlu.norm(text)
    stems = [a.strip() for a in d.aliases if len(a.strip()) >= 3] + [nlu.norm(d.name)[:5]]
    return any(re.search(r"(?<![а-я])" + re.escape(st), t) for st in stems)


def merge_updates(rule: dict, llm_upd: dict | None, text: str = "") -> dict:
    if not llm_upd:
        return rule
    llm_upd = dict(llm_upd)
    dest = llm_upd.get("destination")
    rules_saw_place = bool(rule.get("destination") or rule.get("_unknown_place") or rule.get("country"))
    if dest and dest != rule.get("destination") and text and rules_saw_place and not mentioned(dest, text):
        # в сообщении есть другое место (например, «Антарктида»), а LLM подставила похожее из каталога («Анталья»)
        llm_upd.pop("destination")
    unk = llm_upd.get("_unknown_place")
    if isinstance(unk, str) and not rules_saw_place and any(re.search(rx, nlu.norm(unk)) for _, rx in nlu.PREF_PATTERNS):
        llm_upd.pop("_unknown_place")  # «кемпинг», «море» — это тип отдыха, а не место
    out = dict(rule)
    rule_dates = any(k in rule for k in ("date_from", "date_to"))
    for k, v in llm_upd.items():
        if rule_dates and k in ("date_from", "date_to", "month", "year", "nights"):
            continue  # явные даты правила разбирают детерминированно — LLM их не перебивает
        if k in ("prefs", "avoid"):
            out[k] = sorted(set(out.get(k, [])) | set(v))
        elif k == "_intent" and rule.get("_intent") in ("choose_book", "shift_dates", "extend") and v != rule.get("_intent"):
            continue
        else:
            out[k] = v
    if "children_ages" in llm_upd and "children_pending" not in llm_upd:
        out["children_pending"] = 0  # LLM назвала возрасты всех детей
    return out


def context_text(state: SessionState) -> str:
    """Состояние для LLM — только человеческие названия, без внутренних кодов."""
    r = state.req
    bits = []
    if r.destination and r.destination in DEST_BY_ID:
        bits.append(f"направление {DEST_BY_ID[r.destination].name}")
    if r.origin and r.origin in ORIGIN_BY_CODE:
        bits.append(f"вылет из {ORIGIN_BY_CODE[r.origin].name_gen}")
    if r.date_from:
        bits.append(f"даты {r.date_from}..{r.date_to or ''}")
    elif r.month:
        bits.append(f"месяц {r.month}")
    if r.adults:
        bits.append(f"взрослых {r.adults}, дети {r.children_ages}")
    if state.dest_options:
        bits.append("предложены направления: " + ", ".join(f"{i + 1}) {d['name']}" for i, d in enumerate(state.dest_options)))
    if state.awaiting:
        bits.append(f"агент ждёт: {state.awaiting}")
    return "; ".join(bits) or "ничего"


def apply_updates(state: SessionState, upd: dict, turn: Turn) -> set[str]:
    r = state.req
    changed: set[str] = set()
    old = r.model_dump()
    per_person = upd.pop("_budget_per_person", False)
    adults_assumed = upd.pop("_adults_assumed", False)
    upd.pop("_open_destination", None)
    kids_unknown = upd.pop("_kids_count_unknown", False)
    new_ages = upd.get("children_ages")
    if (state.awaiting == "children_ages" and r.children_ages and r.children_pending and new_ages
            and len(new_ages) <= r.children_pending and not upd.get("children_pending")):
        # спрашивали возраст только новых детей — дополняем, а не заменяем известных
        upd["children_ages"] = r.children_ages + new_ages
        upd["children_pending"] = r.children_pending - len(new_ages)
    gateway_note = upd.pop("_gateway_note", None)
    if gateway_note:
        turn.notes.append("🧭 " + gateway_note)
        upd.pop("_unknown_place", None)  # место известно через «ворота», искать его в LLM не нужно
    origin_place, origin_strong = upd.pop("_origin_place", None), upd.pop("_origin_strong", True)
    if origin_place and "origin" not in upd:
        code = dynamic.origin(origin_place) if llm.enabled() else None
        if code:
            turn.tool("llm_place_card", {"origin": origin_place}, f"город вылета: {ORIGIN_BY_CODE[code].name}")
            upd["origin"] = code
        elif origin_strong:
            names = ", ".join(o.name for o in ORIGINS)
            turn.notes.append(f"Город вылета «{origin_place.capitalize()}» я пока не знаю — умею считать вылет из: {names}. "
                              f"Напишите ближайший из них (например, «вылет из Екатеринбурга»).")
    unknown = upd.pop("_unknown_place", None)
    if isinstance(unknown, str):
        unknown = {"name": unknown, "prefs": []}
    if unknown and "destination" not in upd and llm.enabled():
        # LLM знает любое место: просим карточку места и превращаем её в направление
        query = unknown.get("query") or unknown["name"]
        llm.last_error = None
        res = dynamic.resolve(query)
        if res is None:
            reason = llm.last_error or "модель не смогла описать это место"
            turn.tool("llm_place_card", {"place": query}, f"не получилось: {reason}")
            unknown = {**unknown, "llm_failed": reason}
        if res and res["kind"] == "place":
            turn.tool("llm_place_card", {"place": query}, f"направление: {DEST_BY_ID[res['dest_id']].name}")
            upd["destination"] = res["dest_id"]
            unknown = None
        elif res and res["dest_ids"]:
            turn.tool("llm_place_card", {"place": query}, f"{res['kind']}: варианты — "
                      + ", ".join(DEST_BY_ID[i].name for i in res["dest_ids"]))
            state.unknown_place = {"name": res["label"], "note": res["note"], "kind": res["kind"], "prefs": []}
            r.candidates, r.similar_to, r.similar_label = res["dest_ids"], [], None
            r.destination, r.country = None, None
            unknown = None
    if unknown and "destination" not in upd:
        state.unknown_place = unknown
        upd["prefs"] = sorted(set(upd.get("prefs", [])) | set(unknown["prefs"]))
        r.similar_to, r.similar_label = list(unknown.get("similar", [])), unknown["name"]
        if r.destination:  # пользователь назвал новое место — прежнее направление больше не актуально
            r.destination, r.country, r.candidates = None, None, []
            changed.add("destination")
        turn.think(f"«{unknown['name']}» нет в каталоге — подберу похожее по духу: "
                   + ", ".join(PREF_LABELS.get(p, p) for p in unknown["prefs"]))
    elif "destination" in upd:
        state.unknown_place = None
        r.similar_to, r.similar_label, r.candidates = [], None, []
    if "country" in upd:
        r.candidates = []
    for k, v in list(upd.items()):
        if k.startswith("_") or k not in nlu.SLOT_KEYS:
            continue
        if k == "adults" and adults_assumed and r.adults:
            continue
        if k == "prefs":
            v = sorted(set(r.prefs) | set(v))
            if "mild" in v and "warm" in v:
                v.remove("warm")
        if k == "avoid":
            v = sorted(set(r.avoid) | set(v))
            if r.destination and (r.destination in v or DEST_BY_ID[r.destination].country in v):
                r.destination = None
                state.plan = None
                changed.add("destination")
        if k == "month" and "date_from" not in upd:
            r.date_from = r.date_to = None
        if k == "date_from" and "date_to" not in upd:
            r.date_to = None
        if k == "destination":
            r.country = None
        if k == "country" and "destination" not in upd:
            r.destination = None
        setattr(r, k, v)
    if r.destination is None and state.plan:
        state.plan, state.cart, state.pinned = None, None, {}
    if "children_ages" in upd or "children_pending" in upd:
        profile = store.get_profile(state.user_id)
        mem = memory_kids(profile)
        if r.children_pending and not r.children_ages and mem and (kids_unknown or r.children_pending == len(mem) or
                                                                    r.children_pending == 1 and len(mem) == 1):
            r.children_ages, r.children_pending = mem, 0
            turn.think(f"Возраст детей взял из памяти: {', '.join(map(str, mem))} лет")
            r.assumptions["children"] = "возраст из памяти"
    if "budget_level" in upd and r.budget_level == "low" and "priority" not in upd and r.priority == "balance":
        r.priority = "price"
    if per_person and r.budget:
        r.budget = r.budget * r.party_size
        turn.think(f"Бюджет указан на человека — пересчитал на всех: {rub(r.budget)}")
    if "origin" in upd:
        r.origin_source = "user"
    if "nights" in upd:
        r.nights_source = "user"
        if r.date_from and "date_to" not in upd:
            r.date_to = (date.fromisoformat(r.date_from) + timedelta(days=r.nights)).isoformat()
    if "date_from" in upd or "date_to" in upd:
        fix = normalize_dates(r)
        if fix:
            turn.think(fix)
    if r.date_from and r.date_to and ("date_from" in upd or "date_to" in upd):
        r.nights = (date.fromisoformat(r.date_to) - date.fromisoformat(r.date_from)).days
    new = r.model_dump()
    for k in new:
        if new[k] != old.get(k) and k not in ("assumptions", "origin_source", "nights_source", "year", "flexible"):
            changed.add(k)
    return changed


def _shift_year(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 29 февраля
        return d.replace(year=d.year + years, day=28)


def normalize_dates(r: TripRequest) -> str | None:
    """Чиним только явные ошибки: прошедшую дату начала (LLM ошиблась годом) и конец раньше начала.

    Длинные поездки (месяц, полгода, год) допустимы — длительность считается от реальных дат.
    """
    if not r.date_from:
        return None
    t = today()
    d1 = date.fromisoformat(r.date_from)
    d2 = date.fromisoformat(r.date_to) if r.date_to else None
    note = None
    if d1 < t:
        while d1 < t:
            d1 = _shift_year(d1, 1)
        if d2 and d2 < t:  # конец тоже в прошлом — сдвигаем вместе с началом
            while d2 <= d1:
                d2 = _shift_year(d2, 1)
        note = "Дата начала уже прошла — перенёс её на ближайший такой день в будущем"
    if d2 and d2 <= d1:  # «с 28 декабря по 5 января» — конец в следующем году
        while d2 <= d1:
            d2 = _shift_year(d2, 1)
    r.date_from, r.date_to = d1.isoformat(), (d2.isoformat() if d2 else None)
    r.month, r.year = d1.month, d1.year
    return note


def action_to_updates(action: dict) -> dict:
    t = action.get("type")
    if t == "set":
        key, val = action.get("key"), action.get("value")
        if key == "party":
            return {"adults": int(val.get("adults", 2)), "children_ages": [int(a) for a in val.get("children", [])],
                    "children_pending": 0}
        if key == "pets":
            return {"pets": val or None}
        if key in nlu.SLOT_KEYS:
            return {key: val}
        return {}
    mapping = {
        "choose_destination": lambda a: {"destination": a["dest_id"], "_intent": "choose_dest"},
        "choose_bundle": lambda a: {"_intent": "choose", "_choice_idx": a["idx"]},
        "book": lambda a: {"_intent": "book"},
        "checkout": lambda a: {"_intent": "book"},
        "pay": lambda a: {"_intent": "pay", "_pay": a},
        "shift": lambda a: {"_intent": "shift_dates", "_shift": int(a["days"])},
        "extend": lambda a: {"_intent": "extend", "_extend": int(a["nights"])},
        "cheaper": lambda a: {"_intent": "cheaper"},
        "pricier": lambda a: {"_intent": "pricier"},
        "show_hotels": lambda a: {"_intent": "show_hotels"},
        "show_flights": lambda a: {"_intent": "show_flights"},
        "show_destinations": lambda a: {"_intent": "show_destinations"},
        "why": lambda a: {"_intent": "why"},
        "reset": lambda a: {"_intent": "reset"},
        "skip": lambda a: {"_intent": "skip"},
        "swap_hotel": lambda a: {"_intent": "swap", "_pin": {"hotel_id": a["hotel_id"]}},
        "swap_out": lambda a: {"_intent": "swap", "_pin": {"out_id": a["id"]}},
        "swap_back": lambda a: {"_intent": "swap", "_pin": {"back_id": a["id"]}},
        "set_rate": lambda a: {"_intent": "set_rate", "_rate": a["code"]},
        "cart_transfer": lambda a: {"_intent": "cart_transfer", "_args": a},
        "toggle_addon": lambda a: {"_intent": "toggle_addon", "_args": a},
        "early_checkin": lambda a: {"_intent": "early_checkin"},
        "late_checkout": lambda a: {"_intent": "late_checkout"},
        "add_night_before": lambda a: {"_intent": "add_night_before"},
        "reminders": lambda a: {"_intent": "reminders"},
        "change_booking": lambda a: {"_intent": "change_booking", "_shift": a.get("days")},
        "confirm_change": lambda a: {"_intent": "confirm_change", "_shift": int(a["days"])},
        "cancel_booking": lambda a: {"_intent": "cancel_booking"},
        "confirm_cancel": lambda a: {"_intent": "confirm_cancel"},
        "add_transfer": lambda a: {"_intent": "add_transfer"},
        "booking_transfer": lambda a: {"_intent": "booking_transfer", "_args": a},
        "back_to_plan": lambda a: {"_intent": "back_to_plan"},
        "apply_form": lambda a: {"_intent": "apply_form", "_form": a.get("values") or {}},
        "info": lambda a: {"_intent": "info"},
    }
    fn = mapping.get(t)
    return fn(action) if fn else {}


# ====================================================================== главный ход

# Новый запрос посреди старой поездки («а теперь хочу в поход») — подбираем заново, а не правим прежний план
TRIP_TYPE_PREFS = {"sea", "warm", "mild", "nature", "mountains", "ski", "city", "culture", "party", "northern_lights",
                   "surf", "diving", "wellness", "kids_fun", "adventure", "shopping", "food", "romantic"}
PREF_KIND = {"sea": "beach", "warm": "beach", "nature": "nature", "mountains": "nature", "adventure": "nature",
             "ski": "ski", "city": "city", "culture": "city", "shopping": "city", "food": "city"}
NEW_TRIP_STRONG = re.compile(
    r"составь|спланируй|подбери|организуй|придумай|предложи|поездк|путешеств|отпуск|а теперь|теперь хочу|вместо|"
    r"лучше|передумал|друг(ое|ую|ой|ие)|нов(ую|ое|ый|ые)|куда-нибудь|куда нибудь")
NEW_TRIP_WEAK = re.compile(r"хочу|хотим|хотелось|давай|поехать|поедем|съездить|махнуть|отправиться|рвануть|"
                           r"(?<![а-я])(в|на)\s+(поход|кемпинг|глэмпинг|природ|море|горы|пляж|рыбалк|сафари|сплав)")
GATEWAY_IDS = {g[1] for g in GATEWAYS}  # Ушуая, Мурманск — «ворота» к круизам, а не место для любого отдыха
CAMPING = re.compile(r"кемпинг|глэмпинг|палатк|поход|костр|сплав")


def new_trip_prefs(state: SessionState, upd: dict, intent: str | None, message: str | None) -> list[str] | None:
    """Если сообщение — новый запрос другой поездки, возвращает её тип (prefs), иначе None."""
    r = state.req
    if not message or intent or state.awaiting or state.stage in ("start", "checkout"):
        return None
    if not (r.destination or r.country or r.candidates or state.unknown_place):
        return None  # прежней поездки ещё нет — нечего сбрасывать
    if any(k in upd for k in ("country", "_unknown_place", "_gateway_note")):
        return None  # названо новое место — это обычная смена направления
    if upd.get("destination") and (upd["destination"] != r.destination or mentioned(upd["destination"], message)):
        return None
    if r.destination and mentioned(r.destination, message):
        return None  # говорят о текущем направлении
    kinds = set(upd.get("prefs", [])) & TRIP_TYPE_PREFS
    if not kinds or kinds <= set(r.prefs):
        return None
    t = nlu.norm(message)
    if NEW_TRIP_STRONG.search(t) or upd.get("_open_destination"):
        return sorted(kinds)
    if NEW_TRIP_WEAK.search(t) and r.destination:
        d = DEST_BY_ID.get(r.destination)
        fits = d is not None and r.destination not in GATEWAY_IDS and all(
            p in d.tags or PREF_KIND.get(p) in d.kinds for p in kinds)
        return None if fits else sorted(kinds)
    return None


def start_new_trip(state: SessionState, upd: dict, kinds: list[str], message: str, turn: Turn) -> None:
    """Сбрасываем прежнее направление и план; город вылета, состав, даты и бюджет оставляем."""
    r = state.req
    old = DEST_BY_ID[r.destination].name if r.destination and r.destination in DEST_BY_ID else (r.country or "")
    if upd.get("destination") == r.destination:
        upd.pop("destination")  # LLM повторила прежнее направление из контекста
    r.destination, r.country, r.candidates, r.similar_to, r.similar_label = None, None, [], [], None
    r.prefs = [p for p in r.prefs if p not in TRIP_TYPE_PREFS]  # прежний тип отдыха больше не нужен
    state.unknown_place = None
    state.plan, state.cart, state.pinned, state.selected, state.dest_options = None, None, {}, None, []
    label = ", ".join(PREF_LABELS.get(p, p) for p in kinds)
    if CAMPING.search(nlu.norm(message)):
        label = "кемпинг и походы на природе"
    turn.think(f"Новый запрос ({label}) — начинаю подбор заново" + (f", «{old}» больше не предлагаю" if old else ""))
    turn.notes.append(f"Новая поездка — {label}: подбираю направления заново, город вылета, состав и даты сохранил.")


def handle(state: SessionState, message: str | None = None, action: dict | None = None) -> dict:
    turn = Turn()
    state.turns += 1
    if message:
        state.history.append({"role": "user", "text": message, "ts": time.time()})
        if state.first_user_at is None:
            state.first_user_at = time.time()
            if not state.memory_applied:
                apply_memory(state, store.get_profile(state.user_id))
    elif action and state.first_user_at is None:
        state.first_user_at = time.time()

    silent_token = SILENT.set(bool(action and action.get("silent")))
    if action:
        upd = action_to_updates(action)
        turn.think(f"Действие из интерфейса: {action.get('type')}")
    else:
        upd = nlu.parse(message or "", state.stage, state.awaiting)
        llm_upd = llm.extract(message or "", state.stage, context_text(state), today().isoformat()) if message else None
        if llm_upd is not None:
            turn.tool("llm_extract", {"model": "LLM"}, f"{len(llm_upd)} полей")
        upd = merge_updates(upd, llm_upd, message or "")
        found = {k: v for k, v in upd.items() if not k.startswith("_")}
        if found:
            turn.tool("parse_request", {"text": (message or "")[:80]}, describe_updates(found))
        if upd.get("_intent"):
            turn.think(f"Намерение: {upd['_intent']}")

    intent = upd.pop("_intent", None)
    if not action:
        kinds = new_trip_prefs(state, upd, intent, message)
        if kinds:
            start_new_trip(state, upd, kinds, message or "", turn)
    try:
        out = route(state, upd, intent, message, turn)
    except Exception as e:  # noqa: BLE001 — агент не должен падать на ходе пользователя
        import logging
        logging.getLogger("agent").exception("turn failed")
        out = reply(f"Что-то пошло не так при обработке ({type(e).__name__}). Попробуйте переформулировать — я сохранил все условия.",
                    chips=[chip("Начать заново", action={"type": "reset"})])
    if turn.notes and out.get("text"):
        out["text"] = "\n".join(turn.notes) + "\n\n" + out["text"]
    out["text"] = hide_codes(out.get("text", ""))
    state.trace = turn.trace
    out["trace"] = turn.trace
    out["elapsed_ms"] = int((time.time() - turn.t0) * 1000)
    out["silent"] = SILENT.get()
    if not out["silent"]:
        state.history.append({"role": "agent", "text": out["text"], "ts": time.time()})
    SILENT.reset(silent_token)
    return out


def hide_codes(text: str) -> str:
    """Подстраховка: если в текст (например, от LLM) попал служебный код места — меняем его на название."""
    import re as _re

    def repl(m):
        code = m.group(0)
        if code.startswith("o-"):
            o = ORIGIN_BY_CODE.get(code)
            return o.name if o else code
        d = DEST_BY_ID.get(code)
        return d.name if d else code
    return _re.sub(r"\b[xo]-[0-9a-f]{10}\b", repl, text)


def describe_updates(u: dict) -> str:
    bits = []
    for k, v in u.items():
        if k == "destination":
            bits.append(f"куда: {DEST_BY_ID[v].name}")
        elif k == "origin":
            bits.append(f"откуда: {ORIGIN_BY_CODE[v].name}")
        elif k == "prefs":
            bits.append("интересы: " + ", ".join(PREF_LABELS.get(p, p) for p in v))
        elif k == "budget":
            bits.append(f"бюджет: {rub(v)}")
        elif k in ("children_ages",) and v:
            bits.append("дети: " + ", ".join(f"{a} л." for a in v))
        elif k == "month":
            bits.append(f"месяц: {MONTH_NAMES[v - 1]}")
        elif k == "max_flight_h":
            bits.append(f"в пути ≤ {v:g} ч")
        elif k not in ("year", "flexible", "children_pending"):
            bits.append(f"{k}: {v}")
    return "; ".join(bits)


def route(state: SessionState, upd: dict, intent: str | None, message: str | None, turn: Turn) -> dict:
    # --- сброс
    if intent == "apply_form":
        return do_apply_form(state, upd.get("_form", {}), turn)
    if intent == "reset":
        keep_user = state.user_id
        fresh = SessionState(id=state.id, user_id=keep_user, history=state.history, booking_ids=state.booking_ids,
                             started_at=state.started_at, first_user_at=state.first_user_at, first_reco_at=state.first_reco_at,
                             turns=state.turns, clarifications=state.clarifications, replans=state.replans)
        state.__dict__.update(fresh.__dict__)
        turn.think("Начинаю новую поездку, память о вас сохраняю")
        w = welcome(state)
        w["text"] = "Хорошо, начинаем новую поездку. " + w["text"].split("\n\n")[-1]
        return w

    # --- ожидание имён путешественников (чат без формы)
    if state.awaiting == "travelers" and message and intent not in ("why", "info", "reset", "cheaper", "shift_dates"):
        names = nlu.names_from_text(message)
        if names:
            return do_pay(state, {"travelers": [{"name": n} for n in names]}, turn)

    # --- пост-продажа
    if state.stage == "booked":
        new_trip = any(k in upd for k in ("destination", "country", "month", "date_from", "prefs"))
        if intent in POSTSALE or intent in ("confirm_change", "confirm_cancel", "booking_transfer", "add_night_before"):
            return postsale(state, intent, upd, turn)
        if new_trip and intent not in ("why", "info"):
            turn.think("Похоже на новую поездку — начинаю новый план, бронь остаётся в «Моих поездках»")
            prev_origin = state.req.origin
            state.req = TripRequest(origin=prev_origin, origin_source="memory" if prev_origin else None)
            state.stage, state.plan, state.cart, state.dest_options, state.asked = "start", None, None, [], []
        elif intent not in ("why", "info"):
            return postsale(state, "menu", upd, turn)

    changed = apply_updates(state, upd, turn)
    if changed:
        turn.think("Изменилось: " + ", ".join(sorted(changed)))
    if SILENT.get() and intent is None:
        # переключили настройку: пересобираем видимую карточку, если она есть, иначе только запоминаем
        if not changed:
            return reply("")
        if state.stage == "bundles" and state.plan and state.req.destination:
            if "priority" in changed:
                # новый приоритет — подбираем заново, не держась за прежний отель и рейсы
                for k in ("hotel_id", "out_id", "back_id", "rate"):
                    state.pinned.pop(k, None)
                return do_plan(state, turn, replan=True, sticky=False)
            return do_plan(state, turn, replan=True)
        if state.stage == "destinations" and state.dest_options:
            return do_destinations(state, turn)
        turn.think("Настройку запомнил — применю при следующем подборе")
        return reply("")

    if intent == "greet" and not changed:
        return welcome(state)
    if intent == "why":
        return do_why(state, turn)
    if intent == "info":
        return do_info(state, message or "", turn)
    if intent == "show_hotels" and state.plan:
        return show_hotels(state)
    if intent == "show_flights" and state.plan:
        return show_flights(state)
    if intent == "show_destinations":
        state.req.destination = None
        state.plan, state.pinned, state.cart = None, {}, None
        return do_destinations(state, turn, reason="Показываю другие направления под ваши условия.")
    if intent == "choose_dest" or (intent in ("choose", "choose_book") and state.stage == "destinations" and "_choice_idx" in upd):
        if intent != "choose_dest":
            idx = upd["_choice_idx"]
            if not state.dest_options or idx >= len(state.dest_options):
                return reply("Не нашёл такой вариант — выберите из списка выше.")
            state.req.destination = state.dest_options[idx]["dest_id"]
        turn.think(f"Выбрано направление: {DEST_BY_ID[state.req.destination].name}")
        return do_plan(state, turn)
    if intent in ("choose", "choose_book") and state.stage in ("bundles", "cart", "checkout") and state.plan:
        idx = upd.get("_choice_idx", 0)
        if upd.get("_choice_code"):
            codes = [b["code"] for b in state.plan["bundles"]]
            idx = codes.index(upd["_choice_code"]) if upd["_choice_code"] in codes else 0
        if idx == -1:
            idx = len(state.plan["bundles"]) - 1
        if idx >= len(state.plan["bundles"]):
            return reply(f"Вариантов всего {len(state.plan['bundles'])} — выберите один из них.")
        return do_cart(state, idx, turn)
    if intent == "swap" and state.plan:
        pin = upd.get("_pin", {})
        state.pinned.update(pin)
        turn.think("Пользователь сам выбрал компонент — фиксирую его и пересобираю остальное вокруг")
        return do_plan(state, turn, replan=True, reason="Заменил компонент, остальное пересобрал вокруг него.")
    if intent == "set_rate" and state.cart:
        state.pinned["rate"] = upd["_rate"]
        state.pinned["hotel_id"] = state.cart["bundle"]["hotel"]["id"]
        return rebuild_cart(state, turn, "Сменил тариф отеля.")
    if intent == "cart_transfer" and state.cart:
        return cart_transfer(state, upd["_args"], turn)
    if intent == "toggle_addon" and state.cart:
        return toggle_addon(state, upd["_args"]["addon"], turn)
    if intent in ("early_checkin", "late_checkout") and state.cart:
        return toggle_addon(state, intent, turn)
    if intent == "add_night_before" and state.cart:
        return toggle_addon(state, "night_before", turn)
    if intent == "add_insurance" and state.cart:
        return toggle_addon(state, "insurance", turn)
    if intent == "back_to_plan" and state.plan:
        state.stage = "bundles"
        return present_plan(state, turn, None)
    if intent in ("no_transfer", "with_transfer") and state.plan:
        state.pinned["no_transfer"] = intent == "no_transfer"
        turn.think("Трансфер " + ("убираю — доберётесь сами" if intent == "no_transfer" else "возвращаю в план"))
        if state.stage == "cart" and state.cart:
            return rebuild_cart(state, turn, "Убрал трансфер." if intent == "no_transfer" else "Добавил трансфер.")
        return do_plan(state, turn, replan=True, reason="Без трансфера — доберётесь сами." if intent == "no_transfer" else "Вернул трансфер в план.")
    if intent == "book" and "destination" in changed:
        intent = None
    if intent == "book":
        if state.stage == "bundles" and state.plan:
            idx = state.plan.get("selected") or 0
            return do_cart(state, idx, turn)
        if state.stage == "cart":
            return do_checkout(state, turn)
        if state.stage == "checkout":
            return do_pay(state, upd.get("_pay"), turn)
        if state.stage == "destinations" and state.dest_options:
            state.req.destination = state.dest_options[0]["dest_id"]
            return do_plan(state, turn)
    if intent == "pay":
        if state.stage in ("checkout", "cart") and state.cart:
            return do_pay(state, upd.get("_pay"), turn)
        if state.stage == "bundles" and state.plan:
            return do_cart(state, state.plan.get("selected") or 0, turn)
    if intent == "cheaper":
        return do_cheaper(state, turn)
    if intent == "pricier":
        return do_pricier(state, turn)
    if intent == "shift_dates":
        return do_shift(state, upd.get("_shift", 1), turn)
    if intent == "extend":
        return do_extend(state, upd.get("_extend", 1), turn)
    if intent == "skip":
        for s in ("when", "children_ages", "party", "origin"):
            if s not in state.asked:
                state.asked.append(s)
        turn.think("Пользователь доверил выбор мне — заполняю пробелы разумными значениями")

    # --- основной поток: уточнить → направления → план
    missing = missing_slots(state)
    if missing:
        return ask(state, missing, turn)
    fill_defaults(state, turn)

    if state.req.destination:
        if state.plan and state.plan.get("dest_id") == state.req.destination and not (changed & REPLAN_KEYS) and state.stage != "start":
            if state.stage == "cart" and state.cart:
                return present_cart(state, "Корзина без изменений. Готовы оформить?")
            return present_plan(state, turn, None, intro="План прежний. Что поменять?")
        replan = state.plan is not None
        what = describe_changes(changed, state.req)
        reason = None
        if replan:
            keep = ("Собрал поездку под новое направление, остальные условия сохранил." if "destination" in changed else
                    "Пересобрал план, а не искал с нуля — направление и ваш выбор сохранил, где это возможно.")
            reason = (f"Учёл: {what}. " if what else "") + keep
        return do_plan(state, turn, replan=replan, reason=reason)
    if changed or state.stage in ("start", "clarify") or not state.dest_options:
        if state.stage in ("start", "clarify") and not changed and not intent:
            return fallback(state, message, turn)
        what = describe_changes(changed, state.req)
        return do_destinations(state, turn, reason=(f"Учёл: {what}. Перестроил подборку." if state.dest_options and changed and what else None))
    return fallback(state, message, turn)


def describe_changes(changed: set[str], r: TripRequest) -> str:
    bits = []
    if "destination" in changed and r.destination:
        bits.append(f"направление — {DEST_BY_ID[r.destination].name}")
    if "origin" in changed and r.origin:
        bits.append(f"вылет из {ORIGIN_BY_CODE[r.origin].name_gen}")
    if changed & {"date_from", "date_to", "month"}:
        bits.append("новые даты")
    if "nights" in changed and r.nights:
        bits.append(f"{r.nights} {plural(r.nights, 'ночь', 'ночи', 'ночей')}")
    if changed & {"adults", "children_ages", "children_pending"}:
        bits.append(f"состав — {r.adults or 2} взрослых" + (" и дети " + ", ".join(map(str, r.children_ages)) + " лет" if r.children_ages else ""))
    if "pets" in changed:
        bits.append("едет питомец — только отели и рейсы, где можно с животными" if r.pets else "без питомца")
    if "budget" in changed and r.budget:
        bits.append(f"бюджет до {rub(r.budget)}")
    if "meal" in changed and r.meal:
        bits.append(f"питание — {inv.MEAL_NAMES[r.meal]}")
    if "direct_only" in changed and r.direct_only:
        bits.append("только прямые рейсы")
    if "flight_time_pref" in changed and r.flight_time_pref:
        bits.append({"morning": "утренний вылет", "day": "дневной вылет", "evening": "вечерний вылет"}[r.flight_time_pref])
    if "free_cancel" in changed and r.free_cancel:
        bits.append("бесплатная отмена")
    if "stars_min" in changed and r.stars_min:
        bits.append(f"отели от {r.stars_min}★")
    if "priority" in changed:
        bits.append(PRIORITY_LABELS[r.priority].lower())
    if "transport" in changed:
        bits.append({"train": "поездом", "plane": "самолётом"}.get(r.transport, "любой транспорт"))
    if "max_flight_h" in changed and r.max_flight_h:
        bits.append(f"в пути до {r.max_flight_h:g} ч")
    if "prefs" in changed:
        bits.append("интересы: " + ", ".join(PREF_LABELS.get(p, p) for p in r.prefs if p in PREF_LABELS))
    return "; ".join(bits)


def fallback(state: SessionState, message: str | None, turn: Turn) -> dict:
    if message and llm.enabled():
        facts = context_text(state)
        ans = llm.answer(message, f"Состояние диалога: {facts}. Этап: {state.stage}.")
        if ans:
            turn.tool("llm_answer", {}, "ответ на свободный вопрос")
            return reply(ans, chips=stage_chips(state))
    hints = {
        "start": "Не совсем понял запрос. Расскажите, куда или какой отдых хотите, когда и с кем — например: «на море в ноябре вдвоём».",
        "destinations": "Выберите направление из списка (можно написать «второе» или название), или уточните условия.",
        "bundles": "Можно выбрать вариант («беру оптимальный»), поменять условия («на 2 дня позже», «бюджет меньше», «с нами собака») "
                   "или спросить «почему этот отель?».",
        "cart": "Проверьте корзину и нажмите «Оформить», или скажите, что поменять.",
        "checkout": "Заполните данные путешественников и нажмите «Оплатить».",
    }
    return reply(hints.get(state.stage, hints["start"]), chips=stage_chips(state))


def stage_chips(state: SessionState) -> list[dict]:
    if state.stage == "bundles":
        return [chip("Беру оптимальный", action={"type": "choose_bundle", "idx": 0}), chip("Подешевле", action={"type": "cheaper"}),
                chip("На 2 дня позже", action={"type": "shift", "days": 2}), chip("Почему так?", action={"type": "why"})]
    if state.stage == "destinations":
        return [chip(f"{d['flag']} {d['name']}", action={"type": "choose_destination", "dest_id": d["dest_id"]})
                for d in state.dest_options[:4]]
    if state.stage == "cart":
        return [chip("Оформить", action={"type": "checkout"}), chip("Вернуться к вариантам", action={"type": "back_to_plan"})]
    return []


# ====================================================================== уточнения

def missing_slots(state: SessionState) -> list[str]:
    r = state.req
    missing = []
    if not r.has_when() and "when" not in state.asked:
        missing.append("when")
    if r.origin is None and r.destination == "moscow" and "origin" not in state.asked:
        missing.append("origin")  # по умолчанию считали бы вылет из Москвы — тут так нельзя
    if r.children_pending and "children_ages" not in state.asked:
        missing.append("children_ages")
    if not missing:
        return []
    # раз уж спрашиваем — заодно уточним то, что сильно влияет на цену
    if r.adults is None and not r.has_kids and "party" not in state.asked:
        missing.append("party")
    if r.origin is None and "origin" not in state.asked and "origin" not in missing:
        missing.append("origin")
    return missing


def ask(state: SessionState, missing: list[str], turn: Turn) -> dict:
    r = state.req
    state.stage = "clarify"
    state.clarifications += 1
    for m in missing:
        if m not in state.asked:
            state.asked.append(m)
    store.log_event(state.id, state.user_id, "question", {"slots": missing})
    understood = understood_line(state)
    qs, chips_ = [], []
    if "when" in missing:
        qs.append("**Когда** хотите поехать и примерно на сколько? Можно месяц — подберу самые выгодные даты.")
        t = today()
        m1, m2 = t.month % 12 + 1, (t.month + 1) % 12 + 1
        chips_ += [chip(f"В {MONTH_LOC[m1 - 1]}, на неделю"), chip(f"В {MONTH_LOC[m2 - 1]}, на неделю"),
                   chip("На новогодние"), chip("На ближайшие выходные")]
    if "children_ages" in missing:
        n = r.children_pending
        qs.append(f"Сколько лет {'ребёнку' if n == 1 else 'детям'}? От возраста зависят цена билетов, детские клубы и то, "
                  f"какие отели вообще подходят.")
        state.awaiting = "children_ages"
        if n == 1:
            chips_ += [chip("Ребёнку 2 года"), chip("Ребёнку 5 лет"), chip("Ребёнку 10 лет")]
        else:
            chips_ += [chip("Детям 4 и 8 лет"), chip("Детям 7 и 12 лет")]
    if "party" in missing:
        qs.append("Сколько вас едет?")
        profile = store.get_profile(state.user_id)
        if profile.get("adults"):
            kids = memory_kids(profile)
            label = f"Как в прошлый раз: {profile['adults']} взр." + (f" + дети {', '.join(map(str, kids))} л." if kids else "")
            chips_.append(chip(label, action={"type": "set", "key": "party", "value": {"adults": profile["adults"], "children": kids}}))
        chips_ += [chip("Вдвоём"), chip("Один/одна"), chip("Семьёй с ребёнком")]
    if "origin" in missing:
        qs.append("Откуда едете?" if r.destination == "moscow" else "Откуда стартуете? (по умолчанию — Москва)")
        if r.destination == "moscow":
            chips_ += [chip("Из Петербурга"), chip("Из Казани"), chip("Из Екатеринбурга")]
    chips_.append(chip("Неважно, подбери сам", action={"type": "skip"}))
    turn.think("Без этого нельзя посчитать цены и погоду — спрашиваю одним сообщением: " + ", ".join(missing))
    text = (understood + "\n\n" if understood else "") + "Чтобы подобрать точно, уточню:\n" + "\n".join(f"• {q}" for q in qs)
    if state.unknown_place:
        text = unknown_note(state.unknown_place) + "\n\n" + text
    return reply(text, [], chips_)


def unknown_note(place: dict) -> str:
    if place.get("llm_failed"):
        busy = re.search(r"HTTP (429|5\d\d)|не ответила|нет связи", str(place["llm_failed"]))
        why = "модель сейчас перегружена или не ответила" if busy else "модель ответила неполными сведениями"
        return (f"⚠️ Не получилось узнать у LLM подробности про «{place.get('query') or place['name']}» — {why}. "
                f"Напишите это место ещё раз через минуту, а пока подберу похожее из проверенного каталога.")
    if not llm.enabled() and not place.get("kind"):
        return (f"❗ «{place['name']}» без подключённой LLM я не знаю — сейчас агент работает только по каталогу из 33 "
                f"направлений. Чтобы он понимал любое место мира, укажите LLM_API_KEY, LLM_BASE_URL и LLM_MODEL в .env. "
                f"Пока подберу ближайшие по смыслу варианты.")
    if place.get("kind") == "region":
        return f"🧭 {place['name']}: {place['note'] or 'подобрал конкретные места, куда можно поехать'}"
    if place.get("kind") == "unreachable":
        note = (place["note"] or "туда не продаются обычные туры").rstrip(". ")
        return f"❗ {place['name']}: {note}. Подобрал, что реально забронировать."
    like = ", ".join(PREF_LABELS.get(p, p) for p in place["prefs"] if p in PREF_LABELS)
    return (f"❗ Направления «{place['name']}» в нашем каталоге пока нет, поэтому забронировать туда поездку не смогу. "
            + (f"Подберу то, что ближе всего по духу: {like}." if like else "Подберу ближайшие по смыслу варианты."))


def understood_line(state: SessionState) -> str:
    r = state.req
    bits = []
    if r.destination:
        bits.append(DEST_BY_ID[r.destination].name)
    elif r.country:
        bits.append(r.country)
    if r.origin and r.origin_source == "user" and r.origin in ORIGIN_BY_CODE:
        bits.append(f"вылет из {ORIGIN_BY_CODE[r.origin].name_gen}")
    if r.prefs:
        bits.append(", ".join(PREF_LABELS.get(p, p) for p in r.prefs if p in PREF_LABELS))
    if r.month:
        bits.append(MONTH_LOC[r.month - 1].join(["в ", ""]))
    if r.adults:
        bits.append(f"{r.adults} взр.")
    if r.has_kids:
        bits.append("с детьми" if (len(r.children_ages) + r.children_pending) > 1 else "с ребёнком")
    if r.pets:
        bits.append("с питомцем")
    if r.budget:
        bits.append(f"до {rub(r.budget)}")
    elif r.budget_level == "low":
        bits.append("недорого")
    if r.max_flight_h:
        bits.append(f"в пути до {r.max_flight_h:g} ч")
    return ("Понял: " + "; ".join(bits) + ".") if bits else ""


def fill_defaults(state: SessionState, turn: Turn) -> None:
    r = state.req
    state.awaiting = None if state.awaiting == "children_ages" else state.awaiting
    if not r.origin:
        r.origin, r.origin_source = "MOW", "assumed"
        r.assumptions["origin"] = "Москва — по умолчанию"
        turn.think("Город вылета не указан — считаю, что из Москвы (можно поменять)")
    if r.adults is None:
        r.adults = 2
        r.assumptions["adults"] = "2 взрослых — по умолчанию"
        turn.think("Состав не указан — считаю на двоих взрослых")
    if r.children_pending:
        r.children_ages += [7] * r.children_pending
        r.children_pending = 0
        r.assumptions["children"] = "возраст детей не указан — считал 7 лет"
    if not r.has_when():
        m = today().month % 12 + 1
        r.month = m
        r.assumptions["when"] = f"{MONTH_NAMES[m - 1]} — ближайший удобный месяц"
        turn.think(f"Даты не указаны — беру ближайший удобный месяц: {MONTH_NAMES[m - 1]}")
    if not r.nights and not r.date_to:
        dest = DEST_BY_ID.get(r.destination) if r.destination else None
        n = planner.default_nights(r, dest)
        r.assumptions["nights"] = f"{n} {plural(n, 'ночь', 'ночи', 'ночей')} — типично для такого отдыха"


# ====================================================================== вдохновение

def do_destinations(state: SessionState, turn: Turn, reason: str | None = None) -> dict:
    r = state.req
    profile = store.get_profile(state.user_id)
    visited = [t["dest_id"] for t in profile.get("trips", [])]
    t0 = time.time()
    extra = []
    if llm.enabled() and not r.candidates and not r.similar_to:
        extra, names = dynamic.suggest(request_summary(r), ORIGIN_BY_CODE[r.origin or "MOW"].name)
        if names:
            turn.tool("llm_suggest_places", {"запрос": request_summary(r)[:80]}, "идеи ИИ: " + ", ".join(names))
    opts, excluded = planner.rank_destinations(r, visited, extra=extra)
    turn.tool("search_destinations", {"month": r.month or (r.date_from or "")[:7], "prefs": r.prefs, "origin": r.origin,
                                      "max_flight_h": r.max_flight_h, "budget": r.budget},
              f"{len(opts)} направлений, исключено {len(excluded)} ({int((time.time() - t0) * 1000)} мс)")
    if not opts:
        state.stage = "clarify"
        return reply("Под все условия сразу ничего не нашлось: " + "; ".join(f"{e['name']} — {e['reason']}" for e in excluded[:4]) +
                     ". Что можно ослабить?",
                     chips=[chip("Можно лететь дольше", action={"type": "set", "key": "max_flight_h", "value": None}),
                            chip("Бюджет можно больше", action={"type": "pricier"}),
                            chip("Другой месяц")])
    best = opts[0]["score"]
    opts = [o for i, o in enumerate(opts) if i < 2 or o["score"] >= best - 3.0]
    state.dest_options = opts
    state.excluded = excluded
    state.stage = "destinations"
    mark_reco(state)
    for o in opts:
        turn.tool("get_weather", {"dest": o["name"], "month": o["dates"]["text"]},
                  deg(o['weather']['air']) + (f", море +{o['weather']['sea']}°" if o["weather"]["sea"] is not None else ""))
    lead = opts[0]
    if state.unknown_place:
        reason = unknown_note(state.unknown_place) + (" " + reason if reason else "")
        state.unknown_place = None
    text = (reason + "\n\n" if reason else "") + (understood_line(state) + "\n\n" if understood_line(state) else "")
    text += (f"Подобрал {len(opts)} {'направления' if len(opts) < 5 else 'направлений'} — от лучшего совпадения. "
             "Цена «под ключ»: дорога, отель и трансфер на всех, даты — самые выгодные в выбранном периоде.") \
        if len(opts) > 1 else "Нашёл подходящее направление."
    text += f"\n\nМой фаворит — **{lead['flag']} {lead['name']}**: " + ", ".join(lead["why"][:3]) + "."
    if excluded:
        text += "\n\nНе стал предлагать: " + "; ".join(f"{e['name']} ({e['reason']})" for e in excluded[:3]) + "."
    blocks = [{"type": "destinations", "items": opts, "excluded": excluded[:6]}]
    chips_ = [chip(f"Выбрать {o['name']}", action={"type": "choose_destination", "dest_id": o["dest_id"]}) for o in opts[:3]]
    chips_.append(chip("Почему такой выбор?", action={"type": "why"}))
    store.log_event(state.id, state.user_id, "destinations", {"n": len(opts)})
    return reply(text, blocks, chips_)


def request_summary(r: TripRequest) -> str:
    """Запрос человеческим языком — для идей направлений от LLM."""
    bits = []
    if r.date_from:
        d1 = date.fromisoformat(r.date_from)
        bits.append("даты " + (date_range_text(d1, date.fromisoformat(r.date_to)) if r.date_to else f"с {ddmm(d1)} {d1.year}"))
    elif r.month:
        bits.append(f"в {MONTH_LOC[r.month - 1]} {r.year or ''}".strip())
    if r.nights:
        bits.append(f"{r.nights} {plural(r.nights, 'ночь', 'ночи', 'ночей')}")
    bits.append(f"{r.adults or 2} взрослых" + (f", дети {', '.join(map(str, r.children_ages))} лет" if r.children_ages else ""))
    if r.prefs:
        bits.append("хотят: " + ", ".join(PREF_LABELS.get(p, p) for p in r.prefs if p in PREF_LABELS))
    if r.budget:
        bits.append(f"бюджет до {rub(r.budget)} на всех")
    elif r.budget_level == "low":
        bits.append("недорого")
    elif r.budget_level == "high":
        bits.append("бюджет не ограничен")
    if r.max_flight_h:
        bits.append(f"в пути не больше {r.max_flight_h:g} ч")
    if r.pets:
        bits.append("едут с питомцем")
    if r.visa_free:
        bits.append("без визы")
    if r.transport == "train":
        bits.append("только поездом")
    if r.country:
        bits.append(f"страна: {r.country}")
    if r.avoid:
        bits.append("кроме: " + ", ".join(r.avoid))
    return "; ".join(bits)


def mark_reco(state: SessionState) -> None:
    if state.first_reco_at is None:
        state.first_reco_at = time.time()
        store.log_event(state.id, state.user_id, "first_reco",
                        {"ms": int((state.first_reco_at - (state.first_user_at or state.started_at)) * 1000)})


# ====================================================================== план

def do_plan(state: SessionState, turn: Turn, replan: bool = False, reason: str | None = None, sticky: bool = True) -> dict:
    r = state.req
    dest = DEST_BY_ID[r.destination]
    missing = missing_slots(state)
    if missing:
        return ask(state, missing, turn)
    fill_defaults(state, turn)
    # проверка жёстких ограничений направления
    if r.pets and dest.pets == "banned":
        turn.think(f"{dest.name}: ввоз животных запрещён — план невозможен, предлагаю альтернативы")
        r.destination = None
        old_plan = state.plan
        state.plan, state.cart = None, None
        res = do_destinations(state, turn, reason=f"⚠️ {dest.name}: ввоз животных запрещён, с питомцем туда не получится. "
                                                  f"Подобрал похожие направления, куда можно с ним.")
        if old_plan:
            res["blocks"].insert(0, {"type": "diff", "items": [f"направление {dest.name} исключено: ввоз животных запрещён"]})
        return res
    old = state.plan
    if old and old.get("dest_id") != r.destination:
        state.pinned = {}
    turn.think(("Пересобираю: " if replan else "Собираю поездку ") + f"{dest.name} — транспорт, отели, трансферы, состыковки")
    plan = planner.build_plan(r, r.destination, state.pinned, old if replan and sticky else None)
    for t in plan.get("trace", []):
        turn.tool(t["tool"], t["args"], t["result"])
    if plan.get("error"):
        state.stage = "destinations" if state.dest_options else "clarify"
        return reply(f"{plan['error']} Попробуем другие даты или направление?",
                     chips=[chip("На 3 дня позже", action={"type": "shift", "days": 3}),
                            chip("Другие направления", action={"type": "show_destinations"})])
    state.plan = plan
    state.stage = "bundles"
    state.cart = None
    mark_reco(state)
    update_profile(state)
    diff = planner.plan_diff(old, plan) if replan and not SILENT.get() else []
    if replan:
        state.replans += 1
        store.log_event(state.id, state.user_id, "replan", {"diff": diff})
    store.log_event(state.id, state.user_id, "plan", {"dest": r.destination, "bundles": len(plan["bundles"]),
                                                      "issues": sum(1 for b in plan["bundles"] for i in b["issues"]
                                                                    if i["level"] == "error")})
    return present_plan(state, turn, diff, intro=reason)


def present_plan(state: SessionState, turn: Turn, diff: list[str] | None, intro: str | None = None) -> dict:
    plan = state.plan
    r = state.req
    best = plan["bundles"][0]
    lines = []
    if intro:
        lines.append(intro)
    lines.append(f"**{plan['flag']} {plan['dest_name']}, {plan['dates_text']}** — собрал {len(plan['bundles'])} "
                 f"{'варианта' if len(plan['bundles']) in (2, 3, 4) else 'вариант'} поездки под ключ.")
    if plan.get("date_note"):
        lines.append(f"Даты выбрал сам: {plan['date_note']}.")
    w = plan["weather"]
    lines.append(f"Погода: днём около {deg(w['air'])}" + (f", море +{w['sea']}°" if w["sea"] is not None else "") +
                 (f", дождливых дней ~{w['rain_days']}" if w["rain_days"] >= 5 else "") + ".")
    lines.append(f"\nРекомендую **{best['title'].lower()}** за **{rub(best['total'])}**: " + ", ".join(best["explain"]["why"][:3]) + ".")
    if best["explain"]["tradeoffs"]:
        lines.append("Чем пожертвовали: " + ", ".join(best["explain"]["tradeoffs"][:2]) + ".")
    errors = [i for b in plan["bundles"] for i in b["issues"] if i["level"] == "error"]
    warns = [i for i in best["issues"] if i["level"] == "warn"]
    adj = best.get("adjustments") or []
    if adj:
        lines.append("Подстроил состыковки: " + "; ".join(adj) + ".")
    lines.append(f"Проверил состыковки: {'всё сходится' if not errors and not warns else f'{len(warns)} замечания, см. в карточке'}.")
    for n in plan.get("notes", []):
        lines.append("ℹ️ " + n)
    if r.meal and best["stay"]["meal"] != r.meal and not (r.meal == "AI" and best["stay"]["meal"] == "UAI"):
        lines.append(f"ℹ️ Тарифов «{inv.MEAL_NAMES[r.meal]}» в подходящих отелях нет — здесь работают по системе "
                     f"«{best['stay']['meal_name']}», это уже включает {inv.MEAL_NAMES[r.meal]}.")
    dest = DEST_BY_ID[plan["dest_id"]]
    month = date.fromisoformat(plan["date_from"]).month
    risk = dest.risks.get(month)
    weather_warn = None
    if risk:
        weather_warn = risk
    elif "dry" in r.prefs and plan["weather"]["rain_days"] >= 10:
        weather_warn = f"в это время ~{plan['weather']['rain_days']} дождливых дней"
    if weather_warn:
        dry = [m for m in range(1, 13) if dest.rain[m - 1] <= 7 and (not dest.sea or m in dest.swim_months or not dest.swim_months)]
        tip = f" Лучшее время для поездки: {months_span(dry)}." if dry and len(dry) < 12 else ""
        lines.insert(1 if intro else 0, f"⚠️ **Важно про {dest.name}:** {weather_warn}.{tip} Могу подобрать альтернативу на эти же даты.")
    if r.assumptions:
        lines.append("\n_Допущения: " + "; ".join(r.assumptions.values()) + " — поправьте, если не так._")
    blocks = []
    if diff:
        blocks.append({"type": "diff", "items": diff})
    blocks.append({"type": "plan", "plan": slim_plan(plan)})
    chips_ = [chip(f"Беру «{best['title']}»", action={"type": "choose_bundle", "idx": 0})]
    if len(plan["bundles"]) > 1:
        chips_.append(chip(f"«{plan['bundles'][1]['title']}»", action={"type": "choose_bundle", "idx": 1}))
    chips_ += [chip("На 2 дня позже", action={"type": "shift", "days": 2}),
               chip("Бюджет меньше", action={"type": "cheaper"}),
               chip("С нами едет собака", "с нами едет собака") if not r.pets else chip("Другие отели", action={"type": "show_hotels"}),
               chip("Почему этот отель?", action={"type": "why"}),
               chip("Другие направления", action={"type": "show_destinations"})]
    if weather_warn:
        chips_.insert(1, chip("Альтернатива без дождей" if "дожд" in weather_warn or "муссон" in weather_warn else "Подобрать альтернативу",
                              action={"type": "show_destinations"}))
    return reply("\n".join(lines), blocks, chips_)


def months_span(months: list[int]) -> str:
    """[5,6,7,8,9] → «май–сентябрь»; несвязные — через запятую."""
    if not months:
        return ""
    ms = sorted(months)
    groups, cur = [], [ms[0]]
    for m in ms[1:]:
        if m == cur[-1] + 1:
            cur.append(m)
        else:
            groups.append(cur)
            cur = [m]
    groups.append(cur)
    if len(groups) > 1 and groups[0][0] == 1 and groups[-1][-1] == 12:
        groups[0] = groups.pop() + groups[0]
    return ", ".join(MONTH_NAMES[g[0] - 1] + (f"–{MONTH_NAMES[g[-1] - 1]}" if len(g) > 1 else "") for g in groups)


def slim_plan(plan: dict) -> dict:
    """Урезаем план для отправки в UI (без служебных вариантов трансферов)."""
    p = {k: v for k, v in plan.items() if k not in ("trace",)}
    bundles = []
    for b in plan["bundles"]:
        bb = {k: v for k, v in b.items() if k not in ("transfer_in_options", "transfer_out_options")}
        bb["hotel"] = {k: b["hotel"][k] for k in ("id", "name", "stars", "area", "rating", "reviews", "beach_m", "center_km",
                                                  "airport_km", "kids_club", "pets_allowed", "pool", "spa", "vibe", "adults_only",
                                                  "early_checkin", "fit", "fit_pros", "fit_cons")}
        bundles.append(bb)
    p["bundles"] = bundles
    return p


def show_hotels(state: SessionState) -> dict:
    plan = state.plan
    rows = plan["hotels"]
    text = (f"Сравнение отелей ({plan['dest_name']}, {plan['dates_text']}). Цена — за весь срок на всех, «подходит» — насколько "
            f"отель совпадает с вашими условиями. Нажмите «Выбрать», и я пересоберу поездку вокруг отеля.")
    if plan.get("excluded_hotels"):
        text += "\n\nЧестно отсеял: " + "; ".join(f"{h['name']} — {h['reason']}" for h in plan["excluded_hotels"][:3]) + "."
    return reply(text, [{"type": "hotels", "rows": rows, "excluded": plan.get("excluded_hotels", [])}],
                 [chip("Сравнить маршруты", action={"type": "show_flights"}), chip("Вернуться к вариантам", action={"type": "back_to_plan"})])


def show_flights(state: SessionState) -> dict:
    plan = state.plan
    return reply("Варианты дороги туда и обратно. Цена — на всех, с багажом. Выберите — пересоберу план вокруг рейса.",
                 [{"type": "flights", "out": plan["flights_out"], "back": plan["flights_back"]}],
                 [chip("Сравнить отели", action={"type": "show_hotels"}), chip("Вернуться к вариантам", action={"type": "back_to_plan"})])


# ====================================================================== изменения условий

def current_dates(state: SessionState) -> tuple[date, date] | None:
    if state.plan:
        return date.fromisoformat(state.plan["date_from"]), date.fromisoformat(state.plan["date_to"])
    if state.req.date_from:
        d1 = date.fromisoformat(state.req.date_from)
        d2 = date.fromisoformat(state.req.date_to) if state.req.date_to else d1 + timedelta(days=planner.default_nights(state.req))
        return d1, d2
    return None


def do_shift(state: SessionState, days: int, turn: Turn) -> dict:
    cur = current_dates(state)
    if not cur:
        return reply("Пока не от чего сдвигать — скажите, когда примерно хотите поехать.")
    d1, d2 = cur[0] + timedelta(days=days), cur[1] + timedelta(days=days)
    if d1 < today() + timedelta(days=1):
        return reply("Раньше уже не получится — это слишком близко к сегодняшнему дню.")
    state.req.date_from, state.req.date_to = d1.isoformat(), d2.isoformat()
    state.req.month, state.req.flexible = d1.month, False
    state.req.assumptions.pop("when", None)
    turn.think(f"Сдвигаю даты на {days:+d} дн.: {date_range_text(d1, d2)}. Направление и отель сохраняю, пересчитываю транспорт и цены")
    for k in ("out_id", "back_id"):
        state.pinned.pop(k, None)
    if state.cart:
        state.pinned["hotel_id"] = state.cart["bundle"]["hotel"]["id"]
    if state.req.destination:
        return do_plan(state, turn, replan=True, reason=f"Сдвинул поездку на {abs(days)} дн. {'позже' if days > 0 else 'раньше'}.")
    return do_destinations(state, turn, reason=f"Сдвинул даты: {date_range_text(d1, d2)}.")


def do_extend(state: SessionState, n: int, turn: Turn) -> dict:
    cur = current_dates(state)
    nights = max(1, (cur[1] - cur[0]).days + n) if cur else max(1, (state.req.nights or 7) + n)
    state.req.nights = nights
    state.req.nights_source = "user"
    if cur:
        state.req.date_from = cur[0].isoformat()
        state.req.date_to = (cur[0] + timedelta(days=nights)).isoformat()
    state.req.assumptions.pop("nights", None)
    turn.think(f"Меняю длительность: {nights} ночей")
    state.pinned.pop("back_id", None)
    if state.req.destination:
        return do_plan(state, turn, replan=True, reason=f"Теперь {nights} ночей.")
    return do_destinations(state, turn)


def do_cheaper(state: SessionState, turn: Turn) -> dict:
    r = state.req
    if state.plan:
        base = state.plan["bundles"][state.plan.get("selected") or 0]["total"]
        r.budget = int(min(r.budget or base, base) * 0.85 / 1000) * 1000
        r.priority = "price"
        state.pinned = {}
        turn.think(f"Нужно дешевле: ставлю потолок {rub(r.budget)} (−15% от текущего), приоритет — цена")
        return do_plan(state, turn, replan=True, reason=f"Ищу дешевле: бюджет до {rub(r.budget)}, приоритет — цена.")
    r.budget_level = "low"
    r.priority = "price"
    if r.budget:
        r.budget = int(r.budget * 0.85)
    return do_destinations(state, turn, reason="Сделал упор на бюджетные варианты.")


def do_pricier(state: SessionState, turn: Turn) -> dict:
    r = state.req
    r.priority = "comfort"
    if r.budget:
        r.budget = int(r.budget * 1.3)
    r.budget_level = "high" if r.budget_level != "low" else None
    state.pinned = {}
    turn.think("Можно дороже — переключаю приоритет на комфорт")
    if state.plan:
        return do_plan(state, turn, replan=True, reason="Сделал упор на комфорт.")
    return do_destinations(state, turn, reason="Сделал упор на комфорт.")


# ====================================================================== объяснения

def do_why(state: SessionState, turn: Turn) -> dict:
    r = state.req
    if state.plan:
        plan = state.plan
        idx = plan.get("selected") or 0
        b = plan["bundles"][idx]
        w = plan["weights"]
        h = b["hotel"]
        lines = [f"**Почему «{b['title']}»: {h['name']}** ({h['stars']}★, {h['area']}).",
                 f"Я сравнивал варианты по формуле: цена {int(w['price'] * 100)}% · отель под вас {int(w['hotel'] * 100)}% · "
                 f"удобство рейсов {int(w['transport'] * 100)}% · время в пути {int(w['time'] * 100)}%. "
                 f"Режим «{PRIORITY_LABELS[r.priority]}» — переключите, если важнее другое."]
        lines.append("\n**Плюсы для вас:** " + "; ".join(b["explain"]["why"]) + ".")
        if b["explain"]["tradeoffs"]:
            lines.append("**Чем пожертвовали:** " + "; ".join(b["explain"]["tradeoffs"]) + ".")
        if b["explain"]["risks"]:
            lines.append("**Риски:** " + "; ".join(b["explain"]["risks"]) + ".")
        others = [x for x in plan["hotels"] if not x["in_plan"]][:2]
        if others:
            lines.append("\n**Почему не другие:** " + "; ".join(
                f"{o['name']} — {', '.join(o['cons'][:1]) or 'ниже совпадение'} ({o['fit']}% совпадения, {rub(o['price'])})" for o in others) + ".")
        if plan.get("excluded_hotels"):
            lines.append("**Отсеял сразу:** " + "; ".join(f"{e['name']} — {e['reason']}" for e in plan["excluded_hotels"][:3]) + ".")
        chips_ = [chip(PRIORITY_LABELS[p], action={"type": "set", "key": "priority", "value": p})
                  for p in ("price", "comfort", "time") if p != r.priority]
        chips_.append(chip("Покажи все отели", action={"type": "show_hotels"}))
        return reply("\n".join(lines), [{"type": "weights", "weights": w, "priority": r.priority}], chips_)
    if state.dest_options:
        lead = state.dest_options[0]
        lines = ["Как я выбирал направления: оценил погоду в ваши даты (воздух, море, дожди), время в пути из вашего города, "
                 "совпадение с интересами, визы, бюджет под ключ и удобство для вашего состава. Потом посчитал реальные цены "
                 "на перелёт+отель+трансфер и выбрал самые выгодные даты в месяце.",
                 f"\n**{lead['name']}** впереди, потому что: " + "; ".join(lead["why"]) + "."]
        if lead["cons"] or lead["risks"]:
            lines.append("Минусы: " + "; ".join(lead["cons"] + lead["risks"]) + ".")
        if state.excluded:
            lines.append("\n**Исключил:** " + "; ".join(f"{e['name']} — {e['reason']}" for e in state.excluded[:5]) + ".")
        return reply("\n".join(lines), [], stage_chips(state))
    return reply("Я пока ничего не выбирал — расскажите о поездке, и я объясню каждый шаг.")


def do_info(state: SessionState, message: str, turn: Turn) -> dict:
    dest_id = state.req.destination or (state.dest_options[0]["dest_id"] if state.dest_options else None)
    if not dest_id:
        return reply("О каком направлении рассказать? Назовите город или страну.")
    d = DEST_BY_ID[dest_id]
    month = state.req.month or today().month
    w = planner.weather(d, month)
    facts = (f"{d.name} ({d.country}). Погода в {MONTH_LOC[month - 1]}: воздух днём {deg(w['air'])}, "
             + (f"море +{w['sea']}°, " if w["sea"] is not None else "") + f"дождливых дней {w['rain_days']}. "
             f"Виза: {d.visa_note}. Что посмотреть: {'; '.join(d.highlights)}. "
             f"Питомцы: {'просто' if d.pets == 'easy' else 'нужны документы' if d.pets == 'docs' else 'сложно' if d.pets == 'hard' else 'запрещено'}. "
             f"Расходы на месте ~{rub(d.daily_spend)} в день на человека.")
    ans = llm.answer(message, facts)
    if ans:
        turn.tool("llm_answer", {"dest": d.name}, "ответ по фактам направления")
        return reply(ans, chips=stage_chips(state))
    text = (f"**{d.flag} {d.name}** в {MONTH_LOC[month - 1]}: днём около {deg(w['air'])}" +
            (f", море +{w['sea']}°" if w["sea"] is not None else "") + f", дождливых дней ~{w['rain_days']}.\n"
            f"Документы: {d.visa_note}.\n"
            f"Что посмотреть:\n" + "\n".join(f"• {h}" for h in d.highlights) +
            f"\nНа месте закладывайте ~{rub(d.daily_spend)} в день на человека.")
    return reply(text, chips=stage_chips(state))


# ====================================================================== корзина

def insurance_price(b: dict, dest_id: str) -> int:
    d = DEST_BY_ID[dest_id]
    days = (date.fromisoformat(b["stay"]["check_out"]) - date.fromisoformat(b["stay"]["check_in"])).days + 2
    people = b["party"]["adults"] + len(b["party"]["children"])
    return inv.r100((60 if d.domestic else 160) * days * people)


def do_cart(state: SessionState, idx: int, turn: Turn) -> dict:
    plan = state.plan
    plan["selected"] = idx
    b = plan["bundles"][idx]
    state.pinned.update({"hotel_id": b["hotel"]["id"], "out_id": b["out"]["id"], "back_id": b["back"]["id"]})
    state.cart = {"bundle": b, "addons": {}, "dest_id": plan["dest_id"]}
    state.stage = "cart"
    turn.think(f"Кладу в корзину «{b['title']}» и повторно проверяю состыковки")
    store.log_event(state.id, state.user_id, "cart", {"bundle": b["code"]})
    return present_cart(state)


def rebuild_cart(state: SessionState, turn: Turn, note: str) -> dict:
    idx = state.plan.get("selected") or 0
    plan = planner.build_plan(state.req, state.plan["dest_id"], state.pinned, state.plan)
    if plan.get("error"):
        return reply(plan["error"])
    plan["selected"] = 0
    addons = state.cart.get("addons", {}) if state.cart else {}
    state.plan = plan
    b = plan["bundles"][0]
    state.cart = {"bundle": b, "addons": addons, "dest_id": plan["dest_id"]}
    _ = idx
    return present_cart(state, note)


def cart_view(state: SessionState) -> dict:
    c = state.cart
    b = c["bundle"]
    a, ch = b["party"]["adults"], b["party"]["children"]
    items = []
    o, back = b["out"], b["back"]
    for leg, label in ((o, "Туда"), (back, "Обратно")):
        items.append({"kind": "transport", "title": f"{label}: {leg['from_name']} → {leg['to_name']}",
                      "details": f"{leg['carrier']} {leg['number']}, {planner.dt(leg['dep']):%d.%m %H:%M} → "
                                 f"{planner.dt(leg['arr']):%d.%m %H:%M}, {hours(leg['duration_min'])}"
                                 + (f", пересадка {leg['via']}" if leg.get("stops") else "") + f", {leg['fare_name']}",
                      "price": leg["total_price"]})
    st = b["stay"]
    items.append({"kind": "hotel", "title": f"{b['hotel']['name']} {b['hotel']['stars']}★",
                  "details": f"{ddmm(date.fromisoformat(st['check_in']))} – {ddmm(date.fromisoformat(st['check_out']))}, "
                             f"{st['nights']} ноч., {st['rooms']} ном., {st['meal_name']}, {st['rate']['name']}",
                  "price": st["rate"]["price"], "rates": st["rates"], "rate_code": st["rate"]["code"]})
    for t, label, opts in ((b.get("transfer_in"), "Трансфер до отеля", b.get("transfer_in_options", [])),
                           (b.get("transfer_out"), "Трансфер обратно", b.get("transfer_out_options", []))):
        if t:
            items.append({"kind": "transfer", "title": f"{label}: {t['name']}", "details": f"~{hours(t['duration_min'])}. {t['note']}",
                          "price": t["price"], "dir": "in" if "до" in label else "out",
                          "options": [{"id": x["id"], "name": x["name"], "price": x["price"], "available": x["available"],
                                       "duration_min": x["duration_min"]} for x in opts]})
    addons_def = cart_addons(state)
    total = sum(i["price"] for i in items) + sum(x["price"] for k, x in addons_def.items() if c["addons"].get(k))
    issues = planner.check_connections(b, state.req)
    return {"items": items, "addons": [{"code": k, **v, "selected": bool(c["addons"].get(k))} for k, v in addons_def.items()],
            "total": total, "issues": issues, "timeline": planner.itinerary(b, state.req),
            "dest_name": DEST_BY_ID[b["dest_id"]].name, "party": {"adults": a, "children": ch},
            "dates_text": date_range_text(date.fromisoformat(st["check_in"]), date.fromisoformat(st["check_out"]))}


def cart_addons(state: SessionState) -> dict:
    b = state.cart["bundle"]
    st = b["stay"]
    night = st["rate"]["price"] // max(1, st["nights"])
    h = b["hotel"]
    out = {"insurance": {"title": "Медицинская страховка", "price": insurance_price(b, b["dest_id"]),
                         "note": "обязательна для Кубы, рекомендуется везде" if not DEST_BY_ID[b["dest_id"]].domestic else "по России — по желанию"}}
    early_txt = {"guaranteed": "гарантированно", "on_request": "по запросу, подтвердят за сутки", "none": "отель не предоставляет"}
    codes = {i["code"] for i in planner.check_connections(b, state.req)}
    if "early_arrival" in codes or state.cart["addons"].get("early_checkin") or state.cart["addons"].get("night_before"):
        if h["early_checkin"] != "none":
            out["early_checkin"] = {"title": "Ранний заезд", "price": inv.r100(night * 0.5) if h["early_checkin"] == "guaranteed" else 0,
                                    "note": early_txt[h["early_checkin"]]}
        else:
            out["night_before"] = {"title": "Ночь до заезда (номер сразу по прилёту)", "price": inv.r100(night),
                                   "note": "ранний заезд отель не даёт — так номер будет готов к прилёту"}
    if "late_checkout" in codes or state.cart["addons"].get("late_checkout"):
        out["late_checkout"] = {"title": "Поздний выезд до 18:00", "price": inv.r100(night * 0.5), "note": "удобно при вечернем рейсе"}
    return out


def present_cart(state: SessionState, note: str | None = None) -> dict:
    view = cart_view(state)
    errors = [i for i in view["issues"] if i["level"] == "error"]
    warns = [i for i in view["issues"] if i["level"] == "warn"]
    lines = []
    if note and not SILENT.get():
        lines.append(note)
    lines.append(f"Собрал поездку в корзину: **{view['dest_name']}, {view['dates_text']}**, итого **{rub(view['total'])}**.")
    lines.append("Прошёлся по состыковкам ещё раз: " + ("ошибок нет" if not errors else f"⚠️ {len(errors)} ошибки") +
                 (f", {len(warns)} на что обратить внимание" if warns else "") + ".")
    for i in warns:
        lines.append("• " + i["text"])
    sug = []
    if not DEST_BY_ID[state.cart["bundle"]["dest_id"]].domestic and not state.cart["addons"].get("insurance"):
        sug.append(chip("Добавить страховку", action={"type": "toggle_addon", "addon": "insurance"}))
    for i in view["issues"]:
        if i.get("fix") and i["fix"]["action"] in ("early_checkin", "late_checkout"):
            sug.append(chip(i["fix"]["label"], action={"type": "toggle_addon",
                                                       "addon": i["fix"]["action"] if i["fix"]["action"] != "early_checkin"
                                                       or state.cart["bundle"]["hotel"]["early_checkin"] != "none" else "night_before"}))
    chips_ = [chip("Оформить и оплатить", action={"type": "checkout"})] + sug + \
             [chip("Вернуться к вариантам", action={"type": "back_to_plan"})]
    return reply("\n".join(lines), [{"type": "cart", "cart": view}], chips_)


def cart_transfer(state: SessionState, args: dict, turn: Turn) -> dict:
    b = state.cart["bundle"]
    key = "transfer_in" if args.get("dir") == "in" else "transfer_out"
    opts = b.get(key + "_options", [])
    if args.get("id") == "none":
        b[key] = None
    else:
        sel = next((o for o in opts if o["id"] == args.get("id")), None)
        if not sel:
            return reply("Такого трансфера нет.")
        if not sel["available"]:
            return reply(f"«{sel['name']}» не работает в это время — выберите другой.")
        b[key] = sel
    b["prices"]["transfer"] = sum(x["price"] for x in (b.get("transfer_in"), b.get("transfer_out")) if x)
    b["total"] = b["prices"]["transport"] + b["prices"]["hotel"] + b["prices"]["transfer"]
    b["issues"] = planner.check_connections(b, state.req)
    turn.think("Пользователь сменил трансфер — пересчитал итог и проверки")
    return present_cart(state, "Трансфер обновил.")


def toggle_addon(state: SessionState, code: str, turn: Turn) -> dict:
    addons = cart_addons(state)
    if code not in addons:
        if code == "early_checkin" and "night_before" in addons:
            code = "night_before"
        else:
            return reply("Эта опция для отеля недоступна.")
    cur = state.cart["addons"].get(code, False)
    state.cart["addons"][code] = not cur
    turn.think(f"{'Убрал' if cur else 'Добавил'} опцию: {addons[code]['title']}")
    return present_cart(state, f"{'Убрал' if cur else 'Добавил'}: {addons[code]['title']} ({rub(addons[code]['price'])}).")


def do_checkout(state: SessionState, turn: Turn) -> dict:
    view = cart_view(state)
    if any(i["level"] == "error" for i in view["issues"]):
        return reply("В корзине есть ошибка состыковки — сначала исправим: " +
                     "; ".join(i["text"] for i in view["issues"] if i["level"] == "error"),
                     [{"type": "cart", "cart": view}])
    state.stage = "checkout"
    state.awaiting = "travelers"
    profile = store.get_profile(state.user_id)
    a, ch = view["party"]["adults"], view["party"]["children"]
    saved = profile.get("travelers", [])
    travelers = []
    for i in range(a):
        travelers.append({"type": "adult", "label": f"Взрослый {i + 1}",
                          "name": saved[i]["name"] if i < len(saved) else ""})
    for j, age in enumerate(ch):
        k = a + j
        travelers.append({"type": "child", "label": f"Ребёнок {j + 1} ({age} л.)", "age": age,
                          "name": saved[k]["name"] if k < len(saved) else ""})
    store.log_event(state.id, state.user_id, "checkout", {})
    turn.think("Финальная проверка пройдена — запрашиваю данные путешественников (оплата имитируется)")
    text = (f"Осталось оформить. К оплате **{rub(view['total'])}**. Заполните данные путешественников "
            f"(как в паспорте) — или просто напишите имена через запятую.\n_Оплата в прототипе имитируется, деньги не списываются._")
    return reply(text, [{"type": "checkout", "travelers": travelers, "email": profile.get("email", ""), "total": view["total"]}],
                 [chip("Вернуться в корзину", action={"type": "back_to_plan"})])


def do_pay(state: SessionState, pay: dict | None, turn: Turn) -> dict:
    if not state.cart:
        return reply("Корзина пуста — сначала выберите вариант поездки.")
    view = cart_view(state)
    a, ch = view["party"]["adults"], view["party"]["children"]
    travelers = (pay or {}).get("travelers") or []
    travelers = [t for t in travelers if (t.get("name") or "").strip()]
    if len(travelers) < a + len(ch):
        if state.stage != "checkout":
            return do_checkout(state, turn)
        need = a + len(ch)
        return reply(f"Нужны имена всех путешественников: {need} (сейчас {len(travelers)}). Напишите через запятую, "
                     f"например: «Иван Петров, Мария Петрова».")
    if any(i["level"] == "error" for i in view["issues"]):
        return reply("Не могу оформить: в плане ошибка состыковки.")
    turn.tool("payment_gateway_mock", {"amount": view["total"]}, "оплата подтверждена (имитация)")
    b = state.cart["bundle"]
    bid = "OST-" + "".join(random.choice("0123456789") for _ in range(6))
    booking = {
        "id": bid, "user_id": state.user_id, "session_id": state.id, "status": "confirmed", "created": time.time(),
        "dest_id": b["dest_id"], "dest_name": view["dest_name"], "dates_text": view["dates_text"],
        "bundle": {k: v for k, v in b.items()}, "addons": {k: v for k, v in state.cart["addons"].items() if v},
        "addons_def": cart_addons(state), "total": view["total"],
        "travelers": [{"name": t["name"].strip(), **({"age": t["age"]} if "age" in t else {})} for t in travelers],
        "email": (pay or {}).get("email", ""), "history": [{"ts": time.time(), "text": f"Оплачено {rub(view['total'])}"}],
        "requests": {}, "pets": state.req.pets,
    }
    if booking["addons"].get("early_checkin"):
        early = b["hotel"]["early_checkin"]
        booking["requests"]["early_checkin"] = "confirmed" if early == "guaranteed" else "pending"
    booking["reminders"] = make_reminders(booking)
    store.save_booking(booking)
    state.booking_ids.append(bid)
    state.active_booking = bid
    state.stage = "booked"
    state.awaiting = None
    update_profile(state, booking)
    store.log_event(state.id, state.user_id, "booking", {"total": view["total"], "errors": sum(
        1 for i in view["issues"] if i["level"] == "error")})
    turn.think("Бронь создана, напоминания запланированы, предпочтения сохранены в память")
    text = (f"Готово! Поездка оформлена, номер брони **{bid}**. Подтверждение и билеты отправил бы на почту.\n"
            f"Я запланировал напоминания и остаюсь на связи: могу запросить ранний заезд, поменять даты, добавить трансфер.")
    return reply(text, [{"type": "booking", "booking": booking_view(booking)}], postsale_chips(booking))


def make_reminders(bk: dict) -> list[dict]:
    b = bk["bundle"]
    d = DEST_BY_ID[bk["dest_id"]]
    dep = planner.dt(b["out"]["dep"])
    rem = []
    def add(when: datetime, text: str):
        if when > datetime.now() - timedelta(days=1):
            rem.append({"at": when.strftime("%Y-%m-%dT%H:%M"), "text": text})
    add(datetime.now(), f"Подтверждение брони {bk['id']} отправлено на почту")
    rate = b["stay"]["rate"]
    if rate["refundable"]:
        dl = datetime.fromisoformat(b["stay"]["check_in"]) - timedelta(days=rate["free_cancel_days"])
        add(dl, "Последний день бесплатной отмены отеля")
    if not d.domestic:
        add(dep - timedelta(days=14), "Проверьте загранпаспорт" + (f" и визу: {d.visa_note}" if d.visa != "none" else ""))
    if bk.get("pets"):
        add(dep - timedelta(days=5), "Ветклиника: справка/сертификат для питомца")
    if b["out"]["mode"] == "plane":
        add(dep - timedelta(hours=24), "Открылась онлайн-регистрация на рейс " + b["out"]["number"])
        add(dep - timedelta(hours=4), "Пора выезжать в аэропорт")
    else:
        add(dep - timedelta(hours=3), "Пора выезжать на вокзал")
    if b.get("transfer_in"):
        add(planner.dt(b["out"]["arr"]), f"Трансфер «{b['transfer_in']['name']}» встретит после прилёта")
    add(datetime.fromisoformat(b["stay"]["check_out"] + "T10:00"), f"Выезд из отеля до {b['stay']['checkout_time']}")
    rem.sort(key=lambda x: x["at"])
    return rem


def booking_view(bk: dict) -> dict:
    b = bk["bundle"]
    addons_def = bk.get("addons_def", {})
    return {
        "id": bk["id"], "status": bk["status"], "dest_name": bk["dest_name"], "dates_text": bk["dates_text"], "total": bk["total"],
        "hotel": f"{b['hotel']['name']} {b['hotel']['stars']}★",
        "out": f"{b['out']['carrier']} {b['out']['number']}, {planner.dt(b['out']['dep']):%d.%m %H:%M}",
        "back": f"{b['back']['carrier']} {b['back']['number']}, {planner.dt(b['back']['dep']):%d.%m %H:%M}",
        "transfer": (b.get("transfer_in") or {}).get("name"),
        "travelers": bk["travelers"], "reminders": bk.get("reminders", []),
        "addons": [addons_def.get(k, {}).get("title", k) for k in bk.get("addons", {})],
        "requests": bk.get("requests", {}), "history": bk.get("history", []),
        "timeline": planner.itinerary(b, TripRequest()),
    }


# ====================================================================== после покупки

def postsale_chips(bk: dict) -> list[dict]:
    c = [chip("Ранний заезд", action={"type": "early_checkin"}),
         chip("Перенести на 2 дня позже", action={"type": "change_booking", "days": 2}),
         chip("Напоминания", action={"type": "reminders"})]
    if not bk["bundle"].get("transfer_in"):
        c.insert(1, chip("Добавить трансфер", action={"type": "add_transfer"}))
    c.append(chip("Отменить бронь", action={"type": "cancel_booking"}))
    c.append(chip("Новая поездка", action={"type": "reset"}))
    return c


def postsale(state: SessionState, intent: str, upd: dict, turn: Turn) -> dict:
    bk = store.get_booking(state.active_booking) if state.active_booking else None
    if not bk:
        state.stage = "start"
        return reply("Не нашёл активной брони. Начнём новую поездку?")
    if bk["status"] == "cancelled" and intent not in ("menu",):
        return reply(f"Бронь {bk['id']} отменена. Начнём новую поездку?", chips=[chip("Новая поездка", action={"type": "reset"})])
    b = bk["bundle"]
    h = b["hotel"]
    night = b["stay"]["rate"]["price"] // max(1, b["stay"]["nights"])
    if intent == "early_checkin":
        arr = planner.dt(b["out"]["arr"])
        policy = h["early_checkin"]
        ready = arr + timedelta(minutes=(b.get("transfer_in") or {"duration_min": 60})["duration_min"])
        cin = int(b["stay"]["checkin_time"][:2])
        if ready.date() == date.fromisoformat(b["stay"]["check_in"]) and ready.hour >= cin - 1:
            return reply(f"Ранний заезд не нужен: в отеле будете примерно в {ready:%H:%M}, а заселение с {b['stay']['checkin_time']}. "
                         f"Предупредил отель о времени прибытия.", [{"type": "booking", "booking": booking_view(bk)}], postsale_chips(bk))
        if date.fromisoformat(b["stay"]["check_in"]) < arr.date():
            return reply(f"Номер уже ваш с ночи {ddmm(date.fromisoformat(b['stay']['check_in']))} — заселитесь сразу после прилёта в {arr:%H:%M}.",
                         [{"type": "booking", "booking": booking_view(bk)}], postsale_chips(bk))
        if policy == "guaranteed":
            fee = inv.r100(night * 0.5)
            bk["requests"]["early_checkin"] = "confirmed"
            bk["history"].append({"ts": time.time(), "text": f"Ранний заезд подтверждён, доплата {rub(fee)}"})
            bk["total"] += fee if not bk["addons"].get("early_checkin") else 0
            bk["addons"]["early_checkin"] = True
            text = f"Отель подтвердил ранний заезд (прилёт в {arr:%H:%M}). Доплата {rub(fee)} — списал бы с той же карты."
        elif policy == "on_request":
            bk["requests"]["early_checkin"] = "pending"
            bk["history"].append({"ts": time.time(), "text": "Отправлен запрос на ранний заезд"})
            text = (f"Отправил запрос в отель (прилёт в {arr:%H:%M}). Гарантии нет — отель ответит за сутки до заезда, я сообщу. "
                    f"Если нужна гарантия — могу докупить ночь до заезда за {rub(night)}.")
        else:
            text = (f"Этот отель не делает ранний заезд. Вариант — докупить ночь до заезда ({rub(night)}), тогда номер будет "
                    f"готов сразу. Или оставить багаж на ресепшене и пойти на пляж.")
        turn.tool("hotel_request_mock", {"hotel": h["name"], "type": "early_checkin"}, bk["requests"].get("early_checkin", "offer"))
        store.save_booking(bk)
        return reply(text, [{"type": "booking", "booking": booking_view(bk)}], postsale_chips(bk))
    if intent == "late_checkout":
        fee = inv.r100(night * 0.5)
        bk["requests"]["late_checkout"] = "confirmed"
        bk["addons"]["late_checkout"] = True
        bk["total"] += fee
        bk["history"].append({"ts": time.time(), "text": f"Поздний выезд до 18:00, доплата {rub(fee)}"})
        store.save_booking(bk)
        return reply(f"Поздний выезд до 18:00 подтверждён, доплата {rub(fee)}.", [{"type": "booking", "booking": booking_view(bk)}],
                     postsale_chips(bk))
    if intent == "reminders":
        return reply(f"Напоминания по брони {bk['id']}. Можно скачать их в календарь.",
                     [{"type": "reminders", "items": bk["reminders"], "ics": f"/api/bookings/{bk['id']}/ics"}], postsale_chips(bk))
    if intent == "add_transfer":
        opts = inv.search_transfers(h, b["out"]["arr"], b["party"]["adults"], b["party"]["children"],
                                    "station" if b["out"]["mode"] == "train" else "airport")
        turn.tool("search_transfers", {"hotel": h["name"], "arrival": b["out"]["arr"]}, f"{len(opts)} вариантов")
        rec = choose_transfer_for(opts, state.req)
        items = [chip(f"{o['name']} — {rub(o['price'])}, ~{hours(o['duration_min'])}" + (" (рекомендую)" if rec and o["id"] == rec["id"] else ""),
                      action={"type": "booking_transfer", "id": o["id"]}) for o in opts if o["available"]]
        return reply(f"Трансфер к {h['name']} на прилёт {planner.dt(b['out']['arr']):%d.%m в %H:%M}. "
                     + (f"Рекомендую «{rec['name']}»: {rec['note'] or 'оптимально по цене и времени'}." if rec else ""), [], items)
    if intent == "booking_transfer":
        opts = inv.search_transfers(h, b["out"]["arr"], b["party"]["adults"], b["party"]["children"],
                                    "station" if b["out"]["mode"] == "train" else "airport")
        sel = next((o for o in opts if o["id"] == upd.get("_args", {}).get("id")), None)
        if not sel:
            return reply("Не нашёл этот трансфер.")
        b["transfer_in"] = sel
        bk["total"] += sel["price"]
        bk["history"].append({"ts": time.time(), "text": f"Добавлен трансфер «{sel['name']}» за {rub(sel['price'])}"})
        bk["reminders"] = make_reminders(bk)
        store.save_booking(bk)
        return reply(f"Добавил трансфер «{sel['name']}» к брони {bk['id']} (+{rub(sel['price'])}). Водитель встретит после прилёта.",
                     [{"type": "booking", "booking": booking_view(bk)}], postsale_chips(bk))
    if intent == "change_booking":
        days = upd.get("_shift")
        if not days:
            return reply("На сколько сдвинуть поездку?", chips=[chip(f"{d:+d} дн.", action={"type": "change_booking", "days": d})
                                                                 for d in (-2, -1, 1, 2, 3, 7)])
        return quote_change(state, bk, int(days), turn)
    if intent == "confirm_change":
        return apply_change(state, bk, int(upd["_shift"]), turn)
    if intent == "cancel_booking":
        q = cancel_quote(bk)
        return reply(f"Если отменить сейчас: вернётся **{rub(q['refund'])}** из {rub(bk['total'])}. " + "; ".join(q["notes"]) + ".",
                     chips=[chip("Да, отменить", action={"type": "confirm_cancel"}), chip("Не отменять", action={"type": "reminders"})])
    if intent == "confirm_cancel":
        q = cancel_quote(bk)
        bk["status"] = "cancelled"
        bk["history"].append({"ts": time.time(), "text": f"Бронь отменена, возврат {rub(q['refund'])}"})
        store.save_booking(bk)
        return reply(f"Бронь {bk['id']} отменена, возврат {rub(q['refund'])} придёт на карту (имитация).",
                     [{"type": "booking", "booking": booking_view(bk)}], [chip("Новая поездка", action={"type": "reset"})])
    if intent == "add_night_before":
        bk["addons"]["night_before"] = True
        bk["total"] += night
        bk["history"].append({"ts": time.time(), "text": f"Добавлена ночь до заезда (+{rub(night)})"})
        store.save_booking(bk)
        return reply(f"Добавил ночь до заезда (+{rub(night)}). Номер будет готов сразу по прилёте.",
                     [{"type": "booking", "booking": booking_view(bk)}], postsale_chips(bk))
    return reply(f"Ваша бронь **{bk['id']}**: {bk['dest_name']}, {bk['dates_text']}. Чем помочь?",
                 [{"type": "booking", "booking": booking_view(bk)}], postsale_chips(bk))


def choose_transfer_for(opts, req):
    return planner.choose_transfer(opts, req)


def change_fee(leg: dict, pax: int) -> int:
    if leg["mode"] == "train":
        return 300 * pax
    if leg["refundable"]:
        return 0
    return (3500 if leg["lowcost"] else 2500) * pax


def quote_change(state: SessionState, bk: dict, days: int, turn: Turn) -> dict:
    b = bk["bundle"]
    req = state.req.model_copy(deep=True)
    d1 = planner.dt(b["out"]["dep"]).date() + timedelta(days=days)
    nights = (date.fromisoformat(b["stay"]["check_out"]) - date.fromisoformat(b["stay"]["check_in"])).days
    d2 = planner.dt(b["back"]["dep"]).date() + timedelta(days=days)
    if d1 <= today():
        return reply("Так рано сдвинуть уже нельзя.")
    req.date_from, req.date_to = d1.isoformat(), d2.isoformat()
    new_plan = planner.build_plan(req, bk["dest_id"], {"hotel_id": b["hotel"]["id"]}, None)
    turn.tool("reprice_booking", {"shift": days, "dates": date_range_text(d1, d2)}, "переоценка выполнена" if not new_plan.get("error") else "нет вариантов")
    if new_plan.get("error"):
        return reply(f"На {date_range_text(d1, d2)} не нашёл вариантов: {new_plan['error']}")
    nb = next((x for x in new_plan["bundles"] if x["hotel"]["id"] == b["hotel"]["id"]), new_plan["bundles"][0])
    pax = b["party"]["adults"] + len([a for a in b["party"]["children"] if a >= 2])
    fees = change_fee(b["out"], pax) + change_fee(b["back"], pax)
    hotel_pen = 0
    rate = b["stay"]["rate"]
    days_left = (date.fromisoformat(b["stay"]["check_in"]) - today()).days
    if not rate["refundable"]:
        hotel_pen = inv.r100(rate["price"] * 0.3)
    elif days_left < rate["free_cancel_days"]:
        hotel_pen = inv.r100(rate["price"] / max(1, nights))
    old_cost = b["total"]
    new_cost = nb["total"]
    extra = max(0, new_cost - old_cost) + fees + hotel_pen
    refund = max(0, old_cost - new_cost) - fees - hotel_pen
    lines = [f"Перенос на {abs(days)} дн. {'позже' if days > 0 else 'раньше'}: **{date_range_text(d1, d2)}**."]
    lines.append(f"Отель: {'тот же — ' + nb['hotel']['name'] if nb['hotel']['id'] == b['hotel']['id'] else 'на эти даты нет мест, предлагаю ' + nb['hotel']['name']}.")
    lines.append(f"Рейсы: {nb['out']['number']} {planner.dt(nb['out']['dep']):%d.%m %H:%M} и {nb['back']['number']} {planner.dt(nb['back']['dep']):%d.%m %H:%M}.")
    lines.append(f"Сборы за обмен билетов: {rub(fees)}" + (f", штраф отеля: {rub(hotel_pen)}" if hotel_pen else ", отель меняет даты бесплатно") + ".")
    lines.append(f"Разница в цене: {rub(new_cost - old_cost) if new_cost >= old_cost else '−' + rub(old_cost - new_cost)}.")
    lines.append(f"**Итого {'доплата ' + rub(extra) if refund <= 0 else 'вернём ' + rub(refund)}.**")
    state.cart = {"bundle": nb, "addons": {}, "dest_id": bk["dest_id"], "_change_quote": {"days": days, "extra": extra, "refund": refund}}
    return reply("\n".join(lines), [], [chip("Подтвердить перенос", action={"type": "confirm_change", "days": days}),
                                        chip("Оставить как есть", action={"type": "reminders"})])


def apply_change(state: SessionState, bk: dict, days: int, turn: Turn) -> dict:
    q = (state.cart or {}).get("_change_quote")
    if not q or q["days"] != days:
        return quote_change(state, bk, days, turn)
    nb = state.cart["bundle"]
    bk["bundle"] = nb
    bk["total"] = nb["total"] + sum(bk.get("addons_def", {}).get(k, {}).get("price", 0) for k in bk.get("addons", {}))
    bk["dates_text"] = date_range_text(date.fromisoformat(nb["stay"]["check_in"]), date.fromisoformat(nb["stay"]["check_out"]))
    bk["history"].append({"ts": time.time(), "text": f"Даты изменены на {bk['dates_text']}; " +
                          (f"доплата {rub(q['extra'])}" if q["refund"] <= 0 else f"возврат {rub(q['refund'])}")})
    bk["reminders"] = make_reminders(bk)
    issues = planner.check_connections(nb, state.req)
    store.save_booking(bk)
    state.cart = None
    errs = [i for i in issues if i["level"] == "error"]
    return reply(f"Перенёс поездку: {bk['dates_text']}. Состыковки перепроверил — {'всё сходится' if not errs else 'есть ошибки!'}. "
                 f"Напоминания обновил.", [{"type": "booking", "booking": booking_view(bk)}], postsale_chips(bk))


def cancel_quote(bk: dict) -> dict:
    b = bk["bundle"]
    rate = b["stay"]["rate"]
    days_left = (date.fromisoformat(b["stay"]["check_in"]) - today()).days
    notes = []
    refund = 0
    if rate["refundable"] and days_left >= rate["free_cancel_days"]:
        refund += rate["price"]
        notes.append("отель вернёт полностью")
    else:
        notes.append("отель не вернёт деньги (невозвратный тариф или поздняя отмена)")
    for leg in (b["out"], b["back"]):
        if leg["refundable"]:
            refund += leg["total_price"]
        else:
            refund += int(leg["total_price"] * 0.15)
    notes.append("по невозвратным билетам вернутся только сборы (~15%)")
    refund += b["prices"]["transfer"]
    return {"refund": inv.r10(refund), "notes": notes}


# ====================================================================== сводка для UI

# ====================================================================== форма «Что я понял»

WISH_KEYS = ("prefs", "meal", "free_cancel", "direct_only", "max_flight_h", "transport", "stars_min", "beach_close",
             "center_close", "visa_free", "flight_time_pref", "accessible")


def wishes_text(r: TripRequest) -> str:
    """Пожелания словами — так, чтобы их же понял разбор при «Применить»."""
    bits = [PREF_LABELS[p] for p in r.prefs if p in PREF_LABELS]
    if r.max_flight_h:
        bits.append(f"в пути не больше {r.max_flight_h:g} ч")
    if r.direct_only:
        bits.append("без пересадок")
    if r.transport == "train":
        bits.append("поездом")
    if r.meal:
        bits.append(inv.MEAL_NAMES[r.meal])
    if r.free_cancel:
        bits.append("бесплатная отмена")
    if r.stars_min:
        bits.append(f"от {r.stars_min} звёзд")
    if r.visa_free:
        bits.append("без визы")
    if r.beach_close:
        bits.append("первая линия")
    if r.center_close:
        bits.append("в центре")
    if r.flight_time_pref:
        bits.append({"morning": "утренний вылет", "day": "дневной вылет", "evening": "вечерний вылет"}[r.flight_time_pref])
    if r.budget_level == "low" and not r.budget:
        bits.append("недорого")
    if r.accessible:
        bits.append("доступная среда")
    return ", ".join(bits)


def form_values(state: SessionState) -> dict:
    r = state.req
    dest = ""
    if r.destination and r.destination in DEST_BY_ID:
        dest = DEST_BY_ID[r.destination].name
    elif r.country:
        dest = r.country
    date_from, date_to = r.date_from or "", r.date_to or ""
    if state.plan and not date_from:
        date_from, date_to = state.plan["date_from"], state.plan["date_to"]
    return {
        "origin": ORIGIN_BY_CODE[r.origin].name if r.origin and r.origin in ORIGIN_BY_CODE else "",
        "destination": dest, "date_from": date_from, "date_to": date_to,
        "month": r.month or "", "nights": r.nights or (state.plan["nights_requested"] if state.plan else ""),
        "adults": r.adults or "", "children": (len(r.children_ages) + r.children_pending) or "",
        "budget": r.budget or "", "pets": r.pets or "", "wishes": wishes_text(r),
    }


def do_apply_form(state: SessionState, f: dict, turn: Turn) -> dict:
    """Пользователь сам заполнил параметры поездки в панели — пересчитываем подборку под них."""
    r = state.req
    old_dest, old_plan = r.destination, state.plan
    before = r.model_dump()
    turn.think("Пользователь задал параметры в форме — применяю их целиком и подбираю заново")
    upd: dict = {}

    def text(k):
        return str(f.get(k) or "").strip()

    # откуда
    if text("origin"):
        p = nlu.parse("вылет из " + text("origin"))
        if p.get("origin"):
            upd["origin"] = p["origin"]
        else:
            upd["_origin_place"], upd["_origin_strong"] = text("origin"), True
    # куда: пусто — подобрать за пользователя
    r.destination, r.country, r.candidates, r.similar_to, r.similar_label = None, None, [], [], None
    state.unknown_place = None
    if text("destination"):
        p = nlu.parse("хочу в " + text("destination"))
        for k in ("destination", "country", "_unknown_place", "_gateway_note"):
            if k in p:
                upd[k] = p[k]
        if not any(k in p for k in ("destination", "country", "_unknown_place")):
            upd["_unknown_place"] = {"name": text("destination"), "prefs": [], "query": text("destination")}
    # когда
    r.date_from = r.date_to = None
    r.assumptions.pop("when", None)
    if text("date_from"):
        upd["date_from"] = text("date_from")
        if text("date_to"):
            upd["date_to"] = text("date_to")
        upd["flexible"] = False
    elif text("month"):
        try:
            m = int(text("month"))
            upd["month"], upd["year"] = m, nlu._year_for(m, 28)
        except ValueError:
            pass
    if text("nights") and not text("date_to"):
        try:
            upd["nights"] = max(1, int(text("nights")))
        except ValueError:
            pass
    # кто едет
    if text("adults"):
        try:
            upd["adults"] = max(1, min(12, int(text("adults"))))
            r.assumptions.pop("adults", None)
        except ValueError:
            pass
    if "children" in f:  # «Детей»: число; возрасты уже известных детей сохраняем, про новых спросим
        try:
            n = max(0, min(10, int(text("children") or 0)))
        except ValueError:
            n = len(r.children_ages)
        ages = r.children_ages[:n]
        upd["children_ages"], upd["children_pending"] = ages, n - len(ages)
        if n - len(ages) > 0:
            upd["_kids_count_unknown"] = True
        else:
            r.assumptions.pop("children", None)
    # бюджет, питомец и пожелания — только если поля есть в форме (иначе сохраняем то, что понято из диалога)
    if "budget" in f:
        budget = re.sub(r"[^\d]", "", text("budget"))
        r.budget = int(budget) if budget else None
    if "pets" in f:
        r.pets = text("pets") or None
    if "wishes" in f:  # пожелания из формы заменяют прежние
        for k, v in TripRequest().model_dump().items():
            if k in WISH_KEYS:
                setattr(r, k, v)
        r.budget_level = None
    if text("wishes"):
        w = nlu.parse(text("wishes"))
        if llm.enabled():
            w = merge_updates(w, llm.extract(text("wishes"), state.stage, context_text(state), today().isoformat()), text("wishes"))
        for k in WISH_KEYS + ("budget_level",):
            if k in w:
                upd[k] = w[k]
        if w.get("_unknown_place") and "destination" not in upd and "_unknown_place" not in upd and not text("destination"):
            upd["_unknown_place"] = w["_unknown_place"]
    state.pinned, state.cart = {}, None
    for k in ("when", "party", "origin"):
        if k not in state.asked:
            state.asked.append(k)
    if upd.get("children_pending"):  # добавили детей — спросим их возраст (влияет на цену и выбор отелей)
        state.asked = [a for a in state.asked if a != "children_ages"]
    apply_updates(state, upd, turn)
    after = r.model_dump()
    changed = {k for k in after if after[k] != before.get(k) and k not in ("assumptions", "origin_source", "nights_source",
                                                                          "year", "flexible", "candidates", "similar_to",
                                                                          "similar_label")}
    if state.plan and old_plan and r.date_from == old_plan["date_from"] and r.date_to == old_plan["date_to"]:
        changed -= {"date_from", "date_to", "month", "nights"}
    if state.stage in ("booked",):
        state.stage = "start"
    missing = missing_slots(state)
    if missing:
        return ask(state, missing, turn)
    fill_defaults(state, turn)
    if r.destination:
        same = old_plan is not None and old_dest == r.destination
        state.plan = old_plan if same else None
        return do_plan(state, turn, replan=same, reason="Применил ваши параметры" + (": " + describe_changes(changed, r) if same and describe_changes(changed, r) else "") + ".")
    state.plan = None
    return do_destinations(state, turn, reason="Применил ваши параметры — вот что подходит.")


STAGE_LABELS = {"start": "новый", "clarify": "уточняю детали", "destinations": "выбор направления",
                "bundles": "варианты поездки", "cart": "в корзине", "checkout": "оформление", "booked": "забронировано ✓"}


def chat_title(state: SessionState) -> str:
    """Название чата в списке: направление, а пока его нет — начало первого сообщения."""
    r = state.req
    if r.destination:
        try:
            return DEST_BY_ID[r.destination].name
        except KeyError:
            pass
    first = next((h["text"] for h in state.history if h.get("role") == "user" and h.get("text")), "")
    first = hide_codes(" ".join(first.split()))
    if not first:
        return "Новый чат"
    return first if len(first) <= 42 else first[:40].rstrip() + "…"


def summary(state: SessionState) -> dict:
    r = state.req
    slots = []
    def add(key, label, value, source="user", editable=True):
        slots.append({"key": key, "label": label, "value": value, "source": source, "editable": editable})
    if r.origin:
        add("origin", "Откуда", ORIGIN_BY_CODE[r.origin].name, r.origin_source or "user")
    if r.destination:
        add("destination", "Куда", DEST_BY_ID[r.destination].name)
    elif r.country:
        add("country", "Куда", r.country)
    elif state.stage != "start":
        add("destination", "Куда", "подбираю", "agent", False)
    if state.plan and state.stage in ("bundles", "cart", "checkout"):
        add("dates", "Когда", state.plan["dates_text"], "user" if r.date_from and not r.flexible else "agent")
    elif r.date_from:
        d1 = date.fromisoformat(r.date_from)
        txt = date_range_text(d1, date.fromisoformat(r.date_to)) if r.date_to else f"с {ddmm(d1)}"
        add("dates", "Когда", txt)
    elif r.month:
        add("dates", "Когда", MONTH_NAMES[r.month - 1] + (" (даты подберу)" if not state.plan else ""),
            "assumed" if "when" in r.assumptions else "user")
    if state.plan:
        add("nights", "Ночей", str(state.plan["nights_requested"]), "assumed" if "nights" in r.assumptions else "user")
    elif r.nights:
        add("nights", "Ночей", str(r.nights))
    if r.adults is not None or r.has_kids:
        v = f"{r.adults or 2} взр."
        if r.children_ages:
            v += " + дети: " + ", ".join(f"{a} л." for a in r.children_ages)
        if r.children_pending:
            v += f" + {r.children_pending} реб. (возраст?)"
        add("party", "Кто едет", v, "assumed" if "adults" in r.assumptions else "user")
    if r.pets:
        add("pets", "Питомец", {"dog": "собака", "cat": "кошка"}.get(r.pets, "питомец"))
    if r.budget:
        add("budget", "Бюджет", f"до {rub(r.budget)}")
    elif r.budget_level:
        add("budget_level", "Бюджет", {"low": "экономно", "high": "без ограничений", "mid": "средний"}[r.budget_level])
    if r.prefs:
        add("prefs", "Важно", ", ".join(PREF_LABELS.get(p, p) for p in r.prefs if p in PREF_LABELS) or "—", editable=False)
    constraints = []
    if r.max_flight_h:
        constraints.append(f"в пути ≤ {r.max_flight_h:g} ч")
    if r.direct_only:
        constraints.append("без пересадок")
    if r.transport == "train":
        constraints.append("поездом")
    if r.meal:
        constraints.append(inv.MEAL_NAMES[r.meal])
    if r.free_cancel:
        constraints.append("бесплатная отмена")
    if r.stars_min:
        constraints.append(f"от {r.stars_min}★")
    if r.visa_free:
        constraints.append("без визы")
    if r.beach_close:
        constraints.append("первая линия")
    if constraints:
        add("constraints", "Условия", ", ".join(constraints), editable=False)
    cart = None
    if state.cart and state.stage in ("cart", "checkout"):
        b = state.cart["bundle"]
        cart = {"title": f"{DEST_BY_ID[b['dest_id']].name}: {b['hotel']['name']}", "total": b["total"]}
    return {"slots": slots, "priority": r.priority, "stage": state.stage, "cart": cart, "memory": state.memory_applied,
            "assumptions": r.assumptions, "booking_ids": state.booking_ids, "form": form_values(state)}
