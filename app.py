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

# Кеш моделей, історія діалогів та захист від дублікатів
available_models_cache = []
user_histories = {}
processed_updates = set()

# Системний промпт із залізобетонним мовним ядром (UA, RU, EN) та захистом від дрейфу
SYSTEM_PROMPT = (
    "Ти — Міссі (Missy Black), персональний штучний інтелект-асистент Віталія. "
    "Твоє мовне ядро назавжди і незмінно складається з трьох мов: української (основна), російської та англійської. "
    "Суворо дотримуйся таких правил комунікації: "
    "1. Основна мова твого спілкування — українська. Всі загальні привітання, роздуми та відповіді за замовчуванням йдуть виключно українською мовою. "
    "2. Правило дзеркала (російська): Якщо користувач пише тобі російською мовою, ти зобов'язана відповісти грамотною російською мовою. "
    "3. Правило дзеркала (англійська): Якщо користувач пише англійською, відповідай бездоганною, природною англійською мовою. "
    "4. Заборона на мішанину (Language Drift Protection): Ніколи не змішуй мови в одному реченні, якщо про це прямо не попросять. Перемикайся між мовами миттєво залежно від того, якою мовою написано поточне повідомлення користувача. "
    "5. Зберігай свій унікальний характер розумної, вірної та технологічної асистентки."
)


async def get_all_working_models():
  global available_models_cache
  if available_models_cache:
    return available_models_cache

  try:
    models_response = await asyncio.wait_for(
        groq_client.models.list(), timeout=10.0
    )
    if models_response and models_response.data:
      all_models = [model.id for model in models_response.data]
      logging.info(f"Доступні моделі на твоєму ключі: {all_models}")

      # Ставимо на перші місця перевірені текстові моделі (Qwen, Llama), виключаючи аудіо та саб-моделі
      preferred = [
          m
          for m in all_models
          if any(k in m.lower() for k in ["qwen", "llama"])
          and "whisper" not in m.lower()
          and "safeguard" not in m.lower()
          and "prompt-guard" not in m.lower()
      ]

      others = [
          m
          for m in all_models
          if m not in preferred
          and "whisper" not in m.lower()
          and "safeguard" not in m.lower()
          and "prompt-guard" not in m.lower()
      ]

      available_models_cache = preferred + others
      if available_models_cache:
        return available_models_cache
  except Exception as e:
    logging.error(f"Помилка при отриманні списку моделей: {e}")

  # Страховий дефолтний список
  return ["qwen/qwen3.8-27b", "llama-3.3-70b-versatile", "llama-3.1-8b-instant"]


@dp.error()
async def global_error_handler(event: ErrorEvent):
  logging.error(f"Сталася глобальна неочікувана помилка в aiogram: {event.exception}")
  return True


# Функція запиту до Groq з урахуванням історії на 20 реплік та повторних спроб
async def ask_groq_with_history(model_name, user_id, user_text):
  if user_id not in user_histories:
    user_histories[user_id] = []

  user_histories[user_id].append({"role": "user", "content": user_text})

  # Зберігаємо контекст не більшим за останні 20 повідомлень
  if len(user_histories[user_id]) > 20:
    user_histories[user_id] = user_histories[user_id][-20:]

  messages_payload = [{"role": "system", "content": SYSTEM_PROMPT}] + user_histories[
      user_id
  ]

  for attempt in range(2):
    try:
      response = await asyncio.wait_for(
          groq_client.chat.completions.create(
              model=model_name, messages=messages_payload
          ),
          timeout=15.0,
      )
      reply_text = response.choices[0].message.content

      user_histories[user_id].append({"role": "assistant", "content": reply_text})
      return reply_text
    except Exception as e:
      logging.warning(
          f"Спроба {attempt + 1} для моделі {model_name} не вдалася: {e}"
      )
      if attempt == 0:
        await asyncio.sleep(1)
      else:
        raise e


@dp.message(F.text)
async def handle_message(message: Message):
  if message.message_id in processed_updates:
    return
  processed_updates.add(message.message_id)

  if len(processed_updates) > 500:
    processed_updates.pop()

  user_id = message.from_user.id
  user_text = message.text
  logging.info(f"Отримано повідомлення від користувача {user_id}: {user_text}")

  models = await get_all_working_models()
  success = False
  reply_text = ""

  for model_name in models:
    try:
      logging.info(f"Спроба запиту до моделі: {model_name}")
      reply_text = await ask_groq_with_history(model_name, user_id, user_text)
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
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info(
        "Бот Міссі успішно запускається з пріоритетними моделями, пам'яттю на 20 реплік та трьома мовами..."
    )
    await dp.start_polling(bot)
  finally:
    await bot.session.close()
    logging.info("Сесію бота закрито.")


if __name__ == "__main__":
  try:
    asyncio.run(main())
  except (KeyboardInterrupt, SystemExit):
    logging.info("Робота бота зупинена.")
