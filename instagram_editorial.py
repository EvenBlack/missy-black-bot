"""Editorial plan for Nastya: reusable English captions, zero external API calls.

All captions are fictional-character creative writing, not claims about real personal life.
These are content suggestions only; NEVER published without owner confirmation.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

KYIV = ZoneInfo("Europe/Kyiv")
IDEAS = (
    {
        "topic": "Everyday humor",
        "shot": "Funny phone-camera outtake, messy red curls, relaxed hoodie",
        "caption": (
            "Some days the plan is 'be productive' and the reality is "
            "'take 37 pictures of a coffee cup.' ☕😂\n\n"
            "What's your most unnecessary photo today?\n\n"
            "#EverydayHumor #CozyMoments #CurlyHair #LittleThings"
        ),
    },
    {
        "topic": "Beauty",
        "shot": "Natural freckles and copper curls in soft window light; unfiltered mood",
        "caption": (
            "Freckles, curls, and a little bit of chaos ✨🧡\n\n"
            "Are you team five-minute makeup or team 'where's my lip balm'?\n\n"
            "#FreckledFace #CurlyHair #NaturalBeauty #SimpleStyle"
        ),
    },
    {
        "topic": "Fashion",
        "shot": "Playful thrift-style outfit: worn jeans, bright mismatched Converse",
        "caption": (
            "One outfit, two different sneakers, zero regrets 👟😅\n\n"
            "Would you wear mismatched colors on purpose?\n\n"
            "#CasualOutfit #ColorfulStyle #ConverseStyle #OutfitIdeas"
        ),
    },
    {
        "topic": "Design",
        "shot": "Small colorful room detail, quirky painted mug or charming sketch",
        "caption": (
            "A tiny splash of color can change the whole mood 🎨🌼\n\n"
            "If your room could have ONE bold color, what would it be?\n\n"
            "#ColorfulDecor #CreativeCorners #DesignDetails #CozySpace"
        ),
    },
    {
        "topic": "Life update",
        "shot": "Cozy AI-character diary scene: autumn park, thermos, golden leaves",
        "caption": (
            "Today's little scene: crunchy leaves, a warm drink, and curls "
            "with a mind of their own 🍂☕\n\n"
            "What's one tiny thing that made you smile?\n\n"
            "#AutumnMood #CozyDays #CurlyHair #LittleJoy"
        ),
    },
    {
        "topic": "Community",
        "shot": "Two casual outfits side by side; poll-style friendly visual",
        "caption": (
            "Important research question: oversized hoodie or cozy jacket? 😄🧥\n\n"
            "You get one choice for a chilly day. Go!\n\n"
            "#ThisOrThat #CozyStyle #CasualFashion #AutumnOutfit"
        ),
    },
    {
        "topic": "Creative",
        "shot": "Fictional AI-character behind-the-scenes moodboard or design sketches",
        "caption": (
            "A little behind-the-scenes peek at building a fictional AI character 🤖🧡\n\n"
            "The curly-hair moodboard might be getting out of hand 😂\n\n"
            "What detail should I experiment with next?\n\n"
            "#AICharacter #CreativeProcess #CharacterDesign #DigitalCreator"
        ),
    },
)


def editorial_idea(day=None):
    """Local weekday order, Monday through Sunday, stable across process restarts."""
    if day is None:
        day = datetime.now(KYIV).date()
    if not isinstance(day, date):
        raise TypeError("day must be a date")
    return {**IDEAS[day.weekday()], "date": day.isoformat(), "weekday": day.weekday() + 1}


def editorial_week(start=None):
    """Preview the 7-day content library, not scheduled or published posts."""
    if start is None:
        start = datetime.now(KYIV).date()
    from datetime import timedelta
    return [editorial_idea(start + timedelta(days=i)) for i in range(7)]


def render_editorial_idea(idea):
    return (
        f"📝 Контент Насті · {idea['date']}\n"
        f"Тема: {idea['topic']}\n"
        f"Кадр: {idea['shot']}\n\n"
        f"{idea['caption']}\n\n"
        "Це ідея, не опублікований допис. Публікація потребує окремого погодження."
    )
