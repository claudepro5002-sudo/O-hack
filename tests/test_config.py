import re
from pathlib import Path

from app.config import env, parse_env

ROOT = Path(__file__).resolve().parent.parent


def test_env_example_lists_every_variable_without_values():
    example = parse_env((ROOT / ".env.example").read_text(encoding="utf-8"))
    assert not {k: v for k, v in example.items() if v}, "в .env.example не должно быть значений"
    used = set()
    for p in [*ROOT.glob("app/**/*.py"), ROOT / "run.py"]:
        used |= set(re.findall(r'(?:getenv|env)\("([A-Z_]+)"', p.read_text(encoding="utf-8")))
    assert used - set(example) == set(), "переменные из кода должны быть перечислены в .env.example"


def test_gitignore_has_env():
    assert ".env" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()


def test_parse_env_and_empty_values(monkeypatch):
    assert parse_env("﻿# c\nA=1\nB=\n C = 'x' \nA=2\n") == {"A": "2", "B": "", "C": "x"}
    monkeypatch.setenv("LLM_TIMEOUT", "")
    assert env("LLM_TIMEOUT", "30") == "30"
