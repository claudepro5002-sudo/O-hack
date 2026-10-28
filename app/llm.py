"""Опциональная LLM через OpenAI-совместимый API.

LLM используется для двух вещей:
1) извлечение слотов и намерения из свободного текста (поверх правил nlu.py);
2) ответы на свободные вопросы о направлении («что посмотреть», «что взять»).

Все цены, наличие и проверки делает детерминированный планировщик — LLM не
может «придумать» отель или цену. Без ключа агент полностью работает на правилах.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time

import httpx

from .config import env
from .data.destinations import DESTINATIONS
from .data.geo import ORIGINS

log = logging.getLogger("agent.llm")

INTENTS = ["reset", "greet", "choose", "choose_book", "book", "pay", "shift_dates", "extend", "cheaper", "pricier",
           "show_hotels", "show_flights", "show_destinations", "why", "info", "skip", "early_checkin", "late_checkout",
           "reminders", "cancel_booking", "change_booking", "add_transfer", "add_insurance"]
PREFS = ["sea", "warm", "mild", "culture", "food", "party", "quiet", "romantic", "nature", "mountains", "ski", "shopping",
         "wellness", "diving", "northern_lights", "kids_fun", "city", "surf", "adventure", "pool", "kitchen"]


def enabled() -> bool:
    """LLM включена, только если при запуске заданы ключ, адрес API и модель (в коде их нет)."""
    return all(os.getenv(k) for k in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL")) and \
        env("LLM_ENABLED", "true").lower() not in ("0", "false", "no")


def models() -> list[str]:
    """LLM_MODEL может содержать несколько моделей через запятую — запасные на случай лимитов."""
    return [m.strip() for m in os.getenv("LLM_MODEL", "").split(",") if m.strip()]


def model_name() -> str | None:
    return ", ".join(models()) if enabled() else None


def is_reasoning_model(model: str) -> bool:
    m = model.lower()
    return any(x in m for x in ("gpt-oss", "deepseek-r1", "deepseek-v4", "reasoner", "qwq", ":thinking", "o1", "o3",
                                "o4-mini", "-r1", "nemotron", "gemma-4", "reasoning", "qwen3.5", "qwen3.6", "minimax",
                                "kimi", "glm-4"))


EXTRA_KEYS = ("response_format", "reasoning", "reasoning_effort", "chat_template_kwargs")


def request(model: str, messages: list[dict], max_tokens: int, json_mode: bool, client: httpx.Client) -> httpx.Response:
    """Один запрос к chat/completions с учётом особенностей модели и провайдера."""
    base = os.getenv("LLM_BASE_URL", "").rstrip("/")
    reasoning = is_reasoning_model(model)
    if reasoning:
        max_tokens *= 4  # рассуждающие модели тратят часть лимита на «мысли» — даём запас
    body = {"model": model, "messages": messages, "temperature": 0, "max_tokens": max_tokens}
    if reasoning and "openrouter.ai" in base:
        body["reasoning"] = {"effort": "low", "exclude": True}
    elif reasoning:
        # vLLM-совместимые облака (cloud.ru Foundation Models и др.): «мысли» выключаем — агенту нужен быстрый JSON
        if "gpt-oss" in model.lower():
            body["reasoning_effort"] = "low"
        else:
            body["chat_template_kwargs"] = {"enable_thinking": False, "thinking": False}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {os.getenv('LLM_API_KEY')}", "Content-Type": "application/json"}
    r = client.post(f"{base}/chat/completions", json=body, headers=headers)
    if r.status_code in (400, 422) and any(k in body for k in EXTRA_KEYS):  # сервер не знает доп. параметры
        for k in EXTRA_KEYS:
            body.pop(k, None)
        r = client.post(f"{base}/chat/completions", json=body, headers=headers)
    return r


def strip_thinking(content: str) -> str:
    """Убираем «мысли» модели, если сервер вернул их в тексте ответа."""
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.S)
    return content.split("</think>")[-1].strip()


last_error: str | None = None  # почему последний запрос к LLM не удался — агент честно скажет об этом


def _chat(messages: list[dict], max_tokens: int = 600, json_mode: bool = True, timeout: float | None = None) -> str | None:
    """Запрос к моделям по очереди: ошибка или таймаут одной — пробуем следующую."""
    global last_error
    timeout = timeout or float(env("LLM_TIMEOUT", "30"))
    errors = []
    with httpx.Client(timeout=timeout) as c:
        for model in models():
            try:
                r = request(model, messages, max_tokens, json_mode, c)
            except httpx.TimeoutException:
                errors.append(f"{model}: не ответила за {timeout:.0f} с")
                continue
            except httpx.HTTPError as e:
                errors.append(f"{model}: нет связи с сервисом ({type(e).__name__})")
                continue
            if r.status_code != 200:
                errors.append(f"{model}: HTTP {r.status_code}")
                if r.status_code in (401, 403):
                    break  # ключ не подходит — другие модели не помогут
                continue
            try:
                content = strip_thinking(r.json()["choices"][0]["message"].get("content") or "")
            except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                errors.append(f"{model}: ответ в неожиданном формате")
                continue
            if content.strip():
                last_error = None
                return content
            errors.append(f"{model}: пустой ответ")
    last_error = "; ".join(errors) or "не задана модель"
    log.warning("LLM не ответила: %s", last_error)
    return None


def parse_json(raw: str | None) -> dict | None:
    """JSON-объект из ответа модели. Прощаем частые огрехи: ```json-обёртку, текст вокруг,
    // комментарии и запятую перед закрывающей скобкой."""
    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    text = m.group(0)
    for attempt in (text, re.sub(r",\s*([}\]])", r"\1", re.sub(r"(?m)^\s*//.*$|(?<=[,\[{\s])//[^\n\"]*$", "", text))):
        try:
            data = json.loads(attempt)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            continue
    return None


def _system_prompt(today_iso: str) -> str:
    dests = ", ".join(f"{d.id}={d.name} ({d.country})" for d in DESTINATIONS)
    origins = ", ".join(f"{o.code}={o.name}" for o in ORIGINS)
    return f"""Ты — модуль понимания запросов тревел-агента. Сегодня {today_iso}.
Верни ТОЛЬКО JSON-объект с полями, которые явно следуют из последнего сообщения пользователя (остальные не включай):
origin: код города вылета из [{origins}]
destination: id направления из [{dests}]
country: страна, если названа страна, а не город (например "Турция", "Россия")
avoid: список стран/id направлений, которые пользователь исключил
date_from, date_to: YYYY-MM-DD; month: 1-12; year; nights: число ночей
adults: взрослых; children_ages: список возрастов детей; children_pending: число детей без возраста
pets: "dog" | "cat" | "pet" | null (null — если сказал, что питомец не едет)
budget: общий бюджет в рублях (если «на человека» — поставь _budget_per_person: true)
budget_level: "low" | "high"
prefs: подмножество {PREFS}
meal: "RO"|"BB"|"HB"|"AI"; free_cancel: bool; direct_only: bool; max_flight_h: число часов; transport: "plane"|"train"
stars_min: 2-5; beach_close: bool; center_close: bool; visa_free: bool; priority: "price"|"balance"|"comfort"|"time"
flight_time_pref: "morning"|"day"|"evening"
_intent: одно из {INTENTS} или не указывай. shift_dates → _shift: дни (+позже/−раньше); extend → _extend: ±ночей.
choose → _choice_idx (0-based) или _choice_code ("optimal"|"economy"|"comfort").
_origin_place: если пользователь назвал город вылета, которого нет в списке кодов выше, — его название в
именительном падеже (например «Магнитогорск»).
_unknown_place: если пользователь назвал место (город, курорт, остров, страну, регион), которого нет в списке
направлений выше, — его название в именительном падеже (например «Рейкьявик», «Италия», «Антарктида»).
«без долгих перелётов» → max_flight_h 5.5. Не выдумывай значения."""


def extract(text: str, stage: str, context: str, today_iso: str) -> dict | None:
    if not enabled():
        return None
    t0 = time.time()
    raw = _chat([
        {"role": "system", "content": _system_prompt(today_iso)},
        {"role": "user", "content": f"Этап диалога: {stage}. Что уже известно: {context}\nСообщение: {text}"},
    ])
    data = parse_json(raw)
    if data is None:
        return None
    log.info("LLM extract %.2fs: %s", time.time() - t0, data)
    return _validate(data)


def _validate(d: dict) -> dict:
    ok: dict = {}
    ids = {x.id for x in DESTINATIONS}
    codes = {o.code for o in ORIGINS}
    for k, v in d.items():
        if v is None and k != "pets":
            continue
        if k == "origin" and v in codes:
            ok[k] = v
        elif k == "destination" and v in ids:
            ok[k] = v
        elif k in ("country",) and isinstance(v, str):
            ok[k] = v
        elif k == "avoid" and isinstance(v, list):
            ok[k] = [str(x) for x in v]
        elif k in ("date_from", "date_to") and isinstance(v, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", v):
            ok[k] = v
        elif k in ("month", "year", "nights", "adults", "children_pending", "budget", "stars_min", "_shift", "_extend",
                   "_choice_idx") and isinstance(v, (int, float)):
            ok[k] = int(v)
        elif k == "children_ages" and isinstance(v, list):
            ok[k] = [int(x) for x in v if isinstance(x, (int, float)) and 0 <= x < 18]
        elif k == "pets" and v in ("dog", "cat", "pet", None):
            ok[k] = v
        elif k == "budget_level" and v in ("low", "mid", "high"):
            ok[k] = v
        elif k == "prefs" and isinstance(v, list):
            ok[k] = [p for p in v if p in PREFS]
        elif k == "meal" and v in ("RO", "BB", "HB", "AI"):
            ok[k] = v
        elif k in ("free_cancel", "direct_only", "beach_close", "center_close", "visa_free", "_budget_per_person") and isinstance(v, bool):
            ok[k] = v
        elif k == "max_flight_h" and isinstance(v, (int, float)) and 1 <= v <= 30:
            ok[k] = float(v)
        elif k == "transport" and v in ("plane", "train", "any"):
            ok[k] = v
        elif k == "priority" and v in ("price", "balance", "comfort", "time"):
            ok[k] = v
        elif k == "flight_time_pref" and v in ("morning", "day", "evening"):
            ok[k] = v
        elif k == "_intent" and v in INTENTS:
            ok[k] = v
        elif k in ("_unknown_place", "_origin_place") and isinstance(v, str) and v.strip():
            ok[k] = v.strip()[:60]
        elif k == "_choice_code" and v in ("optimal", "economy", "comfort"):
            ok[k] = v
    return ok


def answer(question: str, facts: str) -> str | None:
    if not enabled():
        return None
    raw = _chat([
        {"role": "system", "content": "Ты — дружелюбный тревел-агент. Отвечай по-русски, кратко (2–5 предложений), опирайся "
                                      "только на факты ниже, не выдумывай цены, наличие и даты. Ты НЕ меняешь условия поездки "
                                      "и ничего не бронируешь сам — это делает система, поэтому никогда не пиши «поменял», "
                                      "«изменил», «забронировал». Если пользователь просит что-то изменить, а в фактах этого нет, "
                                      "скажи, что не смог разобрать просьбу, и предложи сформулировать её короче. Не показывай "
                                      "служебные коды и идентификаторы. Факты:\n" + facts},
        {"role": "user", "content": question},
    ], max_tokens=400, json_mode=False)
    return raw.strip() if raw else None


PLACE_PROMPT = """Ты — справочник путешествий. Пользователь из России назвал место: «{q}».
Верни ТОЛЬКО JSON со сведениями (реалистичные средние значения, без выдумок):
{{
 "kind": "place" — конкретный город/курорт/остров, куда можно поехать;
         "region" — страна или большой регион (например «Италия», «Европа», «Карибы»);
         "unreachable" — туда не ездят обычные туристы (Антарктида, Северный полюс, космос, зона войны),
 "name": "название по-русски", "name_loc": "в Рейкьявике / на Крите", "name_acc": "в Рейкьявик / на Крит",
 "name_gen": "родительный падеж для «из …»: Рейкьявика",
 "country": "страна по-русски", "cc": "ISO-код страны из 2 букв",
 "lat": широта, "lon": долгота, "iata": "код ближайшего аэропорта", "airport_km": км от аэропорта до центра,
 "tz": смещение UTC зимой (число),
 "types": подмножество ["beach","city","nature","ski"],
 "tags": подмножество ["sea","warm","culture","food","party","quiet","romantic","nature","mountains","ski","shopping",
          "wellness","diving","northern_lights","kids","budget","luxury","all_inclusive","surf","adventure"],
 "air_max": [12 чисел — средний дневной максимум °C по месяцам с января],
 "sea": [12 чисел — температура моря у берега] или null, если моря нет,
 "rain_days": [12 чисел — дождливых дней в месяце],
 "best_months": [лучшие месяцы для поездки], "ski_months": [], "swim_months": [],
 "areas": [2–4 района для проживания: {{"name": "...", "airport_km": 20, "beach_m": 100 или null, "center_km": 1.5,
          "vibe": подмножество ["family","quiet","lively","party","luxury","budget","city","culture","food","nature","ski"]}}],
 "highlights": [5 конкретных мест и занятий],
 "hotel_price_rub": цена ночи в номере на двоих 3–4* в рублях,
 "daily_spend_rub": расходы на месте в день на человека в рублях,
 "direct_flights_from_moscow": есть ли сейчас прямые рейсы из Москвы (true/false),
 "visa_note": "визовый режим для граждан РФ, кратко",
 "risks": {{"номер месяца": "сезонный риск: дожди, тайфуны, жара, закрытие отелей"}},
 "suggest": для region и unreachable — 3–4 конкретных места-названия, куда реально поехать (для unreachable — откуда туда
           отправляются или что ближе всего по духу), иначе [],
 "note": одно предложение-пояснение для пользователя (особенно для region/unreachable)
}}"""


def place_card(query: str) -> dict | None:
    """Сведения о любом месте мира от LLM (для направлений вне ручного каталога)."""
    if not enabled():
        return None
    global last_error
    # карточка места длинная — большим моделям даём больше времени, чем на разбор фразы
    raw = _chat([{"role": "system", "content": "Отвечай только валидным JSON."},
                 {"role": "user", "content": PLACE_PROMPT.format(q=query[:80])}], max_tokens=1600,
                timeout=max(60.0, float(env("LLM_TIMEOUT", "30"))))
    if not raw:
        return None
    card = parse_json(raw)
    if card is None:
        last_error = "модель вернула сведения о месте не в формате JSON"
        log.warning("place_card %r: не JSON: %s", query, raw[:300])
        return None
    log.info("place_card %r: kind=%s name=%s suggest=%s", query, card.get("kind"), card.get("name"), card.get("suggest"))
    return card


SUGGEST_PROMPT = """Ты — эксперт по путешествиям. Турист из России (вылет: {origin}) ищет поездку: {summary}.
Предложи 5 конкретных мест в мире (город, курорт или остров — не страну), которые лучше всего подходят под запрос
в эти даты, включая неочевидные. Учитывай сезон и погоду, визы и прямые рейсы для граждан РФ, бюджет и состав.
Верни ТОЛЬКО JSON: {{"places": ["Название по-русски", ...]}}"""


def suggest_places(summary: str, origin: str) -> list[str]:
    """Идеи направлений со всего мира для запроса без конкретного места."""
    if not enabled():
        return []
    raw = _chat([{"role": "system", "content": "Отвечай только валидным JSON."},
                 {"role": "user", "content": SUGGEST_PROMPT.format(summary=summary[:400], origin=origin)}], max_tokens=300)
    places = (parse_json(raw) or {}).get("places", [])
    if not isinstance(places, list):
        return []
    return [str(p).strip()[:60] for p in places if isinstance(p, str) and p.strip()][:5]

