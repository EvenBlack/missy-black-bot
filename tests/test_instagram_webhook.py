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
