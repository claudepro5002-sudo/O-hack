"""Запуск трип-агента с вводом ключа LLM в терминале.

    python run.py            # спросит ключ, адрес API и модель, затем запустит http://localhost:8080
    python run.py --visible  # ключ вводится видимым текстом (если вставка в скрытое поле не работает)
    python run.py --port 9000

Ключ вводится скрыто и нигде не сохраняется: он живёт только в памяти запущенного
процесса. Нажмите Enter на вопросе о ключе — агент запустится без LLM (на встроенных правилах).
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

import httpx
import uvicorn

from app import llm


def ask(prompt: str, current: str | None = None) -> str:
    hint = f" [Enter — оставить {current}]" if current else ""
    try:
        value = input(f"{prompt}{hint}: ").strip()
    except EOFError:
        value = ""
    return value or (current or "")


def read_key(visible: bool) -> str:
    env_key = os.getenv("LLM_API_KEY", "").strip()
    if env_key:
        answer = input(f"Найден ключ в переменной окружения LLM_API_KEY (…{env_key[-4:]}). Использовать его? [Y/n]: ")
        if answer.strip().lower() not in ("n", "н", "no", "нет"):
            return env_key
    if visible:
        return input("Ключ API (Enter — запуск без LLM): ").strip()
    print("Вставьте ключ и нажмите Enter. Символы не отображаются — так и задумано.")
    print("Не вставляется? Перезапустите так: python run.py --visible")
    try:
        return getpass.getpass("Ключ API (Enter — запуск без LLM): ").strip()
    except (EOFError, KeyboardInterrupt):
        raise
    except Exception:  # noqa: BLE001 — терминал без поддержки скрытого ввода
        return input("Скрытый ввод недоступен. Ключ API: ").strip()


def check(base: str, key: str, model_list: list[str]) -> tuple[str | None, list[str]]:
    """Пробный запрос к каждой модели по очереди. → (первая рабочая модель или None, ошибки)."""
    hints = {401: "ключ не подходит", 402: "на балансе нет средств", 403: "доступ запрещён",
             404: "неверный адрес API или имя модели", 429: "лимит запросов (часто временный у бесплатных моделей)"}
    errors = []
    try:
        with httpx.Client(timeout=40) as c:
            for model in model_list:
                r = llm.request(model, [{"role": "user", "content": "Ответь словом: ок"}], 5, False, c)
                if r.status_code == 200:
                    return model, errors
                try:
                    detail = r.json().get("error", {}).get("message", "")
                except ValueError:
                    detail = r.text[:200]
                errors.append(f"{model} — HTTP {r.status_code}: {hints.get(r.status_code, 'ошибка сервиса')}. {detail}".strip())
                if r.status_code == 401:
                    break
    except httpx.HTTPError as e:
        errors.append(f"не удалось подключиться: {e}")
    return None, errors


def main() -> None:
    ap = argparse.ArgumentParser(description="Запуск трип-агента")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.getenv("APP_PORT", "8080")))
    ap.add_argument("--no-check", action="store_true", help="не проверять подключение к LLM перед запуском")
    ap.add_argument("--visible", action="store_true",
                    help="вводить ключ видимым текстом (если в вашем терминале не работает вставка в скрытое поле)")
    args = ap.parse_args()

    print("=== Трип-агент ===")
    print("Подключение LLM (любой OpenAI-совместимый API: OpenRouter, DeepSeek, Ollama и т. п.).")
    key = read_key(args.visible)
    if key:
        print(f"Ключ получен: {len(key)} символов, оканчивается на …{key[-4:]}")
    if key:
        base = ask("Адрес API, например https://openrouter.ai/api/v1", os.getenv("LLM_BASE_URL"))
        model = ask("Модель (можно несколько через запятую — запасные на случай лимитов)", os.getenv("LLM_MODEL"))
        if not base or not model:
            print("Не указан адрес API или модель — запускаю без LLM.")
            key = base = model = ""
        elif not args.no_check:
            os.environ["LLM_API_KEY"], os.environ["LLM_BASE_URL"] = key, base
            print("Проверяю подключение…", flush=True)
            ok, errors = check(base, key, [m.strip() for m in model.split(",") if m.strip()])
            for e in errors:
                print(f"  ✕ {e}")
            if ok:
                print(f"  ✓ работает: {ok}")
                rest = [m.strip() for m in model.split(",") if m.strip() and m.strip() != ok]
                model = ",".join([ok, *rest])  # рабочую модель ставим первой
            elif all("HTTP 429" in e for e in errors):
                print("Все модели сейчас перегружены. Обычно это проходит через минуту-другую.")
                if input("Всё равно запустить с LLM (при ошибке агент сам ответит без неё)? [Y/n]: ").strip().lower() \
                        in ("n", "н", "no", "нет"):
                    key = base = model = ""
            else:
                if input("Запустить без LLM? [Y/n]: ").strip().lower() in ("n", "н", "no", "нет"):
                    sys.exit(1)
                key = base = model = ""
    else:
        base = model = ""
    # задаём явно (в т. ч. пустыми), чтобы значения из .env не подменили выбор в терминале
    os.environ["LLM_API_KEY"], os.environ["LLM_BASE_URL"], os.environ["LLM_MODEL"] = key, base, model
    print(f"Режим: {'LLM — ' + model if key else 'без LLM (встроенные правила)'}")
    print(f"Открывайте http://localhost:{args.port}  (остановить — Ctrl+C)")
    uvicorn.run("app.main:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
