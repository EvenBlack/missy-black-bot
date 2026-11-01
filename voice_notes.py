"""Voice transcription and archive protocol. Never log audio or transcripts."""
import asyncio
import base64
import hashlib
import re
from drive_memory import MemoryUnavailable

MAX_AUDIO_BYTES = 8 * 1024 * 1024
MAX_AUDIO_SECONDS = 600
MAX_TRANSCRIPT = 40000
FOLDERS = {'memories': 'Voice_Memories', 'notes': 'Voice_Notes', 'photos': 'Photo_Descriptions'}


class VoiceError(ValueError):
    pass


def classify_note(text, photo_id=None):
    # A real reply association wins; otherwise keep uncertain material as a note.
    if photo_id:
        return 'photos'
    if re.search(r"запам[’'ʼ`]?ят|спогад|пам[’'ʼ`]?ятаю|у дитинстві|в дитинстві|коли я був|коли я була", text, re.I):
        return 'memories'
    return 'notes'


async def transcribe(client, data):
    if not data or len(data) > MAX_AUDIO_BYTES:
        raise VoiceError('AUDIO_SIZE')
    try:
        result = await asyncio.to_thread(
            client.audio.transcriptions.create,
            model='whisper-large-v3-turbo', file=('voice.ogg', data, 'audio/ogg'),
            language='uk', response_format='verbose_json', temperature=0,
            timeout=90,
        )
    except Exception as exc:
        if getattr(exc, 'status_code', None) == 429:
            raise VoiceError('RATE_LIMIT') from None
        raise VoiceError('TRANSCRIPTION_UNAVAILABLE') from None
    text = getattr(result, 'text', '')
    if not isinstance(text, str) or not text.strip():
        raise VoiceError('NO_SPEECH')
    segments = getattr(result, 'segments', None)
    if segments and all(isinstance(s, dict) and s.get('no_speech_prob', 0) > 0.8 for s in segments):
        raise VoiceError('NO_SPEECH')
    if len(text) > MAX_TRANSCRIPT:
        raise VoiceError('TRANSCRIPT_TOO_LONG')
    return text.strip()


async def save_voice(store, data, owner, transcript, category, created, photo_id=None):
    if not data or len(data) > MAX_AUDIO_BYTES or category not in FOLDERS or not transcript or len(transcript) > MAX_TRANSCRIPT:
        raise VoiceError('INVALID_VOICE')
    digest = hashlib.sha256(data).hexdigest()
    # The key includes transcript/category/association, allowing a corrected interpretation
    # of identical bytes without overwriting an earlier transcript.
    key = hashlib.sha256((digest + '\n' + category + '\n' + (photo_id or '') + '\n' + transcript).encode()).hexdigest()[:24]
    result = await store.request('store_voice', owner=str(owner), sha256=digest,
        note_id=key, category=category, transcript=transcript, photo_id=photo_id,
        content_b64=base64.b64encode(data).decode('ascii'), created=created)
    if result.get('note_id') != key or result.get('sha256') != digest or result.get('size') != len(data) or result.get('category') != category:
        raise MemoryUnavailable('INVALID_VOICE_ACKNOWLEDGEMENT')
    return key


def remember_note(memory, uid, note):
    all_notes = memory.setdefault('voice_notes', {})
    items = all_notes.setdefault(str(uid), [])
    items[:] = [old for old in items if old.get('note_id') != note['note_id']]
    items.append(note)
    items[:] = items[-20:]
