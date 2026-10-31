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

# --- ІНТЕЛЕКТУАЛЬНИЙ СКІЛ ФОРМУВАННЯ ЗАПИТУ ЧЕРЕЗ ШІ (QUERY REFINER) ---

def smart_refine_image_query(user_prompt: str, history_list: list) -> str:
    """Використовує моделювання ШІ для виділення сутності з урахуванням історії діалогу"""
    try:
        context_str = "\n".join([f"{h['role']}: {h['content']}" for h in history_list[-6:]])
        refine_prompt = (
            f"Історія діалогу:\n{context_str}\n\n"
            f"Поточний запит користувача на фото: '{user_prompt}'\n"
            "Завдання: Визнач, який саме об'єкт, пам'ятку чи людину хоче побачити користувач, враховуючи контекст (наприклад, якщо написано 'її фото' після розмови про піраміду Хеопса, то об'єкт — піраміда Хеопса). "
            "Поверни ВИКЛЮЧНО коротку оптимізовану англійську фразу для пошуку картинки у високій якості (наприклад: 'Great Pyramid of Giza', 'Statue of Liberty', 'NATO logo'). Жодних додаткових слів чи пояснень."
        )
        response = client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[{"role": "user", "content": refine_prompt}],
            temperature=0.1
        )
        refined = response.choices[0].message.content.strip().replace('"', '').replace("'", "")
        if len(refined) > 2:
            log_event("AI_REFINE", f"Оригінал: '{user_prompt}' -> ШІ-запит: '{refined}'")
            return refined
    except Exception as e:
        log_event("REFINE_ERR", str(e))
    return user_prompt

# --- НАДІЙНИЙ МЕДІА-ПОШУК ЧЕРЕЗ WIKIMEDIA ТА COMMONS ---

def find_image_url_from_web(query: str) -> str:
    clean_query = query.strip()
    log_event("MEDIA_SEARCH", f"Шукаю фото за оптимізованим запитом: '{clean_query}'")

    # 1. Wikimedia Commons API
    try:
        search_api = f"https://commons.wikimedia.org/w/api.php?{urllib.parse.urlencode({'action': 'query', 'generator': 'search', 'gsrsearch': clean_query, 'gsrnamespace': '6', 'gsrlimit': '3', 'prop': 'imageinfo', 'iiprop': 'url', 'format': 'json'})}"
        req = urllib.request.Request(search_api, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            pages = data.get('query', {}).get('pages', {})
            for _, page_info in pages.items():
                imageinfo = page_info.get('imageinfo', [])
                if imageinfo and imageinfo[0].get('url'):
                    img_url = imageinfo[0].get('url')
                    if any(ext in img_url.lower() for ext in ['.jpg', '.jpeg', '.png', '.svg']):
                        return img_url
    except Exception as e:
        log_event("COMMONS_ERR", str(e))

    # 2. Wikipedia Article Image API
    try:
        wiki_url = f"https://uk.wikipedia.org/w/api.php?{urllib.parse.urlencode({'action': 'query', 'list': 'search', 'srsearch': clean_query, 'format': 'json'})}"
        req_w = urllib.request.Request(wiki_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req_w, timeout=5) as resp_w:
            w_data = json.loads(resp_w.read().decode('utf-8'))
            items = w_data.get('query', {}).get('search', [])
            if items:
                title = items[0]['title']
                p_url = f"https://uk.wikipedia.org/w/api.php?{urllib.parse.urlencode({'action': 'query', 'prop': 'pageimages', 'piprop': 'original', 'titles': title, 'format': 'json'})}"
                req_p = urllib.request.Request(p_url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req_p, timeout=5) as resp_p:
                    p_data = json.loads(resp_p.read().decode('utf-8'))
                    pages = p_data.get('query', {}).get('pages', {})
                    for _, pinfo in pages.items():
                        if 'original' in pinfo and pinfo['original'].get('source'):
                            return pinfo['original']['source']
    except Exception as e:
        log_event("WIKI_ERR", str(e))

    # 3. Залізобетонний резерв за хешем запиту
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
        await message.answer("Віталю, я на зв'язку! Інтелектуальний скіл розуміння контексту та пошуку фото активовано! 😉")
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

# --- ГОЛОВНИЙ ОБРОБНИК ЗІ ШІ-АНАЛІЗОМ КОНТЕКСТУ ТА МЕДІА ---

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

    # Ініціалізація історії користувача
    if user_id not in user_histories:
        user_histories[user_id] = []

    # Чіткий датчик запиту на фото
    has_action_kw = any(kw in text_lower for kw in ["надішли", "покажи", "скинь", "дай", "пришли"])
    has_object_kw = any(obj in text_lower for obj in ["фото", "картинк", "картинку", "картинки", "лого", "логотип", "її", "його", "виглядає"])
    is_photo_request = has_action_kw and has_object_kw
    
    if is_photo_request:
        # ЗАЛУЧАЄМО ШІ ДЛЯ ВИЗНАЧЕННЯ СУТНОСТІ ЗАПИТУ З УРАХУВАННЯМ ІСТОРІЇ
        search_query = smart_refine_image_query(user_text, user_histories[user_id])
        
        image_cache = memory.get("image_cache", {})

        if search_query in image_cache:
            try:
                cached_file_id = image_cache[search_query]
                await message.answer_photo(photo=cached_file_id, caption=f"Ось тримай за запитом: «{search_query}»! 😉")
                user_histories[user_id].append({"role": "user", "content": user_text})
                user_histories[user_id].append({"role": "assistant", "content": "[Надіслано фото з кешу]"})
                return
            except Exception:
                pass

        try:
            log_event("MEDIA_PROCESS", f"Завантажую точне фото для ШІ-запиту: '{search_query}'")
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
                        
                        user_histories[user_id].append({"role": "user", "content": user_text})
                        user_histories[user_id].append({"role": "assistant", "content": "[Надіслано фото]"})
                        return
        except Exception as e:
            log_event("PHOTO_ERR", str(e))

        await message.answer("Ой, я б із радістю кинула картинку, але мережа на мить сховалася. Спробуй ще раз!")
        return

    # ЗВИЧАЙНИЙ ТЕКСТОВИЙ ДІАЛОГ ІЗ ПОВНИМ ЗБЕРЕЖЕННЯМ ІСТОРІЇ ТА КОНТЕКСТУ
    search_context = ""
    skip_words = ["привіт", "як справи", "що робиш", "добридень", "бувай", "дякую"]
    is_greeting = any(word in text_lower for word in skip_words) and len(text_lower.split()) < 4
    
    if not is_greeting:
        search_results = web_search(user_text)
        if search_results:
            search_context = f"\n[Результати пошуку в інтернеті]: {search_results}\n"
    
    system_prompt = (
        "Ти — Міссі (Міссі Блек), персональна україномовна асистентка, близька подруга та інтелектуальна партнерка Віталя. "
        "Твій стиль спілкування: теплий, дружній, неформальний, живий (як найкраща подруга). "
        "Ти завжди ідеально розумієш і пам'ятаєш контекст попередніх повідомлень у діалозі (наприклад, про піраміди, історичні об'єкти, міста тощо). "
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
    return web.Response(text="Missy is alive 2
