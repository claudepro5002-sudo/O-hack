"""Любое место мира через LLM: «карточка места» → полноценное направление.

Если пользователь называет город, остров, страну или регион вне ручного каталога,
агент спрашивает у LLM карточку места (координаты, аэропорт, климат, районы,
что посмотреть, риски), проверяет её и превращает в Destination. Дальше работает
обычный конвейер: отели, рейсы, трансферы, проверка состыковок, корзина.
Визы, прямые рейсы и уровень цен по возможности сверяются со справочником world_meta.
Карточки кэшируются в SQLite — повторный запрос не тратит токены.
"""
from __future__ import annotations

import hashlib
import logging
import re
from concurrent.futures import ThreadPoolExecutor

from . import llm, store
from .data import world_meta as wm
from .data.destinations import DEST_BY_ID, DESTINATIONS, Area, Destination
from .data.geo import ORIGIN_BY_CODE, ORIGINS, Origin

log = logging.getLogger("agent.dynamic")

TYPES = {"beach", "city", "nature", "ski"}
TAGS = {"sea", "warm", "culture", "food", "party", "quiet", "romantic", "nature", "mountains", "ski", "shopping", "wellness",
        "diving", "northern_lights", "kids", "budget", "luxury", "all_inclusive", "surf", "adventure", "city"}
VIBES = {"family", "quiet", "lively", "party", "luxury", "budget", "city", "culture", "food", "nature", "ski", "kids", "romantic",
         "wellness", "surf", "diving", "shopping"}


def norm_key(q: str) -> str:
    return re.sub(r"\s+", " ", q.lower().replace("ё", "е")).strip(" .,!?«»\"")


def dest_id_for(name: str) -> str:
    return "x-" + hashlib.md5(norm_key(name).encode()).hexdigest()[:10]


def catalog_match(name: str) -> str | None:
    """Если место уже есть в ручном каталоге — используем его, а не карточку от LLM."""
    t = norm_key(name)
    for d in DESTINATIONS:
        if norm_key(d.name) == t or any(t.startswith(a.strip()) for a in d.aliases if len(a.strip()) >= 4):
            return d.id
    return None


# ---------------------------------------------------------------- проверка карточки

def _num(v, lo, hi, default):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return x if lo <= x <= hi else default


def _months12(v, lo, hi) -> tuple[int, ...] | None:
    if not isinstance(v, list) or len(v) != 12:
        return None
    out = []
    for x in v:
        try:
            out.append(int(round(max(lo, min(hi, float(x))))))
        except (TypeError, ValueError):
            return None
    return tuple(out)


def _month_list(v) -> tuple[int, ...]:
    if not isinstance(v, list):
        return ()
    return tuple(sorted({int(m) for m in v if isinstance(m, (int, float)) and 1 <= int(m) <= 12}))


def _loc_forms(name: str) -> tuple[str, str]:
    """Грубое склонение для «в Х / в Х-е», если LLM не прислала формы."""
    n = name
    low = n.lower()
    if low.endswith(("о", "е", "и", "у", "ю", "э")) or " " in n:
        return f"в {n}", f"в {n}"
    if low.endswith("а"):
        return f"в {n[:-1]}е", f"в {n[:-1]}у"
    if low.endswith("я"):
        return f"в {n[:-1]}и", f"в {n[:-1]}ю"
    if low.endswith("ь"):
        return f"в {n[:-1]}и", f"в {n}"
    if low.endswith("й"):
        return f"в {n[:-1]}е", f"в {n}"
    return f"в {n}е", f"в {n}"


def build_destination(card: dict, dest_id: str) -> Destination | None:
    name = str(card.get("name") or "").strip()[:60]
    lat, lon = _num(card.get("lat"), -90, 90, None), _num(card.get("lon"), -180, 180, None)
    air = _months12(card.get("air_max"), -50, 50)
    if not name or lat is None or lon is None or air is None:
        return None
    cc = str(card.get("cc") or "").upper()[:2]
    if cc == "AQ" or lat < -60:  # на континент Антарктида нет рейсов и отелей — только круизы (см. GATEWAYS)
        return None
    country = wm.COUNTRY_RU.get(cc) or str(card.get("country") or "").strip()[:40] or "—"
    sea = _months12(card.get("sea"), -2, 35) if card.get("sea") else None
    rain = _months12(card.get("rain_days"), 0, 31) or tuple(8 for _ in range(12))
    types = tuple(t for t in card.get("types", []) if t in TYPES) or ("city",)
    if sea is None and "beach" in types:
        types = tuple(t for t in types if t != "beach") or ("city",)
    tags = tuple(t for t in card.get("tags", []) if t in TAGS)
    if sea and "sea" not in tags:
        tags += ("sea",)
    swim = _month_list(card.get("swim_months")) or (tuple(m for m in range(1, 13) if sea and sea[m - 1] >= 22) if sea else ())
    best = _month_list(card.get("best_months")) or tuple(m for m in range(1, 13) if 20 <= air[m - 1] <= 30) or (6, 7, 8)
    airport_km = _num(card.get("airport_km"), 1, 300, 25.0)
    areas = []
    for a in (card.get("areas") or [])[:4]:
        if not isinstance(a, dict) or not a.get("name"):
            continue
        beach = a.get("beach_m")
        areas.append(Area(str(a["name"])[:40], _num(a.get("airport_km"), 1, 400, airport_km),
                          int(_num(beach, 0, 5000, 300)) if (beach is not None and sea) else None,
                          _num(a.get("center_km"), 0, 300, 2.0), tuple(v for v in a.get("vibe", []) if v in VIBES)))
    if not areas:
        areas = [Area("Центр", airport_km, 300 if sea else None, 0.5, ("city", "lively")),
                 Area("Тихий район", airport_km * 1.1, 600 if sea else None, 4.0, ("quiet", "family"))]
    visa, visa_note = wm.VISA.get(cc, ("visa", str(card.get("visa_note") or "Проверьте визовый режим")[:120]))
    if cc not in wm.VISA and card.get("visa_note"):
        note = str(card["visa_note"])[:120]
        visa = "none" if re.search(r"без виз|не нужн|не требует", note.lower()) else "visa"
        visa_note = note
    domestic = cc == "RU"
    direct = cc in wm.DIRECT_CC if cc else bool(card.get("direct_flights_from_moscow"))
    continent = {"RU": "EU"}.get(cc)
    hubs = () if direct else wm.HUBS_FOR_CONTINENT.get(continent or _continent(lat, lon), ("IST", "DXB"))
    price = wm.PRICE_BY_CC.get(cc) or int(_num(card.get("hotel_price_rub"), 1500, 150000, 8000))
    risks = {}
    for k, v in (card.get("risks") or {}).items():
        try:
            m = int(k)
        except (TypeError, ValueError):
            continue
        if 1 <= m <= 12 and isinstance(v, str):
            risks[m] = v[:160]
    if not direct and not domestic:
        risks[0] = f"прямых рейсов из России нет — лететь с пересадкой ({', '.join(_hub_names(hubs))})"
    highlights = tuple(str(h)[:90] for h in (card.get("highlights") or []) if h)[:5] or (
        f"Прогулка по центру: {name}", "Местная кухня", "Музеи и смотровые площадки")
    loc, acc = card.get("name_loc"), card.get("name_acc")
    if not (isinstance(loc, str) and isinstance(acc, str) and loc and acc):
        loc, acc = _loc_forms(name)
    return Destination(
        id=dest_id, name=name, name_loc=loc[:70], name_acc=acc[:70], country=country, flag=wm.flag(cc),
        iata=str(card.get("iata") or "???")[:4].upper(), lat=lat, lon=lon, tz=_num(card.get("tz"), -12, 14, round(lon / 15)),
        kinds=types, tags=tags, air=air, sea=sea, rain=rain, high_months=best,
        visa=visa, visa_note=visa_note, hotel_base=price,
        daily_spend=int(_num(card.get("daily_spend_rub"), 500, 50000, price * 0.35)),
        domestic=domestic, areas=tuple(areas), highlights=highlights, aliases=(),
        pets=wm.PETS_BY_CC.get(cc, "easy" if domestic else "docs"),
        meal_default="AI" if "all_inclusive" in tags else "BB",
        swim_months=swim, ski_months=_month_list(card.get("ski_months")), lights_months=(10, 11, 12, 1, 2, 3) if "northern_lights" in tags else (),
        flights=True, direct_from=("MOW", "LED") if direct else (), hub="MOW", hubs=hubs, risks=risks, source="llm",
    )


def _continent(lat: float, lon: float) -> str:
    if lat < -60:
        return "AN"
    if -170 <= lon <= -30:
        return "NA" if lat > 12 else "SA"
    if lon > 110 and lat < 0:
        return "OC"
    if -20 <= lon <= 55 and lat < 35:
        return "AF"
    if lon < 40 and lat >= 35:
        return "EU"
    return "AS"


def _hub_names(hubs) -> list[str]:
    from .data.geo import hub_point
    return [hub_point(h).name for h in hubs]


# ---------------------------------------------------------------- разрешение места

def _load(dest_id: str) -> Destination | None:
    card = store.get_place("id:" + dest_id)
    return build_destination(card, dest_id) if card else None


DEST_BY_ID.loader = _load  # type: ignore[attr-defined]


def place(query: str) -> str | None:
    """Конкретное место → id направления (из каталога или по карточке LLM)."""
    cat = catalog_match(query)
    if cat:
        return cat
    key = "q:" + norm_key(query)
    cached = store.get_place(key)
    card = cached if cached else llm.place_card(query)
    if not card or card.get("kind") not in (None, "place"):
        return None
    dest_id = dest_id_for(card.get("name") or query)
    d = build_destination(card, dest_id)
    if d is None:
        return None
    store.save_place(key, card)
    store.save_place("id:" + dest_id, card)
    DEST_BY_ID[dest_id] = d
    return dest_id


def _resolve_card(query: str, card: dict) -> dict | None:
    """Карточка → направление(я); None, если по ней нечего предложить."""
    kind = card.get("kind") or "place"
    if kind == "place":
        dest_id = dest_id_for(card.get("name") or query)
        d = build_destination(card, dest_id)
        if d is None:
            llm.last_error = "модель прислала неполные сведения о месте (нет координат или климата)"
            return None
        store.save_place("id:" + dest_id, card)
        DEST_BY_ID[dest_id] = d
        return {"kind": "place", "dest_id": dest_id, "name": d.name}
    label = str(card.get("name") or query)[:60]
    names = [str(n) for n in (card.get("suggest") or []) if n][:4]
    if not names:  # страна/регион без конкретных мест — спросим идеи отдельно
        names = llm.suggest_places(f"поездка: {label}, лучшие города и курорты", "Москва")[:4]
    with ThreadPoolExecutor(max_workers=4) as ex:
        ids = list(dict.fromkeys(i for i in ex.map(place, names) if i))
    if not ids:
        llm.last_error = llm.last_error or f"модель не предложила конкретных мест для «{label}»"
        return None
    return {"kind": kind, "label": label, "note": str(card.get("note") or "")[:300], "dest_ids": ids}


def resolve(query: str) -> dict | None:
    """Разобрать названное пользователем место.

    → {"kind": "place", "dest_id"} | {"kind": "region"|"unreachable", "label", "note", "dest_ids": [...]} | None
    """
    cat = catalog_match(query)
    if cat:
        return {"kind": "place", "dest_id": cat}
    key = "q:" + norm_key(query)
    cached = store.get_place(key)
    res = _resolve_card(query, cached) if cached else None
    if res is None:  # в кэше нет или там неудачная карточка — спрашиваем модель заново
        card = llm.place_card(query)
        res = _resolve_card(query, card) if card else None
        if res is not None:
            store.save_place(key, card)  # кэшируем только карточки, по которым что-то нашлось
    return res


# ---------------------------------------------------------------- города вылета

def _gen_form(name: str) -> str:
    """Родительный падеж для «из Х», если LLM не прислала форму."""
    low = name.lower()
    if low.endswith(("о", "е", "и", "у", "ю", "э")) or " " in name:
        return name
    if low.endswith("а"):
        return name[:-1] + ("и" if low[-2:-1] in "гкхжчшщ" else "ы")
    if low.endswith(("я", "ь", "й")):
        return name[:-1] + "и" if not low.endswith("й") else name[:-1] + "я"
    return name + "а"


def build_origin(card: dict, code: str) -> Origin | None:
    name = str(card.get("name") or "").strip()[:60]
    lat, lon = _num(card.get("lat"), -90, 90, None), _num(card.get("lon"), -180, 180, None)
    if not name or lat is None or lon is None:
        return None
    gen = card.get("name_gen")
    gen = str(gen).strip()[:60] if isinstance(gen, str) and gen.strip() else _gen_form(name)
    return Origin(code, name, gen, lat, lon, _num(card.get("tz"), -12, 14, round(lon / 15)), (),
                  str(card.get("iata") or name)[:20])


def _load_origin(code: str) -> Origin | None:
    card = store.get_place("id:" + code)
    return build_origin(card, code) if card else None


ORIGIN_BY_CODE.loader = _load_origin  # type: ignore[attr-defined]


def origin(query: str) -> str | None:
    """Любой город вылета → код (свой справочник или карточка от LLM)."""
    q = norm_key(query)
    for o in ORIGINS:
        if norm_key(o.name) == q or any(q.startswith(a) for a in o.aliases):
            return o.code
    key = "q:" + q
    card = store.get_place(key) or llm.place_card(query)
    if not card or card.get("kind") not in (None, "place"):
        return None
    for o in ORIGINS:  # LLM привела к известному городу («Питер» → Санкт-Петербург)
        if norm_key(o.name) == norm_key(str(card.get("name") or "")):
            return o.code
    code = "o-" + hashlib.md5(norm_key(card.get("name") or query).encode()).hexdigest()[:10]
    o = build_origin(card, code)
    if o is None:
        return None
    store.save_place(key, card)
    store.save_place("id:" + code, card)
    ORIGIN_BY_CODE[code] = o
    return code


# ---------------------------------------------------------------- идеи направлений от LLM

def suggest(summary: str, origin_name: str) -> tuple[list[str], list[str]]:
    """LLM предлагает места под запрос → (id направлений, названия). Кэшируется по тексту запроса."""
    key = "s:" + norm_key(origin_name + "|" + summary)
    cached = store.get_place(key)
    names = cached.get("places", []) if cached else llm.suggest_places(summary, origin_name)
    if not names:
        return [], []
    store.save_place(key, {"places": names})
    with ThreadPoolExecutor(max_workers=5) as ex:
        ids = [i for i in ex.map(place, names) if i]
    return list(dict.fromkeys(ids)), names
