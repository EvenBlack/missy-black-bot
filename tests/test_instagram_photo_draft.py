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
