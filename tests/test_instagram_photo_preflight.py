"""Read-only diagnostics never publish or expose private archive URLs."""
import asyncio
import pytest
import instagram_growth


class FakeResponse:
    def __init__(self, status=200, mime="image/jpeg", data=None):
        self.status = status
        self.headers = {"Content-Type": mime}
        self.content = self
        self.data = data if data is not None else bytes([255, 216, 255, 224]) + b"hello"
    async def read(self):
        return self.data
    async def __aenter__(self):
        return self
    async def __aexit__(self, *_args):
        return False


class FakeSession:
    def __init__(self, response, requests):
        self.response, self.requests = response, requests
    async def __aenter__(self):
        return self
    async def __aexit__(self, *_args):
        return False
    def get(self, url, allow_redirects=False):
        self.requests.append((url, allow_redirects))
        return self.response


URL = "https://missy-black-bot.onrender.com/ig/media/1234abcd/" + "a" * 24 + "?owner=123&expires=1999999999&sig=" + "1" * 64


@pytest.mark.asyncio
async def test_read_only_photo_preflight_accepts_actual_jpeg(monkeypatch):
    requests = []
    monkeypatch.setattr(instagram_growth.aiohttp, "ClientSession",
                        lambda **kw: FakeSession(FakeResponse(), requests))
    result = await instagram_growth.check_archived_photo_delivery(URL)
    assert result["ok"] is True
    assert result["http"] == 200
    assert requests == [(URL, False)]


@pytest.mark.asyncio
@pytest.mark.parametrize("status,mime,data,reason", [
    (503, "image/jpeg", b"temporary", "http"),
    (200, "text/html", b"<html>", "content_type"),
    (200, "image/jpeg", b"<html>", "jpeg"),
])
async def test_read_only_photo_preflight_rejects_invalid_delivery(
        monkeypatch, status, mime, data, reason):
    requests = []
    monkeypatch.setattr(instagram_growth.aiohttp, "ClientSession",
                        lambda **kw: FakeSession(FakeResponse(status, mime, data), requests))
    result = await instagram_growth.check_archived_photo_delivery(URL)
    assert result["ok"] is False
    assert result["reason"] == reason
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_preflight_blocks_other_hosts_without_request(monkeypatch):
    monkeypatch.setattr(instagram_growth.aiohttp, "ClientSession",
                        lambda **kw: pytest.fail("untrusted URL was fetched"))
    with pytest.raises(ValueError):
        await instagram_growth.check_archived_photo_delivery(
            "https://evil.test/ig/media/1234abcd/" + "a" * 24
        )
