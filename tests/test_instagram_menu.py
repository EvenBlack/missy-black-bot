"""Tests for Instagram owner menu and daily Kyiv reports (no Meta/Telegram network)."""
from datetime import datetime, timezone
from types import SimpleNamespace
import copy

import pytest

from instagram_menu import (
    KYIV, menu_keyboard, should_send, report_text, enable_report, disable_report,
    daily_report_once,
)


def test_menu_buttons_and_report_day_boundary():
    keyboard = menu_keyboard()
    actions = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert "igmenu:stats" in actions
    assert "igmenu:plan" in actions
    assert "igmenu:dialogs" in actions
    assert "igmenu:report" in actions
    state = {"enabled": True, "chat_id": 123}
    before_nine = datetime(2026, 11, 9, 6, 59, tzinfo=timezone.utc)  # 08:59 Kyiv
    at_nine = datetime(2026, 11, 9, 7, 0, tzinfo=timezone.utc)  # 09:00 Kyiv
    assert not should_send(before_nine, state)
    assert should_send(at_nine, state)
    state["last_date"] = "2026-11-09"
    assert not should_send(at_nine, state)
    assert not should_send(at_nine, {"enabled": False, "chat_id": 123})
    assert KYIV.key == "Europe/Kyiv"


def test_report_text_is_grounded_not_invented():
    assert "12" in report_text({"username": "_tsarapka__", "followers_count": 12, "media_count": 3})
    assert "+5" in report_text({"username": "_tsarapka__", "followers_count": 12, "media_count": 3}, 7)
    assert "поки не повертає" in report_text({"username": "_tsarapka__", "followers_count": None})


@pytest.mark.asyncio
async def test_morning_report_once_and_disable(monkeypatch):
    state = {}
    async def read():
        return copy.deepcopy(state)
    async def change(updater):
        updater(state)
        return copy.deepcopy(state)

    calls = []
    class FakeBot:
        async def send_message(self, chat_id, message):
            calls.append((chat_id, message))

    async def fake_fetch(read_memory):
        return {"username": "_tsarapka__", "followers_count": 74, "media_count": 9}

    await enable_report(change, 1234)
    # Fixed-time delivery scenario, independent of the actual date on the CI runner.
    state["instagram_morning_report"]["first_date"] = "2026-11-09"
    now = datetime(2026, 11, 9, 7, 1, tzinfo=timezone.utc)
    assert await daily_report_once(FakeBot(), read, change, now, fake_fetch)
    assert len(calls) == 1 and calls[0][0] == 1234
    assert "74" in calls[0][1]
    assert state["instagram_morning_report"]["last_date"] == "2026-11-09"
    assert state["instagram_growth_snapshots"]  # persisted
    assert not await daily_report_once(FakeBot(), read, change, now, fake_fetch)
    assert len(calls) == 1
    await disable_report(change)
    assert not state["instagram_morning_report"]["enabled"]


@pytest.mark.asyncio
async def test_report_when_meta_unavailable_does_not_fabricate_count():
    state = {}
    async def read():
        return copy.deepcopy(state)
    async def change(updater):
        updater(state)
        return copy.deepcopy(state)
    sent = []
    async def fake_fetch(read_memory):
        raise RuntimeError("permissions")
    bot = SimpleNamespace(send_message=None)
    async def sender(chat_id, text):
        sent.append((chat_id, text))
    bot.send_message = sender
    await enable_report(change, 1234)
    # Fixed-time delivery scenario, independent of the actual date on the CI runner.
    state["instagram_morning_report"]["first_date"] = "2026-11-09"
    assert await daily_report_once(bot, read, change,
                                   datetime(2026, 11, 9, 7, 0, tzinfo=timezone.utc),
                                   fake_fetch)
    assert len(sent) == 1
    assert "недоступна" in sent[0][1]


@pytest.mark.asyncio
async def test_new_subscription_schedules_next_day_and_no_late_delivery():
    from datetime import timedelta
    state = {}
    async def read():
        return copy.deepcopy(state)
    async def change(updater):
        updater(state)
        return copy.deepcopy(state)
    await enable_report(change, 123)
    sub = state["instagram_morning_report"]
    today = datetime.now(KYIV).date()
    assert sub["first_date"] == (today + timedelta(days=1)).isoformat()
    late_today = datetime.combine(today, datetime.min.time(), tzinfo=KYIV).replace(hour=14)
    assert not should_send(late_today, sub)
    at_nine_tomorrow = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=KYIV).replace(hour=9)
    assert should_send(at_nine_tomorrow, sub)
    assert not should_send(at_nine_tomorrow.replace(hour=10), sub)
