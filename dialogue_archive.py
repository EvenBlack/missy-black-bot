"""Owner-scoped durable conversation archive and explicit preference learning."""
import asyncio
import hashlib
import json
import re
from contextvars import ContextVar
from datetime import datetime, timezone
from aiogram import BaseMiddleware, types
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from drive_memory import MemoryUnavailable

_SKIP = ContextVar('skip_dialogue_archive', default=False)
STOP = set('що це як ти я ми ви він вона воно вони мене мені мої моя моє мій про та і й в у на з до чи не так будь ласка памятаєш пам ятаєш згадати говорив казав писав тому тиждень книгу книжку книжки'.split())


def search_terms(text):
    return list(dict.fromkeys(w for w in re.findall(r'[a-zа-яіїєґ0-9]{3,}', text.lower()) if w not in STOP))[:12]


def recall_query(text):
    if not re.search(r'пам[’\x27ʼ`]?ятаєш|згадай|що я (?:казав|писав|говорив)|минулого тижня|тиждень тому|наша переписка|(?:що|який|яку|де|коли).{0,80}\bя\b.{0,80}(?:купив|казав|говорив|розповід|ходив|був|писав)', text, re.I):
        return None
    return ' '.join(search_terms(text))


def preference_update(text):
    # Only explicit instructions/corrections; do not infer sensitive traits or diagnoses.
    if len(text) > 3000:
        return None
    t = text.casefold()
    if re.search(r'^(?:(?:міссі|місі|катрусю|катруся)[,!:\s]+)?(?:будь ласка[, ]+)?(?:(?:надалі|на майбутнє)[, ]+)?(?:відповідай|пиши|пояснюй|звертайся|називай мене|мені подобається|мені не подобається|не роби так|це помилка|ти помили|я хочу,? щоб ти)', t):
        return text
    return None


def apply_preference(memory, owner, text, source_id):
    profile = memory.setdefault('user_profiles', {}).setdefault(str(owner), {'feedback':[]})
    items = profile.setdefault('feedback', [])
    if any(x.get('source') == str(source_id) for x in items):
        return
    items.append({'text':text[:900], 'source':str(source_id)})
    profile['feedback'] = items[-12:]
    # Resolve common conflicting style preferences deterministically (latest wins).
    if re.search(r'стисло|коротко|лаконічно', text, re.I):
        profile['verbosity'] = 'Відповідай стисло, якщо користувач не просить подробиць.'
    elif re.search(r'детально|докладно|розгорнуто', text, re.I):
        profile['verbosity'] = 'Пояснюй докладно, якщо користувач не просить стислу відповідь.'


def record_from_message(message, role):
    data = {'id':f'{role}:{message.message_id}', 'role':role,
            'created':message.date.isoformat(), 'text':message.text or message.caption or '',
            'message_id':message.message_id, 'kind':str(message.content_type)}
    if message.reply_to_message:
        data['reply_to'] = message.reply_to_message.message_id
    for key in ('voice','audio','document','video','sticker','animation','video_note'):
        media = getattr(message, key, None)
        if media:
            data['media'] = {k:getattr(media,k,None) for k in ('file_id','file_unique_id','file_name','mime_type','file_size','duration') if getattr(media,k,None) is not None}
            break
    if message.photo:
        data['media'] = {'file_id':message.photo[-1].file_id, 'file_unique_id':message.photo[-1].file_unique_id}
    # Full Telegram payload preserves contacts, locations, polls and edits as data;
    # drop nested replied content (archived separately) to avoid exponential growth.
    payload = message.model_dump(mode='json', exclude_none=True,
        exclude={'bot','reply_to_message','pinned_message','external_reply'})
    data['telegram'] = payload
    if message.edit_date:
        data['id'] += ':edit:' + str(int(message.edit_date.timestamp()))
    return data


class DialogueArchive:
    def __init__(self, get_store, access, logger):
        self.get_store, self.access, self.logger = get_store, access, logger
        self.owners = set()
        self.locks = {}
        self.processing_locks = {}

    async def append(self, owner, record):
        store = self.get_store()
        if store is None:
            raise MemoryUnavailable('DIALOGUE_STORAGE_NOT_CONFIGURED')
        # Deterministic event IDs make delivery retries idempotent.
        serialized = json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        digest = hashlib.sha256(serialized.encode()).hexdigest()
        async with self.locks.setdefault(owner, asyncio.Lock()):
            result = await store.request('append_history', owner=str(owner), record_json=serialized, digest=digest)
        if result.get('id') != record['id'] or result.get('digest') != digest:
            raise MemoryUnavailable('INVALID_HISTORY_ACK')

    async def warn(self, message):
        token = _SKIP.set(True)
        try:
            await message.answer('Не вдалося підтвердити запис у повний архів переписки. Це повідомлення може бути відсутнім у довгостроковій історії. Спробуй пізніше.')
        finally:
            _SKIP.reset(token)


class ArchiveIncoming(BaseMiddleware):
    def __init__(self, archive):
        self.archive = archive

    async def __call__(self, handler, event, data):
        if not isinstance(event, types.Message) or not self.archive.access(event):
            return await handler(event, data)
        # Never put group conversations or group IDs in a user's private archive.
        if event.chat.type != 'private':
            await event.answer('Працюю з особистими даними лише в приватному чаті.')
            return
        owner = event.from_user.id
        self.archive.owners.add(owner)
        try:
            await self.archive.append(owner, record_from_message(event,'user'))
        except Exception as exc:
            self.archive.logger('HISTORY_INPUT_ERROR', type(exc).__name__)
            await self.archive.warn(event)
            return  # Do not acknowledge processing when the original cannot be archived.
        command=(event.text or '').split(maxsplit=1)[0].split('@')[0] if event.text else ''
        if command in ('/stop_all','/cancel'):
            # Cancellation must not wait behind a long research or book request.
            return await handler(event,data)
        # Inputs are durably recorded before waiting. Process all commands, text,
        # voice and uploads in order for this owner; other owners remain independent.
        async with self.archive.processing_locks.setdefault(owner, asyncio.Lock()):
            return await handler(event, data)


class ArchiveOutgoing(BaseRequestMiddleware):
    def __init__(self, archive):
        self.archive = archive

    async def __call__(self, make_request, bot, method):
        result = await make_request(bot, method)
        if _SKIP.get():
            return result
        messages = result if isinstance(result, list) else [result]
        for message in messages:
            if not isinstance(message, types.Message) or message.chat.type != 'private' or message.chat.id not in self.archive.owners:
                continue
            try:
                await self.archive.append(message.chat.id, record_from_message(message,'assistant'))
            except Exception as exc:
                self.archive.logger('HISTORY_OUTPUT_ERROR', type(exc).__name__)
                # A delivered message must not be resent just because archiving failed.
                try:
                    await self.archive.warn(message.as_(bot))
                except Exception as warning_error:
                    self.archive.logger('HISTORY_WARNING_ERROR',type(warning_error).__name__)
        return result
