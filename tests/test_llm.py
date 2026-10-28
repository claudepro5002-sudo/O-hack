import json

from conftest import llm_env

from app import agent, llm, store
from app.state import SessionState


def test_llm_extract_validates_and_merges(monkeypatch):
    llm_env(monkeypatch)
    fake = {"destination": "antalya", "adults": 3, "children_ages": [7], "prefs": ["sea", "unknown_pref"],
            "budget": "много", "_intent": "bogus", "month": 6}
    monkeypatch.setattr(llm, "_chat", lambda *a, **k: "Вот JSON: " + json.dumps(fake))
    data = llm.extract("какой-то текст", "start", "", "2026-10-24")
    assert data == {"destination": "antalya", "adults": 3, "children_ages": [7], "prefs": ["sea"], "month": 6}


def test_agent_survives_llm_failure(monkeypatch):
    store.reset_for_tests()
    llm_env(monkeypatch)
    monkeypatch.setattr(llm, "_chat", lambda *a, **k: None)
    s = SessionState(user_id="t-llm")
    out = agent.handle(s, "Стамбул в декабре вдвоём на 4 ночи")
    assert s.stage == "bundles" and out["blocks"]


def test_llm_overrides_rules(monkeypatch):
    store.reset_for_tests()
    llm_env(monkeypatch)
    # правила не поймут «Кёнигсберг-стайл», а LLM — да
    monkeypatch.setattr(llm, "_chat", lambda *a, **k: json.dumps({"destination": "kaliningrad", "month": 7, "adults": 2}))
    s = SessionState(user_id="t-llm2")
    agent.handle(s, "хочу туда, где янтарь и Куршская коса, летом, вдвоём")
    assert s.req.destination == "kaliningrad" and s.stage == "bundles"


def _capture(status_first=200):
    import httpx
    sent = []

    def handler(req):
        sent.append(json.loads(req.content))
        code = status_first if len(sent) == 1 else 200
        return httpx.Response(code, json={"choices": [{"message": {"content": "<think>хм</think>{\"a\": 1}"}}]})
    return sent, httpx.Client(transport=httpx.MockTransport(handler))


def test_cloud_ru_reasoning_model_thinking_off(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://foundation-models.api.cloud.ru/v1")
    sent, c = _capture()
    llm.request("Qwen/Qwen3.5-397B-A17B", [{"role": "user", "content": "x"}], 100, True, c)
    assert sent[0]["chat_template_kwargs"]["enable_thinking"] is False and "reasoning" not in sent[0]
    sent, c = _capture()
    llm.request("ai-sage/GigaChat3.5-432B-A28B", [{"role": "user", "content": "x"}], 100, True, c)
    assert "chat_template_kwargs" not in sent[0] and sent[0]["max_tokens"] == 100
    sent, c = _capture(status_first=400)  # сервер не принял доп. параметры — повтор без них
    llm.request("zai-org/GLM-4.7", [{"role": "user", "content": "x"}], 100, True, c)
    assert len(sent) == 2 and "chat_template_kwargs" not in sent[1] and "response_format" not in sent[1]


def test_strip_thinking():
    assert llm.strip_thinking("<think>a {b}</think>\n{\"x\": 1}") == '{"x": 1}'
    assert llm.strip_thinking("рассуждаю…</think>{\"x\": 1}") == '{"x": 1}'
