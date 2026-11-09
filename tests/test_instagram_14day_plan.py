from datetime import date, timedelta
import pytest
from instagram_14day_plan import calendar_day, calendar_summary, render_day, render_summary


def test_two_week_commitment():
    start = date(2026, 11, 9)
    result = calendar_summary(start)
    assert result["photos"] == 28
    assert result["reels"] == 8
    assert result["story_days"] == 14
    assert len(result["days"]) == 14
    assert result["days"][-1]["date"] == start + timedelta(days=13)
    assert [i["day"] for i in result["days"] if i["reel"]] == [1,3,5,7,8,10,12,14]


def test_english_caption_separate_and_owner_only_preview():
    day = calendar_day(date(2026, 11, 9), 1)
    for photo in day["photos"]:
        assert "caption_en" in photo
        assert "#NastyaTsarapka" in photo["caption_en"]
        assert photo["idea_uk"] not in photo["caption_en"]
    assert "не опубліковано" in render_day(day)
    assert "не автопублікація" in render_summary(date(2026,11,9))


@pytest.mark.parametrize("day", [0, 15, -1])
def test_invalid_day_rejected(day):
    with pytest.raises(ValueError):
        calendar_day(date(2026,11,9), day)
