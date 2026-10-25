"""Синтетический инвентарь: отели, перелёты, поезда, трансферы.

Все данные генерируются детерминированно (seed = параметры запроса), поэтому
один и тот же поиск всегда даёт один и тот же результат — это важно для
пересборки плана и воспроизводимых метрик. Реальных цен и наличия здесь нет.
"""
from __future__ import annotations

import hashlib
import math
import random
from datetime import date, datetime, timedelta
from functools import cache

from .data.destinations import DEST_BY_ID, Destination
from .data.geo import HUB_AIRLINES, ORIGIN_BY_CODE, flight_minutes, haversine_km, hub_point


def rng(*parts) -> random.Random:
    h = hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()
    return random.Random(int(h[:12], 16))


def r10(x: float) -> int:
    return int(round(x / 10.0)) * 10


def r100(x: float) -> int:
    return int(round(x / 100.0)) * 100


# ---------------------------------------------------------------- сезонность

def season_mult(dest: Destination, d: date) -> float:
    m = d.month
    if m in dest.high_months:
        mult = 1.3
    elif ((m % 12) + 1) in dest.high_months or (((m - 2) % 12) + 1) in dest.high_months:
        mult = 1.08
    else:
        mult = 0.85
    if (d.month == 12 and d.day >= 28) or (d.month == 1 and d.day <= 8):
        mult *= 1.55
    if d.month == 5 and d.day <= 10:
        mult *= 1.2
    return mult


def price_level(dest: Destination) -> float:
    return min(1.8, max(0.6, dest.hotel_base / 7000))


# ---------------------------------------------------------------- отели

FOREIGN_NAMES = {
    "beach": ["Sea Breeze", "Coral Bay", "Blue Lagoon", "Palm Garden", "Sunrise", "Aqua", "Golden Sand",
              "Marine", "Lagoon Park", "Royal Beach", "Family Club", "Serenity", "Azure", "Paradise Cove",
              "Sandy Shore", "Ocean Pearl", "Turquoise", "Laguna", "Wave", "Seashell"],
    "city": ["Old Town", "Central", "Boutique 19", "Grand", "Loft", "Park", "Riverside", "Art House",
             "Heritage", "Plaza", "Panorama", "Gallery", "Bazaar", "Corner", "Mosaic", "Garden Inn"],
}
FOREIGN_SUFFIX = {
    "beach": ["Resort & Spa", "Beach Resort", "Hotel", "Club", "Family Resort", "Villas", "Boutique Hotel", "Resort"],
    "city": ["Hotel", "Boutique Hotel", "Suites", "Residence", "Inn", "Hotel & Spa", "Apartments", "Design Hotel"],
}
RU_PREFIX = ["Отель", "Парк-отель", "Апарт-отель", "Бутик-отель", "Гостевой дом", "Эко-отель", "Курортный отель", "Гранд-отель"]
RU_WORDS = ["Лазурный", "Берёзка", "Кедр", "Сосновый бор", "Империал", "Ривьера", "Горный воздух", "Северная звезда",
            "Волна", "Причал", "Старый город", "Купеческий", "Лесная сказка", "Тайга", "Панорама", "Маяк",
            "Шале", "Усадьба", "Европа", "Ковчег"]

STAR_FACTOR = {2: 0.55, 3: 0.8, 4: 1.15, 5: 1.9}

MEAL_NAMES = {"RO": "без питания", "BB": "завтраки", "HB": "полупансион", "FB": "полный пансион",
              "AI": "всё включено", "UAI": "ультра всё включено"}


def _hotel_name(dest: Destination, r: random.Random, used: set[str], kind: str) -> str:
    """Уникальное в пределах направления имя: ключевое слово не повторяется."""
    ru = dest.domestic or dest.country in ("Абхазия",)
    words = RU_WORDS if ru else FOREIGN_NAMES[kind]
    free = [w for w in words if w not in used] or words
    word = r.choice(free)
    used.add(word)
    if ru:
        return f"{r.choice(RU_PREFIX)} «{word}»"
    return f"{word} {r.choice(FOREIGN_SUFFIX[kind])}"


@cache
def _hotels(dest_id: str) -> tuple[dict, ...]:
    dest = DEST_BY_ID[dest_id]
    kind = "beach" if "beach" in dest.kinds else "city"
    out = []
    used: set[str] = set()
    # архетипы гарантируют разнообразие выдачи в каждом направлении
    archetypes = ["family", "budget", "luxury", "couples", "pets", "standard", "standard", "budget",
                  "family", "standard", "boutique", "standard"]
    for i, arch in enumerate(archetypes):
        r = rng("hotel", dest_id, i)
        area = dest.areas[i % len(dest.areas)]
        if arch == "luxury":
            stars = 5
        elif arch == "budget":
            stars = r.choice([2, 3, 3])
        elif arch in ("family", "couples"):
            stars = r.choice([4, 4, 5])
        else:
            stars = r.choice([3, 3, 4, 4, 4, 5])
        beach_m = None
        if area.beach_m is not None:
            if arch in ("luxury", "family") or r.random() < 0.3:
                beach_m = r.choice([0, 30, 50, 80, 120])  # первая линия
            else:
                beach_m = int(area.beach_m + r.choice([100, 250, 400, 650, 900]))
        airport_km = round(max(2.0, area.airport_km * r.uniform(0.85, 1.15)), 1)
        center_km = round(max(0.1, area.center_km * r.uniform(0.7, 1.3)), 1)
        rating = round(min(9.7, max(6.2, 6.3 + stars * 0.5 + r.gauss(0, 0.45) + (0.3 if arch == "boutique" else 0))), 1)
        reviews = int(r.uniform(40, 2400) * (1.5 if stars >= 4 else 1))

        is_beachy = "beach" in dest.kinds and beach_m is not None
        kids_club = arch == "family" or (is_beachy and stars >= 4 and r.random() < 0.5)
        adults_only = arch == "couples" and not dest.domestic and is_beachy
        pool = is_beachy and (stars >= 3 or r.random() < 0.5) or (stars >= 4 and r.random() < 0.5)
        spa = stars >= 4 and r.random() < 0.7 or arch == "luxury"
        family_rooms = arch == "family" or (not adults_only and r.random() < 0.45)
        kitchen = arch == "pets" and dest.domestic or (arch == "standard" and r.random() < 0.2)
        pets_allowed = arch == "pets" or r.random() < (0.5 if dest.domestic else 0.2 if dest.pets in ("easy", "docs") else 0)
        if dest.pets == "banned":
            pets_allowed = False
        lively = "party" in area.vibe or "lively" in area.vibe
        vibe = "party" if ("party" in area.vibe and r.random() < 0.6) else ("lively" if lively else "quiet")
        if arch in ("couples", "boutique") and vibe == "party":
            vibe = "lively"
        if arch == "family" and vibe == "party":
            vibe = "lively"
        accessible = stars >= 4 and r.random() < 0.6

        # питание
        if dest.meal_default == "AI" and stars >= 3:
            meals = {"AI": 0}
            if stars >= 4:
                meals["UAI"] = r100(dest.hotel_base * 0.08)
        elif dest.meal_default == "HB":
            meals = {"BB": 0, "HB": r100(dest.hotel_base * 0.25), "FB": r100(dest.hotel_base * 0.4)}
            if stars >= 5:
                meals["AI"] = r100(dest.hotel_base * 0.6)
        else:
            meals = {"RO": 0, "BB": r100(max(400, dest.hotel_base * 0.09))}
            if is_beachy and stars >= 4:
                meals["HB"] = r100(dest.hotel_base * 0.22)
                if r.random() < 0.5:
                    meals["AI"] = r100(dest.hotel_base * 0.45)
            if arch in ("luxury", "boutique") or (stars >= 4 and r.random() < 0.5):
                meals = {k: v for k, v in meals.items() if k != "RO"}
                meals["BB"] = 0

        free_cancel_days = None if (arch == "budget" and r.random() < 0.6) else r.choice([1, 3, 3, 7, 14])
        early = "guaranteed" if stars >= 4 and r.random() < 0.6 else ("on_request" if r.random() < 0.7 else "none")
        price = dest.hotel_base * STAR_FACTOR[stars] * r.uniform(0.85, 1.2)
        if beach_m is not None and beach_m <= 120:
            price *= 1.12
        if arch == "pets" and not dest.domestic:
            pets_allowed = True

        h = {
            "id": f"{dest_id}-h{i}",
            "dest_id": dest_id,
            "name": _hotel_name(dest, r, used, kind),
            "archetype": arch,
            "stars": stars,
            "area": area.name,
            "airport_km": airport_km,
            "beach_m": beach_m,
            "center_km": center_km,
            "rating": rating,
            "reviews": reviews,
            "meals": meals,
            "kids_club": kids_club,
            "adults_only": adults_only,
            "pool": bool(pool),
            "spa": bool(spa),
            "family_rooms": family_rooms,
            "kitchen": bool(kitchen),
            "pets_allowed": pets_allowed,
            "pet_fee": r100(800 * price_level(dest)) if pets_allowed else 0,
            "vibe": vibe,
            "accessible": accessible,
            "checkin": "14:00" if r.random() < 0.5 else "15:00",
            "checkout": "12:00",
            "early_checkin": early,
            "free_cancel_days": free_cancel_days,
            "base_price": r100(price),
        }
        h["pros"], h["cons"] = _review_summary(h, dest, r)
        h["ideal_for"], h["not_for"] = _fit_tags(h, dest)
        out.append(h)
    return tuple(out)


def _review_summary(h: dict, dest: Destination, r: random.Random) -> tuple[list[str], list[str]]:
    pros, cons = [], []
    if h["beach_m"] is not None and h["beach_m"] <= 80:
        pros.append("свой пляж в паре шагов")
    if h["kids_club"]:
        pros.append("детский клуб и анимация")
    if h["rating"] >= 8.8:
        pros.append("гости хвалят сервис и чистоту")
    if h["vibe"] == "quiet":
        pros.append("тихо, хорошо высыпаться")
    if h["spa"]:
        pros.append("СПА")
    if h["center_km"] <= 1.0:
        pros.append("всё рядом пешком")
    if h["pets_allowed"]:
        pros.append("можно с питомцем")
    if h["kitchen"]:
        pros.append("кухня в номере")
    if "AI" in h["meals"] or "UAI" in h["meals"]:
        pros.append("разнообразное питание")
    if h["vibe"] == "party":
        cons.append("шумно вечером: рядом бары")
    if h["beach_m"] is not None and h["beach_m"] > 500:
        cons.append(f"до моря {h['beach_m']} м, нужен шаттл или прогулка")
    if h["airport_km"] > 60:
        cons.append(f"далеко от аэропорта ({int(h['airport_km'])} км)")
    if h["stars"] <= 3 and h["rating"] < 8:
        cons.append("номера уставшие, ремонт давно")
    if not h["pool"] and "beach" in dest.kinds:
        cons.append("нет бассейна")
    if h["free_cancel_days"] is None:
        cons.append("только невозвратный тариф")
    if not cons and r.random() < 0.6:
        cons.append(r.choice(["в сезон бывают очереди на ресторане", "Wi-Fi слабый в номерах", "маленькие номера"]))
    return pros[:4], cons[:3]


def _fit_tags(h: dict, dest: Destination) -> tuple[list[str], list[str]]:
    ideal, notfor = [], []
    if h["kids_club"] or h["family_rooms"]:
        ideal.append("families")
    if h["adults_only"] or h["archetype"] in ("couples", "boutique"):
        ideal.append("couples")
    if h["stars"] <= 3:
        ideal.append("budget")
    if h["pets_allowed"]:
        ideal.append("pets")
    if h["vibe"] == "quiet":
        ideal.append("quiet")
    if h["vibe"] == "party":
        ideal.append("party")
    if h["stars"] == 5:
        ideal.append("luxury")
    if h["adults_only"]:
        notfor.append("kids")
    if h["vibe"] == "party":
        notfor.extend(["small_kids", "quiet"])
    if h["beach_m"] is not None and h["beach_m"] > 600:
        notfor.append("sea")
    if h["airport_km"] > 80:
        notfor.append("short_trip")
    return ideal, notfor


def get_hotels(dest_id: str) -> list[dict]:
    return [dict(h) for h in _hotels(dest_id)]


def get_hotel(hotel_id: str) -> dict | None:
    dest_id = hotel_id.rsplit("-h", 1)[0]
    if dest_id not in DEST_BY_ID:
        return None
    for h in _hotels(dest_id):
        if h["id"] == hotel_id:
            return dict(h)
    return None


def rooms_needed(hotel: dict, adults: int, children_ages: list[int]) -> int:
    kids = len([a for a in children_ages if a >= 2])
    cap = 4 if hotel["family_rooms"] else 3
    return max(1, math.ceil(adults / 2), math.ceil((adults + kids) / cap))


def price_hotel(hotel: dict, check_in: date, check_out: date, adults: int, children_ages: list[int],
                meal: str | None = None, pets: bool = False) -> dict:
    """Цена проживания на весь срок для состава гостей."""
    dest = DEST_BY_ID[hotel["dest_id"]]
    nights = max(1, (check_out - check_in).days)
    rooms = rooms_needed(hotel, adults, children_ages)
    total_room = 0.0
    for n in range(nights):
        d = check_in + timedelta(days=n)
        r = rng("hprice", hotel["id"], d.isoformat())
        weekend = 1.08 if d.weekday() in (4, 5) and "city" in dest.kinds else 1.0
        total_room += hotel["base_price"] * season_mult(dest, d) * weekend * r.uniform(0.95, 1.05)
    total_room *= rooms
    kids_paying = [a for a in children_ages if a >= 2]
    extra_adults = max(0, adults - 2 * rooms)
    total_room += hotel["base_price"] * nights * (0.22 * len(kids_paying) + 0.35 * extra_adults)

    plans = hotel["meals"]
    plan = meal if meal in plans else min(plans, key=lambda k: plans[k])
    meal_cost = plans[plan] * nights * (adults + 0.5 * len(kids_paying))
    pet_cost = hotel["pet_fee"] * nights if pets and hotel["pets_allowed"] else 0
    flex = r100(total_room + meal_cost + pet_cost)
    rates = []
    if hotel["free_cancel_days"] is not None:
        rates.append({"code": "flex", "name": f"Бесплатная отмена за {hotel['free_cancel_days']} дн.",
                      "refundable": True, "free_cancel_days": hotel["free_cancel_days"], "price": flex})
    rates.append({"code": "nonref", "name": "Невозвратный", "refundable": False, "free_cancel_days": None,
                  "price": r100(flex * 0.88)})
    return {"nights": nights, "rooms": rooms, "meal": plan, "meal_name": MEAL_NAMES[plan],
            "pet_fee_total": pet_cost, "rates": rates}


# ---------------------------------------------------------------- перелёты

AIRLINES_INTL = [("SU", "Аэрофлот", False, True), ("S7", "S7 Airlines", False, True), ("U6", "Уральские авиалинии", False, True),
                 ("WZ", "Red Wings", False, True), ("N4", "Nordwind", False, True), ("ZF", "Azur Air", False, False)]
AIRLINES_DOM = [("SU", "Аэрофлот", False, True), ("S7", "S7 Airlines", False, True), ("DP", "Победа", True, True),
                ("U6", "Уральские авиалинии", False, True), ("FV", "Россия", False, True), ("5N", "Smartavia", True, True),
                ("UT", "ЮТэйр", False, True), ("WZ", "Red Wings", False, True)]
DEP_SLOTS = [(0, 55), (2, 10), (6, 40), (8, 5), (9, 50), (11, 30), (13, 15), (15, 40), (17, 20), (19, 45), (21, 30), (23, 10)]


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M")


def search_flights(origin_code: str, dest_id: str, day: date, direction: str = "out") -> list[dict]:
    dest = DEST_BY_ID[dest_id]
    if not dest.flights:
        return []
    o = ORIGIN_BY_CODE[origin_code]
    dist = haversine_km(o.lat, o.lon, dest.lat, dest.lon)
    if dist < 150:
        return []
    direct = origin_code in dest.direct_from
    a_code, a_name, a_tz = o.code, o.name, o.tz
    b_code, b_name, b_tz = dest.iata, dest.name, dest.tz
    if direction == "back":
        a_code, b_code = b_code, a_code
        a_name, b_name = b_name, a_name
        a_tz, b_tz = b_tz, a_tz
    r = rng("flights", origin_code, dest_id, day.isoformat(), direction)
    pool = AIRLINES_DOM if dest.domestic else AIRLINES_INTL
    days_ahead = (day - date.today()).days
    urgency = 1.3 if days_ahead < 10 else (1.12 if days_ahead < 25 else 1.0)
    base = (2600 + dist * 2.9) * season_mult(dest, day) * urgency

    options = []
    n_direct = r.randint(3, 5) if direct else 0
    foreign_hubs = list(dest.hubs) if not direct and dest.hubs else []
    slots = r.sample(DEP_SLOTS, k=min(len(DEP_SLOTS), n_direct + (4 if foreign_hubs else 2)))
    hub = dest.hub if dest.hub != origin_code else "MOW"
    for idx, (hh, mm) in enumerate(slots):
        is_direct = idx < n_direct
        leg_hub = foreign_hubs[idx % len(foreign_hubs)] if foreign_hubs and not is_direct else hub
        if not is_direct and (leg_hub == origin_code or _hub_is_dest(leg_hub, dest)):
            continue
        code, airline, lowcost, pet_cabin = r.choice(HUB_AIRLINES[leg_hub] if leg_hub in HUB_AIRLINES and not is_direct else pool)
        dep = datetime(day.year, day.month, day.day, hh, mm)
        if is_direct:
            dur = flight_minutes(dist) + r.randint(-10, 15)
            stops, via, layover = 0, None, 0
        else:
            h = hub_point(leg_hub)
            leg1 = flight_minutes(haversine_km(o.lat, o.lon, h.lat, h.lon))
            leg2 = flight_minutes(haversine_km(h.lat, h.lon, dest.lat, dest.lon))
            layover = r.choice([75, 110, 160, 240, 340, 480])
            dur = leg1 + leg2 + layover
            stops, via = 1, h.name
        arr = dep + timedelta(minutes=dur) + timedelta(hours=(b_tz - a_tz))
        price = base * r.uniform(0.85, 1.22) * (0.8 if lowcost else 1.0) * (0.86 if stops else 1.0)
        if hh < 5:
            price *= 0.92  # ночные рейсы дешевле
        refundable = (not lowcost) and r.random() < 0.25
        if refundable:
            price *= 1.25
        bag_included = not lowcost and r.random() < 0.85
        num = r.randint(100, 2999)
        options.append({
            "id": f"F-{code}{num}-{day.strftime('%m%d')}-{direction}",
            "mode": "plane",
            "carrier": airline,
            "number": f"{code} {num}",
            "from_code": a_code, "from_name": a_name,
            "to_code": b_code, "to_name": b_name,
            "dep": _fmt(dep), "arr": _fmt(arr),
            "duration_min": dur, "stops": stops, "via": via, "layover_min": layover,
            "price": r10(price),
            "baggage_included": bag_included,
            "bag_fee": 0 if bag_included else r100(2200 + dist * 0.3),
            "refundable": refundable,
            "fare_name": "Гибкий" if refundable else ("Лайт без багажа" if not bag_included else "Базовый с багажом"),
            "lowcost": lowcost,
            "pet_cabin": pet_cabin,
            "pet_fee": r100(2500 + dist * 0.4) if pet_cabin else 0,
            "class_name": "Эконом",
        })
    options.sort(key=lambda f: f["dep"])
    return options


def _hub_is_dest(hub: str, dest: Destination) -> bool:
    o = hub_point(hub)
    return haversine_km(o.lat, o.lon, dest.lat, dest.lon) < 150


def pax_price(item: dict, adults: int, children_ages: list[int], add_bags: bool = True) -> int:
    """Стоимость транспорта для всей группы (одно направление)."""
    p = item["price"]
    if item["mode"] == "plane":
        total = p * adults
        for a in children_ages:
            total += p * (0.1 if a < 2 else 0.75)
        seats = adults + len([a for a in children_ages if a >= 2])
        if add_bags and not item["baggage_included"]:
            total += item["bag_fee"] * max(1, math.ceil(seats * 0.75))
        return r10(total)
    total = p * adults
    for a in children_ages:
        total += 0 if a < 5 else p * 0.6
    return r10(total)


# ---------------------------------------------------------------- поезда

TRAIN_ROUTES: dict[tuple[str, str], list[tuple[str, int, list[tuple[int, int]], dict[str, int]]]] = {
    ("MOW", "spb"): [("Сапсан", 235, [(6, 50), (9, 50), (13, 40), (17, 40), (19, 25)], {"Эконом": 4300, "Бизнес": 11500}),
                     ("Красная стрела", 520, [(23, 55)], {"Купе": 6900, "СВ": 14500})],
    ("LED", "moscow"): [("Сапсан", 235, [(7, 0), (10, 0), (13, 30), (16, 0), (20, 0)], {"Эконом": 4300, "Бизнес": 11500}),
                        ("Красная стрела", 520, [(23, 55)], {"Купе": 6900, "СВ": 14500})],
    ("MOW", "kazan"): [("Ласточка", 690, [(6, 30)], {"Сидячий": 3100}),
                       ("Премиум", 720, [(21, 10)], {"Плацкарт": 2900, "Купе": 5600, "СВ": 11800})],
    ("KZN", "moscow"): [("Ласточка", 690, [(15, 50)], {"Сидячий": 3100}),
                        ("Премиум", 720, [(20, 30)], {"Плацкарт": 2900, "Купе": 5600, "СВ": 11800})],
    ("GOJ", "moscow"): [("Ласточка", 235, [(5, 10), (13, 45), (18, 30)], {"Сидячий": 1900})],
    ("MOW", "sochi"): [("Фирменный №104", 1440, [(11, 35)], {"Плацкарт": 5400, "Купе": 9800, "СВ": 21000}),
                       ("Двухэтажный №102", 1410, [(16, 50)], {"Купе": 8800, "СВ": 19000})],
    ("LED", "sochi"): [("Фирменный №036", 2190, [(15, 55)], {"Плацкарт": 6900, "Купе": 12500, "СВ": 26000})],
    ("MOW", "rosa"): [("Двухэтажный №102 (до Роза Хутор)", 1500, [(16, 50)], {"Купе": 9200, "СВ": 20000})],
    ("MOW", "anapa"): [("№012 «Анапа»", 1560, [(9, 20)], {"Плацкарт": 4900, "Купе": 9200, "СВ": 19500}),
                       ("Двухэтажный №018", 1500, [(21, 30)], {"Купе": 8400, "СВ": 18000})],
    ("LED", "anapa"): [("№ 046", 2040, [(13, 10)], {"Плацкарт": 6200, "Купе": 11000})],
    ("MOW", "karelia"): [("«Карелия» №018", 780, [(21, 30)], {"Плацкарт": 2800, "Купе": 5600, "СВ": 11000})],
    ("LED", "karelia"): [("Ласточка", 320, [(7, 10), (15, 30)], {"Сидячий": 2300})],
    ("MOW", "kislovodsk"): [("«Кисловодск» №004", 1500, [(12, 15)], {"Плацкарт": 4800, "Купе": 8900, "СВ": 18500})],
    ("MOW", "minsk"): [("«Беларусь» №001", 540, [(22, 50)], {"Купе": 5200, "СВ": 10500}),
                       ("Ласточка", 420, [(9, 10)], {"Сидячий": 3900})],
    ("LED", "minsk"): [("№ 052", 720, [(19, 40)], {"Плацкарт": 3200, "Купе": 6100})],
    ("MOW", "dagestan"): [("«Дагестан» №006", 2160, [(19, 40)], {"Плацкарт": 5900, "Купе": 10800})],
}
TRAIN_TERMINALS = {"MOW": "Москва", "LED": "Санкт-Петербург", "KZN": "Казань", "GOJ": "Нижний Новгород"}


def train_available(origin_code: str, dest_id: str) -> bool:
    return (origin_code, dest_id) in TRAIN_ROUTES


def search_trains(origin_code: str, dest_id: str, day: date, direction: str = "out") -> list[dict]:
    routes = TRAIN_ROUTES.get((origin_code, dest_id))
    if not routes:
        return []
    dest = DEST_BY_ID[dest_id]
    o = ORIGIN_BY_CODE[origin_code]
    out = []
    for name, dur, deps, classes in routes:
        for hh, mm in deps:
            r = rng("train", origin_code, dest_id, day.isoformat(), direction, name, hh)
            if direction == "back":
                hh = (hh + 7) % 24  # обратные поезда ходят в другое время
            dep = datetime(day.year, day.month, day.day, hh, mm)
            arr = dep + timedelta(minutes=dur)
            num = r.randint(1, 199)
            for cls, price in classes.items():
                p = price * season_mult(dest, day) ** 0.6 * r.uniform(0.9, 1.15)
                a_name, b_name = TRAIN_TERMINALS.get(origin_code, o.name), dest.name
                if direction == "back":
                    a_name, b_name = b_name, a_name
                out.append({
                    "id": f"T-{num:03d}-{cls}-{day.strftime('%m%d')}-{direction}-{hh}",
                    "mode": "train", "carrier": "РЖД / ФПК", "number": f"{name}",
                    "from_code": a_name, "from_name": a_name, "to_code": b_name, "to_name": b_name,
                    "dep": _fmt(dep), "arr": _fmt(arr), "duration_min": dur, "stops": 0, "via": None, "layover_min": 0,
                    "price": r10(p), "baggage_included": True, "bag_fee": 0,
                    "refundable": True, "fare_name": cls, "lowcost": False,
                    "pet_cabin": cls not in ("Бизнес",), "pet_fee": 1200 if cls not in ("Бизнес",) else 0,
                    "class_name": cls,
                })
    out.sort(key=lambda t: (t["dep"], t["price"]))
    return out


# ---------------------------------------------------------------- трансферы

def search_transfers(hotel: dict, when: str, adults: int, children_ages: list[int], hub: str = "airport") -> list[dict]:
    dest = DEST_BY_ID[hotel["dest_id"]]
    pl = price_level(dest)
    at = datetime.fromisoformat(when)
    dist = hotel["airport_km"] if hub == "airport" else max(1.0, hotel["center_km"] + 1.5)
    pax = adults + len(children_ages)
    small_kids = any(a < 7 for a in children_ages)
    out = []
    if dest.id == "maldives" and hub == "airport":
        if dist <= 30:
            out.append({"id": f"TR-boat-{hotel['id']}", "kind": "speedboat", "name": "Скоростной катер",
                        "duration_min": int(dist * 1.6 + 20), "price": r100(9000 * pax), "child_seat": True,
                        "hours": (0, 24), "note": "встреча у стойки отеля в аэропорту"})
        else:
            out.append({"id": f"TR-seaplane-{hotel['id']}", "kind": "seaplane", "name": "Гидросамолёт",
                        "duration_min": 50, "price": r100(36000 * pax), "child_seat": True, "hours": (6, 16),
                        "note": "летает только в светлое время, 06:00–16:00"})
            out.append({"id": f"TR-boat-{hotel['id']}", "kind": "speedboat", "name": "Скоростной катер",
                        "duration_min": int(dist * 1.8 + 30), "price": r100(14000 * pax), "child_seat": True,
                        "hours": (6, 22), "note": "дольше, но дешевле и не зависит от светового дня"})
    else:
        cars = 1 if pax <= 4 else 2
        taxi_min = int(dist / 45 * 60 + 12)
        out.append({"id": f"TR-taxi-{hotel['id']}-{hub}", "kind": "taxi", "name": "Такси" + (" (минивэн)" if pax > 4 else ""),
                    "duration_min": taxi_min, "price": r100((350 + 42 * dist) * pl * (1.5 if cars > 1 else 1)),
                    "child_seat": False, "hours": (0, 24), "note": "детские кресла не гарантированы" if small_kids else ""})
        out.append({"id": f"TR-private-{hotel['id']}-{hub}", "kind": "private", "name": "Индивидуальный трансфер",
                    "duration_min": taxi_min, "price": r100((900 + 58 * dist) * pl * (1.4 if cars > 1 else 1)),
                    "child_seat": True, "hours": (0, 24),
                    "note": "встреча с табличкой, ожидание при задержке рейса, детские кресла"})
        if "beach" in dest.kinds and dist > 6 and hub == "airport":
            out.append({"id": f"TR-shuttle-{hotel['id']}", "kind": "shuttle", "name": "Групповой шаттл",
                        "duration_min": int(taxi_min * 1.5 + 30),
                        "price": r100(650 * pl * (adults + 0.5 * len([a for a in children_ages if a >= 2]))),
                        "child_seat": False, "hours": (7, 22), "note": "развозит по нескольким отелям, работает 07:00–22:00"})
        if ("city" in dest.kinds or hub == "station") and dist <= 45:
            out.append({"id": f"TR-public-{hotel['id']}-{hub}", "kind": "public",
                        "name": "Аэроэкспресс / метро" if hub == "airport" else "Метро / автобус",
                        "duration_min": int(dist / 28 * 60 + 20), "price": r100(250 * pl * pax),
                        "child_seat": False, "hours": (6, 23), "note": "дёшево, но с чемоданами и детьми неудобно"})
    for t in out:
        h0, h1 = t["hours"]
        t["available"] = h0 <= at.hour < h1 or (h0 == 0 and h1 == 24)
        t["hub"] = hub
    return out
