"""Instagram Login messaging transport. Replies are opt-in and use an Instagram access token."""
import asyncio
import logging
import os

import aiohttp

logger = logging.getLogger("instagram_messaging")


async def send_instagram_text(ig_user_id, recipient_id, text):
    """Only send a response to an Instagram-scoped user who initiated contact."""
    token = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
    if not token or not str(ig_user_id).isdigit():
        raise RuntimeError("Instagram Login token or Instagram account ID missing")
    api_version = os.getenv("META_GRAPH_VERSION", "v24.0")
    url = f"https://graph.instagram.com/{api_version}/{ig_user_id}/messages"
    payload = {"recipient": {"id": str(recipient_id)}, "message": {"text": text[:1000]}}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        async with session.post(url, json=payload, headers={"Authorization": f"Bearer {token}"}) as response:
            if response.status >= 400:
                try:
                    body = await response.json(content_type=None)
                    err = body.get("error", {}) if isinstance(body, dict) else {}
                    if not isinstance(err, dict):
                        err = {}
                    # Never log access tokens, request bodies, recipient IDs, or Meta error messages.
                    logger.error("INSTAGRAM_SEND_REJECTED http=%s type=%s code=%s subcode=%s",
                                 response.status, str(err.get("type", ""))[:64],
                                 str(err.get("code", ""))[:16], str(err.get("error_subcode", ""))[:16])
                except Exception:
                    logger.error("INSTAGRAM_SEND_REJECTED http=%s response_unparseable", response.status)
                raise RuntimeError("Instagram API rejected message")
            return await response.json(content_type=None)


def create_instagram_message_handler(groq_client, model, completion_options):
    """Returns a webhook callback with isolated per-sender conversation context."""
    conversations = {}
    processed = set()
    user_locks = {}

    async def handle(sender_id, recipient_id, text, message_id):
        test_phrase = os.getenv("INSTAGRAM_REPLY_TEST_PHRASE", "").strip()
        test_mode = bool(test_phrase) and text.strip() == test_phrase
        if not test_mode and os.getenv("INSTAGRAM_AUTO_REPLY_ENABLED", "false").lower() != "true":
            return
        if not os.getenv("INSTAGRAM_ACCESS_TOKEN"):
            logger.warning("Instagram replies enabled without configured Instagram token")
            return
        lock = user_locks.setdefault(sender_id, asyncio.Lock())
        async with lock:
            if message_id in processed:
                return
            processed.add(message_id)
            if len(processed) > 5000:
                processed.clear()
                processed.add(message_id)
            if test_mode:
                try:
                    await send_instagram_text(recipient_id, sender_id, "Тест Instagram успішний! Повідомлення доставлено через Міссі Блек.")
                    logger.info("INSTAGRAM_TEST_REPLY sent")
                except Exception as exc:
                    logger.error("INSTAGRAM_TEST_REPLY failed: %s", type(exc).__name__)
                return
            history = conversations.setdefault(sender_id, [])
            messages = [
                {"role": "system", "content": (
                    "Ти Настя Царапка, україномовний віртуальний AI-персонаж. "
                    "Ти спілкуєшся від імені прозоро позначеного AI-персонажа Настя Царапка. "
                    "Не вводь людей в оману щодо того, що вони спілкуються з ШІ. "
                    "Не вигадуй факти, спогади та виконані дії. Відповідай коротко."
                )}
            ] + history[-10:] + [{"role": "user", "content": text}]
            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        groq_client.chat.completions.create,
                        model=model,
                        messages=messages,
                        temperature=0.5,
                        **completion_options(500),
                    ),
                    timeout=30,
                )
                answer = str(response.choices[0].message.content or "").strip()
                if not answer:
                    return
                # Instagram API limits text; keep a single concise reply.
                await send_instagram_text(recipient_id, sender_id, answer)
                history.extend([
                    {"role": "user", "content": text},
                    {"role": "assistant", "content": answer[:1000]},
                ])
                conversations[sender_id] = history[-10:]
            except Exception as exc:
                logger.error("Instagram reply error: %s", type(exc).__name__)

    async def schedule(sender_id, recipient_id, text, message_id):
        task = asyncio.create_task(handle(sender_id, recipient_id, text, message_id))
        task.add_done_callback(lambda t: logger.error("Instagram task failed") if t.exception() else None)

    return schedule
