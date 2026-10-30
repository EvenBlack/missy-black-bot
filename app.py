import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from openai import AsyncOpenAI

# Налаштування логування в консоль
logging.basicConfig(level=logging.INFO)

# Отримуємо змінні середовища з Render
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Ініціалізація Telegram-бота та диспетчера
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# Налаштування клієнта Groq через OpenAI-сумісний шлюз
groq_client = AsyncOpenAI(
    api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1"
)


# Обробник усіх текстових повідомлень у чаті
@dp.message(F.text)
async def handle_message(message: Message):
  user_text = message.text
  logging.info(f"Отримано повідомлення: {user_text}")

  try:
    # Запит до Groq API з актуальною робочою моделлю
    response = await groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": user_text}],
    )
    reply_text = response.choices[0].message.content

    # Надсилаємо відповідь у Telegram
    await message.answer(reply_text)

  except Exception as e:
    logging.error(f"Помилка при зверненні до Groq: {e}")
    await message.answer(
        "Ой, сталася помилка при зверненні до штучного інтелекту... 🤖"
    )


async def main():
  # Очищаємо старі сесії та вебхуки перед запуском
  await bot.delete_webhook(drop_pending_updates=True)
  logging.info("Бот Міссі успішно запускається на Groq API...")

  # Запускаємо постійне опитування Telegram
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())
