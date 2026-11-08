"""Nastya editorial captions and local reconciliation require no Meta or paid API."""
import copy
from datetime import date

import pytest

from instagram_editorial import IDEAS, editorial_idea, editorial_week, render_editorial_idea
from instagram_growth import mark_manual_publication


def test_all_seven_day_ideas_are_fictional_safe_and_distinct():
    assert len(IDEAS) == 7
    week = editorial_week(date(2026, 11, 9))
    assert len(week) == 7
    assert week[0]["topic"] == "Everyday humor"
    assert week[6]["topic"] == "Creative"
    assert [r["date"] for r in week] == [
        "2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12",
        "2026-11-13", "2026-11-14", "2026-11-15",
    ]
    for item in week:
        assert len(item["caption"]) < 2200
        assert 3 <= item["caption"].count("#") <= 5
        assert "not published" not in item["caption"]
        assert "не опублікований" in render_editorial_idea(item)


@pytest.mark.asyncio
async def test_manual_publication_reconciles_only_owner_and_removes_signed_url():
    state = {"instagram_publish_queue": {
        "abcdef01": {
            "status": "approved",
            "photo_owner_id": "17",
            "image_url": "https://example.com/signed-private-media",
        },
        "abcdef02": {
            "status": "approved",
            "photo_owner_id": "18",
            "image_url": "https://example.com/other-photo",
        },
    }}
    async def read():
        return copy.deepcopy(state)
    async def change(fn):
        fn(state)
    with pytest.raises(ValueError):
        await mark_manual_publication(change, read, "abcdef01", 18)
    assert state["instagram_publish_queue"]["abcdef01"]["status"] == "approved"
    assert await mark_manual_publication(change, read, "abcdef01", 17)
    record = state["instagram_publish_queue"]["abcdef01"]
    assert record["status"] == "published_manual"
    assert "image_url" not in record
    assert state["instagram_publish_queue"]["abcdef02"]["status"] == "approved"
    with pytest.raises(ValueError):
        await mark_manual_publication(change, read, "abcdef01", 17)


@pytest.mark.asyncio
async def test_published_and_cancelled_cannot_be_manually_marked():
    state = {"instagram_publish_queue": {
        "abcdef01": {"status": "published", "photo_owner_id": "17"},
        "abcdef02": {"status": "cancelled", "photo_owner_id": "17"},
    }}
    async def read():
        return copy.deepcopy(state)
    async def change(fn):
        fn(state)
    for key in ("abcdef01", "abcdef02"):
        with pytest.raises(ValueError):
            await mark_manual_publication(change, read, key, 17)
