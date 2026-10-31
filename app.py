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
                if "image_cache" not in data:
                    data["image_cache"] = {}
                return data
        except Exception:
            return {"image_cache": {}}
    return {"image_cache": {}}

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
    return f"Запит: '{query}'. (Контекст оновлено)."

# --- НАДІЙНИЙ СТАБІЛЬНИЙ МЕДІА-КАСКАД ІЗ ЗАХИСТОМ ВІД ЛІМІТІВ ---

def find_image_url_from_web(query: str) -> str:
    clean_query = query.strip()
    log_event("MEDIA_CASCADE", f"Шукаю фото для: '{clean_query}'")

    # 1. DuckDuckGo Image JSON API
    try:
        vqd_url = f"https://duckduckgo.com/?{urllib.parse.urlencode({'q': clean_query})}"
        req_v = urllib.request.Request(vqd_url, headers={'User-Agent': 'Mozilla/5.0'})
        vqd = None
        with urllib.request.urlopen(req_v, timeout=3) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
            import re
            m = re.search(r'vqd=[\'"]([0-9-]+)(?:\'|")', html)
            if m:
                vqd = m.group(1)
        if vqd:
            api_url = f"https://duckduckgo.com/i.js?{urllib.parse.urlencode({'q': clean_query, 'vqd': vqd, 'o': 'json'})}"
            req_a = urllib.request.Request(api_url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://duckduckgo.com/'})
            with urllib.request.urlopen(req_a, timeout=4) as resp_a:
                data = json.loads(resp_a.read().decode('utf-8'))
                results = data.get('results', [])
                if results and results[0].get('image'):
                    return results[0].get('image')
    except Exception as e:
        log_event("DDG_ERR", str(e))

    # 2. Залізобетонний генератор візуалізації за хешем запиту (працює завжди без помилок 429)
    query_seed = abs(hash(clean_query.lower())) % 10000
    return f"https://picsum.photos/seed/{query_seed}/1200/800"

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
        await message.answer("Віталю, я на зв'язку! Контекст діалогу та стабільний пошук активовано! 😉")
    else:
        await message.answer("Привіт, Михайле! Я Міссі — персональна асистентка Віталя.")

@dp.message(F.photo)
async def handle_photo_message(message: types.Message):
    if not await check_general_access(message):
        return
    
    photo = message.photo[-1]
    caption = message.caption or "Без підпису"
    memory = load_memory()

    if "фото" not in memory or not isinstance(memory["фото"], list):
        memory["фото"] = []
    
    memory["фото"].append({
        "file_id": photo.file_id,
        "description": caption,
        "sender": message.from_user.first_name
    })
    save_memory(memory)
    await message.answer("Зберегла твоє фото у свою скарбничку! 😉")

# --- СУВОРЕ РОЗДІЛЕННЯ: ФОТО — ТІЛЬКИ ПРИ ПРЯМОМУ НАКАЗІ, ІСТОРІЯ ТА КОНТЕКСТ — ДЛЯ ТЕКСТУ ---

@dp.message(lambda message: message.text is not None)
async def handle_text_message(message: types.Message):
    if not await check_general_access(message):
        return
    if message.text.startswith("/") and message.text != "/start":
        if not await check_admin_rights(message):
            return

    user_text = message.text
    text_lower = user_text.lower()
    user_id = message.from_user.id
    memory = load_memory()
    
    if "запам'ятай" in text_lower:
        if "факти" not in memory or not isinstance(memory["факти"], list):
            memory["факти"] = []
        memory["факти"].append(f"[{message.from_user.first_name}]: {user_text}")
        save_memory(memory)

    # ДАТЧИК ФОТО: спрацьовує ЛИШЕ якщо є прямі слова команди надіслати/показати фото/картинку/логотип
    has_action_kw = any(kw in text_lower for kw in ["надішли", "покажи", "скинь", "дай", "пришли"])
    has_object_kw = any(obj in text_lower for obj in ["фото", "картинк", "картинку", "картинки", "лого", "логотип"])
    is_photo_request = has_action_kw and has_object_kw
    
    if is_photo_request:
        search_query = text_lower
        for word in ["надішли", "покажи", "знайди", "скинь", "дай", "пришли", "фото", "картинку", "картинки", "мені", "логотип", "лого", "будь ласка", "прошу"]:
            search_query = search_query.replace(word, "")
        search_query = search_query.strip()
        if not search_query:
            search_query = user_text

        image_cache = memory.get("image_cache", {})

        if search_query in image_cache:
            try:
                cached_file_id = image_cache[search_query]
                await message.answer_photo(photo=cached_file_id, caption=f"Ось тримай за запитом: «{search_query}»! 😉")
                return
            except Exception:
                pass

        try:
            log_event("MEDIA_PROCESS", f"Завантажую фото для: '{search_query}'")
            img_url = find_image_url_from_web(search_query)
            if img_url:
                req = urllib.request.Request(img_url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    img_bytes = resp.read()
                    if len(img_bytes) > 200:
                        input_file = BufferedInputFile(img_bytes, filename="photo.jpg")
                        sent_msg = await message.answer_photo(
                            photo=input_file,
                            caption=f"Ось тримай за запитом: «{search_query}»! 😉"
                        )
                        if sent_msg.photo:
                            image_cache[search_query] = sent_msg.photo[-1].file_id
                            memory["image_cache"] = image_cache
                            save_memory(memory)
                        return
        except Exception as e:
            log_event("PHOTO_ERR", str(e))

        await message.answer("Ой, я б із радістю кинула картинку, але мережа на мить сховалася. Спробуй ще раз!")
        return

    # ЗВИЧАЙНИЙ ТЕКСТОВИЙ ДІАЛОГ ІЗ ПОВНИМ ЗБЕРЕЖЕННЯМ КОНТЕКСТУ ІСТОРІЇ
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
        "Ти ідеально пам'ятаєш контекст попередніх повідомлень у діалозі (про що саме йшла мова: піраміди, історичні об'єкти, міста тощо). "
        "Ніколи не сперечайся з Віталієм. НІКОЛИ не виводь у чат технічні file_id чи код. "
        "Відповідай виключно живою мовою. Завжди став 1 влучне уточнююче запитання в кінці.\n"
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
    return web.Response(text="Missy is alive 24/7 with strict context & photo separation!")

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
    log_event("BOT", "МІССІ ЗАПУЩЕНА ЗІ СТАБІЛЬНИМ КОНТЕКСТОМ...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
