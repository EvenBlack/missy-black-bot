"""The startup probe reads only the selected failed draft and never posts."""
import copy

import pytest

from instagram_photo_probe import check_failed_photo_once


@pytest.mark.asyncio
async def test_disabled_does_not_read_state(monkeypatch):
    monkeypatch.delenv("INSTAGRAM_PHOTO_CHECK_DRAFT_ID", raising=False)
    async def forbidden():
        raise AssertionError("must not read state")
    assert await check_failed_photo_once(forbidden) == {"status": "disabled"}


@pytest.mark.asyncio
async def test_selected_failed_draft_probe_is_read_only(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_PHOTO_CHECK_DRAFT_ID", "b56051b8")
    state = {"instagram_publish_queue": {
        "b56051b8": {"status": "publishing", "photo_archive_id": "a" * 24,
                     "photo_owner_id": "1234", "image_url": "https://example.org/image"},
        "11111111": {"status": "approved", "image_url": "https://example.org/other"}
    }}
    original = copy.deepcopy(state)
    urls = []
    async def fake_sleep(_seconds):
        return None
    monkeypatch.setattr("instagram_photo_probe.asyncio.sleep", fake_sleep)
    async def read():
        return state
    async def check(url):
        urls.append(url)
        return {"ok": False, "http": 503, "reason": "http"}
    outcome = await check_failed_photo_once(read, check)
    assert outcome == {"ok": False, "http": 503, "reason": "http"}
    assert urls == ["https://example.org/image"] * 2
    assert state == original


@pytest.mark.asyncio
async def test_unapproved_draft_is_not_probed(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_PHOTO_CHECK_DRAFT_ID", "b56051b8")
    state = {"instagram_publish_queue": {
        "b56051b8": {"status": "draft", "photo_archive_id": "a" * 24,
                     "photo_owner_id": "1234", "image_url": "https://example.org/image"}
    }}
    async def read():
        return state
    async def forbidden(_url):
        raise AssertionError("wrong draft status")
    result = await check_failed_photo_once(read, forbidden)
    assert result == {"status": "not_eligible"}


@pytest.mark.asyncio
async def test_transient_gateway_error_is_rechecked_after_handover(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_PHOTO_CHECK_DRAFT_ID", "b56051b8")
    state = {"instagram_publish_queue": {
        "b56051b8": {"status": "publishing", "photo_archive_id": "a" * 24,
                     "photo_owner_id": "1234", "image_url": "https://example.org/image"}
    }}
    delays = []
    async def no_wait(seconds):
        delays.append(seconds)
    monkeypatch.setattr("instagram_photo_probe.asyncio.sleep", no_wait)
    async def read():
        return state
    calls = []
    async def check(_url):
        calls.append(1)
        return {"ok": len(calls) == 2, "http": 502 if len(calls) == 1 else 200}
    result = await check_failed_photo_once(read, check)
    assert result["ok"] is True
    assert len(calls) == 2
    assert delays == [20]
