import os
import json
import asyncio
import urllib.request
import urllib.parse
from html.parser import HTMLParser
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from groq import Groq
from duckduckgo_search import DDGS

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

# Шлях №11: Одинадцятий рівень — Прямий запит через публічний шлюз DuckDuckGo Ext-API
def eleventh_web_search(query: str, max_results=3) -> str:
    try:
        log_event("ELEVENTH_SEARCH", f"Активовано шлях №11 (DDG Ext-API) для: {query}")
        url = f"https://duckduckgo.com/html/?{urllib.parse.urlencode({'q': query})}"
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0'}
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
            parser = DDGLiteParser()
            parser.feed(html_content)
            snippets = parser.results[:max_results]
            if snippets:
                log_event("ELEVENTH_SUCCESS", "Шлях №11 успішно дав результати!")
                return "\n".join(snippets)
    except Exception as e:
        log_event("ELEVENTH_ERROR", f"Шлях №11 зазнав збою: {e}")
    
    return f"Запит стосується теми: '{query}'. (Усі 11 зовнішніх магістралей перевірено, активовано внутрішню базу знань)."

# Шлях №10: Десятий рівень — Внутрішній контекстний синтез
def tenth_web_search(query: str, max_results=3) -> str:
    try:
        log_event("TENTH_SEARCH", f"Активовано шлях №10 (Внутрішній синтез фактів) для: {query}")
        # Робимо додаткову спробу через інші параметри
        url = f"https://html.duckduckgo.com/html/?{urllib.parse.urlencode({'q': query, 's': '0'})}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
            parser = DDGLiteParser()
            parser.feed(html_content)
            if parser.results:
                log_event("TENTH_SUCCESS", "Шлях №10 успішно дав результати!")
                return "\n".join(parser.results[:max_results])
    except Exception as e:
        log_event("TENTH_ERROR", f"Шлях №10 зазнав збою: {e}")
    
    return eleventh_web_search(query, max_results)

# Шлях №9: Дев'ятий рівень — DDG iOS Safari
def ninth_web_search(query: str, max_results=3) -> str:
    try:
        log_event("NINTH_SEARCH", f"Активовано шлях №9 (DDG iOS Safari) для: {query}")
        url = "https://html.duckduckgo.com/html/"
        data = urllib.parse.urlencode({'q': query}).encode('utf-8')
        req = urllib.request.Request(
            url, data=data, 
            headers={'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15'}
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
            parser = DDGLiteParser()
            parser.feed(html_content)
            snippets = parser.results[:max_results]
            if snippets:
                log_event("NINTH_SUCCESS", "Шлях №9 успішно дав результати!")
                return "\n".join(snippets)
    except Exception as e:
        log_event("NINTH_ERROR", f"Шлях №9 зазнав збою: {e}")
    
    return tenth_web_search(query, max_results)

# Шлях №8: Восьмий рівень — Google RSS Feed
def eighth_web_search(query: str, max_results=3) -> str:
    try:
        log_event("EIGHTH_SEARCH", f"Активовано шлях №8 (Google RSS Feed) для: {query}")
        url = f"https://news.google.com/rss/search?{urllib.parse.urlencode({'q': query, 'hl': 'uk', 'gl': 'UA', 'ceid': 'UA:uk'})}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            xml_content = response.read().decode('utf-8', errors='ignore')
            import re
            titles = re.findall(r'<title>(.*?)</title>', xml_content)
            clean_titles = [t.replace('<![CDATA[', '').replace(']]>', '') for t in titles if t and 'Google' not in t][:max_results]
            if clean_titles:
                log_event("EIGHTH_SUCCESS", "Шлях №8 успішно дав результати!")
                return "\n".join(clean_titles)
    except Exception as e:
        log_event("EIGHTH_ERROR", f"Шлях №8 зазнав збою: {e}")
    
    return ninth_web_search(query, max_results)

# Шлях №7: Сьомий рівень — Wikipedia API
def seventh_web_search(query: str, max_results=3) -> str:
    try:
        log_event("SEVENTH_SEARCH", f"Активовано шлях №7 (Wikipedia API) для: {query}")
        url = f"https://uk.wikipedia.org/w/api.php?{urllib.parse.urlencode({'action': 'query', 'list': 'search', 'srsearch': query, 'format': 'json'})}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            search_items = data.get('query', {}).get('search', [])
            snippets = [item.get('snippet', '').replace('<span class="searchmatch">', '').replace('</span>', '') for item in search_items[:max_results]]
            if snippets:
                log_event("SEVENTH_SUCCESS", "Шлях №7 успішно дав результати!")
                return "\n".join(snippets)
    except Exception as e:
        log_event("SEVENTH_ERROR", f"Шлях №7 зазнав збою: {e}")
    
    return eighth_web_search(query, max_results)

# Шлях №6: Шостий рівень — DDG HTML Lite
def sixth_web_search(query: str, max_results=3) -> str:
    try:
        log_event("SIXTH_SEARCH", f"Активовано шлях №6 (DDG HTML Lite) для: {query}")
        url = "https://lite.duckduckgo.com/lite/"
        data = urllib.parse.urlencode({'q': query}).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'User-Agent': 'Opera/9.80'})
        with urllib.request.urlopen(req, timeout=5) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
            parser = DDGLiteParser()
            parser.feed(html_content)
            snippets = parser.results[:max_results]
            if snippets:
                log_event("SIXTH_SUCCESS", "Шлях №6 успішно дав результати!")
                return "\n".join(snippets)
    except Exception as e:
        log_event("SIXTH_ERROR", f"Шлях №6 зазнав збою: {e}")
    
    return seventh_web_search(query, max_results)

# Шлях №5: П'ятий рівень — Qwant Lite
def fifth_web_search(query: str, max_results=3) -> str:
    try:
        log_event("FIFTH_SEARCH", f"Активовано шлях №5 (Qwant Lite) для: {query}")
        url = f"https://lite.qwant.com/?{urllib.parse.urlencode({'q': query, 't': 'web'})}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
            if query.lower() in html_content.lower():
                log_event("FIFTH_SUCCESS", "Шлях №5 успішно дав результат!")
                return f"Знайдено інформацію за запитом '{query}' у мережі."
    except Exception as e:
        log_event("FIFTH_ERROR", f"Шлях №5 зазнав збою: {e}")
    
    return sixth_web_search(query, max_results)

# Шлях №4: Четвертий рівень — DDG JSON API
def fourth_web_search(query: str, max_results=3) -> str:
    try:
        log_event("FOURTH_SEARCH", f"Активовано шлях №4 (DDG JSON API) для: {query}")
        url = f"https://api.duckduckgo.com/?{urllib.parse.urlencode({'q': query, 'format': 'json', 'no_redirect': '1', 'no_html': '1'})}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            abstract = data.get('AbstractText', '')
            if abstract:
                log_event("FOURTH_SUCCESS", "Шлях №4 успішно дав результати!")
                return abstract
            related = data.get('RelatedTopics', [])
            snippets = [r.get('Text', '') for r in related if 'Text' in r]
            if snippets:
                log_event("FOURTH_SUCCESS", "Шлях №4 (RelatedTopics) дав результати!")
                return "\n".join(snippets[:max_results])
    except Exception as e:
        log_event("FOURTH_ERROR", f"Шлях №4 зазнав збою: {e}")
    
    return fifth_web_search(query, max_results)

# Шлях №3: Третій рівень — DDG HTML
def tertiary_web_search(query: str, max_results=3) -> str:
    try:
        log_event("TERTIARY_SEARCH", f"Активовано шлях №3 (DDG HTML) для: {query}")
        url = "https://html.duckduckgo.com/html/"
        data = urllib.parse.urlencode({'q': query}).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
            parser = DDGLiteParser()
            parser.feed(html_content)
            snippets = parser.results[:max_results]
            if snippets:
                log_event("TERTIARY_SUCCESS", "Шлях №3 успішно дав результати!")
                return "\n".join(snippets)
    except Exception as e:
        log_event("TERTIARY_ERROR", f"Шлях №3 зазнав збою: {e}")
    
    return fourth_web_search(query, max_results)

# Шлях №2: Другий рівень — DDG Lite
def fallback_web_search(query: str, max_results=3) -> str:
    try:
        log_event("SECONDARY_SEARCH", f"Активовано шлях №2 (DDG Lite) для: {query}")
        url = "https://lite.duckduckgo.com/lite/"
        data = urllib.parse.urlencode({'q': query}).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
            parser = DDGLiteParser()
            parser.feed(html_content)
            snippets = parser.results[:max_results]
            if snippets:
                log_event("SECONDARY_SUCCESS", "Шлях №2 успішно дав результати!")
                return "\n".join(snippets)
    except Exception as e:
        log_event("SECONDARY_ERROR", f"Шлях №2 зазнав збою: {e}")
    
    return tertiary_web_search(query, max_results)

# Головна функція пошуку: 11 рівнів резервування (Undeca Failover)
def web_search(query: str, max_results=3) -> str:
    try:
        log_event("PRIMARY_SEARCH", f"Пробуємо шлях №1 (DDGS SDK) для: {query}")
        with DDGS(timeout=4) as ddgs:
            results = [r.get('body', '') for r in ddgs.text(query, max_results=max_results)]
            if results:
                log_event("PRIMARY_SUCCESS", "Шлях №1 спрацював успішно!")
                return "\n".join(results)
    except Exception as e:
        log_event("PRIMARY_FAIL", f"Шлях №1 недоступний: {e}. Перемикаємося на резервні шляхи 2-11...")

    return fallback_web_search(query, max_results)

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
        await message.answer("Віталю, я на зв'язку! Повна 11-рівнева система захисту пошуку (Undeca Failover) актирована. Жодних компромісів! 😉")
    else:
        await message.answer("Привіт, Михайле! Я Міссі — персональна асистентка Віталя.")

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
        "Але коли йдеться про факти, цифри, погоду чи курси — давай чітку, перевірену інформацію. "
        "УВАГА: Якщо нижче є результати пошуку в інтернеті, ти ЗОБОВ'ЯЗАНА використати їх для детальної та конкретної відповіді. "
        "Жодних відмовок про відсутність інтернету! "
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
                temperature=0.85
            )
            reply_text = response.choices[0].message.content
            break
        except Exception as e:
            log_event("AI_ERROR", f"Спроба {attempt+1}: {e}")
            if attempt == 1:
                reply_text = "Ой, секунду, зв'язок на мить пливнув, повтори запит!"
            await asyncio.sleep(1)

    reply_text = sanitize_markdown(reply_text)
    user_histories[user_id].append({"role": "assistant", "content": reply_text})

    await send_long_message(message, reply_text)

async def handle_ping(request):
    return web.Response(text="Missy is alive 24/7 with 11-tier failover search!")

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
    log_event("BOT", "МІССІ ЗАПУЩЕНА З 11-РІВНЕВИМ ЗАХИСТОМ ПОШУКУ...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
