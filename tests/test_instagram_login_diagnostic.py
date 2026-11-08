"""No secrets or media published by read-only login route diagnostic."""
from instagram_login_diagnostic import render_login_path, summarize_probe
from instagram_menu import menu_keyboard


def test_read_only_route_classification():
    assert summarize_probe(200, {"id": "123"}, "123") == "recognized_account"
    assert summarize_probe(200, {"id": "321"}, "123") == "valid_other_identity"
    assert summarize_probe(400, {"error": {"code": 200}}) == "permission_denied"
    assert summarize_probe(400, {"error": {"code": 190}}) == "token_rejected"
    assert summarize_probe(400, {"error": {"code": 10}}) == "api_rejected"
    assert summarize_probe(200, {}) == "unrecognized_response"


def test_report_contains_only_status_not_raw_data():
    result = render_login_path({
        "status": "ok",
        "facebook": "valid_other_identity",
        "instagram": "recognized_account",
    })
    assert "Facebook Graph" in result and "Instagram Graph" in result
    assert "Жодних дописів" in result
    assert "123" not in result


def test_owner_menu_links_to_read_only_diagnostic():
    actions = [button.callback_data for row in menu_keyboard().inline_keyboard for button in row]
    assert "igmenu:loginpath" in actions


import pytest
import instagram_login_diagnostic as diag


@pytest.mark.asyncio
async def test_standard_meta_graph_version_does_not_abort_before_probe(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "test-secret-not-a-real-token")
    monkeypatch.setenv("META_GRAPH_VERSION", "v27.0")
    monkeypatch.setenv("INSTAGRAM_ACCOUNT_ID", "123")
    seen = []
    async def fake_probe(host, token, version, expected_id, session):
        seen.append((host, version))
        return "recognized_account"
    class FakeSession:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
    monkeypatch.setattr(diag, "probe_token_host", fake_probe)
    monkeypatch.setattr(diag.aiohttp, "ClientSession", FakeSession)
    result = await diag.check_login_path()
    assert result["status"] == "ok"
    assert len(seen) == 2
    assert all(version == "v27.0" for _, version in seen)
