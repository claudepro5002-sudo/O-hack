"""Любое место мира через LLM (ответы модели подменены)."""
import json
import re

from conftest import llm_env

from app import agent, llm, store
from app.data.destinations import DEST_BY_ID
from app.state import SessionState

AIR = [2, 3, 3, 6, 9, 12, 14, 13, 11, 7, 4, 2]


def card(name, cc, lat, lon, kind="place", suggest=(), sea=None, types=("city",)):
    return {"kind": kind, "name": name, "name_loc": f"в {name}е", "name_acc": f"в {name}", "country": "", "cc": cc,
            "lat": lat, "lon": lon, "iata": "XXX", "airport_km": 40, "tz": 0, "types": list(types), "tags": ["culture", "nature"],
            "air_max": AIR, "sea": sea, "rain_days": [10] * 12, "best_months": [6, 7, 8],
            "areas": [{"name": "Центр", "airport_km": 45, "beach_m": None, "center_km": 0.5, "vibe": ["city"]}],
            "highlights": ["Голубая лагуна", "Гейзер Строккюр"], "hotel_price_rub": 15000, "daily_spend_rub": 6000,
            "direct_flights_from_moscow": False, "visa_note": "нужна виза", "risks": {"12": "очень короткий день"},
            "suggest": list(suggest), "note": "пояснение"}


CARDS = {
    "Рейкьявик": {**card("Рейкьявик", "IS", 64.15, -21.94), "tags": ["nature", "northern_lights", "culture"]},
    "Италия": card("Италия", "IT", 42, 12, kind="region", suggest=["Рим", "Венеция"]),
    "Рим": card("Рим", "IT", 41.9, 12.5, sea=[14] * 12),
    "Венеция": card("Венеция", "IT", 45.44, 12.33),
    "Луна": card("Луна", "", 0, 0, kind="unreachable", suggest=["Ушуая", "Мурманск"]),
    "Ушуая": card("Ушуая", "AR", -54.8, -68.3),
}


SUGGEST: list[str] = []


def fake_llm(monkeypatch, extract):
    llm_env(monkeypatch)

    def chat(messages, **_):
        text = messages[-1]["content"]
        if "Предложи 5 конкретных мест" in text:
            return json.dumps({"places": SUGGEST}, ensure_ascii=False)
        m = re.search(r"назвал место: «(.+?)»", text)
        if m:
            q = m.group(1).lower()
            name = next((k for k in CARDS if k.lower()[:6] == q[:6]), None)
            return json.dumps(CARDS[name], ensure_ascii=False) if name else None
        return json.dumps(extract(text) if callable(extract) else extract, ensure_ascii=False)
    monkeypatch.setattr(llm, "_chat", chat)


def test_any_city_becomes_full_trip(monkeypatch):
    store.reset_for_tests()
    fake_llm(monkeypatch, {"_unknown_place": "Рейкьявик", "month": 2, "adults": 2})
    s = SessionState(user_id="w1")
    out = agent.handle(s, "Хочу в Рейкьявик в феврале вдвоём")
    assert s.stage == "bundles", out["text"]
    d = DEST_BY_ID[s.req.destination]
    assert d.name == "Рейкьявик" and d.source == "llm" and d.visa == "visa"
    b = s.plan["bundles"][0]
    assert b["out"]["stops"] == 1 and b["out"]["via"] in ("Стамбул", "Белград", "Дубай")  # прямых рейсов нет
    assert not [i for i in b["issues"] if i["level"] == "error"]
    assert any(t.get("tool") == "llm_place_card" for t in out["trace"])
    # кэш: при повторе LLM за карточкой не ходим
    monkeypatch.setattr(llm, "place_card", lambda q: (_ for _ in ()).throw(AssertionError("кэш не сработал")))
    s2 = SessionState(user_id="w2")
    agent.handle(s2, "Хочу в Рейкьявик в феврале вдвоём")
    assert s2.req.destination == s.req.destination


def test_region_gives_concrete_places(monkeypatch):
    store.reset_for_tests()
    fake_llm(monkeypatch, {"_unknown_place": "Италия", "month": 6, "adults": 2})
    s = SessionState(user_id="w3")
    out = agent.handle(s, "хочу в Италию в июне вдвоём")
    assert s.stage == "destinations"
    names = [d["name"] for d in out["blocks"][0]["items"]]
    assert set(names) == {"Рим", "Венеция"}
    assert "Италия" in out["text"]


def test_unreachable_place_is_honest(monkeypatch):
    store.reset_for_tests()
    fake_llm(monkeypatch, {"_unknown_place": "Луна", "month": 1, "adults": 2})
    s = SessionState(user_id="w4")
    out = agent.handle(s, "хочу на луну в январе вдвоём")
    names = [d["name"] for d in out["blocks"][0]["items"]]
    assert "Мурманск и Териберка" in names and any(n.startswith("Ушуая") for n in names)
    assert "Луна" in out["text"]


def test_without_llm_falls_back_to_catalog(monkeypatch):
    store.reset_for_tests()
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    s = SessionState(user_id="w5")
    out = agent.handle(s, "Хочу поехать в Гренландию летом вдвоём")
    assert "не знаю" in out["text"] and "LLM" in out["text"] and s.stage == "destinations"


CARDS["Магнитогорск"] = {**card("Магнитогорск", "RU", 53.41, 58.98), "name_gen": "Магнитогорска", "tz": 5}


def test_any_origin_city_via_llm(monkeypatch):
    store.reset_for_tests()
    fake_llm(monkeypatch, lambda text: {"_origin_place": "Магнитогорск"} if "Магнитогорск" in text else {})
    s = SessionState(user_id="w6")
    agent.handle(s, "Хургада в декабре на неделю вдвоём")
    out = agent.handle(s, "меняем город вылета на Магнитогорск")
    from app.data.geo import ORIGIN_BY_CODE
    assert ORIGIN_BY_CODE[s.req.origin].name == "Магнитогорск"
    b = s.plan["bundles"][0]
    assert b["out"]["from_name"] == "Магнитогорск" and b["out"]["stops"] == 1  # прямых нет — через Москву
    assert "из Магнитогорска" in out["text"] and not re.search(r"\b[xo]-[0-9a-f]{10}\b", out["text"])


def test_unknown_origin_without_llm_is_honest(monkeypatch):
    store.reset_for_tests()
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    s = SessionState(user_id="w7")
    out = agent.handle(s, "мы из Магнитогорска, хотим в Хургаду в декабре вдвоём")
    assert "Магнитогорска" in out["text"] and "пока не знаю" in out["text"]
    assert s.req.origin != "MOW" or s.req.origin_source == "assumed"


def test_llm_never_sees_codes(monkeypatch):
    store.reset_for_tests()
    fake_llm(monkeypatch, {"_unknown_place": "Италия", "month": 6, "adults": 2})
    s = SessionState(user_id="w8")
    agent.handle(s, "хочу в Италию в июне вдвоём")
    ctx = agent.context_text(s)
    assert "Рим" in ctx and not re.search(r"\b[xo]-[0-9a-f]{10}\b", ctx)
    rid = s.dest_options[0]["dest_id"]
    assert agent.hide_codes(f"выберите {rid}") == f"выберите {s.dest_options[0]['name']}"


def test_llm_confusing_antarctica_with_antalya_is_ignored(monkeypatch):
    store.reset_for_tests()
    fake_llm(monkeypatch, lambda text: {"destination": "antalya", "month": 1, "adults": 2} if "антаркт" in text.lower()
             else {})
    s = SessionState(user_id="w9")
    agent.handle(s, "Хургада в декабре на неделю вдвоём")
    out = agent.handle(s, "хочу поехать в антарктиду")
    assert s.req.destination == "ushuaia"
    assert "Антарктиду" in out["text"]


def test_unknown_place_replaces_current_destination_without_llm(monkeypatch):
    store.reset_for_tests()
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    s = SessionState(user_id="w10")
    agent.handle(s, "Хургада в декабре на неделю вдвоём")
    out = agent.handle(s, "хочу в гренландию")
    assert s.req.destination is None and s.stage == "destinations"
    assert "Арктика" in out["text"]
    agent.handle(s, "Хургада в декабре на неделю вдвоём")
    agent.handle(s, "хочу в антарктиду")
    assert s.req.destination == "ushuaia"


def test_llm_repeating_old_place_does_not_block_new_request(monkeypatch):
    store.reset_for_tests()
    llm_env(monkeypatch)
    s = SessionState(user_id="t-llm-new")
    monkeypatch.setattr(llm, "_chat", lambda *a, **k: None)
    agent.handle(s, "Анталья в июле вдвоём на неделю из Москвы")
    assert s.req.destination == "antalya"
    # LLM по контексту повторила прежнее направление и приняла «кемпинг» за место
    monkeypatch.setattr(llm, "_chat", lambda *a, **k: json.dumps(
        {"destination": "antalya", "_unknown_place": "кемпинг", "prefs": ["nature"]}))
    agent.handle(s, "составь поездку на кемпинг")
    assert s.req.destination is None and s.stage == "destinations" and s.unknown_place is None


def test_llm_failure_is_reported_honestly(monkeypatch):
    store.reset_for_tests()
    llm_env(monkeypatch)

    def busy(*a, **k):
        llm.last_error = "m1: HTTP 429"
        return None
    monkeypatch.setattr(llm, "_chat", busy)
    s = SessionState(user_id="w-fail")
    out = agent.handle(s, "Хочу в Италию в июне вдвоём из Москвы")
    assert "Не получилось узнать у LLM" in out["text"] and "Итали" in out["text"] and "каталоге пока нет" not in out["text"]
    assert any("HTTP 429" in str(t) for t in out["trace"])


def test_chat_tries_next_model_after_timeout(monkeypatch):
    import httpx
    llm_env(monkeypatch)
    monkeypatch.setenv("LLM_MODEL", "slow,fast")

    def fake_request(model, *a, **k):
        if model == "slow":
            raise httpx.ReadTimeout("timeout")
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": 1}'}}]})
    monkeypatch.setattr(llm, "request", fake_request)
    assert llm._chat([{"role": "user", "content": "x"}]) == '{"ok": 1}' and llm.last_error is None


def test_bad_cached_card_is_refetched_and_region_without_suggest_asks_ideas(monkeypatch):
    store.reset_for_tests()
    # раньше модель прислала неполную карточку — она лежит в кэше и не должна мешать
    store.save_place("q:италию", {"kind": "place", "name": "Италия"})
    global SUGGEST
    SUGGEST = ["Рим", "Венеция"]
    saved = CARDS["Италия"]
    CARDS["Италия"] = {**saved, "suggest": []}  # страна без конкретных мест — идеи спросим отдельно
    try:
        fake_llm(monkeypatch, {"_unknown_place": "Италия", "month": 6, "adults": 2, "origin": "MOW"})
        s = SessionState(user_id="w-it")
        out = agent.handle(s, "Хочу в Италию в июне вдвоём из Москвы")
    finally:
        CARDS["Италия"], SUGGEST = saved, []
    names = [d["name"] for d in s.dest_options]
    assert "Рим" in names and "Венеция" in names, out["text"]
    assert "Не получилось" not in out["text"] and "каталоге пока нет" not in out["text"]


def test_parse_json_forgives_model_quirks():
    assert llm.parse_json('```json\n{"a": [1, 2,], // коммент\n "b": "https://x.ru"}\n```') == {"a": [1, 2], "b": "https://x.ru"}
    assert llm.parse_json("не JSON") is None
