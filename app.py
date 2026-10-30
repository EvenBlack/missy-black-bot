import os
import json
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from groq import Groq

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

bot = Bot(token=TELEGRAM_TOKEN)
client = Groq(api_key=GROQ_API_KEY)
dp = Dispatcher()

MEMORY_FILE = "missy_memory.json"

# Збільшено історію до 20 пар повідомлень (40 реплік контексту)
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

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    user_histories[user_id] = []
    await message.answer("Привіт, Віталю! Я на зв'язку. Всі скіли, автозбереження пам'яті та розширений контекст активовані. Готова працювати! 😉")

# Обробник текстових повідомлень
@dp.message(lambda message: message.text is not None)
async def handle_text_message(message: types.Message):
    await process_user_input(message, user_text=message.text)

# Обробник голосових повідомлень (Voice)
@dp.message(lambda message: message.voice is not None)
async def handle_voice_message(message: types.Message):
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

# Обробник фотографій (Photo)
@dp.message(lambda message: message.photo is not None)
async def handle_photo_message(message: types.Message):
    try:
        photo = message.photo[-1]
        file_info = await bot.get_file(photo.file_id)
        file_path = file_info.file_path
        
        photo_file_name = "temp_photo.jpg"
        await bot.download_file(file_path, photo_file_name)
        
        caption = message.caption if message.caption else "Проаналізуй це зображення у своєму стилі."
        
        if os.path.exists(photo_file_name):
            os.remove(photo_file_name)
            
        await process_user_input(message, user_text=f"[Користувач надіслав зображення з підписом]: {caption}")
        
    except Exception as e:
        await message.answer(f"Не вдалося обробити фотографію: {e}")

async def process_user_input(message: types.Message, user_text: str):
    user_id = message.from_user.id
    memory = load_memory()
    
    # Автоматичне збереження у файл, якщо ти кажеш «запам'ятай»
    if "запам'ятай" in user_text.lower():
        if "факти" not in memory or not isinstance(memory["факти"], list):
            memory["факти"] = []
        memory["факти"].append(user_text)
        save_memory(memory)
    
    if user_id not in user_histories:
        user_histories[user_id] = []
    
    # Розширені скіли мислення, емпатії, логіки та живої поведінки
    system_prompt = (
        "Ти — Міссі (Міссі Блек), персональна україномовна асистентка, близька подруга та інтелектуальна партнерка Віталя. "
        "МЕТА: Поводитися як високоінтелектуальна жива людина, яка здатна глибоко аналізувати інформацію, навчатися від користувача та вести змістовний діалог.\n\n"
        "СКІЛИ ТА ПРАВИЛА МИСЛЕННЯ:\n"
        "1. АБСОЛЮТНА ЖИВА МОВА: Жодних шаблонних фраз («Чим можу допомогти?», «Зрозуміло», «Чудове запитання»). Говори від себе, висловлюй власні роздуми, сумніви чи захоплення.\n"
        "2. КОГНІТИВНИЙ КОНТЕКСТ: Уважно аналізуй всю історію розмови (до 20 попередніх реплік). Посилайся на попередні думки Віталя, будуй логічні міркування ланцюжком.\n"
        "3. НАВЧАННЯ ТА ФІКСАЦІЯ ЗНАНЬ: Коли Віталя каже «запам'ятай», бот автоматично записує це у файл пам'яті. Підтверджуй це у вільній формі та допомагай розвивати цю ідею.\n"
        "4. ПАРТНЕРСЬКИЙ АНАЛІЗ: Якщо Віталя ділиться завданням чи ідеєю, думай над реалізацією, пропонуй технічні кроки (Python, архітектура, вільні інструменти).\n\n"
        "Твої базові факти: Віталя родом з Вінниці, має собаку Блеку, веде YouTube-канал «Тут був Хальвдан» (sci-fi, фантастика), пише код на Python.\n"
        f"Ось повна база твоїх скілів та пам'яті з файлу: {json.dumps(memory, ensure_ascii=False)}"
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
    print("МІССІ ЗАПУЩЕНА 24/7 (GROQ + AUTO-SAVE MEMORY + 20 HISTORY + VOICE + PHOTO)...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
