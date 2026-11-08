"""Read-only account-ID diagnostic comparing known-working Instagram profile read.

Checks only the Instagram account ID already verified from webhook or configured
on the service. Never returns IDs, tokens, raw errors or personal information.
"""
import asyncio
import os

import aiohttp


def classify_reply(status, payload, requested_fields):
    """Return actionable status without disclosing profile data or error text."""
    if not isinstance(payload, dict):
        return "unparseable"
    error = payload.get("error")
    if status >= 400 or isinstance(error, dict):
        code = error.get("code") if isinstance(error, dict) else None
        subcode = error.get("error_subcode") if isinstance(error, dict) else None
        if code == 190:
            return "token_rejected"
        if code in (10, 200, 3):
            return "permission_denied"
        if code == 100:
            return "invalid_request"
        return "api_rejected"
    if any(key not in payload for key in requested_fields):
        return "fields_missing"
    return "ok"


async def _probe(session, url, token, fields, params=None):
    try:
        async with session.get(
            url,
            params=params or {"fields": ",".join(fields)},
            headers={"Authorization": "Bearer " + token},
        ) as response:
            body = await response.json(content_type=None)
            if isinstance(body, dict) and response.status < 400 and not body.get("error"):
                # Do not expose account data. For media edge, require only data array.
                if "data" in fields:
                    return "ok" if isinstance(body.get("data"), list) else "fields_missing"
            return classify_reply(response.status, body, fields)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError):
        return "network_error"


async def check_account_read_path(read_memory):
    token = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
    version = os.getenv("META_GRAPH_VERSION", "v27.0")
    if not token:
        return {"status": "config_missing"}
    import re
    if not re.fullmatch(r"v[0-9]+[.][0-9]+", version):
        return {"status": "config_invalid"}
    try:
        state = await read_memory()
    except Exception:
        return {"status": "memory_unavailable"}
    account = state.get("instagram_account_id") or os.getenv("INSTAGRAM_ACCOUNT_ID")
    if not str(account or "").isdigit():
        return {"status": "account_missing"}
    url = f"https://graph.facebook.com/{version}/{account}"
    ig_url = f"https://graph.instagram.com/{version}/{account}"
    timeout = aiohttp.ClientTimeout(total=16)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # These checks never request a new token and never publish content.
            full, basic, media, ig_basic = await asyncio.gather(
                _probe(session, url, token, ("username", "followers_count", "media_count")),
                _probe(session, url, token, ("username", "media_count")),
                _probe(session, url + "/media", token, ("data",),
                       {"fields": "id", "limit": 1}),
                _probe(session, ig_url, token, ("username",)),
            )
    except Exception:
        return {"status": "network_error"}
    return {
        "status": "ok",
        "profile_full": full,
        "profile_basic": basic,
        "media_list": media,
        "instagram_host": ig_basic,
    }


def render_account_read_path(result):
    if result.get("status") != "ok":
        return "🔍 Перевірка Instagram-акаунта недоступна. Нічого не змінено."
    names = {
        "ok": "✅ доступно",
        "token_rejected": "⛔ токен не прийнято (190)",
        "permission_denied": "⛔ доступ заборонено (10/200/3)",
        "invalid_request": "⚠️ неприпустимий запит (100)",
        "fields_missing": "⚠️ відповідь без потрібних полів",
        "api_rejected": "⚠️ запит відхилено",
        "unparseable": "⚠️ незрозуміла відповідь",
        "network_error": "⚠️ мережна помилка",
    }
    label = lambda key: names.get(result.get(key), "⚠️ невідомий стан")
    return (
        "🔍 Діагностика акаунта Насті (тільки читання):\n"
        f"• Facebook Graph: профіль і статистика — {label('profile_full')}\n"
        f"• Facebook Graph: базові поля — {label('profile_basic')}\n"
        f"• Facebook Graph: список дописів — {label('media_list')}\n"
        f"• Instagram Graph: профіль — {label('instagram_host')}\n\n"
        "Це НЕ перевірка публікації. Нічого не публікували, "
        "не змінювали та не перепідключали. Токен і дані акаунта приховано."
    )
