"""Owner-approved Instagram publishing queue and lightweight growth planning.

No automated likes/comments/follows or outbound unsolicited messaging.
All writes use the existing optimistic-lock Google Drive memory store.
"""
import asyncio
from datetime import datetime, timezone
import logging
import os
import secrets
from instagram_media import signed_media_url
from urllib.parse import urlparse

import aiohttp

log = logging.getLogger("instagram_growth")
TOPICS = (
    ("Everyday humor", "A funny tiny everyday mishap, relatable to English-speaking followers"),
    ("Beauty", "A natural beauty/makeup moment with a question for followers"),
    ("Fashion", "An affordable, playful outfit or styling decision"),
    ("Design", "A colorful decor, art, or design detail that sparks conversation"),
    ("Life update", "A small fictional-character diary scene with warm conversational writing"),
    ("Community", "A genuine audience question: this or that, favorites, or opinions"),
    ("Creative", "A playful behind-the-scenes look at making a fictional AI character"),
)


def weekly_plan():
    return [
        {"day": i + 1, "topic": topic, "idea": idea}
        for i, (topic, idea) in enumerate(TOPICS)
    ]


def validate_media_url(url):
    value = (url or "").strip()
    parsed = urlparse(value)
    if (parsed.scheme != "https" or not parsed.hostname
            or parsed.username or parsed.password
            or parsed.port not in (None, 443)
            or len(value) > 1600
            or parsed.hostname.lower() in {"localhost", "127.0.0.1", "::1"}
            or parsed.hostname.lower().endswith((".local", ".internal", ".localhost"))):
        raise ValueError("Photo must have a public HTTPS URL")
    # Instagram must be able to fetch the JPEG itself.
    return value


def _entries(data):
    result = data.get("instagram_publish_queue", {})
    return result if isinstance(result, dict) else {}


async def add_draft(change_memory, read_memory, image_url, caption, due_at):
    image_url = validate_media_url(image_url)
    caption = (caption or "").strip()
    if not caption or len(caption) > 2200:
        raise ValueError("Caption required (max 2200 chars)")
    due = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
    if due.tzinfo is None:
        raise ValueError("Timezone required")
    if due.timestamp() < datetime.now(timezone.utc).timestamp() - 30:
        raise ValueError("Date is in the past")
    due_iso = due.astimezone(timezone.utc).isoformat(timespec="seconds")
    import secrets
    draft_id = secrets.token_hex(4)

    def update(data):
        queue = data.setdefault("instagram_publish_queue", {})
        if not isinstance(queue, dict):
            raise ValueError("Invalid queue")
        if len(queue) >= 150:
            raise ValueError("Queue capacity reached")
        queue[draft_id] = {
            "status": "draft", "image_url": image_url,
            "caption": caption, "due_at": due_iso,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    await change_memory(update)
    return draft_id


async def add_photo_draft(change_memory, archive_id, owner_id, caption):
    """Draft a Telegram-uploaded Drive photo without making it public yet.

    Approval allows Meta to retrieve this photo, but global publishing stays disabled.
    """
    if not isinstance(caption, str) or not caption.strip() or len(caption) > 2200:
        raise ValueError("Instagram caption required")
    if not isinstance(archive_id, str) or len(archive_id) != 24:
        raise ValueError("Invalid archived photo")
    if not str(owner_id).isdigit():
        raise ValueError("Invalid owner")
    now = datetime.now(timezone.utc)
    due = now + __import__("datetime").timedelta(minutes=1)
    expiry = int((now + __import__("datetime").timedelta(days=7)).timestamp())
    base = os.getenv("INSTAGRAM_PUBLIC_BASE_URL", "https://missy-black-bot.onrender.com")
    draft_id = secrets.token_hex(4)
    url = signed_media_url(base, draft_id, archive_id, owner_id, expiry)

    def update(data):
        queue = data.setdefault("instagram_publish_queue", {})
        if not isinstance(queue, dict) or len(queue) >= 150:
            raise ValueError("Instagram queue full")
        queue[draft_id] = {
            "status": "draft", "image_url": url,
            "caption": caption.strip(),
            "due_at": due.isoformat(timespec="seconds"),
            "created_at": now.isoformat(timespec="seconds"),
            "photo_archive_id": archive_id,
            "photo_owner_id": str(owner_id),
        }
    await change_memory(update)
    return draft_id


async def discard_draft(change_memory, draft_id):
    def update(data):
        record = data.get("instagram_publish_queue", {}).get(draft_id)
        if not isinstance(record, dict) or record.get("status") != "draft":
            raise ValueError("Only unapproved drafts may be discarded")
        record["status"] = "cancelled"
        record.pop("image_url", None)
    await change_memory(update)


async def approve_draft(change_memory, read_memory, draft_id):
    def update(data):
        item = data.get("instagram_publish_queue", {}).get(draft_id)
        if not isinstance(item, dict) or item.get("status") != "draft":
            raise ValueError("Only an existing draft can be approved")
        item["status"] = "approved"
        item["approved_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    await change_memory(update)


async def mark_manual_publication(change_memory, read_memory, draft_id, owner_id):
    """Owner explicitly confirms that this archived photo was posted via Meta UI.

    This only reconciles local state; it NEVER makes a Meta publishing request.
    """
    import re
    if not re.fullmatch(r"[a-f0-9]{8}", str(draft_id)) or not str(owner_id).isdigit():
        raise ValueError("Invalid draft or owner")
    existing = _entries(await read_memory()).get(draft_id)
    if (not isinstance(existing, dict)
            or existing.get("status") not in ("draft", "approved", "publishing")
            or existing.get("photo_owner_id") != str(owner_id)):
        raise ValueError("Cannot reconcile this draft")
    changed = False
    def update(data):
        nonlocal changed
        item = data.get("instagram_publish_queue", {}).get(draft_id)
        if (isinstance(item, dict)
                and item.get("status") in ("draft", "approved", "publishing")
                and item.get("photo_owner_id") == str(owner_id)):
            item["status"] = "published_manual"
            item["manual_confirmed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            item.pop("image_url", None)  # Invalidate the signed preview URL.
            changed = True
    await change_memory(update)
    if not changed:
        raise ValueError("Draft already reconciled")
    return True


async def queue_summary(read_memory):
    data = await read_memory()
    return sorted(_entries(data).items(), key=lambda row: row[1].get("due_at", ""))


async def publish_image(image_url, caption, account_id=None):
    """Use the same Meta Facebook Graph domain already verified for Unified Login."""
    token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
    account = account_id or os.getenv("INSTAGRAM_ACCOUNT_ID")
    if not token or not account or not account.isdigit():
        raise RuntimeError("Instagram token or account ID not configured")
    version = os.getenv("META_GRAPH_VERSION", "v27.0")
    root = f"https://graph.facebook.com/{version}/{account}"
    timeout = aiohttp.ClientTimeout(total=35)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            f"{root}/media",
            data={"image_url": validate_media_url(image_url), "caption": caption},
            headers={"Authorization": f"Bearer {token}"},
        ) as response:
            container = await response.json(content_type=None)
            if response.status >= 400 or not isinstance(container, dict) or not container.get("id"):
                log.error("INSTAGRAM_PUBLISH container_error http=%s", response.status)
                raise RuntimeError("Instagram media container rejected")
        container_id = str(container["id"])
        # Meta processes media asynchronously; publishing an unready container fails.
        ready = False
        for attempt in range(6):
            async with session.get(
                f"https://graph.facebook.com/{version}/{container_id}",
                params={"fields": "status_code"},
                headers={"Authorization": f"Bearer {token}"},
            ) as status_response:
                status_body = await status_response.json(content_type=None)
                if status_response.status >= 400 or not isinstance(status_body, dict):
                    raise RuntimeError("Instagram media readiness check failed")
                status = status_body.get("status_code")
                if status == "FINISHED":
                    ready = True
                    break
                if status in {"ERROR", "EXPIRED"}:
                    raise RuntimeError("Instagram media processing failed")
            if attempt < 5:
                await asyncio.sleep(4)
        if not ready:
            raise RuntimeError("Instagram media container not ready")
        async with session.post(
            f"{root}/media_publish",
            data={"creation_id": container_id},
            headers={"Authorization": f"Bearer {token}"},
        ) as response:
            published = await response.json(content_type=None)
            if response.status >= 400 or not isinstance(published, dict) or not published.get("id"):
                log.error("INSTAGRAM_PUBLISH publish_error http=%s", response.status)
                raise RuntimeError("Instagram publish rejected")
        return str(published["id"])


async def publish_due_once(read_memory, change_memory, publish_func=publish_image):
    """Crash-safe at-most-once attempt: mark publishing BEFORE contacting Meta.

    A crash mid-publish leaves 'publishing' for owner review, never auto-retries.
    """
    if os.getenv("INSTAGRAM_POSTING_ENABLED", "false").lower() != "true":
        return 0
    now = datetime.now(timezone.utc)
    snapshot = await read_memory()
    due = [(key, item) for key, item in _entries(snapshot).items()
           if isinstance(item, dict) and item.get("status") == "approved"
           and datetime.fromisoformat(item["due_at"]) <= now]
    completed = 0
    account_id = snapshot.get("instagram_account_id") or os.getenv("INSTAGRAM_ACCOUNT_ID")
    if not account_id or not str(account_id).isdigit():
        log.warning("INSTAGRAM_PUBLISH account_id_unavailable")
        return 0
    for key, _ in sorted(due, key=lambda row: row[1]["due_at"])[:2]:
        claimed = False
        def claim(data):
            nonlocal claimed
            item = data.get("instagram_publish_queue", {}).get(key)
            if isinstance(item, dict) and item.get("status") == "approved":
                item["status"] = "publishing"
                claimed = True
        await change_memory(claim)
        if not claimed:
            continue
        latest = _entries(await read_memory()).get(key, {})
        try:
            media_id = await publish_func(latest["image_url"], latest["caption"], str(account_id))
        except Exception as exc:
            log.error("INSTAGRAM_PUBLISH queue_failure exception=%s", type(exc).__name__)
            # Leave as 'publishing', because an uncertain API outcome should not retry.
            continue
        def finish(data):
            item = data.get("instagram_publish_queue", {}).get(key)
            if isinstance(item, dict) and item.get("status") == "publishing":
                item["status"] = "published"
                item["media_id"] = media_id
                item["published_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        await change_memory(finish)
        completed += 1
        log.info("INSTAGRAM_PUBLISH success")
    return completed


async def publish_one_now(read_memory, change_memory, draft_id, owner_id, publish_func=publish_image):
    """Publish exactly one owner-approved draft after a dedicated confirm button.

    The global automatic scheduler flag is intentionally NOT checked: it remains off.
    Mark before network activity so a timeout cannot cause duplicate publishing.
    Only the owner who archived the photo can authorize this operation.
    """
    if not isinstance(draft_id, str) or not __import__("re").fullmatch(r"[a-f0-9]{8}", draft_id):
        raise ValueError("Invalid draft ID")
    if not str(owner_id).isdigit():
        raise ValueError("Invalid owner ID")
    snapshot = await read_memory()
    item = _entries(snapshot).get(draft_id)
    if (not isinstance(item, dict) or item.get("status") != "approved"
            or item.get("photo_owner_id") != str(owner_id)
            or not item.get("image_url") or not item.get("caption")):
        raise ValueError("Only your approved photo draft can be published")
    account = snapshot.get("instagram_account_id") or os.getenv("INSTAGRAM_ACCOUNT_ID")
    if not str(account or "").isdigit():
        raise RuntimeError("Instagram account ID unavailable")
    claimed = False
    def claim(data):
        nonlocal claimed
        record = data.get("instagram_publish_queue", {}).get(draft_id)
        if (isinstance(record, dict)
                and record.get("status") == "approved"
                and record.get("photo_owner_id") == str(owner_id)):
            record["status"] = "publishing"
            record["publish_started_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            claimed = True
    await change_memory(claim)
    if not claimed:
        raise ValueError("Draft status changed; publish not attempted")
    latest = _entries(await read_memory()).get(draft_id, {})
    try:
        media_id = await publish_func(latest["image_url"], latest["caption"], str(account))
    except Exception as exc:
        log.error("INSTAGRAM_OWNER_PUBLISH_FAILED reason=%s", type(exc).__name__)
        # Cannot distinguish timeout after publication from failure to publish.
        # Require manual Instagram account check; never auto retry.
        raise RuntimeError(
            "Meta не підтвердила публікацію. Автоматичне повторення заблоковано, "
            "щоб уникнути дублювання. Перевір сторінку Instagram вручну."
        ) from None
    def finish(data):
        record = data.get("instagram_publish_queue", {}).get(draft_id)
        if isinstance(record, dict) and record.get("status") == "publishing":
            record["status"] = "published"
            record["media_id"] = media_id
            record["published_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    await change_memory(finish)
    log.info("INSTAGRAM_OWNER_PUBLISH_SUCCESS")
    return media_id


async def publisher_loop(read_memory, change_memory):
    while True:
        try:
            await publish_due_once(read_memory, change_memory)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("INSTAGRAM_PUBLISH scheduler_error=%s", type(exc).__name__)
        await asyncio.sleep(60)


async def fetch_account_overview(read_memory):
    """Basic current-account overview. No scraping or unrelated accounts."""
    token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
    data = await read_memory()
    account_id = data.get("instagram_account_id") or os.getenv("INSTAGRAM_ACCOUNT_ID")
    if not token:
        raise RuntimeError("У Render не налаштований Instagram токен.")
    if not account_id or not str(account_id).isdigit():
        raise RuntimeError(
            "ID сторінки поки не зареєстровано. Потрібне одне нове вхідне повідомлення "
            "в Direct Насті: після нього підписаний webhook збереже ID."
        )
    version = os.getenv("META_GRAPH_VERSION", "v27.0")
    url = f"https://graph.facebook.com/{version}/{account_id}"
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        async with session.get(url, params={"fields": "username,followers_count,media_count"},
                               headers={"Authorization": f"Bearer {token}"}) as response:
            body = await response.json(content_type=None)
            if response.status >= 400 or not isinstance(body, dict):
                error = body.get("error", {}) if isinstance(body, dict) else {}
                if not isinstance(error, dict):
                    error = {}
                code = error.get("code", "")
                subcode = error.get("error_subcode", "")
                log.warning("INSTAGRAM_STATS rejected http=%s code=%s subcode=%s",
                            response.status, str(code)[:12], str(subcode)[:12])
                if code in (10, 200, 3):
                    raise RuntimeError(
                        "Meta відхилила читання статистики через права доступу. "
                        "Для Facebook Login можуть знадобитися instagram_basic і pages_read_engagement."
                    )
                if code == 190:
                    raise RuntimeError("Meta відхилила токен для читання статистики (код 190).")
                raise RuntimeError(f"Meta API повернув HTTP {response.status}, код {str(code)[:12] or 'невідомий'}.")
            if not isinstance(body.get("followers_count"), int):
                log.warning("INSTAGRAM_STATS followers_count_missing")
                raise RuntimeError("Meta не повернула followers_count: перевір права акаунта і тип токена.")
            return {
                "username": str(body.get("username", ""))[:120],
                "followers_count": body.get("followers_count"),
                "media_count": body.get("media_count"),
            }


async def save_growth_snapshot(change_memory, overview):
    """Save up to 90 daily follower snapshots for trend comparisons."""
    today = datetime.now(timezone.utc).date().isoformat()
    followers = overview.get("followers_count")
    posts = overview.get("media_count")
    if not isinstance(followers, int) or followers < 0:
        return
    def update(data):
        snapshots = data.setdefault("instagram_growth_snapshots", {})
        if not isinstance(snapshots, dict):
            raise ValueError("Invalid follower history")
        snapshots[today] = {"followers": followers, "posts": posts}
        for key in sorted(snapshots)[:-90]:
            snapshots.pop(key, None)
    await change_memory(update)
