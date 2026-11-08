"""Instagram messaging transport. Replies are opt-in and use a Page access token."""
import asyncio
import logging
import os

import aiohttp

logger = logging.getLogger("instagram_messaging")


async def send_instagram_text(recipient_id, text):
    """Only send a response to an Instagram-scoped user who initiated contact."""
    page_id = os.getenv("INSTAGRAM_PAGE_ID", "")
    page_token = os.getenv("INSTAGRAM_PAGE_ACCESS_TOKEN", "")
    if not page_id or not page_token:
        raise RuntimeError("Instagram Page token and ID must be configured")
    api_version = os.getenv("META_GRAPH_VERSION", "v24.0")
    url = f"https://graph.facebook.com/{api_version}/{page_id}/messages"
    payload = {"recipient": {"id": str(recipient_id)}, "message": {"text": text[:1000]}}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        async with session.post(url, json=payload, headers={"Authorization": f"Bearer {page_token}"}) as response:
            if response.status >= 400:
                logger.error("Instagram send failed: HTTP %s", response.status)
                raise RuntimeError("Instagram API rejected message")
            return await response.json(content_type=None)


def create_instagram_message_handler(groq_client, model, completion_options):
    """Returns a webhook callback with isolated per-sender conversation context."""
    conversations = {}
    processed = set()
    user_locks = {}

    async def handle(sender_id, recipient_id, text, message_id):
        if os.getenv("INSTAGRAM_AUTO_REPLY_ENABLED", "false").lower() != "true":
            return
        if not os.getenv("INSTAGRAM_PAGE_ID") or not os.getenv("INSTAGRAM_PAGE_ACCESS_TOKEN"):
            logger.warning("Instagram replies enabled without configured Page credentials")
            return
        lock = user_locks.setdefault(sender_id, asyncio.Lock())
        async with lock:
            if message_id in processed:
                return
            processed.add(message_id)
            if len(processed) > 5000:
                processed.clear()
                processed.add(message_id)
            history = conversations.setdefault(sender_id, [])
            messages = [
                {"role": "system", "content": (
                    "Ти Міссі Блек/Катруся, україномовна доброзичлива асистентка. "
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
                await send_instagram_text(sender_id, answer)
                history.extend([
                    {"role": "user", "content": text},
                    {"role": "assistant", "content": answer[:1000]},
                ])
                conversations[sender_id] = history[-10:]
            except Exception as exc:
                logger.error("Instagram reply error: %s", type(exc).__name__)

    def schedule(sender_id, recipient_id, text, message_id):
        task = asyncio.create_task(handle(sender_id, recipient_id, text, message_id))
        task.add_done_callback(lambda t: logger.error("Instagram task failed") if t.exception() else None)

    return schedule
