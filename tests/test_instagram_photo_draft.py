"""Tests for private photo draft and guarded media URLs."""
import copy
from datetime import datetime, timezone

import pytest
from instagram_media import signed_media_url
from instagram_growth import add_photo_draft, approve_draft, discard_draft


def test_signing_key_is_required(monkeypatch):
    monkeypatch.delenv("META_TOKEN_ENCRYPTION_KEY", raising=False)
    with pytest.raises(RuntimeError):
        signed_media_url("https://missy-black-bot.onrender.com", "1234abcd", "a"*24, 1234, 1950000000)


@pytest.mark.asyncio
async def test_archived_photo_draft_is_not_automatically_published(monkeypatch):
    monkeypatch.setenv("META_TOKEN_ENCRYPTION_KEY", "1"*64)
    state = {}
    async def read():
        return copy.deepcopy(state)
    async def change(fn):
        fn(state)
    key = await add_photo_draft(change, "a"*24, 1234, "Cozy autumn 🍂 #Fall")
    rec = state["instagram_publish_queue"][key]
    assert rec["status"] == "draft"
    assert rec["photo_archive_id"] == "a"*24
    assert rec["photo_owner_id"] == "1234"
    assert "/ig/media/" in rec["image_url"]
    await approve_draft(change, read, key)
    assert state["instagram_publish_queue"][key]["status"] == "approved"
    with pytest.raises(ValueError):
        await discard_draft(change, key)


@pytest.mark.asyncio
async def test_cancellation_revokes_draft_url(monkeypatch):
    monkeypatch.setenv("META_TOKEN_ENCRYPTION_KEY", "1"*64)
    state = {}
    async def change(fn):
        fn(state)
    key = await add_photo_draft(change, "b"*24, 1234, "Autumn #Cozy")
    await discard_draft(change, key)
    assert state["instagram_publish_queue"][key]["status"] == "cancelled"
    assert "image_url" not in state["instagram_publish_queue"][key]


@pytest.mark.asyncio
async def test_explicit_owner_publish_only_once(monkeypatch):
    from instagram_growth import publish_one_now
    monkeypatch.setenv("META_TOKEN_ENCRYPTION_KEY", "1"*64)
    monkeypatch.setenv("INSTAGRAM_ACCOUNT_ID", "178999888777")
    monkeypatch.setenv("INSTAGRAM_POSTING_ENABLED", "false")
    state = {}
    async def read():
        return copy.deepcopy(state)
    async def change(fn):
        fn(state)
    key = await add_photo_draft(change, "a"*24, 1234, "A happy day #Fall")
    await approve_draft(change, read, key)
    calls = []
    async def fake_publish(url, caption, account):
        calls.append((url, caption, account))
        return "17888"
    async def good_preflight(_url):
        return {"ok": True, "http": 200}
    with pytest.raises(ValueError):
        await publish_one_now(read, change, key, 999, fake_publish, good_preflight)
    assert state["instagram_publish_queue"][key]["status"] == "approved"
    result = await publish_one_now(read, change, key, 1234, fake_publish, good_preflight)
    assert result == "17888"
    assert state["instagram_publish_queue"][key]["status"] == "published"
    assert len(calls) == 1
    with pytest.raises(ValueError):
        await publish_one_now(read, change, key, 1234, fake_publish, good_preflight)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_explicit_publish_failed_meta_does_not_retry(monkeypatch):
    from instagram_growth import publish_one_now
    monkeypatch.setenv("META_TOKEN_ENCRYPTION_KEY", "1"*64)
    monkeypatch.setenv("INSTAGRAM_ACCOUNT_ID", "178999888777")
    state = {}
    async def read():
        return copy.deepcopy(state)
    async def change(fn):
        fn(state)
    key = await add_photo_draft(change, "a"*24, 1234, "Some fall vibes")
    await approve_draft(change, read, key)
    calls = []
    async def fail_publish(url, caption, account):
        calls.append(1)
        raise RuntimeError("simulated Meta failure")
    async def good_preflight(_url):
        return {"ok": True, "http": 200}
    with pytest.raises(RuntimeError, match="повторення заблоковано"):
        await publish_one_now(read, change, key, 1234, fail_publish, good_preflight)
    assert state["instagram_publish_queue"][key]["status"] == "publishing"
    with pytest.raises(ValueError):
        await publish_one_now(read, change, key, 1234, fail_publish, good_preflight)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_quick_duplicate_click_reuses_same_unapproved_photo_draft(monkeypatch):
    monkeypatch.setenv("META_TOKEN_ENCRYPTION_KEY", "1" * 64)
    state = {}
    async def change(fn):
        fn(state)
    first = await add_photo_draft(change, "a" * 24, 1234, "Fashion caption")
    again = await add_photo_draft(change, "a" * 24, 1234, "Fashion caption")
    assert first == again
    assert len(state["instagram_publish_queue"]) == 1
    assert state["instagram_publish_queue"][first]["status"] == "draft"
    different_topic = await add_photo_draft(change, "a" * 24, 1234, "Beauty caption")
    assert different_topic != first
    different_owner = await add_photo_draft(change, "a" * 24, 9999, "Fashion caption")
    assert different_owner not in (first, different_topic)
    different_photo = await add_photo_draft(change, "b" * 24, 1234, "Fashion caption")
    assert different_photo not in (first, different_topic, different_owner)
    assert len(state["instagram_publish_queue"]) == 4


@pytest.mark.asyncio
async def test_approved_and_old_drafts_are_not_reused(monkeypatch):
    from datetime import timedelta
    monkeypatch.setenv("META_TOKEN_ENCRYPTION_KEY", "1" * 64)
    state = {}
    async def read():
        return copy.deepcopy(state)
    async def change(fn):
        fn(state)
    first = await add_photo_draft(change, "a" * 24, 1234, "Same photo")
    await approve_draft(change, read, first)
    second = await add_photo_draft(change, "a" * 24, 1234, "Same photo")
    assert second != first
    state["instagram_publish_queue"][second]["created_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=4)
    ).isoformat()
    third = await add_photo_draft(change, "a" * 24, 1234, "Same photo")
    assert third not in (first, second)


@pytest.mark.asyncio
async def test_draft_photo_id_requires_hex_archive_id(monkeypatch):
    monkeypatch.setenv("META_TOKEN_ENCRYPTION_KEY", "1" * 64)
    async def change(fn):
        raise AssertionError("Should never write an invalid archive ID")
    for bad in ("x" * 24, "a" * 23, "a" * 25, "A" * 24):
        with pytest.raises(ValueError):
            await add_photo_draft(change, bad, 1234, "Valid caption")


@pytest.mark.asyncio
async def test_inaccessible_jpeg_blocks_meta_without_locking_approved_draft(monkeypatch):
    from instagram_growth import publish_one_now
    monkeypatch.setenv("META_TOKEN_ENCRYPTION_KEY", "1" * 64)
    monkeypatch.setenv("INSTAGRAM_ACCOUNT_ID", "178999888777")
    state = {}
    async def read():
        return copy.deepcopy(state)
    async def change(fn):
        fn(state)
    key = await add_photo_draft(change, "a" * 24, 1234, "Night thoughts")
    await approve_draft(change, read, key)
    async def fail_preflight(_url):
        return {"ok": False, "http": 502, "reason": "http"}
    async def forbidden_publish(*_args):
        raise AssertionError("Meta must never be contacted")
    with pytest.raises(RuntimeError, match="погодження збережене"):
        await publish_one_now(read, change, key, 1234, forbidden_publish, fail_preflight)
    assert state["instagram_publish_queue"][key]["status"] == "approved"


@pytest.mark.asyncio
async def test_owner_can_reopen_only_failed_photo_after_confirming_absence(monkeypatch):
    from instagram_growth import reopen_failed_photo_draft
    monkeypatch.setenv("META_TOKEN_ENCRYPTION_KEY", "1" * 64)
    state = {}
    async def read():
        return copy.deepcopy(state)
    async def change(fn):
        fn(state)
    key = await add_photo_draft(change, "a" * 24, 1234, "Cozy night")
    await approve_draft(change, read, key)
    with pytest.raises(ValueError):
        await reopen_failed_photo_draft(change, read, key, 1234)
    state["instagram_publish_queue"][key]["status"] = "publishing"
    with pytest.raises(ValueError):
        await reopen_failed_photo_draft(change, read, key, 9999)
    assert state["instagram_publish_queue"][key]["status"] == "publishing"
    assert await reopen_failed_photo_draft(change, read, key, 1234) is True
    assert state["instagram_publish_queue"][key]["status"] == "approved"
    assert "owner_confirmed_absent_at" in state["instagram_publish_queue"][key]
    with pytest.raises(ValueError):
        await reopen_failed_photo_draft(change, read, key, 1234)
