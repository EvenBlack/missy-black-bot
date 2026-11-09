"""One-time read-only health check for an explicitly selected failed photo draft.

Never calls Meta, changes draft status, or logs signed media URLs.
"""
import asyncio
import logging
import os
import re

from instagram_growth import check_archived_photo_delivery

log = logging.getLogger("instagram_photo_probe")


async def check_failed_photo_once(read_memory, check_func=check_archived_photo_delivery):
    draft_id = os.getenv("INSTAGRAM_PHOTO_CHECK_DRAFT_ID", "")
    if not re.fullmatch(r"[a-f0-9]{8}", draft_id):
        return {"status": "disabled"}
    try:
        state = await read_memory()
        item = state.get("instagram_publish_queue", {}).get(draft_id)
        if (not isinstance(item, dict)
                or item.get("status") != "publishing"
                or not item.get("photo_archive_id")
                or not item.get("photo_owner_id")
                or not item.get("image_url")):
            log.warning("INSTAGRAM_FAILED_PHOTO_CHECK not_eligible")
            return {"status": "not_eligible"}
        result = await check_func(item["image_url"])
        if (not result.get("ok") and result.get("http") in (502, 503, 504)):
            # During Render's handover an otherwise valid URL can briefly be 502.
            # Re-check only the JPEG after the new server is fully live.
            await asyncio.sleep(20)
            result = await check_func(item["image_url"])
        log.warning(
            "INSTAGRAM_FAILED_PHOTO_CHECK ok=%s http=%s reason=%s",
            bool(result.get("ok")),
            str(result.get("http")),
            str(result.get("reason", "ok"))[:24],
        )
        return result
    except Exception as exc:
        log.warning("INSTAGRAM_FAILED_PHOTO_CHECK failed reason=%s", type(exc).__name__)
        return {"status": "failed", "reason": type(exc).__name__}
