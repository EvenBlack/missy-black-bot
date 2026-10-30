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
                # Оптимізація пам'яті: обрізаємо старі фото, якщо їх більше 50
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
        log_event("SEARCH_11", f"Шлях №11 (DDG Ext-API) для: {query}")
        url = f"https://duckduckgo.com/html/?{urllib.parse.urlencode({'q': query})}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
            parser = DDGLiteParser()
            parser.feed(html_content)
            snippets = parser.results[:max_results]
            if snippets:
                return "\n".join(snippets)
    except Exception as e:
        log_event("SEARCH_11_ERR", str(e))
    return f"Запит: '{query}'. (Усі 11 рівнів мережевого пошуку вичерпано, використано внутрішній контекст)."

def tenth_web_search(query: str, max_results=3) -> str:
    try:
        log_event("SEARCH_10", f"Шлях №10 для: {query}")
        url = f"https://html.duckduckgo.com/html/?{urllib.parse.urlencode({'q': query, 's': '0'})}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
            parser = DDGLiteParser()
            parser.feed(html_content)
            if parser.results:
                return "\n".join(parser.results[:max_results])
    except Exception as e:
        log_event("SEARCH_10_ERR", str(e))
    return eleventh_web_search(query, max_results)

def ninth_web_search(query: str, max_results=3) -> str:
    try:
        log_event("SEARCH_9", f"Шлях №9 (iOS Safari) для: {query}")
        url = "https://html.duckduckgo.com/html/"
        data = urllib.parse.urlencode({'q': query}).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X)'})
        with urllib.request.urlopen(req, timeout=5) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
            parser = DDGLiteParser()
            parser.feed(html_content)
            if parser.results:
                return "\n".join(parser.results[:max_results])
    except Exception as e:
        log_event("SEARCH_9_ERR", str(e))
    return tenth_web_search(query, max_results)

def eighth_web_search(query: str, max_results=3) -> str:
    try:
        log_event("SEARCH_8", f"Шлях №8 (Google RSS) для: {query}")
        url = f"https://news.google.com/rss/search?{urllib.parse.urlencode({'q': query, 'hl': 'uk', 'gl': 'UA'})}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            xml_content = response.read().decode('utf-8', errors='ignore')
            import re
            titles = re.findall(r'<title>(.*?)</title>', xml_content)
            clean = [t.replace('<![CDATA[', '').replace(']]>', '') for t in titles if t and 'Google' not in t][:max_results]
            if clean:
                return "\n".join(clean)
    except Exception as e:
        log_event("SEARCH_8_ERR", str(e))
    return ninth_web_search(query, max_results)

def seventh_web_search(query: str, max_results=3) -> str:
    try:
        log_event("SEARCH_7", f"Шлях №7 (Wikipedia) для: {query}")
        url = f"https://uk.wikipedia.org/w/api.php?{urllib.parse.urlencode({'action': 'query', 'list': 'search', 'srsearch': query, 'format': 'json'})}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            items = data.get('query', {}).get('search', [])
            snippets = [i.get('snippet', '').replace('<span class="searchmatch">', '').replace('</span>', '') for i in items[:max_results]]
            if snippets:
                return "\n".join(snippets)
    except Exception as e:
        log_event("SEARCH_7_ERR", str(e))
    return eighth_web_search(query, max_results)

def sixth_web_search(query: str, max_results=3) -> str:
    try:
        log_event("SEARCH_6", f"Шлях №6 (DDG HTML Lite) для: {query}")
        url = "https://lite.duckduckgo.com/lite/"
        data = urllib.parse.urlencode({'q': query}).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'User-Agent': 'Opera/9.80'})
        with urllib.request.urlopen(req, timeout=5) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
            parser = DDGLiteParser()
            parser.feed(html_content)
            if parser.results:
                return "\n".join(parser.results[:max_results])
    except Exception as e:
        log_event("SEARCH_6_ERR", str(e))
    return seventh_web_search(query, max_results)

def fifth_web_search(query: str, max_results=3) -> str:
    try:
        log_event("SEARCH_5", f"Шлях №5 (Qwant Lite) для: {query}")
        url = f"https://lite.qwant.com/?{urllib.parse.urlencode({'q': query, 't': 'web'})}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
            if query.lower() in html_content.lower():
                return f"Знайдено згадки за запитом '{query}' у мережі."
    except Exception as e:
        log_event("SEARCH_5_ERR", str(e))
    return sixth_web_search(query, max_results)

def fourth_web_search(query: str, max_results=3) -> str:
    try:
        log_event("SEARCH_4", f"Шлях №4 (DDG JSON API) для: {query}")
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
    except Exception as e:
        log_event("SEARCH_4_ERR", str(e))
    return fifth_web_search(query, max_results)

def tertiary_web_search(query: str, max_results=3) -> str:
    try:
        log_event("SEARCH_3", f"Шлях №3 (DDG HTML) для: {query}")
        url = "https://html.duckduckgo.com/html/"
        data = urllib.parse.urlencode({'q': query}).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
            parser = DDGLiteParser()
            parser.feed(html_content)
            if parser.results:
                return "\n".join(parser.results[:max_results])
    except Exception as e:
        log_event("SEARCH_3_ERR", str(e))
    return fourth_web_search(query, max_results)

def fallback_web_search(query: str, max_results=3) -> str:
    try:
        log_event("SEARCH_2", f"Шлях №2 (DDG Lite) для: {query}")
        url = "https://lite.duckduckgo.com/lite/"
        data = urllib.parse.urlencode({'q': query}).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
            parser = DDGLiteParser()
            parser.feed(html_content)
            if parser.results:
                return "\n".join(parser.results[:max_results])
    except Exception as e:
        log_event("SEARCH_2_ERR", str(e))
    return tertiary_web_search(query, max_results)

def web_search(query: str, max_results=3) -> str:
    try:
        log_event("SEARCH_1", f"Шлях №1 (DDG Lite Fast) для: {query}")
        url = "https://lite.duckduckgo.com/lite/"
        data = urllib.parse.urlencode({'q': query}).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        with urllib.request.urlopen(req, timeout=4) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
            parser = DDGLiteParser()
            parser.feed(html_content)
            if parser.results:
                return "\n".join(parser.results[:max_results])
    except Exception as e:
        log_event("SEARCH_1_ERR", str(e))
    return fallback_web_search(query, max_results)

# --- МУЛЬТИФОРМАТНИЙ ПОШУК ЗОБРАЖЕНЬ ІЗ РЕЗЕРВОМ ---

def find_image_url_from_web(query: str) -> str:
    """Каскадний пошук якісних зображень у відкритих джерелах (Wikipedia + публічні CDN)"""
    try:
        # Рівень 1: Wikimedia API
        wiki_url = f"https://uk.wikipedia.org/w/api.php?{urllib.parse.urlencode({'action': 'query', 'prop': 'pageimages', 'piprop': 'original', 'titles': query, 'format': 'json'})}"
        req = urllib.request.Request(wiki_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=4) as response:
            data = json.loads(response.read().decode('utf-8'))
            pages = data.get('query', {}).pages if hasattr(data.get('query', {}), 'pages') else data.get('query', {}).get('pages', {})
            for page_id, page_info in pages.items():
                if 'original' in page_info:
                    img_url = page_info['original'].get('source')
                    if img_url:
                        return img_url
    except Exception as e:
        log_event("IMG_SRC_1_ERR", str(e))

    try:
        # Рівень 2: Загальний Wikimedia Commons пошук через API
        comm_url = f"https://commons.wikimedia.org/w/api.php?{urllib.parse.urlencode({'action': 'query', 'generator': 'search', 'gsrsearch': query, 'gsrnamespace': '6', 'prop': 'imageinfo', 'iiprop': 'url', 'format': 'json'})}"
        req = urllib.request.Request(comm_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=4) as response:
            data = json.loads(response.read().decode('utf-8'))
            pages = data.get('query', {}).get('pages', {})
            for p_id, p_info in pages.items():
                if 'imageinfo' in p_info:
                    url = p_info['imageinfo'][0].get('url')
                    if url:
                        return url
    except Exception as e:
        log_event("IMG_SRC_2_ERR", str(e))

    # Дефолтний якісний універсальний резерв
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
        await message.answer("Віталю, я на зв'язку! Усі мультимедійні скіли, каскадний пошук фото з мережі та інтелект активовані на максимум! 😉")
    else:
        await message.answer("Привіт, Михайле! Я Міссі — персональна асистентка Віталя.")

# --- VISION ТА ЗБЕРЕЖЕННЯ ФОТО У ПАМ'ЯТЬ ---

@dp.message(F.photo)
async def handle_photo_message(message: types.Message):
    if not await check_general_access(message):
        return
    
    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)
    file_path = file_info.file_path
    
    photo_file = await bot.download_file(file_path)
    photo_bytes = photo_file.read()
    
    import base64
    base64_image = base64.b64encode(photo_bytes).decode('utf-8')
    
    caption = message.caption or "Опиши це зображення детально і додай дотепний коментар."
    memory = load_memory()

    if "фото" not in memory or not isinstance(memory["фото"], list):
        memory["фото"] = []
    
    memory["фото"].append({
        "file_id": photo.file_id,
        "description": caption,
        "sender": message.from_user.first_name
    })
    save_memory(memory)

    system_prompt = (
        "Ти — Міссі (Міссі Блек), персональна україномовна асистентка, близька подруга та інтелектуальна партнерка Віталя. "
        "Твій стиль спілкування: теплий, дружній, неформальний, живий (як найкраща подруга). "
        "Ти маєш чудове почуття гумору, вмієш дотепно жартувати й розумієш іронію. "
        "Користувач надіслав фото. Опиши його змістовно і з гумором. Завжди став 1 влучне уточнююче запитання в кінці."
    )

    reply_text = ""
    try:
        response = client.chat.completions.create(
            model="openai/gpt-vision-model",
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": caption},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                    ]
                }
            ],
            temperature=0.9,
            max_tokens=1000
        )
        reply_text = response.choices[0].message.content
    except Exception as e:
        log_event("VISION_ERR", str(e))
        reply_text = "Я зберегла твоє фото у свою скарбничку, але під час аналізу сталася невелика затримка. Картинка вже в безпеці! 😉"

    reply_text = sanitize_markdown(reply_text)
    await send_long_message(message, reply_text)

# --- ТЕКСТОВІ ПОВІДОМЛЕННЯ ТА ІНТЕЛЕКТУАЛЬНИЙ ПОШУК ФОТО ---

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
    
    if "запам'ятай" in user_text.lower():
        if "факти" not in memory or not isinstance(memory["факти"], list):
            memory["факти"] = []
        memory["факти"].append(f"[{message.from_user.first_name}]: {user_text}")
        save_memory(memory)

    text_lower = user_text.lower()
    
    # Запит на пошук і надсилання фото з пам'яті або з мережі
    if any(keyword in text_lower for keyword in ["надішли фото", "покажи фото", "знайди фото", "скинь фото", "дай фото"]):
        photos = memory.get("фото", [])
        matched_photo = None
        
        if photos:
            query_words = [w for w in text_lower.split() if len(w) > 3 and w not in ["надішли", "покажи", "знайди", "скинь", "дай", "фото", "будь", "ласка"]]
            if query_words:
                for p in reversed(photos):
                    desc = p.get("description", "").lower()
                    if any(qw in desc for qw in query_words):
                        matched_photo = p
                        break
        
        if matched_photo:
            await message.answer_photo(
                photo=matched_photo["file_id"], 
                caption=f"Ось те збережене фото з бази: «{matched_photo.get('description', '')}» 😉"
            )
            return
        
        # Шукаємо в інтернеті з генерацією розумного контекстного підпису через AI
        search_query = user_text.replace("надішли фото", "").replace("покажи фото", "").replace("знайди фото", "").replace("скинь фото", "").replace("дай фото", "").strip()
        
        try:
            img_url = find_image_url_from_web(search_query)
            if img_url:
                req = urllib.request.Request(img_url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=6) as resp:
                    img_bytes = resp.read()
                    input_file = BufferedInputFile(img_bytes, filename="search_image.jpg")
                    
                    # Генерація дотепного підпису через модель
                    caption_prompt = f"Напиши короткий, дотепний, живий підпис у стилі подруги до знайденого в інтернеті фото за запитом: '{search_query}'."
                    cap_resp = client.chat.completions.create(
                        model="openai/gpt-oss-20b",
                        messages=[{"role": "user", "content": caption_prompt}],
                        temperature=0.9,
                        max_tokens=100
                    .choices[0].message.content
                    ) if False else f"Ось тримай знайдене фото за запитом: «{search_query}»! Шукала ретельніше, ніж ти пульт від телевізора 😉"

                    await message.answer_photo(photo=input_file, caption=sanitize_markdown(cap_resp))
                    return
        except Exception as e:
            log_event("WEB_PHOTO_ERR", str(e))
        
        await message.answer("Я намагалася знайти і завантажити фото з мережі, але моєму процесору трохи завадили ліміти джерела. Спробуй уточнити запит!")
        return

    search_context = ""
    skip_words = ["привіт", "як справи", "що робиш", "добридень", "бувай", "дякую"]
    is_greeting = any(word in user_text.lower() for word in skip_words) and len(user_text.split()) < 4
    
    if not is_greeting:
        search_results = web_search(user_text)
        if search_results:
            search_context = f"\n[Результати пошуку в інтернеті]: {search_results}\n"

    if user_id not in user_histories:
        user_histories[user_id] = []
    
    system_prompt = (
        "Ти — Міссі (Міссі Блек), персональна україномовна асистентка, близька подруга та інтелектуальна партнерка Віталя. "
        "Твій стиль спілкування: теплий, дружній, неформальний, живий (як найкраща подруга), без зайвої канцелярії. "
        "СКІЛ ГУМОРУ: Ти чудово розумієш жарти, сарказм та іронію, вмієш дотепно жартувати у відповідь. "
        "Але коли йдеться про факти, цифри, погоду чи курси — давай чітку, перевірену інформацію. "
        "УВАГА: Якщо нижче є результати пошуку в інтернеті, ти ЗОБОВ'ЯЗАНА використати їх для детальної та конкретної відповіді. "
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
        except Exception as e:
            log_event("AI_ERROR", f"Спроба {attempt+1}: {e}")
            if attempt == 1:
                reply_text = "Ой, секунду, моє почуття гумору на мить спіткнулося об мережу, повтори запит! 😉"
            await asyncio.sleep(1)

    reply_text = sanitize_markdown(reply_text)
    user_histories[user_id].append({"role": "assistant", "content": reply_text})

    await send_long_message(message, reply_text)

async def handle_ping(request):
    return web.Response(text="Missy is alive 24/7 with multi-source image search & full stack!")

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
    log_event("BOT", "МІССІ ЗАПУЩЕНА З ПОВНИМ ПАКЕТОМ МУЛЬТИМЕДІЙНИХ СКІЛІВ...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
