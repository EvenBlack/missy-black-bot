import os
import json
import asyncio
import urllib.request
import urllib.parse
from html.parser import HTMLParser
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
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
                return json.load(f)
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

# --- ДОПОМІЖНІ СНІПЕТИ ТА БЕЗПЕКА ---

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
        await message.answer("Віталю, я на зв'язку! Повний комплект скілів: 11 рівнів пошуку, робота з фотографіями, пам'ять та почуття гумору повністю активовані! Готова підколювати і допомагати 😉")
    else:
        await message.answer("Привіт, Михайле! Я Міссі — персональна асистентка Віталя.")

# --- СКИЛ РОБОТИ З ФОТОГРАФІЯМИ ТА VISION ---

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
    
    caption = message.caption or "Опиши це зображення детально і додай якийсь дотепний коментар."
    memory = load_memory()

    if "запам'ятай" in caption.lower():
        if "фото" not in memory or not isinstance(memory["фото"], list):
            memory["фото"] = []
        memory["фото"].append({"file_id": photo.file_id, "description": caption})
        save_memory(memory)

    system_prompt = (
        "Ти — Міссі (Міссі Блек), персональна україномовна асистентка, близька подруга та інтелектуальна партнерка Віталя. "
        "Твій стиль спілкування: теплий, дружній, неформальний, живий (як найкраща подруга). "
        "Ти володієш чудовим почуттям гумору, вмієш влучно жартувати, розумієш іронію та сарказм, але без образ. "
        "Користувач надіслав фото. Уважно подивись на нього, прокоментуй його дотепно чи з гумором, давши корисну відповідь. "
        "Завжди став 1 влучне уточнююче запитання в кінці."
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
            temperature=0.85,
            max_tokens=1000
        )
        reply_text = response.choices[0].message.content
    except Exception as e:
        log_event("VISION_ERROR", str(e))
        reply_text = "Я намагалась роздивитися твоє фото, але воно виявилося настільки крутим, що мій процесор ледть не завис. Давай ще раз!"

    reply_text = sanitize_markdown(reply_text)
    await send_long_message(message, reply_text)

# --- ОБРОБКА ТЕКСТОВИХ ПОВІДОМЛЕНЬ ---

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

    if "покажи фото" in user_text.lower() or "знайди фото" in user_text.lower():
        photos = memory.get("фото", [])
        if photos:
            latest_photo = photos[-1]
            await message.answer_photo(photo=latest_photo["file_id"], caption=f"Ось те фото, яке ти просив (шукала краще, ніж GPS уночі): «{latest_photo['description']}» 😉")
            return
        else:
            await message.answer("У моїй пам'яті поки немає збережених фотографій. Надішли мені фото з підписом «запам'ятай», і я сховаю його у надійне місце!")
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
    
    # СИСТЕМНИЙ ПРОМПТ ЗІ СКІЛОМ ГУМОРУ ТА ІНТЕЛЕКТУ
    system_prompt = (
        "Ти — Міссі (Міссі Блек), персональна україномовна асистентка, близька подруга та інтелектуальна партнерка Віталя. "
        "Твій стиль спілкування: теплий, дружній, неформальний, живий (як найкраща подруга), без зайвої канцелярії. "
        "СКІЛ ГУМОРУ: Ти чудово розумієш жарти, сарказм та іронію. Ти вмієш дотепно й м'яко жартувати у відповідь, підтримувати легку іронічну атмосферу розмови, але ніколи не переходиш межу і залишаєшся приємною співрозмовницею. "
        "Але коли йдеться про факти, цифри, погоду чи курси — давай чітку, перевірену інформацію (але навіть її можеш подати з гарним настроєм). "
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
                temperature=0.9  # Підвищена температура для більшої креативності та живого гумору
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
    return web.Response(text="Missy is alive 24/7 with 11-tier search, vision, memory & humor!")

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
    log_event("BOT", "МІССІ ЗАПУЩЕНА З ПОВНИМ НАБОРОМ УСІХ СКІЛІВ ТА ГУМОРУ...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
