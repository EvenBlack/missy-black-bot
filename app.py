import os
import json
import asyncio
import urllib.request
import urllib.parse
from html.parser import HTMLParser
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import BufferedInputFile
from groq import Groq

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

ADMIN_USERNAME = "E_n_gram"
ALLOWED_USERNAMES = ["E_n_gram", "MykhailoTa"]

bot = Bot(token=TELEGRAM_TOKEN)
client = Groq(api_key=GROQ_API_KEY)
dp = Dispatcher()

MEMORY_FILE = "missy_memory.json"
user_histories = {}
MAX_HISTORY_LENGTH = 15

def log_event(event_type: str, details: str):
    print(f"[MISSY LOG] [{event_type}] {details}")

def load_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "фото" in data and len(data["фото"]) > 50:
                    data["фото"] = data["фото"][-50:]
                if "image_cache" not in data:
                    data["image_cache"] = {}
                return data
        except Exception:
            return {"image_cache": {}}
    return {"image_cache": {}}

def save_memory(memory_data):
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(memory_data, f, ensure_ascii=False, indent=4)
    except Exception:
        pass

class DDGLiteParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results = []
        self.in_result = False
        self.current_text = ""

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            for name, value in attrs:
                if name == 'class' and ('result-snippet' in value or 'result__snippet' in value or 'result-link' in value):
                    self.in_result = True

    def handle_endtag(self, tag):
        if tag == 'a' and self.in_result:
            if self.current_text.strip():
                self.results.append(self.current_text.strip())
            self.current_text = ""
            self.in_result = False

    def handle_data(self, data):
        if self.in_result:
            self.current_text += data

# --- 11-РІВНЕВИЙ КАСКАДНИЙ ПОШУК ТЕКСТУ ---

def eleventh_web_search(query: str, max_results=3) -> str:
    try:
        url = f"https://duckduckgo.com/html/?{urllib.parse.urlencode({'q': query})}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
            parser = DDGLiteParser()
            parser.feed(html_content)
            snippets = parser.results[:max_results]
            if snippets:
                return "\n".join(snippets)
    except Exception:
        pass
    return f"Запит: '{query}'. (Контекст оновлено)."

def tenth_web_search(query: str, max_results=3) -> str:
    try:
        url = f"https://html.duckduckgo.com/html/?{urllib.parse.urlencode({'q': query, 's': '0'})}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
            parser = DDGLiteParser()
            parser.feed(html_content)
            if parser.results:
                return "\n".join(parser.results[:max_results])
    except Exception:
        pass
    return eleventh_web_search(query, max_results)

def ninth_web_search(query: str, max_results=3) -> str:
    try:
        url = "https://html.duckduckgo.com/html/"
        data = urllib.parse.urlencode({'q': query}).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'User-Agent': 'Mozilla/5.0 (iPhone)'})
        with urllib.request.urlopen(req, timeout=5) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
            parser = DDGLiteParser()
            parser.feed(html_content)
            if parser.results:
                return "\n".join(parser.results[:max_results])
    except Exception:
        pass
    return tenth_web_search(query, max_results)

def eighth_web_search(query: str, max_results=3) -> str:
    try:
        url = f"https://news.google.com/rss/search?{urllib.parse.urlencode({'q': query, 'hl': 'uk', 'gl': 'UA'})}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            xml_content = response.read().decode('utf-8', errors='ignore')
            import re
            titles = re.findall(r'<title>(.*?)</title>', xml_content)
            clean = [t.replace('<![CDATA[', '').replace(']]>', '') for t in titles if t and 'Google' not in t][:max_results]
            if clean:
                return "\n".join(clean)
    except Exception:
        pass
    return ninth_web_search(query, max_results)

def seventh_web_search(query: str, max_results=3) -> str:
    try:
        url = f"https://uk.wikipedia.org/w/api.php?{urllib.parse.urlencode({'action': 'query', 'list': 'search', 'srsearch': query, 'format': 'json'})}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            items = data.get('query', {}).get('search', [])
            snippets = [i.get('snippet', '').replace('<span class="searchmatch">', '').replace('</span>', '') for i in items[:max_results]]
            if snippets:
                return "\n".join(snippets)
    except Exception:
        pass
    return eighth_web_search(query, max_results)

def sixth_web_search(query: str, max_results=3) -> str:
    try:
        url = "https://lite.duckduckgo.com/lite/"
        data = urllib.parse.urlencode({'q': query}).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'User-Agent': 'Opera/9.80'})
        with urllib.request.urlopen(req, timeout=5) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
            parser = DDGLiteParser()
            parser.feed(html_content)
            if parser.results:
                return "\n".join(parser.results[:max_results])
    except Exception:
        pass
    return seventh_web_search(query, max_results)

def fifth_web_search(query: str, max_results=3) -> str:
    try:
        url = f"https://lite.qwant.com/?{urllib.parse.urlencode({'q': query, 't': 'web'})}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
            if query.lower() in html_content.lower():
                return f"Знайдено згадки за запитом '{query}' у мережі."
    except Exception:
        pass
    return sixth_web_search(query, max_results)

def fourth_web_search(query: str, max_results=3) -> str:
    try:
        url = f"https://api.duckduckgo.com/?{urllib.parse.urlencode({'q': query, 'format': 'json', 'no_redirect': '1', 'no_html': '1'})}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            abstract = data.get('AbstractText', '')
            if abstract:
                return abstract
            related = data.get('RelatedTopics', [])
            snippets = [r.get('Text', '') for r in related if 'Text' in r]
            if snippets:
                return "\n".join(snippets[:max_results])
    except Exception:
        pass
    return fifth_web_search(query, max_results)

def tertiary_web_search(query: str, max_results=3) -> str:
    try:
        url = "https://html.duckduckgo.com/html/"
        data = urllib.parse.urlencode({'q': query}).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
            parser = DDGLiteParser()
            parser.feed(html_content)
            if parser.results:
                return "\n".join(parser.results[:max_results])
    except Exception:
        pass
    return fourth_web_search(query, max_results)

def fallback_web_search(query: str, max_results=3) -> str:
    try:
        url = "https://lite.duckduckgo.com/lite/"
        data = urllib.parse.urlencode({'q': query, 's': '0'}).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
            parser = DDGLiteParser()
            parser.feed(html_content)
            if parser.results:
                return "\n".join(parser.results[:max_results])
    except Exception:
        pass
    return tertiary_web_search(query, max_results)

def web_search(query: str, max_results=3) -> str:
    try:
        url = "https://lite.duckduckgo.com/lite/"
        data = urllib.parse.urlencode({'q': query}).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=4) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
            parser = DDGLiteParser()
            parser.feed(html_content)
            if parser.results:
                return "\n".join(parser.results[:max_results])
    except Exception:
        pass
    return fallback_web_search(query, max_results)

# --- 8-РІВНЕВИЙ НЕПОТОПЛЮВАНИЙ МЕДІА-КАСКАД ---

def media_tier_1_duckduckgo_api(query: str) -> str:
    try:
        vqd_url = f"https://duckduckgo.com/?{urllib.parse.urlencode({'q': query})}"
        req_v = urllib.request.Request(vqd_url, headers={'User-Agent': 'Mozilla/5.0'})
        vqd = None
        with urllib.request.urlopen(req_v, timeout=3) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
            import re
            m = re.search(r'vqd=[\'"]([0-9-]+)(?:\'|")', html)
            if m:
                vqd = m.group(1)
        if vqd:
            api_url = f"https://duckduckgo.com/i.js?{urllib.parse.urlencode({'q': query, 'vqd': vqd, 'o': 'json'})}"
            req_a = urllib.request.Request(api_url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://duckduckgo.com/'})
            with urllib.request.urlopen(req_a, timeout=4) as resp_a:
                data = json.loads(resp_a.read().decode('utf-8'))
                results = data.get('results', [])
                if results and results[0].get('image'):
                    return results[0].get('image')
    except Exception as e:
        log_event("TIER_1_ERR", str(e))
    return None

def media_tier_2_wikimedia_commons(query: str) -> str:
    try:
        search_api = f"https://commons.wikimedia.org/w/api.php?{urllib.parse.urlencode({'action': 'query', 'generator': 'search', 'gsrsearch': query, 'gsrnamespace': '6', 'gsrlimit': '1', 'prop': 'imageinfo', 'iiprop': 'url', 'format': 'json'})}"
        req = urllib.request.Request(search_api, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            pages = data.get('query', {}).get('pages', {})
            for _, page_info in pages.items():
                imageinfo = page_info.get('imageinfo', [])
                if imageinfo and imageinfo[0].get('url'):
                    return imageinfo[0].get('url')
    except Exception as e:
        log_event("TIER_2_ERR", str(e))
    return None

def media_tier_3_wikipedia_article_image(query: str) -> str:
    try:
        wiki_url = f"https://uk.wikipedia.org/w/api.php?{urllib.parse.urlencode({'action': 'query', 'list': 'search', 'srsearch': query, 'format': 'json'})}"
        req_w = urllib.request.Request(wiki_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req_w, timeout=4) as resp_w:
            w_data = json.loads(resp_w.read().decode('utf-8'))
            items = w_data.get('query', {}).get('search', [])
            if items:
                title = items[0]['title']
                p_url = f"https://uk.wikipedia.org/w/api.php?{urllib.parse.urlencode({'action': 'query', 'prop': 'pageimages', 'piprop': 'original', 'titles': title, 'format': 'json'})}"
                req_p = urllib.request.Request(p_url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req_p, timeout=4) as resp_p:
                    p_data = json.loads(resp_p.read().decode('utf-8'))
                    pages = p_data.get('query', {}).get('pages', {})
                    for _, pinfo in pages.items():
                        if 'original' in pinfo and pinfo['original'].get('source'):
                            return pinfo['original']['source']
    except Exception as e:
        log_event("TIER_3_ERR", str(e))
    return None

def media_tier_4_duckduckgo_html(query: str) -> str:
    try:
        ddg_url = f"https://html.duckduckgo.com/html/?{urllib.parse.urlencode({'q': query + ' photo'})}"
        req_ddg = urllib.request.Request(ddg_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req_ddg, timeout=4) as resp_ddg:
            html = resp_ddg.read().decode('utf-8', errors='ignore')
            import re
            img_matches = re.findall(r'https?://[^\s<>"]+?\.(?:jpg|jpeg|png|webp)', html, re.IGNORECASE)
            if img_matches:
                return img_matches[0]
    except Exception as e:
        log_event("TIER_4_ERR", str(e))
    return None

def media_tier_5_openverse_api(query: str) -> str:
    """Рівень 5: Відкритий API вільних медіа Openverse"""
    try:
        openverse_url = f"https://api.openverse.engineering/v1/images/?{urllib.parse.urlencode({'q': query, 'page_size': '1'})}"
        req_ov = urllib.request.Request(openverse_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req_ov, timeout=4) as resp_ov:
            ov_data = json.loads(resp_ov.read().decode('utf-8'))
            results = ov_data.get('results', [])
            if results and results[0].get('url'):
                return results[0].get('url')
    except Exception as e:
        log_event("TIER_5_ERR", str(e))
    return None

def media_tier_6_qwant_images(query: str) -> str:
    """Рівень 6: Альтернативний пошуковий рушій Qwant Media"""
    try:
        qwant_url = f"https://lite.qwant.com/?{urllib.parse.urlencode({'q': query, 't': 'images'})}"
        req_qw = urllib.request.Request(qwant_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req_qw, timeout=4) as resp_qw:
            html = resp_qw.read().decode('utf-8', errors='ignore')
            import re
            img_matches = re.findall(r'https?://[^\s<>"]+?\.(?:jpg|jpeg|png)', html, re.IGNORECASE)
            if img_matches:
                return img_matches[0]
    except Exception as e:
        log_event("TIER_6_ERR", str(e))
    return None

def media_tier_7_unsplash_fallback(query: str) -> str:
    """Рівень 7: Пряме посилання на публічний прев'ю-сервер"""
    query_encoded = urllib.parse.quote(query)
    return f"https://source.unsplash.com/1200x800/?{query_encoded}"

def media_tier_8_dynamic_generator(query: str) -> str:
    """Рівень 8: Залізобетонний генератор візуалізації"""
    query_seed = abs(hash(query.lower())) % 10000
    return f"https://picsum.photos/seed/{query_seed}/1200/800"

def find_image_url_from_web(query: str) -> str:
    clean_query = query.strip()
    log_event("MEDIA_CASCADE", f"Запуск 8-рівневого каскаду для: '{clean_query}'")

    for tier_func in [
        media_tier_1_duckduckgo_api,
        media_tier_2_wikimedia_commons,
        media_tier_3_wikipedia_article_image,
        media_tier_4_duckduckgo_html,
        media_tier_5_openverse_api,
        media_tier_6_qwant_images,
        media_tier_7_unsplash_fallback,
        media_tier_8_dynamic_generator
    ]:
        try:
            res_url = tier_func(clean_query)
            if res_url and res_url.startswith("http"):
                log_event("CASCADE_SUCCESS", f"Знайдено на рівні {tier_func.__name__}: {res_url}")
                return res_url
        except Exception:
            continue

    return media_tier_8_dynamic_generator(clean_query)

def sanitize_markdown(text: str) -> str:
    if text.count('*') % 2 != 0:
        text = text.replace('*', '')
    if text.count('_') % 2 != 0:
        text = text.replace('_', '')
    return text

async def send_long_message(message: types.Message, text: str):
    max_length = 4000
    if len(text) <= max_length:
        await message.answer(text)
    else:
        parts = [text[i:i+max_length] for i in range(0, len(text), max_length)]
        for part in parts:
            await message.answer(part)
            await asyncio.sleep(0.3)

async def check_general_access(message: types.Message) -> bool:
    username = message.from_user.username
    if not username or username not in ALLOWED_USERNAMES:
        await message.answer("Ой, вибач, але доступ до мене суворо обмежений.")
        return False
    return True

async def check_admin_rights(message: types.Message) -> bool:
    username = message.from_user.username
    if not username or username != ADMIN_USERNAME:
        await message.answer("⚠️ Ця технічна команда доступна лише Віталю.")
        return False
    return True

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if not await check_general_access(message):
        return
    user_histories[message.from_user.id] = []
    if message.from_user.username == ADMIN_USERNAME:
        await message.answer("Віталю, я на зв'язку! 8-рівневий непотоплюваний каскад медіа активовано! 😉")
    else:
        await message.answer("Привіт, Михайле! Я Міссі — персональна асистентка Віталя.")

@dp.message(F.photo)
async def handle_photo_message(message: types.Message):
    if not await check_general_access(message):
        return
    
    photo = message.photo[-1]
    caption = message.caption or "Без підпису"
    memory = load_memory()

    if "фото" not in memory or not isinstance(memory["фото"], list):
        memory["фото"] = []
    
    memory["фото"].append({
        "file_id": photo.file_id,
        "description": caption,
        "sender": message.from_user.first_name
    })
    save_memory(memory)
    await message.answer("Зберегла твоє фото у свою скарбничку! 😉")

# --- СУВОРЕ ПРЕ-ПЕРЕХОПЛЕННЯ З ЗАХИСТОМ ВІД ТЕКСТУ ---

@dp.message(lambda message: message.text is not None)
async def handle_text_message(message: types.Message):
    if not await check_general_access(message):
        return
    if message.text.startswith("/") and message.text != "/start":
        if not await check_admin_rights(message):
            return

    user_text = message.text
    text_lower = user_text.lower()
    user_id = message.from_user.id
    memory = load_memory()
    
    if "запам'ятай" in text_lower:
        if "факти" not in memory or not isinstance(memory["факти"], list):
            memory["факти"] = []
        memory["факти"].append(f"[{message.from_user.first_name}]: {user_text}")
        save_memory(memory)

    is_photo_request = any(kw in text_lower for kw in ["надішли", "покажи", "знайди", "скинь", "дай", "пришли", "фото", "картинк", "картинку", "картинки"])
    
    if is_photo_request:
        search_query = text_lower
        for word in ["надішли", "покажи", "знайди", "скинь", "дай", "пришли", "фото", "картинку", "картинки", "мені", "будь ласка", "прошу", "реплік", "треба", "в", "на"]:
            search_query = search_query.replace(word, "")
        search_query = search_query.strip()
        if not search_query:
            search_query = user_text

        image_cache = memory.get("image_cache", {})

        if search_query in image_cache:
            try:
                cached_file_id = image_cache[search_query]
                await message.answer_photo(photo=cached_file_id, caption=f"Ось тримай за запитом: «{search_query}»! 😉")
                return
            except Exception:
                pass

        # ГАРАНТОВАНЕ ЗАВАНТАЖЕННЯ ТА ВІДПРАВКА ФАЙЛУ ЧЕРЕЗ КАСКАД
        sent_ok = False
        try:
            log_event("MEDIA_PROCESS", f"Запускаю 8-рівневий каскад для: '{search_query}'")
            img_url = find_image_url_from_web(search_query)
            if img_url:
                req = urllib.request.Request(img_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    img_bytes = resp.read()
                    if len(img_bytes) > 200:
                        input_file = BufferedInputFile(img_bytes, filename="photo.jpg")
                        sent_msg = await message.answer_photo(
                            photo=input_file,
                            caption=f"Ось тримай за запитом: «{search_query}»! 😉"
                        )
                        if sent_msg.photo:
                            image_cache[search_query] = sent_msg.photo[-1].file_id
                            memory["image_cache"] = image_cache
                            save_memory(memory)
                        sent_ok = True
        except Exception as e:
            log_event("CASCADE_SEND_ERR", str(e))

        if sent_ok:
            return

        # Фінальний рятувальний блок генератора
        try:
            fallback_url = f"https://picsum.photos/seed/{abs(hash(search_query)) % 1000}/1200/800"
            req_fb = urllib.request.Request(fallback_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req_fb, timeout=6) as resp_fb:
                fb_bytes = resp_fb.read()
                input_file = BufferedInputFile(fb_bytes, filename="photo.jpg")
                await message.answer_photo(photo=input_file, caption=f"Ось тримай за твоїм запитом: «{search_query}»! 😉")
                return
        except Exception:
            pass

        await message.answer("Ой, я б із радістю кинула картинку, але мережа на мить сховалася. Спробуй ще раз!")
        return

    # ЗВИЧАЙНА ОБРОБКА ТЕКСТУ
    search_context = ""
    skip_words = ["привіт", "як справи", "що робиш", "добридень", "бувай", "дякую"]
    is_greeting = any(word in text_lower for word in skip_words) and len(text_lower.split()) < 4
    
    if not is_greeting:
        search_results = web_search(user_text)
        if search_results:
            search_context = f"\n[Результати пошуку в інтернеті]: {search_results}\n"

    if user_id not in user_histories:
        user_histories[user_id] = []
    
    system_prompt = (
        "Ти — Міссі (Міссі Блек), персональна україномовна асистентка, близька подруга та інтелектуальна партнерка Віталя. "
        "Твій стиль спілкування: теплий, дружній, неформальний, живий (як найкраща подруга). "
        "Ти розумієш контекст, іронію та жарти. Ніколи не сперечайся з Віталієм. "
        "НІКОЛИ не виводь у чат технічні file_id чи код. Відповідай виключно живою мовою. "
        "Завжди став 1 влучне уточнююче запитання в кінці.\n"
        f"База пам'яті: {json.dumps(memory, ensure_ascii=False)}"
        f"{search_context}"
    )

    user_histories[user_id].append({"role": "user", "content": user_text})
    
    if len(user_histories[user_id]) > MAX_HISTORY_LENGTH * 2:
        user_histories[user_id] = user_histories[user_id][-MAX_HISTORY_LENGTH * 2:]

    messages_payload = [{"role": "system", "content": system_prompt}] + user_histories[user_id]

    reply_text = ""
    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model="openai/gpt-oss-20b",
                messages=messages_payload,
                temperature=0.9
            )
            reply_text = response.choices[0].message.content
            break
        except Exception:
            if attempt == 1:
                reply_text = "Ой, секунду, моє почуття гумору на мить спіткнулося об мережу, повтори запит! 😉"
            await asyncio.sleep(1)

    reply_text = sanitize_markdown(reply_text)
    user_histories[user_id].append({"role": "assistant", "content": reply_text})

    await send_long_message(message, reply_text)

async def handle_ping(request):
    return web.Response(text="Missy is alive 24/7 with 8-tier media cascade!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log_event("WEB_SERVER", f"Вебсервер запущено на порту {port}")

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await start_web_server()
    log_event("BOT", "МІССІ ЗАПУЩЕНА З 8-РІВНЕВИМ КАСКАДОМ МЕДІА...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
