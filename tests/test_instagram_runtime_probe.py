"""Opt-in Meta diagnostic is read-only and hides credentials."""
import pytest
from instagram_runtime_probe import run_meta_readonly_probe


@pytest.mark.asyncio
async def test_probe_off_by_default(monkeypatch):
    monkeypatch.delenv("INSTAGRAM_READONLY_PROBE_ON_START", raising=False)
    async def forbidden(*args):
        raise AssertionError("unexpected network")
    assert await run_meta_readonly_probe(forbidden, forbidden, forbidden) == {"status": "disabled"}


@pytest.mark.asyncio
async def test_probe_only_reads_and_reports_classifications(monkeypatch, caplog):
    monkeypatch.setenv("INSTAGRAM_READONLY_PROBE_ON_START", "true")
    calls = []
    async def reader():
        calls.append("read")
    async def account_probe(read_memory):
        assert read_memory is reader
        calls.append("account")
        return {"status": "ok", "profile_full": "permission_denied",
                "profile_basic": "ok", "media_list": "permission_denied",
                "instagram_host": "api_rejected"}
    async def permission_probe():
        calls.append("permissions")
        return {"status": "check_unavailable", "http": 400, "code": 200}
    with caplog.at_level("WARNING", logger="instagram_runtime_probe"):
        result = await run_meta_readonly_probe(reader, account_probe, permission_probe)
    assert result["status"] == "complete"
    assert calls == ["account", "permissions"]
    assert "code=200" in caplog.text
    assert "profile=permission_denied" in caplog.text
