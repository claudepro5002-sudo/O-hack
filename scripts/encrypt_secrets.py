"""Шифрование .env для жюри: проверяет файлы и создаёт secrets.env.age одной командой.

    python scripts/encrypt_secrets.py          # проверить .env и зашифровать его в secrets.env.age
    python scripts/encrypt_secrets.py --check  # только проверка перед сдачей, ничего не шифрует

Расшифровать secrets.env.age может только организатор (закрытый ключ у него). Значения ключей
скрипт никогда не печатает. Нужна утилита age: https://github.com/FiloSottile/age
"""
from __future__ import annotations

import argparse
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import LLM_VARS, parse_env  # noqa: E402

RECIPIENT = "age1932fu7f70rwyfhaxflyfszz4gf6avxwc95wc0yjwm6zns9tspycqpgdnc0"  # публичный ключ организаторов
ENV, EXAMPLE, OUT, GITIGNORE, README = (ROOT / n for n in (".env", ".env.example", "secrets.env.age", ".gitignore",
                                                           "README.md"))
AGE_HEADER = b"age-encryption.org/v1\n"
INSTALL = {"Windows": "winget install FiloSottile.age   (или scoop install age), затем откройте новое окно терминала",
           "Darwin": "brew install age", "Linux": "sudo apt install age"}

errors: list[str] = []
warnings: list[str] = []


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def fix_encoding() -> None:
    """UTF-8 без BOM и переводы строк LF: так .env одинаково читают Docker, Python и age."""
    raw = ENV.read_bytes()
    clean = raw.removeprefix(b"\xef\xbb\xbf").replace(b"\r\n", b"\n")
    try:
        clean.decode("utf-8")
    except UnicodeDecodeError:
        errors.append(".env не в кодировке UTF-8 — пересохраните его в UTF-8 (VS Code: внизу справа кодировка)")
        return
    if clean != raw:
        ENV.write_bytes(clean)
        ok(".env: убрал BOM / переводы строк Windows (UTF-8 без BOM)")


def check_env() -> dict[str, str]:
    if not ENV.is_file():
        errors.append("нет файла .env — скопируйте .env.example в .env и впишите значения")
        return {}
    fix_encoding()
    text = ENV.read_text(encoding="utf-8", errors="replace")
    for n, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "=" not in s:
            errors.append(f".env, строка {n}: нужна запись вида ИМЯ=значение")
            continue
        key, value = line.split("=", 1)
        if key != key.strip() or value != value.strip():
            errors.append(f".env, строка {n} ({key.strip()}): уберите пробелы вокруг «=» и в конце строки")
        if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key.strip()):
            errors.append(f".env, строка {n}: имя «{key.strip()}» — только латиница, цифры и _, заглавными")
        v = value.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            errors.append(f".env, строка {n} ({key.strip()}): уберите кавычки вокруг значения")
    values = parse_env(text)
    missing = [k for k in LLM_VARS if not values.get(k)]
    if missing:
        warnings.append(f"в .env не заполнены {', '.join(missing)} — у жюри агент запустится без LLM")
    else:
        ok(f"LLM: {values['LLM_BASE_URL']}, модель {values['LLM_MODEL']} (ключ …{values['LLM_API_KEY'][-4:]})")
    if EXAMPLE.is_file():
        unlisted = sorted(set(values) - set(parse_env(EXAMPLE.read_text(encoding="utf-8-sig"))))
        if unlisted:
            warnings.append(f"этих переменных нет в .env.example — допишите их туда без значений: {', '.join(unlisted)}")
    return values


def check_example() -> None:
    if not EXAMPLE.is_file():
        errors.append("нет .env.example")
        return
    filled = [k for k, v in parse_env(EXAMPLE.read_text(encoding="utf-8-sig")).items() if v]
    if filled:
        errors.append(f".env.example должен быть без значений, а заполнены: {', '.join(filled)}")
    else:
        ok(".env.example: все переменные без значений")


def git(*args: str) -> str | None:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None


def check_git() -> None:
    lines = GITIGNORE.read_text(encoding="utf-8").splitlines() if GITIGNORE.is_file() else []
    if ".env" in (s.strip() for s in lines):
        ok(".gitignore содержит .env")
    else:
        errors.append("добавьте в .gitignore строку .env")
    tracked = git("ls-files", ".env")
    if tracked is None:
        warnings.append("git не найден — проверьте сами, что `git ls-files .env` ничего не выводит")
    elif tracked.strip():
        errors.append(".env отслеживается git! Выполните `git rm --cached .env`, отзовите и перевыпустите все ключи "
                      "из него (старые значения остались в истории) и зашифруйте новый .env")
    else:
        ok("открытый .env не в git (git ls-files .env — пусто)")
        if git("log", "--all", "--oneline", "--", ".env"):
            errors.append(".env уже попадал в коммиты — считайте ключи из него скомпрометированными: отзовите и "
                          "перевыпустите их, затем зашифруйте новый .env")


def check_readme(values: dict[str, str]) -> None:
    text = README.read_text(encoding="utf-8") if README.is_file() else ""
    models = [m.strip() for m in values.get("LLM_MODEL", "").split(",") if m.strip()]
    absent = [m for m in models if m not in text]
    if absent:
        warnings.append(f"укажите в README модель: {', '.join(absent)} (раздел «Ключи и секреты»)")
    elif models:
        ok("README: модель указана")


def check_output(values: dict[str, str]) -> None:
    if not OUT.is_file() or OUT.stat().st_size <= len(AGE_HEADER):
        errors.append("нет secrets.env.age (или он пустой) — запустите скрипт без --check")
        return
    data = OUT.read_bytes()
    if not data.startswith(AGE_HEADER):
        errors.append("secrets.env.age испорчен: это не бинарный файл age. Создайте его заново этим скриптом")
        return
    if any(len(v) >= 6 and v.encode() in data for v in values.values()):
        errors.append("в secrets.env.age читаются открытые значения — создайте файл заново")
        return
    ok(f"secrets.env.age: {OUT.stat().st_size} байт, зашифрован (значения не читаются)")
    if ENV.is_file() and ENV.stat().st_mtime > OUT.stat().st_mtime + 1:
        errors.append(".env изменён после шифрования — зашифруйте заново (запустите скрипт без --check)")
    if git("ls-files", OUT.name) == "":
        warnings.append("secrets.env.age ещё не закоммичен")


def encrypt() -> None:
    # в Windows сначала ищем именно age.exe: в PATH от winget бывает одноимённая папка «age»
    names = ("age.exe", "age") if platform.system() == "Windows" else ("age",)
    age = next((p for p in map(shutil.which, names) if p and Path(p).is_file()), None)
    if not age:
        errors.append(f"не найдена утилита age. Установите: {INSTALL.get(platform.system(), INSTALL['Linux'])}")
        return
    # именно -o, без > и | — в PowerShell перенаправление портит бинарный файл
    try:
        r = subprocess.run([age, "-r", RECIPIENT, "-o", str(OUT), str(ENV)], cwd=ROOT, capture_output=True, text=True)
    except OSError as e:
        errors.append(f"не удалось запустить {age}: {e}. Зашифруйте вручную: age.exe -r {RECIPIENT} -o secrets.env.age .env"
                      " — и запустите скрипт с --check")
        return
    if r.returncode != 0:
        errors.append(f"age завершился с ошибкой: {r.stderr.strip()}")
    else:
        ok("зашифровал .env → secrets.env.age")


def main() -> None:
    ap = argparse.ArgumentParser(description="Шифрование .env в secrets.env.age для жюри")
    ap.add_argument("--check", action="store_true", help="только проверка, без шифрования")
    args = ap.parse_args()
    print("Проверяю файлы…")
    values = check_env()
    check_example()
    check_git()
    check_readme(values)
    if not args.check and not errors:
        encrypt()
    if not errors:
        check_output(values)
    for w in warnings:
        print(f"  ! {w}")
    for e in errors:
        print(f"  ✕ {e}")
    if errors:
        sys.exit(1)
    print("\nГотово. Осталось закоммитить и отправить:")
    print("  git add secrets.env.age .env.example .gitignore")
    print('  git commit -m "Add encrypted secrets"')
    print("  git push")


if __name__ == "__main__":
    main()
