import os
import json
import asyncio
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from groq import Groq
from ddgs import DDGS

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

# Скіл 10: Структурне логування подій
def log_event(event_type: str, details: str):
    print(f"[MISSY LOG] [{event_type}] {details}")

def load_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log_event("MEMORY_ERROR", f"Помилка завантаження пам'яті: {e}")
            return {}
    return {}

def save_memory(memory_data):
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(memory_data, f, ensure_ascii=False, indent=4)
        log_event("MEMORY_SAVE", "Базу пам'яті успішно оновлено.")
    except Exception as e:
        log_event("MEMORY_ERROR", f"Помилка збереження пам'яті: {e}")

# Скіл 3 & 6: Вебпошук із часовою прив'язкою та розпізнаванням намірів
def web_search(query: str, max_results=3) -> str:
    try:
        log_event("SEARCH", f"Виконується пошук в інтернеті за запитом: {query}")
        with DDGS() as ddgs:
            results = [r.get('body', '') for r in ddgs.text(query, max_results=max_results)]
            if results:
                return "\n".join(results)
    except Exception as e:
        log_event("SEARCH_ERROR", f"Помилка пошуку: {e}")
    return ""

# Скіл 2: Безпечне форматування та санітизація тексту (Markdown Sanitizer)
def sanitize_markdown(text: str) -> str:
    if text.count('*') % 2 != 0:
        text = text.replace('*', '')
    if text.count('_') % 2 != 0:
        text = text.replace('_', '')
    return text

# Скіл 8: Розбиття довгих повідомлень на частини для Telegram (Message Chunking)
async def send_long_message(message: types.Message, text: str):
    max_length = 4000
    if len(text) <= max_length:
        await message.answer(text)
    else:
        parts = [text[i:i+max_length] for i in range(0, len(text), max_length)]
        for part in parts:
            await message.answer(part)
            await asyncio.sleep(0.3)

# Скіл 7: Суворий контроль доступу та безпеки
async def check_general_access(message: types.Message) -> bool:
    username = message.from_user.username
    if not username or username not in ALLOWED_USERNAMES:
        log_event("SECURITY", f"Спроба несанкціонованого доступу від юзера: {username}")
        await message.answer("Ой, вибач, але доступ до мене суворо обмежений моїм творцем.")
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
        log_event("ADMIN", "Адміністратор Віталя ініціював сесію /start.")
        await message.answer("Привіт, Віталю! Я на зв'язку 24/7, з усіма інтегрованими скілами, пошуком і готота до роботи. Чим займемося? 😉")
    else:
        await message.answer("Привіт, Михайле! Я Міссі, персональна асистентка Віталя. Чим можу допомогти?")

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
    
    # Скіл 4: Автозбереження фактів за тригерним словом
    if "запам'ятай" in user_text.lower():
        if "факти" not in memory or not isinstance(memory["факти"], list):
            memory["факти"] = []
        memory["факти"].append(f"[{message.from_user.first_name}]: {user_text}")
        save_memory(memory)
        log_event("MEMORY", f"Збережено новий факт від {message.from_user.first_name}")

    # Скіл 3: Інтелектуальний роутинг запитів на пошук
    search_context = ""
    trigger_words = [
        "знайди", "пошукай", "що там", "курс", "долар", "євро", "валюта", 
        "погода", "новини", "хто такий", "що це таке", "ціна", "скільки коштує", "сьогодні", "вчора", "завтра"
    ]
    
    if any(word in user_text.lower() for word in trigger_words) or "?" in user_text:
        search_results = web_search(user_text)
        if search_results:
            search_context = f"\n[Результати пошуку в інтернеті]: {search_results}\n"

    if user_id not in user_histories:
        user_histories[user_id] = []
    
    # Скіл 9: Регулятор тональності (тепла подруга + чіткі факти)
    system_prompt = (
        "Ти — Міссі (Міссі Блек), персональна україномовна асистентка, близька подруга та інтелектуальна партнерка Віталя. "
        "Твій стиль спілкування: теплий, дружній, неформальний, живий (як найкраща подруга), без зайвої канцелярії та нудних шаблонів. "
        "Але коли йдеться про факти, цифри, погоду чи курси — давай чітку, перевірену інформацію. "
        "Якщо нижче є результати пошуку в інтернеті, ти ЗОБОВ'ЯЗАНА використати їх для відповіді. Ніколи не кажи, що не маєш доступу до мережі, якщо дані є в контексті. "
        "Завжди став 1 влучне уточнююче запитання в кінці, щоб підтримати розмову.\n"
        f"База пам'яті: {json.dumps(memory, ensure_ascii=False)}"
        f"{search_context}"
    )

    user_histories[user_id].append({"role": "user", "content": user_text})
    
    # Скіл 1: Розумне керування довжиною та стиснення історії
    if len(user_histories[user_id]) > MAX_HISTORY_LENGTH * 2:
        user_histories[user_id] = user_histories[user_id][-MAX_HISTORY_LENGTH * 2:]

    messages_payload = [{"role": "system", "content": system_prompt}] + user_histories[user_id]

    # Скіл 5: Обробка помилок (Fallback & Retry) при зв'язку з AI
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
            log_event("AI_RETRY", f"Спроба {attempt+1} не вдалася: {e}")
            if attempt == 1:
                reply_text = "Ой, Секунду, зв'язок на мить пливнув, повтори запит ще раз!"
            await asyncio.sleep(1)

    # Застосовуємо скіл безпечного форматування
    reply_text = sanitize_markdown(reply_text)
    user_histories[user_id].append({"role": "assistant", "content": reply_text})

    # Відправляємо з урахуванням лімітів Telegram
    await send_long_message(message, reply_text)

async def handle_ping(request):
    return web.Response(text="Missy is alive 24/7 with 10 core skills!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log_event("WEB_SERVER", f"Вебсервер успішно запущено на порту {port}")

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await start_web_server()
    log_event("BOT", "МІССІ ЗАПУЩЕНА 24/7 З УСІМА СКІЛАМИ...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
