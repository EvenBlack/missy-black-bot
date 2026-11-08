"""Single-owner-approved first Instagram post rollout.

Strictly limited to the specific November 8 autumn selfie post. Not a recurring
autopublisher, and never automatically posts another photo.
"""
import asyncio
from datetime import datetime, timezone
import logging
import os

import aiohttp

from instagram_growth import publish_one_now

log = logging.getLogger("instagram_first_post")
CAPTION_START = "Coffee, messy curls, and crunchy autumn leaves"
EARLIEST = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
LATEST = datetime(2026, 11, 8, 15, 0, tzinfo=timezone.utc)


def eligible_drafts(state):
    queue = state.get("instagram_publish_queue", {})
    if not isinstance(queue, dict):
        return []
    valid = []
    for key, item in queue.items():
        if not isinstance(item, dict) or item.get("status") != "approved":
            continue
        if not str(item.get("caption", "")).startswith(CAPTION_START):
            continue
        if not item.get("photo_archive_id") or not str(item.get("photo_owner_id", "")).isdigit():
            continue
        try:
            when = datetime.fromisoformat(item["created_at"])
            if when.tzinfo is None or not EARLIEST <= when.astimezone(timezone.utc) <= LATEST:
                continue
        except (ValueError, TypeError, KeyError):
            continue
        valid.append((str(key), item))
    return valid


async def find_existing_post(account, caption):
    """Read Meta's recent posts before attempting publication to prevent duplicates.

    A failed or unauthorized verification MUST block publishing.
    """
    token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("Instagram token unavailable")
    version = os.getenv("META_GRAPH_VERSION", "v27.0")
    url = f"https://graph.facebook.com/{version}/{account}/media"
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        async with session.get(url, params={
            "fields": "id,caption,permalink,timestamp", "limit": "30",
        }, headers={"Authorization": "Bearer " + token}) as response:
            body = await response.json(content_type=None)
            if response.status >= 400 or not isinstance(body, dict) or not isinstance(body.get("data"), list):
                log.warning("INSTAGRAM_FIRST_POST verification_unavailable http=%s", response.status)
                raise RuntimeError("Cannot verify existing posts")
    for item in body["data"]:
        if isinstance(item, dict) and str(item.get("caption", "")).strip() == caption.strip():
            return str(item.get("id", ""))
    return None


async def one_shot_first_post(bot, read_memory, change_memory, find_existing=find_existing_post,
                              publish=publish_one_now):
    if os.getenv("INSTAGRAM_FIRST_POST_ENABLED", "false").lower() != "true":
        return "disabled"
    state = await read_memory()
    items = eligible_drafts(state)
    if len(items) != 1:
        log.info("INSTAGRAM_FIRST_POST no_unique_approved_target count=%s", len(items))
        return "not_ready" if not items else "ambiguous"
    draft_id, item = items[0]
    account = state.get("instagram_account_id") or os.getenv("INSTAGRAM_ACCOUNT_ID")
    if not str(account or "").isdigit():
        log.warning("INSTAGRAM_FIRST_POST account_unavailable")
        return "no_account"

    sub = state.get("instagram_morning_report")
    owner_chat_id = sub.get("chat_id") if isinstance(sub, dict) else None
    owner = str(item["photo_owner_id"])
    try:
        existing = await find_existing(str(account), item["caption"])
    except Exception as exc:
        log.warning("INSTAGRAM_FIRST_POST verification_blocked reason=%s", type(exc).__name__)
        return "verification_blocked"

    if existing:
        def mark(data):
            record = data.get("instagram_publish_queue", {}).get(draft_id)
            if isinstance(record, dict) and record.get("status") == "approved":
                record["status"] = "published"
                record["media_id"] = existing
                record["published_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                record["verified_existing"] = True
        await change_memory(mark)
        log.info("INSTAGRAM_FIRST_POST existing_post_detected")
        if isinstance(owner_chat_id, int):
            await bot.send_message(owner_chat_id, "✅ Осінній допис Насті вже знайдено в Instagram. Повторно не публікувала.")
        return "existing"

    try:
        media_id = await publish(read_memory, change_memory, draft_id, owner)
    except Exception as exc:
        log.error("INSTAGRAM_FIRST_POST manual_check_required reason=%s", type(exc).__name__)
        if isinstance(owner_chat_id, int):
            try:
                await bot.send_message(
                    owner_chat_id,
                    "⚠️ Instagram не підтвердив публікацію осіннього фото. "
                    "Автоматичний повтор заблоковано. Перевір сторінку Насті перед новою спробою.",
                )
            except Exception:
                pass
        return "uncertain"
    log.info("INSTAGRAM_FIRST_POST published")
    if isinstance(owner_chat_id, int):
        try:
            await bot.send_message(owner_chat_id, "🎉 Осіннє фото Насті опубліковано через Міссі! Instagram Media ID: " + str(media_id)[:70])
        except Exception:
            pass
    return "published"
