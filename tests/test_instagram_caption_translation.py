"""Ukrainian owner previews must never alter English publishing captions."""
from instagram_caption_translation import (
    TRANSLATIONS_UK, translate_editorial_caption, bilingual_editorial_caption,
)
from instagram_editorial import IDEAS


def test_each_english_editorial_caption_has_a_ukrainian_translation():
    assert len(IDEAS) == len(TRANSLATIONS_UK) == 7
    for idea, translation in zip(IDEAS, TRANSLATIONS_UK):
        assert translate_editorial_caption(idea["caption"]) == translation
        assert len(translation) > 50
        preview = bilingual_editorial_caption(idea["caption"])
        assert "🇬🇧 Англійський підпис:" in preview
        assert "🇺🇦 Український переклад:" in preview
        assert idea["caption"] in preview
        assert translation in preview


def test_unknown_caption_never_receives_incorrect_translation():
    assert translate_editorial_caption("Other caption") is None
    result = bilingual_editorial_caption("Other caption")
    assert "Other caption" in result
    assert "не підготовлено" in result
