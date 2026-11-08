import hashlib
import hmac
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from instagram_webhook import install_instagram_webhook


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("META_APP_SECRET", "a-test-app-secret")
    monkeypatch.setenv("INSTAGRAM_WEBHOOK_VERIFY_TOKEN", "a-test-verify-token")


@pytest.mark.asyncio
async def test_verification_challenge(env):
    app = web.Application()
    install_instagram_webhook(app)
    async with TestClient(TestServer(app)) as client:
        good = await client.get("/webhook/instagram", params={
            "hub.mode": "subscribe",
            "hub.verify_token": "a-test-verify-token",
            "hub.challenge": "12345",
        })
        assert good.status == 200
        assert await good.text() == "12345"
        bad = await client.get("/webhook/instagram", params={
            "hub.mode": "subscribe", "hub.verify_token": "incorrect", "hub.challenge": "12345",
        })
        assert bad.status == 403


@pytest.mark.asyncio
async def test_webhook_signature_and_message_parsing(env):
    received = []

    async def callback(sender, recipient, message, mid):
        received.append((sender, recipient, message, mid))

    app = web.Application()
    install_instagram_webhook(app, callback)
    data = {
        "object": "instagram",
        "entry": [{"messaging": [
            {"sender": {"id": "123"}, "recipient": {"id": "456"},
             "message": {"mid": "mid.1", "text": "Привіт"}},
            {"sender": {"id": "456"}, "recipient": {"id": "123"},
             "message": {"mid": "mid.2", "text": "echo", "is_echo": True}}
        ]}]
    }
    body = json.dumps(data).encode()
    signature = "sha256=" + hmac.new(b"a-test-app-secret", body, hashlib.sha256).hexdigest()
    async with TestClient(TestServer(app)) as client:
        bad = await client.post("/webhook/instagram", data=body,
                                headers={"X-Hub-Signature-256": "sha256=wrong"})
        assert bad.status == 401
        assert received == []
        good = await client.post("/webhook/instagram", data=body,
                                 headers={"X-Hub-Signature-256": signature})
        assert good.status == 200
        assert received == [("123", "456", "Привіт", "mid.1")]


@pytest.mark.asyncio
async def test_no_auto_reply_by_default(monkeypatch):
    monkeypatch.delenv("INSTAGRAM_AUTO_REPLY_ENABLED", raising=False)
    from instagram_messaging import create_instagram_message_handler
    class Client:
        pass
    callback = create_instagram_message_handler(Client(), "dummy", lambda _: {})
    await callback("123", "456", "Hello", "mid.3")
    # The scheduled task is disabled by default and creates no outgoing request.
    import asyncio
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_instagram_send_uses_instagram_graph_and_account_id(monkeypatch):
    import instagram_messaging
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("META_GRAPH_VERSION", "v25.0")
    recorded = {}

    class FakeResponse:
        status = 200
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None
        async def json(self, content_type=None):
            return {"message_id": "success"}

    class FakeSession:
        def __init__(self, timeout):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None
        def post(self, url, json, headers):
            recorded.update(url=url, payload=json, headers=headers)
            return FakeResponse()

    monkeypatch.setattr(instagram_messaging.aiohttp, "ClientSession", FakeSession)
    answer = await instagram_messaging.send_instagram_text("123456", "998877", "Привіт")
    assert answer == {"message_id": "success"}
    assert recorded["url"] == "https://graph.facebook.com/v25.0/123456/messages"
    assert recorded["payload"] == {"recipient": {"id": "998877"}, "message": {"text": "Привіт"}}
    assert recorded["headers"]["Authorization"] == "Bearer test-token"


@pytest.mark.asyncio
async def test_instagram_send_without_token_is_blocked(monkeypatch):
    import instagram_messaging
    monkeypatch.delenv("INSTAGRAM_ACCESS_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="Instagram Unified Login token"):
        await instagram_messaging.send_instagram_text("123456", "998877", "Hello")


@pytest.mark.asyncio
async def test_one_phrase_reply_without_general_auto_replies(monkeypatch):
    import asyncio
    import instagram_messaging
    monkeypatch.setenv("INSTAGRAM_REPLY_TEST_PHRASE", "Тест Міссі 004")
    monkeypatch.setenv("INSTAGRAM_AUTO_REPLY_ENABLED", "false")
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "test-token")
    calls = []

    async def fake_send(account_id, sender_id, text):
        calls.append((account_id, sender_id, text))

    monkeypatch.setattr(instagram_messaging, "send_instagram_text", fake_send)
    handler = instagram_messaging.create_instagram_message_handler(object(), "unused", lambda _: {})
    await handler("sender", "account", "Звичайне повідомлення", "midnormal")
    await asyncio.sleep(0.03)
    assert calls == []
    await handler("sender", "account", "Тест Міссі 004", "midtest")
    await asyncio.sleep(0.03)
    assert len(calls) == 1
    assert calls[0][:2] == ("account", "sender")
    await handler("sender", "account", "Тест Міссі 004", "midtest")
    await asyncio.sleep(0.03)
    assert len(calls) == 1

@pytest.mark.asyncio
async def test_ai_test_prefix_isolated_and_conversational(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    import instagram_messaging

    monkeypatch.setenv("INSTAGRAM_AI_TEST_PREFIX", "Nastya test:")
    monkeypatch.setenv("INSTAGRAM_AUTO_REPLY_ENABLED", "false")
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "unit-token")
    monkeypatch.delenv("INSTAGRAM_REPLY_TEST_PHRASE", raising=False)
    sent = []
    prompts = []

    async def fake_send(ig_id, sender_id, text):
        sent.append((ig_id, sender_id, text))

    def fake_completion(**kwargs):
        prompts.append(kwargs["messages"])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Hey! Nice to meet you 😊"))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fake_completion)))
    monkeypatch.setattr(instagram_messaging, "send_instagram_text", fake_send)
    handler = instagram_messaging.create_instagram_message_handler(client, "test-model", lambda _: {"max_completion_tokens": 100})

    await handler("user1", "123456", "Hi, who are you?", "mid1")
    await asyncio.sleep(0.04)
    assert sent == []

    await handler("user1", "123456", "Nastya test: Hi, who are you?", "mid2")
    await asyncio.sleep(0.04)
    assert len(sent) == 1
    assert sent[0][0:2] == ("123456", "user1")
    assert prompts[0][-1] == {"role": "user", "content": "Hi, who are you?"}
    system = prompts[0][0]["content"]
    assert "language of the user's latest message" in system
    assert "playful joke" in system
    assert "without a formal disclaimer" in system

    await handler("user2", "123456", "Nastya test: Привіт!", "mid3")
    await asyncio.sleep(0.04)
    assert prompts[1][-1]["content"] == "Привіт!"
    assert len(prompts[1]) == 2

    await handler("user1", "123456", "Nastya test: What else?", "mid4")
    await asyncio.sleep(0.04)
    assert prompts[2][-3]["content"] == "Hi, who are you?"
    assert prompts[2][-2]["content"] == "Hey! Nice to meet you 😊"
    assert prompts[2][-1]["content"] == "What else?"

    await handler("user1", "123456", "Nastya test: What else?", "mid4")
    await asyncio.sleep(0.04)
    assert len(sent) == 3


@pytest.mark.asyncio
async def test_handoff_blocks_even_explicit_test_replies(monkeypatch):
    import asyncio
    import copy
    import instagram_messaging
    from instagram_handoff import register_dialog, set_dialog_mode, allow_automatic_reply, list_dialogs

    state = {}
    async def read():
        return copy.deepcopy(state)
    async def write(callback):
        callback(state)
        return copy.deepcopy(state)
    await register_dialog(write, read, "10101")
    assert await allow_automatic_reply(read, "10101")
    assert not await set_dialog_mode(write, read, "unknown", "human")
    assert await set_dialog_mode(write, read, "10101", "human")
    assert not await allow_automatic_reply(read, "10101")
    assert (await list_dialogs(read))[0][1]["mode"] == "human"

    monkeypatch.setenv("INSTAGRAM_REPLY_TEST_PHRASE", "Тест Міссі 004")
    monkeypatch.setenv("INSTAGRAM_AI_TEST_PREFIX", "Nastya test:")
    monkeypatch.setenv("INSTAGRAM_AUTO_REPLY_ENABLED", "false")
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "fake-test-token")
    called = []
    async def send(*args):
        called.append(args)
    monkeypatch.setattr(instagram_messaging, "send_instagram_text", send)
    handler = instagram_messaging.create_instagram_message_handler(
        object(), "unused", lambda _: {}, read, write
    )
    await handler("10101", "20202", "Тест Міссі 004", "id1")
    await handler("10101", "20202", "Nastya test: Hello", "id2")
    await asyncio.sleep(0.05)
    assert called == []
    assert await set_dialog_mode(write, read, "10101", "auto")
    await handler("10101", "20202", "Тест Міссі 004", "id3")
    await asyncio.sleep(0.05)
    assert len(called) == 1
