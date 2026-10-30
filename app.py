import asyncio
import logging
import os
from aiogram import Bot, Dispatcher
from google import genai
from google.genai import types

# Налаштування логування
logging.basicConfig(level=logging.INFO)

# Отримуємо змінні середовища (вони безпечно лежать у налаштуваннях Render)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Ініціалізація бота та диспетчера
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# Ініціалізація клієнта штучного інтелекту
client = genai.Client(api_key=GROQ_API_KEY)


async def main():
  # Примусово скидаємо старі з'єднання та черги на серверах Telegram,
  # щоб уникнути будь-яких конфліктів сесій
  await bot.delete_webhook(drop_pending_updates=True)

  logging.info("Бот Міссі успішно запускається...")
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())
