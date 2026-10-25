from app.nlu import names_from_text, parse


def test_vague_request():
    u = parse("хочу куда-нибудь к тёплому морю в ноябре, недорого, с ребёнком, без долгих перелётов")
    assert u["month"] == 11
    assert {"sea", "warm"} <= set(u["prefs"])
    assert u["budget_level"] == "low"
    assert u["children_pending"] == 1
    assert u["max_flight_h"] == 5.5
    assert "_intent" not in u


def test_exact_dates_family_budget():
    u = parse("с 10 по 17 ноября в Дубай, двое взрослых и двое детей 4 и 9 лет, до 300к")
    assert (u["date_from"], u["date_to"]) == ("2026-11-10", "2026-11-17")
    assert u["adults"] == 2 and u["children_ages"] == [4, 9]
    assert u["budget"] == 300_000
    assert u["destination"] == "dubai"


def test_origin_vs_destination():
    u = parse("На новогодние в Стамбул на 5 дней из Казани")
    assert u["origin"] == "KZN" and u["destination"] == "istanbul"
    assert u["date_from"] == "2026-12-30"


def test_country_and_avoid():
    assert parse("хочу в Турцию всё включено")["country"] == "Турция"
    u = parse("не Египет, а где можно купаться в феврале, без визы")
    assert "Египет" in u["avoid"] and u["visa_free"] is True and "destination" not in u


def test_pets_and_train():
    u = parse("Едем с собакой на море в сентябре, поездом")
    assert u["pets"] == "dog" and u["transport"] == "train"
    assert parse("собаку оставим дома").get("pets") is None


def test_budget_per_person_and_not_hours():
    u = parse("нас 4 человека, бюджет 80 000 на человека, не больше 4 часов лёта")
    assert u["budget"] == 80_000 and u["_budget_per_person"]
    assert u["adults"] == 4 and u["max_flight_h"] == 4


def test_replan_intents():
    assert parse("а если на два дня позже?") == {"_intent": "shift_dates", "_shift": 2}
    assert parse("на неделю раньше")["_shift"] == -7
    assert parse("бюджет меньше")["_intent"] == "cheaper"
    assert parse("на 2 дня дольше")["_extend"] == 2
    assert parse("с нами едет собака")["pets"] == "dog"


def test_choice_in_context():
    assert parse("давай второй", "destinations")["_choice_idx"] == 1
    assert parse("беру оптимальный", "bundles")["_choice_code"] == "optimal"


def test_children_ages_answer():
    assert parse("5 и 8", "clarify", "children_ages")["children_ages"] == [5, 8]
    assert parse("ребенку 3 года")["children_ages"] == [3]
    assert parse("с 5-летним сыном")["children_ages"] == [5]


def test_skiing_does_not_match_kata():
    u = parse("кататься на лыжах в феврале из Новосибирска")
    assert "destination" not in u and "ski" in u["prefs"] and u["origin"] == "OVB"


def test_names():
    assert names_from_text("Иван Петров, Мария Петрова и Петя Петров") == ["Иван Петров", "Мария Петрова", "Петя Петров"]


def test_dates_with_years_and_long_ranges():
    assert parse("с 1 июня 2027 по 1 июня 2028")["date_to"] == "2028-06-01"
    assert parse("с 1 июня по 1 июня следующего года")["date_to"] == "2028-06-01"
    u = parse("с 30 по 3 января")
    assert (u["date_from"], u["date_to"]) == ("2026-12-30", "2027-01-03")
    assert parse("с 01.06.2027 по 01.06.2028")["date_to"] == "2028-06-01"
    assert parse("1-10 июня, бюджет 2027 рублей")["date_to"] == "2027-06-10"
