"""Owner-only read-only audit of Instagram token sources.

The Facebook Login callback stores an encrypted OAuth token in Drive, while
current Instagram API clients use a Render environment token. This module
checks whether a stored token exists and whether it can read the configured
Instagram account. No tokens, decrypted bytes, IDs or raw API errors returned.
No authorization mutation, no token swapping and no publish requests.
"""
import asyncio
import os
import re

import aiohttp
from cryptography.fernet import Fernet, InvalidToken


def _classify(status, data):
    if not isinstance(data, dict):
        return "unexpected_response"
    error = data.get("error")
    if status >= 400 or isinstance(error, dict):
        code = error.get("code") if isinstance(error, dict) else None
        if code == 190:
            return "token_rejected"
        if code in (200, 10, 3):
            return "permission_denied"
        return "api_rejected"
    return "readable" if isinstance(data.get("username"), str) else "fields_missing"


async def _check_token(session, token, url):
    try:
        async with session.get(
            url,
            params={"fields": "username"},
            headers={"Authorization": "Bearer " + token},
        ) as response:
            body = await response.json(content_type=None)
            return _classify(response.status, body)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError):
        return "network_error"


def _stored_oauth_token(state, encryption_key):
    oauth = state.get("instagram_business_oauth") if isinstance(state, dict) else None
    if not isinstance(oauth, dict) or not oauth.get("encrypted_token"):
        return "absent", None
    if not encryption_key:
        return "decryption_unavailable", None
    try:
        token = Fernet(encryption_key.encode()).decrypt(
            oauth["encrypted_token"].encode()
        ).decode()
    except (InvalidToken, ValueError, TypeError, UnicodeError):
        return "decryption_failed", None
    if not token:
        return "decryption_failed", None
    return "available", token


async def audit_instagram_token_sources(read_memory):
    try:
        state = await read_memory()
    except Exception:
        return {"status": "memory_unavailable"}
    render_token = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
    stored_status, stored_token = _stored_oauth_token(
        state, os.getenv("META_TOKEN_ENCRYPTION_KEY", "")
    )
    account_id = state.get("instagram_account_id") or os.getenv("INSTAGRAM_ACCOUNT_ID")
    version = os.getenv("META_GRAPH_VERSION", "v27.0")
    if not str(account_id or "").isdigit() or not re.fullmatch(r"v[0-9]+[.][0-9]+", version):
        return {"status": "configuration_unavailable", "stored": stored_status,
                "render": "configured" if render_token else "absent"}
    url = f"https://graph.facebook.com/{version}/{account_id}"
    import hmac
    same_token = hmac.compare_digest(render_token, stored_token) if render_token and stored_token else None
    render_result = "absent" if not render_token else "not_checked"
    stored_result = stored_status
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=16)) as session:
            tasks = []
            if render_token:
                tasks.append(("render", _check_token(session, render_token, url)))
            if stored_token:
                tasks.append(("stored", _check_token(session, stored_token, url)))
            if tasks:
                results = await asyncio.gather(*(job for _, job in tasks))
                for (source, _), result in zip(tasks, results):
                    if source == "render":
                        render_result = result
                    else:
                        stored_result = result
    except Exception:
        if render_token:
            render_result = "network_error"
        if stored_token:
            stored_result = "network_error"
    finally:
        # Never persist either plaintext token or any value derived from it.
        stored_token = None
    return {
        "status": "ok",
        "render": render_result,
        "stored": stored_result,
        "same_token": same_token,
    }


def render_token_source_audit(result):
    if result.get("status") == "memory_unavailable":
        return "🔐 Не вдалося прочитати пам'ять Google Drive; токени не змінювали."
    labels = {
        "readable": "✅ може прочитати профіль",
        "permission_denied": "⛔ Meta забороняє читання (код 10/200/3)",
        "token_rejected": "⛔ Meta не прийняла токен (190)",
        "api_rejected": "⚠️ API відхилив запит",
        "fields_missing": "⚠️ профіль без очікуваного поля",
        "unexpected_response": "⚠️ незрозуміла відповідь",
        "network_error": "⚠️ помилка зв'язку",
        "absent": "ℹ️ немає",
        "not_checked": "⚠️ не перевірено",
        "available": "ℹ️ збережено",
        "configured": "ℹ️ налаштовано",
        "decryption_unavailable": "⚠️ немає ключа розшифрування",
        "decryption_failed": "⚠️ не вдалося розшифрувати",
    }
    render = labels.get(result.get("render"), "⚠️ невідомо")
    stored = labels.get(result.get("stored"), "⚠️ невідомо")
    summary = (
        "🔐 Джерела Instagram-токенів (лише читання):\n"
        f"• Активний токен Render: {render}\n"
        f"• Збережений OAuth-токен Google Drive: {stored}\n"
    )
    if result.get("same_token") is True:
        summary += "• Токени збігаються.\n"
    elif result.get("same_token") is False:
        summary += "• Токени різні; автоматичного перемикання немає.\n"
    else:
        summary += "• Порівняти токени зараз неможливо.\n"
    return summary + "\nЖодних змін підключення, дозволів або публікацій."
