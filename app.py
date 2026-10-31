
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
CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID")
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN")
CLOUDFLARE_VISION_MODEL = "@cf/meta/llama-3.2-11b-vision-instruct"

ADMIN_USERNAME = "E_n_gram"
ALLOWED_USERNAMES = {"E_n_gram", "MykhailoTa"}
MEMORY_FILE = Path("missy_memory.json")
MAX_HISTORY = 15
MAX_IMAGE_BYTES = 8 * 1024 * 1024
IMAGE_SEARCH_TIMEOUT = 55
MAX_VISION_CANDIDATES = 2
VISION_TIMEOUT = aiohttp.ClientTimeout(total=18, connect=5, sock_read=14)
USER_AGENT = "MissyTelegramBot/2.0 (image search)"
TIMEOUT = aiohttp.ClientTimeout(total=8, connect=3, sock_read=6)

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


# Резервний словник для поширених українських запитів.
# Він використовується, якщо мовна модель не змогла перекласти запит.
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
    "собаки": "dog",
    "пес": "dog",
    "пса": "dog",
    "цуценя": "puppy",
    "цуценяти": "puppy",
    "птах": "bird",
    "птаха": "bird",
    "птахи": "birds",
    "кінь": "horse",
    "коня": "horse",
    "кінь": "horse",
    "кінь": "horse",
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
    "машина": "car",
    "машини": "car",
    "автомобіль": "car",
    "автомобіля": "car",
    "будинок": "house",
    "будинку": "house",
    "місто": "city",
    "міста": "city",
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
    "знайди", "знайти", "покажи", "надішли", "скинь", "пришли",
    "відшукай", "дай", "мені", "будь", "ласка", "фото", "фотографію",
    "фотографії", "картинку", "картин", "зображення", "зображенням",
    "будьласка", "мені", "прошу",
}


def fallback_english_query(text):
    """Перекладає відомі слова резервним словником, не вигадуючи невідомі."""
    words = re.findall(r"[a-zа-яіїєґ0-9]+", text.lower())
    translated = []

    for word in words:
        if word in UKRAINIAN_NON_SUBJECT_WORDS:
            continue
        if word in UKRAINIAN_IMAGE_WORDS:
            translated.append(UKRAINIAN_IMAGE_WORDS[word])
        elif re.fullmatch(r"[a-z0-9]+", word):
            translated.append(word)

    # Прибираємо дублікати зі збереженням порядку.
    translated = list(dict.fromkeys(translated))
    return " ".join(translated[:8])


def is_english_query(text):
    """Не дозволяє передати кириличний текст до англомовного пошуку."""
    if not text or not text.strip():
        return False
    if re.search(r"[а-яА-ЯіїєґІЇЄҐ]", text):
        return False
    return bool(re.search(r"[a-zA-Z]", text))


def refine_query(text, history):
    context = "\n".join(
        f"{m['role']}: {m['content']}" for m in history[-6:]
    )

    prompts = [
        (
            "Translate the user's image-search request into a short English query. "
            "The output MUST be in English only. Translate the subject, not the command. "
            "For example: 'Знайди фото кота' -> 'cat'. "
            "Return only 2 to 8 English search words, without quotes or explanation.\n"
            f"Conversation context:\n{context}\n"
            f"User request: {text}"
        ),
        (
            "Your previous translation was not usable. "
            "Return ONLY an English image search query. "
            "Do not use Ukrainian, Russian, or any Cyrillic characters. "
            "Translate the actual subject into English. "
            "Example: 'Знайди фото кота' means 'cat'.\n"
            f"Request: {text}"
        ),
    ]

    for attempt, prompt in enumerate(prompts, start=1):
        try:
            result = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=40,
            ).choices[0].message.content or ""

            result = re.sub(r"\s+", " ", result.strip().strip("\"'`"))
            result = re.sub(r"^(query|search query)\s*:\s*", "", result, flags=re.I)
            result = result.strip(" .,:;")

            if is_english_query(result) and 1 < len(result) <= 120:
                event(
                    "QUERY_TRANSLATION_SUCCESS",
                    f"attempt={attempt}; query={result!r}",
                )
                return result

            event(
                "QUERY_TRANSLATION_REJECTED",
                f"attempt={attempt}; result={result[:100]!r}",
            )

        except Exception as exc:
            event(
                "QUERY_REFINEMENT_ERROR",
                f"attempt={attempt}; {type(exc).__name__}: {exc}",
            )

    fallback = fallback_english_query(text)
    if fallback:
        event("QUERY_FALLBACK_USED", f"query={fallback!r}")
        return fallback

    event("QUERY_TRANSLATION_FAILED", f"Could not translate: {text[:120]!r}")
    return None


def terms(text):
    stop = {
        "the", "a", "an", "photo", "picture", "image", "of",
        "with", "and", "real", "find", "show",
    }
    return {
        w for w in re.findall(r"[a-zа-яіїєґ0-9]+", text.lower())
        if len(w) > 2 and w not in stop
    }


def score_result(query, title, tags=None):
    searchable = (title or "").lower()
    if tags:
        searchable += " " + " ".join(
            str(t.get("name", "")) if isinstance(t, dict) else str(t)
            for t in tags
        ).lower()
    return len(
        terms(query) &
        set(re.findall(r"[a-zа-яіїєґ0-9]+", searchable))
    )


async def request_json(session, url, params=None, headers=None):
    async with session.get(url, params=params, headers=headers) as response:
        if response.status == 429:
            raise RuntimeError(
                f"HTTP 429 (Retry-After={response.headers.get('Retry-After', '?')})"
            )
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
            raise RuntimeError(
                f"Openverse auth HTTP {response.status}: "
                f"{(await response.text())[:180]}"
            )
        return (await response.json()).get("access_token")


async def search_openverse(session, query):
    headers = {"User-Agent": USER_AGENT}
    stage = "auth"

    try:
        token = await openverse_auth(session)
        if token:
            headers["Authorization"] = f"Bearer {token}"

        stage = "image_search"
        payload = await request_json(
            session,
            "https://api.openverse.org/v1/images/",
            {"q": query, "page_size": 12},
            headers,
        )

        results = payload.get("results", [])
        event(
            "OPENVERSE_RAW_RESULTS",
            f"{len(results)} results for {query!r}",
        )

        results.sort(
            key=lambda x: score_result(
                query, x.get("title", ""), x.get("tags")
            ),
            reverse=True,
        )

        candidates = []
        seen = set()

        for item in results:
            url = item.get("url") or item.get("thumbnail")
            points = score_result(
                query, item.get("title", ""), item.get("tags")
            )

            if url and url not in seen and points >= 1:
                seen.add(url)
                candidates.append({
                    "url": url,
                    "title": item.get("title") or query,
                    "source": "Openverse",
                })
                if len(candidates) >= 2:
                    break

        event(
            "IMAGE_CANDIDATES",
            f"Openverse: {len(candidates)} candidates from {len(results)} results",
        )
        return candidates

    except Exception as exc:
        event(
            "OPENVERSE_ERROR",
            f"stage={stage}; {type(exc).__name__}: {exc}",
        )
        return []


async def search_wikimedia(session, query):
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": f"filetype:bitmap {query}",
        "gsrnamespace": 6,
        "gsrlimit": 12,
        "prop": "imageinfo",
        "iiprop": "url",
        "iiurlwidth": 1200,
        "format": "json",
    }

    try:
        payload = await request_json(
            session,
            "https://commons.wikimedia.org/w/api.php",
            params,
            {"User-Agent": USER_AGENT},
        )

        pages = list(payload.get("query", {}).get("pages", {}).values())
        ranked = []
        rejected = []

        for page in pages:
            title = page.get("title", "")
            info = (page.get("imageinfo") or [{}])[0]
            url = info.get("thumburl") or info.get("url")
            points = score_result(query, title)

            if url and points >= 1:
                ranked.append((points, title, url))
            elif len(rejected) < 8:
                rejected.append(
                    f"{title!r} (score={points}, "
                    f"url={'yes' if url else 'no'})"
                )

        event(
            "WIKIMEDIA_DIAGNOSTIC",
            f"query={query!r}; raw={len(pages)}; "
            f"matched={len(ranked)}; "
            f"rejected_sample={'; '.join(rejected) or 'none'}",
        )

        ranked.sort(reverse=True)

        candidates = [
            {
                "url": url,
                "title": title,
                "source": "Wikimedia Commons",
            }
            for points, title, url in ranked[:2]
        ]

        event(
            "IMAGE_CANDIDATES",
            f"Wikimedia Commons: {len(candidates)} candidates",
        )
        return candidates

    except Exception as exc:
        event("WIKIMEDIA_ERROR", f"{type(exc).__name__}: {exc}")
        return []


async def download_image(session, candidate):
    try:
        async with session.get(
            candidate["url"],
            headers={"User-Agent": USER_AGENT},
        ) as response:
            if response.status != 200:
                raise RuntimeError(
                    f"image download HTTP {response.status}"
                )

            content_type = response.headers.get("Content-Type", "")
            if not content_type.lower().startswith("image/"):
                raise RuntimeError(
                    f"not an image content-type: {content_type}"
                )

            data = await response.content.read(MAX_IMAGE_BYTES + 1)

        if not data or len(data) > MAX_IMAGE_BYTES:
            raise RuntimeError("image empty or larger than 8 MB")

        with Image.open(BytesIO(data)) as img:
            img.verify()

        with Image.open(BytesIO(data)) as img:
            if (
                img.format not in {"JPEG", "PNG", "WEBP"}
                or img.width < 120
                or img.height < 120
            ):
                raise RuntimeError("unsupported or too-small image")

            ext = {
                "JPEG": "jpg",
                "PNG": "png",
                "WEBP": "webp",
            }[img.format]

        return data, ext

    except (UnidentifiedImageError, OSError, ValueError, RuntimeError) as exc:
        event("IMAGE_REJECTED", str(exc))
    except Exception as exc:
        event(
            "IMAGE_DOWNLOAD_ERROR",
            f"{type(exc).__name__}: {exc}",
        )

    return None


async def verify_image_with_cloudflare(data, query):
    if not CLOUDFLARE_ACCOUNT_ID or not CLOUDFLARE_API_TOKEN:
        event(
            "VISION_NOT_CONFIGURED",
            "Cloudflare account ID or API token is missing",
        )
        return False

    try:
        with Image.open(BytesIO(data)) as source:
            image = source.convert("RGB")
            image.thumbnail((1024, 1024))
            buffer = BytesIO()
            image.save(
                buffer,
                format="JPEG",
                quality=85,
                optimize=True,
            )
            image_data = (
                "data:image/jpeg;base64,"
                + base64.b64encode(buffer.getvalue()).decode("ascii")
            )

        url = (
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{CLOUDFLARE_ACCOUNT_ID}/ai/run/{CLOUDFLARE_VISION_MODEL}"
        )

        prompt = (
            "Check whether the image visibly depicts the requested subject. "
            "Judge only the actual visual content, not the filename, title, or source. "
            "Be strict: if the requested subject is absent, ambiguous, or only loosely related, reject it. "
            "Return exactly one JSON object and nothing else: "
            '{"relevant": true} or {"relevant": false}.\n'
            f"Requested subject: {query}"
        )

        async with aiohttp.ClientSession(timeout=VISION_TIMEOUT) as session:
            async with session.post(
                url,
                headers={
                    "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
                    "Content-Type": "application/json",
                },
                json={
                    "prompt": prompt,
                    "image": image_data,
                    "temperature": 0,
                    "max_tokens": 80,
                },
            ) as response:
                body = await response.json(content_type=None)

                if response.status >= 400 or not body.get("success", False):
                    errors = body.get("errors", [])
                    event(
                        "VISION_API_ERROR",
                        f"HTTP {response.status}: {str(errors)[:300]}",
                    )
                    return False

        result = body.get("result") or {}
        answer = (
            result.get("response", "")
            if isinstance(result, dict)
            else str(result)
        )

        match = re.search(
            r"\{\s*['\"]?relevant['\"]?\s*:\s*(true|false)\s*\}",
            answer,
            re.I,
        )

        if not match:
            event("VISION_UNCLEAR", answer[:250])
            return False

        relevant = match.group(1).lower() == "true"
        event(
            "VISION_RESULT",
            "relevant" if relevant else "rejected",
        )
        return relevant

    except asyncio.TimeoutError:
        event(
            "VISION_TIMEOUT",
            "Cloudflare Vision request timed out",
        )
    except Exception as exc:
        event(
            "VISION_ERROR",
            f"{type(exc).__name__}: {exc}",
        )

    return False


async def find_image(query):
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        candidates = []

        for searcher in (search_openverse, search_wikimedia):
            candidates.extend(await searcher(session, query))

        checked = 0

        for candidate in candidates:
            if checked >= MAX_VISION_CANDIDATES:
                break

            downloaded = await download_image(session, candidate)
            if not downloaded:
                continue

            data, ext = downloaded
            checked += 1

            if await verify_image_with_cloudflare(data, query):
                event(
                    "IMAGE_VERIFIED",
                    f"{candidate['source']}: {candidate['title'][:120]}",
                )
                return data, ext, candidate

            event(
                "IMAGE_VISION_REJECTED",
                candidate["title"][:160],
            )

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
                    self.items.append({
                        "title": value,
                        "snippet": "",
                    })
                elif self.items:
                    self.items[-1]["snippet"] = value

            self._capture = None
            self._buffer = ""


async def web_search(query):
    """Короткий вебпошук для звичайних текстових запитів."""

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=4)
        ) as session:
            url = "https://html.duckduckgo.com/html/"

            async with session.get(
                url,
                params={"q": query},
                headers={"User-Agent": USER_AGENT},
            ) as response:
                if response.status != 200:
                    event(
                        "WEB_SEARCH_ERROR",
                        f"DuckDuckGo HTTP {response.status}",
                    )
                    return ""

                html_text = await response.text()

        parser = SearchResultsParser()
        parser.feed(html_text)

        lines = []
        for item in parser.items[:3]:
            lines.append(
                item["title"]
                + (": " + item["snippet"] if item["snippet"] else "")
            )

        return "\n".join(lines)

    except Exception as exc:
        event(
            "WEB_SEARCH_ERROR",
            f"{type(exc).__name__}: {exc}",
        )
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
    await message.answer(
        "Я на зв'язку! Пошук справжніх фотографій оновлено. 😉"
    )


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
    await message.answer(
        "Зберегла твоє фото у свою скарбничку! 😉"
    )


@dp.message(F.text)
async def text_handler(message: types.Message):
    if not await check_access(message):
        return

    text = message.text.strip()

    if (
        text.startswith("/")
        and text != "/start"
        and message.from_user.username != ADMIN_USERNAME
    ):
        await message.answer(
            "⚠️ Ця технічна команда доступна лише адміністратору."
        )
        return

    uid = message.from_user.id
    history = histories.setdefault(uid, [])
    memory = load_memory()

    if "запам'ятай" in text.lower():
        def add_fact(data):
            data["факти"].append(
                f"[{message.from_user.first_name}]: {text}"
            )
            data["факти"] = data["факти"][-100:]

        await change_memory(add_fact)
        memory = load_memory()

    if image_request(text):
        await message.bot.send_chat_action(
            message.chat.id,
            "upload_photo",
        )

        try:
            query = await asyncio.wait_for(
                asyncio.to_thread(
                    refine_query,
                    text,
                    history.copy(),
                ),
                timeout=18,
            )
        except asyncio.TimeoutError:
            query = fallback_english_query(text)
            event(
                "QUERY_REFINEMENT_TIMEOUT",
                f"Fallback query={query!r}",
            )

        if not query:
            await message.answer(
                "Не змогла перекласти запит для пошуку. "
                "Спробуй назвати предмет простіше, наприклад: "
                "«фото кота» або «фото собаки»."
            )
            return

        if not is_english_query(query):
            event(
                "QUERY_LANGUAGE_GUARD",
                f"Rejected non-English query: {query!r}",
            )
            query = fallback_english_query(text)

        if not query:
            await message.answer(
                "Не змогла підготувати англійський пошуковий запит. "
                "Спробуй трохи інакше сформулювати, що саме потрібно знайти."
            )
            return

        # Старий кеш без візуальної перевірки не використовується.
        # Нові записи мають окремий префікс vision-v1.
        cache_key = "vision-v1:" + query
        cached = memory.get("image_cache", {}).get(cache_key)

        if cached:
            try:
                await message.answer_photo(
                    cached,
                    caption="Ось перевірене фото за твоїм запитом! 😉",
                )

                history.extend([
                    {"role": "user", "content": text},
                    {
                        "role": "assistant",
                        "content": "[Перевірене фото з кешу]",
                    },
                ])
                return

            except Exception as exc:
                event("IMAGE_CACHE_ERROR", str(exc))

        event(
            "QUERY_REFINED",
            f"original={text!r}; refined={query!r}",
        )
        event("IMAGE_SEARCH", repr(query))

        try:
            found = await asyncio.wait_for(
                find_image(query),
                timeout=IMAGE_SEARCH_TIMEOUT,
            )
        except asyncio.TimeoutError:
            found = None
            event(
                "IMAGE_SEARCH_TIMEOUT",
                f"Search exceeded {IMAGE_SEARCH_TIMEOUT}s",
            )

        if found:
            data, ext, candidate = found

            try:
                sent = await message.answer_photo(
                    BufferedInputFile(
                        data,
                        filename=f"missy_photo.{ext}",
                    ),
                    caption=f"Ось що знайшла: {candidate['title'][:180]}",
                )

                if sent.photo:
                    def cache(data_obj):
                        data_obj["image_cache"][cache_key] = (
                            sent.photo[-1].file_id
                        )

                        if len(data_obj["image_cache"]) > 100:
                            data_obj["image_cache"].pop(
                                next(iter(data_obj["image_cache"]))
                            )

                    await change_memory(cache)

                history.extend([
                    {"role": "user", "content": text},
                    {
                        "role": "assistant",
                        "content": "[Надіслано перевірене фото]",
                    },
                ])
                return

            except Exception as exc:
                event(
                    "TELEGRAM_PHOTO_SEND_ERROR",
                    f"{type(exc).__name__}: {exc}",
                )

        await message.answer(
            "Не знайшла фото, яке змогла підтвердити візуально. "
            "Спробуй уточнити запит або сформулювати його інакше."
        )

        history.extend([
            {"role": "user", "content": text},
            {
                "role": "assistant",
                "content": "[Фото не знайдено або не пройшло перевірку]",
            },
        ])
        return

    history.append({"role": "user", "content": text})
    history[:] = history[-MAX_HISTORY * 2:]

    search_context = ""
    greeting_words = (
        "привіт", "як справи", "що робиш",
        "добридень", "бувай", "дякую",
    )

    is_greeting = (
        any(word in text.lower() for word in greeting_words)
        and len(text.split()) < 4
    )

    if not is_greeting:
        search_results = await web_search(text)
        if search_results:
            search_context = (
                f"\nРезультати вебпошуку (перевір їхню доречність):\n"
                f"{search_results}"
            )

    system = (
        "Ти — Міссі (Міссі Блек), тепла, дружня україномовна асистентка Віталя. "
        "Враховуй історію та базу пам'яті. Не показуй технічні токени чи file_id.\n"
        f"База пам'яті: {json.dumps(memory, ensure_ascii=False)[:12000]}"
        f"{search_context}"
    )

    payload = [{"role": "system", "content": system}] + history

    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(
                groq_client.chat.completions.create,
                model=GROQ_MODEL,
                messages=payload,
                temperature=0.8,
                max_tokens=900,
            ),
            timeout=10,
        )

        answer = (
            response.choices[0].message.content
            or "Не змогла сформувати відповідь."
        )

    except Exception as exc:
        event(
            "GROQ_ERROR",
            f"{type(exc).__name__}: {exc}",
        )
        answer = (
            "Зараз не можу зв'язатися з мовною моделлю. "
            "Спробуй трохи пізніше."
        )

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
    await web.TCPSite(
        runner,
        "0.0.0.0",
        port,
    ).start()

    event("WEB_SERVER", f"Listening on port {port}")
    return runner


async def main():
    runner = None

    try:
        await bot.delete_webhook(drop_pending_updates=False)
        runner = await start_web()
        event("BOT", "MISSY STARTED")

        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )

    finally:
        if runner:
            await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
