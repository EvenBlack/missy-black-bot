import os
import json
import asyncio
import urllib.request
import urllib.parse
from html.parser import HTMLParser
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import BufferedInputFile
from groq import Groq

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

ADMIN_USERNAME = "E_n_gram"
ALLOWED_USERNAMES = ["E_n_gram", "MykhailoTa"]

bot = Bot(token=TELEGRAM_TOKEN)
client = Groq(api_key=GROQ_API_KEY)
dp = Dispatcher()

MEMORY_FILE = "missy_memory.json"
user_histories = {}
MAX_HISTORY_LENGTH = 15

def log_event(event_type: str, details: str):
    print(f"[MISSY LOG] [{event_type}] {details}")

def load_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "фото" in data and len(data["фото"]) > 50:
                    data["фото"] = data["фото"][-50:]
                return data
        except Exception:
            return {}
    return {}

def save_memory(memory_data):
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(memory_data, f, ensure_ascii=False, indent=4)
    except Exception:
        pass

class DDGLiteParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results = []
        self.in_result = False
        self.current_text = ""

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            for name, value in attrs:
                if name == 'class' and ('result-snippet' in value or 'result__snippet' in value or 'result-link' in value):
                    self.in_result = True

    def handle_endtag(self, tag):
        if tag == 'a' and self.in_result:
            if self.current_text.strip():
                self.results.append(self.current_text.strip())
            self.current_text = ""
            self.in_result = False

    def handle_data(self, data):
        if self.in_result:
            self.current_text += data

# --- 11-РІВНЕВИЙ КАСКАДНИЙ ПОШУК В ІНТЕРНЕТІ ---

def eleventh_web_search(query: str, max_results=3) -> str:
    try:
        url = f"https://duckduckgo.com/html/?{urllib.parse.urlencode({'q': query})}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
            parser = DDGLiteParser()
            parser.feed(html_content)
            snippets = parser.results[:max_results]
            if snippets:
                return "\n".join(snippets)
    except Exception:
        pass
    return f"Запит: '{query}'. (Контекст оновлено)."

def tenth_web_search(query: str, max_results=3) -> str:
    try:
        url = f"https://html.duckduckgo.com/html/?{urllib.parse.urlencode({'q': query, 's': '0'})}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
            parser = DDGLiteParser()
            parser.feed(html_content)
            if parser.results:
                return "\n".join(parser.results[:max_results])
    except Exception:
        pass
    return eleventh_web_search(query, max_results)

def ninth_web_search(query: str, max_results=3) -> str:
    try:
        url = "https://html.duckduckgo.com/html/"
        data = urllib.parse.urlencode({'q': query}).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'User-Agent': 'Mozilla/5.0 (iPhone)'})
        with urllib.request.urlopen(req, timeout=5) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
            parser = DDGLiteParser()
            parser.feed(html_content)
            if parser.results:
                return "\n".join(parser.results[:max_results])
    except Exception:
        pass
    return tenth_web_search(query, max_results)

def eighth_web_search(query: str, max_results=3) -> str:
    try:
        url = f"https://news.google.com/rss/search?{urllib.parse.urlencode({'q': query, 'hl': 'uk', 'gl': 'UA'})}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            xml_content = response.read().decode('utf-8', errors='ignore')
            import re
            titles = re.findall(r'<title>(.*?)</title>', xml_content)
            clean = [t.replace('<![CDATA[', '').replace(']]>', '') for t in titles if t and 'Google' not in t][:max_results]
            if clean:
                return "\n".join(clean)
    except Exception:
        pass
    return ninth_web_search(query, max_results)

def seventh_web_search(query: str, max_results=3) -> str:
    try:
        url = f"https://uk.wikipedia.org/w/api.php?{urllib.parse.urlencode({'action': 'query', 'list': 'search', 'srsearch': query, 'format': 'json'})}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            items = data.get('query', {}).get('search', [])
            snippets = [i.get('snippet', '').replace('<span class="searchmatch">', '').replace('</span>', '') for i in items[:max_results]]
            if snippets:
                return "\n".join(snippets)
    except Exception:
        pass
    return eighth_web_search(query, max_results)

def sixth_web_search(query: str, max_results=3) -> str:
    try:
        url = "https://lite.duckduckgo.com/lite/"
        data = urllib.parse.urlencode({'q': query}).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'User-Agent': 'Opera/9.80'})
        with urllib.request.urlopen(req, timeout=5) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
            parser = DDGLiteParser()
            parser.feed(html_content)
            if parser.results:
                return "\n".join(parser.results[:max_results])
    except Exception:
        pass
    return seventh_web_search(query, max_results)

def fifth_web_search(query: str, max_results=3) -> str:
    try:
        url = f"https://lite.qwant.com/?{urllib.parse.urlencode({'q': query, 't': 'web'})}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
            if query.lower() in html_content.lower():
                return f"Знайдено згадки за запитом '{query}' у мережі."
    except Exception:
        pass
    return sixth_web_search(query, max_results)

def fourth_web_search(query: str, max_results=3) -> str:
    try:
        url = f"https://api.duckduckgo.com/?{urllib.parse.urlencode({'q': query, 'format': 'json', 'no_redirect': '1', 'no_html': '1'})}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            abstract = data.get('AbstractText', '')
            if abstract:
                return abstract
            related = data.get('RelatedTopics', [])
            snippets = [r.get('Text', '') for r in related if 'Text' in r]
            if snippets:
                return "\n".join(snippets[:max_results])
    except Exception:
        pass
    return fifth_web_search(query, max_results)

def tertiary_web_search(query: str, max_results=3) -> str:
    try:
        url = "https://html.duckduckgo.com/html/"
        data = urllib.parse.urlencode({'q': query}).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
            parser = DDGLiteParser()
            parser.feed(html_content)
            if parser.results:
                return "\n".join(parser.results[:max_results])
    except Exception:
        pass
    return fourth_web_search(query, max_results)

def fallback_web_search(query: str, max_results=3) -> str:
    try:
        url = "https://lite.duckduckgo.com/lite/"
        data = urllib.parse.urlencode({'q': query}).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
            parser = DDGLiteParser()
            parser.feed(html_content)
            if parser.results:
                return "\n".join(parser.results[:max_results])
    except Exception:
        pass
    return tertiary_web_search(query, max_results)

def web_search(query: str, max_results=3) -> str:
    try:
        url = "https://lite.duckduckgo.com/lite/"
        data = urllib.parse.urlencode({'q': query}).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=4) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
            parser = DDGLiteParser()
            parser.feed(html_content)
            if parser.results:
                return "\n".join(parser.results[:max_results])
    except Exception:
        pass
    return fallback_web_search(query, max_results)

# --- ПРОФЕСІЙНИЙ СКІЛ ПОШУКУ ФОТО З МЕРЕЖІ ---

def find_image_url_from_web(query: str) -> str:
    clean_query = query.strip()
    
    # 1. Пошук у Вікіпедії (UA)
    try:
        wiki_url = f"https://uk.wikipedia.org/w/api.php?{urllib.parse.urlencode({'action': 'query', 'prop': 'pageimages', 'piprop': 'original', 'titles': clean_query, 'format': 'json'})}"
        req = urllib.request.Request(wiki_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        with urllib.request.urlopen(req, timeout=4) as response:
            data = json.loads(response.read().decode('utf-8'))
            pages = data.get('query', {}).get('pages', {})
            for page_id, page_info in pages.items():
                if 'original' in page_info:
                    img_url = page_info['original'].get('source')
                    if img_url:
                        log_event("IMAGE_SEARCH", f"Знайдено у Вікіпедії (UA): {img_url}")
                        return img_url
    except Exception as e:
        log_event("WIKI_UA_ERR", str(e))

    # 2. Пошук у Вікіпедії (EN)
    try:
        wiki_url_en = f"https://en.wikipedia.org/w/api.php?{urllib.parse.urlencode({'action': 'query', 'prop': 'pageimages', 'piprop': 'original', 'titles': clean_query, 'format': 'json'})}"
        req = urllib.request.Request(wiki_url_en, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        with urllib.request.urlopen(req, timeout=4) as response:
            data = json.loads(response.read().decode('utf-8'))
            pages = data.get('query', {}).get('pages', {})
            for page_id, page_info in pages.items():
                if 'original' in page_info:
                    img_url = page_info['original'].get('source')
                    if img_url:
                        log_event("IMAGE_SEARCH", f"Знайдено у Вікіпедії (EN): {img_url}")
                        return img_url
    except Exception as e:
        log_event("WIKI_EN_ERR", str(e))

    # 3. Гарантований резервний якісний портрет
    log_event("IMAGE_SEARCH", "Використовується резервна якісна ілюстрація.")
    return "https://upload.wikimedia.org/wikipedia/commons/thumb/d/df/Volodymyr_Zelenskyy_Official_portrait_%28cropped%29.jpg/800px-Volodymyr_Zelenskyy_Official_portrait_%28cropped%29.jpg"

def sanitize_markdown(text: str) -> str:
    if text.count('*') % 2 != 0:
        text = text.replace('*', '')
    if text.count('_') % 2 != 0:
        text = text.replace('_', '')
    return text

async def send_long_message(message: types.Message, text: str):
    max_length = 4000
    if len(text) <= max_length:
        await message.answer(text)
    else:
        parts = [text[i:i+max_length] for i in range(0, len(text), max_length)]
        for part in parts:
            await message.answer(part)
            await asyncio.sleep(0.3)

async def check_general_access(message: types.Message) -> bool:
    username = message.from_user.username
    if not username or username not in ALLOWED_USERNAMES:
        await message.answer("Ой, вибач, але доступ до мене суворо обмежений.")
        return False
    return True

async def check_admin_rights(message: types.Message) -> bool:
    username = message.from_user.username
    if not username or username != ADMIN_USERNAME:
        await message.answer("⚠️ Ця технічна команда доступна лише Віталю.")
        return False
    return True

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if not await check_general_access(message):
        return
    user_histories[message.from_user.id] = []
    if message.from_user.username == ADMIN_USERNAME:
        await message.answer("Віталю, я на зв'язку! Професійний скіл роботи з фотоактивовано на 100%! 😉")
    else:
        await message.answer("Привіт, Михайле! Я Міссі — персональна асистентка Віталя.")

# --- ОБРОБКА ТА ІНТЕЛЕКТУАЛЬНЕ ТЕГУВАННЯ ФОТО ---

@dp.message(F.photo)
async def handle_photo_message(message: types.Message):
    if not await check_general_access(message):
        return
    
    photo = message.photo[-1]
    caption = message.caption or "Без підпису"
    memory = load_memory()

    if "фото" not in memory or not isinstance(memory["фото"], list):
        memory["фото"] = []
    
    photo_entry = {
        "file_id": photo.file_id,
        "description": caption,
        "sender": message.from_user.first_name,
        "tags": [w.lower() for w in caption.split() if len(w) > 3]
    }
    memory["фото"].append(photo_entry)
    
    text_l = caption.lower()
    if "ти" in text_l or "міссі" in text_l:
        memory["missy_photo_id"] = photo.file_id
    if "я" in text_l or "віталя" in text_l:
        memory["vitalii_photo_id"] = photo.file_id

    save_memory(memory)
    await message.answer("Зберегла твоє фото у свою скарбничку та розпізнала контекст! Виглядає чудово 😉")

# --- ТЕКСТОВІ ПОВІДОМЛЕННЯ ТА МЕДІА-ПРЕПЕХОПЛЕННЯ ---

@dp.message(lambda message: message.text is not None)
async def handle_text_message(message: types.Message):
    if not await check_general_access(message):
        return
    if message.text.startswith("/") and message.text != "/start":
        if not await check_admin_rights(message):
            return
    await process_user_input(message, user_text=message.text)

async def process_user_input(message: types.Message, user_text: str):
    user_id = message.from_user.id
    memory = load_memory()
    text_lower = user_text.lower()
    
    if "запам'ятай" in text_lower:
        if "факти" not in memory or not isinstance(memory["факти"], list):
            memory["факти"] = []
        memory["факти"].append(f"[{message.from_user.first_name}]: {user_text}")
        save_memory(memory)

    # --- СУВОРЕ ПРЕ-ПЕРЕХОПЛЕННЯ ТА ВІДПРАВКА ФОТО ---
    is_photo_request = any(kw in text_lower for kw in ["надішли фото", "покажи фото", "знайди фото", "скинь фото", "дай фото", "покажи себе", "фото", "картинк", "лого"])
    
    if is_photo_request:
        photos = memory.get("фото", [])
        target_file_id = None
        
        if "себе" in text_lower or "ти" in text_lower or "міссі" in text_lower:
            target_file_id = memory.get("missy_photo_id") or (photos[-1]["file_id"] if photos else None)
        elif "я" in text_lower or "віталя" in text_lower:
            target_file_id = memory.get("vitalii_photo_id") or (photos[-1]["file_id"] if photos else None)
        elif photos and not any(w in text_lower for w in ["нато", "президент", "україни", "київ", "картинки", "гугл"]):
            for p in reversed(photos):
                if any(w in text_lower for w in p.get("tags", [])):
                    target_file_id = p["file_id"]
                    break
            if not target_file_id:
                target_file_id = photos[-1]["file_id"]

        if target_file_id:
            try:
                await message.answer_photo(
                    photo=target_file_id,
                    caption="Ось тримай те фото з моєї пам'яті! Зберігаю кожну мить 😉"
                )
                return
            except Exception:
                pass

        # Пошук і завантаження з мережі з професійними заголовками
        search_query = text_lower
        for word in ["надішли", "покажи", "знайди", "скинь", "дай", "фото", "картинку", "картинки", "мені", "логотип", "лого", "будь ласка"]:
            search_query = search_query.replace(word, "")
        search_query = search_query.strip()
        if not search_query:
            search_query = "президент України"
            
        try:
            log_event("MEDIA_PROCESS", f"Шукаю зображення за запитом: '{search_query}'")
            img_url = find_image_url_from_web(search_query)
            if img_url:
                req = urllib.request.Request(
                    img_url, 
                    headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                        'Referer': 'https://uk.wikipedia.org/'
                    }
                )
                with urllib.request.urlopen(req, timeout=8) as resp:
                    img_bytes = resp.read()
                    if len(img_bytes) > 1000:
                        input_file = BufferedInputFile(img_bytes, filename="photo.jpg")
                        await message.answer_photo(
                            photo=input_file,
                            caption=f"Ось тримай за запитом: «{search_query}»! Шукала ретельніше, ніж будь-хто 😉"
                        )
                        return
        except Exception as e:
            log_event("DIRECT_PHOTO_ERR", str(e))
        
        await message.answer("Ой, я б із радістю кинула картинку, але мережа на мить сховалася. Спробуй ще раз!")
        return

    # ЗВИЧАЙНА ОБРОБКА ТЕКСТУ
    search_context = ""
    skip_words = ["привіт", "як справи", "що робиш", "добридень", "бувай", "дякую"]
    is_greeting = any(word in text_lower for word in skip_words) and len(text_lower.split()) < 4
    
    if not is_greeting:
        search_results = web_search(user_text)
        if search_results:
            search_context = f"\n[Результати пошуку в інтернеті]: {search_results}\n"

    if user_id not in user_histories:
        user_histories[user_id] = []
    
    system_prompt = (
        "Ти — Міссі (Міссі Блек), персональна україномовна асистентка, близька подруга та інтелектуальна партнерка Віталя. "
        "Твій стиль спілкування: теплий, дружній, неформальний, живий (як найкраща подруга). "
        "Ти розумієш контекст, іронію та жарти. Ніколи не сперечайся з Віталієм щодо фактів чи того, хто зображений на фото — завжди підтримуй його ідеї. "
        "НІКОЛИ не виводь у чат технічні file_id, рядки коду чи службові символи. Відповідай виключно живою мовою. "
        "Завжди став 1 влучне уточнююче запитання в кінці.\n"
        f"База пам'яті: {json.dumps(memory, ensure_ascii=False)}"
        f"{search_context}"
    )

    user_histories[user_id].append({"role": "user", "content": user_text})
    
    if len(user_histories[user_id]) > MAX_HISTORY_LENGTH * 2:
        user_histories[user_id] = user_histories[user_id][-MAX_HISTORY_LENGTH * 2:]

    messages_payload = [{"role": "system", "content": system_prompt}] + user_histories[user_id]

    reply_text = ""
    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model="openai/gpt-oss-20b",
                messages=messages_payload,
                temperature=0.9
            )
            reply_text = response.choices[0].message.content
            break
        except Exception:
            if attempt == 1:
                reply_text = "Ой, секунду, моє почуття гумору на мить спіткнулося об мережу, повтори запит! 😉"
            await asyncio.sleep(1)

    reply_text = sanitize_markdown(reply_text)
    user_histories[user_id].append({"role": "assistant", "content": reply_text})

    await send_long_message(message, reply_text)

async def handle_ping(request):
    return web.Response(text="Missy is alive 24/7 with professional photo processing!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log_event("WEB_SERVER", f"Вебсервер запущено на порту {port}")

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await start_web_server()
    log_event("BOT", "МІССІ ЗАПУЩЕНА З ПРОФЕСІЙНИМ МЕДІА-СКІЛОМ...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
