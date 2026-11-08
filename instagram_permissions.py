"""Read-only diagnostics for Meta Instagram publishing permissions.

Never returns full access tokens, app secrets, raw Meta responses or user identities.
"""
import logging
import os

import aiohttp

log = logging.getLogger("instagram_permissions")
REQUIRED = (
    "instagram_basic",
    "instagram_content_publish",
    "pages_show_list",
    "pages_read_engagement",
)


async def check_instagram_permissions():
    token = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
    app_id = os.getenv("META_APP_ID", "")
    app_secret = os.getenv("META_APP_SECRET", "")
    version = os.getenv("META_GRAPH_VERSION", "v27.0")
    if not token or not app_id or not app_secret:
        return {"status": "config_missing"}
    url = f"https://graph.facebook.com/{version}/debug_token"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=18)) as session:
            async with session.get(
                url,
                params={"input_token": token},
                headers={"Authorization": f"Bearer {app_id}|{app_secret}"},
            ) as response:
                body = await response.json(content_type=None)
                status = response.status
    except Exception as exc:
        log.warning("META_PERMISSION_CHECK network_error type=%s", type(exc).__name__)
        return {"status": "network_error"}
    if status >= 400 or not isinstance(body, dict) or not isinstance(body.get("data"), dict):
        error = body.get("error", {}) if isinstance(body, dict) else {}
        code = error.get("code", "") if isinstance(error, dict) else ""
        log.warning("META_PERMISSION_CHECK unavailable http=%s code=%s", status, str(code)[:12])
        return {"status": "check_unavailable", "http": status, "code": str(code)[:12]}
    data = body["data"]
    if not data.get("is_valid"):
        return {"status": "invalid_token"}
    scopes = set(data.get("scopes", [])) | set(data.get("granular_scopes", []) if all(isinstance(x, str) for x in data.get("granular_scopes", [])) else [])
    granted = sorted(p for p in REQUIRED if p in scopes)
    missing = sorted(p for p in REQUIRED if p not in scopes)
    token_type = str(data.get("type", "unknown"))[:24]
    log.info("META_PERMISSION_CHECK completed granted=%s missing=%s token_type=%s", len(granted), len(missing), token_type)
    return {
        "status": "ok",
        "granted": granted,
        "missing": missing,
        "token_type": token_type,
    }


def render_permissions_report(result):
    status = result.get("status")
    if status == "config_missing":
        return "🔐 Meta: відсутні налаштування застосунку або токена в Render."
    if status == "invalid_token":
        return "🔐 Meta: поточний токен більше не дійсний."
    if status != "ok":
        return "🔐 Meta: поки не вдалося перевірити дозволи токена. Спробуй ще раз пізніше."
    good = result["granted"]
    missing = result["missing"]
    return (
        "🔐 Дозволи Meta для публікацій (Facebook Login):\n"
        + "✅ Є: " + (", ".join(good) if good else "нічого з потрібного набору")
        + "\n"
        + "❌ Бракує: " + (", ".join(missing) if missing else "немає у базовому наборі")
        + "\n\nЦе діагностика прав токена, не перевірка публікації. "
        "Токен і секрети не показуються."
    )
