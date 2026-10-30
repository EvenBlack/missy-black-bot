import os
import json
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from groq import Groq

# Читаємо змінні середовища з GitHub Actions
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
    memory = load_memory()
    user_info = memory.get("користувач", {})
    name = user_info.get("ім'я", "Віталю") if isinstance(user_info, dict) else "Віталю"
    await message.answer(f"Привіт, {name}! Я Міссі, твоя асистентка. Вся моя база знань, скіли та пам'ять успішно активовані!")

@dp.message()
async def handle_message(message: types.Message):
    user_text = message.text
    memory = load_memory()
    
    # Головний контекст для швидкого доступу моделі
    user_name = "Віталя"
    youtube_channel = "«Тут був Хальвдан» (українськомовні аудіоісторії: фентезі, sci-fi, пригоди)"
    hometown = "Вінниця"
    dog = "собака Блека"
    
    # Формуємо комплексний системний промпт: пріоритетні факти + повна база всіх скілів та даних з файлу
    system_prompt = (
        f"Ти — Міссі, персональна україномовна асистентка Віталя. "
        f"Головний творець: {user_name} (родом з {hometown}, має собаку {dog}). "
        f"Головний проєкт: YouTube-канал {youtube_channel}. "
        f"У тебе є велика база даних, що містить понад 500 скілів (боністика, android-розробка, self-healing алгоритми, біржовий трейдинг, графічний дизайн), а також нотатки та задачі. "
        f"Ось повний вміст твоєї пам'яті (`missy_memory.json`), до якого ти маєш повний доступ і на який маєш спиратися у відповідях: {json.dumps(memory, ensure_ascii=False)}"
    )

    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text}
            ],
            temperature=0.7
        )
        reply_text = response.choices[0].message.content
    except Exception as e:
        reply_text = f"Ой, сталася технічна помилка: {e}"

    await message.answer(reply_text)

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    print("МІССІ ЗАПУЩЕНА 24/7 (GROQ)...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
