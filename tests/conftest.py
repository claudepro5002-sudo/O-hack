import os
import sys

os.environ["APP_TODAY"] = "2026-10-24"
os.environ["DB_PATH"] = ":memory:"
for _k in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
    os.environ.pop(_k, None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def llm_env(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test")
    monkeypatch.setenv("LLM_BASE_URL", "http://llm.test/v1")
    monkeypatch.setenv("LLM_MODEL", "test-model")
