"""Fourteen-day owner-reviewed Nastya content calendar.

Pure planning: no uploads, synthetic stats, API posts, or unsolicited messages.
Two photo briefs per day, 8 Reels total, 14 days with optional Stories
(owners may choose only 5 of 7 days each week).
"""
from datetime import date, timedelta
from zoneinfo import ZoneInfo
from datetime import datetime

KYIV = ZoneInfo("Europe/Kyiv")
PHOTO_SCENES = (
    ("Ранкова кава й кучері", "Soft morning light, bright copper curls and a mug in hand", "Маленький ранковий ритуал", "What's your first small ritual of the day?"),
    ("Джинси й яскраві кеди", "Ripped-knee jeans, bright sneakers, candid hallway mirror", "Образ без зайвих правил", "Would you wear bright sneakers with everything?"),
    ("Руда кучерява зачіска", "Close-up of curls and freckles in natural window light", "Кучері мають власні плани", "Do you have a good hair day ritual?"),
    ("Осінній затишок", "Autumn leaves, comfortable hoodie and a paper cup", "Маленькі радощі осені", "Are you team autumn walks or warm blankets?"),
    ("Кольорова кімната", "Cozy colorful room, books, handmade sketchbook", "Трохи кольору", "What color instantly improves your mood?"),
    ("Похід у магазин", "Everyday grocery bag and a funny candid expression", "Забула головну покупку", "Ever gone shopping and forgotten the one thing you needed?"),
    ("Домашній вечір", "Sofa, blanket, warm lamp and a cup of tea", "Вечірній настрій", "What does your perfect quiet evening look like?"),
    ("Несподіваний дощ", "Umbrella and playful face in a rainy street scene", "Погода вирішила інакше", "Rainy days: cozy or chaotic?"),
    ("Кеди проти черевиків", "Two contrasting shoe choices laid out on the floor", "Маленька дилема стилю", "Sneakers or boots today?"),
    ("Танцювальний настрій", "Dynamic laugh and playful pose in casual jeans", "Радість без причини", "What's a song that always makes you move?"),
    ("Селфі без ідеального ракурсу", "Funny casual selfie with big copper curls", "Селфі з характером", "How many selfies does it take to get one you like?"),
    ("Ранкова розгубленість", "Oversized hoodie and toast beside a notebook", "Плани й реальність", "Are your mornings organized or improvised?"),
    ("Маленьке хобі", "Desk with a doodle, colored markers and a warm drink", "Трохи творчості", "What creative hobby would you love to try?"),
    ("Тиждень у кадрах", "Photo collage moodboard with red hair and colorful everyday details", "Мій маленький тиждень", "Which little moment made you smile this week?"),
)
REELS = (
    ("Прокинулась продуктивною", "A: set an ambitious to-do list; B: get distracted by coffee and curls; C: laugh at the list.", "Me: today I'll be productive. Also me:"),
    ("Тест двох образів", "A: hoodie and jeans; B: switch sneakers three times; C: settle on the first pair.", "When choosing shoes becomes a full-time job"),
    ("Кучері проти погоди", "A: style curls; B: step outside in wind; C: comical reaction and smile.", "My curls had other plans"),
    ("Купила все, крім потрібного", "A: arrive home with grocery bag; B: unpack random items; C: remember the one missing item.", "I went shopping for ONE thing"),
    ("Незручне селфі", "A: pose confidently; B: camera angle goes wrong; C: laugh and keep the outtake.", "The photo I planned vs. the photo I got"),
    ("Дощ змінив плани", "A: cute outfit ready; B: rain starts; C: grab oversized hoodie and wink.", "Weather: 1. My plans: 0."),
    ("Кава й п'ять хвилин", "A: say only five minutes; B: coffee, doodles, phone; C: realize an hour passed.", "Just five more minutes..."),
    ("Хто керує моїми кучерями?", "A: try to tame hair; B: curls bounce back; C: playful final look.", "Plot twist: the curls are in charge"),
)
STORY_IDEAS = (
    ("Ранкова кава", "Coffee or tea?"),
    ("Вибір образу", "Bright sneakers or neutral ones?"),
    ("Кучері сьогодні", "Good hair day? Yes / Definitely"),
    ("Настрій дня", "Cozy day or adventure day?"),
    ("Маленька творчість", "Which color should I pick?"),
    ("Кумедний побут", "Ever forgotten your shopping list?"),
    ("Вечірній затишок", "Movie night or a book?"),
)
TAGS = "#NastyaTsarapka #CurlyRedhead #EverydayHumor"
REEL_WEEKDAYS = frozenset((0, 2, 4, 6))


def calendar_day(start, day_number):
    if not isinstance(start, date) or not 1 <= day_number <= 14:
        raise ValueError("Expected a start date and day 1..14")
    actual = start + timedelta(days=day_number - 1)
    a, b = PHOTO_SCENES[(day_number - 1) % len(PHOTO_SCENES)], PHOTO_SCENES[day_number % len(PHOTO_SCENES)]
    items = []
    for item in (a, b):
        title, visual, hook_uk, question = item
        items.append({"title": title, "visual": visual, "idea_uk": hook_uk, "caption_en": question + " 🧡\n\n" + TAGS})
    # Four evenly distributed Reels per seven-day block independent of calendar weekday.
    offset = (day_number - 1) % 7
    reel = REELS[(day_number - 1)//7*4 + (0, 2, 4, 6).index(offset)] if offset in REEL_WEEKDAYS else None
    story = STORY_IDEAS[(day_number - 1) % len(STORY_IDEAS)]
    return {"date": actual, "day": day_number, "photos": items,
            "reel": reel, "story": story}


def calendar_summary(start):
    days = [calendar_day(start, i) for i in range(1, 15)]
    return {"days": days, "photos": sum(len(x["photos"]) for x in days),
            "reels": sum(x["reel"] is not None for x in days),
            "story_days": len(days)}


def render_day(day):
    lines = [f"📅 Настя · день {day['day']}/14 · {day['date'].isoformat()}",
             "🖼 Дві фотографії (чернетки, не опубліковано):"]
    for i, photo in enumerate(day["photos"], 1):
        lines += [f"Фото {i}. {photo['title']}", "Кадр: " + photo["visual"],
                  "Ідея українською: " + photo["idea_uk"], "Підпис англійською: " + photo["caption_en"]]
    if day["reel"]:
        title, actions, hook = day["reel"]
        lines += ["🎬 Reels: " + title, "Сценарій: " + actions, "Англійський хук: " + hook]
    else:
        lines.append("🎬 Reels сьогодні не заплановано")
    lines += ["⭕ Stories: " + day["story"][0], "Опитування англійською: " + day["story"][1],
              "\nУсе лише план. Для відео/фото потрібні матеріали; публікація — тільки після твого підтвердження."]
    return "\n".join(lines)


def render_summary(start):
    x = calendar_summary(start)
    return (f"📆 Настя: план на 14 днів від {start.isoformat()}\n"
            f"🖼 {x['photos']} фотодописів (2 щодня)\n"
            f"🎬 {x['reels']} Reels (4 щотижня)\n"
            "⭕ Stories: 5–7 днів щотижня (14 ідей, можна пропускати 2 дні)\n\n"
            "Обирай день нижче, щоб отримати два сюжети, сценарій Reels і Stories. "
            "Це план, не автопублікація.")


def current_start():
    return datetime.now(KYIV).date()
