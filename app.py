import os
import json
import asyncio
import logging
from html.parser import HTMLParser
from typing import Optional

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import BufferedInputFile
from groq import Groq


# ============================================================
# НАЛАШТУВАННЯ
# ============================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

ADMIN_USERNAME = "E_n_gram"
ALLOWED_USERNAMES = {"E_n_gram", "MykhailoTa"}

MEMORY_FILE = "missy_memory.json"
MAX_HISTORY_LENGTH = 15
MAX_IMAGE_BYTES = 9 * 1024 * 1024
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=15, connect=7)

USER_AGENT = "MissyTelegramBot/1.0"

if not TELEGRAM_TOKEN:
    raise RuntimeError("Не задано змінну середовища TELEGRAM_TOKEN")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

user_histories: dict[int, list[dict[str, str]]] = {}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("missy")


def log_event(event_type: str, details: str):
    logger.info("[%s] %s", event_type, details)


# ============================================================
# ПАМ'ЯТЬ
# ============================================================

def load_memory() -> dict:
    if not os.path.exists(MEMORY_FILE):
        return {
            "image_cache": {},
            "фото": [],
            "факти": []
        }

    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, dict):
            return {
                "image_cache": {},
                "фото": [],
                "факти": []
            }

        data.setdefault("image_cache", {})
        data.setdefault("фото", [])
        data.setdefault("факти", [])

        if not isinstance(data["image_cache"], dict):
            data["image_cache"] = {}

        if not isinstance(data["фото"], list):
            data["фото"] = []

        if not isinstance(data["факти"], list):
            data["факти"] = []

        data["фото"] = data["фото"][-50:]

        return data

    except (OSError, json.JSONDecodeError) as error:
        log_event("MEMORY_ERROR", str(error))

        return {
            "image_cache": {},
            "фото": [],
            "факти": []
        }


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


# ============================================================
# ПОШУК ТЕКСТОВОЇ ІНФОРМАЦІЇ
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

            if any(
                name in classes
                for name in ("result-link", "result-snippet")
            ):
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
# УТОЧНЕННЯ ПОШУКОВОГО ЗАПИТУ
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
            f"Запит користувача: {user_prompt}\n"
            "Сформуй точний англомовний пошуковий запит "
            "для пошуку реального фото. "
            "Врахуй контекст і займенники. "
            "Не додавай вигаданих деталей. "
            "Поверни лише пошуковий запит англійською, "
            "без лапок і пояснень."
        )

        response = client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.2
        )

        refined = (
            response.choices[0].message.content or ""
        ).strip().strip("\"'")

        return refined[:300] if len(refined) >= 3 else user_prompt

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
# ПОШУК ФОТОГРАФІЙ
# БЕЗКОШТОВНІ ДЖЕРЕЛА:
# 1. Wikimedia Commons
# 2. Openverse
# ============================================================

async def search_wikimedia(
    session: aiohttp.ClientSession,
    query: str
) -> list[dict]:

    api_url = "https://commons.wikimedia.org/w/api.php"

    params = {
        "action": "query",
        "list": "search",
        "srnamespace": "6",
        "srsearch": query,
        "srlimit": "8",
        "format": "json"
    }

    try:
        async with session.get(
            api_url,
            params=params
        ) as response:

            response.raise_for_status()
            data = await response.json(content_type=None)

        candidates = []

        pages = data.get("query", {}).get("search", [])

        for page in pages:

            title = page.get("title", "")

            info_params = {
                "action": "query",
                "titles": title,
                "prop": "imageinfo",
                "iiprop": "url|mime|size",
                "iiurlwidth": "1400",
                "format": "json"
            }

            async with session.get(
                api_url,
                params=info_params
            ) as response:

                response.raise_for_status()
                info_data = await response.json(content_type=None)

            for item in info_data.get(
                "query", {}
            ).get("pages", {}).values():

                image_info = (
                    item.get("imageinfo") or [{}]
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
                        "title": title
                    })

        return candidates

    except Exception as error:
        log_event("WIKIMEDIA_SEARCH_ERROR", str(error))
        return []


async def search_openverse(
    session: aiohttp.ClientSession,
    query: str
) -> list[dict]:

    api_url = "https://api.openverse.org/v1/images/"

    params = {
        "q": query,
        "page_size": 8,
        "mature": "false"
    }

    try:
        async with session.get(
            api_url,
            params=params
        ) as response:

            response.raise_for_status()
            data = await response.json(content_type=None)

        candidates = []

        for item in data.get("results", []):

            image_url = (
                item.get("thumbnail")
                or item.get("url")
            )

            if image_url and image_url.startswith("https://"):

                candidates.append({
                    "url": image_url,
                    "title": item.get("title") or "Зображення з Openverse",
                    "source": "Openverse"
                })

        return candidates

    except Exception as error:
        log_event("OPENVERSE_SEARCH_ERROR", str(error))
        return []


# ============================================================
# ПЕРЕВІРКА СПРАВЖНОСТІ ЗОБРАЖЕННЯ
# ============================================================

def is_real_image(
    data: bytes,
    content_type: str
) -> bool:

    if not data:
        return False

    if len(data) < 500:
        return False

    if len(data) > MAX_IMAGE_BYTES:
        return False

    mime = content_type.split(";", 1)[0].strip().lower()

    if mime not in {
        "image/jpeg",
        "image/png",
        "image/webp"
    }:
        return False

    return (
        data.startswith(b"\xff\xd8\xff")
        or data.startswith(b"\x89PNG\r\n\x1a\n")
        or (
            data.startswith(b"RIFF")
            and data[8:12] == b"WEBP"
        )
    )


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

            content_type = response.headers.get(
                "Content-Type", ""
            )

            if not content_type.lower().startswith("image/"):
                return None

            content_length = response.headers.get(
                "Content-Length"
            )

            if (
                content_length
                and int(content_length) > MAX_IMAGE_BYTES
            ):
                return None

            data = await response.content.read(
                MAX_IMAGE_BYTES + 1
            )

            if is_real_image(data, content_type):
                return data

    except Exception as error:
        log_event("IMAGE_DOWNLOAD_ERROR", str(error))

    return None


# ============================================================
# ГОЛОВНА ФУНКЦІЯ ПОШУКУ ФОТО
# ============================================================

async def find_image_from_web(
    query: str
) -> Optional[tuple[bytes, str]]:

    headers = {
        "User-Agent": USER_AGENT
    }

    async with aiohttp.ClientSession(
        timeout=HTTP_TIMEOUT,
        headers=headers
    ) as session:

        sources = [
            search_wikimedia,
            search_openverse
        ]

        for source in sources:

            candidates = await source(session, query)

            for candidate in candidates:

                image_bytes = await download_image(
                    session,
                    candidate["url"]
                )

                if image_bytes:

                    title = candidate.get(
                        "title",
                        "Знайдене зображення"
                    )

                    log_event(
                        "IMAGE_FOUND",
                        f"{source.__name__}: {title}"
                    )

                    return image_bytes, title

    return None


# ============================================================
# ДОПОМІЖНІ ФУНКЦІЇ TELEGRAM
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
            "⚠️ Ця технічна команда доступна лише адміністратору."
        )

        return False

    return True


# ============================================================
# КОМАНДА START
# ============================================================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):

    if not await check_general_access(message):
        return

    user_histories[message.from_user.id] = []

    if message.from_user.username == ADMIN_USERNAME:

        await message.answer(
            "Я на зв'язку! 😉 "
            "Тепер шукаю справжні фотографії у Wikimedia Commons "
            "та Openverse й надсилаю їх прямо сюди."
        )

    else:

        await message.answer(
            "Привіт! Я Міссі — персональна асистентка Віталя."
        )


# ============================================================
# ОТРИМАННЯ ФОТО ВІД КОРИСТУВАЧА
# ============================================================

@dp.message(F.photo)
async def handle_photo_message(message: types.Message):

    if not await check_general_access(message):
        return

    photo = message.photo[-1]

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
# ОБРОБКА ЗАПИТУ НА ФОТО
# ============================================================

async def process_photo_request(
    message: types.Message,
    user_text: str,
    user_id: int
):

    history = user_histories.setdefault(user_id, [])

    refined_query = await smart_refine_image_query(
        user_text,
        history
    )

    memory = load_memory()

    image_cache = memory.get(
        "image_cache",
        {}
    )

    # Спочатку перевіряємо кеш Telegram.

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

            return

        except Exception as error:

            log_event("IMAGE_CACHE_ERROR", str(error))

            image_cache.pop(refined_query, None)

    # Якщо кешу немає — шукаємо в інтернеті.

    try:

        log_event(
            "IMAGE_SEARCH",
            f"Шукаю: {refined_query}"
        )

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

                image_cache[refined_query] = (
                    sent_message.photo[-1].file_id
                )

                memory["image_cache"] = image_cache

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

            return

    except Exception as error:

        log_event("PHOTO_ERROR", str(error))

    await message.answer(
        "Не знайшла відповідного справжнього фото "
        "у доступних безкоштовних джерелах. "
        "Спробуй уточнити назву або сформулювати запит інакше."
    )


# ============================================================
# ГОЛОВНИЙ ОБРОБНИК ПОВІДОМЛЕНЬ
# ============================================================

@dp.message(lambda message: message.text is not None)
async def handle_text_message(message: types.Message):

    if not await check_general_access(message):
        return

    user_text = message.text.strip()

    if not user_text:
        return

    if (
        user_text.startswith("/")
        and not user_text.startswith("/start")
    ):

        if not await check_admin_rights(message):
            return

    text_lower = user_text.lower()

    user_id = message.from_user.id

    memory = load_memory()

    # Збереження фактів у пам'ять.

    if (
        "запам'ятай" in text_lower
        or "запамятай" in text_lower
    ):

        memory["факти"].append(
            f"[{message.from_user.first_name}]: {user_text}"
        )

        memory["факти"] = memory["факти"][-200:]

        save_memory(memory)

    history = user_histories.setdefault(
        user_id,
        []
    )

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

    # Розпізнавання продовження запиту через займенники.

    pronoun_request = any(
        word in text_lower
        for word in (
            "її",
            "його",
            "цього",
            "цю",
            "цей"
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

    # Звичайне повідомлення.

    skip_words = [
        "привіт",
        "як справи",
        "що робиш",
        "добридень",
        "бувай",
        "дякую"
    ]

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
            "Додай ключ у змінні середовища хостингу."
        )

        return

    system_prompt = (
        "Ти — Міссі (Міссі Блек), "
        "персональна україномовна асистентка Віталя. "
        "Спілкуйся тепло, дружньо, неформально та природно. "
        "Враховуй контекст попередніх повідомлень. "
        "Ніколи не показуй користувачам технічні file_id "
        "або секретні ключі. "
        "Не вигадуй факти, якщо інформації недостатньо. "
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
                temperature=0.9
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
# ВЕБСЕРВЕР ДЛЯ ХОСТИНГУ
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
# ЗАПУСК МІССІ
# ============================================================

async def main():

    await bot.delete_webhook(
        drop_pending_updates=True
    )

    web_runner = await start_web_server()

    log_event(
        "BOT",
        "МІССІ ЗАПУЩЕНА"
    )

    try:

        await dp.start_polling(bot)

    finally:

        await web_runner.cleanup()

        await bot.session.close()


if __name__ == "__main__":

    asyncio.run(main())
