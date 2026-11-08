"""Meta Facebook Login for Business -> Instagram callback. Tokens encrypted before Drive persistence."""
import base64
import hashlib
import hmac
import os
import secrets
import time
from urllib.parse import urlencode

import aiohttp
from aiohttp import web
from cryptography.fernet import Fernet

BASE = "https://missy-black-bot.onrender.com"
CALLBACK = BASE + "/auth/instagram/callback"


def install_instagram_oauth(app, change_memory, drive_enabled):
    async def start(request):
        app_id = os.getenv("META_APP_ID", "")
        config_id = os.getenv("META_LOGIN_CONFIG_ID", "")
        secret = os.getenv("META_APP_SECRET", "")
        key = os.getenv("META_TOKEN_ENCRYPTION_KEY", "")
        if not all((app_id, config_id, secret, key)) or not drive_enabled:
            raise web.HTTPServiceUnavailable(text="Instagram integration is not configured")
        try:
            Fernet(key.encode())
        except (ValueError, TypeError):
            raise web.HTTPServiceUnavailable(text="Invalid encryption configuration")
        state = secrets.token_urlsafe(32)
        version = os.getenv("META_GRAPH_VERSION", "v24.0")
        params = {
            "client_id": app_id,
            "config_id": config_id,
            "redirect_uri": CALLBACK,
            "response_type": "code",
            "override_default_response_type": "true",
            "state": state,
        }
        response = web.HTTPFound("https://www.facebook.com/" + version + "/dialog/oauth?" + urlencode(params))
        response.set_cookie("ig_oauth_state", state, max_age=600, httponly=True, secure=True, samesite="Lax", path="/auth/instagram")
        return response

    async def callback(request):
        state = request.query.get("state", "")
        cookie = request.cookies.get("ig_oauth_state", "")
        if not state or not cookie or not hmac.compare_digest(state, cookie):
            raise web.HTTPForbidden(text="Invalid or expired OAuth state")
        if request.query.get("error"):
            return web.Response(text="Meta authorization was cancelled or denied.", status=400)
        code = request.query.get("code", "")
        if not code:
            raise web.HTTPBadRequest(text="Missing authorization code")
        app_id = os.getenv("META_APP_ID", "")
        app_secret = os.getenv("META_APP_SECRET", "")
        key = os.getenv("META_TOKEN_ENCRYPTION_KEY", "")
        if not all((app_id, app_secret, key)) or not drive_enabled:
            raise web.HTTPServiceUnavailable(text="Integration not configured")
        version = os.getenv("META_GRAPH_VERSION", "v24.0")
        url = "https://graph.facebook.com/" + version + "/oauth/access_token"
        timeout = aiohttp.ClientTimeout(total=15)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, params={
                    "client_id": app_id,
                    "client_secret": app_secret,
                    "redirect_uri": CALLBACK,
                    "code": code,
                }) as resp:
                    payload = await resp.json(content_type=None)
                    if resp.status != 200:
                        raise web.HTTPBadGateway(text="Meta token exchange failed")
            token = payload.get("access_token")
            if not isinstance(token, str) or not token:
                raise web.HTTPBadGateway(text="Meta returned no access token")
            encrypted = Fernet(key.encode()).encrypt(token.encode()).decode()
            # Only ciphertext and non-sensitive metadata are persisted.
            def save(data):
                data["instagram_business_oauth"] = {
                    "encrypted_token": encrypted,
                    "connected_at": int(time.time()),
                    "token_type": payload.get("token_type", "bearer"),
                }
            await change_memory(save)
        except web.HTTPException:
            raise
        except Exception:
            raise web.HTTPBadGateway(text="Could not complete Instagram authorization")
        response = web.Response(text="Instagram authorization completed. You may close this window.")
        response.del_cookie("ig_oauth_state", path="/auth/instagram")
        return response

    app.router.add_get("/auth/instagram/start", start)
    app.router.add_get("/auth/instagram/callback", callback)
