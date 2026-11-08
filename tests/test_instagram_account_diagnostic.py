"""No-network tests for account-specific Instagram read-only diagnostics."""
import pytest
import instagram_account_diagnostic as diag
from instagram_menu import menu_keyboard


def test_sanitized_api_results():
    assert diag.classify_reply(200, {"username":"private", "followers_count":7, "media_count":4},("username","followers_count","media_count")) == "ok"
    assert diag.classify_reply(200, {"username":"private"}, ("username","media_count")) == "fields_missing"
    assert diag.classify_reply(400, {"error":{"code":190, "message":"secret"}}, ("username",)) == "token_rejected"
    assert diag.classify_reply(400, {"error":{"code":200}}, ("username",)) == "permission_denied"
    assert diag.classify_reply(400, {"error":{"code":100}}, ("username",)) == "invalid_request"


@pytest.mark.asyncio
async def test_account_probe_uses_known_id_and_get_only(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "private-test-token")
    monkeypatch.setenv("META_GRAPH_VERSION", "v27.0")
    monkeypatch.setenv("INSTAGRAM_ACCOUNT_ID", "555")
    urls = []
    class Response:
        status = 200
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def json(self, **kwargs): return {"username":"private", "followers_count":7, "media_count":4, "data":[]}
    class Session:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        def get(self,url,**kwargs):
            urls.append((url, kwargs.get("params")))
            return Response()
        def post(self,*args,**kwargs):
            raise AssertionError("Publishing never allowed in read-only diagnostic")
    monkeypatch.setattr(diag.aiohttp,"ClientSession",Session)
    async def read():
        return {"instagram_account_id":"123"}
    result = await diag.check_account_read_path(read)
    assert result["status"]=="ok"
    assert len(urls)==4
    assert all("/123" in url for url,_ in urls)
    assert any("/123/media" in url for url,_ in urls)
    assert all("/555" not in url for url,_ in urls)
    rendered=diag.render_account_read_path(result)
    assert "private" not in rendered and "123" not in rendered
    assert "Нічого не публікували" in rendered
    assert "igmenu:accountread" in [
        b.callback_data for row in menu_keyboard().inline_keyboard for b in row
    ]


@pytest.mark.asyncio
async def test_missing_account_does_not_call_network(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "test-token")
    monkeypatch.delenv("INSTAGRAM_ACCOUNT_ID", raising=False)
    async def read(): return {}
    assert (await diag.check_account_read_path(read))["status"] == "account_missing"
