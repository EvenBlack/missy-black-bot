"""Read-only check: which Instagram Graph host recognizes the configured token?

Never sends media, modifies permissions, logs secrets or exposes raw API responses.
"""
import asyncio
import os
import aiohttp


def summarize_probe(http_status, payload, expected_id=None):
    if not isinstance(payload, dict):
        return "unavailable"
    error = payload.get("error")
    if http_status >= 400 or isinstance(error, dict):
        code = error.get("code") if isinstance(error, dict) else None
        if code == 190:
            return "token_rejected"
        if code == 200:
            return "permission_denied"
        return "api_rejected"
    actual_id = payload.get("id") or payload.get("user_id")
    if actual_id is None:
        return "unrecognized_response"
    if expected_id and str(actual_id) != str(expected_id):
        return "valid_other_identity"
    return "recognized_account"


async def probe_token_host(host, token, version, expected_id, session):
    if host not in ("graph.facebook.com", "graph.instagram.com"):
        raise ValueError("Unexpected Graph host")
    url = f"https://{host}/{version}/me"
    try:
        async with session.get(
            url, params={"fields": "id"}, headers={"Authorization": "Bearer " + token},
        ) as response:
            result = await response.json(content_type=None)
            return summarize_probe(response.status, result, expected_id)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError):
        return "network_error"


async def check_login_path():
    token = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
    expected = os.getenv("INSTAGRAM_ACCOUNT_ID", "")
    version = os.getenv("META_GRAPH_VERSION", "v27.0")
    if not token:
        return {"status": "config_missing"}
    if not version.startswith("v") or not version[1:].isdigit():
        return {"status": "config_error"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as session:
            facebook, instagram = await asyncio.gather(
                probe_token_host("graph.facebook.com", token, version, expected, session),
                probe_token_host("graph.instagram.com", token, version, expected, session),
            )
    except Exception:
        return {"status": "network_error"}
    return {"status": "ok", "facebook": facebook, "instagram": instagram}


def render_login_path(result):
    if result.get("status") != "ok":
        return "🔍 Не вдалося перевірити тип Instagram-підключення безпечно. Нічого не змінено."
    labels = {
        "recognized_account": "✅ токен визначає потрібний обліковий запис",
        "valid_other_identity": "ℹ️ токен визначає інший ID (можливо Facebook-користувача)",
        "permission_denied": "⛔ Meta заборонила запит (код 200)",
        "token_rejected": "⛔ токен не прийнятий цим API (код 190)",
        "api_rejected": "⚠️ API відхилив запит",
        "network_error": "⚠️ немає відповіді",
        "unrecognized_response": "⚠️ відповідь не містить ID",
        "unavailable": "⚠️ відповідь недоступна",
    }
    fb = labels.get(result.get("facebook"), "⚠️ невідомо")
    ig = labels.get(result.get("instagram"), "⚠️ невідомо")
    return (
        "🔍 Перевірка маршруту Instagram (тільки читання):\n"
        f"• Facebook Graph: {fb}\n"
        f"• Instagram Graph: {ig}\n\n"
        "Це не перевірка права публікації. Токен, паролі й особисті дані не показуються. "
        "Жодних дописів не надіслано."
    )
