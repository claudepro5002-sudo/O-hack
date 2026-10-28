from fastapi.testclient import TestClient

from app import main, store


def client():
    store.reset_for_tests()
    main._sessions.clear()
    return TestClient(main.app)


def test_chats_are_separate_sessions_with_their_own_feed():
    c = client()
    a = c.post("/api/session", json={}).json()
    uid = a["user_id"]
    c.post("/api/chat", json={"session_id": a["session_id"], "user_id": uid,
                              "message": "Стамбул в декабре вдвоём на 4 ночи из Москвы"})
    b = c.post("/api/session", json={"user_id": uid}).json()
    # пустой новый чат виден только как текущий
    assert [x["id"] for x in c.get(f"/api/chats?user_id={uid}").json()] == [a["session_id"]]
    c.post("/api/chat", json={"session_id": b["session_id"], "user_id": uid, "message": "Хочу в Сочи в июле вдвоём"})
    chats = c.get(f"/api/chats?user_id={uid}").json()
    assert [x["id"] for x in chats] == [b["session_id"], a["session_id"]]  # свежий сверху
    assert chats[1]["title"] == "Стамбул" and chats[1]["stage"]

    fa = c.get(f"/api/session/{a['session_id']}/feed?user_id={uid}").json()
    assert [x["role"] for x in fa["feed"]] == ["agent", "user", "agent"]
    assert fa["feed"][1]["text"].startswith("Стамбул")
    assert any(x["key"] == "destination" and x["value"] == "Стамбул" for x in fa["summary"]["slots"])


def test_silent_tweak_replaces_card_and_label_is_shown():
    c = client()
    s = c.post("/api/session", json={}).json()
    sid, uid = s["session_id"], s["user_id"]
    c.post("/api/chat", json={"session_id": sid, "user_id": uid, "message": "Стамбул в декабре вдвоём на 4 ночи из Москвы"})
    before = c.get(f"/api/session/{sid}/feed?user_id={uid}").json()["feed"]
    c.post("/api/chat", json={"session_id": sid, "user_id": uid,
                              "action": {"type": "set", "key": "priority", "value": "price", "silent": True}})
    after = c.get(f"/api/session/{sid}/feed?user_id={uid}").json()["feed"]
    assert len(after) == len(before)  # тихая настройка не добавляет сообщений
    c.post("/api/chat", json={"session_id": sid, "user_id": uid, "action": {"type": "reset"}, "label": "Новая поездка"})
    assert c.get(f"/api/session/{sid}/feed?user_id={uid}").json()["feed"][-2] == {"role": "user", "text": "Новая поездка"}


def test_delete_chat_only_by_owner():
    c = client()
    s = c.post("/api/session", json={}).json()
    sid, uid = s["session_id"], s["user_id"]
    assert c.delete(f"/api/session/{sid}?user_id=someone-else").status_code == 404
    assert c.delete(f"/api/session/{sid}?user_id={uid}").status_code == 200
    assert c.get(f"/api/session/{sid}/feed?user_id={uid}").status_code == 404
