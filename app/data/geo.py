"""Города вылета и геометрия."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Origin:
    code: str
    name: str
    name_gen: str  # «из Москвы»
    lat: float
    lon: float
    tz: float
    aliases: tuple[str, ...]
    airport: str


ORIGINS: list[Origin] = [
    Origin("MOW", "Москва", "Москвы", 55.75, 37.62, 3, ("москв", "мск", "подмосков"), "Шереметьево"),
    Origin("LED", "Санкт-Петербург", "Санкт-Петербурга", 59.94, 30.31, 3,
           ("петербург", "питер", "спб", "санкт", "ленинград"), "Пулково"),
    Origin("SVX", "Екатеринбург", "Екатеринбурга", 56.84, 60.6, 5, ("екатеринбург", "екб", "ебург"), "Кольцово"),
    Origin("KZN", "Казань", "Казани", 55.79, 49.12, 3, ("казан",), "Казань"),
    Origin("OVB", "Новосибирск", "Новосибирска", 55.03, 82.92, 7, ("новосиб", "нск"), "Толмачёво"),
    Origin("KUF", "Самара", "Самары", 53.2, 50.15, 4, ("самар",), "Курумоч"),
    Origin("UFA", "Уфа", "Уфы", 54.74, 55.97, 5, ("уфа", "уфы", "уфе"), "Уфа"),
    Origin("GOJ", "Нижний Новгород", "Нижнего Новгорода", 56.33, 44.0, 3, ("нижний новг", "нижнего новг", "нижнем новг", "нижний", "нижегород"), "Стригино"),
    Origin("PEE", "Пермь", "Перми", 58.01, 56.25, 5, ("перм",), "Большое Савино"),
    Origin("VVO", "Владивосток", "Владивостока", 43.12, 131.89, 10, ("владивосток", "владик"), "Кневичи"),
]

class _OriginRegistry(dict):
    """10 городов из справочника + любые города вылета, которые агент узнал от LLM (коды «o-…»)."""

    loader = None  # устанавливается модулем app.dynamic

    def __missing__(self, key: str) -> Origin:
        if self.loader is not None:
            o = self.loader(key)
            if o is not None:
                self[key] = o
                return o
        raise KeyError(key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key) -> bool:
        return self.get(key) is not None


ORIGIN_BY_CODE: dict[str, Origin] = _OriginRegistry({o.code: o for o in ORIGINS})

# Пересадочные хабы за рубежом — для направлений без прямых рейсов из России
FOREIGN_HUBS = {
    "IST": Origin("IST", "Стамбул", "Стамбула", 41.26, 28.74, 3, (), "Стамбул"),
    "DXB": Origin("DXB", "Дубай", "Дубая", 25.25, 55.36, 4, (), "Дубай"),
    "BEG": Origin("BEG", "Белград", "Белграда", 44.82, 20.29, 1, (), "Белград"),
}
HUB_AIRLINES = {
    "IST": [("TK", "Turkish Airlines", False, True), ("PC", "Pegasus", True, True)],
    "DXB": [("EK", "Emirates", False, True), ("FZ", "flydubai", True, True)],
    "BEG": [("JU", "Air Serbia", False, True)],
}


def hub_point(code: str) -> Origin:
    return ORIGIN_BY_CODE.get(code) or FOREIGN_HUBS[code]

# Город вылета, который совпадает с направлением (не предлагаем «из Москвы в Москву»)
ORIGIN_SAME_DEST = {"MOW": "moscow", "LED": "spb", "KZN": "kazan"}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def flight_minutes(dist_km: float) -> int:
    """Чистое время в воздухе прямого рейса с учётом обходных маршрутов."""
    return int(dist_km / 800 * 60 + 40)
