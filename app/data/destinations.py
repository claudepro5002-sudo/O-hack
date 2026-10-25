"""Справочник направлений.

ВНИМАНИЕ: это синтетические/справочные данные, собранные вручную для прототипа.
Климат — усреднённые климатические нормы (дневной максимум воздуха, температура
воды), цены — модельные уровни. Реальных данных Островка здесь нет.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Area:
    name: str
    airport_km: float
    beach_m: int | None  # до пляжа, None — пляжа нет
    center_km: float
    vibe: tuple[str, ...] = ()


@dataclass(frozen=True)
class Destination:
    id: str
    name: str
    name_loc: str  # «в Анталье»
    name_acc: str  # «в Анталью»
    country: str
    flag: str
    iata: str
    lat: float
    lon: float
    tz: float
    kinds: tuple[str, ...]
    tags: tuple[str, ...]
    air: tuple[int, ...]  # 12 месяцев, дневной максимум
    sea: tuple[int, ...] | None
    rain: tuple[int, ...]  # дождливых дней в месяц
    high_months: tuple[int, ...]
    visa: str  # none | on_arrival | evisa
    visa_note: str
    hotel_base: int  # ₽ за ночь, номер на двоих, 3-4*
    daily_spend: int  # ₽ в день на человека на месте
    domestic: bool
    areas: tuple[Area, ...]
    highlights: tuple[str, ...]
    aliases: tuple[str, ...]
    pets: str  # easy | docs | hard | banned
    meal_default: str = "BB"
    swim_months: tuple[int, ...] = ()
    ski_months: tuple[int, ...] = ()
    lights_months: tuple[int, ...] = ()
    flights: bool = True
    direct_from: tuple[str, ...] = ("MOW", "LED")
    hub: str = "MOW"  # пересадочный хаб для регионов
    hubs: tuple[str, ...] = ()  # зарубежные хабы, если прямых рейсов из России нет (IST, DXB, BEG)
    source: str = "catalog"  # catalog | llm — откуда взялись сведения о месте
    risks: dict = field(default_factory=dict)  # месяц -> текст риска


M = range(1, 13)


def _months(*ms: int) -> tuple[int, ...]:
    return tuple(ms)


DESTINATIONS: list[Destination] = [
    Destination(
        id="antalya", name="Анталья", name_loc="в Анталье", name_acc="в Анталью",
        country="Турция", flag="🇹🇷", iata="AYT", lat=36.9, lon=30.7, tz=3,
        kinds=("beach",), tags=("sea", "warm", "kids", "all_inclusive", "budget", "party", "food"),
        air=(15, 16, 18, 21, 25, 30, 34, 34, 31, 26, 21, 17),
        sea=(17, 17, 17, 18, 21, 24, 27, 28, 27, 24, 21, 19),
        rain=(11, 9, 7, 5, 3, 1, 0, 0, 1, 4, 7, 11),
        high_months=_months(6, 7, 8, 9), visa="none", visa_note="Без визы до 60 дней",
        hotel_base=7500, daily_spend=2500, domestic=False,
        areas=(
            Area("Лара", 14, 80, 12, ("family", "lively")),
            Area("Белек", 35, 60, 40, ("family", "luxury", "quiet")),
            Area("Кемер", 58, 120, 45, ("nature", "lively")),
            Area("Сиде", 65, 150, 70, ("family", "quiet")),
            Area("Коньяалты", 13, 250, 4, ("city", "lively")),
        ),
        highlights=("Старый город Калеичи и водопады Дюден", "Аквапарк и дельфинарий в Ларе",
                    "Канатная дорога на Тахталы", "Античные Перге и Аспендос", "Рафтинг в каньоне Кёпрюлю"),
        aliases=("анталь", "анталий", "лара", "белек", "кемер", "сиде", "аланьи", "алани"),
        pets="docs", meal_default="AI", swim_months=_months(5, 6, 7, 8, 9, 10),
        direct_from=("MOW", "LED", "SVX", "KZN", "OVB", "KUF", "UFA", "GOJ", "PEE"),
        risks={11: "в ноябре море уже прохладное (~21°), часть отелей закрывается на зиму",
               4: "в апреле море холодное (~18°), купаться — только в бассейне"},
    ),
    Destination(
        id="istanbul", name="Стамбул", name_loc="в Стамбуле", name_acc="в Стамбул",
        country="Турция", flag="🇹🇷", iata="IST", lat=41.0, lon=28.97, tz=3,
        kinds=("city",), tags=("city", "culture", "food", "shopping", "romantic", "party"),
        air=(9, 10, 12, 17, 22, 27, 29, 29, 26, 20, 15, 11), sea=None,
        rain=(16, 14, 12, 9, 7, 5, 3, 4, 6, 10, 13, 16),
        high_months=_months(4, 5, 6, 9, 10, 12), visa="none", visa_note="Без визы до 60 дней",
        hotel_base=8000, daily_spend=3500, domestic=False,
        areas=(
            Area("Султанахмет", 45, None, 0.5, ("culture", "quiet")),
            Area("Бейоглу", 42, None, 1.0, ("lively", "party", "food")),
            Area("Бешикташ", 40, None, 3.0, ("lively", "food")),
            Area("Кадыкёй", 30, None, 5.0, ("food", "quiet")),
        ),
        highlights=("Айя-София и Голубая мечеть", "Прогулка на пароме по Босфору", "Гранд-базар и Египетский рынок",
                    "Ужин с видом на Галатскую башню", "Принцевы острова"),
        aliases=("стамбул", "истанбул"), pets="docs",
        direct_from=("MOW", "LED", "SVX", "KZN", "KUF", "UFA"),
    ),
    Destination(
        id="dubai", name="Дубай", name_loc="в Дубае", name_acc="в Дубай",
        country="ОАЭ", flag="🇦🇪", iata="DXB", lat=25.2, lon=55.27, tz=4,
        kinds=("beach", "city"), tags=("sea", "warm", "city", "shopping", "luxury", "kids", "food"),
        air=(24, 25, 28, 33, 38, 40, 41, 41, 39, 35, 30, 26),
        sea=(21, 21, 23, 25, 28, 30, 32, 33, 32, 30, 27, 23),
        rain=(3, 3, 3, 1, 0, 0, 0, 0, 0, 0, 1, 2),
        high_months=_months(11, 12, 1, 2, 3), visa="none", visa_note="Без визы до 90 дней",
        hotel_base=12000, daily_spend=5000, domestic=False,
        areas=(
            Area("Джумейра-Бич", 30, 50, 15, ("family", "luxury")),
            Area("Дубай Марина", 38, 150, 25, ("lively", "party", "city")),
            Area("Даунтаун", 15, None, 1.0, ("city", "shopping")),
            Area("Дейра", 6, None, 8.0, ("budget", "city")),
            Area("Пальма Джумейра", 40, 30, 28, ("luxury", "quiet", "family")),
        ),
        highlights=("Бурдж-Халифа и Дубай Молл", "Аквапарк Atlantis Aquaventure", "Сафари по пустыне",
                    "Старый Дубай и абра через залив", "Музей будущего"),
        aliases=("дубай", "дубае", "оаэ", "эмират", "арабск"), pets="hard",
        swim_months=_months(1, 2, 3, 4, 5, 10, 11, 12),
        direct_from=("MOW", "LED", "SVX", "KZN", "OVB", "KUF", "UFA", "GOJ", "PEE", "VVO"),
        risks={6: "в июне–августе днём +40° и выше, гулять можно только вечером",
               7: "в июле днём +41°, на улице тяжело, особенно детям",
               8: "в августе днём +41°, на улице тяжело, особенно детям"},
    ),
    Destination(
        id="hurghada", name="Хургада", name_loc="в Хургаде", name_acc="в Хургаду",
        country="Египет", flag="🇪🇬", iata="HRG", lat=27.26, lon=33.81, tz=2,
        kinds=("beach",), tags=("sea", "warm", "diving", "budget", "all_inclusive", "kids"),
        air=(22, 23, 25, 29, 33, 35, 36, 36, 34, 31, 27, 23),
        sea=(22, 21, 22, 23, 25, 26, 28, 28, 28, 27, 25, 23),
        rain=(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1),
        high_months=_months(10, 11, 12, 1, 3, 4), visa="on_arrival", visa_note="Виза по прилёту, $25",
        hotel_base=5500, daily_spend=2000, domestic=False,
        areas=(
            Area("Сахл-Хашиш", 25, 40, 20, ("luxury", "quiet", "family")),
            Area("Эль-Гуна", 25, 100, 22, ("luxury", "romantic", "lively")),
            Area("Центр (Шератон-роуд)", 6, 200, 1.0, ("budget", "lively")),
            Area("Макади-Бей", 32, 60, 30, ("family", "quiet")),
        ),
        highlights=("Снорклинг на рифах и остров Гифтун", "Морская прогулка с дельфинами", "Экскурсия в Луксор",
                    "Квадроциклы по пустыне", "Аквапарк Jungle"),
        aliases=("хургад", "эль-гун", "макади"), pets="docs", meal_default="AI",
        swim_months=tuple(M),
        direct_from=("MOW", "LED", "SVX", "KZN", "OVB", "KUF", "UFA", "GOJ", "PEE"),
        risks={1: "в январе вечером прохладно (+14°) и бывает ветрено", 2: "в феврале вода ~21°, ветрено"},
    ),
    Destination(
        id="sharm", name="Шарм-эш-Шейх", name_loc="в Шарм-эш-Шейхе", name_acc="в Шарм-эш-Шейх",
        country="Египет", flag="🇪🇬", iata="SSH", lat=27.92, lon=34.33, tz=2,
        kinds=("beach",), tags=("sea", "warm", "diving", "kids", "all_inclusive", "budget", "quiet"),
        air=(21, 22, 25, 29, 33, 36, 37, 37, 35, 31, 27, 23),
        sea=(22, 21, 22, 23, 25, 27, 28, 28, 28, 27, 25, 23),
        rain=(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1),
        high_months=_months(10, 11, 12, 1, 3, 4), visa="none", visa_note="Без визы: синайский штамп при отдыхе на Синае",
        hotel_base=6000, daily_spend=2000, domestic=False,
        areas=(
            Area("Наама-Бей", 10, 150, 1.0, ("lively", "party")),
            Area("Набк", 8, 80, 15, ("family", "quiet")),
            Area("Шаркс-Бей", 5, 100, 6, ("family", "diving")),
            Area("Рас-Ум-Сид", 17, 200, 5, ("quiet", "diving")),
        ),
        highlights=("Национальный парк Рас-Мохаммед", "Снорклинг у рифа прямо с понтона", "Гора Моисея и монастырь Св. Екатерины",
                    "Старый рынок", "Цветной каньон"),
        aliases=("шарм", "синай", "наама"), pets="docs", meal_default="AI", swim_months=tuple(M),
        direct_from=("MOW", "LED", "SVX", "KZN", "KUF", "UFA"),
    ),
    Destination(
        id="phuket", name="Пхукет", name_loc="на Пхукете", name_acc="на Пхукет",
        country="Таиланд", flag="🇹🇭", iata="HKT", lat=7.88, lon=98.39, tz=7,
        kinds=("beach",), tags=("sea", "warm", "party", "nature", "food", "romantic"),
        air=(32, 33, 33, 33, 32, 31, 31, 31, 30, 30, 31, 31),
        sea=(28, 28, 29, 29, 29, 29, 28, 28, 28, 28, 28, 28),
        rain=(3, 2, 5, 12, 20, 19, 19, 19, 22, 21, 13, 6),
        high_months=_months(12, 1, 2, 3), visa="none", visa_note="Без визы до 30 дней",
        hotel_base=6500, daily_spend=2800, domestic=False,
        areas=(
            Area("Патонг", 38, 150, 1.0, ("party", "lively")),
            Area("Ката", 47, 100, 3.0, ("family", "lively")),
            Area("Карон", 44, 200, 2.0, ("family", "quiet")),
            Area("Банг-Тао", 22, 80, 6.0, ("luxury", "quiet", "family")),
            Area("Май-Кхао", 6, 120, 15, ("quiet", "nature")),
        ),
        highlights=("Острова Пхи-Пхи и залив Пханг-Нга", "Большой Будда и смотровая Промтеп", "Ночной рынок и тайская кухня",
                    "Слоновий заповедник", "Снорклинг на Симиланах (ноябрь–апрель)"),
        aliases=("пхукет", "таиланд", "тайланд", "патонг", "тайск"), pets="hard",
        swim_months=tuple(M), direct_from=("MOW", "LED", "SVX", "OVB", "KZN"),
        risks={5: "с мая по октябрь — сезон дождей и волн, на западных пляжах купаться опасно",
               6: "сезон дождей и сильных волн, на западных пляжах часто красные флаги",
               7: "сезон дождей и сильных волн", 8: "сезон дождей и сильных волн",
               9: "сентябрь — самый дождливый месяц", 10: "октябрь — ещё сезон дождей, шторма",
               11: "начало сухого сезона: первая половина ноября ещё бывает дождливой"},
    ),
    Destination(
        id="nhatrang", name="Нячанг", name_loc="в Нячанге", name_acc="в Нячанг",
        country="Вьетнам", flag="🇻🇳", iata="CXR", lat=12.24, lon=109.19, tz=7,
        kinds=("beach",), tags=("sea", "warm", "budget", "food", "kids"),
        air=(27, 28, 29, 31, 32, 32, 32, 32, 31, 29, 28, 27),
        sea=(24, 24, 25, 27, 28, 28, 28, 28, 28, 27, 26, 25),
        rain=(7, 3, 2, 3, 6, 6, 6, 7, 12, 18, 19, 14),
        high_months=_months(2, 3, 4, 5, 6, 7, 8), visa="none", visa_note="Без визы до 45 дней",
        hotel_base=4200, daily_spend=1800, domestic=False,
        areas=(
            Area("Центр, набережная Чан Фу", 35, 50, 0.5, ("lively", "city", "budget")),
            Area("Бай Зай", 15, 100, 25, ("quiet", "nature")),
            Area("Остров Хон Че", 40, 60, 5, ("family", "luxury")),
        ),
        highlights=("Парк VinWonders на острове Хон Че", "Грязевые ванны и термальные источники", "Тур по островам",
                    "Башни Понагар", "Морепродукты на ночном рынке"),
        aliases=("нячанг", "вьетнам", "вьетн"), pets="docs",
        swim_months=_months(2, 3, 4, 5, 6, 7, 8, 9), direct_from=("MOW", "LED", "SVX", "OVB", "KZN", "VVO"),
        risks={10: "октябрь–декабрь — сезон дождей и тайфунов, море часто штормит",
               11: "ноябрь — пик сезона дождей и тайфунов, море часто штормит",
               12: "декабрь — дожди и волны, купаться не каждый день"},
    ),
    Destination(
        id="bali", name="Бали", name_loc="на Бали", name_acc="на Бали",
        country="Индонезия", flag="🇮🇩", iata="DPS", lat=-8.74, lon=115.17, tz=8,
        kinds=("beach", "nature"), tags=("sea", "warm", "nature", "romantic", "surf", "wellness", "food"),
        air=(30, 30, 31, 31, 31, 30, 29, 29, 30, 31, 31, 30),
        sea=(29, 29, 29, 29, 28, 27, 27, 26, 27, 28, 28, 29),
        rain=(18, 16, 14, 9, 7, 6, 5, 4, 5, 8, 12, 16),
        high_months=_months(6, 7, 8, 12), visa="on_arrival", visa_note="Виза по прилёту (e-VoA), ~500 000 IDR + туристический сбор",
        hotel_base=7000, daily_spend=2800, domestic=False,
        areas=(
            Area("Убуд", 35, None, 1.0, ("nature", "quiet", "romantic", "wellness")),
            Area("Чангу", 20, 300, 2.0, ("lively", "party", "surf")),
            Area("Нуса-Дуа", 12, 50, 10, ("family", "luxury", "quiet")),
            Area("Семиньяк", 12, 200, 1.5, ("lively", "food")),
        ),
        highlights=("Рисовые террасы Тегаллаланг", "Храмы Улувату и Танах Лот на закате", "Рассвет на вулкане Батур",
                    "Сёрф-уроки в Чангу", "Водопады и йога в Убуде"),
        aliases=("бали", "индонез"), pets="banned", swim_months=tuple(M),
        direct_from=("MOW",),
        risks={12: "декабрь–февраль — сезон дождей: ливни обычно короткие, но влажно",
               1: "январь — сезон дождей, влажно, мутное море", 2: "февраль — сезон дождей"},
    ),
    Destination(
        id="maldives", name="Мальдивы", name_loc="на Мальдивах", name_acc="на Мальдивы",
        country="Мальдивы", flag="🇲🇻", iata="MLE", lat=4.19, lon=73.53, tz=5,
        kinds=("beach",), tags=("sea", "warm", "romantic", "luxury", "quiet", "diving"),
        air=(30, 31, 31, 32, 31, 31, 30, 30, 30, 30, 30, 30),
        sea=(28, 28, 29, 30, 30, 29, 29, 28, 28, 28, 28, 28),
        rain=(6, 3, 5, 9, 15, 13, 12, 13, 15, 15, 12, 11),
        high_months=_months(12, 1, 2, 3, 4), visa="none", visa_note="Бесплатная виза по прилёту на 30 дней",
        hotel_base=28000, daily_spend=3000, domestic=False,
        areas=(
            Area("Атолл Северный Мале", 25, 0, 25, ("luxury", "romantic", "quiet")),
            Area("Атолл Южный Мале", 40, 0, 40, ("luxury", "quiet", "diving")),
            Area("Маафуши (локальный остров)", 27, 50, 27, ("budget", "lively")),
        ),
        highlights=("Снорклинг с мантами и черепахами", "Ужин на песчаной косе", "Рыбалка на закате",
                    "Вилла над водой", "Дайвинг на внешних рифах"),
        aliases=("мальдив",), pets="banned", meal_default="HB", swim_months=tuple(M),
        direct_from=("MOW",),
        risks={5: "май–октябрь — юго-западный муссон: дожди и волны", 6: "муссон: дожди и волны"},
    ),
    Destination(
        id="sanya", name="Санья (Хайнань)", name_loc="на Хайнане", name_acc="на Хайнань",
        country="Китай", flag="🇨🇳", iata="SYX", lat=18.25, lon=109.5, tz=8,
        kinds=("beach",), tags=("sea", "warm", "kids", "luxury", "wellness"),
        air=(26, 27, 29, 31, 32, 33, 32, 32, 31, 30, 28, 26),
        sea=(24, 24, 26, 27, 29, 29, 29, 29, 28, 27, 26, 25),
        rain=(4, 4, 5, 7, 13, 15, 15, 17, 17, 12, 7, 4),
        high_months=_months(12, 1, 2, 3), visa="none", visa_note="Без визы до 30 дней",
        hotel_base=7500, daily_spend=2600, domestic=False,
        areas=(
            Area("Ялунвань", 30, 30, 20, ("luxury", "family", "quiet")),
            Area("Дадунхай", 20, 100, 3, ("lively", "budget")),
            Area("Хайтан-Бэй", 40, 50, 35, ("luxury", "shopping", "family")),
        ),
        highlights=("Парк Атлантис с аквапарком", "Остров обезьян", "Буддийский центр Наньшань",
                    "Duty-free в Хайтане", "Тропический лес Ялунвань"),
        aliases=("санья", "хайнан"), pets="hard", swim_months=_months(1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12),
        direct_from=("MOW", "LED", "SVX", "OVB", "KZN", "VVO"),
        risks={8: "август–сентябрь — сезон тайфунов", 9: "сентябрь — пик тайфунов"},
    ),
    Destination(
        id="srilanka", name="Шри-Ланка", name_loc="на Шри-Ланке", name_acc="на Шри-Ланку",
        country="Шри-Ланка", flag="🇱🇰", iata="CMB", lat=6.42, lon=80.0, tz=5.5,
        kinds=("beach", "nature"), tags=("sea", "warm", "nature", "surf", "culture", "budget", "wellness"),
        air=(30, 31, 32, 32, 31, 30, 30, 30, 30, 30, 30, 30),
        sea=(27, 28, 28, 29, 29, 28, 28, 28, 28, 28, 28, 27),
        rain=(7, 6, 9, 15, 20, 16, 12, 12, 15, 19, 16, 10),
        high_months=_months(12, 1, 2, 3), visa="evisa", visa_note="Электронная виза ETA (оформляется онлайн)",
        hotel_base=5000, daily_spend=2000, domestic=False,
        areas=(
            Area("Бентота", 75, 50, 3, ("quiet", "family", "wellness")),
            Area("Хиккадува", 110, 80, 2, ("lively", "surf", "budget")),
            Area("Унаватуна", 140, 60, 6, ("romantic", "quiet")),
        ),
        highlights=("Сафари в Яле с леопардами", "Чайные плантации Нувара-Элии", "Форт Галле",
                    "Черепашья ферма", "Аюрведа-центры"),
        aliases=("шри-ланк", "шриланк", "ланк", "цейлон", "бентот", "хиккадув"), pets="hard",
        swim_months=_months(11, 12, 1, 2, 3, 4), direct_from=("MOW",),
        risks={5: "май–октябрь — муссон на юго-западном побережье, волны", 10: "октябрь — дожди и волны",
               11: "ноябрь — начало сезона, ещё бывают ливни"},
    ),
    Destination(
        id="varadero", name="Варадеро", name_loc="в Варадеро", name_acc="в Варадеро",
        country="Куба", flag="🇨🇺", iata="VRA", lat=23.03, lon=-81.43, tz=-5,
        kinds=("beach",), tags=("sea", "warm", "romantic", "culture", "all_inclusive"),
        air=(26, 27, 28, 29, 30, 31, 32, 32, 31, 30, 28, 27),
        sea=(25, 25, 25, 26, 28, 29, 30, 30, 29, 28, 27, 26),
        rain=(5, 4, 4, 4, 8, 12, 10, 11, 13, 12, 7, 5),
        high_months=_months(12, 1, 2, 3, 4), visa="none", visa_note="Без визы до 90 дней, нужна страховка",
        hotel_base=8000, daily_spend=2500, domestic=False,
        areas=(Area("Полуостров Икакос", 30, 30, 12, ("quiet", "romantic", "family")),
               Area("Центр Варадеро", 25, 100, 1.0, ("lively", "budget"))),
        highlights=("Старая Гавана и ретро-кабриолеты", "Пляж Варадеро — 20 км белого песка", "Долина Виньялес",
                    "Пещера Сатурно", "Кубинская музыка и ром"),
        aliases=("варадеро", "куба", "кубу", "гаван"), pets="hard", meal_default="AI", swim_months=tuple(M),
        direct_from=("MOW",),
        risks={9: "сентябрь–октябрь — пик сезона ураганов", 10: "сезон ураганов"},
    ),
    Destination(
        id="sochi", name="Сочи", name_loc="в Сочи", name_acc="в Сочи",
        country="Россия", flag="🇷🇺", iata="AER", lat=43.58, lon=39.72, tz=3,
        kinds=("beach", "city", "nature"), tags=("sea", "kids", "nature", "food", "party", "wellness"),
        air=(10, 11, 13, 17, 21, 25, 28, 29, 25, 20, 15, 12),
        sea=(9, 8, 9, 11, 16, 22, 25, 26, 23, 19, 15, 11),
        rain=(15, 13, 12, 11, 10, 8, 6, 6, 8, 10, 12, 15),
        high_months=_months(6, 7, 8, 9, 12), visa="none", visa_note="Виза не нужна",
        hotel_base=7500, daily_spend=3000, domestic=True,
        areas=(
            Area("Центр Сочи", 30, 200, 0.5, ("city", "lively")),
            Area("Адлер", 5, 150, 25, ("family", "budget")),
            Area("Хоста", 18, 100, 15, ("quiet", "family")),
            Area("Сириус (Имеретинка)", 8, 100, 30, ("family", "kids", "quiet")),
        ),
        highlights=("Олимпийский парк и Сочи Парк", "Дендрарий и Ривьера", "Скайпарк и мост над пропастью",
                    "Тисо-самшитовая роща", "Красная Поляна на день"),
        aliases=("сочи", "адлер", "хост", "сириус", "имеретин"), pets="easy",
        swim_months=_months(6, 7, 8, 9, 10),
        direct_from=("MOW", "LED", "SVX", "KZN", "OVB", "KUF", "UFA", "GOJ", "PEE", "VVO"),
    ),
    Destination(
        id="rosa", name="Красная Поляна", name_loc="на Красной Поляне", name_acc="на Красную Поляну",
        country="Россия", flag="🇷🇺", iata="AER", lat=43.68, lon=40.2, tz=3,
        kinds=("ski", "nature"), tags=("ski", "mountains", "nature", "kids", "wellness", "romantic"),
        air=(2, 3, 7, 13, 18, 22, 25, 25, 21, 15, 9, 4), sea=None,
        rain=(14, 13, 13, 12, 13, 11, 9, 8, 9, 10, 12, 14),
        high_months=_months(12, 1, 2, 3, 7, 8), visa="none", visa_note="Виза не нужна",
        hotel_base=9000, daily_spend=4000, domestic=True,
        areas=(Area("Роза Хутор", 50, None, 1.0, ("family", "lively", "ski")),
               Area("Эсто-Садок", 45, None, 3.0, ("quiet", "budget")),
               Area("Газпром Поляна", 55, None, 4.0, ("luxury", "ski"))),
        highlights=("Катание на Роза Хутор (декабрь–апрель)", "Канатка на Роза Пик 2320 м", "Термы и СПА",
                    "Водопады Менделиха", "Хайкинг к озёрам (летом)"),
        aliases=("красн поля", "красная поляна", "красную поляну", "красной поляне", "роза хутор", "роз хутор", "розу хутор", "роза пик"),
        pets="easy", ski_months=_months(12, 1, 2, 3, 4),
        direct_from=("MOW", "LED", "SVX", "KZN", "OVB", "KUF", "UFA", "GOJ", "PEE", "VVO"),
    ),
    Destination(
        id="kaliningrad", name="Калининград", name_loc="в Калининграде", name_acc="в Калининград",
        country="Россия", flag="🇷🇺", iata="KGD", lat=54.71, lon=20.51, tz=2,
        kinds=("city", "beach"), tags=("city", "culture", "food", "nature", "quiet"),
        air=(1, 2, 6, 12, 18, 21, 23, 23, 18, 12, 6, 2),
        sea=(3, 2, 3, 6, 11, 16, 19, 19, 16, 12, 8, 5),
        rain=(18, 15, 13, 11, 10, 11, 12, 13, 13, 15, 17, 18),
        high_months=_months(6, 7, 8), visa="none", visa_note="Виза не нужна (перелёт над нейтральными водами)",
        hotel_base=6000, daily_spend=2500, domestic=True,
        areas=(Area("Центр", 22, None, 0.5, ("city", "culture")),
               Area("Светлогорск", 30, 200, 38, ("quiet", "romantic")),
               Area("Зеленоградск", 25, 150, 30, ("quiet", "family"))),
        highlights=("Кафедральный собор и остров Канта", "Куршская коса и Танцующий лес", "Рыбная деревня",
                    "Светлогорский променад", "Музей Мирового океана"),
        aliases=("калининград", "кёнигсберг", "кенигсберг", "светлогорск", "зеленоградск"), pets="easy",
        swim_months=_months(7, 8), direct_from=("MOW", "LED", "SVX", "KZN"),
    ),
    Destination(
        id="spb", name="Санкт-Петербург", name_loc="в Петербурге", name_acc="в Петербург",
        country="Россия", flag="🇷🇺", iata="LED", lat=59.94, lon=30.31, tz=3,
        kinds=("city",), tags=("city", "culture", "food", "romantic", "party"),
        air=(-3, -3, 2, 10, 17, 21, 24, 22, 16, 9, 3, -1), sea=None,
        rain=(18, 15, 14, 12, 13, 14, 15, 16, 17, 17, 18, 19),
        high_months=_months(5, 6, 7, 12), visa="none", visa_note="Виза не нужна",
        hotel_base=7000, daily_spend=3000, domestic=True,
        areas=(Area("Невский проспект", 17, None, 0.3, ("city", "lively")),
               Area("Петроградка", 22, None, 3.0, ("quiet", "culture")),
               Area("Васильевский остров", 22, None, 3.0, ("culture", "quiet")),
               Area("Рубинштейна", 16, None, 1.0, ("party", "food", "lively"))),
        highlights=("Эрмитаж и Дворцовая площадь", "Развод мостов (апрель–ноябрь)", "Петергоф и фонтаны (май–октябрь)",
                    "Гастро-бары на Рубинштейна", "Крыши и Новая Голландия"),
        aliases=("петербург", "питер", "спб", "ленинград", "санкт"), pets="easy",
        direct_from=("MOW", "SVX", "KZN", "OVB", "KUF", "UFA", "GOJ", "PEE", "VVO"),
    ),
    Destination(
        id="moscow", name="Москва", name_loc="в Москве", name_acc="в Москву",
        country="Россия", flag="🇷🇺", iata="MOW", lat=55.75, lon=37.62, tz=3,
        kinds=("city",), tags=("city", "culture", "food", "shopping", "party", "kids"),
        air=(-4, -3, 3, 12, 19, 22, 24, 22, 16, 9, 2, -2), sea=None,
        rain=(18, 15, 13, 12, 13, 14, 15, 14, 15, 16, 17, 18),
        high_months=_months(5, 6, 7, 12), visa="none", visa_note="Виза не нужна",
        hotel_base=8000, daily_spend=3500, domestic=True,
        areas=(Area("Китай-город", 35, None, 0.5, ("city", "culture", "lively")),
               Area("Патриаршие", 33, None, 2.0, ("food", "lively")),
               Area("Замоскворечье", 36, None, 1.5, ("quiet", "culture")),
               Area("Москва-Сити", 30, None, 6.0, ("shopping", "city"))),
        highlights=("Красная площадь и Зарядье", "Третьяковка и Пушкинский", "ВДНХ и Москвариум",
                    "Гастромаркеты и рестораны на Патриках", "Прогулка на теплоходе по Москве-реке"),
        aliases=("москв", "мск"), pets="easy",
        direct_from=("LED", "SVX", "KZN", "OVB", "KUF", "UFA", "GOJ", "PEE", "VVO"),
    ),
    Destination(
        id="kazan", name="Казань", name_loc="в Казани", name_acc="в Казань",
        country="Россия", flag="🇷🇺", iata="KZN", lat=55.79, lon=49.12, tz=3,
        kinds=("city",), tags=("city", "culture", "food", "kids", "budget"),
        air=(-9, -8, -1, 10, 19, 24, 26, 23, 16, 8, -1, -6), sea=None,
        rain=(16, 13, 10, 10, 11, 12, 12, 12, 13, 15, 16, 17),
        high_months=_months(5, 6, 7, 8, 12, 1), visa="none", visa_note="Виза не нужна",
        hotel_base=5500, daily_spend=2200, domestic=True,
        areas=(Area("Кремль и Баумана", 25, None, 0.3, ("city", "culture", "lively")),
               Area("Старо-Татарская слобода", 25, None, 1.5, ("culture", "food", "quiet")),
               Area("Ривьера", 23, None, 3.0, ("family", "kids"))),
        highlights=("Казанский кремль и мечеть Кул-Шариф", "Остров-град Свияжск", "Чак-чак и эчпочмаки в Старо-Татарской слободе",
                    "Аквапарк Ривьера", "Храм всех религий"),
        aliases=("казан",), pets="easy",
        direct_from=("MOW", "LED", "SVX", "OVB", "UFA", "PEE", "VVO"),
    ),
    Destination(
        id="karelia", name="Карелия", name_loc="в Карелии", name_acc="в Карелию",
        country="Россия", flag="🇷🇺", iata="PES", lat=61.79, lon=34.36, tz=3,
        kinds=("nature",), tags=("nature", "quiet", "budget", "romantic"),
        air=(-7, -7, -2, 5, 12, 18, 21, 18, 12, 5, -1, -5), sea=None,
        rain=(18, 16, 14, 12, 13, 14, 15, 17, 18, 18, 19, 19),
        high_months=_months(6, 7, 8, 2, 3), visa="none", visa_note="Виза не нужна",
        hotel_base=5500, daily_spend=2000, domestic=True,
        areas=(Area("Петрозаводск", 15, None, 0.5, ("city",)),
               Area("Рускеала", 270, None, 270, ("nature", "quiet")),
               Area("Онежское озеро, загородный парк-отель", 40, None, 40, ("nature", "quiet", "family"))),
        highlights=("Горный парк Рускеала", "Кижи на «Метеоре» (июнь–сентябрь)", "Водопад Кивач",
                    "Сапы и байдарки на озёрах", "Хаски-парк зимой"),
        aliases=("карели", "петрозаводск", "рускеал", "кижи", "онеж"), pets="easy",
        direct_from=("MOW",),
    ),
    Destination(
        id="altai", name="Алтай", name_loc="на Алтае", name_acc="на Алтай",
        country="Россия", flag="🇷🇺", iata="RGK", lat=51.97, lon=85.96, tz=7,
        kinds=("nature",), tags=("nature", "mountains", "quiet", "budget", "romantic"),
        air=(-10, -8, -1, 10, 18, 23, 24, 22, 16, 8, -2, -8), sea=None,
        rain=(7, 6, 7, 9, 12, 14, 15, 14, 11, 10, 9, 8),
        high_months=_months(7, 8), visa="none", visa_note="Виза не нужна",
        hotel_base=5000, daily_spend=2000, domestic=True,
        areas=(Area("Чемал", 95, None, 95, ("nature", "quiet", "family")),
               Area("Манжерок", 35, None, 35, ("nature", "lively", "ski")),
               Area("Телецкое озеро", 160, None, 160, ("nature", "quiet", "romantic"))),
        highlights=("Чуйский тракт и Гейзерное озеро", "Телецкое озеро", "Катунь и Голубые озёра",
                    "Рафтинг", "Манжерок — канатка и курорт"),
        aliases=("алта", "горно-алтайск", "горноалтайск", "чемал", "телецк", "манжерок"), pets="easy",
        direct_from=("MOW", "OVB"), hub="OVB",
    ),
    Destination(
        id="baikal", name="Байкал", name_loc="на Байкале", name_acc="на Байкал",
        country="Россия", flag="🇷🇺", iata="IKT", lat=52.29, lon=104.28, tz=8,
        kinds=("nature",), tags=("nature", "quiet", "romantic"),
        air=(-15, -12, -3, 7, 15, 21, 23, 21, 14, 6, -5, -13), sea=None,
        rain=(8, 6, 6, 7, 9, 12, 13, 13, 10, 8, 9, 10),
        high_months=_months(2, 3, 7, 8), visa="none", visa_note="Виза не нужна",
        hotel_base=5500, daily_spend=2500, domestic=True,
        areas=(Area("Листвянка", 70, None, 70, ("nature", "quiet")),
               Area("Иркутск, центр", 8, None, 0.5, ("city", "culture")),
               Area("Остров Ольхон", 290, None, 290, ("nature", "quiet", "romantic"))),
        highlights=("Прозрачный лёд и пузыри метана (февраль–март)", "Остров Ольхон и мыс Хобой", "Кругобайкальская железная дорога",
                    "Байкальский музей и нерпинарий", "Омуль копчёный"),
        aliases=("байкал", "иркутск", "ольхон", "листвянк"), pets="easy",
        direct_from=("MOW", "LED", "OVB"), hub="OVB",
    ),
    Destination(
        id="kamchatka", name="Камчатка", name_loc="на Камчатке", name_acc="на Камчатку",
        country="Россия", flag="🇷🇺", iata="PKC", lat=53.02, lon=158.65, tz=12,
        kinds=("nature",), tags=("nature", "mountains", "adventure", "luxury"),
        air=(-5, -5, -2, 3, 8, 13, 16, 17, 13, 6, 0, -4), sea=None,
        rain=(10, 9, 9, 8, 8, 8, 9, 10, 11, 12, 11, 10),
        high_months=_months(7, 8, 9), visa="none", visa_note="Виза не нужна",
        hotel_base=10000, daily_spend=6000, domestic=True,
        areas=(Area("Петропавловск-Камчатский", 30, None, 0.5, ("city",)),
               Area("Паратунка (термальные источники)", 25, None, 60, ("nature", "wellness", "quiet"))),
        highlights=("Вулканы Авачинский и Мутновский", "Вертолёт в Долину гейзеров", "Медведи на Курильском озере",
                    "Морская прогулка в Авачинской бухте", "Термальные бассейны Паратунки"),
        aliases=("камчат", "петропавловск"), pets="easy", direct_from=("MOW", "LED", "OVB", "VVO"),
    ),
    Destination(
        id="dagestan", name="Дагестан", name_loc="в Дагестане", name_acc="в Дагестан",
        country="Россия", flag="🇷🇺", iata="MCX", lat=42.98, lon=47.5, tz=3,
        kinds=("nature",), tags=("nature", "mountains", "food", "budget", "culture"),
        air=(3, 4, 8, 14, 21, 27, 30, 29, 24, 17, 10, 5),
        sea=(7, 6, 7, 10, 16, 22, 25, 25, 22, 17, 12, 9),
        rain=(7, 7, 8, 7, 7, 5, 4, 5, 6, 8, 8, 8),
        high_months=_months(5, 6, 7, 8, 9), visa="none", visa_note="Виза не нужна",
        hotel_base=4500, daily_spend=1800, domestic=True,
        areas=(Area("Махачкала, центр", 15, 600, 0.5, ("city", "lively")),
               Area("Дербент", 120, 1500, 120, ("culture", "quiet")),
               Area("Горный Дагестан (Гуниб)", 170, None, 170, ("nature", "quiet"))),
        highlights=("Сулакский каньон и катер по водохранилищу", "Бархан Сарыкум", "Древний Дербент и крепость Нарын-Кала",
                    "Аул-призрак Гамсутль", "Чуду и хинкал"),
        aliases=("дагестан", "махачкал", "дербент", "сулак"), pets="easy", swim_months=_months(6, 7, 8, 9),
        direct_from=("MOW", "LED", "SVX", "KZN"),
    ),
    Destination(
        id="kislovodsk", name="Кисловодск", name_loc="в Кисловодске", name_acc="в Кисловодск",
        country="Россия", flag="🇷🇺", iata="MRV", lat=43.91, lon=42.72, tz=3,
        kinds=("nature",), tags=("wellness", "nature", "quiet", "mountains"),
        air=(3, 4, 8, 15, 19, 23, 26, 26, 21, 15, 9, 4), sea=None,
        rain=(5, 6, 8, 10, 14, 14, 11, 9, 7, 6, 5, 5),
        high_months=_months(5, 6, 7, 8, 9), visa="none", visa_note="Виза не нужна",
        hotel_base=5500, daily_spend=2000, domestic=True,
        areas=(Area("Курортный парк", 60, None, 1.0, ("wellness", "quiet")),
               Area("Центр, Курортный бульвар", 60, None, 0.3, ("city", "wellness")),
               Area("Пятигорск", 25, None, 40, ("city", "budget"))),
        highlights=("Курортный парк и терренкуры", "Нарзанная галерея", "Эльбрус и Приэльбрусье на день",
                    "Долина нарзанов", "Санаторные процедуры"),
        aliases=("кисловодск", "минвод", "минеральн", "пятигорск", "ессентук", "кмв"), pets="easy",
        direct_from=("MOW", "LED", "SVX", "KZN", "OVB", "KUF", "UFA", "GOJ", "PEE"),
    ),
    Destination(
        id="anapa", name="Анапа", name_loc="в Анапе", name_acc="в Анапу",
        country="Россия", flag="🇷🇺", iata="AAQ", lat=44.89, lon=37.32, tz=3,
        kinds=("beach",), tags=("sea", "kids", "budget", "wellness"),
        air=(6, 7, 10, 15, 21, 26, 29, 29, 24, 18, 12, 8),
        sea=(7, 6, 7, 10, 16, 22, 25, 25, 22, 17, 13, 9),
        rain=(10, 9, 9, 8, 7, 6, 4, 4, 5, 6, 8, 10),
        high_months=_months(6, 7, 8), visa="none", visa_note="Виза не нужна",
        hotel_base=5000, daily_spend=2000, domestic=True,
        areas=(Area("Пионерский проспект", 12, 50, 8, ("family", "kids", "quiet")),
               Area("Центр Анапы", 6, 300, 0.5, ("lively", "budget")),
               Area("Джемете", 8, 150, 4, ("family", "lively"))),
        highlights=("Песчаные пляжи с пологим входом — идеально для детей", "Утриш и можжевеловые рощи",
                    "Винодельни Абрау-Дюрсо и Фанагории", "Аквапарки Джемете", "Грязелечебницы"),
        aliases=("анап", "джемете", "витязево"), pets="easy", swim_months=_months(6, 7, 8, 9),
        flights=False, direct_from=(),
        risks={0: "аэропорт Анапы закрыт — добираться поездом до Анапы или самолётом до Сочи + 7 ч на машине"},
    ),
    Destination(
        id="abkhazia", name="Абхазия (Сухум)", name_loc="в Абхазии", name_acc="в Абхазию",
        country="Абхазия", flag="🏳️", iata="SUI", lat=43.0, lon=41.02, tz=3,
        kinds=("beach", "nature"), tags=("sea", "nature", "budget", "quiet", "food"),
        air=(11, 12, 14, 18, 22, 26, 29, 29, 26, 22, 17, 13),
        sea=(10, 9, 10, 12, 17, 23, 26, 26, 24, 20, 16, 12),
        rain=(13, 12, 12, 11, 10, 9, 8, 7, 9, 10, 12, 13),
        high_months=_months(7, 8), visa="none", visa_note="Виза не нужна, нужен загранпаспорт или внутренний (по правилам границы)",
        hotel_base=4000, daily_spend=1800, domestic=False,
        areas=(Area("Сухум, набережная", 20, 150, 0.5, ("city", "lively")),
               Area("Гагра", 80, 100, 80, ("family", "lively")),
               Area("Новый Афон", 30, 200, 25, ("quiet", "nature"))),
        highlights=("Новоафонская пещера и монастырь", "Озеро Рица и Голубое озеро", "Гагрские колоннады",
                    "Мандариновые сады и чача", "Ботанический сад Сухума"),
        aliases=("абхаз", "сухум", "гагр", "пицунд", "новый афон"), pets="easy", swim_months=_months(6, 7, 8, 9, 10),
        direct_from=("MOW",),
    ),
    Destination(
        id="tbilisi", name="Тбилиси", name_loc="в Тбилиси", name_acc="в Тбилиси",
        country="Грузия", flag="🇬🇪", iata="TBS", lat=41.69, lon=44.8, tz=4,
        kinds=("city",), tags=("city", "food", "culture", "budget", "romantic", "party"),
        air=(7, 9, 14, 19, 24, 29, 32, 32, 27, 21, 14, 9), sea=None,
        rain=(5, 6, 8, 10, 12, 9, 6, 6, 6, 7, 6, 5),
        high_months=_months(5, 6, 9, 10), visa="none", visa_note="Без визы до 1 года",
        hotel_base=4800, daily_spend=2200, domestic=False,
        areas=(Area("Старый город", 17, None, 0.5, ("culture", "lively", "food")),
               Area("Сололаки", 18, None, 1.0, ("quiet", "romantic")),
               Area("Ваке", 22, None, 3.0, ("quiet", "food"))),
        highlights=("Серные бани Абанотубани", "Канатка к крепости Нарикала", "Винные дегустации в Кахетии",
                    "Мцхета и Джвари", "Хинкали и хачапури в Сололаки"),
        aliases=("тбилис", "кахети"), pets="docs", direct_from=("MOW", "LED"),
    ),
    Destination(
        id="batumi", name="Батуми", name_loc="в Батуми", name_acc="в Батуми",
        country="Грузия", flag="🇬🇪", iata="BUS", lat=41.64, lon=41.63, tz=4,
        kinds=("beach", "city"), tags=("sea", "city", "budget", "food", "party"),
        air=(10, 11, 13, 16, 20, 24, 27, 27, 24, 20, 16, 12),
        sea=(11, 10, 10, 12, 17, 22, 25, 26, 24, 20, 16, 13),
        rain=(14, 13, 13, 12, 10, 10, 11, 12, 14, 14, 13, 14),
        high_months=_months(7, 8), visa="none", visa_note="Без визы до 1 года",
        hotel_base=4500, daily_spend=2000, domestic=False,
        areas=(Area("Бульвар", 3, 100, 0.5, ("lively", "city")),
               Area("Новый Бульвар", 2, 150, 3, ("lively", "party")),
               Area("Гонио", 8, 200, 10, ("quiet", "family"))),
        highlights=("Приморский бульвар и Али и Нино", "Ботанический сад", "Аджарские хачапури",
                    "Крепость Гонио", "Канатная дорога Арго"),
        aliases=("батум", "аджар"), pets="docs", swim_months=_months(6, 7, 8, 9),
        direct_from=("MOW",),
        risks={9: "в сентябре в Батуми много дождей (до 14 дней)", 10: "октябрь — сезон проливных дождей"},
    ),
    Destination(
        id="yerevan", name="Ереван", name_loc="в Ереване", name_acc="в Ереван",
        country="Армения", flag="🇦🇲", iata="EVN", lat=40.18, lon=44.51, tz=4,
        kinds=("city",), tags=("city", "food", "culture", "budget", "romantic"),
        air=(2, 5, 12, 19, 25, 30, 34, 34, 29, 22, 13, 5), sea=None,
        rain=(5, 6, 7, 10, 10, 6, 3, 2, 2, 5, 5, 5),
        high_months=_months(5, 6, 9, 10), visa="none", visa_note="Без визы, можно по внутреннему паспорту",
        hotel_base=4800, daily_spend=2200, domestic=False,
        areas=(Area("Кентрон", 12, None, 0.3, ("city", "lively", "food")),
               Area("Каскад", 13, None, 1.0, ("culture", "quiet")),
               Area("Арабкир", 15, None, 3.0, ("quiet", "budget"))),
        highlights=("Каскад и вид на Арарат", "Гарни и Гегард", "Озеро Севан", "Коньячный завод «Арарат»", "Вернисаж и рынок ГУМ"),
        aliases=("ереван", "армени"), pets="docs",
        direct_from=("MOW", "LED", "SVX", "KZN", "KUF", "UFA", "OVB"),
    ),
    Destination(
        id="samarkand", name="Самарканд", name_loc="в Самарканде", name_acc="в Самарканд",
        country="Узбекистан", flag="🇺🇿", iata="SKD", lat=39.65, lon=66.96, tz=5,
        kinds=("city",), tags=("city", "culture", "food", "budget"),
        air=(7, 9, 15, 22, 27, 33, 35, 34, 29, 22, 15, 9), sea=None,
        rain=(8, 9, 10, 9, 6, 2, 1, 0, 1, 4, 6, 8),
        high_months=_months(4, 5, 9, 10), visa="none", visa_note="Без визы до 30 дней",
        hotel_base=4200, daily_spend=1800, domestic=False,
        areas=(Area("Регистан", 7, None, 0.5, ("culture", "lively")),
               Area("Старый город", 6, None, 1.0, ("culture", "quiet")),
               Area("Самарканд Вечный город", 12, None, 6.0, ("family", "quiet"))),
        highlights=("Регистан и медресе", "Шахи-Зинда", "Обсерватория Улугбека", "Плов в центре плова",
                    "Поезд «Афросиаб» в Бухару"),
        aliases=("самарканд", "узбекист", "бухар", "ташкент"), pets="docs",
        direct_from=("MOW", "LED", "SVX", "KZN", "OVB"),
        risks={7: "в июле днём +35°, экскурсии лучше утром", 6: "в июне днём +33°"},
    ),
    Destination(
        id="minsk", name="Минск", name_loc="в Минске", name_acc="в Минск",
        country="Беларусь", flag="🇧🇾", iata="MSQ", lat=53.9, lon=27.56, tz=3,
        kinds=("city",), tags=("city", "culture", "food", "budget", "quiet", "kids"),
        air=(-2, -1, 4, 13, 19, 22, 24, 23, 17, 10, 3, -1), sea=None,
        rain=(17, 15, 14, 12, 12, 13, 14, 12, 13, 14, 16, 18),
        high_months=_months(5, 6, 7, 8, 12), visa="none", visa_note="Виза не нужна, можно по внутреннему паспорту",
        hotel_base=5000, daily_spend=2000, domestic=False,
        areas=(Area("Верхний город", 42, None, 0.5, ("city", "culture")),
               Area("Немига", 42, None, 1.0, ("lively", "food")),
               Area("Парк Победы", 40, None, 3.0, ("quiet", "family"))),
        highlights=("Верхний город и Троицкое предместье", "Мирский и Несвижский замки", "Беловежская пуща",
                    "Драники и колбасы на Комаровке", "Национальная библиотека"),
        aliases=("минск", "беларус", "белорус"), pets="easy", direct_from=("MOW", "LED", "SVX", "KZN"),
    ),
    Destination(
        id="murmansk", name="Мурманск и Териберка", name_loc="в Мурманске", name_acc="в Мурманск",
        country="Россия", flag="🇷🇺", iata="MMK", lat=68.97, lon=33.07, tz=3,
        kinds=("nature",), tags=("nature", "northern_lights", "adventure", "romantic"),
        air=(-7, -7, -3, 2, 8, 14, 18, 15, 10, 3, -2, -5), sea=None,
        rain=(19, 17, 16, 13, 13, 13, 14, 15, 17, 18, 19, 19),
        high_months=_months(1, 2, 3, 12), visa="none", visa_note="Виза не нужна",
        hotel_base=6500, daily_spend=3500, domestic=True,
        areas=(Area("Центр Мурманска", 25, None, 0.5, ("city",)),
               Area("Териберка", 130, None, 130, ("nature", "quiet", "romantic"))),
        highlights=("Охота за северным сиянием (октябрь–март)", "Териберка и Баренцево море", "Ледокол «Ленин»",
                    "Хаски и олени в саамской деревне", "Снегоходы по тундре"),
        aliases=("мурманск", "териберк", "северн сия"), pets="easy",
        lights_months=_months(10, 11, 12, 1, 2, 3), direct_from=("MOW", "LED"),
        risks={12: "в декабре полярная ночь — светло всего пару часов", 1: "в январе световой день короткий, -15° с ветром"},
    ),
    Destination(
        id="sheregesh", name="Шерегеш", name_loc="в Шерегеше", name_acc="в Шерегеш",
        country="Россия", flag="🇷🇺", iata="NOZ", lat=52.92, lon=87.98, tz=7,
        kinds=("ski",), tags=("ski", "mountains", "party", "budget"),
        air=(-14, -12, -5, 5, 15, 21, 23, 20, 14, 5, -5, -12), sea=None,
        rain=(15, 12, 11, 10, 12, 13, 14, 13, 12, 14, 16, 16),
        high_months=_months(12, 1, 2, 3), visa="none", visa_note="Виза не нужна",
        hotel_base=6000, daily_spend=3500, domestic=True,
        areas=(Area("Сектор Е (у подъёмников)", 160, None, 0.3, ("ski", "party", "lively")),
               Area("Посёлок Шерегеш", 155, None, 3.0, ("budget", "quiet"))),
        highlights=("Пухляк — лучший фрирайд Сибири", "Гора Зелёная и вечерние вечеринки", "Баня после катания",
                    "Канатка на гору Мустаг", "Тюбинг для детей"),
        aliases=("шерегеш", "шерег"), pets="easy", ski_months=_months(12, 1, 2, 3, 4),
        direct_from=("MOW", "LED", "OVB"), hub="OVB",
    ),
    Destination(
        id="ushuaia", name="Ушуая (ворота в Антарктиду)", name_loc="в Ушуае", name_acc="в Ушуаю",
        country="Аргентина", flag="🇦🇷", iata="USH", lat=-54.84, lon=-68.3, tz=-3,
        kinds=("nature",), tags=("nature", "adventure", "mountains", "luxury"),
        air=(14, 14, 12, 9, 6, 4, 4, 5, 7, 10, 12, 13), sea=None,
        rain=(12, 11, 13, 13, 13, 13, 13, 12, 11, 12, 12, 13),
        high_months=_months(11, 12, 1, 2, 3), visa="none", visa_note="Аргентина: без визы до 90 дней",
        hotel_base=13000, daily_spend=6000, domestic=False,
        areas=(Area("Центр Ушуаи", 5, None, 0.5, ("city", "lively")),
               Area("Порт (у причала круизных судов)", 4, None, 1.0, ("city", "quiet")),
               Area("Склоны у ледника Мартиал", 9, None, 4.0, ("nature", "quiet", "luxury"))),
        highlights=("Экспедиционный круиз в Антарктиду: 10–12 дней, пингвины, айсберги, высадки на континент",
                    "Национальный парк Огненная Земля", "Канал Бигл, морские львы и пингвины",
                    "Поезд «На край света»", "Ледник Мартиал"),
        aliases=("ушуа", "огненн земл", "огненную землю"), pets="docs",
        direct_from=(), hub="MOW", hubs=("IST", "DXB"),
        risks={0: "сам круиз в Антарктиду бронируется у экспедиционного оператора отдельно: от ~1 млн ₽ на человека",
               **{m: "в это время круизы в Антарктиду не ходят — сезон с ноября по март" for m in range(4, 11)}},
    ),
]

class _DestRegistry(dict):
    """Каталог + направления, которые агент узнал от LLM (подгружаются по id из кэша)."""

    loader = None  # устанавливается модулем app.dynamic

    def __missing__(self, key: str) -> Destination:
        if self.loader is not None:
            d = self.loader(key)
            if d is not None:
                self[key] = d
                return d
        raise KeyError(key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key) -> bool:
        return self.get(key) is not None


DEST_BY_ID: dict[str, Destination] = _DestRegistry({d.id: d for d in DESTINATIONS})

COUNTRY_ALIASES: dict[str, str] = {
    "турци": "Турция", "египет": "Египет", "египт": "Египет", "оаэ": "ОАЭ", "эмират": "ОАЭ",
    "таиланд": "Таиланд", "тайланд": "Таиланд",
    "вьетнам": "Вьетнам", "индонез": "Индонезия", "китай": "Китай", "китае": "Китай",
    "куба": "Куба", "кубу": "Куба", "грузи": "Грузия", "армени": "Армения", "узбекист": "Узбекистан",
    "беларус": "Беларусь", "белорус": "Беларусь", "абхаз": "Абхазия",
    "по россии": "Россия", "в россии": "Россия", "россии": "Россия", "россию": "Россия", "российск": "Россия",
}

MONTH_NAMES = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]
MONTH_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
MONTH_LOC = ["январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе", "сентябре", "октябре", "ноябре", "декабре"]


# Места, куда попадают только через «ворота»: агент собирает поездку до ворот и объясняет остальное.
GATEWAYS: list[tuple[str, str, str]] = [
    (r"антарктид|антарктик|южн\w* полюс", "ushuaia",
     "В Антарктиду туристы попадают только экспедиционными круизами из Ушуаи (Аргентина) — сезон с ноября по март, "
     "круиз длится 10–12 дней. Собираю поездку до Ушуаи: перелёт, отель перед отплытием и трансфер; "
     "сам круиз бронируется у экспедиционного оператора (от ~1 млн ₽ на человека)."),
    (r"северн\w* полюс", "murmansk",
     "На Северный полюс ходят круизы на атомном ледоколе из Мурманска (июнь–август, около 2 недель). "
     "Собираю поездку до Мурманска; сам круиз бронируется отдельно у оператора."),
]

# Места, которых нет в каталоге: агент честно говорит об этом и предлагает
# похожие по духу направления. (регулярка, как назвать, интересы, ближайшие аналоги из каталога)
UNKNOWN_PLACES: list[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = [
    (r"арктик|гренланди|шпицберген", "Арктика", ("nature", "adventure"),
     ("kamchatka", "murmansk", "baikal", "altai")),
    (r"исланди|норвеги|финлянди|лапланди|швеци", "Скандинавия", ("nature", "northern_lights"),
     ("murmansk", "karelia", "kamchatka", "kaliningrad")),
    (r"швейцари|альп(ы|ах|и)\b|куршевел|андорр", "Альпы", ("mountains", "nature"), ("rosa", "sheregesh", "altai", "kislovodsk")),
    (r"непал|тибет|гимала|монголи|эверест", "Гималаи", ("mountains", "nature", "adventure"), ("altai", "dagestan", "kamchatka", "baikal")),
    (r"франци|париж|германи|берлин|мюнхен|чехи|праг[аеуи]\b|австри|вен[ауе]\b|нидерланд|голланди|амстердам|бельги|"
     r"великобритани|англи[яюи]\b|лондон|шотланди|ирланди|польш|варшав|венгри|будапешт|европ", "Европа", ("city", "culture", "food"),
     ("kaliningrad", "istanbul", "spb", "minsk", "tbilisi")),
    (r"итали|рим[аеу]?\b|венеци|флоренци|милан|испани|барселон|мадрид|португали|лиссабон|греци|крит|кипр|черногори|хорвати|"
     r"мальт|сицили|сардини", "Средиземноморье", ("sea", "culture", "food"), ("antalya", "batumi", "abkhazia", "istanbul", "sochi")),
    (r"\bсша\b|америк|нью-йорк|нью йорк|лас-вегас|майами|калифорни|канад", "Северная Америка", ("city", "shopping", "party"),
     ("dubai", "moscow", "istanbul", "spb")),
    (r"мексик|канкун|доминикан|пунта-кан|карибы|карибск|ямайк|багам|бразили|рио-де|аргентин|перу\b", "Латинская Америка",
     ("sea", "warm", "party"), ("varadero", "phuket", "bali", "nhatrang")),
    (r"япони|токио|киото|коре[яюи]\b|сеул|сингапур|гонконг|тайван|шанха|пекин", "Восточная Азия", ("city", "culture", "food"),
     ("sanya", "istanbul", "phuket", "samarkand")),
    (r"сейшел|маврики|занзибар|танзани|филиппин|малайзи|лангкав|\bгоа\b|инди[яюи]\b|фиджи|таити|бора-бора|полинези",
     "тропические острова", ("sea", "warm", "romantic"), ("maldives", "srilanka", "bali", "phuket")),
    (r"кени[яюи]\b|сафари|африк|намиби|ботсван|юар\b", "Африка (сафари)", ("nature", "adventure"),
     ("srilanka", "hurghada", "dubai", "kamchatka")),
    (r"австрали|новая зеланди|новую зеланди|новой зеланди|сидне", "Австралия и Океания", ("nature", "adventure", "sea"),
     ("bali", "srilanka", "kamchatka", "phuket")),
    (r"израил|иордани|петр[аеу]\b|марокко|тунис|катар|доха|оман|бахрейн|абу-даби|абу даби", "Ближний Восток и Северная Африка",
     ("sea", "warm", "culture"), ("dubai", "hurghada", "sharm", "istanbul", "yerevan")),
]
