import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
os.environ.setdefault('TELEGRAM_TOKEN', '123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi')
os.environ.setdefault('GROQ_API_KEY', 'test-key-not-real')
import app
from drive_memory import MemoryUnavailable
from voice_notes import transcribe, save_voice, classify_note, remember_note, VoiceError, MAX_AUDIO_BYTES


class VoiceTests(unittest.IsolatedAsyncioTestCase):
    def test_routing_and_bounded_user_memory(self):
        self.assertEqual(classify_note('У дитинстві я жив у Львові'), 'memories')
        self.assertEqual(classify_note('Завтра зустріч'), 'notes')
        self.assertEqual(classify_note('На фото моя родина', 'a'*24), 'photos')
        memory = {}
        for n in range(25):
            remember_note(memory, 1, {'note_id':str(n)})
        remember_note(memory, 2, {'note_id':'private'})
        remember_note(memory, 1, {'note_id':'24', 'excerpt':'corrected'})
        self.assertEqual(len(memory['voice_notes']['1']), 20)
        self.assertEqual(memory['voice_notes']['1'][-1]['excerpt'], 'corrected')
        self.assertNotIn('private', str(memory['voice_notes']['1']))

    async def test_transcription_provider_and_no_translation(self):
        client = MagicMock()
        client.audio.transcriptions.create.return_value = SimpleNamespace(text='  Мій спогад  ', segments=[])
        self.assertEqual(await transcribe(client, b'ogg bytes'), 'Мій спогад')
        kwargs = client.audio.transcriptions.create.call_args.kwargs
        self.assertEqual(kwargs['language'], 'uk')
        self.assertEqual(kwargs['model'], 'whisper-large-v3-turbo')
        self.assertEqual(kwargs['file'][1], b'ogg bytes')

    async def test_empty_silence_size_and_quota(self):
        client = MagicMock()
        for result in [SimpleNamespace(text=' '), SimpleNamespace(text='Thank you', segments=[{'no_speech_prob':0.99}])]:
            client.audio.transcriptions.create.return_value = result
            with self.assertRaisesRegex(VoiceError, 'NO_SPEECH'):
                await transcribe(client, b'audio')
        class Quota(Exception):
            status_code = 429
        client.audio.transcriptions.create.side_effect = Quota('secret request body')
        with self.assertRaisesRegex(VoiceError, '^RATE_LIMIT$'):
            await transcribe(client, b'audio')
        with self.assertRaisesRegex(VoiceError, 'AUDIO_SIZE'):
            await transcribe(client, b'a' * (MAX_AUDIO_BYTES + 1))

    async def test_save_requires_verified_ack_and_is_deterministic(self):
        store = SimpleNamespace(request=AsyncMock())
        async def ack(action, **kw):
            self.assertEqual(action, 'store_voice')
            self.assertEqual(kw['owner'], '42')
            return {**kw, 'size':3}
        store.request.side_effect = ack
        first = await save_voice(store, b'ogg', 42, 'Мій спогад', 'memories', 'today')
        self.assertEqual(first, await save_voice(store, b'ogg', 42, 'Мій спогад', 'memories', 'today'))
        self.assertNotEqual(first, await save_voice(store, b'ogg', 42, 'Інша розшифровка', 'memories', 'today'))
        store.request.side_effect = None
        store.request.return_value = {'ok':True}
        with self.assertRaises(MemoryUnavailable):
            await save_voice(store, b'ogg', 42, 'text', 'notes', 'today')

    def message(self):
        return SimpleNamespace(from_user=SimpleNamespace(id=42, username='E_n_gram'),
            voice=SimpleNamespace(duration=10,file_size=3), reply_to_message=None,
            date=datetime.now(timezone.utc), answer=AsyncMock(),
            bot=SimpleNamespace(download=AsyncMock(side_effect=lambda media,destination,timeout: destination.write(b'ogg'))))

    async def test_handler_ack_only_after_cloud_commit(self):
        message = self.message()
        with patch.object(app,'drive_store', object()), patch.object(app,'transcribe',AsyncMock(return_value='У дитинстві я любив море')), patch.object(app,'save_voice',AsyncMock(return_value='a'*24)), patch.object(app,'change_memory',AsyncMock()) as index:
            await app.receive_voice(message)
        self.assertIn('Voice_Memories', message.answer.call_args.args[0])
        self.assertIn('/note memories:', message.answer.call_args.args[0])
        index.assert_awaited_once()

    async def test_failure_and_index_failure_do_not_fake_success(self):
        message = self.message()
        with patch.object(app,'drive_store', object()), patch.object(app,'transcribe',AsyncMock(return_value='Нотатка')), patch.object(app,'save_voice',AsyncMock(side_effect=MemoryUnavailable('offline'))), patch.object(app,'change_memory',AsyncMock()) as index:
            await app.receive_voice(message)
        self.assertIn('Не отримала підтвердження', message.answer.call_args.args[0])
        index.assert_not_awaited()
        with patch.object(app,'drive_store', object()), patch.object(app,'transcribe',AsyncMock(return_value='Нотатка')), patch.object(app,'save_voice',AsyncMock(return_value='a'*24)), patch.object(app,'change_memory',AsyncMock(side_effect=MemoryUnavailable('offline'))):
            await app.receive_voice(message)
        self.assertIn('Архів збережено, але', message.answer.call_args.args[0])

    async def test_limits_reject_before_api_and_photo_binding(self):
        message = self.message()
        message.voice.duration = 601
        with patch.object(app,'drive_store', object()), patch.object(app,'transcribe',AsyncMock()) as asr:
            await app.receive_voice(message)
        asr.assert_not_awaited()
        message.voice.duration = 10
        with patch.object(app,'drive_store', object()), patch.object(app,'transcribe',AsyncMock(return_value='Це наш будинок')), patch.object(app,'voice_photo_id',AsyncMock(return_value='b'*24)), patch.object(app,'save_voice',AsyncMock(return_value='a'*24)) as save, patch.object(app,'change_memory',AsyncMock()):
            await app.receive_voice(message)
        self.assertEqual(save.call_args.args[4], 'photos')
        self.assertEqual(save.call_args.args[-1], 'b'*24)
        self.assertIn('Опис прив’язано', message.answer.call_args.args[0])

    async def test_voice_question_uses_text_view_and_same_lock(self):
        message = self.message()
        with patch.object(app,'drive_store', object()), patch.object(app,'transcribe',AsyncMock(return_value='Як ти?')), patch.object(app,'save_voice',AsyncMock(return_value='a'*24)), patch.object(app,'change_memory',AsyncMock()), patch.object(app,'handle_text',AsyncMock()) as handle, patch.object(app,'persist_conversation',AsyncMock()):
            await app.receive_voice(message)
        self.assertTrue(handle.call_args.args[0].voice_input)
        self.assertEqual(handle.call_args.args[0].text, 'Як ти?')
        self.assertIs(handle.call_args.args[0].from_user, message.from_user)

    async def test_private_voice_context_is_scoped_and_never_web_searched(self):
        message = self.message()
        memory = {'факти':[], 'фото':[], 'image_cache':{}, 'voice_notes':{
            '42':[{'excerpt':'OWN_PRIVATE_NOTE'}], '99':[{'excerpt':'OTHER_PRIVATE_NOTE'}]}}
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='Відповідь'))])
        app.histories.pop(42, None)
        with patch.object(app,'read_memory',AsyncMock(return_value=memory)), patch.object(app,'web_search',AsyncMock()) as search, patch.object(app.groq_client.chat.completions,'create',MagicMock(return_value=response)) as complete:
            await app.handle_text(app.VoiceTextMessage(message, 'Що ти пам’ятаєш про мене?'))
        search.assert_not_awaited()
        system = complete.call_args.kwargs['messages'][0]['content']
        self.assertIn('OWN_PRIVATE_NOTE', system)
        self.assertNotIn('OTHER_PRIVATE_NOTE', system)
        app.histories.pop(42, None)
