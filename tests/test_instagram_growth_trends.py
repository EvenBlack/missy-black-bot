"""Accurate weekly Instagram follower trends: no interpolation or made-up counts."""
from datetime import date

from instagram_growth import weekly_growth_report
from instagram_menu import menu_keyboard


def test_weekly_growth_observed_dates_only():
    sample = {"instagram_growth_snapshots": {
        "2026-11-04": {"followers": 7},
        "2026-11-08": {"followers": 8},
        "2026-11-09": {"followers": 100},
        "2026-11-01": {"followers": 1},
    }}
    text = weekly_growth_report(sample, date(2026, 11, 8))
    assert "2026-11-04: 7" in text
    assert "2026-11-08: 8" in text
    assert "2026-11-09" not in text
    assert "2026-11-01" not in text
    assert "+1" in text
    assert "Пропущені дні" in text


def test_weekly_growth_empty_one_day_and_invalid():
    assert "немає" in weekly_growth_report({}, date(2026, 11, 8))
    result = weekly_growth_report({"instagram_growth_snapshots": {
        "2026-11-08": {"followers": 8},
        "2026-11-07": {"followers": "seventeen"},
        "2026-11-06": {"followers": -1},
    }}, date(2026, 11, 8))
    assert "2026-11-08: 8" in result
    assert "2026-11-07" not in result
    assert "потрібні" in result


def test_growth_trends_available_in_owner_menu():
    actions = [button.callback_data for row in menu_keyboard().inline_keyboard for button in row]
    assert "igmenu:trends" in actions
