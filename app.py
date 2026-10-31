import os
import re
import json
import base64
import asyncio
import logging
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path

import aiohttp
from aiohttp import web
from PIL import Image, UnidentifiedImageError
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import BufferedInputFile
from groq import Groq


# ============================================================
# CONFIGURATION
# ============================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")

OPENVERSE_CLIENT_ID = os.getenv("OPENVERSE_CLIENT_ID")
OPENVERSE_CLIENT_SECRET = os.getenv("OPENVERSE_CLIENT_SECRET")

CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID")
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN")
CLOUDFLARE_VISION_MODEL = "@cf/meta/llama-3.2-11b-vision-instruct"

ADMIN_USERNAME = "E_n_gram"
ALLOWED_USERNAMES = {"E_n_gram", "MykhailoTa"}

MEMORY_FILE = Path("missy_memory.json")

MAX_HISTORY = 15
MAX_IMAGE_BYTES = 8 * 1024 * 1024
IMAGE_SEARCH_TIMEOUT = 48
MAX_VISION_CANDIDATES = 4

VISION_TIMEOUT = aiohttp.ClientTimeout(
    total=16,
    connect=5,
    sock_read=12,
)

IMAGE_DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(
    total=16,
    connect=4,
    sock_read=12,
)

OPENVERSE_TIMEOUT = aiohttp.ClientTimeout(
    total=9,
    connect=3,
    sock_read=6,
)

TIMEOUT = aiohttp.ClientTimeout(
    total=8,
    connect=3,
    sock_read=6,
)

USER_AGENT = "MissyTelegramBot/2.1 (image search)"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
)

log = logging.getLogger("missy")


if not TELEGRAM_TOKEN:
    raise RuntimeError("Не задано TELEGRAM_TOKEN у Render Environment")

if not GROQ_API_KEY:
    raise RuntimeError("Не задано GROQ_API_KEY у Render Environment")


bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

groq_client = Groq(
    api_key=GROQ_API_KEY,
    timeout=8.0,
    max_retries=0,
)

histories = {}
memory_lock = asyncio.Lock()


# ============================================================
# LOGGING
# ============================================================

def event(name, detail):
    log.info("[%s] %s", name, detail)


# ============================================================
# MEMORY
# ============================================================

def load_memory():
    if not MEMORY_FILE.exists():
        return {
            "image_cache": {},
            "фото": [],
            "факти": [],
        }

    try:
        with MEMORY_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            raise ValueError("JSON root is not an object")

        for key, default in (
            ("image_cache", {}),
            ("фото", []),
            ("факти", []),
        ):
            if not isinstance(data.get(key), type(default)):
                data[key] = default

        return data

    except Exception:
        log.exception(
            "Не вдалося прочитати missy_memory.json; "
            "використовую порожню пам'ять"
        )

        return {
            "image_cache": {},
            "фото": [],
            "факти": [],
        }


def save_memory(data):
    tmp = MEMORY_FILE.with_suffix(".json.tmp")

    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2,
            )

        os.replace(tmp, MEMORY_FILE)

    except Exception:
        log.exception("Помилка збереження пам'яті")
        tmp.unlink(missing_ok=True)


async def change_memory(callback):
    async with memory_lock:
        data = load_memory()
        callback(data)
        save_memory(data)
        return data


# ============================================================
# ACCESS CONTROL
# ============================================================

def has_access(message):
    return bool(
        message.from_user
        and message.from_user.username in ALLOWED_USERNAMES
    )


async def check_access(message):
    if not has_access(message):
        await message.answer(
            "Ой, вибач, але доступ до мене суворо обмежений."
        )
        return False

    return True


# ============================================================
# IMAGE REQUEST DETECTION
# ============================================================

def image_request(text):
    low = re.sub(
        r"\s+",
        " ",
        text.lower(),
    ).strip()

    actions = (
        "надішли",
        "покажи",
        "скинь",
        "дай",
        "пришли",
        "знайди",
        "відшукай",
        "підбери",
    )

    objects = (
        "фото",
        "фотограф",
        "картин",
        "зображен",
        "лого",
        "логотип",
        "світлину",
    )

    has_object = any(word in low for word in objects)
    has_action = any(word in low for word in actions)

    starts_with_object = bool(
        re.match(
            r"^(?:фото|фотограф\w*|картин\w*|зображен\w*|світлин\w*)\b",
            low,
        )
    )

    return has_object and (
        has_action or starts_with_object
    )


# ============================================================
# UKRAINIAN FALLBACK TRANSLATION
# ============================================================

UKRAINIAN_IMAGE_WORDS = {
    "кіт": "cat",
    "кота": "cat",
    "коту": "cat",
    "котом": "cat",
    "коти": "cats",
    "котів": "cats",

    "кошеня": "kitten",
    "кошеняти": "kitten",
    "кошенята": "kittens",

    "собака": "dog",
    "собаки": "dogs",
    "пес": "dog",
    "пса": "dog",
    "цуценя": "puppy",
    "цуценяти": "puppy",

    "птах": "bird",
    "птаха": "bird",
    "птахи": "birds",

    "кінь": "horse",
    "коня": "horse",
    "лев": "lion",
    "лева": "lion",
    "тигр": "tiger",
    "тигра": "tiger",
    "вовк": "wolf",
    "вовка": "wolf",
    "лисиця": "fox",
    "лисиці": "fox",
    "ведмідь": "bear",
    "ведмедя": "bear",
    "заєць": "hare",
    "зайця": "hare",
    "кролик": "rabbit",
    "кролика": "rabbit",

    "хмара": "clouds sky",
    "хмари": "clouds sky",
    "хмару": "clouds sky",
    "хмар": "clouds sky",

    "шоколадка": "chocolate bar",
    "шоколадки": "chocolate bars",
    "баунті": "Bounty coconut chocolate bar",

    "машина": "car",
    "машини": "cars",
    "автомобіль": "car",
    "автомобіля": "car",

    "будинок": "house",
    "будинку": "house",
    "місто": "city",
    "міста": "cities",
    "море": "sea",
    "моря": "sea",
    "гори": "mountains",
    "гора": "mountain",
    "ліс": "forest",
    "лісу": "forest",

    "квітка": "flower",
    "квітки": "flowers",
    "троянда": "rose",
    "троянди": "roses",
    "сонце": "sun",
    "місяць": "moon",

    "людина": "person",
    "людини": "person",
    "люди": "people",
    "чоловік": "man",
    "чоловіка": "man",
    "жінка": "woman",
    "жінки": "women",
    "дитина": "child",
    "дитини": "child",
    "діти": "children",

    "портрет": "portrait",
    "портрета": "portrait",
    "їжа": "food",
    "їжі": "food",

    "будівля": "building",
    "будівлі": "buildings",
    "замок": "castle",
    "замку": "castle",

    "логотип": "logo",
    "логотипу": "logo",

    "чорний": "black",
    "чорна": "black",
    "чорне": "black",
    "білий": "white",
    "біла": "white",
    "біле": "white",

    "рудий": "ginger",
    "руда": "ginger",
    "сірий": "gray",
    "сіра": "gray",

    "маленький": "small",
    "маленька": "small",
    "великий": "large",
    "велика": "large",

    "гарний": "beautiful",
    "гарна": "beautiful",
    "милий": "cute",
    "мила": "cute",

    "реалістичний": "realistic",
    "реалістична": "realistic",
}


UKRAINIAN_NON_SUBJECT_WORDS = {
    "знайди",
    "знайти",
    "покажи",
    "надішли",
    "скинь",
    "пришли",
    "відшукай",
    "підбери",
    "дай",
    "мені",
    "будь",
    "ласка",
    "фото",
    "фотографію",
    "фотографії",
    "картинку",
    "картин",
    "зображення",
    "зображенням",
    "будьласка",
    "прошу",
}


def fallback_english_query(text):
    words = re.findall(
        r"[a-zа-яіїєґ0-9]+",
        text.lower(),
    )

    translated = []

    for word in words:
        if word in UKRAINIAN_NON_SUBJECT_WORDS:
            continue

        if word in UKRAINIAN_IMAGE_WORDS:
            translated.extend(
                UKRAINIAN_IMAGE_WORDS[word].split()
            )

        elif re.fullmatch(r"[a-z0-9]+", word):
            translated.append(word)

    return " ".join(dict.fromkeys(translated))[:120]


def is_english_query(text):
    if not text or not text.strip():
        return False

    if re.search(r"[а-яА-ЯіїєґІЇЄҐ]", text):
        return False

    return bool(
        re.search(r"[a-zA-Z]", text)
    )


def canonicalize_query(query, original_text=""):
    source = (
        original_text + " " + query
    ).lower()

    if (
        "баунті" in source
        or (
            "bounty" in source
            and (
                "chocolate" in source
                or "coconut" in source
            )
        )
    ):
        return "Bounty coconut chocolate bar"

    if (
        "хмар" in source
        or re.search(r"\bclouds?\b", query, re.I)
    ):
        return "clouds sky cumulus"

    return re.sub(
        r"\s+",
        " ",
        query,
    ).strip(" .,:;")


# ============================================================
# GROQ QUERY TRANSLATION
# ============================================================

def refine_query(text, history):
    context = "\n".join(
        f"{m['role']}: {m['content']}"
        for m in history[-6:]
    )

    prompts = [
        (
            "Translate the user's image-search request into a concise English "
            "query describing only the visible subject. "
            "Do not translate the command. Do not add the words photo, image "
            "or picture. Keep important product or brand details. "
            "Return only 2 to 8 English words. "
            "Example: 'Знайди фото кота' -> 'cat'. "
            "Example: 'фото шоколадки Баунті' -> "
            "'Bounty coconut chocolate bar'. "
            "Example: 'фото хмар' -> 'clouds sky'.\n"
            f"Context:\n{context}\n"
            f"Request: {text}"
        ),
        (
            "Return only a short English image-search subject. "
            "No explanation, no command, no photo/image/picture filler. "
            "Preserve brand and object details.\n"
            f"Request: {text}"
        ),
    ]

    for attempt, prompt in enumerate(prompts, start=1):
        try:
            response = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
                temperature=0,
                reasoning_effort="low",
                max_completion_tokens=512,
            )

            choice = response.choices[0]
            result = choice.message.content or ""

            finish_reason = getattr(
                choice,
                "finish_reason",
                None,
            )

            usage = getattr(
                response,
                "usage",
                None,
            )

            completion_tokens = getattr(
                usage,
                "completion_tokens",
                None,
            )

            details = getattr(
                usage,
                "completion_tokens_details",
                None,
            )

            reasoning_tokens = getattr(
                details,
                "reasoning_tokens",
                None,
            )

            if not result.strip():
                event(
                    "QUERY_TRANSLATION_EMPTY",
                    f"attempt={attempt}; "
                    f"finish_reason={finish_reason}; "
                    f"completion_tokens={completion_tokens}; "
                    f"reasoning_tokens={reasoning_tokens}",
                )
                continue

            result = re.sub(
                r"\s+",
                " ",
                result.strip(),
            )

            result = result.strip(
                chr(34) + chr(39) + chr(96) + " .,:;"
            )

            result = re.sub(
                r"^(query|search query)\s*:\s*",
                "",
                result,
                flags=re.I,
            )

            if (
                is_english_query(result)
                and 1 < len(result) <= 120
            ):
                result = canonicalize_query(
                    result,
                    text,
                )

                event(
                    "QUERY_TRANSLATION_SUCCESS",
                    f"attempt={attempt}; "
                    f"query={result!r}; "
                    f"finish_reason={finish_reason}; "
                    f"completion_tokens={completion_tokens}; "
                    f"reasoning_tokens={reasoning_tokens}",
