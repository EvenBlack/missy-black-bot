import unittest
import os
os.environ.setdefault('TELEGRAM_TOKEN','123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi')
os.environ.setdefault('GROQ_API_KEY','test-key-not-real')
from datetime import datetime,timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock,MagicMock,patch
from aiogram import Bot,Dispatcher,types
from aiogram.filters import Command
from natural_commands import NaturalCommands,parse_intent,ALLOWED
from automation_engine import AutomationStore,utcnow
from test_automation import Transport

class ParserTests(unittest.TestCase):
    def test_navigation_politeness_and_case(self):
        for phrase in ['Відкрий меню','Міссі, відкрий меню.','Катрусю, будь ласка, покажи меню!','Місі, чи можеш відкрити меню?','Відкрий головне меню, будь ласка.','слеш меню']:
            self.assertEqual(parse_intent(phrase).command,'menu',phrase)
        self.assertEqual(parse_intent('Покажи мої завдання').command,'tasks')
        self.assertEqual(parse_intent('Покажи себе.').command,'showself')
    def test_create_task_preserves_case_and_does_not_split_speech_into_parameters(self):
        intent=parse_intent('Додай завдання Зателефонувати Другу')
        self.assertEqual((intent.command,intent.argument),('task','Зателефонувати Другу'))
        self.assertTrue(parse_intent('Додай завдання Test | project').hint)
    def test_relative_numbers_and_absolute_time_kyiv(self):
        for n,word in [(2,'дві'),(5,'п’ять'),(15,'п’ятнадцять'),(25,'двадцять п’ять')]:
            intent=parse_intent(f'Нагадай мені через {word} хвилин Перевірити пошту.')
            self.assertEqual(intent.command,'remind');self.assertIn(f'через {n} хвилин | Перевірити пошту.',intent.argument)
        now=datetime(2026,11,1,23,30,tzinfo=timezone.utc)
        intent=parse_intent('Нагадай завтра о 9:30 Зателефонувати',now)
        self.assertEqual(intent.argument,'2026-11-03 09:30 | Зателефонувати')
    def test_repeat_preserves_body_and_asks_for_ambiguous_time(self):
        intent=parse_intent('Нагадай мені по буднях о 9:00 Перевірити EMAIL')
        self.assertEqual(intent.command,'remind_repeat');self.assertEqual(intent.argument,'будні 09:00 | Перевірити EMAIL')
        self.assertTrue(parse_intent('Нагадай мені потім перевірити пошту').hint)
    def test_quoted_stories_negations_and_questions_do_not_execute(self):
        for phrase in ['Я сказав: відкрий меню','Не відкривай меню','«Відкрий меню»','У дитинстві я казав додай завдання купити чашку','Що означає команда відкрий меню?']:
            self.assertIsNone(parse_intent(phrase),phrase)
    def test_public_send_delete_restore_confirm_never_dispatch(self):
        for command in ('broadcast_send','send_draft','schedule_draft','delete_sent','workspace_restore_confirm','task_delete','memory_forget'):
            self.assertNotIn(command,ALLOWED)
            self.assertTrue(parse_intent('/'+command+' a12345678901 confirm').hint)
        for text in ['Відправ розсилку a12345678901','Видали завдання a12345678901','Віднови робочий простір','Підтверджую розсилку']:
            intent=parse_intent(text);self.assertTrue(intent.hint,text);self.assertFalse(intent.command)
        self.assertEqual(parse_intent('Підготуй розсилку Привіт усім').command,'broadcast')

class DispatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dp=Dispatcher();self.called=AsyncMock();self.authorized=True
        async def handler(message):await self.called(message)
        self.dp.message.register(handler,Command('menu','task','broadcast','remind'))
        async def access(message):return self.authorized
        self.router=NaturalCommands(self.dp,access,MagicMock())
        self.message=SimpleNamespace(chat=SimpleNamespace(type='private',id=42),from_user=SimpleNamespace(id=42),answer=AsyncMock(),message_id=8)
    async def test_same_handler_keeps_original_owner_and_reply_methods(self):
        self.assertTrue(await self.router.handle(self.message,'Додай завдання Buy a cup'))
        proxy=self.called.call_args.args[0]
        self.assertEqual(proxy.text,'/task Buy a cup');self.assertIs(proxy.from_user,self.message.from_user)
        self.assertIs(proxy.answer,self.message.answer)
    async def test_group_unauthorized_and_typed_slash_not_reinterpreted(self):
        self.authorized=False;self.assertFalse(await self.router.handle(self.message,'Відкрий меню'))
        self.authorized=True;self.message.chat.type='group';self.assertFalse(await self.router.handle(self.message,'Відкрий меню'))
        self.message.chat.type='private';self.assertFalse(await self.router.handle(self.message,'/menu'))
        self.called.assert_not_awaited()
    async def test_voice_raw_slash_never_routes_to_confirmation(self):
        self.message.voice_input=True
        self.assertTrue(await self.router.handle(self.message,'/broadcast_send a12345678901'))
        self.called.assert_not_awaited();self.message.answer.assert_awaited_once()
    async def test_failure_does_not_claim_completion(self):
        self.called.side_effect=OSError('offline')
        self.assertTrue(await self.router.handle(self.message,'Відкрий меню'))
        self.assertIn('Не вдалося підтвердити',self.message.answer.call_args.args[0])

class VoiceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_voice_handler_archives_before_creating_owner_task(self):
        import app
        transport=Transport();store=AutomationStore(lambda:transport);calls=[];steps=[]
        message=types.Message(message_id=77,date=utcnow(),chat=types.Chat(id=1,type='private'),from_user=types.User(id=1,is_bot=False,first_name='Test',username='E_n_gram'),voice=types.Voice(file_id='voice',file_unique_id='unique',duration=2,file_size=3)).as_(app.bot)
        async def download(media,destination,timeout):destination.write(b'ogg')
        async def saved(*args):steps.append('voice_saved');return 'a'*24
        async def telegram(bot,method,**kwargs):
            calls.append(method)
            if 'Завдання збережено' in str(getattr(method,'text','')):steps.append('task_ack')
            return types.Message(message_id=88,date=utcnow(),chat=types.Chat(id=1,type='private'),text=getattr(method,'text',None))
        with patch.object(app,'drive_store',SimpleNamespace(archive_protocol=0)),patch.object(app.bot,'download',AsyncMock(side_effect=download)),patch.object(app,'transcribe',AsyncMock(return_value='Міссі, додай завдання Перевірити листи')),patch.object(app,'save_voice',AsyncMock(side_effect=saved)),patch.object(app,'change_memory',AsyncMock()),patch.object(app.assistant_hub,'store',store),patch.object(app.bot.session,'make_request',AsyncMock(side_effect=telegram)):
            await app.receive_voice(message)
        task=next(iter(transport.data['tasks']['1'].values()))
        self.assertEqual(task['text'],'Перевірити листи')
        self.assertNotIn('2',transport.data['tasks']);self.assertEqual(steps,['voice_saved','task_ack'])
    async def test_photo_description_is_not_executed_as_navigation(self):
        import app
        message=SimpleNamespace(from_user=SimpleNamespace(id=42,username='E_n_gram'),voice=SimpleNamespace(duration=2,file_size=3),reply_to_message=None,date=utcnow(),answer=AsyncMock(),bot=SimpleNamespace(download=AsyncMock(side_effect=lambda media,destination,timeout:destination.write(b'ogg'))))
        with patch.object(app,'drive_store',object()),patch.object(app,'transcribe',AsyncMock(return_value='Відкрий меню')),patch.object(app,'voice_photo_id',AsyncMock(return_value='b'*24)),patch.object(app,'save_voice',AsyncMock(return_value='a'*24)),patch.object(app,'change_memory',AsyncMock()),patch.object(app.natural_commands,'handle',AsyncMock()) as route:
            await app.receive_voice(message)
        route.assert_not_awaited()
    async def test_voice_storage_failure_does_not_execute_task(self):
        import app
        message=SimpleNamespace(from_user=SimpleNamespace(id=42,username='E_n_gram'),voice=SimpleNamespace(duration=2,file_size=3),reply_to_message=None,date=utcnow(),answer=AsyncMock(),bot=SimpleNamespace(download=AsyncMock(side_effect=lambda media,destination,timeout:destination.write(b'ogg'))))
        with patch.object(app,'drive_store',object()),patch.object(app,'transcribe',AsyncMock(return_value='Додай завдання Test')),patch.object(app,'save_voice',AsyncMock(side_effect=OSError('offline'))),patch.object(app.natural_commands,'handle',AsyncMock()) as route:
            await app.receive_voice(message)
        route.assert_not_awaited()
