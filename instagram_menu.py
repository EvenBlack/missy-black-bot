"""Owner-only Instagram menu and Kyiv morning follower reports.

No proactive DMs to followers, no posting without owner approval.
"""
import asyncio
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import logging

from aiogram import F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, BufferedInputFile

from instagram_growth import weekly_plan, queue_summary, fetch_account_overview, save_growth_snapshot, add_photo_draft, approve_draft, discard_draft, publish_one_now, mark_manual_publication, weekly_growth_report
from instagram_handoff import list_dialogs, set_dialog_mode
from instagram_permissions import check_instagram_permissions, render_permissions_report
from instagram_login_diagnostic import check_login_path, render_login_path
from instagram_account_diagnostic import check_account_read_path, render_account_read_path
from instagram_token_source_audit import audit_instagram_token_sources, render_token_source_audit
from instagram_editorial import IDEAS, editorial_idea, editorial_week, render_editorial_idea
from instagram_caption_translation import bilingual_editorial_caption, translate_editorial_caption, RED_FOX_NIGHT_CAPTION

log = logging.getLogger("instagram_menu")
KYIV = ZoneInfo("Europe/Kyiv")


def menu_keyboard():
    b = lambda label, action: InlineKeyboardButton(text=label, callback_data="igmenu:" + action)
    return InlineKeyboardMarkup(inline_keyboard=[
        [b("📊 Підписники", "stats"), b("📈 Приріст за 7 днів", "trends")],
        [b("📅 План на тиждень", "plan")],
        [b("📝 Ідея на сьогодні", "idea"), b("📚 Ідеї на 7 днів", "ideasweek")],
        [b("✍️ Підпис до фото", "caption"), b("🖼 Підготувати допис", "photo")],
        [b("📋 Черга дописів", "queue"), b("💬 Instagram Direct", "dialogs")],
        [b("🔐 Дозволи Meta", "permissions"), b("🔍 Запит до акаунта", "accountread")],
        [b("🔑 Джерела токенів", "tokensources")],
        [b("🔔 Звіт о 09:00", "report")],
        [b("🔄 Оновити меню", "home")],
    ])


def caption_options_keyboard(archive_id):
    """Owner chooses one of seven original caption topics before drafting."""
    if not __import__("re").fullmatch(r"[a-f0-9]{24}", str(archive_id)):
        raise ValueError("Invalid archive photo")
    rows = [
        [InlineKeyboardButton(
            text=f"✍️ {i+1}. {idea['topic']}",
            callback_data=f"igmenu:concept:{archive_id}:{i}",
        )]
        for i, idea in enumerate(IDEAS)
    ]
    rows.append([InlineKeyboardButton(
        text="🌙 Наш підпис — Red Fox",
        callback_data="igmenu:night:" + archive_id,
    )])
    rows.append([InlineKeyboardButton(text="⬅️ До архіву", callback_data="igmenu:archive")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def caption_for_topic(index):
    """Caption is chosen explicitly; never assume an autumn photo."""
    if type(index) is not int or not 0 <= index < len(IDEAS):
        raise ValueError("Invalid editorial topic")
    return IDEAS[index]


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


def install_instagram_menu(dp, bot, admin_username, read_memory, change_memory, generate_caption=None, archive_photo=None, list_owner_photos=None, get_owner_photo=None):
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

    async def show_caption_options(message, archive_id, owner_id):
        """Offer captions before writing anything into the persistent queue."""
        if not __import__("re").fullmatch(r"[a-f0-9]{24}", str(archive_id)):
            raise ValueError("Invalid photo ID")
        if list_owner_photos is None:
            raise ValueError("Photo archive unavailable")
        photos = await list_owner_photos(owner_id)
        if not any(
            isinstance(photo, dict) and photo.get("archive_id") == archive_id
            for photo in photos
        ):
            raise ValueError("Owner photo unavailable")
        await message.answer(
            "🖼 Фото вибрано. Тепер вибери тему англомовного підпису. "
            "Після цього Міссі покаже повний текст, і ти зможеш погодити "
            "або відхилити чернетку. Нічого не опубліковано.",
            reply_markup=caption_options_keyboard(archive_id),
        )

    async def send_photo_draft(message, archive_id, owner_id, caption_index=None, red_fox_night=False):
        """Create draft only after owner selected a topic or the exact agreed caption."""
        if list_owner_photos is None:
            raise ValueError("Photo archive unavailable")
        photos = await list_owner_photos(owner_id)
        if not any(
            isinstance(photo, dict) and photo.get("archive_id") == archive_id
            for photo in photos
        ):
            raise ValueError("Owner photo unavailable")
        if red_fox_night:
            if caption_index is not None:
                raise ValueError("Conflicting caption choices")
            caption = RED_FOX_NIGHT_CAPTION
            idea = {"topic": "Red Fox — нічні думки", "shot": "Фото Насті з чашкою Red Fox"}
        else:
            idea = caption_for_topic(caption_index)
            caption = idea["caption"]
        draft_id = await add_photo_draft(change_memory, archive_id, owner_id, caption)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Погодити чернетку", callback_data="igmenu:approvephoto:" + draft_id)],
            [InlineKeyboardButton(text="❌ Скасувати", callback_data="igmenu:cancelphoto:" + draft_id)],
        ])
        await message.answer(
            "🖼 Чернетка №" + draft_id + "\\n"
            "Тема: " + idea["topic"] + "\\n"
            "Рекомендований кадр: " + idea["shot"] + "\\n\\n"
            + bilingual_editorial_caption(caption) +
            "\\n\\nПереконайся, що текст відповідає саме вибраному фото. "
            "Нічого не опубліковано; автоматичні публікації вимкнені.",
            reply_markup=kb,
        )

    async def preview_archived_photo(message, owner_id, index):
        """One photo at a time: visual confirmation before drafting any post."""
        if list_owner_photos is None or get_owner_photo is None:
            await message.answer("Перегляд фотографій поки не підключений.")
            return
        photos = await list_owner_photos(owner_id)
        valid = [
            p for p in photos
            if isinstance(p, dict)
            and __import__("re").fullmatch(r"[a-f0-9]{24}", str(p.get("archive_id", "")))
        ]
        if not valid:
            await message.answer("У доступному архіві немає фотографій.")
            return
        if not isinstance(index, int) or not 0 <= index < min(len(valid), 30):
            await message.answer("Сторінку не знайдено. Відкрий архів заново.")
            return
        item = valid[index]
        archive_id = str(item["archive_id"])
        data, filename, _ = await get_owner_photo(owner_id, archive_id)
        if not isinstance(data, bytes) or not data or len(data) > 8 * 1024 * 1024:
            raise ValueError("Invalid archived photo bytes")
        # Telegram previews use a modest JPEG; the archived original is never changed.
        from io import BytesIO
        from PIL import Image
        def thumbnail():
            with Image.open(BytesIO(data)) as picture:
                picture.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
                if picture.mode != "RGB":
                    picture = picture.convert("RGB")
                out = BytesIO()
                picture.save(out, format="JPEG", quality=78, optimize=True)
                return out.getvalue()
        thumb = await asyncio.to_thread(thumbnail)
        # Only render the requested item; no public URLs or private photo data in logs.
        nav = []
        if index > 0:
            nav.append(InlineKeyboardButton(text="⬅️ Попереднє", callback_data=f"igmenu:gallery:{index-1}"))
        if index + 1 < min(len(valid), 30):
            nav.append(InlineKeyboardButton(text="Наступне ➡️", callback_data=f"igmenu:gallery:{index+1}"))
        keyboard_rows = [
            [InlineKeyboardButton(text="✅ Вибрати саме це фото", callback_data="igmenu:pick:" + archive_id)]
        ]
        if nav:
            keyboard_rows.append(nav)
        keyboard_rows.append([InlineKeyboardButton(text="⬅️ Меню", callback_data="igmenu:home")])
        caption = (
            f"📷 Фото {index+1} із {len(valid)}\n"
            f"{str(item.get('caption') or item.get('filename') or filename)[:110]}\n"
            "Перевір зображення перед вибором."
        )
        await message.answer_photo(
            BufferedInputFile(thumb, filename="archive_preview.jpg"),
            caption=caption,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_rows),
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
        elif action == "idea":
            idea = editorial_idea()
            await message.answer(render_editorial_idea(idea) + "\n\n🇺🇦 Український переклад:\n" + (translate_editorial_caption(idea["caption"]) or "Немає перекладу"), reply_markup=menu_keyboard())
        elif action == "ideasweek":
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text=f"{idea['date']} · {idea['topic']}",
                    callback_data="igmenu:idea:" + idea["date"],
                )]
                for idea in editorial_week()
            ] + [[InlineKeyboardButton(text="⬅️ Меню", callback_data="igmenu:home")]])
            await message.answer(
                "📚 Сім підготовлених англомовних ідей. Обери дату для тексту й кадру:",
                reply_markup=kb,
            )
        elif action.startswith("idea:"):
            from datetime import date
            try:
                day = date.fromisoformat(action.partition(":")[2])
                from datetime import timedelta
                if not datetime.now(KYIV).date() <= day <= datetime.now(KYIV).date() + timedelta(days=7):
                    raise ValueError("Date outside current editorial week")
                idea = editorial_idea(day)
                await message.answer(render_editorial_idea(idea) + "\n\n🇺🇦 Український переклад:\n" + (translate_editorial_caption(idea["caption"]) or "Немає перекладу"), reply_markup=menu_keyboard())
            except ValueError:
                await message.answer("Цю дату вже не знайдено. Відкрий план ще раз.")
        elif action == "trends":
            try:
                await message.answer(
                    weekly_growth_report(await read_memory()),
                    reply_markup=menu_keyboard(),
                )
            except Exception:
                await message.answer("Історія підписників поки недоступна.", reply_markup=menu_keyboard())
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
        elif action == "tokensources":
            try:
                report = await audit_instagram_token_sources(read_memory)
                await message.answer(render_token_source_audit(report), reply_markup=menu_keyboard())
            except Exception:
                await message.answer("Джерела токенів перевірити не вдалося.", reply_markup=menu_keyboard())
        elif action == "accountread":
            try:
                report = await check_account_read_path(read_memory)
                await message.answer(render_account_read_path(report), reply_markup=menu_keyboard())
            except Exception:
                await message.answer("Не вдалося перевірити доступ до акаунта.", reply_markup=menu_keyboard())
        elif action == "loginpath":
            try:
                await message.answer(
                    render_login_path(await check_login_path()),
                    reply_markup=menu_keyboard(),
                )
            except Exception:
                await message.answer(
                    "Не вдалося перевірити тип Instagram-підключення.",
                    reply_markup=menu_keyboard(),
                )
        elif action == "permissions":
            try:
                report = render_permissions_report(await check_instagram_permissions())
                await message.answer(report, reply_markup=menu_keyboard())
            except Exception:
                await message.answer("Не вдалося перевірити права Meta безпечно.", reply_markup=menu_keyboard())
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
            try:
                await preview_archived_photo(message, query.from_user.id, 0)
            except Exception as exc:
                log.warning("INSTAGRAM_ARCHIVE_PREVIEW_FAILED reason=%s", type(exc).__name__)
                await message.answer("Не вдалося показати фото з архіву. Спробуй ще раз.")
        elif action.startswith("gallery:"):
            value = action.partition(":")[2]
            if not value.isdigit() or len(value) > 3:
                return
            try:
                await preview_archived_photo(message, query.from_user.id, int(value))
            except Exception as exc:
                log.warning("INSTAGRAM_ARCHIVE_PREVIEW_FAILED reason=%s", type(exc).__name__)
                await message.answer("Не вдалося відкрити це фото. Перейди до іншого або повернися в меню.")
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
                await show_caption_options(message, photo_id, query.from_user.id)
            except Exception:
                await message.answer("Не вдалося підготувати чернетку з архіву.")
        elif action.startswith("night:"):
            photo_id = action.partition(":")[2]
            if not __import__("re").fullmatch(r"[a-f0-9]{24}", photo_id):
                return
            try:
                await send_photo_draft(
                    message, photo_id, query.from_user.id, red_fox_night=True
                )
            except ValueError:
                await message.answer("Не вдалося створити чернетку: фото недоступне.")
            except Exception as exc:
                log.warning("INSTAGRAM_NIGHT_DRAFT_FAILED reason=%s", type(exc).__name__)
                await message.answer("Не вдалося зберегти чернетку. Спробуй ще раз.")
        elif action.startswith("concept:"):
            parts = action.split(":")
            if (len(parts) != 3
                    or not __import__("re").fullmatch(r"[a-f0-9]{24}", parts[1])
                    or not parts[2].isdigit()
                    or len(parts[2]) > 2):
                return
            try:
                await send_photo_draft(
                    message, parts[1], query.from_user.id, int(parts[2])
                )
            except ValueError:
                await message.answer("Не вдалося створити чернетку: фото або тема недоступні.")
            except Exception as exc:
                log.warning("INSTAGRAM_CAPTION_DRAFT_FAILED reason=%s", type(exc).__name__)
                await message.answer("Не вдалося зберегти чернетку. Спробуй ще раз.")
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
        elif action.startswith("publishask:"):
            draft_id = action.partition(":")[2]
            if not __import__("re").fullmatch(r"[a-f0-9]{8}", draft_id):
                return
            try:
                entries = dict(await queue_summary(read_memory))
                item = entries.get(draft_id)
                if (not isinstance(item, dict) or item.get("status") != "approved"
                        or item.get("photo_owner_id") != str(query.from_user.id)):
                    await message.answer("Публікація доступна лише для твоєї погодженої чернетки.")
                    return
                caption = str(item.get("caption", ""))[:1400]
                caption_preview = bilingual_editorial_caption(caption)
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🚀 ТАК, ОПУБЛІКУВАТИ ЗАРАЗ", callback_data="igmenu:publishconfirm:" + draft_id)],
                    [InlineKeyboardButton(text="⬅️ Ні, повернутися", callback_data="igmenu:queue")],
                ])
                await message.answer(
                    "⚠️ ФІНАЛЬНЕ ПІДТВЕРДЖЕННЯ: опублікувати фото з цим підписом "
                    "зараз у Instagram Насті?\\n\\n" + caption_preview +
                    "\\n\\nЦя дія справді створить публічний допис.",
                    reply_markup=kb,
                )
            except Exception:
                await message.answer("Не вдалося перевірити статус чернетки.")
        elif action.startswith("publishconfirm:"):
            draft_id = action.partition(":")[2]
            if not __import__("re").fullmatch(r"[a-f0-9]{8}", draft_id):
                return
            await message.answer("🚀 Надсилаю лише цей погоджений допис у Meta. Повторно не натискай.")
            try:
                media_id = await publish_one_now(read_memory, change_memory, draft_id, query.from_user.id)
                await message.answer(
                    "✅ Instagram підтвердив публікацію! ID: " + str(media_id)[:70],
                    reply_markup=menu_keyboard(),
                )
            except Exception as exc:
                await message.answer(str(exc)[:400], reply_markup=menu_keyboard())
        elif action.startswith("manualask:"):
            draft_id = action.partition(":")[2]
            if not __import__("re").fullmatch(r"[a-f0-9]{8}", draft_id):
                return
            entries = dict(await queue_summary(read_memory))
            item = entries.get(draft_id)
            if (not isinstance(item, dict)
                    or item.get("status") not in ("draft", "approved", "publishing")
                    or item.get("photo_owner_id") != str(query.from_user.id)):
                await message.answer("Цей допис уже позначений або недоступний.")
                return
            await message.answer(
                f"📌 Підтверди: ти вже сам опублікував допис {draft_id} у Meta Business Suite? "
                "Це лише оновить стан чернетки, нічого не надішле в Instagram.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Так, уже опубліковано вручну",
                                          callback_data="igmenu:manualconfirm:" + draft_id)],
                    [InlineKeyboardButton(text="⬅️ Скасувати", callback_data="igmenu:queue")],
                ]),
            )
        elif action.startswith("manualconfirm:"):
            draft_id = action.partition(":")[2]
            try:
                await mark_manual_publication(change_memory, read_memory, draft_id, query.from_user.id)
                await message.answer(
                    "✅ Позначено як опублікований вручну. Міссі не повторюватиме "
                    "автоматичну публікацію цієї чернетки.",
                    reply_markup=menu_keyboard(),
                )
            except ValueError:
                await message.answer("Не вдалося змінити стан: чернетка вже змінена або недоступна.")
        elif action == "queue":
            try:
                items = await queue_summary(read_memory)
                lines = ["📋 Черга дописів:"]
                buttons = []
                for key, item in items[-10:]:
                    lines.append(f"{key}: {item.get('status', '?')} — {item.get('due_at', '?')}")
                    if item.get("photo_owner_id") == str(query.from_user.id):
                        if item.get("status") == "approved":
                            buttons.append([InlineKeyboardButton(
                                text="🚀 Опублікувати зараз · " + key,
                                callback_data="igmenu:publishask:" + key,
                            )])
                        if item.get("status") in ("draft", "approved", "publishing"):
                            buttons.append([InlineKeyboardButton(
                                text="📌 Уже опублікував вручну · " + key,
                                callback_data="igmenu:manualask:" + key,
                            )])
                buttons.append([InlineKeyboardButton(text="⬅️ Меню", callback_data="igmenu:home")])
                await message.answer(
                    "\n".join(lines) if items else "Черга дописів порожня.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                )
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
        if archive_photo is None:
            await message.answer(
                "🖼 Завантаження фотографій зараз недоступне. "
                "Можеш обрати раніше збережену фотографію в архіві.",
                reply_markup=menu_keyboard(),
            )
            return
        try:
            archive_id = await archive_photo(message)
        except Exception as exc:
            log.warning("INSTAGRAM_PHOTO_ARCHIVE_FAILED reason=%s", type(exc).__name__)
            await message.answer(
                "Не вдалося підтвердити збереження фото. Перевір архів перед повторним надсиланням.",
                reply_markup=menu_keyboard(),
            )
            return
        if not archive_id:
            await message.answer(
                "🖼 Не отримано підтвердження збереження фотографії, "
                "тому чернетку не створено.",
                reply_markup=menu_keyboard(),
            )
            return
        try:
            await show_caption_options(message, archive_id, message.from_user.id)
        except Exception as exc:
            log.warning("INSTAGRAM_PHOTO_CAPTION_OPTIONS_FAILED reason=%s", type(exc).__name__)
            await message.answer(
                "✅ Фото збережено в архіві, але меню вибору підпису зараз недоступне. "
                "Відкрий «🖼 Підготувати допис» → «📂 Обрати з уже збережених фото». "
                "Повторно завантажувати фотографію не потрібно.",
                reply_markup=menu_keyboard(),
            )
