import os
import re
import json
import asyncio
import logging
from html.parser import HTMLParser
from urllib.parse import urlencode
from io import BytesIO
from pathlib import Path

import aiohttp
from aiohttp import web
from PIL import Image, UnidentifiedImageError
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import BufferedInputFile
from groq import Groq

# Render Environment Variables
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
OPENVERSE_CLIENT_ID = os.getenv("OPENVERSE_CLIENT_ID")
OPENVERSE_CLIENT_SECRET = os.getenv("OPENVERSE_CLIENT_SECRET")

ADMIN_USERNAME = "E_n_gram"
ALLOWED_USERNAMES = {"E_n_gram", "MykhailoTa"}
MEMORY_FILE = Path("missy_memory.json")
MAX_HISTORY = 15
MAX_IMAGE_BYTES = 8 * 1024 * 1024
IMAGE_SEARCH_TIMEOUT = 14
USER_AGENT = "MissyTelegramBot/2.0 (image search)"
TIMEOUT = aiohttp.ClientTimeout(total=5, connect=3, sock_read=4)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s:%(name)s:%(message)s")
log = logging.getLogger("missy")

if not TELEGRAM_TOKEN:
    raise RuntimeError("Не задано TELEGRAM_TOKEN у Render Environment")
if not GROQ_API_KEY:
    raise RuntimeError("Не задано GROQ_API_KEY у Render Environment")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
groq_client = Groq(api_key=GROQ_API_KEY, timeout=8.0, max_retries=0)
histories = {}
memory_lock = asyncio.Lock()


def event(name, detail):
    log.info("[%s] %s", name, detail)


def load_memory():
    if not MEMORY_FILE.exists():
        return {"image_cache": {}, "фото": [], "факти": []}
    try:
        with MEMORY_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("JSON root is not an object")
        for key, default in (("image_cache", {}), ("фото", []), ("факти", [])):
            if not isinstance(data.get(key), type(default)):
                data[key] = default
        return data
    except Exception:
        log.exception("Не вдалося прочитати missy_memory.json; використовую порожню пам'ять")
        return {"image_cache": {}, "фото": [], "факти": []}


def save_memory(data):
    tmp = MEMORY_FILE.with_suffix(".json.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, MEMORY_FILE)
    except Exception:
        log.exception("Помилка збереження пам'яті")
        tmp.unlink(missing_ok=True)


async def change_memory(callback):
    async with memory_lock:
        data = load_memory()
        callback(data)
        save_memory(data)
        return data


def has_access(message):
    return bool(message.from_user and message.from_user.username in ALLOWED_USERNAMES)


async def check_access(message):
    if not has_access(message):
        await message.answer("Ой, вибач, але доступ до мене суворо обмежений.")
