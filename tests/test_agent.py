from app import agent, store
from app.state import SessionState


def run(state, *steps):
    out = None
    for s in steps:
        out = agent.handle(state, None, s) if isinstance(s, dict) else agent.handle(state, s)
    return out


def test_single_clarification_then_destinations():
    store.reset_for_tests()
    s = SessionState(user_id="t-a")
    out = run(s, "хочу к тёплому морю в ноябре с ребёнком")
    assert s.stage == "clarify" and "лет" in out["text"]
    out = run(s, "ребенку 4 года")
    assert s.stage == "destinations" and out["blocks"][0]["type"] == "destinations"
    assert s.clarifications == 1


def test_full_path_to_booking_and_postsale():
    store.reset_for_tests()
    s = SessionState(user_id="t-b")
    run(s, "Хургада в декабре на неделю вдвоём")
    assert s.stage == "bundles"
    out = run(s, "а если на два дня позже?")
    assert any(b["type"] == "diff" for b in out["blocks"])
    run(s, "беру оптимальный")
    assert s.stage == "cart"
    run(s, {"type": "checkout"})
    out = run(s, "Иван Петров, Мария Петрова")
    assert s.stage == "booked" and s.active_booking
    bk = store.get_booking(s.active_booking)
    assert bk["status"] == "confirmed" and len(bk["travelers"]) == 2
    out = run(s, {"type": "change_booking", "days": 1})
    assert "Итого" in out["text"]
    out = run(s, {"type": "confirm_change", "days": 1})
    assert store.get_booking(s.active_booking)["history"][-1]["text"].startswith("Даты изменены")
    out = run(s, {"type": "reminders"})
    assert out["blocks"][0]["type"] == "reminders"


def test_memory_between_sessions():
    store.reset_for_tests()
    s = SessionState(user_id="t-mem")
    run(s, "из Казани в Сочи в августе, едем с женой и сыном 6 лет", {"type": "choose_bundle", "idx": 0},
        {"type": "checkout"}, "Иван Петров, Анна Петрова, Миша Петров")
    assert s.stage == "booked"
    s2 = SessionState(user_id="t-mem")
    w = agent.welcome(s2)
    assert "Помню" in w["text"] and s2.req.origin == "KZN"
    run(s2, "хотим на море в июне с ребенком")
    # возраст ребёнка взят из памяти — уточнять не нужно
    assert s2.req.children_ages == [6] and s2.stage == "destinations"


def test_dog_on_banned_destination_replans():
    store.reset_for_tests()
    s = SessionState(user_id="t-dog")
    run(s, "Бали в декабре на 10 ночей вдвоём")
    assert s.req.destination == "bali"
    out = run(s, "с нами едет собака")
    assert s.req.destination is None and s.stage == "destinations"
    assert "запрещён" in out["text"]


def test_unknown_place_is_honest_and_similar():
    store.reset_for_tests()
    s = SessionState(user_id="t-ant")
    out = run(s, "Хочу поехать в Гренландию летом с детьми", "5 и 9")
    assert "нет" in out["text"] and "Арктика" in out["text"]
    ids = [d["dest_id"] for d in out["blocks"][0]["items"]]
    assert ids[0] in ("kamchatka", "murmansk", "baikal", "altai") and "sochi" not in ids[:3]


def test_antarctica_goes_through_ushuaia():
    store.reset_for_tests()
    s = SessionState(user_id="t-ant2")
    out = run(s, "хочу в антарктиду в январе вдвоём")
    assert s.req.destination == "ushuaia" and s.stage == "bundles"
    assert "Антарктиду" in out["text"] and "Ушуаи" in out["text"]
    out = run(s, "а если в июле?")
    assert "не ходят" in out["text"]


def test_silent_priority_switch_has_no_chat_message():
    store.reset_for_tests()
    s = SessionState(user_id="t-sil")
    run(s, "Хургада в декабре на неделю вдвоём")
    hist = len(s.history)
    out = agent.handle(s, None, {"type": "set", "key": "priority", "value": "price", "silent": True})
    assert out["silent"] and s.req.priority == "price" and s.stage == "bundles"
    assert not any(b["type"] == "diff" for b in out["blocks"])
    assert "Учёл" not in out["text"] and len(s.history) == hist
    # до начала подбора переключение просто запоминается
    s2 = SessionState(user_id="t-sil2")
    out = agent.handle(s2, None, {"type": "set", "key": "priority", "value": "comfort", "silent": True})
    assert out["text"] == "" and s2.req.priority == "comfort" and s2.stage == "start"


def test_bad_years_from_llm_are_fixed(monkeypatch):
    import json

    from conftest import llm_env

    from app import llm
    store.reset_for_tests()
    llm_env(monkeypatch)
    # модель перепутала годы: начало в прошлом, конец через год
    monkeypatch.setattr(llm, "_chat", lambda *a, **k: json.dumps({"date_from": "2026-06-01", "date_to": "2027-06-11", "adults": 2}))
    s = SessionState(user_id="t-dates")
    out = agent.handle(s, "Сочи летом на десять дней, вдвоём")
    assert (s.req.date_from, s.req.date_to) == ("2027-06-01", "2027-06-11")
    assert "374" not in out["text"]


def test_rule_dates_beat_llm(monkeypatch):
    import json

    from conftest import llm_env

    from app import llm
    store.reset_for_tests()
    llm_env(monkeypatch)
    monkeypatch.setattr(llm, "_chat", lambda *a, **k: json.dumps({"date_from": "2026-06-01", "date_to": "2026-06-11"}))
    s = SessionState(user_id="t-dates2")
    agent.handle(s, "Сочи с 1 по 11 июня вдвоём")
    assert (s.req.date_from, s.req.date_to) == ("2027-06-01", "2027-06-11") and s.req.nights == 10


def test_long_trips_allowed_and_counted():
    store.reset_for_tests()
    s = SessionState(user_id="t-long")
    run(s, "Сочи с 1 июня по 31 августа вдвоём")
    assert (s.req.date_from, s.req.date_to, s.req.nights) == ("2027-06-01", "2027-08-31", 91)
    assert s.stage == "bundles" and s.plan["nights_requested"] == 91
    s2 = SessionState(user_id="t-long2")
    run(s2, "Бали на 120 дней с 1 декабря вдвоём")
    assert s2.req.nights == 120 and s2.plan["nights_requested"] == 120


def test_trip_form_apply():
    store.reset_for_tests()
    s = SessionState(user_id="t-form")
    agent.handle(s, "тёплое море, всё включено, без визы, бюджет 300 000")
    form = {"origin": "Казань", "destination": "", "date_from": "2027-02-10", "date_to": "2027-02-17", "adults": "2",
            "children": "1"}
    out = agent.handle(s, None, {"type": "apply_form", "values": form})
    assert s.stage == "clarify" and "лет" in out["text"]  # добавили ребёнка — агент спрашивает возраст
    out = agent.handle(s, "6 лет")
    assert s.stage == "destinations" and s.req.origin == "KZN" and s.req.children_ages == [6] and s.req.month == 2
    # бюджет и пожелания из диалога сохраняются — в форме их нет
    assert s.req.budget == 300000 and s.req.meal == "AI" and s.req.visa_free and "sea" in s.req.prefs
    assert out["blocks"][0]["type"] == "destinations"
    # конкретное место → сразу пакеты; потом меняем только детей → пересборка с диффом
    form = {k: v for k, v in agent.summary(s)["form"].items() if k in form} | {"destination": "Шарм-эш-Шейх"}
    agent.handle(s, None, {"type": "apply_form", "values": form})
    assert s.stage == "bundles" and s.req.destination == "sharm"
    form = {k: v for k, v in agent.summary(s)["form"].items() if k in form} | {"children": "2"}
    agent.handle(s, None, {"type": "apply_form", "values": form})
    out = agent.handle(s, "10 лет")
    assert s.req.children_ages[0] == 6 and len(s.req.children_ages) == 2 and s.stage == "bundles"
    form = {k: v for k, v in agent.summary(s)["form"].items() if k in form} | {"adults": "3"}
    out = agent.handle(s, None, {"type": "apply_form", "values": form})
    assert any(b["type"] == "diff" for b in out["blocks"]) and out["text"].startswith("Применил ваши параметры: состав")
    # пустое «Куда» — снова подбор направлений
    form = {k: v for k, v in agent.summary(s)["form"].items() if k in form} | {"destination": ""}
    agent.handle(s, None, {"type": "apply_form", "values": form})
    assert s.req.destination is None and s.stage == "destinations" and s.req.meal == "AI"


def test_new_request_restarts_search():
    store.reset_for_tests()
    s = SessionState(user_id="t-newtrip")
    agent.handle(s, "Хочу в Антарктиду в январе вдвоём из Москвы")
    assert s.req.destination == "ushuaia" and s.stage == "bundles"
    out = agent.handle(s, "составь поездку на кемпинг")
    assert s.req.destination is None and s.stage == "destinations" and "nature" in s.req.prefs
    assert "Новая поездка" in out["text"] and "Ушуа" not in out["text"].split("\n")[0]
    assert all(d["dest_id"] != "ushuaia" for d in s.dest_options[:1])
    assert s.req.origin == "MOW" and s.req.adults == 2 and s.req.month == 1  # остальное сохранили


def test_refinement_keeps_trip():
    store.reset_for_tests()
    s = SessionState(user_id="t-refine")
    agent.handle(s, "Анталья в июле вдвоём на неделю из Москвы")
    assert s.req.destination == "antalya"
    agent.handle(s, "хочу на море, и чтобы было тихо")
    assert s.req.destination == "antalya" and s.stage == "bundles"
    agent.handle(s, "подбери подешевле")
    assert s.req.destination == "antalya"
