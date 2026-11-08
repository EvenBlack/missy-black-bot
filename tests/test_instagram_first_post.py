"""Regression tests for precisely scoped one-time authorized Nastya Instagram launch."""
import copy
from datetime import datetime, timezone

import pytest
from instagram_first_post import eligible_drafts, one_shot_first_post, CAPTION_START


def sample(status="approved", owner="123"):
    return {"status": status, "caption": CAPTION_START + " 🍂☕🧡",
            "created_at": "2026-11-08T13:34:00+00:00",
            "photo_owner_id": owner, "photo_archive_id": "a" * 24,
            "image_url": "https://example.com/x.jpg"}


def test_only_the_target_post_is_eligible():
    state = {"instagram_publish_queue": {
        "1234abcd": sample(),
        "1234abce": {**sample(), "caption": "Other photo"},
        "1234abcf": {**sample(), "status": "draft"},
        "1234abc0": {**sample(), "created_at": "2026-11-09T13:34:00+00:00"},
    }}
    assert [k for k, _ in eligible_drafts(state)] == ["1234abcd"]


@pytest.mark.asyncio
async def test_one_shot_requires_enable_and_verifies_existing_post(monkeypatch):
    state = {"instagram_publish_queue": {"1234abcd": sample()}, "instagram_account_id": "1789001",
             "instagram_morning_report": {"chat_id": 98}}
    async def read():
        return copy.deepcopy(state)
    async def change(fn):
        fn(state)
    calls = []
    class Bot:
        async def send_message(self, chat_id, text):
            calls.append((chat_id, text))
    async def existing(account, caption):
        return "1789123"
    async def fail_publish(*args):
        raise AssertionError("Cannot publish an existing item")
    monkeypatch.delenv("INSTAGRAM_FIRST_POST_ENABLED", raising=False)
    assert await one_shot_first_post(Bot(), read, change, existing, fail_publish) == "disabled"
    monkeypatch.setenv("INSTAGRAM_FIRST_POST_ENABLED", "true")
    assert await one_shot_first_post(Bot(), read, change, existing, fail_publish) == "existing"
    assert state["instagram_publish_queue"]["1234abcd"]["status"] == "published"
    assert len(calls) == 1
    assert await one_shot_first_post(Bot(), read, change, existing, fail_publish) == "not_ready"


@pytest.mark.asyncio
async def test_one_shot_publishes_once_only_after_verified_no_existing(monkeypatch):
    state = {"instagram_publish_queue": {"1234abcd": sample()}, "instagram_account_id": "1789001"}
    async def read():
        return copy.deepcopy(state)
    async def change(fn):
        fn(state)
    class Bot:
        async def send_message(self, chat_id, text):
            raise AssertionError("No configured chat")
    counts = []
    async def no_existing(account, caption):
        counts.append("checked")
        return None
    async def publish(read_memory, change_memory, draft_id, owner):
        counts.append("publish")
        assert draft_id == "1234abcd" and owner == "123"
        def finish(data):
            data["instagram_publish_queue"][draft_id]["status"] = "published"
        await change_memory(finish)
        return "999"
    monkeypatch.setenv("INSTAGRAM_FIRST_POST_ENABLED", "true")
    assert await one_shot_first_post(Bot(), read, change, no_existing, publish) == "published"
    assert counts == ["checked", "publish"]
    assert await one_shot_first_post(Bot(), read, change, no_existing, publish) == "not_ready"


@pytest.mark.asyncio
async def test_one_shot_never_publishes_when_verification_fails(monkeypatch):
    state = {"instagram_publish_queue": {"1234abcd": sample()}, "instagram_account_id": "1789001"}
    async def read():
        return copy.deepcopy(state)
    async def change(fn):
        fn(state)
    async def fail_check(account, caption):
        raise RuntimeError("Meta unavailable")
    async def publish(*args):
        raise AssertionError("Cannot publish without verification")
    monkeypatch.setenv("INSTAGRAM_FIRST_POST_ENABLED", "true")
    assert await one_shot_first_post(None, read, change, fail_check, publish) == "verification_blocked"
    assert state["instagram_publish_queue"]["1234abcd"]["status"] == "approved"
