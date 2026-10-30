import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from google import genai

# Налаштування логування
logging.basicConfig(level=logging.INFO)

# Отримуємо змінні середовища
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Ініціалізація бота та диспетчера
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# Ініціалізація клієнта штучного інтелекту через Groq API
client = genai.Client(api_key=GROQ_API_KEY)


# Обробник будь-яких текстових повідомлень від користувача
@dp.message(F.text)
async def handle_message(message: Message):
  user_text = message.text
  logging.info(f"Отримано повідомлення від користувача: {user_text}")

  try:
    # Запит до штучного інтелекту
    response = client.models.generate_content(
        model="gemini-2.5-flash",  # або інша актуальна модель, яку ти використовуєш
        contents=user_text,
    )
    reply_text = response.text

    # Надсилаємо відповідь назад у Telegram-чат
    await message.answer(reply_text)
  except Exception as e:
    logging.error(f"Помилка при зверненні до ШІ: {e}")
    await message.answer(
        "Ой, щось пішло не так при зверненні до мого штучного"
        " інтелекту... 🤖"
    )


async def main():
  # Примусово скидаємо старі сесії та вебхуки перед стартом
  await bot.delete_webhook(drop_pending_updates=True)
  logging.info("Старі сесії скинуто. Бот Міссі успішно запускається...")

  # Запускаємо опитування (long polling)
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())
