"""Модели состояния диалога и запроса на поездку."""
from __future__ import annotations

import os
import time
import uuid
from datetime import date

from pydantic import BaseModel, Field

PREF_LABELS = {
    "sea": "море и пляж", "warm": "тепло", "culture": "экскурсии и история", "food": "гастрономия",
    "party": "ночная жизнь", "quiet": "тишина и спокойствие", "romantic": "романтика", "nature": "природа",
    "mountains": "горы", "ski": "горные лыжи", "shopping": "шопинг", "wellness": "СПА и оздоровление",
    "diving": "дайвинг и снорклинг", "northern_lights": "северное сияние", "kids_fun": "развлечения для детей",
    "city": "городской отдых", "surf": "сёрфинг", "adventure": "приключения",
    "pool": "бассейн", "kitchen": "кухня в номере", "dry": "без дождей", "mild": "без жары",
}

PRIORITY_LABELS = {"price": "Важнее цена", "balance": "Баланс", "comfort": "Важнее комфорт", "time": "Меньше времени в пути"}


def today() -> date:
    v = os.getenv("APP_TODAY")
    if v:
        try:
            return date.fromisoformat(v)
        except ValueError:
            pass
    return date.today()


class TripRequest(BaseModel):
    origin: str | None = None
    origin_source: str | None = None  # user | memory | assumed
    destination: str | None = None
    country: str | None = None
    avoid: list[str] = Field(default_factory=list)
    similar_to: list[str] = Field(default_factory=list)  # аналоги места, которого нет в каталоге
    similar_label: str | None = None
    candidates: list[str] = Field(default_factory=list)  # конкретные места региона, которые подобрала LLM
    date_from: str | None = None
    date_to: str | None = None
    month: int | None = None
    year: int | None = None
    nights: int | None = None
    nights_source: str | None = None
    flexible: bool = True
    adults: int | None = None
    children_ages: list[int] = Field(default_factory=list)
    children_pending: int = 0  # дети без известного возраста
    pets: str | None = None
    budget: int | None = None
    budget_level: str | None = None  # low | mid | high
    prefs: list[str] = Field(default_factory=list)
    meal: str | None = None
    free_cancel: bool | None = None
    direct_only: bool = False
    max_flight_h: float | None = None
    transport: str = "any"  # any | plane | train
    stars_min: int | None = None
    beach_close: bool = False
    center_close: bool = False
    visa_free: bool = False
    priority: str = "balance"
    flight_time_pref: str | None = None
    accessible: bool = False
    assumptions: dict[str, str] = Field(default_factory=dict)

    @property
    def party_size(self) -> int:
        return (self.adults or 2) + len(self.children_ages)

    @property
    def has_kids(self) -> bool:
        return bool(self.children_ages) or self.children_pending > 0

    @property
    def small_kids(self) -> bool:
        return any(a < 7 for a in self.children_ages)

    def has_when(self) -> bool:
        return bool(self.date_from or self.month)


class SessionState(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    user_id: str = "anon"
    stage: str = "start"  # start | clarify | destinations | bundles | cart | checkout | booked
    req: TripRequest = Field(default_factory=TripRequest)
    asked: list[str] = Field(default_factory=list)
    clarifications: int = 0
    dest_options: list[dict] = Field(default_factory=list)
    excluded: list[dict] = Field(default_factory=list)
    plan: dict | None = None  # результат build_plan
    selected: int | None = None
    pinned: dict = Field(default_factory=dict)  # hotel_id/out_id/back_id/rate/meal, выбранные вручную
    cart: dict | None = None
    booking_ids: list[str] = Field(default_factory=list)
    active_booking: str | None = None
    history: list[dict] = Field(default_factory=list)
    trace: list[dict] = Field(default_factory=list)
    started_at: float = Field(default_factory=time.time)
    first_user_at: float | None = None
    first_reco_at: float | None = None
    turns: int = 0
    replans: int = 0
    memory_applied: list[str] = Field(default_factory=list)
    unknown_place: dict | None = None  # место вне каталога, о котором нужно честно сказать
    awaiting: str | None = None  # что агент ждёт от пользователя (например, travelers_names)
