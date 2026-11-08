"""A short-lived signed Instagram media URL backed by the existing private Drive photo archive.

Never exposes owner photo archive without an approved Instagram draft.
"""
import asyncio
from datetime import datetime, timezone, timedelta
import hashlib
import hmac
from io import BytesIO
import logging
import os
import re
from urllib.parse import quote

from aiohttp import web
log = logging.getLogger("instagram_media")
ID_PATTERN = re.compile(r"^[a-f0-9]{24}$")
DRAFT_PATTERN = re.compile(r"^[a-f0-9]{8}$")
MAX_OUTPUT_BYTES = 8 * 1024 * 1024


def _secret():
    # Reuse the private 32+-character encryption secret; never print it.
    value = os.getenv("META_TOKEN_ENCRYPTION_KEY", "")
    if len(value) < 32:
        raise RuntimeError("Instagram media signing key unavailable")
    return value.encode("utf-8")


def _signature(draft_id, archive_id, owner_id, expires):
    payload = f"{draft_id}:{archive_id}:{owner_id}:{expires}".encode("ascii")
    return hmac.new(_secret(), payload, hashlib.sha256).hexdigest()


def signed_media_url(base_url, draft_id, archive_id, owner_id, expires):
    if not DRAFT_PATTERN.fullmatch(str(draft_id)) or not ID_PATTERN.fullmatch(str(archive_id)):
        raise ValueError("Invalid Instagram photo reference")
    if not str(owner_id).isdigit() or not str(expires).isdigit():
        raise ValueError("Invalid Instagram media URL fields")
    base = base_url.rstrip("/")
    if not base.startswith("https://") or "/" in base[8:]:
        raise ValueError("Invalid public HTTPS origin")
    sig = _signature(draft_id, archive_id, str(owner_id), str(expires))
    return f"{base}/ig/media/{draft_id}/{archive_id}?owner={owner_id}&expires={expires}&sig={sig}"


async def _to_jpeg(data):
    def convert():
        from PIL import Image
        with Image.open(BytesIO(data)) as image:
            image.load()
            if image.width * image.height > 40_000_000:
                raise ValueError("Image too large")
            image.thumbnail((3072, 3072), Image.Resampling.LANCZOS)
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            output = BytesIO()
            image.save(output, format="JPEG", quality=88, optimize=True)
            jpg = output.getvalue()
            if not jpg or len(jpg) > MAX_OUTPUT_BYTES:
                raise ValueError("JPEG too large")
            return jpg
    return await asyncio.to_thread(convert)


def install_instagram_media(app, read_memory, archive_store):
    async def fetch_media(request):
        if archive_store is None:
            raise web.HTTPServiceUnavailable()
        draft_id = request.match_info.get("draft_id", "")
        archive_id = request.match_info.get("archive_id", "")
        owner = request.query.get("owner", "")
        expires = request.query.get("expires", "")
        sig = request.query.get("sig", "")
        if not (DRAFT_PATTERN.fullmatch(draft_id) and ID_PATTERN.fullmatch(archive_id)
                and owner.isdigit() and expires.isdigit()
                and len(sig) == 64 and re.fullmatch(r"[a-f0-9]{64}", sig)):
            raise web.HTTPNotFound()
        try:
            expiry = int(expires)
            now = datetime.now(timezone.utc).timestamp()
            if now > expiry or expiry > now + 15 * 86400:
                raise web.HTTPNotFound()
            expected = _signature(draft_id, archive_id, owner, expires)
            if not hmac.compare_digest(expected, sig):
                raise web.HTTPNotFound()
            state = await read_memory()
            entry = state.get("instagram_publish_queue", {}).get(draft_id)
            if (not isinstance(entry, dict)
                    or entry.get("status") not in {"approved", "publishing"}
                    or str(entry.get("photo_archive_id")) != archive_id
                    or str(entry.get("photo_owner_id")) != owner):
                raise web.HTTPNotFound()
            from photo_archive import retrieve_image
            data, _, _ = await retrieve_image(archive_store, owner, archive_id)
            jpeg = await _to_jpeg(data)
            return web.Response(body=jpeg, content_type="image/jpeg",
                                headers={"Cache-Control": "private, max-age=60",
                                         "X-Content-Type-Options": "nosniff"})
        except web.HTTPException:
            raise
        except Exception as exc:
            log.warning("INSTAGRAM_MEDIA unavailable reason=%s", type(exc).__name__)
            raise web.HTTPServiceUnavailable() from None

    app.router.add_get("/ig/media/{draft_id}/{archive_id}", fetch_media)
