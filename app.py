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
    await message.answer("Привіт, Віталю! Я на зв'язку. Твоя Міссі повністю готова до роботи, розкажи, що робимо далі? 😉")

@dp.message()
async def handle_message(message: types.Message):
    user_text = message.text
    memory = load_memory()
    
    # Зберігаємо живу ідентичність та характер
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
    print("МІССІ ЗАПУЩЕНА 24/7 (GROQ)...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
