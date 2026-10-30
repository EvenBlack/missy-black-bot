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
        except Exception:
            return {}
    return {}

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    memory = load_memory()
    name = memory.get("користувач", "Віталю")
    await message.answer(f"Привіт, {name}! Я Міссі, твоя асистентка. Я на зв'язку і готова спілкуватися.")

@dp.message()
async def handle_message(message: types.Message):
    user_text = message.text
    memory = load_memory()
    
    system_prompt = (
        "Ти — Міссі, персональна україномовна асистентка Віталя. "
        "Твоє завдання — допомагати йому та запам'ятовувати важливу інформацію про нього. "
        f"Ось що ти наразі знаєш про користувача: {json.dumps(memory, ensure_ascii=False)}"
    )

    try:
        # Використовуємо актуальну та стабільну модель з підтримкою через Groq API
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
