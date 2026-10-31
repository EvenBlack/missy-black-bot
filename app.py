import os
import re
import json
import asyncio
import logging
from html.parser import HTMLParser
from urllib.parse import urlencode
from io import BytesIO
from pathlib import Path

import aiohttp
from aiohttp import web
from PIL import Image, UnidentifiedImageError
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import BufferedInputFile
from groq import Groq

# Render Environment Variables
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
OPENVERSE_CLIENT_ID = os.getenv("OPENVERSE_CLIENT_ID")
OPENVERSE_CLIENT_SECRET = os.getenv("OPENVERSE_CLIENT_SECRET")

ADMIN_USERNAME = "E_n_gram"
ALLOWED_USERNAMES = {"E_n_gram", "MykhailoTa"}
MEMORY_FILE = Path("missy_memory.json")
MAX_HISTORY = 15
MAX_IMAGE_BYTES = 8 * 1024 * 1024
IMAGE_SEARCH_TIMEOUT = 14
USER_AGENT = "MissyTelegramBot/2.0 (image search)"
TIMEOUT = aiohttp.ClientTimeout(total=5, connect=3, sock_read=4)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s:%(name)s:%(message)s")
log = logging.getLogger("missy")

if not TELEGRAM_TOKEN:
    raise RuntimeError("Не задано TELEGRAM_TOKEN у Render Environment")
if not GROQ_API_KEY:
    raise RuntimeError("Не задано GROQ_API_KEY у Render Environment")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
groq_client = Groq(api_key=GROQ_API_KEY, timeout=8.0, max_retries=0)
histories = {}
memory_lock = asyncio.Lock()


def event(name, detail):
    log.info("[%s] %s", name, detail)


def load_memory():
    if not MEMORY_FILE.exists():
        return {"image_cache": {}, "фото": [], "факти": []}
    try:
        with MEMORY_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("JSON root is not an object")
        for key, default in (("image_cache", {}), ("фото", []), ("факти", [])):
            if not isinstance(data.get(key), type(default)):
                data[key] = default
        return data
    except Exception:
        log.exception("Не вдалося прочитати missy_memory.json; використовую порожню пам'ять")
        return {"image_cache": {}, "фото": [], "факти": []}


def save_memory(data):
    tmp = MEMORY_FILE.with_suffix(".json.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
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


def has_access(message):
    return bool(message.from_user and message.from_user.username in ALLOWED_USERNAMES)


async def check_access(message):
    if not has_access(message):
        await message.answer("Ой, вибач, але доступ до мене суворо обмежений.")
        return False
    return True


def image_request(text):
    low = text.lower()
    actions = ("надішли", "покажи", "скинь", "дай", "пришли", "знайди", "відшукай")
    objects = ("фото", "фотограф", "картин", "зображен", "лого", "логотип")
    return any(x in low for x in actions) and any(x in low for x in objects)


def refine_query(text, history):
    try:
        context = "\n".join(f"{m['role']}: {m['content']}" for m in history[-6:])
        prompt = (
            "Convert this request into a short precise English image search query. "
            "Preserve the exact subject and details. Return only the query.\n"
            f"Context:\n{context}\nRequest: {text}"
        )
        result = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=40,
        ).choices[0].message.content or ""
        result = re.sub(r"\s+", " ", result.strip().strip("\"'"))
        if 2 < len(result) <= 120:
            return result
    except Exception as exc:
        event("QUERY_REFINEMENT_ERROR", f"{type(exc).__name__}: {exc}")
    return text[:120]


def terms(text):
    stop = {"the", "a", "an", "photo", "picture", "image", "of", "with", "and", "real"}
    return {w for w in re.findall(r"[a-zа-яіїєґ0-9]+", text.lower()) if len(w) > 2 and w not in stop}


def score_result(query, title, tags=None):
    searchable = (title or "").lower()
    if tags:
        searchable += " " + " ".join(
            str(t.get("name", "")) if isinstance(t, dict) else str(t) for t in tags
        ).lower()
    return len(terms(query) & set(re.findall(r"[a-zа-яіїєґ0-9]+", searchable)))


async def request_json(session, url, params=None, headers=None):
    async with session.get(url, params=params, headers=headers) as response:
        if response.status == 429:
            raise RuntimeError(f"HTTP 429 (Retry-After={response.headers.get('Retry-After', '?')})")
        if response.status >= 400:
            body = (await response.text())[:180]
            raise RuntimeError(f"HTTP {response.status}: {body}")
        return await response.json(content_type=None)


async def openverse_auth(session):
    if not (OPENVERSE_CLIENT_ID and OPENVERSE_CLIENT_SECRET):
        return None
    url = "https://api.openverse.org/v1/auth_tokens/token/"
    form = {
        "grant_type": "client_credentials",
        "client_id": OPENVERSE_CLIENT_ID,
        "client_secret": OPENVERSE_CLIENT_SECRET,
    }
    async with session.post(url, data=form) as response:
        if response.status >= 400:
            raise RuntimeError(f"Openverse auth HTTP {response.status}: {(await response.text())[:180]}")
        return (await response.json()).get("access_token")


async def search_openverse(session, query):
    headers = {"User-Agent": USER_AGENT}
    try:
        token = await openverse_auth(session)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        payload = await request_json(
            session,
            "https://api.openverse.org/v1/images/",
            {"q": query, "page_size": 10},
            headers,
        )
        results = payload.get("results", [])
        results.sort(
            key=lambda x: score_result(query, x.get("title", ""), x.get("tags")),
            reverse=True,
        )
        for item in results:
            if score_result(query, item.get("title", ""), item.get("tags")) >= 1:
                url = item.get("url") or item.get("thumbnail")
                if url:
                    return {"url": url, "title": item.get("title") or query, "source": "Openverse"}
        event("IMAGE_CANDIDATES", f"Openverse: 0 relevant from {len(results)} results")
    except Exception as exc:
        event("OPENVERSE_ERROR", f"{type(exc).__name__}: {exc}")
    return None


async def search_wikimedia(session, query):
    params = {
        "action": "query", "generator": "search",
        "gsrsearch": f"filetype:bitmap {query}", "gsrnamespace": 6,
        "gsrlimit": 12, "prop": "imageinfo", "iiprop": "url",
        "iiurlwidth": 1400, "format": "json",
    }
    try:
        payload = await request_json(
            session, "https://commons.wikimedia.org/w/api.php",
            params, {"User-Agent": USER_AGENT},
        )
        pages = payload.get("query", {}).get("pages", {}).values()
        candidates = []
        for page in pages:
            title = page.get("title", "")
            info = (page.get("imageinfo") or [{}])[0]
            url = info.get("thumburl") or info.get("url")
            points = score_result(query, title)
            if url and points >= 1:
                candidates.append((points, title, url))
        if candidates:
            points, title, url = sorted(candidates, reverse=True)[0]
            return {"url": url, "title": title, "source": "Wikimedia Commons"}
        event("IMAGE_CANDIDATES", "Wikimedia Commons: 0 relevant results")
    except Exception as exc:
        event("WIKIMEDIA_ERROR", f"{type(exc).__name__}: {exc}")
    return None


async def download_image(session, candidate):
    try:
        async with session.get(candidate["url"], headers={"User-Agent": USER_AGENT}) as response:
            if response.status != 200:
                raise RuntimeError(f"image download HTTP {response.status}")
            content_type = response.headers.get("Content-Type", "")
            if not content_type.lower().startswith("image/"):
                raise RuntimeError(f"not an image content-type: {content_type}")
            data = await response.content.read(MAX_IMAGE_BYTES + 1)
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise RuntimeError("image empty or larger than 8 MB")
        with Image.open(BytesIO(data)) as img:
            img.verify()
        with Image.open(BytesIO(data)) as img:
            if img.format not in {"JPEG", "PNG", "WEBP"} or img.width < 120 or img.height < 120:
                raise RuntimeError("unsupported or too-small image")
            ext = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}[img.format]
        return data, ext
    except (UnidentifiedImageError, OSError, ValueError, RuntimeError) as exc:
        event("IMAGE_REJECTED", str(exc))
    except Exception as exc:
        event("IMAGE_DOWNLOAD_ERROR", f"{type(exc).__name__}: {exc}")
    return None


async def find_image(query):
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        # Не опитуємо одне джерело багато разів: максимум один запит до кожного.
        for searcher in (search_openverse, search_wikimedia):
            candidate = await searcher(session, query)
            if candidate:
                downloaded = await download_image(session, candidate)
                if downloaded:
                    data, ext = downloaded
                    return data, ext, candidate
    return None



class SearchResultsParser(HTMLParser):
    """Витягує заголовки та короткі описи з HTML-сторінки DuckDuckGo."""
    def __init__(self):
        super().__init__()
        self.items = []
        self._capture = None
        self._buffer = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "")
        if tag == "a" and "result__a" in classes:
            self._capture = "title"
            self._buffer = ""
        elif tag == "a" and "result__snippet" in classes:
            self._capture = "snippet"
            self._buffer = ""

    def handle_data(self, data):
        if self._capture:
            self._buffer += data

    def handle_endtag(self, tag):
        if tag == "a" and self._capture:
            value = " ".join(self._buffer.split())
            if value:
                if self._capture == "title":
                    self.items.append({"title": value, "snippet": ""})
                elif self.items:
                    self.items[-1]["snippet"] = value
            self._capture = None
            self._buffer = ""


async def web_search(query):
    """Короткий вебпошук для звичайних текстових запитів; помилка не блокує чат."""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4)) as session:
            url = "https://html.duckduckgo.com/html/"
            async with session.get(
                url,
                params={"q": query},
                headers={"User-Agent": USER_AGENT},
            ) as response:
                if response.status != 200:
                    event("WEB_SEARCH_ERROR", f"DuckDuckGo HTTP {response.status}")
                    return ""
                html_text = await response.text()
        parser = SearchResultsParser()
        parser.feed(html_text)
        lines = []
        for item in parser.items[:3]:
            lines.append(item["title"] + (": " + item["snippet"] if item["snippet"] else ""))
        return "\n".join(lines)
    except Exception as exc:
        event("WEB_SEARCH_ERROR", f"{type(exc).__name__}: {exc}")
        return ""

def clean_markdown(text):
    if text.count("*") % 2:
        text = text.replace("*", "")
    if text.count("_") % 2:
        text = text.replace("_", "")
    return text


async def send_long(message, text):
    for i in range(0, len(text), 4000):
        await message.answer(text[i:i + 4000])


@dp.message(Command("start"))
async def start_command(message: types.Message):
    if not await check_access(message):
        return
    histories[message.from_user.id] = []
    await message.answer("Я на зв'язку! Пошук справжніх фотографій оновлено. 😉")


@dp.message(F.photo)
async def receive_photo(message: types.Message):
    if not await check_access(message):
        return
    photo = message.photo[-1]
    def add(data):
        data["фото"].append({
            "file_id": photo.file_id,
            "description": message.caption or "Без підпису",
            "sender": message.from_user.first_name,
        })
        data["фото"] = data["фото"][-50:]
    await change_memory(add)
    await message.answer("Зберегла твоє фото у свою скарбничку! 😉")


@dp.message(F.text)
async def text_handler(message: types.Message):
    if not await check_access(message):
        return
    text = message.text.strip()
    if text.startswith("/") and text != "/start" and message.from_user.username != ADMIN_USERNAME:
        await message.answer("⚠️ Ця технічна команда доступна лише адміністратору.")
        return

    uid = message.from_user.id
    history = histories.setdefault(uid, [])
    memory = load_memory()

    if "запам'ятай" in text.lower():
        def add_fact(data):
            data["факти"].append(f"[{message.from_user.first_name}]: {text}")
            data["факти"] = data["факти"][-100:]
        await change_memory(add_fact)
        memory = load_memory()

    if image_request(text):
        await message.bot.send_chat_action(message.chat.id, "upload_photo")
        try:
            query = await asyncio.wait_for(
                asyncio.to_thread(refine_query, text, history.copy()), timeout=9
            )
        except asyncio.TimeoutError:
            query = text
            event("QUERY_REFINEMENT_TIMEOUT", "Using original query")

        cached = memory.get("image_cache", {}).get(query)
        if cached:
            try:
                await message.answer_photo(cached, caption="Ось фото за твоїм запитом! 😉")
                history.extend([
                    {"role": "user", "content": text},
                    {"role": "assistant", "content": "[Фото з кешу]"},
                ])
                return
            except Exception as exc:
                event("IMAGE_CACHE_ERROR", str(exc))

        event("IMAGE_SEARCH", repr(query))
        try:
            found = await asyncio.wait_for(find_image(query), timeout=IMAGE_SEARCH_TIMEOUT)
        except asyncio.TimeoutError:
            found = None
            event("IMAGE_SEARCH_TIMEOUT", f"Search exceeded {IMAGE_SEARCH_TIMEOUT}s")

        if found:
            data, ext, candidate = found
            try:
                sent = await message.answer_photo(
                    BufferedInputFile(data, filename=f"missy_photo.{ext}"),
                    caption=f"Ось що знайшла: {candidate['title'][:180]}",
                )
                if sent.photo:
                    def cache(data_obj):
                        data_obj["image_cache"][query] = sent.photo[-1].file_id
                        if len(data_obj["image_cache"]) > 100:
                            data_obj["image_cache"].pop(next(iter(data_obj["image_cache"])))
                    await change_memory(cache)
                history.extend([
                    {"role": "user", "content": text},
                    {"role": "assistant", "content": "[Надіслано справжнє фото]"},
                ])
                return
            except Exception as exc:
                event("TELEGRAM_PHOTO_SEND_ERROR", f"{type(exc).__name__}: {exc}")

        await message.answer(
            "Не знайшла відповідного справжнього фото у доступних безкоштовних джерелах. "
            "Спробуй уточнити запит або сформулювати його інакше."
        )
        history.extend([
            {"role": "user", "content": text},
            {"role": "assistant", "content": "[Фото не знайдено]"},
        ])
        return

    history.append({"role": "user", "content": text})
    history[:] = history[-MAX_HISTORY * 2:]
    search_context = ""
    greeting_words = ("привіт", "як справи", "що робиш", "добридень", "бувай", "дякую")
    is_greeting = any(word in text.lower() for word in greeting_words) and len(text.split()) < 4
    if not is_greeting:
        search_results = await web_search(text)
        if search_results:
            search_context = f"\\nРезультати вебпошуку (перевір їхню доречність):\\n{search_results}"
    system = (
        "Ти — Міссі (Міссі Блек), тепла, дружня україномовна асистентка Віталя. "
        "Враховуй історію та базу пам'яті. Не показуй технічні токени чи file_id.\\n"
        f"База пам'яті: {json.dumps(memory, ensure_ascii=False)[:12000]}"
        f"{search_context}"
    )
    payload = [{"role": "system", "content": system}] + history
    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(
                groq_client.chat.completions.create,
                model=GROQ_MODEL, messages=payload, temperature=0.8, max_tokens=900,
            ),
            timeout=10,
        )
        answer = response.choices[0].message.content or "Не змогла сформувати відповідь."
    except Exception as exc:
        event("GROQ_ERROR", f"{type(exc).__name__}: {exc}")
        answer = "Зараз не можу зв'язатися з мовною моделлю. Спробуй трохи пізніше."
    answer = clean_markdown(answer)
    history.append({"role": "assistant", "content": answer})
    history[:] = history[-MAX_HISTORY * 2:]
    await send_long(message, answer)


async def ping(request):
    return web.Response(text="Missy is alive")


async def start_web():
    app = web.Application()
    app.router.add_get("/", ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "10000"))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    event("WEB_SERVER", f"Listening on port {port}")
    return runner


async def main():
    runner = None
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        runner = await start_web()
        event("BOT", "MISSY STARTED")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        if runner:
            await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
