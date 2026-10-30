import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, F
from aiogram.types import ErrorEvent, Message
from openai import AsyncOpenAI

# Налаштування логування в консоль
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)

# 1. Жорстка перевірка наявності токенів на старті
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not TELEGRAM_TOKEN:
  logging.critical(
      "ПОМИЛКА: Змінна середовища TELEGRAM_TOKEN не задана на хостингу!"
  )
  exit(1)

if not GROQ_API_KEY:
  logging.critical(
      "ПОМИЛКА: Змінна середовища GROQ_API_KEY не задана на хостингу!"
  )
  exit(1)

# Ініціалізація Telegram-бота та диспетчера
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# Налаштування клієнта Groq через OpenAI-сумісний шлюз
groq_client = AsyncOpenAI(
    api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1"
)

# Кеш для збереження списку доступних моделей
available_models_cache = []
# Множина для захисту від обробки дублікатів оновлень
processed_updates = set()


async def get_all_working_models():
  global available_models_cache
  if available_models_cache:
    return available_models_cache

  try:
    models_response = await asyncio.wait_for(
        groq_client.models.list(), timeout=10.0
    )
    if models_response and models_response.data:
      available_models_cache = [model.id for model in models_response.data]
      logging.info(f"Доступні моделі на твоєму ключі: {available_models_cache}")
      return available_models_cache
  except Exception as e:
    logging.error(f"Помилка при отриманні списку моделей: {e}")

  # Страховий дефолтний список на випадок збою мережі
  return ["llama-3.1-8b-instant", "llama-3.3-70b-versatile"]


# Глобальний обробник помилок aiogram, щоб бот не падав від винятків
@dp.error()
async def global_error_handler(event: ErrorEvent):
  logging.error(f"Сталася глобальна неочікувана помилка в aiogram: {event.exception}")
  return True


# Функція запиту до Groq із системою повторних спроб (Retries)
async def ask_groq_with_retries(model_name, user_text):
  for attempt in range(2):  # 2 спроби на кожну модель
    try:
      response = await asyncio.wait_for(
          groq_client.chat.completions.create(
              model=model_name, messages=[{"role": "user", "content": user_text}]
          ),
          timeout=15.0,
      )
      return response.choices[0].message.content
    except Exception as e:
      logging.warning(
          f"Спроба {attempt + 1} для моделі {model_name} не вдалася: {e}"
      )
      if attempt == 0:
        await asyncio.sleep(1)  # коротка пауза перед повтором
      else:
        raise e


# Обробник усіх текстових повідомлень у чаті
@dp.message(F.text)
async def handle_message(message: Message):
  # Захист від обробки дублікатів оновлень
  if message.message_id in processed_updates:
    return
  processed_updates.add(message.message_id)

  # Обмежуємо розмір кешу оброблених повідомлень у пам'яті
  if len(processed_updates) > 500:
    processed_updates.pop()

  user_text = message.text
  logging.info(
      f"Отримано повідомлення від {message.from_user.id}: {user_text}"
  )

  models = await get_all_working_models()
  success = False
  reply_text = ""

  # Перебираємо доступні моделі по черзі
  for model_name in models:
    try:
      logging.info(f"Спроба запиту до моделі: {model_name}")
      reply_text = await ask_groq_with_retries(model_name, user_text)
      if reply_text:
        success = True
        break
    except Exception as e:
      logging.warning(
          f"Модель {model_name} повністю вичерпала спроби. Помилка: {e}"
      )

  try:
    if success and reply_text:
      await message.answer(reply_text)
    else:
      await message.answer(
          "Ой, на жаль, штучний інтелект зараз перевантажений або не відповів учасно... 🤖"
      )
  except Exception as err:
    logging.error(f"Помилка при відправці відповіді в Telegram: {err}")


async def main():
  try:
    # Очищаємо старі сесії та вебхуки перед запуском
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info(
        "Бот Міссі успішно запускається на Groq API з абсолютним захистом..."
    )

    # Запускаємо постійне опитування Telegram
    await dp.start_polling(bot)
  finally:
    # Коректне закриття сесій при зупинці
    await bot.session.close()
    logging.info("Сесію бота успішно закрито.")


if __name__ == "__main__":
  try:
    asyncio.run(main())
  except (KeyboardInterrupt, SystemExit):
    logging.info("Робота бота була зупинена.")
