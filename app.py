import os
import re
import json
import asyncio
import logging
import shutil
from io import BytesIO
from html.parser import HTMLParser
from typing import Optional

import aiohttp
from aiohttp import web
from PIL import Image, UnidentifiedImageError

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import BufferedInputFile
from aiogram.exceptions import TelegramConflictError, TelegramNetworkError
from groq import Groq


# ============================================================
# 1. НАЛАШТУВАННЯ
# ============================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

ADMIN_USERNAME = "E_n_gram"
ALLOWED_USERNAMES = {"E_n_gram", "MykhailoTa"}

MEMORY_FILE = "missy_memory.json"

MAX_HISTORY_LENGTH = 15
MAX_IMAGE_BYTES = 9 * 1024 * 1024
MAX_IMAGE_SIDE = 2000

HTTP_TIMEOUT = aiohttp.ClientTimeout(
    total=20,
    connect=8
)

USER_AGENT = "MissyTelegramBot/1.0 (personal assistant)"

if not TELEGRAM_TOKEN:
    raise RuntimeError(
        "Не задано змінну середовища TELEGRAM_TOKEN"
    )

if not GROQ_API_KEY:
    logging.warning(
        "GROQ_API_KEY не задано. ШІ-функції будуть недоступні."
    )

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

user_histories = {}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s:%(name)s:%(message)s"
)

logger = logging.getLogger("missy")

# Не дозволяємо двом обробникам одночасно змінювати пам'ять.
memory_lock = asyncio.Lock()

# Захист від паралельного пошуку однакових зображень.
image_search_locks = {}


def log_event(event_type: str, details: str):
    logger.info("[%s] %s", event_type, details)


# ============================================================
# 2. ПАМ'ЯТЬ
# ============================================================

def default_memory():
    return {
        "image_cache": {},
        "фото": [],
        "факти": []
    }


def load_memory() -> dict:
    if not os.path.exists(MEMORY_FILE):
        return default_memory()

    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as file:
            raw = file.read()

        try:
            data = json.loads(raw)

        except json.JSONDecodeError:
            # Спроба врятувати перший коректний JSON-об'єкт.
            decoder = json.JSONDecoder()
            data, end = decoder.raw_decode(raw)

            backup = MEMORY_FILE + ".broken"

            if not os.path.exists(backup):
                shutil.copy2(MEMORY_FILE, backup)

            log_event(
                "MEMORY_RECOVERY",
                f"Зайві дані після позиції {end}. "
                f"Резервна копія: {backup}"
            )

        if not isinstance(data, dict):
            raise ValueError("Некоректна структура пам'яті")

        defaults = default_memory()

        for key, value in defaults.items():
            if not isinstance(data.get(key), type(value)):
                data[key] = value

        data["фото"] = data["фото"][-50:]
        data["факти"] = data["факти"][-200:]

        return data

    except Exception as error:
        log_event("MEMORY_ERROR", str(error))

        backup = MEMORY_FILE + ".broken"

        try:
            if os.path.exists(MEMORY_FILE) and not os.path.exists(backup):
                shutil.copy2(MEMORY_FILE, backup)
        except OSError:
            pass

        return default_memory()


def save_memory(memory_data: dict):
    temporary_file = MEMORY_FILE + ".tmp"

    try:
        with open(temporary_file, "w", encoding="utf-8") as file:
            json.dump(
                memory_data,
                file,
                ensure_ascii=False,
                indent=4
            )

        os.replace(temporary_file, MEMORY_FILE)

    except OSError as error:
        log_event("MEMORY_SAVE_ERROR", str(error))

        try:
            if os.path.exists(temporary_file):
                os.remove(temporary_file)
        except OSError:
            pass


async def update_memory(callback):
    async with memory_lock:
        memory = load_memory()
        callback(memory)
        save_memory(memory)


# ============================================================
# 3. ПОШУК ТЕКСТОВОЇ ІНФОРМАЦІЇ
# ============================================================

class DDGLiteParser(HTMLParser):

    def __init__(self):
        super().__init__()
        self.results = []
        self.in_result = False
        self.current_text = ""

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            classes = dict(attrs).get("class", "")

            if "result-link" in classes or "result-snippet" in classes:
                self.in_result = True

    def handle_endtag(self, tag):
        if tag == "a" and self.in_result:
            value = self.current_text.strip()

            if value:
                self.results.append(value)

            self.current_text = ""
            self.in_result = False

    def handle_data(self, data):
        if self.in_result:
            self.current_text += data


async def web_search(query: str, max_results: int = 3) -> str:
    url = "https://lite.duckduckgo.com/lite/"

    try:
        async with aiohttp.ClientSession(
            timeout=HTTP_TIMEOUT
        ) as session:

            async with session.post(
                url,
                data={"q": query},
                headers={"User-Agent": USER_AGENT}
            ) as response:

                response.raise_for_status()
                html = await response.text(errors="ignore")

        parser = DDGLiteParser()
        parser.feed(html)

        if parser.results:
            return "\n".join(parser.results[:max_results])

    except Exception as error:
        log_event("WEB_SEARCH_ERROR", str(error))

    return ""


# ============================================================
# 4. УТОЧНЕННЯ ЗАПИТУ ЧЕРЕЗ GROQ
# ============================================================

def _refine_image_query_sync(
    user_prompt: str,
    history_list: list
) -> str:

    if not client:
        return user_prompt

    try:
        context = "\n".join(
            f"{item.get('role', '')}: {item.get('content', '')}"
            for item in history_list[-6:]
        )

        prompt = (
            f"Історія діалогу:\n{context}\n\n"
            f"Запит користувача: {user_prompt}\n\n"
            "Створи короткий і точний англомовний запит "
            "для пошуку фотографії в інтернеті. "
            "Враховуй контекст діалогу та займенники. "
            "Не додавай деталей, яких немає в запиті. "
            "Якщо користувач просить кота, шукай саме домашнього кота, "
            "а не рись, тигра чи іншу дику тварину. "
            "Поверни тільки пошуковий запит англійською, "
            "без пояснень та лапок."
        )

        response = client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.1
        )

        refined = (
            response.choices[0].message.content or ""
        ).strip().strip("\"'")

        return refined[:250] if len(refined) >= 3 else user_prompt

    except Exception as error:
        log_event("QUERY_REFINE_ERROR", str(error))
        return user_prompt


async def smart_refine_image_query(
    user_prompt: str,
    history_list: list
) -> str:

    return await asyncio.to_thread(
        _refine_image_query_sync,
        user_prompt,
        history_list
    )


# ============================================================
# 5. ЗАГАЛЬНИЙ HTTP ЗАПИТ З ПОВТОРНИМИ СПРОБАМИ
# ============================================================

async def get_json_with_retry(
    session: aiohttp.ClientSession,
    url: str,
    params: dict,
    attempts: int = 3
) -> Optional[dict]:

    for attempt in range(attempts):

        try:
            async with session.get(
                url,
                params=params
            ) as response:

                if response.status == 429:
                    retry_after = response.headers.get("Retry-After")

                    try:
                        delay = min(float(retry_after), 20)
                    except (ValueError, TypeError):
                        delay = 2 ** (attempt + 1)

                    log_event(
                        "RATE_LIMIT",
                        f"HTTP 429. Повтор через {delay} секунд."
                    )

                    if attempt < attempts - 1:
                        await asyncio.sleep(delay)

                    continue

                response.raise_for_status()

                return await response.json(content_type=None)

        except (
            aiohttp.ClientError,
            asyncio.TimeoutError
        ) as error:

            log_event("API_REQUEST_ERROR", str(error))

            if attempt < attempts - 1:
                await asyncio.sleep(2 ** attempt)

    return None


# ============================================================
# 6. ПОШУК У WIKIMEDIA COMMONS
# ============================================================

async def search_wikimedia(
    session: aiohttp.ClientSession,
    query: str
) -> list:

    api_url = "https://commons.wikimedia.org/w/api.php"

    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": "6",
        "gsrlimit": "10",
        "prop": "imageinfo",
        "iiprop": "url|mime|size",
        "iiurlwidth": "1400",
        "format": "json"
    }

    data = await get_json_with_retry(
        session,
        api_url,
        params
    )

    if not data:
        return []

    candidates = []

    pages = data.get("query", {}).get("pages", {})

    for page in pages.values():

        title = page.get("title", "")

        image_info = (
            page.get("imageinfo") or [{}]
        )[0]

        mime = image_info.get("mime", "")

        image_url = (
            image_info.get("thumburl")
            or image_info.get("url")
        )

        if (
            mime in {
                "image/jpeg",
                "image/png",
                "image/webp"
            }
            and image_url
        ):

            candidates.append({
                "url": image_url,
                "title": title,
                "source": "Wikimedia Commons"
            })

    return candidates


# ============================================================
# 7. ПОШУК В OPENVERSE
# ============================================================

async def search_openverse(
    session: aiohttp.ClientSession,
    query: str
) -> list:

    api_url = "https://api.openverse.org/v1/images/"

    params = {
        "q": query,
        "page_size": 10,
        "mature": "false"
    }

    data = await get_json_with_retry(
        session,
        api_url,
        params
    )

    if not data:
        return []

    candidates = []

    for item in data.get("results", []):

        image_url = (
            item.get("thumbnail")
            or item.get("url")
        )

        if (
            image_url
            and image_url.startswith("https://")
        ):

            candidates.append({
                "url": image_url,
                "title": item.get("title") or "",
                "source": "Openverse",
                "license": item.get("license") or ""
            })

    return candidates


# ============================================================
# 8. ОЦІНКА ВІДПОВІДНОСТІ НАЗВИ ЗАПИТУ
# ============================================================

STOP_WORDS = {
    "real", "photo", "picture", "image",
    "of", "the", "a", "an", "and",
    "find", "show", "please",
    "знайди", "фото", "фотографію",
    "покажи", "картинку", "надішли",
    "мені", "будь", "ласка"
}


def tokenize(value: str) -> set:

    words = re.findall(
        r"[a-zа-яіїєґ0-9]+",
        value.lower()
    )

    return {
        word
        for word in words
        if len(word) > 1 and word not in STOP_WORDS
    }


def image_relevance(
    query: str,
    title: str
) -> float:

    query_words = tokenize(query)
    title_words = tokenize(title)

    if not query_words or not title_words:
        return 0.0

    common = query_words & title_words

    return len(common) / len(query_words)


# ============================================================
# 9. ПЕРЕВІРКА ТА КОНВЕРТАЦІЯ ЗОБРАЖЕННЯ
# ============================================================

async def download_image(
    session: aiohttp.ClientSession,
    url: str
) -> Optional[bytes]:

    try:
        async with session.get(
            url,
            headers={"User-Agent": USER_AGENT},
            allow_redirects=True
        ) as response:

            response.raise_for_status()

            content_type = (
                response.headers.get("Content-Type", "")
                .split(";", 1)[0]
                .lower()
            )

            if not content_type.startswith("image/"):
                return None

            content_length = response.headers.get("Content-Length")

            if (
                content_length
                and int(content_length) > MAX_IMAGE_BYTES
            ):
                return None

            raw = await response.content.read(
                MAX_IMAGE_BYTES + 1
            )

            if len(raw) > MAX_IMAGE_BYTES:
                return None

        # Перевіряємо справжність файлу.
        with Image.open(BytesIO(raw)) as img:
            img.verify()

        # Повторно відкриваємо й нормалізуємо.
        with Image.open(BytesIO(raw)) as img:

            if img.width < 100 or img.height < 100:
                return None

            img = img.convert("RGB")

            img.thumbnail(
                (MAX_IMAGE_SIDE, MAX_IMAGE_SIDE)
            )

            output = BytesIO()

            img.save(
                output,
                format="JPEG",
                quality=90,
                optimize=True
            )

            return output.getvalue()

    except (
        aiohttp.ClientError,
        asyncio.TimeoutError,
        OSError,
        ValueError,
        UnidentifiedImageError
    ) as error:

        log_event(
            "IMAGE_DOWNLOAD_ERROR",
            f"{url}: {error}"
        )

        return None


# ============================================================
# 10. ГОЛОВНИЙ ПОШУК ФОТО
# ============================================================

async def find_image_from_web(
    query: str
) -> Optional[tuple]:

    headers = {
        "User-Agent": USER_AGENT
    }

    async with aiohttp.ClientSession(
        timeout=HTTP_TIMEOUT,
        headers=headers
    ) as session:

        # Спочатку Openverse, потім Wikimedia.
        sources = [
            search_openverse,
            search_wikimedia
        ]

        for source in sources:

            try:
                candidates = await source(
                    session,
                    query
                )

                log_event(
                    "IMAGE_CANDIDATES",
                    f"{source.__name__}: {len(candidates)} результатів"
                )

                # Спершу перевіряємо найрелевантніші назви.
                candidates.sort(
                    key=lambda item: image_relevance(
                        query,
                        item.get("title", "")
                    ),
                    reverse=True
                )

                for candidate in candidates:

                    title = candidate.get("title", "")

                    score = image_relevance(
                        query,
                        title
                    )

                    if score < 0.25:
                        continue

                    image_bytes = await download_image(
                        session,
                        candidate["url"]
                    )

                    if image_bytes:

                        log_event(
                            "IMAGE_FOUND",
                            f"{source.__name__}: {title}"
                        )

                        return image_bytes, title

                    # Невелика пауза між завантаженнями.
                    await asyncio.sleep(0.3)

            except Exception as error:

                log_event(
                    "IMAGE_SOURCE_ERROR",
                    f"{source.__name__}: {error}"
                )

            # Не перевантажуємо безкоштовні API.
            await asyncio.sleep(1)

    return None


# ============================================================
# 11. ДОПОМІЖНІ ФУНКЦІЇ TELEGRAM
# ============================================================

def sanitize_markdown(text: str) -> str:

    if text.count("*") % 2 != 0:
        text = text.replace("*", "")

    if text.count("_") % 2 != 0:
        text = text.replace("_", "")

    return text


async def send_long_message(
    message: types.Message,
    text: str
):

    max_length = 4000

    for start in range(0, len(text), max_length):

        await message.answer(
            text[start:start + max_length]
        )

        if start + max_length < len(text):
            await asyncio.sleep(0.3)


async def check_general_access(
    message: types.Message
) -> bool:

    username = (
        message.from_user.username
        if message.from_user
        else None
    )

    if not username or username not in ALLOWED_USERNAMES:

        await message.answer(
            "Ой, вибач, але доступ до мене суворо обмежений."
        )

        return False

    return True


async def check_admin_rights(
    message: types.Message
) -> bool:

    username = (
        message.from_user.username
        if message.from_user
        else None
    )

    if username != ADMIN_USERNAME:

        await message.answer(
            "Ця технічна команда доступна лише адміністратору."
        )

        return False

    return True


# ============================================================
# 12. КОМАНДА START
# ============================================================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):

    if not await check_general_access(message):
        return

    user_histories[message.from_user.id] = []

    if message.from_user.username == ADMIN_USERNAME:

        await message.answer(
            "Я на зв'язку! 😉\n\n"
            "Тепер шукаю справжні фотографії через "
            "безкоштовні джерела Openverse та Wikimedia Commons."
        )

    else:

        await message.answer(
            "Привіт! Я Міссі — персональна асистентка Віталя."
        )


# ============================================================
# 13. ОТРИМАННЯ ФОТО ВІД КОРИСТУВАЧА
# ============================================================

@dp.message(F.photo)
async def handle_photo_message(message: types.Message):

    if not await check_general_access(message):
        return

    photo = message.photo[-1]

    async with memory_lock:

        memory = load_memory()

        memory["фото"].append({
            "file_id": photo.file_id,
            "description": message.caption or "Без підпису",
            "sender": message.from_user.first_name
        })

        memory["фото"] = memory["фото"][-50:]

        save_memory(memory)

    await message.answer(
        "Зберегла твоє фото у свою скарбничку! 😉"
    )


# ============================================================
# 14. ОБРОБКА ЗАПИТУ НА ФОТО
# ============================================================

async def process_photo_request(
    message: types.Message,
    user_text: str,
    user_id: int
):

    history = user_histories.setdefault(
        user_id,
        []
    )

    refined_query = await smart_refine_image_query(
        user_text,
        history
    )

    log_event(
        "IMAGE_SEARCH",
        f"Шукаю: {refined_query}"
    )

    # Перевірка кешу.
    async with memory_lock:
        memory = load_memory()
        image_cache = memory.get("image_cache", {})
        cached_file_id = image_cache.get(refined_query)

    if cached_file_id:

        try:
            await message.answer_photo(
                photo=cached_file_id,
                caption="Ось фото за твоїм запитом! 😉"
            )

            history.extend([
                {
                    "role": "user",
                    "content": user_text
                },
                {
                    "role": "assistant",
                    "content": "[Надіслано фото з кешу]"
                }
            ])

            history[:] = history[-MAX_HISTORY_LENGTH * 2:]
            return

        except Exception as error:

            log_event("IMAGE_CACHE_ERROR", str(error))

            async with memory_lock:
                memory = load_memory()
                memory.get("image_cache", {}).pop(
                    refined_query,
                    None
                )
                save_memory(memory)

    # Захист від одночасного пошуку однакового запиту.
    lock = image_search_locks.setdefault(
        refined_query,
        asyncio.Lock()
    )

    async with lock:

        try:
            result = await find_image_from_web(
                refined_query
            )

            if result:

                image_bytes, title = result

                photo_file = BufferedInputFile(
                    image_bytes,
                    filename="missy_photo.jpg"
                )

                sent_message = await message.answer_photo(
                    photo=photo_file,
                    caption="Ось що я знайшла! 😉"
                )

                if sent_message.photo:

                    file_id = sent_message.photo[-1].file_id

                    async with memory_lock:

                        memory = load_memory()

                        memory.setdefault(
                            "image_cache",
                            {}
                        )

                        memory["image_cache"][
                            refined_query
                        ] = file_id

                        save_memory(memory)

                history.extend([
                    {
                        "role": "user",
                        "content": user_text
                    },
                    {
                        "role": "assistant",
                        "content": f"[Надіслано фото: {title}]"
                    }
                ])

                history[:] = history[-MAX_HISTORY_LENGTH * 2:]
                return

        except Exception as error:

            log_event("PHOTO_ERROR", str(error))

    await message.answer(
        "Не знайшла відповідного справжнього фото "
        "у доступних безкоштовних джерелах. "
        "Спробуй уточнити назву або сформулювати запит інакше."
    )


# ============================================================
# 15. ГОЛОВНИЙ ОБРОБНИК ПОВІДОМЛЕНЬ
# ============================================================

@dp.message(F.text)
async def handle_text_message(message: types.Message):

    if not await check_general_access(message):
        return

    user_text = message.text.strip()

    if not user_text:
        return

    if user_text.startswith("/") and user_text != "/start":

        if not await check_admin_rights(message):
            return

    text_lower = user_text.lower()
    user_id = message.from_user.id

    history = user_histories.setdefault(
        user_id,
        []
    )

    # Збереження фактів.
    if (
        "запам'ятай" in text_lower
        or "запамятай" in text_lower
    ):

        async with memory_lock:

            memory = load_memory()

            memory["факти"].append(
                f"[{message.from_user.first_name}]: {user_text}"
            )

            memory["факти"] = memory["факти"][-200:]

            save_memory(memory)

    # Розпізнавання запитів на фото.
    action_words = (
        "надішли",
        "надіслати",
        "покажи",
        "показати",
        "скинь",
        "скинути",
        "дай",
        "пришли",
        "знайди",
        "знайти",
        "відправ",
        "відправити"
    )

    image_words = (
        "фото",
        "фотограф",
        "зображ",
        "картинк",
        "малюнок",
        "логотип",
        "лого",
        "світлин"
    )

    has_action = any(
        word in text_lower
        for word in action_words
    )

    has_image = any(
        word in text_lower
        for word in image_words
    )

    is_photo_request = has_action and has_image

    # Підтримка продовження розмови через займенники.
    pronoun_request = any(
        word in text_lower
        for word in (
            "її",
            "його",
            "цього",
            "цю",
            "цей",
            "нього",
            "неї"
        )
    )

    previous_context_has_image = any(
        any(
            word in item.get("content", "").lower()
            for word in image_words
        )
        for item in history[-4:]
    )

    if (
        has_action
        and pronoun_request
        and previous_context_has_image
    ):
        is_photo_request = True

    if is_photo_request:

        await process_photo_request(
            message,
            user_text,
            user_id
        )

        return

    # Звичайна розмова.
    skip_words = (
        "привіт",
        "як справи",
        "що робиш",
        "добридень",
        "бувай",
        "дякую"
    )

    is_greeting = (
        any(word in text_lower for word in skip_words)
        and len(text_lower.split()) < 4
    )

    search_context = ""

    if not is_greeting:

        search_results = await web_search(
            user_text
        )

        if search_results:

            search_context = (
                "\n[Результати пошуку в інтернеті]:\n"
                f"{search_results}\n"
            )

    if not client:

        await message.answer(
            "Для роботи мовної моделі потрібен GROQ_API_KEY. "
            "Перевір змінні середовища на хостингу."
        )

        return

    async with memory_lock:
        memory = load_memory()

    system_prompt = (
        "Ти — Міссі (Міссі Блек), "
        "персональна україномовна асистентка Віталя. "
        "Спілкуйся тепло, дружньо, неформально та природно. "
        "Враховуй контекст попередніх повідомлень. "
        "Не вигадуй факти, якщо інформації недостатньо. "
        "Ніколи не показуй користувачам технічні file_id "
        "або секретні ключі. "
        "Завжди відповідай українською, якщо користувач "
        "не попросив іншу мову. "
        f"База пам'яті: {json.dumps(memory, ensure_ascii=False)}"
        f"{search_context}"
    )

    history.append({
        "role": "user",
        "content": user_text
    })

    history[:] = history[-MAX_HISTORY_LENGTH * 2:]

    messages_payload = [
        {
            "role": "system",
            "content": system_prompt
        }
    ] + history

    reply_text = ""

    for attempt in range(2):

        try:

            response = await asyncio.to_thread(
                client.chat.completions.create,
                model="openai/gpt-oss-20b",
                messages=messages_payload,
                temperature=0.8
            )

            reply_text = (
                response.choices[0].message.content
                or "Не змогла сформувати відповідь."
            )

            break

        except Exception as error:

            log_event("GROQ_ERROR", str(error))

            if attempt == 1:

                reply_text = (
                    "Ой, сталася тимчасова помилка з'єднання. "
                    "Спробуй повторити запит трохи пізніше."
                )

            else:
                await asyncio.sleep(1)

    reply_text = sanitize_markdown(reply_text)

    history.append({
        "role": "assistant",
        "content": reply_text
    })

    history[:] = history[-MAX_HISTORY_LENGTH * 2:]

    await send_long_message(
        message,
        reply_text
    )


# ============================================================
# 16. ВЕБСЕРВЕР ДЛЯ RENDER
# ============================================================

async def handle_ping(request):

    return web.Response(
        text="Missy is alive!"
    )


async def start_web_server():

    app = web.Application()

    app.router.add_get(
        "/",
        handle_ping
    )

    runner = web.AppRunner(app)

    await runner.setup()

    port = int(
        os.environ.get("PORT", 10000)
    )

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        port
    )

    await site.start()

    log_event(
        "WEB_SERVER",
        f"Вебсервер запущено на порту {port}"
    )

    return runner


# ============================================================
# 17. ЗАПУСК БОТА
# ============================================================

async def main():

    web_runner = None

    try:

        # Видаляємо webhook перед запуском polling.
        await bot.delete_webhook(
            drop_pending_updates=True
        )

        web_runner = await start_web_server()

        log_event(
            "BOT",
            "МІССІ ЗАПУЩЕНА"
        )

        await dp.start_polling(
            bot,
            handle_signals=False
        )

    except TelegramConflictError:

        log_event(
            "TELEGRAM_CONFLICT",
            "Інший процес уже використовує цей токен. "
            "Зупини зайвий екземпляр бота."
        )

        raise

    except TelegramNetworkError as error:

        log_event(
            "TELEGRAM_NETWORK_ERROR",
            str(error)
        )

        raise

    finally:

        if web_runner:
            await web_runner.cleanup()

        await bot.session.close()


if __name__ == "__main__":

    asyncio.run(main())
