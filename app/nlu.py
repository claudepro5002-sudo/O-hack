"""Разбор запроса на естественном языке (русский), без внешних зависимостей.

Возвращает обновления слотов TripRequest и намерение пользователя.
LLM (если подключена) уточняет результат поверх этих правил — см. llm.py.
"""
from __future__ import annotations

import re
from calendar import monthrange
from datetime import date, timedelta

from .data.destinations import COUNTRY_ALIASES, DESTINATIONS, GATEWAYS, UNKNOWN_PLACES
from .data.geo import ORIGINS
from .state import today

NUM_WORDS = {
    "один": 1, "одна": 1, "одну": 1, "одного": 1, "день": 1, "два": 2, "две": 2, "двух": 2, "пару": 2, "пара": 2,
    "три": 3, "трех": 3, "четыре": 4, "четырех": 4, "пять": 5, "пяти": 5, "шесть": 6, "шести": 6, "семь": 7, "семи": 7,
    "восемь": 8, "восьми": 8, "девять": 9, "девяти": 9, "десять": 10, "десяти": 10, "одиннадцать": 11, "двенадцать": 12,
    "четырнадцать": 14, "пятнадцать": 15, "двадцать": 20, "тридцать": 30,
}
NUM_RE = r"(\d+|" + "|".join(sorted(NUM_WORDS, key=len, reverse=True)) + r")"

MONTH_STEMS = [r"январ", r"феврал", r"март", r"апрел", r"ма[йяе]", r"июн", r"июл", r"август", r"сентябр", r"октябр",
               r"ноябр", r"декабр"]
MONTH_RE = r"(" + "|".join(MONTH_STEMS) + r")[а-я]*"

PREF_PATTERNS = [
    ("sea", r"мор[еяю]|морск|пляж|океан|купат|остров|на берег|загора"),
    ("warm", r"тепл|жар[кк]|солн|лето посреди|согреться|тропик"),
    ("culture", r"экскурс|истори|музе|архитект|достопримеч|культур|погулять по город|старый город|храм"),
    ("food", r"\bеда\b|\bеды\b|поесть|покушать|гастро|вкусн|кухн|ресторан|вино|винн|дегустац"),
    ("party", r"тус|вечерин|клуб(?!ом)|ночн\w* жизн|бары|потанцев|движ"),
    ("quiet", r"тих|спокойн|уединен|без толп|релакс|отдохнуть от|выспаться|перезагруз|не шумн"),
    ("romantic", r"романт|медов\w* месяц|годовщин|свадеб|предложение сделать|с девушкой|с любим|с парнем"),
    ("nature", r"природ|поход|трекинг|хайкинг|\bлес|озер|вулкан|водопад|заповедн|горы и|национальн\w* парк"),
    ("mountains", r"\bгор[аыуе]?\b|горн(?!олыж)|в горы|горах"),
    ("ski", r"лыж|сноуборд|горнолыж|покататься|кататься на"),
    ("shopping", r"шопинг|шоппинг|магазин|аутлет"),
    ("wellness", r"\bспа\b|spa|оздоров|санатор|минеральн|лечени|термы|термальн|аюрвед|йог"),
    ("diving", r"дайв|снорк|риф|кораллов|подводн"),
    ("northern_lights", r"северн\w* сияни|полярн\w* сияни"),
    ("kids_fun", r"аквапарк|анимаци|детск\w* клуб|развлечени\w* для дет"),
    ("city", r"городск|в город|мегаполис|city break|сити"),
    ("surf", r"серф|сёрф"),
    ("adventure", r"приключ|экстрим|рафтинг|драйв"),
]


def norm(text: str) -> str:
    t = text.lower().replace("ё", "е")
    t = re.sub(r"[«»\"“”]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def to_num(tok: str) -> int | None:
    tok = tok.strip()
    if tok.isdigit():
        return int(tok)
    return NUM_WORDS.get(tok)


def month_from_stem(s: str) -> int | None:
    for i, st in enumerate(MONTH_STEMS):
        if re.match(st, s):
            return i + 1
    return None


def _year_for(month: int, day: int = 1) -> int:
    t = today()
    y = t.year
    try:
        d = date(y, month, min(day, monthrange(y, month)[1]))
    except ValueError:
        return y
    return y if d >= t else y + 1


def _mkdate(day: int, month: int, year: int | None = None) -> date | None:
    y = year or _year_for(month, day)
    try:
        return date(y, month, day)
    except ValueError:
        return None


def _next_weekday(start: date, wd: int) -> date:
    days = (wd - start.weekday()) % 7
    return start + timedelta(days=days or 7)


# ---------------------------------------------------------------- даты

def parse_dates(t: str, out: dict) -> None:
    td = today()
    # диапазоны: «с 10 по 17 ноября», «с 28 декабря по 5 января», «с 1 июня 2027 по 1 июня 2028 года»,
    # «с 1 июня по 1 июня следующего года»
    year_re = r"(?:\s+(\d{4})\s*(?:г(?:ода|од)?\.?)?)?"
    m = re.search(r"(?<![\d.])(\d{1,2})(?:\s+" + MONTH_RE + r")?" + year_re + r"\s*(?:-|–|—|по|до)\s*"
                  r"(\d{1,2})\s+" + MONTH_RE + year_re + r"(\s+следующ\w+\s+год\w*)?", t)
    if m:
        day1, mon1_s, y1_s, day2, mon2_s, y2_s, next_year = m.groups()
        mon2 = month_from_stem(mon2_s)
        mon1 = month_from_stem(mon1_s) if mon1_s else mon2
        if not mon1_s and int(day1) > int(day2):  # «с 30 по 3 января» — начало в предыдущем месяце
            mon1 = mon2 - 1 if mon2 > 1 else 12
        y1 = int(y1_s) if y1_s else None
        y2 = int(y2_s) if y2_s else None
        if y1 is None:
            if y2 is not None:
                y1 = y2 if (mon1, int(day1)) < (mon2, int(day2)) else y2 - 1
            else:
                y1 = _year_for(mon1, int(day1))
        if y2 is None:
            y2 = y1 + 1 if next_year or (mon2, int(day2)) <= (mon1, int(day1)) else y1
        d1, d2 = _mkdate(int(day1), mon1, y1), _mkdate(int(day2), mon2, y2)
        if d1 and d2 and d2 > d1:
            out.update(date_from=d1.isoformat(), date_to=d2.isoformat(), month=d1.month, year=d1.year, flexible=False)
            return
    # 12.11 - 19.11 / 12.11.2026
    ds = re.findall(r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\b", t)
    if ds:
        parsed = []
        for d_, m_, y_ in ds:
            y = int(y_) + (2000 if y_ and len(y_) == 2 else 0) if y_ else None
            if 1 <= int(m_) <= 12:
                dd = _mkdate(int(d_), int(m_), y)
                if dd:
                    parsed.append(dd)
        if len(parsed) >= 2 and parsed[1] > parsed[0]:
            out.update(date_from=parsed[0].isoformat(), date_to=parsed[1].isoformat(), month=parsed[0].month, flexible=False)
            return
        if len(parsed) == 1:
            out.update(date_from=parsed[0].isoformat(), month=parsed[0].month, flexible=False)
            return
    # праздники
    if re.search(r"новогодн|на новый год|новый год|на нг\b|на праздники в январ|рождеств", t):
        y = td.year if td.month <= 12 and not (td.month == 12 and td.day > 29) else td.year + 1
        out.update(date_from=date(y, 12, 30).isoformat(), month=12, flexible=False)
        out.setdefault("nights", 7)
        return
    if re.search(r"майск", t):
        y = _year_for(5, 1)
        out.update(date_from=date(y, 5, 1).isoformat(), month=5, flexible=False)
        out.setdefault("nights", 5)
        return
    # выходные
    if re.search(r"(?<![а-я])(длинные\s+)?выходн(ые|ых)(?!\s+дн)", t) or re.search(r"уикенд|викенд", t):
        start = td + timedelta(days=1)
        if "следующ" in t:
            start = td + timedelta(days=7)
        fri = _next_weekday(start - timedelta(days=1), 4)
        out.update(date_from=fri.isoformat(), month=fri.month, flexible=False)
        out["nights"] = 3 if "длинн" in t else 2
        return
    m = re.search(r"через\s+" + NUM_RE + r"?\s*(недел|месяц|дн|день)", t)
    if m:
        n = to_num(m.group(1)) if m.group(1) else 1
        unit = m.group(2)
        days = n * (7 if unit.startswith("недел") else 30 if unit.startswith("месяц") else 1)
        d = td + timedelta(days=days)
        out.update(date_from=d.isoformat(), month=d.month, flexible=False)
        return
    if re.search(r"\bзавтра\b", t):
        d = td + timedelta(days=1)
        out.update(date_from=d.isoformat(), month=d.month, flexible=False)
        return
    if re.search(r"(в\s+)?следующ\w+\s+месяц", t):
        m_ = td.month % 12 + 1
        out.update(month=m_, year=_year_for(m_))
        return
    # одиночная дата: «с 12 ноября», «12 ноября 2027»
    m = re.search(r"(?:с\s+|на\s+|вылет\s+)?(?<![\d.])(\d{1,2})\s+" + MONTH_RE + r"(?:\s+(\d{4}))?", t)
    if m and not re.search(r"\d{1,2}\s+" + MONTH_RE + r"\s+(лет|год)(?!а\b)", t):
        d = _mkdate(int(m.group(1)), month_from_stem(m.group(2)), int(m.group(3)) if m.group(3) else None)
        if d:
            out.update(date_from=d.isoformat(), month=d.month, flexible=False)
            return
    # месяц: «в ноябре», «ноябрь», «в начале декабря»
    m = re.search(r"(начал\w*|середин\w*|конц\w*|перв\w+ половин\w*|втор\w+ половин\w*)?\s*" + MONTH_RE, t)
    if m:
        mon = month_from_stem(m.group(2))
        if mon:
            y = _year_for(mon, 28)
            out.update(month=mon, year=y)
            part = m.group(1) or ""
            day = 3 if part.startswith("начал") or part.startswith("перв") else 14 if part.startswith("серед") else \
                22 if part.startswith("конц") or part.startswith("втор") else None
            if day:
                d = _mkdate(day, mon, y)
                if d and d > td:
                    out.update(date_from=d.isoformat(), flexible=True)
            return
    seasons = [(r"\bлетом|\bлето\b|летн\w+ отпуск", [7, 6, 8]), (r"\bосенью|\bосень\b", [9, 10, 11]),
               (r"\bзимой|\bзима\b|зимн\w+ отпуск", [12, 1, 2]), (r"\bвесной|\bвесна\b", [4, 5, 3])]
    for pat, months in seasons:
        if re.search(pat, t):
            chosen = None
            for mon in months:
                if _year_for(mon, 28) == td.year and date(td.year, mon, 1) + timedelta(days=27) >= td + timedelta(days=7):
                    chosen = mon
                    break
            chosen = chosen or months[0]
            out.update(month=chosen, year=_year_for(chosen, 28), _season=months)
            return
    if re.search(r"\bсейчас\b|\bсрочно\b|в ближайш\w* (время|дни|неделю)|прямо сейчас|на днях", t):
        d = td + timedelta(days=4)
        out.update(date_from=d.isoformat(), month=d.month, flexible=True)


def parse_duration(t: str, out: dict) -> None:
    if re.search(r"(позже|раньше|пораньше|попозже|дольше|короче|меньше|больше)\b", t) and re.search(
            r"на\s+" + NUM_RE + r"?\s*(дн|день|дня|ноч|недел)\w*\s+(позже|раньше|пораньше|попозже|дольше|короче|меньше|больше)", t):
        return
    m = re.search(r"на\s+(\d+|" + "|".join(NUM_WORDS) + r")\s*-?\s*(ноч|дн|день|дня|сут)", t)
    if m:
        n = to_num(m.group(1))
        if n:
            out["nights"] = n if m.group(2).startswith("ноч") else (max(1, n - 1) if n <= 4 else n)
            return
    m = re.search(r"\b(\d+|" + "|".join(NUM_WORDS) + r")\s*(ноч|дней|дня|суток)", t)
    if m and not re.search(r"(позже|раньше)", t):
        n = to_num(m.group(1))
        if n and n < 400:
            out["nights"] = n if m.group(2).startswith("ноч") else (max(1, n - 1) if n <= 4 else n)
            return
    if re.search(r"полторы недел", t):
        out["nights"] = 10
    elif re.search(r"(две|2)\s*недел", t):
        out["nights"] = 14
    elif re.search(r"(три|3)\s*недел", t):
        out["nights"] = 21
    elif re.search(r"на\s+недел|недельк|на одну недел|неделю\b", t) and not re.search(r"через недел|на неделю (позже|раньше)", t):
        out["nights"] = 7
    elif re.search(r"на\s+месяц\b", t):
        out["nights"] = 28
    elif re.search(r"на\s+пару\s+дн", t):
        out["nights"] = 2


# ---------------------------------------------------------------- состав

KID_WORDS = r"(реб[её]н|дет[еяи]|детьм|сын|доч|малыш|младен|грудн|школьн|подрост|внук|внучк|дочур|сынишк|карапуз)"


def parse_party(t: str, out: dict, expect_ages: bool = False) -> None:
    kids = None
    ages: list[int] = []
    m = re.search(r"(?:с\s+)?(двумя|тремя|двое|трое|четверо|2|3|4)\s*(?:маленьк\w+\s+)?(детьми|детей|ребенка|ребят|дет|малыш)", t)
    if m:
        kids = {"двумя": 2, "двое": 2, "2": 2, "тремя": 3, "трое": 3, "3": 3, "четверо": 4, "4": 4}[m.group(1)]
    elif re.search(r"с\s+(реб[её]нком|сыном|дочкой|дочерью|малышом|младенцем|грудничком|дочуркой|сынишкой|подростком|внуком|внучкой|"
                   r"\d{1,2}-?летн\w+\s+\w+)", t):
        kids = 1
    elif re.search(r"(один|1)\s+реб[её]н", t) or re.search(r"реб[её]нку|реб[её]нок\s+\d|дочке\s+\d|сыну\s+\d", t):
        kids = 1
    elif re.search(r"с\s+детьми|с\s+детками|семь[её]й с дет|дети\s", t):
        kids = -1  # количество неизвестно
    # возрасты
    for mm in re.finditer(r"(\d{1,2}(?:[.,]5)?)(?:\s*(?:,|и)\s*(\d{1,2}))?(?:\s*(?:,|и)\s*(\d{1,2}))?\s*(?:-?\s*(?:ти|ми|х|и))?\s*"
                          r"(лет|год|годик|годиков|года|мес)", t):
        nums = [g for g in mm.groups()[:3] if g]
        unit = mm.group(4)
        ctx = t[max(0, mm.start() - 40): mm.end() + 25]
        if not (re.search(KID_WORDS, ctx) or expect_ages):
            continue
        if re.search(r"(годовщин|лет (свадьб|вместе|брак))", ctx):
            continue
        for n in nums:
            v = float(n.replace(",", "."))
            age = 0 if unit == "мес" else int(v)
            if age < 18:
                ages.append(age)
    for mm in re.finditer(r"(\d{1,2})\s*-?\s*летн", t):
        if int(mm.group(1)) < 18 and int(mm.group(1)) not in ages:
            ages.append(int(mm.group(1)))
    mm = re.search(r"(?:реб[её]нку|реб[её]нок|сыну|дочке|дочери|сын|дочь|дочка|малышу|детям|дети|детьми)\s+(\d{1,2})(?:\s*(?:и|,)\s*(\d{1,2}))?(?!\s*(?:лет|год|мес|ч|дн|ноч|\d))", t)
    if mm:
        for g in mm.groups():
            if g and int(g) < 18 and int(g) not in ages:
                ages.append(int(g))
    if expect_ages and not ages:
        nums = re.findall(r"\b(\d{1,2})\b", t)
        if nums and not re.search(r"(тыс|руб|₽|ноч|дн|час)", t):
            ages = [int(n) for n in nums if int(n) < 18]
    if re.search(r"младен|грудн|до года", t) and 0 not in ages:
        ages.append(0)
    if re.search(r"подрост", t) and not ages:
        ages.append(14)
    if re.search(r"школьник", t) and not ages:
        ages.append(10)
    if ages and kids and kids > len(ages) == 1 and re.search(r"\bпо\s+\d{1,2}\s*(?:-?\s*(?:ти|ми|х))?\s*(лет|год|мес)|"
                                                            r"оба\w*\s+\d|обоим\s+\d|близнец|двойняшк", t):
        ages = ages * kids  # «двое детей по 7 лет», «близнецы 5 лет»
    if ages:
        if kids is None or kids == -1 or kids < len(ages):
            kids = len(ages)
        out["children_ages"] = ages[:kids]
        out["children_pending"] = max(0, kids - len(ages))
    elif kids:
        out["children_ages"] = []
        out["children_pending"] = 2 if kids == -1 else kids
        if kids == -1:
            out["_kids_count_unknown"] = True
    if re.search(r"без\s+детей", t) and not kids:
        out["children_ages"], out["children_pending"] = [], 0

    n_kids = len(out.get("children_ages", [])) + out.get("children_pending", 0)
    # взрослые
    adults = None
    m = re.search(r"(\d+|двое|трое|четверо|пятеро|шестеро)\s+взросл", t)
    if m:
        adults = {"двое": 2, "трое": 3, "четверо": 4, "пятеро": 5, "шестеро": 6}.get(m.group(1)) or int(m.group(1))
    m2 = re.search(r"(?:нас|компани\w+ из|группа из|едем)\s+(\d+|двое|трое|четверо|пятеро|шестеро)\s*(человек|чел)?", t) or \
        re.search(r"\b(\d+)\s*(человек|чел\b|персон)", t)
    if adults is None and m2:
        n = {"двое": 2, "трое": 3, "четверо": 4, "пятеро": 5, "шестеро": 6}.get(m2.group(1)) or int(m2.group(1))
        adults = max(1, n - n_kids)
    if adults is None:
        m4 = re.search(r"(?<![а-я])(двое|трое|четверо|пятеро)(?![а-я])(?!\s+(детей|дет|ребят|малыш))", t)
        if m4:
            adults = max(1, {"двое": 2, "трое": 3, "четверо": 4, "пятеро": 5}[m4.group(1)] - n_kids)
    if adults is None:
        m3 = re.search(r"\b(вдвоем|втроем|вчетвером|впятером|вшестером)", t)
        if m3:
            n = {"вдвоем": 2, "втроем": 3, "вчетвером": 4, "впятером": 5, "вшестером": 6}[m3.group(1)]
            adults = max(1, n - n_kids)
    if adults is None:
        partner = re.search(r"с\s+(женой|мужем|супруг\w*|девушкой|парнем|подругой|другом|любим\w+|второй половин\w*|невестой|"
                            r"женихом|бойфренд\w*|мамой|папой|сестрой|братом|коллегой|бабушкой|дедушкой)", t)
        parents = re.search(r"с\s+(\w+\s+)?родител", t)
        pair = re.search(r"(мамы|папы|мамой|папой|мужа|жены|подруги|друга|сестры|брата)\s+и\s+(меня|мной|я)|(меня|я)\s+и\s+(мамы|мамой|папы|мужа|жены)", t)
        friends = re.search(r"с\s+друзьями|с\s+подругами|компанией|с\s+коллегами", t)
        solo = re.search(r"\b(соло|в одиночку|один|одна|сам|сама)\b", t) and re.search(
            r"(еду|поеду|лечу|полечу|отдыхаю|путешествую|отправлюсь|хочу|буду|я)\s+(один|одна|сам|сама)\b|\bсоло\b|в одиночку|"
            r"(один|одна)\s+(еду|поеду|лечу|полечу|хочу|отдыхать|отдохнуть)", t)
        family = re.search(r"\bсемь[её]й\b|всей семь", t)
        business = re.search(r"по работе|командировк|на конференц|деловая поездк", t)
        both_partner = re.search(r"с\s+(женой|мужем|супруг\w*)\s+и\s+(реб|дет|сын|доч)", t)
        if both_partner or (partner and n_kids):
            adults = 2
        elif partner:
            adults = 2 + len(re.findall(r"\s+и\s+(мамой|папой|сестрой|братом|другом|подругой)", t))
        elif parents:
            adults = 3
        elif pair:
            adults = 2
        elif friends:
            adults = 4
        elif solo or business:
            adults = 1
        elif family or n_kids:
            adults = 2
            out["_adults_assumed"] = True
    if adults:
        out["adults"] = adults
    # питомцы
    if re.search(r"(собак|собачк|пес\b|псом|пса\b|щен|корги|шпиц|той-терьер|овчарк|лабрадор|хаски\b(?! парк)|мопс|такс[аоу])", t) and \
            not re.search(r"без собак|собак\w* оставим|не с собак", t):
        out["pets"] = "dog"
    elif re.search(r"(\bкот\b|котом|кошк|котик|котенк|питом|животн|хомяк|попуга)", t) and not re.search(r"без (питом|животн|кошк)", t):
        out["pets"] = "cat" if re.search(r"\bкот|кошк|котик|котенк", t) else "pet"
    if re.search(r"без (собак|питом|животн|кошк|кота)|собак\w* (не едет|оставим|не берем)|питом\w* не едет", t):
        out["pets"] = None


# ---------------------------------------------------------------- бюджет

def parse_budget(t: str, out: dict) -> None:
    best = None
    for m in re.finditer(r"(\d+(?:[  ]\d{3})*(?:[.,]\d+)?)\s*(к\b|k\b|тыс\w*|тр\b|т\.?р\.?|млн|миллион\w*|лям\w*)?\s*(₽|руб\w*|р\.|р\b)?", t):
        num_s, unit, cur = m.group(1), m.group(2), m.group(3)
        after = t[m.end(): m.end() + 14]
        before = t[max(0, m.start() - 30): m.start()]
        if re.match(r"\s*(-?\s*х\s+)?(час|ч\b|ноч|дн|дня|день|лет|год|км|мин|звезд|\*|человек|чел|взросл|дет|реб|недел|м\b|метр|мес|сут|числ|-?го\b|раз)", after):
            continue
        if re.match(r"\s*(" + MONTH_RE + ")", after):
            continue
        v = float(num_s.replace(" ", "").replace(" ", "").replace(",", "."))
        if unit:
            if unit.startswith(("к", "k", "тыс", "тр", "т")):
                v *= 1000
            elif unit.startswith(("млн", "милл", "лям")):
                v *= 1_000_000
        money_ctx = bool(unit or cur) or re.search(r"(бюджет|до|за|не дороже|не больше|в пределах|максимум|около|примерно|потратить|уложиться)\s*$", before)
        if not money_ctx or v < 5000:
            continue
        if v > 20_000_000:
            continue
        best = int(v)
        per_person = re.match(r"\s*(₽|руб\w*)?\s*(на|с)\s+(человек|чел|одного|каждого|нос|персон)", after)
        if per_person:
            out["_budget_per_person"] = True
    if best:
        out["budget"] = best
    if re.search(r"недорог|бюджетн|дешев|эконом|подешевле|не\s+дорого|подешевке|копейк|сэконом|без лишних трат|студент", t):
        out["budget_level"] = "low"
    elif re.search(r"не\s+экономим|роскош|люкс|шикарн|премиум|vip|вип|по-богатому|деньги не (вопрос|проблема)|без ограничен\w* (по )?бюджет|"
                   r"бюджет не огранич|лучш\w+ отел|пятизвезд|5\s*звезд", t):
        out["budget_level"] = "high"


# ---------------------------------------------------------------- места

ORIGIN_STOP = {"них", "этих", "этого", "этой", "тех", "двух", "трех", "всех", "списка", "предложенных", "вариантов", "вариант",
               "отеля", "дома", "отпуска", "офиса", "чего", "него", "нее", "россии", "города", "страны", "поездки", "поездка",
               "отдыхать", "отдохнуть", "поехать", "полететь", "хотим", "хочу", "едем", "летим", "вдвоем", "втроем", "семьей",
               "детьми", "ребенком", "собакой", "женой", "мужем", "друзьями", "москвы", "сразу", "самолета", "поезда", "аэропорта",
               "работы", "командировки", "моря", "отпуск", "через", "ближайшего", "какого", "любого", "далеко", "неделю"}

def parse_places(t: str, out: dict) -> None:
    # город вылета
    origin_spans = []
    for m in re.finditer(r"(?<![а-я])(?:из|от|вылет\w*\s+из|живу\s+в|живем\s+в|я\s+в|мы\s+в|я\s+из|мы\s+из|с\s+вылетом\s+из|"
                         r"стартуем\s+из|выезжаем\s+из|едем\s+из|летим\s+из|летим\s+с|из\s+г\.?)\s+([а-я-]+(?:\s+[а-я-]+)?)", t):
        chunk = m.group(1)
        for o in ORIGINS:
            if any(chunk.startswith(a) or (" " in chunk and chunk.split()[0].startswith(a)) for a in o.aliases):
                out["origin"] = o.code
                origin_spans.append((m.start(), m.end()))
                break
    if "origin" not in out:
        # город вылета не из справочника: «из Магнитогорска», «город вылета — Магнитогорск»
        place = r"([а-я][а-я-]{3,}(?:\s+[а-я][а-я-]{3,})?)"
        m = re.search(r"(?:город\w*\s+вылета|вылет\w*)\s*(?:из|на|—|-|:)?\s*" + place, t) or \
            re.search(r"(?:стартуем|выезжаем|едем|летим|мы|я)\s+из\s+" + place, t) or \
            re.search(r"(?:живу|живем|живём)\s+в\s+" + place, t)
        strong = bool(m)
        if not m:
            m = re.search(r"(?<![а-я])из\s+" + place, t)
        if m:
            words = m.group(1).split()
            if words[0] not in ORIGIN_STOP and not re.match(MONTH_RE, words[0]):
                two = len(words) == 2 and words[1] not in ORIGIN_STOP and words[1].endswith(("а", "я", "и", "ы", "е", "о"))
                out["_origin_place"] = " ".join(words) if two else words[0]
                out["_origin_strong"] = strong
                origin_spans.append((m.start(), m.end()))

    def in_origin(pos):
        return any(a <= pos < b for a, b in origin_spans)
    # исключения: «не в Турцию», «кроме Египта»
    avoid = []
    for m in re.finditer(r"(кроме|только не|не хочу в|не хочу на|не|без|надоел\w*|были уже в|уже были в)\s+(в\s+|на\s+)?([а-я-]+)", t):
        word = m.group(3)
        for alias, country in COUNTRY_ALIASES.items():
            if word.startswith(alias) and country != "Россия":
                avoid.append(country)
        for d in DESTINATIONS:
            if any(word.startswith(a.strip()) for a in d.aliases if len(a.strip()) >= 4):
                avoid.append(d.id)
    if avoid:
        out["avoid"] = sorted(set(avoid))
    found = []
    for d in DESTINATIONS:
        for a in d.aliases:
            a = a.strip()
            for m in re.finditer(r"(?<![а-я])" + re.escape(a), t):
                if not in_origin(m.start()) and d.id not in avoid and d.country not in avoid:
                    found.append((m.start(), d.id))
    if found:
        found.sort()
        out["destination"] = found[-1][1]
    countries = []
    for alias, country in COUNTRY_ALIASES.items():
        for m in re.finditer(r"(?<![а-я])" + re.escape(alias), t):
            if not in_origin(m.start()) and country not in avoid:
                countries.append((m.start(), country))
    if countries and "destination" not in out:
        countries.sort()
        c = countries[-1][1]
        dests = [d.id for d in DESTINATIONS if d.country == c or (c == "Россия" and d.domestic)]
        if len(dests) == 1:
            out["destination"] = dests[0]
        else:
            out["country"] = c
    if "destination" not in out:
        for pat, dest_id, note in GATEWAYS:
            m = re.search(r"(?<![а-я])(?:" + pat + ")", t)
            if m and not in_origin(m.start()):
                out["destination"], out["_gateway_note"] = dest_id, note
                out.pop("country", None)
                break
    if "destination" not in out and "country" not in out:
        for pat, name, prefs, similar in UNKNOWN_PLACES:
            m = re.search(r"(?<![а-я])(?:" + pat + ")", t)
            if m and not in_origin(m.start()):
                word = re.match(r"[а-я-]+", t[m.start():]).group(0)
                out["_unknown_place"] = {"name": name, "prefs": list(prefs), "similar": list(similar), "word": m.group(0),
                                         "query": word.capitalize()}
                break
    if re.search(r"куда[- ]?(нибудь|угодно|то)|не знаю куда|посоветуй\w* (куда|направлен)|предлож\w+ (куда|направлен|вариант)|"
                 r"любое направлен|без разницы куда|куда поехать|куда полететь|куда съездить|куда махнуть", t):
        out["_open_destination"] = True


# ---------------------------------------------------------------- предпочтения

def parse_prefs(t: str, out: dict) -> None:
    prefs = []
    for key, pat in PREF_PATTERNS:
        if re.search(pat, t):
            prefs.append(key)
    if "warm" in prefs and re.search(r"не\s+(люблю|хочу|переношу)\s+жар|без жары|не\s+жарк|не в жару|жару не", t):
        prefs.remove("warm")
        prefs.append("mild")
    if "mild" not in prefs and re.search(r"без жары|не жарко|нежарк|не в жару", t):
        prefs = [p for p in prefs if p != "warm"] + ["mild"]
    if re.search(r"не\s+(люблю|хочу)\s+(тус|шум|толп)", t):
        prefs = [p for p in prefs if p != "party"] + ["quiet"]
    if "sea" in prefs and re.search(r"\bморе не\b|без моря|не (на )?море", t):
        prefs.remove("sea")
    if re.search(r"не в сезон дожд|без дожд|чтобы не было дожд|сухой сезон|не дождлив|не хочу (под )?дожд|не хочу ливн", t):
        prefs.append("dry")
    if {"surf", "diving"} & set(prefs):
        prefs.append("sea")
    if re.search(r"бассейн", t):
        prefs.append("pool")
    if re.search(r"кухн\w* в номере|апартамент|сам\w* готовить", t):
        prefs.append("kitchen")
    if "mountains" in prefs and "ski" in prefs:
        pass
    if prefs:
        out["prefs"] = sorted(set(prefs))
    # питание: учитываем «вместо всё включено», «не нужно всё включено»
    meal_pats = [("AI", r"вс[её]\s*включен|олл\s*инклюзив|all\s*inclusive|ультра|\bai\b|\buai\b"),
                 ("HB", r"полупансион|завтрак\w* и ужин"), ("BB", r"с\s+завтрак|завтрак\w* включ|только завтрак|завтрак"),
                 ("RO", r"без питания|сами (будем )?готовить")]
    hits = []
    for code, pat in meal_pats:
        for m in re.finditer(pat, t):
            neg = re.search(r"(вместо|не нужн\w*|не надо|не хочу|без|кроме|не)\s+$", t[max(0, m.start() - 14): m.start()])
            if not neg:
                hits.append((m.start(), code))
    if hits:
        out["meal"] = sorted(hits)[0][1] if len({c for _, c in hits}) == 1 else min(hits, key=lambda x: ["HB", "AI", "BB", "RO"].index(x[1]))[1]
    # отмена
    if re.search(r"бесплатн\w* отмен|возможность\w* отмен|можно было отменить|с отменой|возвратн\w* тариф|вернуть деньги|"
                 r"гибк\w* тариф|планы могут поменяться|могут измениться", t) and not re.search(r"невозвратн\w* (ок|подойд|можно|норм)", t):
        out["free_cancel"] = True
    if re.search(r"невозвратн\w* (ок|подойд|можно|норм)|отмена не нужна|не нужна отмена", t):
        out["free_cancel"] = False
    # транспорт
    if re.search(r"на поезде|поездом|\bржд\b|не летать|боюсь летать|без самолет|не хочу лететь|не люблю летать|на сапсане|ласточк|"
                 r"без\s+перел[её]т(ов|а)?(?![а-я])|не на самолет", t):
        out["transport"] = "train"
    elif re.search(r"на самолете|самолетом|только перел[её]т|не на поезде|без поезд", t):
        out["transport"] = "plane"
    if re.search(r"без пересад|прям\w+ рейс|прямым|прямой перел|только прям", t):
        out["direct_only"] = True
    m = re.search(r"(?:не\s+)?(?:больше|более|дольше|до|максимум|не более|в пределах)\s+(\d+(?:[.,]\d)?|" + "|".join(NUM_WORDS) +
                  r")\s*-?\s*(?:х\s+)?(?:ч\b|час)", t)
    if m and re.search(r"(лет|перел|в пути|дорог|самолет|добира|рейс|ехать|поезд)", t):
        v = to_num(m.group(1)) if not re.match(r"\d", m.group(1)) else float(m.group(1).replace(",", "."))
        if v and 1 <= v <= 30:
            out["max_flight_h"] = float(v)
    elif re.search(r"без\s+долг\w+\s+(перел|дорог|путешеств)|недолг\w+ перел|коротк\w+ перел|недалеко|не далеко|не очень далеко|близко|"
                   r"не\s+долго\s+лет|не хочу долго (лет|ехать)|не люблю долго (лет|ехать)|недолго лет|ненадолго лет|без длинн\w+ перел", t):
        out["max_flight_h"] = 5.5
    # звёзды
    m = re.search(r"(\d)\s*(?:\*|★|звезд|зв\b)", t)
    if m and 2 <= int(m.group(1)) <= 5:
        out["stars_min"] = int(m.group(1))
    elif re.search(r"пятерк|пятизвезд", t):
        out["stars_min"] = 5
    elif re.search(r"четверк", t):
        out["stars_min"] = 4
    if re.search(r"перв\w+ линии|первая линия|у самого моря|на берегу|прямо на пляже|рядом с пляжем|у моря", t):
        out["beach_close"] = True
        out.setdefault("prefs", [])
        if "sea" not in out["prefs"]:
            out["prefs"] = sorted(set(out["prefs"] + ["sea"]))
    if re.search(r"в центре|в самом центре|ближе к центру|центр города|рядом с центром|недалеко от центра|в центральн", t):
        out["center_close"] = True
    if re.search(r"без виз|безвиз|виз\w* не|не нужна виза|чтобы не нужна была виза", t):
        out["visa_free"] = True
    if re.search(r"коляск|инвалид|доступн\w+ сред|маломобил", t):
        out["accessible"] = True
    # приоритет
    if re.search(r"главное\W+(подешевле|дешевле|цена|сэконом|недорого)|важнее цена|минимальн\w+ цен|самое дешевое|самый дешевый", t):
        out["priority"] = "price"
    elif re.search(r"главное\W+(комфорт|удоб|качеств|сервис)|комфорт важнее|не экономим|важнее комфорт|максимальн\w+ комфорт", t):
        out["priority"] = "comfort"
    elif re.search(r"главное\W+(быстр|побыстрее|меньше в дороге)|быстрее добраться|меньше времени в пути", t):
        out["priority"] = "time"
    # время вылета
    if re.search(r"утренн\w+ (рейс|вылет|поезд)|вылет\w* утром|утром вылет|пораньше утром", t):
        out["flight_time_pref"] = "morning"
    elif re.search(r"вечерн\w+ (рейс|вылет|поезд)|вылет\w* вечером|вечером вылет", t):
        out["flight_time_pref"] = "evening"
    elif re.search(r"дневн\w+ (рейс|вылет)|вылет\w* дн[её]м|не ночн|не ночью|без ночных", t):
        out["flight_time_pref"] = "day"


# ---------------------------------------------------------------- намерения

ORDINALS = [(r"\bперв\w*|\b1\b|№\s*1|номер один", 0), (r"\bвтор\w*|\b2\b|№\s*2", 1), (r"\bтрет\w*|\b3\b|№\s*3", 2),
            (r"\bчетв\w*|\b4\b|№\s*4", 3), (r"\bпоследн\w*", -1)]


def parse_intent(t: str, out: dict, stage: str) -> None:
    def has(p):
        return re.search(p, t) is not None

    # сдвиг дат
    m = re.search(r"(?:на|сдвин\w*\s+на|перенес\w*\s+на|перенести\s+на)\s+" + NUM_RE + r"?\s*(дн\w*|день|дня|недел\w*|ноч\w*)?\s*"
                  r"(позже|раньше|пораньше|попозже|вперед|назад)", t) or \
        re.search(r"(позже|раньше|пораньше|попозже)\s+на\s+" + NUM_RE + r"?\s*(дн\w*|день|дня|недел\w*)", t)
    if m:
        g = m.groups()
        if g[0] in ("позже", "раньше", "пораньше", "попозже"):
            direction, n_s, unit = g[0], g[1], g[2]
        else:
            n_s, unit, direction = g[0], g[1], g[2]
        n = to_num(n_s) if n_s else 1
        if unit and unit.startswith("недел"):
            n *= 7
        sign = -1 if direction in ("раньше", "пораньше", "назад") else 1
        out["_intent"] = "shift_dates"
        out["_shift"] = sign * (n or 1)
    m = re.search(r"на\s+" + NUM_RE + r"?\s*(дн\w*|день|дня|ноч\w*|недел\w*)\s+(дольше|больше|короче|меньше)", t)
    if m:
        n = to_num(m.group(1)) if m.group(1) else 1
        if m.group(2).startswith("недел"):
            n *= 7
        out["_intent"] = "extend"
        out["_extend"] = n if m.group(3) in ("дольше", "больше") else -n
    if "_intent" in out:
        return
    if has(r"^(начать )?заново|начн\w+ (сначала|заново)|сначала\b|новый поиск|нов\w+ поездк|сброс|другая поездка|забудь (все|это)"):
        out["_intent"] = "reset"
    elif stage == "booked" and has(r"ранн\w+ заезд|заселиться (пораньше|раньше|рано)|заехать (пораньше|раньше)"):
        out["_intent"] = "early_checkin"
    elif stage == "booked" and has(r"поздн\w+ выезд|выехать (позже|попозже)"):
        out["_intent"] = "late_checkout"
    elif stage == "booked" and has(r"напомин|напомни|календар"):
        out["_intent"] = "reminders"
    elif stage == "booked" and has(r"отмен\w+ (брон|поездк|заказ)|хочу отменить|отменить все"):
        out["_intent"] = "cancel_booking"
    elif stage == "booked" and has(r"(измен|помен|перенес|сдвин)\w*\s+(даты|брон|поездк)"):
        out["_intent"] = "change_booking"
    elif stage == "booked" and has(r"трансфер|такси|довезти|встретить"):
        out["_intent"] = "add_transfer"
    elif has(r"почему|зачем ты|обоснуй|объясни|с чего ты взял|как ты выбрал|по какому принцип"):
        out["_intent"] = "why"
    elif has(r"(покажи|другие|другой|сравни|еще|ещё)\s+(\w+\s+)?(отел|гостиниц|вариант\w* (отел|жиль)|жиль)"):
        out["_intent"] = "show_hotels"
    elif has(r"(покажи|другие|другой|сравни|еще|ещё)\s+(\w+\s+)?(рейс|перел|билет|поезд|маршрут)"):
        out["_intent"] = "show_flights"
    elif has(r"(другие|другое|еще|ещё|покажи)\s+(\w+\s+)?(направлен|страны|страну|места|город)|куда еще|куда ещё|вернись к направлен|"
             r"к выбору направлен|не нравится направлен"):
        out["_intent"] = "show_destinations"
    elif has(r"(?<![а-я])дорого|дороговато|дешевле|подешевле|бюджет\w*\s+(меньше|поменьше|ниже|урезать|сократ)|уменьш\w+ бюджет|"
             r"сэкономить|экономнее|подешевк"):
        out["_intent"] = "cheaper"
    elif has(r"подороже|получше|покруче|посолиднее|побогаче|дороже можно|бюджет\w*\s+(больше|побольше|выше)|увелич\w+ бюджет|"
             r"можно потратить больше"):
        out["_intent"] = "pricier"
    elif has(r"без трансфер|трансфер не нужен|трансфер не надо|сами доберемся|сами доедем|доберемся сами|уберите трансфер|убери трансфер"):
        out["_intent"] = "no_transfer"
    elif stage in ("bundles", "cart") and has(r"(добавь|нужен|верни|с)\s+трансфер"):
        out["_intent"] = "with_transfer"
    elif has(r"страховк"):
        out["_intent"] = "add_insurance"
    elif has(r"оплат|оплачи|плачу|заплат"):
        out["_intent"] = "pay"
    elif has(r"\bберу\b|берем|бронир|оформ|покупа|купить|куплю|в корзину|подходит|годится|устраивает|забронируй|давай (его|этот|эту|так)|"
             r"^(да|ок|окей|давай|отлично|супер|хорошо)[\s,.!]*((бер|оформ|брон)\w*)?[.!]*$"):
        out["_intent"] = "book"
    elif has(r"погод|что посмотреть|чем заняться|что делать|достопримеч|какая (там )?виза|нужна ли виза|какие документ|"
             r"что взять|расскажи (про|о)|что (там )?интересн"):
        out["_intent"] = "info"
    elif has(r"^(неважно|не важно|без разницы|любой|любые|любая|не знаю|пропусти\w*|подбери сам\w*|на твой выбор|реши сам|"
             r"как хочешь|все равно|всё равно|дальше|пропустить)"):
        out["_intent"] = "skip"
    elif has(r"^(привет|здравств|добрый|хай|hello|hi)\b") and len(t) < 30:
        out["_intent"] = "greet"
    # выбор пункта
    if stage in ("destinations", "bundles") and out.get("_intent") in (None, "book"):
        slots_here = any(not k.startswith("_") for k in out)
        priority_talk = has(r"главное|важнее|приоритет|в первую очередь")
        if stage == "bundles" and not priority_talk:
            for pat, code in ((r"оптимальн", "optimal"), (r"экономн\w* вариант|вариант\w* эконом|беру эконом|давай эконом|^экономн\w*[.!]?$", "economy"),
                              (r"комфорт\w* вариант|вариант\w* комфорт|беру комфорт|давай комфорт|^комфорт\w*[.!]?$|«комфорт»", "comfort")):
                if has(pat):
                    out["_choice_code"] = code
        for pat, idx in ORDINALS:
            if slots_here:
                pat = pat.split("|")[0]  # при наличии слотов цифры — это возраст/количество, а не номер варианта
            if has(pat) and not has(r"\d+\s*(лет|год|ноч|дн|час|тыс|к\b|взросл|дет|чел)") and len(t) < 60:
                out["_choice_idx"] = idx
                break
        if ("_choice_idx" in out or "_choice_code" in out) and "_intent" not in out:
            out["_intent"] = "choose"
        elif "_choice_idx" in out or "_choice_code" in out:
            out["_intent"] = "choose_book" if out.get("_intent") == "book" else out["_intent"]


def parse(text: str, stage: str = "start", expect: str | None = None) -> dict:
    """Главная функция: текст → словарь обновлений."""
    t = norm(text)
    out: dict = {}
    parse_dates(t, out)
    parse_duration(t, out)
    parse_party(t, out, expect_ages=(expect == "children_ages"))
    parse_budget(t, out)
    parse_places(t, out)
    parse_prefs(t, out)
    parse_intent(t, out, stage)
    season = out.pop("_season", None)
    if season and out.get("destination"):
        # «Байкал зимой» → выбираем лучший для направления месяц внутри сезона
        from .data.destinations import DEST_BY_ID
        good = [m for m in season if m in DEST_BY_ID[out["destination"]].high_months]
        if good and out["month"] not in good:
            out["month"], out["year"] = good[0], _year_for(good[0], 28)
    if out.get("max_flight_h") == 5.5 and out.get("nights", 99) <= 3:
        out["max_flight_h"] = 3.0  # «недалеко на выходные» — строже
    if out.get("_intent") in ("shift_dates", "extend"):
        for k in ("date_from", "date_to", "nights", "month", "year"):
            out.pop(k, None)
    return out


SLOT_KEYS = {"origin", "destination", "country", "avoid", "date_from", "date_to", "month", "year", "nights", "flexible",
             "adults", "children_ages", "children_pending", "pets", "budget", "budget_level", "prefs", "meal", "free_cancel",
             "direct_only", "max_flight_h", "transport", "stars_min", "beach_close", "center_close", "visa_free", "priority",
             "flight_time_pref", "accessible"}


def names_from_text(text: str) -> list[str]:
    """Имена путешественников из свободного текста («Иван Петров, Мария Петрова»)."""
    parts = re.split(r"[,;\n]|\s+и\s+", text)
    names = []
    for p in parts:
        p = p.strip(" .")
        words = [w for w in p.split() if re.match(r"^[A-Za-zА-Яа-яЁё-]{2,}$", w)]
        if 2 <= len(words) <= 3 and all(w[0].isupper() for w in words):
            names.append(" ".join(words))
    return names
