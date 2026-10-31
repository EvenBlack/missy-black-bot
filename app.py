        event("WEB_SEARCH_ERROR", f"{type(exc).__name__}: {exc}")
        return ""

def clean_markdown(text):
    if text.count("*") % 2:
        text = text.replace("*", "")
    if text.count("_") % 2:
        text = text.replace("_", "")
    return text


async def send_long(message, text):
    for i in range(0, len(text), 4000):
        await message.answer(text[i:i + 4000])


@dp.message(Command("start"))
async def start_command(message: types.Message):
    if not await check_access(message):
        return
    histories[message.from_user.id] = []
    await message.answer("Я на зв'язку! Пошук справжніх фотографій оновлено. 😉")


@dp.message(F.photo)
async def receive_photo(message: types.Message):
    if not await check_access(message):
        return
    photo = message.photo[-1]
    def add(data):
        data["фото"].append({
            "file_id": photo.file_id,
            "description": message.caption or "Без підпису",
            "sender": message.from_user.first_name,
        })
        data["фото"] = data["фото"][-50:]
    await change_memory(add)
    await message.answer("Зберегла твоє фото у свою скарбничку! 😉")


@dp.message(F.text)
async def text_handler(message: types.Message):
    if not await check_access(message):
        return
    text = message.text.strip()
    if text.startswith("/") and text != "/start" and message.from_user.username != ADMIN_USERNAME:
        await message.answer("⚠️ Ця технічна команда доступна лише адміністратору.")
        return

    uid = message.from_user.id
    history = histories.setdefault(uid, [])
    memory = load_memory()

    if "запам'ятай" in text.lower():
        def add_fact(data):
            data["факти"].append(f"[{message.from_user.first_name}]: {text}")
            data["факти"] = data["факти"][-100:]
        await change_memory(add_fact)
        memory = load_memory()

    if image_request(text):
        await message.bot.send_chat_action(message.chat.id, "upload_photo")
        try:
            query = await asyncio.wait_for(
                asyncio.to_thread(refine_query, text, history.copy()), timeout=9
            )
        except asyncio.TimeoutError:
            query = text
            event("QUERY_REFINEMENT_TIMEOUT", "Using original query")

        cached = memory.get("image_cache", {}).get(query)
        if cached:
            try:
                await message.answer_photo(cached, caption="Ось фото за твоїм запитом! 😉")
                history.extend([
                    {"role": "user", "content": text},
                    {"role": "assistant", "content": "[Фото з кешу]"},
                ])
                return
            except Exception as exc:
                event("IMAGE_CACHE_ERROR", str(exc))

        event("IMAGE_SEARCH", repr(query))
        try:
            found = await asyncio.wait_for(find_image(query), timeout=IMAGE_SEARCH_TIMEOUT)
        except asyncio.TimeoutError:
            found = None
            event("IMAGE_SEARCH_TIMEOUT", f"Search exceeded {IMAGE_SEARCH_TIMEOUT}s")

        if found:
            data, ext, candidate = found
            try:
                sent = await message.answer_photo(
                    BufferedInputFile(data, filename=f"missy_photo.{ext}"),
                    caption=f"Ось що знайшла: {candidate['title'][:180]}",
                )
                if sent.photo:
                    def cache(data_obj):
                        data_obj["image_cache"][query] = sent.photo[-1].file_id
                        if len(data_obj["image_cache"]) > 100:
                            data_obj["image_cache"].pop(next(iter(data_obj["image_cache"])))
                    await change_memory(cache)
                history.extend([
                    {"role": "user", "content": text},
                    {"role": "assistant", "content": "[Надіслано справжнє фото]"},
                ])
                return
            except Exception as exc:
                event("TELEGRAM_PHOTO_SEND_ERROR", f"{type(exc).__name__}: {exc}")

        await message.answer(
            "Не знайшла відповідного справжнього фото у доступних безкоштовних джерелах. "
            "Спробуй уточнити запит або сформулювати його інакше."
        )
        history.extend([
            {"role": "user", "content": text},
            {"role": "assistant", "content": "[Фото не знайдено]"},
        ])
        return

    history.append({"role": "user", "content": text})
    history[:] = history[-MAX_HISTORY * 2:]
    search_context = ""
    greeting_words = ("привіт", "як справи", "що робиш", "добридень", "бувай", "дякую")
    is_greeting = any(word in text.lower() for word in greeting_words) and len(text.split()) < 4
    if not is_greeting:
        search_results = await web_search(text)
        if search_results:
            search_context = f"\\nРезультати вебпошуку (перевір їхню доречність):\\n{search_results}"
    system = (
        "Ти — Міссі (Міссі Блек), тепла, дружня україномовна асистентка Віталя. "
        "Враховуй історію та базу пам'яті. Не показуй технічні токени чи file_id.\\n"
        f"База пам'яті: {json.dumps(memory, ensure_ascii=False)[:12000]}"
        f"{search_context}"
    )
    payload = [{"role": "system", "content": system}] + history
    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(
                groq_client.chat.completions.create,
                model=GROQ_MODEL, messages=payload, temperature=0.8, max_tokens=900,
            ),
            timeout=10,
        )
        answer = response.choices[0].message.content or "Не змогла сформувати відповідь."
    except Exception as exc:
        event("GROQ_ERROR", f"{type(exc).__name__}: {exc}")
        answer = "Зараз не можу зв'язатися з мовною моделлю. Спробуй трохи пізніше."
    answer = clean_markdown(answer)
    history.append({"role": "assistant", "content": answer})
    history[:] = history[-MAX_HISTORY * 2:]
    await send_long(message, answer)


async def ping(request):
    return web.Response(text="Missy is alive")


async def start_web():
    app = web.Application()
    app.router.add_get("/", ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "10000"))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    event("WEB_SERVER", f"Listening on port {port}")
    return runner


async def main():
    runner = None
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        runner = await start_web()
        event("BOT", "MISSY STARTED")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        if runner:
            await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
