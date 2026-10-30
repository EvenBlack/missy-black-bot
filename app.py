import asyncio
import logging
import os
from aiogram import Bot, Dispatcher
from google import genai

# Налаштування логування
logging.basicConfig(level=logging.INFO)

# Отримуємо змінні середовища
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Ініціалізація бота та диспетчера
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# Ініціалізація клієнта штучного інтелекту
client = genai.Client(api_key=GROQ_API_KEY)


async def main():
  # Найголовніше: спочатку повністю видаляємо вебхуки та скидаємо всі старі сесії
  await bot.delete_webhook(drop_pending_updates=True)
  logging.info("Старі сесії скинуто. Бот Міссі успішно запускається...")

  # Запускаємо опитування
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())
