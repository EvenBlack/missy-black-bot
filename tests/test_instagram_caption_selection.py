"""Archived-photo editorial choices are guarded before any draft write."""
import pytest

from instagram_editorial import IDEAS
from instagram_menu import caption_options_keyboard, caption_for_topic


def test_archive_photo_offers_all_seven_topics():
    photo_id = "a" * 24
    kb = caption_options_keyboard(photo_id)
    buttons = [button for row in kb.inline_keyboard for button in row]
    assert len(buttons) == len(IDEAS) + 1
    for idx, item in enumerate(IDEAS):
        assert buttons[idx].callback_data == f"igmenu:concept:{photo_id}:{idx}"
        assert item["topic"] in buttons[idx].text
    assert buttons[-1].callback_data == "igmenu:archive"
    assert all(len(button.callback_data) <= 64 for button in buttons)


def test_topic_selection_is_explicit_no_default_autumn_caption():
    for index, idea in enumerate(IDEAS):
        assert caption_for_topic(index) is idea
        assert caption_for_topic(index)["caption"] == idea["caption"]
    for bad in (-1, 7, None, "1", 1.0, True):
        with pytest.raises(ValueError):
            caption_for_topic(bad)


def test_malformed_or_unowned_id_cannot_be_offered():
    for bad in ("", "a" * 23, "A" * 24, "a" * 25, "123:0", None):
        with pytest.raises(ValueError):
            caption_options_keyboard(bad)
