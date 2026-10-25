"""Планировщик: ранжирование направлений, сборка пакетов, проверка состыковок.

Здесь вся «механика» агента: инструменты поиска вызываются отсюда,
а модуль agent.py решает, когда и что вызвать и как объяснить результат.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from . import inventory as inv
from .data.destinations import DEST_BY_ID, DESTINATIONS, MONTH_GEN, Destination
from .data.geo import ORIGIN_BY_CODE, ORIGIN_SAME_DEST, flight_minutes, haversine_km, hub_point
from .state import TripRequest, today

WEIGHTS = {
    # price — деньги, hotel — отель под вас, transport — удобство рейсов, time — время «от вылета до двери отеля»
    "price": {"price": 0.65, "hotel": 0.17, "transport": 0.1, "time": 0.08},
    "balance": {"price": 0.35, "hotel": 0.35, "transport": 0.18, "time": 0.12},
    "comfort": {"price": 0.08, "hotel": 0.55, "transport": 0.25, "time": 0.12},
    "time": {"price": 0.12, "hotel": 0.18, "transport": 0.15, "time": 0.55},
}


# ---------------------------------------------------------------- утилиты

def rub(x: float | int) -> str:
    return f"{int(round(x)):,}".replace(",", " ") + " ₽"


def deg(v: int) -> str:
    return f"+{v}°" if v > 0 else (f"−{abs(v)}°" if v < 0 else "0°")


def hours(mins: int) -> str:
    h, m = divmod(int(mins), 60)
    if h and m:
        return f"{h} ч {m:02d} мин"
    return f"{h} ч" if h else f"{m} мин"


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def ddmm(d: date) -> str:
    return f"{d.day} {MONTH_GEN[d.month - 1]}"


def date_range_text(d1: date, d2: date) -> str:
    if d1.year != d2.year and (d2 - d1).days > 60:
        return f"{ddmm(d1)} {d1.year} – {ddmm(d2)} {d2.year}"
    if d1.month == d2.month and d1.year == d2.year:
        return f"{d1.day}–{d2.day} {MONTH_GEN[d2.month - 1]}"
    return f"{ddmm(d1)} – {ddmm(d2)}"


def default_nights(req: TripRequest, dest: Destination | None = None) -> int:
    if req.nights:
        return req.nights
    prefs = set(req.prefs)
    city_pref = bool({"culture", "food", "city", "party", "shopping"} & prefs) and not ({"sea", "warm"} & prefs)
    if dest is not None:
        if "beach" in dest.kinds and not city_pref:
            return 7
        if "ski" in dest.kinds:
            return 6
        if "city" in dest.kinds:
            return 4
        return 5
    if {"sea", "warm", "ski"} & prefs or req.has_kids:
        return 7
    if city_pref:
        return 4
    return 6


def children(req: TripRequest) -> list[int]:
    ages = list(req.children_ages)
    ages += [7] * req.children_pending
    return ages


def adults(req: TripRequest) -> int:
    return req.adults or 2


# ---------------------------------------------------------------- транспорт

def transport_options(req: TripRequest, dest_id: str, day: date, direction: str) -> list[dict]:
    origin = req.origin or "MOW"
    opts: list[dict] = []
    if req.transport in ("any", "plane"):
        opts += inv.search_flights(origin, dest_id, day, direction)
    if req.transport in ("any", "train"):
        trains = inv.search_trains(origin, dest_id, day, direction)
        if req.transport == "any" and opts:
            trains = [t for t in trains if t["duration_min"] <= 14 * 60]  # длинные поезда — только по запросу
        opts += trains
    if req.transport == "plane" and not opts:
        opts += inv.search_trains(origin, dest_id, day, direction)
    if req.transport == "train" and not opts:
        opts += inv.search_flights(origin, dest_id, day, direction)
    return opts


def fastest_minutes(req: TripRequest, dest: Destination) -> tuple[int | None, str]:
    """Оценка минимального времени в пути до направления и вид транспорта."""
    origin = ORIGIN_BY_CODE[req.origin or "MOW"]
    best, mode = None, "plane"
    if dest.flights and req.transport != "train":
        dist = haversine_km(origin.lat, origin.lon, dest.lat, dest.lon)
        if dist >= 150:
            m = flight_minutes(dist)
            if origin.code not in dest.direct_from:
                hub = hub_point(dest.hubs[0] if dest.hubs else dest.hub if dest.hub != origin.code else "MOW")
                m = flight_minutes(haversine_km(origin.lat, origin.lon, hub.lat, hub.lon)) + \
                    flight_minutes(haversine_km(hub.lat, hub.lon, dest.lat, dest.lon)) + 90
            best = m
    if req.transport != "plane" and inv.train_available(origin.code, dest.id):
        tmin = min(r[1] for r in inv.TRAIN_ROUTES[(origin.code, dest.id)])
        if best is None or req.transport == "train" or tmin < best:
            best, mode = tmin, "train"
    return best, mode


def score_transport(t: dict, req: TripRequest, all_opts: list[dict]) -> tuple[float, list[str], list[str]]:
    pros, cons = [], []
    fastest = min(o["duration_min"] for o in all_opts)
    s = 1.0 - min(0.6, (t["duration_min"] - fastest) / max(fastest, 120) * 0.6)
    dep, arr = dt(t["dep"]), dt(t["arr"])
    if t["mode"] == "plane":
        if t["stops"]:
            s -= 0.35 if req.has_kids else 0.22
            cons.append(f"пересадка в городе {t['via']} ({hours(t['layover_min'])})")
            if t["layover_min"] > 300:
                s -= 0.1
                cons.append("долгое ожидание между рейсами")
        else:
            pros.append("прямой рейс")
        if req.direct_only and t["stops"]:
            s -= 0.6
        if not t["baggage_included"]:
            cons.append(f"тариф без багажа: докупил багаж (+{rub(t['bag_fee'])} за место)")
            s -= 0.04
        if req.priority == "comfort" and t["lowcost"]:
            s -= 0.45
            cons.append("лоукост: тесные кресла, всё за доплату")
        if req.max_flight_h and t["duration_min"] > req.max_flight_h * 60:
            s -= 0.45
            cons.append(f"в пути {hours(t['duration_min'])} — больше вашего лимита {req.max_flight_h:g} ч")
        if req.pets and not t["pet_cabin"]:
            s -= 1.0
            cons.append("перевозчик не берёт животных в салон")
    else:
        pros.append("поезд: без досмотров и ожидания в аэропорту")
        night = dep.hour >= 19 or dep.hour < 2
        cls = t["class_name"]
        if t["duration_min"] > 600:
            if cls == "Плацкарт":
                s -= 0.25 if req.has_kids or req.priority == "comfort" else 0.1
                cons.append("плацкарт на долгой дороге — тесновато")
            elif cls == "СВ":
                s += 0.12 if req.priority == "comfort" else -0.05
                pros.append("СВ — двухместное купе")
            elif cls == "Купе":
                s += 0.08
                pros.append("купе — удобно с семьёй и питомцем" if (req.has_kids or req.pets) else "купе")
        if night and t["duration_min"] < 800:
            pros.append("ночной поезд: экономит день и ночь в отеле")
        if cls == "Бизнес" and req.priority != "comfort":
            s -= 0.1
    if req.has_kids and 0 <= dep.hour < 5:
        s -= 0.15
        cons.append(f"вылет ночью в {dep:%H:%M} — тяжело с детьми")
    if 0 <= arr.hour < 5:
        s -= 0.08
        cons.append(f"прибытие ночью в {arr:%H:%M}")
    if t["id"].endswith("-back") and dep.hour < 6:
        short = (req.nights or 7) <= 3
        s -= 0.3 if short else 0.12
        cons.append(f"обратно ночью в {dep:%H:%M}: последний вечер без номера")
    pref = req.flight_time_pref
    if pref:
        ok = {"morning": 5 <= dep.hour < 11, "day": 11 <= dep.hour < 17, "evening": 17 <= dep.hour <= 23}[pref]
        s += 0.12 if ok else -0.08
    if t["refundable"] and req.free_cancel:
        s += 0.05
        pros.append("возвратный тариф")
    return max(0.0, min(1.2, s)), pros, cons


# ---------------------------------------------------------------- отели

def hotel_filter(h: dict, req: TripRequest, dest: Destination) -> str | None:
    """Возвращает причину исключения или None."""
    if req.pets and not h["pets_allowed"]:
        return "не принимает животных"
    if req.has_kids and h["adults_only"]:
        return "только для взрослых (adults only)"
    if req.stars_min and h["stars"] < req.stars_min:
        return f"{h['stars']}★ — ниже желаемых {req.stars_min}★"
    if req.meal and req.meal not in h["meals"] and not (req.meal == "AI" and "UAI" in h["meals"]):
        if not (req.meal == "BB" and any(m in h["meals"] for m in ("HB", "FB", "AI"))):
            return f"нет питания «{inv.MEAL_NAMES[req.meal]}»"
    if req.small_kids and h["vibe"] == "party":
        return "тусовочный район, шумно ночью — не для маленьких детей"
    if req.accessible and not h["accessible"]:
        return "нет доступной среды"
    return None


def score_hotel(h: dict, req: TripRequest, dest: Destination) -> tuple[float, list[str], list[str]]:
    pros, cons = [], []
    s = (h["rating"] - 6.0) / 3.7 * 0.55 + 0.2
    if h["rating"] >= 9:
        pros.append(f"рейтинг {h['rating']} по {h['reviews']} отзывам")
    elif h["rating"] < 7.5:
        cons.append(f"рейтинг всего {h['rating']}")
    wants_sea = "sea" in req.prefs or ("beach" in dest.kinds and "city" not in req.prefs)
    if wants_sea and h["beach_m"] is not None:
        if h["beach_m"] <= 100:
            s += 0.15
            pros.append("первая линия, свой пляж" if h["beach_m"] <= 50 else f"до моря {h['beach_m']} м")
        elif h["beach_m"] > 600:
            s -= 0.18
            cons.append(f"до моря {h['beach_m']} м")
        elif req.beach_close:
            s -= 0.08
            cons.append(f"не первая линия: {h['beach_m']} м до моря")
    if req.has_kids:
        if h["kids_club"]:
            s += 0.12
            pros.append("детский клуб и анимация")
        if h["family_rooms"]:
            s += 0.05
            pros.append("семейные номера")
        if h["pool"]:
            s += 0.04
        if h["vibe"] == "party":
            s -= 0.2
            cons.append("рядом бары, вечером шумно")
    if "quiet" in req.prefs:
        s += 0.1 if h["vibe"] == "quiet" else (-0.15 if h["vibe"] == "party" else -0.03)
        if h["vibe"] == "quiet":
            pros.append("тихое место")
    if "party" in req.prefs and h["vibe"] in ("party", "lively"):
        s += 0.1
        pros.append("рядом бары и ночная жизнь")
    if "romantic" in req.prefs and (h["adults_only"] or h["archetype"] in ("couples", "boutique")):
        s += 0.12
        pros.append("атмосфера для пары" + (" (adults only)" if h["adults_only"] else ""))
    if "wellness" in req.prefs and h["spa"]:
        s += 0.08
        pros.append("СПА")
    if req.meal:
        if req.meal in h["meals"] or (req.meal == "AI" and "UAI" in h["meals"]):
            s += 0.08
            pros.append(inv.MEAL_NAMES[req.meal])
    elif dest.meal_default == "AI" and "AI" in h["meals"]:
        pros.append("всё включено")
    if req.free_cancel and h["free_cancel_days"] is None:
        s -= 0.2
        cons.append("нет бесплатной отмены")
    elif h["free_cancel_days"] is not None and h["free_cancel_days"] >= 3:
        s += 0.03
    city_trip = "city" in dest.kinds or {"culture", "food", "city", "shopping"} & set(req.prefs)
    if city_trip or req.center_close:
        if h["center_km"] <= 1.0:
            s += 0.12
            pros.append("в центре, всё пешком")
        elif h["center_km"] > 5 and "beach" not in dest.kinds:
            s -= 0.1
            cons.append(f"{h['center_km']:g} км до центра")
    if h["airport_km"] > 70:
        s -= 0.06
        cons.append(f"трансфер {int(h['airport_km'])} км")
    if req.budget_level == "high" or req.priority == "comfort":
        s += {5: 0.15, 4: 0.06}.get(h["stars"], -0.05)
    if req.pets and h["pets_allowed"]:
        pros.append(f"можно с питомцем (+{rub(h['pet_fee'])}/ночь)" if h["pet_fee"] else "можно с питомцем")
    if h["reviews"] < 150:
        cons.append("мало отзывов — меньше уверенности")
    for p in h["pros"]:
        if not req.has_kids and "детск" in p:
            continue
        dup_beach = "пляж" in p and any("пляж" in x or "моря" in x for x in pros)
        if p not in pros and len(pros) < 5 and ("пляж" not in p or wants_sea) and not dup_beach:
            pros.append(p)
    for c in h["cons"]:
        if c not in cons and len(cons) < 4 and "отмен" not in c:
            cons.append(c)
    return max(0.0, min(1.2, s)), pros[:5], cons[:4]


def choose_rate(price_info: dict, req: TripRequest, pinned_rate: str | None = None) -> dict:
    rates = {r["code"]: r for r in price_info["rates"]}
    if pinned_rate and pinned_rate in rates:
        return rates[pinned_rate]
    if "flex" in rates and (req.free_cancel or req.has_kids or req.priority != "price"):
        return rates["flex"]
    return rates["nonref"]


def choose_transfer(opts: list[dict], req: TripRequest) -> dict | None:
    avail = [o for o in opts if o["available"]]
    if not avail:
        return None
    small = req.small_kids
    def key(o):
        p = o["price"]
        if small and not o["child_seat"]:
            p *= 3
        if req.pets and o["kind"] in ("shuttle", "public"):
            p *= 3
        if req.priority == "comfort":
            p *= 0.4 if o["kind"] in ("private", "seaplane") else 4 if o["kind"] in ("shuttle", "public") else 1
        if req.priority == "time":
            p *= 1 + o["duration_min"] / 30
        if req.priority in ("balance", "time") and o["kind"] in ("shuttle", "public"):
            p *= 1.6 if req.has_kids else 1.15
        if o["kind"] == "seaplane" and req.priority != "price":
            p *= 0.7
        return p
    return min(avail, key=key)


# ---------------------------------------------------------------- даты

def candidate_starts(req: TripRequest) -> list[date]:
    t = today()
    if req.date_from:
        return [date.fromisoformat(req.date_from)]
    m = req.month or ((t.month % 12) + 1)
    y = req.year or (t.year if m >= t.month else t.year + 1)
    out = []
    for dd in (1, 4, 7, 10, 13, 16, 19, 22, 25, 28):
        try:
            d = date(y, m, dd)
        except ValueError:
            continue
        if d >= t + timedelta(days=3):
            out.append(d)
    if not out:
        out = [t + timedelta(days=5)]
    return out


def pick_dates(req: TripRequest, dest: Destination) -> tuple[date, date, str | None]:
    """Если даты гибкие — выбираем самое выгодное окно в месяце."""
    nights = default_nights(req, dest)
    if req.date_from and req.date_to:
        return date.fromisoformat(req.date_from), date.fromisoformat(req.date_to), None
    if req.date_from:
        d1 = date.fromisoformat(req.date_from)
        return d1, d1 + timedelta(days=nights), None
    starts = candidate_starts(req)
    best, best_cost, worst_cost = None, None, None
    for d1 in starts:
        d2 = d1 + timedelta(days=nights)
        out = transport_options(req, dest.id, d1, "out")
        back = transport_options(req, dest.id, d2, "back")
        if not out or not back:
            continue
        cost = min(o["price"] for o in out) + min(b["price"] for b in back)
        cost = cost * (adults(req) + 0.75 * len(children(req)))
        cost += dest.hotel_base * sum(inv.season_mult(dest, d1 + timedelta(days=i)) for i in range(nights))
        if best_cost is None or cost < best_cost:
            best, best_cost = d1, cost
        worst_cost = cost if worst_cost is None else max(worst_cost, cost)
    if best is None:
        best = starts[0]
        return best, best + timedelta(days=nights), None
    note = None
    if len(starts) > 1 and worst_cost and best_cost and worst_cost - best_cost > 2000:
        note = f"самые выгодные даты в месяце (дешевле худшего окна на ~{rub(round((worst_cost - best_cost) / 100) * 100)})"
    return best, best + timedelta(days=nights), note


# ---------------------------------------------------------------- направления

def weather(dest: Destination, month: int) -> dict:
    i = month - 1
    return {"air": dest.air[i], "sea": dest.sea[i] if dest.sea else None, "rain_days": dest.rain[i]}


def rank_destinations(req: TripRequest, visited: list[str] | None = None, limit: int = 4,
                      extra: list[str] | None = None) -> tuple[list[dict], list[dict]]:
    visited = visited or []
    excluded: list[dict] = []
    scored: list[dict] = []
    month = req.month or (date.fromisoformat(req.date_from).month if req.date_from else ((today().month % 12) + 1))
    prefs = set(req.prefs)
    origin = req.origin or "MOW"
    pool = [DEST_BY_ID[i] for i in req.candidates if i in DEST_BY_ID] if req.candidates else list(DESTINATIONS)
    if not req.candidates and extra:  # места, которые предложила LLM, соревнуются с каталогом на равных
        known = {d.id for d in pool}
        pool += [DEST_BY_ID[i] for i in extra if i not in known and i in DEST_BY_ID]
    for d in pool:
        if ORIGIN_SAME_DEST.get(origin) == d.id or d.id in req.avoid or d.country in req.avoid:
            continue
        if req.country and not req.candidates and d.country != req.country and not (req.country == "Россия" and d.domestic):
            continue
        reason = None
        fm, mode = fastest_minutes(req, d)
        if fm is None:
            reason = "не добраться выбранным транспортом"
        elif req.transport == "train" and mode != "train":
            reason = "нет поезда из вашего города"
        elif req.visa_free and d.visa != "none":
            reason = d.visa_note.lower()
        elif req.pets and d.pets == "banned":
            reason = "ввоз животных запрещён"
        elif req.max_flight_h and fm > req.max_flight_h * 60 * 1.05:
            reason = f"в пути от {hours(fm)} — больше лимита {req.max_flight_h:g} ч"
        elif "ski" in prefs and month not in d.ski_months and "ski" in d.tags:
            reason = "в этом месяце нет снега"
        if reason:
            if len(excluded) < 12 and (d.country == req.country or {"sea", "warm"} & prefs & set(d.tags) or prefs & set(d.tags)):
                excluded.append({"dest_id": d.id, "name": d.name, "flag": d.flag, "reason": reason})
            continue
        w = weather(d, month)
        why, cons, risks = [], [], []
        s = 0.0
        # погода и сезон
        wants_warm = bool({"warm", "sea"} & prefs)
        if wants_warm:
            if w["air"] >= 26:
                s += 1.0
                why.append(f"тепло: днём около +{w['air']}°")
            elif w["air"] >= 22:
                s += 0.5
                why.append(f"комфортно: днём около +{w['air']}°")
            else:
                s -= 1.5
                cons.append(f"прохладно: днём около {deg(w['air'])}" if w["air"] > 5 else f"холодно: днём около {deg(w['air'])}")
        if "sea" in prefs:
            if w["sea"] is None:
                s -= 2.0
                cons.append("нет моря")
            elif w["sea"] >= 24 and month in d.swim_months:
                s += 1.0
                why.append(f"море +{w['sea']}° — можно купаться")
            elif w["sea"] >= 21 and month in d.swim_months:
                s += 0.3
                why.append(f"море +{w['sea']}°, бодрящее")
            else:
                s -= 1.6
                cons.append(f"море холодное (+{w['sea']}°)")
        if not wants_warm:
            if w["air"] > 34:
                s -= 0.6
                cons.append(f"жарко: днём до +{w['air']}°")
            elif w["air"] < 0 and not ({"ski", "northern_lights", "nature"} & prefs):
                s -= 0.4
                cons.append(f"морозно: днём {deg(w['air'])}")
            elif 15 <= w["air"] <= 30:
                s += 0.3 + (0.4 if not prefs and w["air"] >= 22 else 0)
                if w["air"] >= 22:
                    why.append(f"тепло: днём около +{w['air']}°")
            elif w["air"] < 10 and not ({"ski", "northern_lights", "nature", "culture", "city", "food"} & prefs):
                s -= 0.5
                cons.append(f"{'прохладно' if w['air'] > 5 else 'холодно'}: днём около {deg(w['air'])}")
        if "ski" in prefs:
            if month in d.ski_months:
                s += 1.6
                why.append("сезон катания")
            elif "ski" not in d.tags:
                s -= 2.0
                cons.append("нет горнолыжных трасс")
        if "dry" in prefs and w["rain_days"] >= 10:
            s -= 1.2
            cons.append(f"сезон дождей: ~{w['rain_days']} дождливых дней")
        leisure = {"romantic", "quiet", "wellness", "food"}
        if prefs and prefs <= leisure | {"pool", "kitchen"} and w["air"] >= 22:
            s += 0.35
        if "northern_lights" in prefs:
            if month in d.lights_months:
                s += 1.6
                why.append("сезон северного сияния")
            else:
                s -= 2.0 if "northern_lights" not in d.tags else 0.5
                cons.append("северное сияние здесь не увидеть" if "northern_lights" not in d.tags else "не сезон сияния")
        if w["rain_days"] >= 14:
            s -= 0.5
            risks.append(f"дождливо: около {w['rain_days']} дождливых дней в месяц")
        risk = d.risks.get(month) or d.risks.get(0)
        if risk:
            risks.append(risk)
            s -= 0.6 if "сезон дождей" in risk or "тайфун" in risk or "ураган" in risk else 0.3
        if month in d.high_months:
            why.append("высокий сезон")
        # интересы
        tag_hits = [p for p in prefs if p in d.tags and p not in ("sea", "warm")]
        s += 0.5 * len(tag_hits)
        labels = {"culture": "много экскурсий", "food": "отличная кухня", "party": "ночная жизнь",
                  "quiet": "спокойно и без толп", "romantic": "романтично", "nature": "красивая природа",
                  "mountains": "горы", "shopping": "шопинг", "wellness": "оздоровление и СПА",
                  "diving": "рифы для снорклинга и дайвинга", "city": "интересный город", "surf": "сёрф-споты",
                  "adventure": "приключения", "kids_fun": "много развлечений для детей"}
        for p in tag_hits[:3]:
            if p in labels:
                why.append(labels[p])
        if "kids" in d.tags and req.has_kids:
            s += 0.5
            why.append("удобно с детьми")
        if req.small_kids and "party" in d.tags and "kids" not in d.tags:
            s -= 0.5
        if "all_inclusive" in d.tags and (req.meal == "AI" or req.has_kids):
            s += 0.4 if req.meal == "AI" else 0.15
            if req.meal == "AI":
                why.append("много отелей «всё включено»")
        if req.meal == "AI" and "all_inclusive" not in d.tags:
            s -= 0.3
        # дорога
        trip_h = fm / 60
        long_pen = 0.18 if (req.has_kids or req.max_flight_h) else 0.1
        s -= max(0.0, trip_h - 4) * long_pen
        travel = {"mode": mode, "minutes": fm, "direct": (req.origin or "MOW") in d.direct_from or mode == "train"}
        if trip_h <= 4.5:
            why.append(("поездом " if mode == "train" else "лететь ") + f"всего ~{hours(fm)}")
        elif trip_h > 9:
            cons.append(f"долгая дорога: ~{hours(fm)}")
        if not travel["direct"] and mode == "plane":
            cons.append("нет прямых рейсов, пересадка")
            s -= 0.3
        # визы и животные
        if d.visa != "none":
            s -= 0.15
            risks.append(d.visa_note)
        if req.pets:
            if d.pets == "easy":
                s += 0.4
                why.append("просто с питомцем: не нужен ветсертификат для границы")
            elif d.pets == "docs":
                s -= 0.2
                risks.append("для питомца: чип, прививка от бешенства и ветсертификат для въезда")
            elif d.pets == "hard":
                s -= 1.2
                risks.append("ввоз питомца сложный: разрешение, тест на антитела, возможен карантин")
        if req.budget_level == "low" and "budget" in d.tags:
            s += 0.5
            why.append("недорогое направление")
        if req.budget_level == "high" and "luxury" in d.tags:
            s += 0.4
        if d.id in visited:
            s -= 0.25
        if extra and d.id in extra and d.source == "llm":
            s += 0.5
        if d.id in req.similar_to:
            s += 2.2 - 0.2 * req.similar_to.index(d.id)
            why.insert(0, f"ближе всего к «{req.similar_label}»" if req.similar_label else "похоже на то, что вы искали")
        if req.country:
            s += 0.2
        scored.append({"dest": d, "score": s, "why": why, "cons": cons, "risks": risks, "weather": w, "travel": travel})

    scored.sort(key=lambda x: -x["score"])
    # подробно оцениваем стоимость только для лучших кандидатов
    detailed = []
    for item in scored[: max(limit * 3, 10)]:
        d = item["dest"]
        d1, d2, date_note = pick_dates(req, d)
        est = estimate_trip(req, d, d1, d2)
        if est is None:
            continue
        item.update({"d1": d1, "d2": d2, "date_note": date_note, "est": est})
        if req.budget:
            ratio = est["total"] / req.budget
            if ratio > 1.0:
                item["score"] -= min(2.5, (ratio - 1.0) * 4)
                item["cons"].append(f"дороже бюджета на ~{rub(est['total'] - req.budget)}")
            elif ratio < 0.85:
                item["score"] += 0.3
                item["why"].append(f"укладывается в бюджет с запасом ~{rub(req.budget - est['total'])}")
            else:
                item["why"].append("укладывается в бюджет")
        elif req.budget_level == "low":
            item["score"] -= est["total"] / max(1, req.party_size) / 60000
        detailed.append(item)
    detailed.sort(key=lambda x: -x["score"])

    out, per_country = [], {}
    picked: list[dict] = []
    if not prefs and not req.country:
        # запрос без предпочтений — вдохновляем разными типами отдыха
        top = detailed[0]["score"] if detailed else 0
        for kind in ("beach", "city", "nature", "ski"):
            for item in detailed:
                if item["dest"].kinds[0] == kind and item not in picked and item["score"] >= top - 1.5:
                    picked.append(item)
                    break
        picked.sort(key=lambda x: -x["score"])
    for item in picked + [x for x in detailed if x not in picked]:
        d = item["dest"]
        if per_country.get(d.country, 0) >= (3 if req.country or d.domestic else 2):
            continue
        per_country[d.country] = per_country.get(d.country, 0) + 1
        out.append(_dest_card(item, req))
        if len(out) >= limit:
            break
    return out, excluded


def estimate_trip(req: TripRequest, d: Destination, d1: date, d2: date) -> dict | None:
    out = transport_options(req, d.id, d1, "out")
    back = transport_options(req, d.id, d2, "back")
    if not out or not back:
        return None
    a, ch = adults(req), children(req)
    def best(opts):
        scored = sorted(opts, key=lambda o: inv.pax_price(o, a, ch) * (1.25 if o.get("stops") else 1.0))
        return scored[0]
    o, b = best(out), best(back)
    tp = inv.pax_price(o, a, ch) + inv.pax_price(b, a, ch)
    hotels = [h for h in inv.get_hotels(d.id) if hotel_filter(h, req, d) is None]
    if not hotels:
        return None
    ci = dt(o["arr"]).date()
    co = d2 if dt(b["dep"]).hour >= 6 else d2 - timedelta(days=1)
    if co <= ci:
        co = ci + timedelta(days=1)
    prices = sorted(choose_rate(inv.price_hotel(h, ci, co, a, ch, req.meal, bool(req.pets)), req)["price"] for h in hotels)
    hp = prices[min(len(prices) - 1, len(prices) // 3)]
    transfer = inv.r100(1500 * inv.price_level(d) * 2) if d.id != "maldives" else inv.r100(9000 * req.party_size * 2)
    total = tp + hp + transfer
    return {"total": total, "transport": tp, "hotel": hp, "transfer": transfer,
            "per_person": total // max(1, req.party_size),
            "on_site_per_day": d.daily_spend * req.party_size, "nights": (co - ci).days,
            "mode": o["mode"], "duration_min": o["duration_min"], "stops": o.get("stops", 0)}


TAG_WHY = {"culture": "история и экскурсии", "food": "отличная кухня", "party": "ночная жизнь", "quiet": "спокойно и без толп",
           "romantic": "романтичная атмосфера", "nature": "красивая природа", "mountains": "горы", "shopping": "шопинг",
           "wellness": "СПА и оздоровление", "diving": "рифы и снорклинг", "city": "интересный город", "kids": "удобно с детьми",
           "budget": "недорого на месте", "all_inclusive": "много отелей «всё включено»", "luxury": "премиальные отели"}


def _dest_card(item: dict, req: TripRequest) -> dict:
    d: Destination = item["dest"]
    est, w = item["est"], item["weather"]
    if len(item["why"]) < 2:
        item["why"] += [TAG_WHY[t] for t in d.tags if t in TAG_WHY][:2]
    return {
        "dest_id": d.id, "name": d.name, "country": d.country, "flag": d.flag,
        "score": round(item["score"], 2),
        "dates": {"from": item["d1"].isoformat(), "to": item["d2"].isoformat(),
                  "text": date_range_text(item["d1"], item["d2"]), "note": item["date_note"]},
        "weather": w,
        "travel": {**item["travel"], "text": ("поезд " if item["travel"]["mode"] == "train" else "") +
                   f"~{hours(item['travel']['minutes'])}" + ("" if item["travel"]["direct"] else ", с пересадкой")},
        "est": est,
        "visa": d.visa_note,
        "why": _uniq(item["why"])[:4],
        "cons": _uniq(item["cons"])[:3],
        "risks": _uniq(item["risks"])[:3],
        "highlights": list(d.highlights[:3]),
        "kinds": list(d.kinds),
        "ai": d.source == "llm",
    }


def _uniq(xs: list[str]) -> list[str]:
    seen, out = set(), []
    for x in xs:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


# ---------------------------------------------------------------- пакеты

def build_plan(req: TripRequest, dest_id: str, pinned: dict | None = None, prev: dict | None = None) -> dict:
    """Собирает 3 варианта поездки «под ключ» для направления."""
    pinned = pinned or {}
    dest = DEST_BY_ID[dest_id]
    d1, d2, date_note = pick_dates(req, dest)
    a, ch = adults(req), children(req)
    trace = []
    out_opts = transport_options(req, dest_id, d1, "out")
    back_opts = transport_options(req, dest_id, d2, "back")
    trace.append({"tool": "search_transport", "args": {"from": req.origin or "MOW", "to": dest.name, "date": d1.isoformat(),
                                                     "mode": req.transport}, "result": f"{len(out_opts)} вариантов туда"})
    trace.append({"tool": "search_transport", "args": {"from": dest.name, "to": req.origin or "MOW", "date": d2.isoformat(),
                                                     "mode": req.transport}, "result": f"{len(back_opts)} вариантов обратно"})
    notes: list[str] = []
    if not out_opts or not back_opts:
        return {"error": f"Не нашёл транспорта на {date_range_text(d1, d2)} до направления {dest.name}.", "trace": trace,
                "dest_id": dest_id}
    if req.transport == "plane" and out_opts[0]["mode"] == "train":
        notes.append("Самолётов туда нет — предлагаю поезд.")
    if req.transport == "train" and out_opts[0]["mode"] == "plane":
        notes.append("Поезда туда не ходят — оставил только перелёт.")

    def rank_t(opts):
        scored = []
        for t in opts:
            s, pros, cons = score_transport(t, req, opts)
            price = inv.pax_price(t, a, ch) + (t["pet_fee"] if req.pets else 0)
            scored.append({**t, "fit": round(s, 3), "pros": pros, "cons": cons, "total_price": price})
        hard = [t for t in scored if not (req.pets and not t["pet_cabin"])]
        if req.direct_only and any(t["stops"] == 0 for t in hard):
            hard = [t for t in hard if t["stops"] == 0]
        if req.max_flight_h and any(t["duration_min"] <= req.max_flight_h * 60 for t in hard):
            hard = [t for t in hard if t["duration_min"] <= req.max_flight_h * 60]
        return hard or scored

    outs, backs = rank_t(out_opts), rank_t(back_opts)
    if req.max_flight_h and min(t["duration_min"] for t in outs) > req.max_flight_h * 60:
        notes.append(f"Быстрее {hours(min(t['duration_min'] for t in outs))} туда не добраться — это больше лимита {req.max_flight_h:g} ч.")

    hotels_all = inv.get_hotels(dest_id)
    trace.append({"tool": "search_hotels", "args": {"dest": dest.name, "check_in": d1.isoformat(), "guests": f"{a} взр. + {len(ch)} дет."},
                  "result": f"{len(hotels_all)} отелей"})
    excluded_hotels, hotels = [], []
    for h in hotels_all:
        r = hotel_filter(h, req, dest)
        if r:
            excluded_hotels.append({"hotel_id": h["id"], "name": h["name"], "stars": h["stars"], "reason": r})
        else:
            s, pros, cons = score_hotel(h, req, dest)
            hotels.append({**h, "fit": round(s, 3), "fit_pros": pros, "fit_cons": cons})
    if not hotels:
        # ослабляем только мягкие условия; животные и adults-only с детьми — жёсткие
        hard = ("не принимает животных", "только для взрослых")
        for h in hotels_all:
            reason = hotel_filter(h, req, dest)
            if reason and not reason.startswith(hard):
                s, pros, cons = score_hotel(h, req, dest)
                hotels.append({**h, "fit": round(s - 0.2, 3), "fit_pros": pros, "fit_cons": [reason] + cons})
        if hotels:
            notes.append("Идеальных отелей под все условия нет — показываю ближайшие, компромисс указан в карточке.")
            excluded_hotels = [e for e in excluded_hotels if e["reason"].startswith(hard)]
        else:
            why = "с животными" if req.pets else "под ваши условия"
            return {"error": f"В направлении {dest.name} нет отелей {why}.", "trace": trace, "dest_id": dest_id,
                    "no_hotels": True}
    hotels.sort(key=lambda h: -h["fit"])

    # фиксированные пользователем компоненты
    def pick(lst, key):
        if pinned.get(key):
            sel = [x for x in lst if x["id"] == pinned[key]]
            if sel:
                return sel + [x for x in lst if x["id"] != pinned[key]]
        return lst

    top_out = pick(sorted(outs, key=lambda t: -t["fit"]), "out_id")[:4]
    top_out += [t for t in sorted(outs, key=lambda t: t["total_price"])[:2] if t not in top_out]
    top_out += [t for t in sorted(outs, key=lambda t: t["duration_min"])[:1] if t not in top_out]
    top_back = pick(sorted(backs, key=lambda t: -t["fit"]), "back_id")[:4]
    top_back += [t for t in sorted(backs, key=lambda t: t["total_price"])[:2] if t not in top_back]
    top_back += [t for t in sorted(backs, key=lambda t: t["duration_min"])[:1] if t not in top_back]
    top_hotels = pick(hotels, "hotel_id")[:7]
    extra_h = sorted(hotels, key=lambda h: h["base_price"])[:2]  # самые дешёвые
    extra_h += sorted(hotels, key=lambda h: (-h["stars"], -h["rating"]))[:2]  # самые классные
    extra_h += sorted(hotels, key=lambda h: h["airport_km"])[:2]  # ближе всего к аэропорту
    top_hotels += [h for h in extra_h if h not in top_hotels]

    prev_ids = {}
    if prev and prev.get("bundles"):
        sel = prev["bundles"][prev.get("selected") or 0] if prev.get("selected") is not None else prev["bundles"][0]
        prev_ids = {"hotel": sel["hotel"]["id"]}

    combos = []
    hotel_price_cache = {}
    for o in top_out:
        for b in top_back:
            for h in top_hotels:
                combo = _make_bundle(req, dest, o, b, h, d1, d2, pinned, hotel_price_cache)
                if combo is None:
                    continue
                if prev_ids.get("hotel") == h["id"]:
                    combo["_sticky"] = 0.05
                combos.append(combo)
    if not combos:
        return {"error": "Не удалось состыковать перелёт и отель на эти даты.", "trace": trace, "dest_id": dest_id}
    trace.append({"tool": "search_transfers", "args": {"dest": dest.name}, "result": "подобраны трансферы для каждого отеля"})

    cheapest = min(c["total"] for c in combos)
    for c in combos:
        c["door_min"] = (c["out"]["duration_min"] + c["back"]["duration_min"] +
                         (c["transfer_in"] or {"duration_min": 60})["duration_min"] +
                         (c["transfer_out"] or {"duration_min": 60})["duration_min"])
    fastest_door = min(c["door_min"] for c in combos)
    w = WEIGHTS.get(req.priority, WEIGHTS["balance"])
    for c in combos:
        price_s = cheapest / c["total"]
        if req.budget and c["total"] > req.budget:
            price_s -= min(1.0, (c["total"] / req.budget - 1) * 2.5)
        tr_s = (c["out"]["fit"] + c["back"]["fit"]) / 2
        tf_s = c["transfer_fit"]
        time_s = fastest_door / c["door_min"]
        h = c["hotel"]
        hotel_s = h["fit"]
        if req.priority == "comfort":  # комфорт — это ещё и класс отеля, гибкий тариф и встреча в аэропорту
            hotel_s += (h["stars"] - 3) * 0.12 + (h["rating"] - 8) * 0.08 + (0.08 if c["stay"]["rate"]["refundable"] else 0)
            tr_s += 0.1 * tf_s
        if req.priority == "time":
            time_s -= min(0.3, h["airport_km"] / 300)
        pen = sum(0.5 if i["level"] == "error" else 0.04 if i["level"] == "warn" else 0 for i in c["issues"])
        c["utility"] = round(w["price"] * price_s + w["hotel"] * hotel_s + w["transport"] * tr_s +
                             w["time"] * time_s - pen + c.pop("_sticky", 0), 4)
        c["quality"] = c["hotel"]["fit"] * 0.6 + tr_s * 0.3 + tf_s * 0.1 - pen
        c["blocking"] = any(i["level"] == "error" for i in c["issues"])

    valid = [c for c in combos if not c["blocking"]] or combos
    pinned_any = any(pinned.get(k) for k in ("hotel_id", "out_id", "back_id"))
    if pinned_any:
        def match(c):
            return all(not pinned.get(k) or c[part]["id"] == pinned[k]
                       for k, part in (("hotel_id", "hotel"), ("out_id", "out"), ("back_id", "back")))
        pool = [c for c in valid if match(c)] or valid
        best = max(pool, key=lambda c: c["utility"])
    else:
        best = max(valid, key=lambda c: c["utility"])
    budget_cap = req.budget * 1.0 if req.budget else None

    def distinct(c, chosen):
        return all(c["hotel"]["id"] != x["hotel"]["id"] for x in chosen)

    chosen = [best]
    econ_pool = [c for c in valid if distinct(c, chosen)]
    econ = min(econ_pool, key=lambda c: (c["total"], -c["utility"])) if econ_pool else None
    if econ and econ["total"] >= best["total"]:
        econ = None
    if econ:
        chosen.append(econ)
    cap = max(best["total"] * 1.6, (budget_cap or 0) * 1.15)
    def comfort_q(c):
        h = c["hotel"]
        return c["quality"] * 0.6 + h["stars"] / 5 * 0.25 + h["rating"] / 10 * 0.15
    comfort_pool = [c for c in valid if distinct(c, chosen) and c["total"] <= cap and c["total"] > best["total"]
                    and comfort_q(c) > comfort_q(best) - 0.03]
    comfort = max(comfort_pool, key=lambda c: (comfort_q(c), -c["total"])) if comfort_pool else None
    if comfort:
        chosen.append(comfort)
    labels = []
    for c in chosen:
        if c is best:
            labels.append(("optimal", "Оптимальный", "Лучший баланс под ваши условия"))
        elif c is econ:
            labels.append(("economy", "Экономный", "Минимальная цена без критичных компромиссов"))
        else:
            labels.append(("comfort", "Комфорт", "Максимум удобства в разумных деньгах"))
    bundles = []
    for c, (code, title, subtitle) in zip(chosen, labels, strict=True):
        c.update({"code": code, "title": title, "subtitle": subtitle})
        bundles.append(c)
    for c in bundles:
        c["explain"] = explain_bundle(c, bundles, req, dest)
    hotels_cmp = _hotel_comparison(req, dest, best, hotels, hotel_price_cache, d1, d2)
    trace.append({"tool": "check_connections", "args": {"bundles": len(bundles)},
                  "result": ", ".join(f"{b['title']}: {sum(1 for i in b['issues'] if i['level'] in ('warn', 'error'))} замечаний"
                                      for b in bundles)})
    plan = {
        "dest_id": dest_id, "dest_name": dest.name, "flag": dest.flag, "country": dest.country,
        "date_from": d1.isoformat(), "date_to": d2.isoformat(), "dates_text": date_range_text(d1, d2), "date_note": date_note,
        "nights_requested": (d2 - d1).days,
        "bundles": bundles, "selected": None,
        "hotels": hotels_cmp,
        "excluded_hotels": excluded_hotels[:4],
        "flights_out": [_t_short(t) for t in sorted(outs, key=lambda t: -t["fit"])[:5]],
        "flights_back": [_t_short(t) for t in sorted(backs, key=lambda t: -t["fit"])[:5]],
        "weights": w, "priority": req.priority,
        "notes": notes, "trace": trace,
        "weather": weather(dest, d1.month),
        "dest_risks": _dest_risks(dest, d1.month, req),
    }
    return plan


def _dest_risks(dest: Destination, month: int, req: TripRequest) -> list[str]:
    risks = []
    r = dest.risks.get(month) or dest.risks.get(0)
    if r:
        risks.append(r)
    if dest.visa != "none":
        risks.append(dest.visa_note)
    if not dest.domestic:
        risks.append("загранпаспорт должен действовать ещё 6 месяцев после возвращения")
    if req.pets:
        if dest.pets == "docs":
            risks.append("питомцу нужны чип, прививка от бешенства и ветсертификат")
        elif dest.pets == "hard":
            risks.append("ввоз питомца: разрешение страны и тест на антитела — оформляется заранее")
    return risks


def _t_short(t: dict) -> dict:
    return {k: t[k] for k in ("id", "mode", "carrier", "number", "from_name", "to_name", "dep", "arr", "duration_min",
                              "stops", "via", "layover_min", "baggage_included", "refundable", "fare_name", "class_name",
                              "total_price", "fit", "pros", "cons")}


def _make_bundle(req, dest, o, b, h, d1, d2, pinned, cache) -> dict | None:
    a, ch = adults(req), children(req)
    arr = dt(o["arr"])
    dep_back = dt(b["dep"])
    ci = arr.date()
    adjustments = []
    # если прилёт до 6 утра — номер нужен с предыдущей ночи (иначе ждать заселения 8+ часов)
    early_night = arr.hour < 5 and pinned.get("early_night", True)
    if early_night:
        ci = ci - timedelta(days=1)
        adjustments.append(f"прилёт в {arr:%H:%M}: бронирую номер с ночи {ddmm(ci)}, чтобы сразу заселиться")
    co = dep_back.date()
    if dep_back.hour < 6:
        co = co - timedelta(days=1)
        adjustments.append(f"вылет обратно в {dep_back:%H:%M}: выезд из отеля {ddmm(co)} вечером, без лишней ночи")
    if co <= ci:
        return None
    if ci > d1 + timedelta(days=1):
        adjustments.append(f"заселение {ddmm(ci)}: из-за длинного перелёта первая ночь проходит в пути")
    key = (h["id"], ci, co)
    if key not in cache:
        cache[key] = inv.price_hotel(h, ci, co, a, ch, req.meal or pinned.get("meal"), bool(req.pets))
    pinfo = cache[key]
    rate = choose_rate(pinfo, req, pinned.get("rate") if pinned.get("hotel_id") == h["id"] else None)
    hub_out = "station" if o["mode"] == "train" else "airport"
    hub_back = "station" if b["mode"] == "train" else "airport"
    t_in_opts = inv.search_transfers(h, o["arr"], a, ch, hub_out)
    t_in = choose_transfer(t_in_opts, req)
    leave_dt = dep_back - timedelta(minutes=_buffer_min(b, dest)) - timedelta(minutes=(t_in or {"duration_min": 60})["duration_min"])
    t_out_opts = inv.search_transfers(h, leave_dt.strftime("%Y-%m-%dT%H:%M"), a, ch, hub_back)
    t_out = choose_transfer(t_out_opts, req)
    no_transfer = pinned.get("no_transfer")
    if no_transfer:
        t_in = t_out = None
    tf_fit = 0.7
    for t in (t_in, t_out):
        if t is None:
            continue
        if t["kind"] == "private":
            tf_fit += 0.15
        if t["kind"] in ("shuttle", "public") and req.has_kids:
            tf_fit -= 0.2
        if t["duration_min"] > 90:
            tf_fit -= 0.15
    tf_fit = max(0.0, min(1.0, tf_fit))
    transfer_price = (t_in["price"] if t_in else 0) + (t_out["price"] if t_out else 0)
    total = o["total_price"] + b["total_price"] + rate["price"] + transfer_price
    bundle = {
        "out": o, "back": b,
        "hotel": h,
        "stay": {"check_in": ci.isoformat(), "check_out": co.isoformat(), "nights": (co - ci).days,
                 "rooms": pinfo["rooms"], "meal": pinfo["meal"], "meal_name": pinfo["meal_name"],
                 "rate": rate, "rates": pinfo["rates"], "pet_fee_total": pinfo["pet_fee_total"],
                 "checkin_time": h["checkin"], "checkout_time": h["checkout"]},
        "transfer_in": t_in, "transfer_out": t_out,
        "transfer_in_options": t_in_opts, "transfer_out_options": t_out_opts,
        "transfer_fit": tf_fit,
        "adjustments": adjustments,
        "prices": {"transport": o["total_price"] + b["total_price"], "hotel": rate["price"], "transfer": transfer_price},
        "total": total,
        "per_person": total // max(1, a + len(ch)),
        "party": {"adults": a, "children": ch},
        "dest_id": dest.id,
        "no_transfer_ok": bool(no_transfer),
    }
    bundle["issues"] = check_connections(bundle, req)
    return bundle


def _buffer_min(t: dict, dest: Destination) -> int:
    if t["mode"] == "train":
        return 30
    return 120 if dest.domestic else 150


# ---------------------------------------------------------------- состыковки

def check_connections(b: dict, req: TripRequest | None = None) -> list[dict]:
    """Независимая проверка пакета: даты, время, трансферы, документы."""
    issues: list[dict] = []
    dest = DEST_BY_ID[b["dest_id"]]
    o, back, stay = b["out"], b["back"], b["stay"]
    arr, dep_back = dt(o["arr"]), dt(back["dep"])
    ci, co = date.fromisoformat(stay["check_in"]), date.fromisoformat(stay["check_out"])
    cin_h = int(stay["checkin_time"][:2])
    t_in, t_out = b.get("transfer_in"), b.get("transfer_out")
    kids = b["party"]["children"]

    def add(level, code, text, fix=None):
        issues.append({"level": level, "code": code, "text": text, **({"fix": fix} if fix else {})})

    # 1. даты отеля против прилёта/вылета
    if ci > arr.date():
        add("error", "checkin_after_arrival", f"Отель с {ddmm(ci)}, а прибытие {ddmm(arr.date())} — ночь без жилья")
    if arr.date() > ci + timedelta(days=1) or (arr.date() > ci and arr.hour >= 5):
        add("error", "checkin_before_arrival", f"Отель оплачен с {ddmm(ci)}, а прибытие только {ddmm(arr.date())} — ночь пропадёт")
    if co < dep_back.date() - timedelta(days=1) or (co < dep_back.date() and dep_back.hour >= 6):
        add("error", "checkout_gap", f"Выезд из отеля {ddmm(co)}, а вылет только {ddmm(dep_back.date())}")
    if co > dep_back.date():
        add("error", "checkout_after_departure", f"Отель оплачен до {ddmm(co)}, а уезжаете {ddmm(dep_back.date())}")
    if dep_back <= arr:
        add("error", "return_before_arrival", "Обратный рейс раньше прибытия")
    # 2. время прибытия и заселение
    ready = arr + timedelta(minutes=(t_in or {"duration_min": 60})["duration_min"])
    if ci == arr.date():
        wait = (cin_h * 60) - (ready.hour * 60 + ready.minute)
        if wait > 150:
            early = b["hotel"]["early_checkin"]
            txt = {"guaranteed": "отель гарантирует ранний заезд за доплату ~50% ночи",
                   "on_request": "ранний заезд — по запросу, без гарантии",
                   "none": "ранний заезд отель не предоставляет — можно оставить багаж"}[early]
            add("warn", "early_arrival",
                f"В отеле будете в {ready:%H:%M}, заселение с {stay['checkin_time']} — ждать {hours(wait)}; {txt}",
                {"action": "early_checkin", "label": "Запросить ранний заезд"})
        if ready.hour >= 23 or ready.date() > arr.date():
            add("warn", "late_arrival", f"В отеле будете около {ready:%H:%M} — предупредим отель о позднем заезде")
    elif ci < arr.date():
        add("ok", "night_before", f"Номер с ночи {ddmm(ci)} — заселитесь сразу после прилёта в {arr:%H:%M}")
    # 3. трансферы
    if t_in is None and not b.get("no_transfer_ok"):
        add("info", "no_transfer_in", "Трансфер до отеля не выбран — можно добавить в корзине",
            {"action": "add_transfer", "label": "Добавить трансфер"})
    elif t_in and not t_in["available"]:
        add("error", "transfer_unavailable", f"«{t_in['name']}» не работает в {arr:%H:%M}")
    if t_in and t_in["kind"] == "seaplane":
        add("info", "seaplane", "Гидросамолёт летает только днём — прилёт рассчитан под него")
    if t_in and any(a < 7 for a in kids) and not t_in["child_seat"]:
        add("warn", "child_seat", "В выбранном трансфере нет детского кресла")
    # 4. обратная дорога
    leave = dep_back - timedelta(minutes=_buffer_min(back, dest)) - timedelta(minutes=(t_out or {"duration_min": 60})["duration_min"])
    out_h = int(stay["checkout_time"][:2])
    if leave.date() == co:
        gap = leave.hour * 60 + leave.minute - out_h * 60
        if gap > 240:
            add("info", "late_checkout",
                f"Выезд в {stay['checkout_time']}, а выезжать в {'аэропорт' if back['mode'] == 'plane' else 'на вокзал'} в {leave:%H:%M} — "
                f"{hours(gap)} без номера: можно оставить багаж или взять поздний выезд",
                {"action": "late_checkout", "label": "Поздний выезд"})
    elif leave.date() < co and dep_back.hour >= 6:
        add("warn", "early_leave", "Уезжать придётся до утра — ночь в отеле частично пропадает")
    if leave < arr:
        add("error", "impossible", "Не успеваете на обратный рейс")
    # 5. перелёт
    for leg, name in ((o, "туда"), (back, "обратно")):
        if leg["mode"] == "plane" and leg["stops"] and leg["layover_min"] < 90:
            add("warn", "short_layover", f"Короткая пересадка {name}: {hours(leg['layover_min'])}")
        if kids and leg["duration_min"] > 600:
            add("warn", "long_trip_kids", f"Долгая дорога {name} с ребёнком: {hours(leg['duration_min'])}")
    # 6. документы
    if not dest.domestic:
        if dest.visa != "none":
            add("info", "visa", dest.visa_note)
        add("info", "passport", "Загранпаспорт должен действовать 6 месяцев после возвращения")
    if req and req.pets:
        if not b["hotel"]["pets_allowed"]:
            add("error", "pet_hotel", "Отель не принимает животных")
        for leg in (o, back):
            if not leg["pet_cabin"]:
                add("error", "pet_transport", f"{leg['carrier']} не перевозит животных")
        if leg["mode"] == "plane":
            add("info", "pet_rules", "Питомец до 8 кг с переноской — в салоне, крупнее — в багажном отсеке; нужна бронь места заранее")
    return issues


# ---------------------------------------------------------------- объяснения

def explain_bundle(c: dict, bundles: list[dict], req: TripRequest, dest: Destination) -> dict:
    h, o, b = c["hotel"], c["out"], c["back"]
    why, tradeoffs, risks = [], [], []
    why += h["fit_pros"][:3]
    why += [p for p in o["pros"][:2] if p not in why]
    if c["stay"]["rate"]["refundable"]:
        why.append(f"бесплатная отмена отеля за {c['stay']['rate']['free_cancel_days']} дн. до заезда")
    if req.budget:
        if c["total"] <= req.budget:
            why.append(f"в бюджете: остаётся {rub(req.budget - c['total'])}")
        else:
            tradeoffs.append(f"выше бюджета на {rub(c['total'] - req.budget)}")
    if c["transfer_in"] and c["transfer_in"]["kind"] == "private":
        why.append("индивидуальный трансфер с встречей" + (" и детским креслом" if req.small_kids else ""))
    tradeoffs += h["fit_cons"][:2]
    tradeoffs += o["cons"][:2] + [x for x in b["cons"][:1] if x not in o["cons"]]
    if not c["stay"]["rate"]["refundable"]:
        tradeoffs.append("невозвратный тариф отеля — дешевле, но деньги не вернуть")
    others = [x for x in bundles if x is not c]
    for x in others:
        diff = c["total"] - x["total"]
        if diff > 0 and x["code"] == "economy":
            tradeoffs.append(f"на {rub(diff)} дороже экономного варианта")
        elif diff < 0 and x["code"] in ("optimal", "comfort") and c["code"] != "comfort":
            pass
    if c["code"] == "economy":
        best = next((x for x in bundles if x["code"] == "optimal"), None)
        if best:
            tradeoffs.insert(0, f"экономия {rub(best['total'] - c['total'])} относительно оптимального")
    if c["code"] == "comfort":
        best = next((x for x in bundles if x["code"] == "optimal"), None)
        if best:
            tradeoffs.insert(0, f"дороже оптимального на {rub(c['total'] - best['total'])}")
    for i in c["issues"]:
        if i["level"] in ("warn", "error"):
            risks.append(i["text"])
    r = dest.risks.get(date.fromisoformat(c["stay"]["check_in"]).month)
    if r:
        risks.append(r)
    if h["reviews"] < 150:
        risks.append("у отеля мало отзывов")
    return {"why": _uniq(why)[:5], "tradeoffs": _uniq(tradeoffs)[:4], "risks": _uniq(risks)[:4]}


def _hotel_comparison(req, dest, best, hotels, cache, d1, d2) -> list[dict]:
    a, ch = adults(req), children(req)
    ci, co = date.fromisoformat(best["stay"]["check_in"]), date.fromisoformat(best["stay"]["check_out"])
    rows = []
    for h in hotels[:8]:
        key = (h["id"], ci, co)
        if key not in cache:
            cache[key] = inv.price_hotel(h, ci, co, a, ch, req.meal, bool(req.pets))
        p = cache[key]
        rate = choose_rate(p, req)
        transfer = choose_transfer(inv.search_transfers(h, best["out"]["arr"], a, ch,
                                                        "station" if best["out"]["mode"] == "train" else "airport"), req)
        rows.append({
            "id": h["id"], "name": h["name"], "stars": h["stars"], "area": h["area"], "rating": h["rating"],
            "reviews": h["reviews"], "price": rate["price"], "rate_name": rate["name"], "refundable": rate["refundable"],
            "meal": p["meal_name"], "beach_m": h["beach_m"], "center_km": h["center_km"],
            "transfer_min": transfer["duration_min"] if transfer else None,
            "fit": int(min(1.0, h["fit"]) * 100), "pros": h["fit_pros"][:3], "cons": h["fit_cons"][:2],
            "kids_club": h["kids_club"], "pets_allowed": h["pets_allowed"], "in_plan": h["id"] == best["hotel"]["id"],
        })
    return rows


def plan_diff(old: dict | None, new: dict) -> list[str]:
    if not old or not old.get("bundles") or not new.get("bundles"):
        return []
    ob = old["bundles"][old["selected"]] if old.get("selected") is not None else old["bundles"][0]
    nb = new["bundles"][0]
    out = []
    if old["dest_id"] != new["dest_id"]:
        out.append(f"направление: {old['dest_name']} → {new['dest_name']}")
    if old["date_from"] != new["date_from"] or old["date_to"] != new["date_to"]:
        out.append(f"даты: {old['dates_text']} → {new['dates_text']}")
    if ob["hotel"]["id"] == nb["hotel"]["id"]:
        out.append(f"отель тот же — {nb['hotel']['name']}")
    else:
        out.append(f"отель: {ob['hotel']['name']} → {nb['hotel']['name']}")
    if ob["out"]["id"] != nb["out"]["id"]:
        out.append(f"туда: {ob['out']['number']} {dt(ob['out']['dep']):%d.%m %H:%M} → {nb['out']['number']} {dt(nb['out']['dep']):%d.%m %H:%M}")
    if ob["back"]["id"] != nb["back"]["id"]:
        out.append(f"обратно: {nb['back']['number']} {dt(nb['back']['dep']):%d.%m %H:%M}")
    delta = nb["total"] - ob["total"]
    sign = "+" if delta > 0 else "−"
    out.append(f"итого: {rub(ob['total'])} → {rub(nb['total'])} ({sign}{rub(abs(delta))})" if delta else f"итого без изменений: {rub(nb['total'])}")
    return out


def itinerary(b: dict, req: TripRequest) -> list[dict]:
    dest = DEST_BY_ID[b["dest_id"]]
    o, back, stay = b["out"], b["back"], b["stay"]
    arr, dep = dt(o["arr"]), dt(o["dep"])
    days = []
    d0 = dep.date()
    first = [f"{dep:%H:%M} {'вылет' if o['mode'] == 'plane' else 'отправление'} {o['from_name']} — {o['number']}"]
    if arr.date() != d0:
        days.append({"date": d0.isoformat(), "title": "В пути", "items": first})
        first = []
    first.append(f"{arr:%H:%M} прибытие {o['to_name']}")
    if b.get("transfer_in"):
        first.append(f"трансфер: {b['transfer_in']['name']}, ~{hours(b['transfer_in']['duration_min'])}")
    first.append(f"заселение в {b['hotel']['name']} (с {stay['checkin_time']})")
    days.append({"date": arr.date().isoformat(), "title": "Прибытие", "items": first})
    ideas = list(dest.highlights)
    co = date.fromisoformat(stay["check_out"])
    cur = arr.date() + timedelta(days=1)
    i = 0
    if (co - cur).days > 14:  # длинная поездка: первые дни по идеям, остальное одной строкой
        for k in range(len(ideas)):
            days.append({"date": cur.isoformat(), "title": "Идея на день", "items": [ideas[k]]})
            cur += timedelta(days=1)
        rest = (co - cur).days
        if rest > 0:
            days.append({"date": cur.isoformat(), "title": f"Ещё {rest} дн. — свободная программа",
                         "items": [f"с {ddmm(cur)} по {ddmm(co - timedelta(days=1))}: отдых, поездки по окрестностям"]})
        cur = co
    while cur < co:
        free = i % 2 == 1 and "beach" in dest.kinds
        days.append({"date": cur.isoformat(), "title": "Свободный день" if free else "Идея на день",
                     "items": ["пляж и отдых в отеле"] if free else [ideas[i // (2 if 'beach' in dest.kinds else 1) % len(ideas)]]})
        cur += timedelta(days=1)
        i += 1
    last = [f"выезд из отеля до {stay['checkout_time']}"]
    bd = dt(back["dep"])
    if b.get("transfer_out"):
        last.append(f"трансфер: {b['transfer_out']['name']}")
    if bd.date() == co:
        last.append(f"{bd:%H:%M} {'вылет' if back['mode'] == 'plane' else 'поезд'} {back['number']} → {back['to_name']}")
        days.append({"date": co.isoformat(), "title": "Домой", "items": last})
    else:
        days.append({"date": co.isoformat(), "title": "Выезд", "items": last})
        days.append({"date": bd.date().isoformat(), "title": "Домой",
                     "items": [f"{bd:%H:%M} вылет {back['number']} → {back['to_name']}"]})
    return days
