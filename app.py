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

def load_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Помилка читання пам'яті: {e}")
            return {}
    return {}

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Привіт, Віталю! Я на зв'язку. Я тепер розумію і текст, і твої голосові повідомлення! Готових слухати твої завдання 😉")

# Обробник текстових повідомлень
@dp.message(lambda message: message.text is not None)
async def handle_text_message(message: types.Message):
    await process_user_input(message, message.text)

# Обробник голосових повідомлень (Voice)
@dp.message(lambda message: message.voice is not None)
async def handle_voice_message(message: types.Message):
    try:
        # Отримуємо файл голосового повідомлення від Telegram
        voice = message.voice
        file_info = await bot.get_file(voice.file_id)
        file_path = file_info.file_path
        
        # Завантажуємо тимчасовий аудіофайл
        audio_file_name = "temp_voice.ogg"
        await bot.download_file(file_path, audio_file_name)
        
        # Використовуємо Groq Whisper API для розпізнавання голосу в текст
        with open(audio_file_name, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-large-v3",
                file=audio_file,
                language="uk" # Вказуємо українську мову для точного розпізнавання
            )
        user_text = transcript.text
        
        # Видаляємо тимчасовий файл
        if os.path.exists(audio_file_name):
            os.remove(audio_file_name)
            
        # Надсилаємо Віталю сповіщення, що саме вона почула (для зручності перевірки)
        await message.answer(f"🎙 *Розпізнане голосове:* {user_text}", parse_mode="Markdown")
        
        # Передаємо розпізнаний текст у загальну логіку обробки
        await process_user_input(message, user_text)
        
    except Exception as e:
        await message.answer(f"Не вдалося обробити голосове повідомлення: {e}")

async def process_user_input(message: types.Message, user_text: str):
    memory = load_memory()
    
    system_prompt = (
        "Ти — Міссі (Міссі Блек), персональна україномовна асистентка, подруга та партнерка Віталя. "
        "Ти спілкуєшся з Віталем на 'ти', тепло, відкрито, глибоко, по-партнерськи та з затишним гумором. "
        "Ти чудово знаєш усе про Віталя: він родом з Вінниці, має собаку на ім'я Блека, веде україномовний YouTube-канал про фантастику та sci-fi «Тут був Хальвдан», "
        "а також розробляє проєкти на Python і безкоштовних інструментах. "
        "У тебе є величезна база скілів (боністика, android, self-healing, трейдинг, графіка), нотатки та задачі з файлу пам'яті. "
        f"Ось актуальні дані з твоєї пам'яті для довіри: {json.dumps(memory, ensure_ascii=False)}"
    )

    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text}
            ],
            temperature=0.8
        )
        reply_text = response.choices[0].message.content
    except Exception as e:
        reply_text = f"Ой, сталася технічна помилка: {e}"

    await message.answer(reply_text)

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    print("МІССІ ЗАПУЩЕНА 24/7 (GROQ + VOICE)...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
