"""Owner-only Instagram menu and Kyiv morning follower reports.

No proactive DMs to followers, no posting without owner approval.
"""
import asyncio
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import logging

from aiogram import F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from instagram_growth import weekly_plan, queue_summary, fetch_account_overview, save_growth_snapshot, add_photo_draft, approve_draft, discard_draft
from instagram_handoff import list_dialogs, set_dialog_mode

log = logging.getLogger("instagram_menu")
KYIV = ZoneInfo("Europe/Kyiv")


def menu_keyboard():
    b = lambda label, action: InlineKeyboardButton(text=label, callback_data="igmenu:" + action)
    return InlineKeyboardMarkup(inline_keyboard=[
        [b("📊 Підписники", "stats"), b("📅 План на тиждень", "plan")],
        [b("✍️ Підпис до фото", "caption"), b("🖼 Підготувати допис", "photo")],
        [b("📋 Черга дописів", "queue"), b("💬 Instagram Direct", "dialogs")],
        [b("🔔 Звіт о 09:00", "report"), b("🔄 Оновити меню", "home")],
    ])


def dialog_keyboard(dialogs):
    b = lambda label, action: InlineKeyboardButton(text=label, callback_data="igmenu:" + action)
    rows = []
    for sender_id, entry in dialogs[:8]:
        if not str(sender_id).isdigit() or len(str(sender_id)) > 40:
            continue
        human = entry.get("mode") == "human"
        rows.append([b(
            ("▶️ Повернути ШІ" if human else "⏸ Взяти вручну") + " · " + str(sender_id)[-6:],
            ("resume:" if human else "pause:") + str(sender_id),
        )])
    rows.append([b("⬅️ Меню", "home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def report_text(overview, previous=None):
    followers = overview.get("followers_count")
    posts = overview.get("media_count")
    user = overview.get("username") or "Настя"
    if not isinstance(followers, int):
        return "📊 Instagram @" + user + ": Meta поки не повертає кількість підписників. Дивись /ig_stats."
    delta = ""
    if isinstance(previous, int):
        delta = f" ({followers-previous:+d} від попереднього вимірювання)"
    return (
        f"☀️ Ранковий звіт Насті\nInstagram @{user}\n"
        f"👥 Підписники: {followers}{delta}\n"
        f"🖼 Дописи: {posts if isinstance(posts, int) else 'недоступно'}"
    )


def should_send(now, subscription):
    if not isinstance(subscription, dict) or not subscription.get("enabled"):
        return False
    if not isinstance(subscription.get("chat_id"), int):
        return False
    local = now.astimezone(KYIV)
    return (local.hour == 9
            and local.date().isoformat() >= subscription.get("first_date", "0000-00-00")
            and subscription.get("last_date") != local.date().isoformat())


async def enable_report(change_memory, chat_id):
    if not isinstance(chat_id, int) or chat_id <= 0:
        raise ValueError("Invalid Telegram chat ID")
    def update(data):
        item = data.setdefault("instagram_morning_report", {})
        if not isinstance(item, dict):
            raise ValueError("Invalid report state")
        item["chat_id"] = chat_id
        item["enabled"] = True
        # Opt-in during the day must not trigger a surprise report immediately.
        item["first_date"] = (datetime.now(KYIV).date() + timedelta(days=1)).isoformat()
    await change_memory(update)


async def disable_report(change_memory):
    def update(data):
        item = data.get("instagram_morning_report")
        if isinstance(item, dict):
            item["enabled"] = False
    await change_memory(update)


async def daily_report_once(bot, read_memory, change_memory, now=None, fetch=fetch_account_overview):
    now = now or datetime.now(timezone.utc)
    state = await read_memory()
    sub = state.get("instagram_morning_report")
    if not should_send(now, sub):
        return False
    chat_id = sub["chat_id"]
    today = now.astimezone(KYIV).date().isoformat()
    old_snapshots = state.get("instagram_growth_snapshots", {})
    previous = None
    if isinstance(old_snapshots, dict):
        older = [k for k in old_snapshots if k < today]
        if older:
            value = old_snapshots[max(older)]
            previous = value.get("followers") if isinstance(value, dict) else None
    try:
        overview = await fetch(read_memory)
        message = report_text(overview, previous)
    except Exception as exc:
        log.warning("INSTAGRAM_MORNING_REPORT stats_unavailable reason=%s", type(exc).__name__)
        overview = None
        message = "☀️ Звіт Насті на 09:00: кількість підписників поки недоступна через Instagram API. Перевір /ig_stats."

    # Reserve once-per-day delivery before sending to avoid duplicate reports after restart.
    claimed = False
    def reserve(data):
        nonlocal claimed
        current = data.get("instagram_morning_report")
        if (isinstance(current, dict) and current.get("enabled")
                and current.get("chat_id") == chat_id
                and today >= current.get("first_date", "0000-00-00")
                and current.get("last_date") != today):
            current["last_date"] = today
            claimed = True
    await change_memory(reserve)
    if not claimed:
        return False
    try:
        await bot.send_message(chat_id, message)
    except Exception as exc:
        log.warning("INSTAGRAM_MORNING_REPORT delivery_failed reason=%s", type(exc).__name__)
        # Leave last_date for at-most-once attempt, to avoid duplicated notifications.
        return False
    if overview is not None:
        try:
            await save_growth_snapshot(change_memory, overview)
        except Exception as exc:
            log.warning("INSTAGRAM_MORNING_REPORT snapshot_error reason=%s", type(exc).__name__)
    log.info("INSTAGRAM_MORNING_REPORT sent")
    return True


async def daily_report_loop(bot, read_memory, change_memory):
    while True:
        try:
            await daily_report_once(bot, read_memory, change_memory)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("INSTAGRAM_MORNING_REPORT loop_error reason=%s", type(exc).__name__)
        await asyncio.sleep(40)


def install_instagram_menu(dp, bot, admin_username, read_memory, change_memory, generate_caption=None, archive_photo=None, list_owner_photos=None):
    awaiting_input = {}
    def authorized(user, message):
        return bool(
            user and (user.username or "").casefold() == admin_username.casefold()
            and message and message.chat.type == "private"
            and message.chat.id == user.id
        )

    @dp.message(Command("ig_menu", "menu"))
    async def show_menu(message):
        if not authorized(message.from_user, message):
            return
        await message.answer("🌿 Меню Насті й Міссі. Обери дію:", reply_markup=menu_keyboard())

    async def send_photo_draft(message, archive_id, owner_id):
        caption = (
            "Coffee, messy curls, and crunchy autumn leaves 🍂☕🧡\n\n"
            "Honestly, I could stay out here all day… until my coffee gets cold 😂\n\n"
            "Are you a fall person or do you already miss summer?\n\n"
            "#AutumnVibes #CurlyHair #CozyDays #FallOutfit #CoffeeLover"
        )
        draft_id = await add_photo_draft(change_memory, archive_id, owner_id, caption)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Погодити чернетку", callback_data="igmenu:approvephoto:" + draft_id)],
            [InlineKeyboardButton(text="❌ Скасувати", callback_data="igmenu:cancelphoto:" + draft_id)],
        ])
        await message.answer(
            "🖼 Чернетка №" + draft_id + "\n\n" + caption +
            "\n\nНічого не опубліковано. Навіть погодження не запускає "
            "публікацію, доки автопублікатор вимкнений.",
            reply_markup=kb,
        )

    @dp.callback_query(F.data.startswith("igmenu:"))
    async def handle_menu(query):
        if not authorized(query.from_user, query.message):
            await query.answer("Немає доступу.", show_alert=True)
            return
        await query.answer()
        action = query.data.partition(":")[2]
        message = query.message
        if action == "home":
            awaiting_input.pop(query.from_user.id, None)
            await message.answer("🌿 Меню Насті:", reply_markup=menu_keyboard())
            return
        if action == "plan":
            lines = ["📅 Контент-план Насті на 7 днів:"]
            lines += [f"{r['day']}. {r['topic']}: {r['idea']}" for r in weekly_plan()]
            await message.answer("\n".join(lines), reply_markup=menu_keyboard())
        elif action == "stats":
            try:
                overview = await fetch_account_overview(read_memory)
                await save_growth_snapshot(change_memory, overview)
                await message.answer(report_text(overview), reply_markup=menu_keyboard())
            except Exception as exc:
                await message.answer(
                    "Статистика Instagram наразі недоступна. " + str(exc)[:220],
                    reply_markup=menu_keyboard(),
                )
        elif action == "caption":
            awaiting_input[query.from_user.id] = "caption"
            await message.answer("✍️ Напиши тему для підпису одним повідомленням — без команд. Наприклад: autumn walk and coffee.")
        elif action == "photo":
            awaiting_input[query.from_user.id] = "photo"
            extra = []
            if list_owner_photos is not None:
                extra = [InlineKeyboardButton(text="📂 Обрати з уже збережених фото", callback_data="igmenu:archive")]
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [*extra] if extra else [InlineKeyboardButton(text="⬅️ Меню", callback_data="igmenu:home")],
                [InlineKeyboardButton(text="⬅️ Меню", callback_data="igmenu:home")],
            ]) if extra else menu_keyboard()
            await message.answer(
                "🖼 Надішли фото Насті наступним повідомленням або обери вже збережене в архіві.",
                reply_markup=keyboard,
            )
        elif action == "archive":
            awaiting_input.pop(query.from_user.id, None)
            if list_owner_photos is None:
                await message.answer("Список фото недоступний.")
                return
            try:
                photos = await list_owner_photos(query.from_user.id)
                rows = []
                for photo in photos[:8]:
                    pid = str(photo.get("archive_id", ""))
                    if not __import__("re").fullmatch(r"[a-f0-9]{24}", pid):
                        continue
                    label = str(photo.get("caption") or photo.get("filename") or "Фото")[:32]
                    rows.append([InlineKeyboardButton(text="📸 " + label, callback_data="igmenu:pick:" + pid)])
                rows.append([InlineKeyboardButton(text="⬅️ Меню", callback_data="igmenu:home")])
                await message.answer(
                    "📂 Обери потрібне фото з архіву. За можливості перевір його дату й назву:",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
                )
            except Exception:
                await message.answer("Не вдалося відкрити архів фото.")
        elif action.startswith("pick:"):
            photo_id = action.partition(":")[2]
            if not __import__("re").fullmatch(r"[a-f0-9]{24}", photo_id):
                return
            if list_owner_photos is None:
                return
            try:
                photos = await list_owner_photos(query.from_user.id)
                if not any(str(p.get("archive_id")) == photo_id for p in photos):
                    await message.answer("Цього фото вже немає в доступному списку архіву.")
                    return
                await send_photo_draft(message, photo_id, query.from_user.id)
            except Exception:
                await message.answer("Не вдалося підготувати чернетку з архіву.")
        elif action.startswith(("approvephoto:", "cancelphoto:")):
            cmd, _, draft_id = action.partition(":")
            if not __import__("re").fullmatch(r"[a-f0-9]{8}", draft_id):
                await message.answer("Неправильний код чернетки.")
                return
            try:
                if cmd == "approvephoto":
                    await approve_draft(change_memory, read_memory, draft_id)
                    await message.answer(
                        "✅ Чернетку погоджено. Автопублікатор ще вимкнено: "
                        "фото не опублікується до окремої перевірки та увімкнення публікацій.",
                        reply_markup=menu_keyboard(),
                    )
                else:
                    await discard_draft(change_memory, draft_id)
                    await message.answer("🗑 Чернетку скасовано. Фото лишилося у твоєму приватному архіві.",
                                         reply_markup=menu_keyboard())
            except Exception:
                await message.answer("Не вдалося змінити чернетку. Вона могла бути вже погоджена або скасована.")
        elif action == "queue":
            try:
                items = await queue_summary(read_memory)
                lines = ["📋 Черга дописів:"]
                lines += [f"{key}: {item.get('status', '?')} — {item.get('due_at', '?')}" for key, item in items[-10:]]
                await message.answer("\n".join(lines) if items else "Черга дописів порожня.", reply_markup=menu_keyboard())
            except Exception:
                await message.answer("Черга зараз недоступна.")
        elif action == "dialogs":
            try:
                dialogs = await list_dialogs(read_memory)
                await message.answer(
                    "💬 Ручне керування: натисни кнопку біля потрібного діалогу. "
                    "Якщо зупиниш ШІ, він не відповідатиме цьому підписнику.",
                    reply_markup=dialog_keyboard(dialogs),
                )
            except Exception:
                await message.answer("Неможливо прочитати список діалогів.")
        elif action in {"report", "report_off"}:
            try:
                if action == "report":
                    await enable_report(change_memory, message.chat.id)
                    await message.answer(
                        "🔔 Увімкнено: звіт щодня після 09:00 за Києвом. "
                        "Час доставки залежить від доступності Render. "
                        "Щоб вимкнути, натисни кнопку нижче.",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🔕 Вимкнути ранкові звіти", callback_data="igmenu:report_off")],
                            [InlineKeyboardButton(text="⬅️ Меню", callback_data="igmenu:home")],
                        ]),
                    )
                else:
                    await disable_report(change_memory)
                    await message.answer("🔕 Ранкові звіти вимкнені.", reply_markup=menu_keyboard())
            except Exception:
                await message.answer("Не вдалося зберегти налаштування звіту.")
        elif action.startswith(("pause:", "resume:")):
            mode, _, sender_id = action.partition(":")
            if not sender_id.isdigit() or len(sender_id) > 40:
                return
            try:
                ok = await set_dialog_mode(change_memory, read_memory, sender_id,
                                           "human" if mode == "pause" else "auto")
                if ok:
                    await message.answer(
                        "⏸ Тепер відповідає людина." if mode == "pause" else "▶️ ШІ знову може відповідати.",
                        reply_markup=menu_keyboard(),
                    )
                else:
                    await message.answer("Діалог не знайдено.")
            except Exception:
                await message.answer("Зберегти перемикання не вдалося.")

    @dp.message(F.text, lambda m: awaiting_input.get(m.from_user.id if m.from_user else None) == "caption")
    async def caption_input(message):
        if not authorized(message.from_user, message):
            return
        awaiting_input.pop(message.from_user.id, None)
        topic = (message.text or "").strip()[:180]
        if not topic:
            await message.answer("Тему не отримано.", reply_markup=menu_keyboard())
            return
        if generate_caption is None:
            await message.answer("Генератор підписів поки недоступний.", reply_markup=menu_keyboard())
            return
        try:
            caption = await generate_caption(topic)
            await message.answer(
                (caption or "Немає відповіді від генератора.") + "\\n\\nЦе чернетка, нічого не опубліковано.",
                reply_markup=menu_keyboard(),
            )
        except Exception:
            await message.answer("Зараз не вдалося створити підпис.", reply_markup=menu_keyboard())

    @dp.message(F.photo, lambda m: awaiting_input.get(m.from_user.id if m.from_user else None) == "photo")
    async def photo_input(message):
        if not authorized(message.from_user, message):
            return
        awaiting_input.pop(message.from_user.id, None)
        try:
            if archive_photo is not None:
                archive_id = await archive_photo(message)
                if not archive_id:
                    await message.answer(
                        "🖼 Не отримано підтвердження збереження фотографії, "
                        "тому чернетку не створено.",
                        reply_markup=menu_keyboard(),
                    )
                    return
                await send_photo_draft(message, archive_id, message.from_user.id)
        except Exception:
            await message.answer("Не вдалося зберегти фото в архів.", reply_markup=menu_keyboard())
