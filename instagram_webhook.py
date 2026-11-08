"""Signed Instagram webhook intake. No messages are logged or sent until a handler is installed."""
import hashlib
import hmac
import json
import logging
import os

from aiohttp import web

logger = logging.getLogger("instagram_webhook")


def install_instagram_webhook(app, on_message=None):
    async def verify(request):
        token = os.getenv("INSTAGRAM_WEBHOOK_VERIFY_TOKEN", "")
        candidate = request.query.get("hub.verify_token", "")
        mode = request.query.get("hub.mode")
        challenge = request.query.get("hub.challenge", "")
        if not token or mode != "subscribe" or not candidate or not hmac.compare_digest(token, candidate):
            raise web.HTTPForbidden(text="Webhook verification failed")
        if not challenge or len(challenge) > 256:
            raise web.HTTPBadRequest(text="Invalid challenge")
        return web.Response(text=challenge, content_type="text/plain")

    async def receive(request):
        secret = os.getenv("META_APP_SECRET", "")
        if not secret:
            raise web.HTTPServiceUnavailable(text="Webhook secret missing")
        if request.content_length is not None and request.content_length > 256 * 1024:
            raise web.HTTPRequestEntityTooLarge(max_size=256 * 1024, actual_size=request.content_length)
        raw = await request.read()
        if len(raw) > 256 * 1024:
            raise web.HTTPRequestEntityTooLarge(max_size=256 * 1024, actual_size=len(raw))
        supplied = request.headers.get("X-Hub-Signature-256", "")
        expected = "sha256=" + hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(supplied, expected):
            raise web.HTTPUnauthorized(text="Invalid signature")
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise web.HTTPBadRequest(text="Invalid JSON")
        if not isinstance(payload, dict) or payload.get("object") != "instagram":
            logger.info("INSTAGRAM_WEBHOOK ignored_unrecognized_object")
            return web.Response(text="ignored")
        logger.info("INSTAGRAM_WEBHOOK signed_event_received entries=%s", len(payload.get("entry", [])))
        if on_message is not None:
            for entry in payload.get("entry", []):
                if not isinstance(entry, dict):
                    continue
                for event in entry.get("messaging", []):
                    if not isinstance(event, dict):
                        continue
                    message = event.get("message") or {}
                    sender = event.get("sender") or {}
                    recipient = event.get("recipient") or {}
                    if not isinstance(message, dict) or not isinstance(sender, dict) or not isinstance(recipient, dict):
                        continue
                    if message.get("is_echo") or message.get("is_deleted"):
                        continue
                    text = message.get("text")
                    sender_id = sender.get("id")
                    recipient_id = recipient.get("id")
                    message_id = message.get("mid")
                    if isinstance(text, str) and text.strip() and sender_id and recipient_id and message_id:
                        logger.info("INSTAGRAM_WEBHOOK text_event_accepted")
                        await on_message(str(sender_id), str(recipient_id), text[:4000], str(message_id))
        return web.Response(text="EVENT_RECEIVED")

    app.router.add_get("/webhook/instagram", verify)
    app.router.add_post("/webhook/instagram", receive)
