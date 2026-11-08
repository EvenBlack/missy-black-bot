"""Owner-only Ukrainian translations for seven fixed Nastya English captions.

Translations are reference text displayed in Telegram only. The caption submitted
to Instagram remains the original English text from instagram_editorial.IDEAS.
"""
from instagram_editorial import IDEAS

TRANSLATIONS_UK = (
    (
        "Іноді план на день — «бути продуктивною», а реальність — "
        "«зробити 37 фотографій чашки кави». ☕😂\n\n"
        "Яке найбезглуздіше фото ти сьогодні зробив?\n\n"
        "#ПовсякденнийГумор #ЗатишніМиті #КучерявеВолосся #МаленькіРадощі"
    ),
    (
        "Веснянки, кучері й трошки хаосу ✨🧡\n\n"
        "Ти за макіяж за п’ять хвилин чи за «де мій бальзам для губ»?\n\n"
        "#Веснянки #КучерявеВолосся #ПрироднаКраса #ПростийСтиль"
    ),
    (
        "Один образ, два різні кеди й жодних жалкувань 👟😅\n\n"
        "Ти б навмисне взув кеди різних кольорів?\n\n"
        "#ПовсякденнийОбраз #ЯскравийСтиль #СтильConverse #ІдеїОбразів"
    ),
    (
        "Маленький яскравий акцент може повністю змінити настрій 🎨🌼\n\n"
        "Якби ти міг вибрати лише ОДИН сміливий колір для кімнати, який би це був?\n\n"
        "#ЯскравийДекор #ТворчіКуточки #ДеталіДизайну #ЗатишнийПростір"
    ),
    (
        "Маленька сценка дня: листя шарудить під ногами, теплий напій, "
        "а кучері живуть своїм життям 🍂☕\n\n"
        "Яка дрібничка сьогодні змусила тебе усміхнутися?\n\n"
        "#ОсіннійНастрій #ЗатишніДні #КучерявеВолосся #МаленькаРадість"
    ),
    (
        "Важливе дослідження: худі оверсайз чи затишна куртка? 😄🧥\n\n"
        "На прохолодний день можна вибрати тільки щось одне. Що береш?\n\n"
        "#АбоАбо #ЗатишнийСтиль #ПовсякденнаМода #ОсіннійОбраз"
    ),
    (
        "Невеличкий погляд за лаштунки створення вигаданого ШІ-персонажа 🤖🧡\n\n"
        "Здається, мудборд із кучерявим волоссям виходить з-під контролю 😂\n\n"
        "З якою деталлю образу мені поекспериментувати далі?\n\n"
        "#ШІПерсонаж #ТворчийПроцес #ДизайнПерсонажа #ЦифровийАвтор"
    ),
)


def translate_editorial_caption(caption):
    """Return known Ukrainian translation, or None rather than inventing one."""
    for idea, translation in zip(IDEAS, TRANSLATIONS_UK):
        if caption == idea["caption"]:
            return translation
    return None


def bilingual_editorial_caption(caption):
    """Caption shown to owner; no translated text is ever included in publish payload."""
    translation = translate_editorial_caption(caption)
    if translation is None:
        return "🇬🇧 English (оригінал):\n" + caption + "\n\n🇺🇦 Переклад поки не підготовлено для цього тексту."
    return "🇬🇧 Англійський підпис:\n" + caption + "\n\n🇺🇦 Український переклад:\n" + translation
