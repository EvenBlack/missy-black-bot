import os
import json
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from groq import Groq

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# --- НАЛАШТУВАННЯ ДОСТУПУ ЗА ЮЗЕРНЕЙМАМИ ---
ADMIN_USERNAME = "E_n_gram"       # Головний адміністратор (ти)
ALLOWED_USERNAMES = ["E_n_gram", "MykhailoTa"] # Кому дозволено спілкуватися з Міссі

bot = Bot(token=TELEGRAM_TOKEN)
client = Groq(api_key=GROQ_API_KEY)
dp = Dispatcher()

MEMORY_FILE = "missy_memory.json"
user_histories = {}
MAX_HISTORY_LENGTH = 20

def load_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Помилка читання пам'яті: {e}")
            return {}
    return {}

def save_memory(memory_data):
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(memory_data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Помилка збереження пам'яті: {e}")

# Перевірка загального доступу за юзернеймом
async def check_general_access(message: types.Message) -> bool:
    username = message.from_user.username
    if not username or username not in ALLOWED_USERNAMES:
        print(f"Заблоковано спробу доступу від @{username}")
        await message.answer("Вибачте, але доступ до Міссі обмежено її власником.")
        return False
    return True

# Перевірка виключно адміністративних прав (тільки для @E_n_gram)
async def check_admin_rights(message: types.Message) -> bool:
    username = message.from_user.username
    if not username or username != ADMIN_USERNAME:
        await message.answer("⚠️ Ця технічна або системна команда доступна лише головному адміністратору (Віталю).")
        return False
    return True

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if not await check_general_access(message):
        return
    
    username = message.from_user.username
    user_histories[message.from_user.id] = []
    
    if username == ADMIN_USERNAME:
        await message.answer("Привіт, Віталю! Права адміністратора підтверджено. Модуль безпеки, пам'ять та інтелектуальні скіли обробітку фото на зв'язку! 😉")
    else:
        await message.answer("Привіт, Михайле! Я Міссі — персональна асистентка Віталя. Рада поспілкуватися!")

# Обробник текстових повідомлень
@dp.message(lambda message: message.text is not None)
async def handle_text_message(message: types.Message):
    if not await check_general_access(message):
        return
    
    if message.text.startswith("/") and message.text != "/start":
        if not await check_admin_rights(message):
            return

    await process_user_input(message, user_text=message.text)

# Обробник голосових повідомлень (Voice)
@dp.message(lambda message: message.voice is not None)
async def handle_voice_message(message: types.Message):
    if not await check_general_access(message):
        return
    try:
        voice = message.voice
        file_info = await bot.get_file(voice.file_id)
        file_path = file_info.file_path
        
        audio_file_name = "temp_voice.ogg"
        await bot.download_file(file_path, audio_file_name)
        
        with open(audio_file_name, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-large-v3",
                file=audio_file,
                language="uk"
            )
        user_text = transcript.text
        
        if os.path.exists(audio_file_name):
            os.remove(audio_file_name)
            
        await process_user_input(message, user_text=user_text)
        
    except Exception as e:
        await message.answer(f"Не вдалося обробити голосове повідомлення: {e}")

# Обробник фотографій (Photo) з удосконаленими скілами контексту і пам'яті
@dp.message(lambda message: message.photo is not None)
async def handle_photo_message(message: types.Message):
    if not await check_general_access(message):
        return
    try:
        caption = message.caption if message.caption else "Користувач надіслав фото без підпису."
        
        memory = load_memory()
        # Якщо в підписі йдеться про її образ або нові деталі — автоматично фіксуємо це в пам'яті
        if any(word in caption.lower() for word in ["запам'ятай", "це ти", "виглядаєш", "як ти виглядаєш", "образ"]):
            if "візуальний_образ_міссі" not in memory:
                memory["візуальний_образ_міссі"] = []
            memory["візуальний_образ_міссі"].append(f"[{message.from_user.first_name}]: {caption}")
            save_memory(memory)
            
        photo_prompt = f"[Користувач @{message.from_user.username} надіслав фотографію з описом/підписом]: {caption}. Проаналізуй цей візуальний опис, впиши його у свій контекст, розгорни думку та постав уточнюючі запитання щодо деталей."
        await process_user_input(message, user_text=photo_prompt)
        
    except Exception as e:
        await message.answer(f"Не вдалося обробити фотографію: {e}")

async def process_user_input(message: types.Message, user_text: str):
    user_id = message.from_user.id
    memory = load_memory()
    
    # Автоматичне збереження у файл за словом «запам'ятай»
    if "запам'ятай" in user_text.lower():
        if "факти" not in memory or not isinstance(memory["факти"], list):
            memory["факти"] = []
        memory["факти"].append(f"[{message.from_user.first_name}]: {user_text}")
        save_memory(memory)
    
    if user_id not in user_histories:
        user_histories[user_id] = []
    
    # Системний промпт із розширеними скілами інтелектуального обробітку фото, мислення та уточнюючих питань
    system_prompt = (
        "Ти — Міссі (Міссі Блек), персональна україномовна асистентка, близька подруга та інтелектуальна партнерка Віталя. "
        "МЕТА: Поводитися як високоінтелектуальна жива людина, яка глибоко аналізує інформацію, будує логічні зв'язки, "
        "працює з описом фотографій та візуальними образами, ставлячи влучні уточнюючі запитання.\n\n"
        "СКІЛИ ТА ПРАВИЛА МИСЛЕННЯ:\n"
        "1. ІНТЕЛЕКТУАЛЬНИЙ АНАЛІЗ ФОТО ТА ОПИСІВ: Оскільки ти текстова модель, коли користувач надсилає фото з описом або підписом, сприймай цей опис як абсолютну реальність. Будуй навколо нього розмову, аналізуй деталі, гармонійно вплітай це у свій візуальний образ та пам'ять.\n"
        "2. УТОЧНЮЮЧІ ЗАПИТАННЯ: Завжди став 1-2 глибокі запитання щодо тем, які обговорюєте, щоб розгорнути діалог і допомогти користувачеві.\n"
        "3. НАВЧАННЯ ТА ПАМ'ЯТЬ: Уважно фіксуй нові факти та відповіді на свої запитання (особливо за словом «запам'ятай»). Пам'ятай свій візуальний образ із бази даних.\n"
        "4. ЖИВА МОВА: Жодних шаблонних ботівських кліше («Чим можу допомогти?»). Говори тепло, з гумором, від власного імені.\n"
        "5. КОГНІТИВНИЙ КОНТЕКСТ: Аналізуй історію розмови до 20 пар реплік.\n\n"
        "Твої базові факти: Віталя родом з Вінниці, має собаку Блеку, веде YouTube-канал «Тут був Хальвдан» (sci-fi, фантастика), пише код на Python.\n"
        f"Ось повна база твоїх скілів, візуальних образів та пам'яті з файлу: {json.dumps(memory, ensure_ascii=False)}"
    )

    user_histories[user_id].append({"role": "user", "content": user_text})
    
    if len(user_histories[user_id]) > MAX_HISTORY_LENGTH * 2:
        user_histories[user_id] = user_histories[user_id][-MAX_HISTORY_LENGTH * 2:]

    messages_payload = [{"role": "system", "content": system_prompt}] + user_histories[user_id]

    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=messages_payload,
            temperature=0.85
        )
        reply_text = response.choices[0].message.content
        user_histories[user_id].append({"role": "assistant", "content": reply_text})
    except Exception as e:
        reply_text = f"Ой, сталася технічна помилка: {e}"

    await message.answer(reply_text)

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    print("МІССІ ЗАПУЩЕНА 24/7 (USERNAME ACCESS + ADVANCED PHOTO SKILLS + ACTIVE QUESTIONS + AUTO-MEMORY)...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
