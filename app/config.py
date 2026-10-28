"""Конфигурация из переменных окружения и файла .env (в коде значений нет)."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LLM_VARS = ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL")


def parse_env(text: str) -> dict[str, str]:
    """KEY=VALUE по строке; пустые строки и # — комментарии. При повторе побеждает последняя строка."""
    values: dict[str, str] = {}
    for line in text.lstrip("﻿").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip():
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Читаем .env при запуске без Docker. Уже заданные переменные окружения важнее файла."""
    if not path.is_file():
        return
    for key, value in parse_env(path.read_text(encoding="utf-8-sig")).items():
        if key not in os.environ:
            os.environ[key] = value


def env(name: str, default: str = "") -> str:
    """Значение переменной; пустая строка (KEY= в .env) считается незаданной."""
    return os.getenv(name) or default
