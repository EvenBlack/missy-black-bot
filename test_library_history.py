import asyncio
import hashlib
import json
import os
import unittest
import zipfile
from datetime import datetime,timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock,MagicMock,patch
os.environ.setdefault('TELEGRAM_TOKEN','123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi')
os.environ.setdefault('GROQ_API_KEY','test-key-not-real')
from aiogram import Bot,Dispatcher,types
from book_library import parse_book,import_book,BookContextCache,BookError,trusted_book_url
from dialogue_archive import DialogueArchive,ArchiveIncoming,ArchiveOutgoing,record_from_message,apply_preference,preference_update,recall_query
from library_handlers import register_library
from drive_memory import MemoryUnavailable


def epub():
    target=BytesIO()
    with zipfile.ZipFile(target,'w') as z:
        z.writestr('mimetype','application/epub+zip')
        z.writestr('META-INF/container.xml','<container><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>')
        z.writestr('OPS/book.opf','<package xmlns:dc="urn:dc"><metadata><dc:title>Синя чашка</dc:title><dc:creator>Автор</dc:creator></metadata><manifest><item id="two" href="two.xhtml" media-type="application/xhtml+xml"/><item id="one" href="one.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="one"/><itemref idref="two"/></spine></package>')
        z.writestr('OPS/two.xhtml','<html><body><h1>Другий розділ</h1><p>Фінал історії.</p></body></html>')
        z.writestr('OPS/one.xhtml','<!DOCTYPE html><html><head><style>hidden CSS</style></head><body><h1>Перший розділ</h1><p>Синя чашка &amp; подарунок.</p><script>hidden script</script></body></html>')
    return target.getvalue()


def message(owner=1,text='Мій спогад',role='user',**fields):
    return types.Message(message_id=20,date=datetime.now(timezone.utc),chat=types.Chat(id=owner,type='private'),
        from_user=types.User(id=owner if role=='user' else 999,is_bot=role!='user',first_name='User'),text=text,**fields)


class LibraryTests(unittest.IsolatedAsyncioTestCase):
    def test_epub_order_metadata_visible_text_and_original_unchanged(self):
        data=epub();digest=hashlib.sha256(data).hexdigest();book=parse_book(data,'novel.EPUB')
        self.assertEqual((book.title,book.author),('Синя чашка','Автор'))
        self.assertEqual(book.chapters[0][0],'Перший розділ')
        self.assertIn('Синя чашка & подарунок.',book.chapters[0][1])
        self.assertNotIn('hidden',str(book.chapters))
        self.assertEqual(hashlib.sha256(data).hexdigest(),digest)

    def test_invalid_encrypted_and_oversized_book_rejected(self):
        with self.assertRaises(BookError):parse_book(b'invalid','a.epub')
        with self.assertRaisesRegex(BookError,'TXT_UTF8_REQUIRED'):parse_book(b'\xff','a.txt')
        with self.assertRaisesRegex(BookError,'BOOK_TEXT_TOO_LARGE'):parse_book(b'a'*3000001,'a.txt')
        out=BytesIO()
        with zipfile.ZipFile(out,'w') as z:z.writestr('META-INF/encryption.xml','encrypted')
        with self.assertRaisesRegex(BookError,'ENCRYPTED_EPUB'):parse_book(out.getvalue(),'a.epub')
        self.assertFalse(trusted_book_url('https://www.gutenberg.org.evil.test/a.epub'))
        self.assertFalse(trusted_book_url('http://127.0.0.1/a.epub'))
        self.assertFalse(trusted_book_url('https://user@www.gutenberg.org/a.epub'))

    async def test_import_upload_and_index_acknowledged_separately(self):
        async def request(action,**kw):
            if action=='store_book':return {'sha256':kw['sha256'],'size':len(epub())}
            index=json.loads(kw['index_json'])
            return {'book_id':kw['book_id'],'index_digest':kw['index_digest'],'chunks':len(index['chunks'])}
        store=SimpleNamespace(request=AsyncMock(side_effect=request))
        book_id,book,count=await import_book(store,7,epub(),'book.epub','2026-11-01')
        self.assertEqual(count,2);self.assertEqual(len(book_id),24)
        self.assertEqual([x.args[0] for x in store.request.call_args_list],['store_book','index_book'])
        self.assertEqual(store.request.call_args.kwargs['owner'],'7')
        store.request.side_effect=None;store.request.return_value={'sha256':hashlib.sha256(epub()).hexdigest(),'size':len(epub())}
        with self.assertRaisesRegex(MemoryUnavailable,'INVALID_BOOK_INDEX_ACK'):
            await import_book(store,7,epub(),'book.epub','2026-11-01')

    async def test_context_cache_never_crosses_owner_and_expires(self):
        store=SimpleNamespace(request=AsyncMock(side_effect=lambda action,**kw:{'owner':kw['owner']}))
        cache=BookContextCache()
        with patch('time.monotonic',return_value=0):
            self.assertEqual((await cache.get(store,1,'a'*24,'query'))['owner'],'1')
            await cache.get(store,1,'a'*24,'query')
            self.assertEqual((await cache.get(store,2,'a'*24,'query'))['owner'],'2')
        self.assertEqual(store.request.await_count,2)
        with patch('time.monotonic',return_value=301):await cache.get(store,1,'a'*24,'query')
        self.assertEqual(store.request.await_count,3)

    async def test_real_dispatcher_routes_epub_before_generic_document(self):
        dp=Dispatcher();bot=Bot('123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi')
        async def request(action,**kw):
            if action=='store_book':return {'sha256':kw['sha256'],'size':len(epub())}
            return {'book_id':kw['book_id'],'index_digest':kw['index_digest'],'chunks':2}
        store=SimpleNamespace(request=AsyncMock(side_effect=request));generic=AsyncMock()
        register_library(dp,lambda:store,AsyncMock(return_value=True),AsyncMock(),AsyncMock(),MagicMock())
        from aiogram import F
        async def generic_handler(event): await generic(event)
        dp.message.register(generic_handler,F.document)
        msg=message(text=None,document=types.Document(file_id='fake',file_unique_id='unique',file_name='novel.epub',file_size=len(epub())))
        sent=[]
        async def telegram(bot,method,timeout=None):
            sent.append(method);return message(role='assistant',text=getattr(method,'text','ack'))
        async def download(file,destination,timeout):destination.write(epub())
        try:
            with patch.object(bot,'download',AsyncMock(side_effect=download)),patch.object(bot.session,'make_request',AsyncMock(side_effect=telegram)):
                await dp.feed_update(bot,types.Update(update_id=1,message=msg))
            generic.assert_not_awaited()
            self.assertEqual(store.request.await_count,2)
            self.assertTrue(any('проіндексований' in str(getattr(m,'text','')) for m in sent))
        finally:await bot.session.close()


class HistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_message_saved_without_2000_character_truncation(self):
        original=message(text='я'*4096)
        record=record_from_message(original,'user')
        self.assertEqual(len(record['text']),4096)
        self.assertEqual(record['telegram']['text'],original.text)
        async def request(action,**kw):
            parsed=json.loads(kw['record_json'])
            self.assertEqual(parsed['text'],original.text)
            self.assertEqual(hashlib.sha256(kw['record_json'].encode()).hexdigest(),kw['digest'])
            return {'id':parsed['id'],'digest':kw['digest']}
        archive=DialogueArchive(lambda:SimpleNamespace(request=AsyncMock(side_effect=request)),lambda m:True,MagicMock())
        await archive.append(1,record)

    async def test_archive_failure_does_not_process_input_or_resend_output(self):
        archive=DialogueArchive(lambda:None,lambda m:True,MagicMock());archive.warn=AsyncMock()
        handler=AsyncMock()
        await ArchiveIncoming(archive)(handler,message(),{})
        handler.assert_not_awaited();archive.warn.assert_awaited_once()
        archive.warn.reset_mock();archive.owners.add(1)
        bot=Bot('123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi')
        try:
            make_request=AsyncMock(return_value=message(role='assistant'))
            result=await ArchiveOutgoing(archive)(make_request,bot,object())
            self.assertIsInstance(result,types.Message)
            make_request.assert_awaited_once();archive.warn.assert_awaited_once()
        finally:await bot.session.close()

    async def test_group_and_unauthorized_messages_not_archived(self):
        archive=DialogueArchive(lambda:None,lambda m:False,MagicMock());archive.append=AsyncMock()
        handler=AsyncMock();await ArchiveIncoming(archive)(handler,message(99),{})
        archive.append.assert_not_awaited();handler.assert_awaited_once()

    def test_preferences_scoped_latest_style_wins_and_duplicate_feedback_ignored(self):
        memory={}
        apply_preference(memory,1,'Відповідай коротко',20)
        apply_preference(memory,2,'Пиши докладно',20)
        apply_preference(memory,1,'Пояснюй докладно',21)
        apply_preference(memory,1,'Пояснюй докладно',21)
        self.assertEqual(len(memory['user_profiles']['1']['feedback']),2)
        self.assertIn('докладно',memory['user_profiles']['1']['verbosity'])
        self.assertNotIn('Відповідай коротко',str(memory['user_profiles']['2']))
        self.assertIsNone(preference_update('Сьогодні гарна погода'))
        self.assertIsNone(recall_query('Знайди погоду'))
        self.assertIn('чашку',recall_query('Пам’ятаєш, я казав про синю чашку тиждень тому?'))

    async def test_real_bot_middleware_archives_both_sides_with_private_owner(self):
        import app
        records=[]
        async def store_request(action,**kw):
            self.assertEqual(action,'append_history')
            record=json.loads(kw['record_json']);records.append((kw['owner'],record))
            return {'id':record['id'],'digest':kw['digest']}
        store=SimpleNamespace(archive_protocol=3,request=AsyncMock(side_effect=store_request))
        incoming=message(text='/start').model_copy(update={'from_user':types.User(id=1,is_bot=False,first_name='User',username='E_n_gram')})
        async def telegram(bot,method,timeout=None):
            return message(role='assistant',text=method.text).model_copy(update={'message_id':21})
        app.dialogue_archive.owners.clear()
        with patch.object(app,'drive_store',store),patch.object(app.bot.session,'make_request',AsyncMock(side_effect=telegram)):
            await app.dp.feed_update(app.bot,types.Update(update_id=101,message=incoming))
        self.assertEqual([r[1]['role'] for r in records],['user','assistant'])
        self.assertTrue(all(owner=='1' for owner,_ in records))
        self.assertEqual(records[0][1]['text'],'/start')
        self.assertIn('Я на зв',records[1][1]['text'])
        app.dialogue_archive.owners.clear()

    async def test_real_prompt_never_includes_other_users_profile_facts_or_library(self):
        import app
        memory={'факти':['LEGACY_UNATTRIBUTED'],'фото':[],'image_cache':{},
            'user_profiles':{'1':{'verbosity':'OWN_STYLE'},'2':{'verbosity':'OTHER_STYLE'}},
            'user_facts':{'1':['OWN_FACT'],'2':['OTHER_FACT']},
            'user_books':{'2':{'active':'b'*24}},'voice_notes':{'2':[{'excerpt':'OTHER_VOICE'}]}}
        incoming=message(text='Привіт').model_copy(update={'from_user':types.User(id=1,is_bot=False,first_name='User',username='E_n_gram')})
        response=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='Привіт'))])
        app.histories.pop(1,None)
        async def telegram(bot,method,timeout=None):return message(role='assistant',text=method.text)
        with patch.object(app,'read_memory',AsyncMock(return_value=memory)),patch.object(app.groq_client.chat.completions,'create',MagicMock(return_value=response)) as llm,patch.object(app.bot.session,'make_request',AsyncMock(side_effect=telegram)):
            await app.handle_text(incoming.as_(app.bot))
        system=llm.call_args.kwargs['messages'][0]['content']
        self.assertIn('OWN_STYLE',system);self.assertIn('OWN_FACT',system)
        for secret in ['OTHER_STYLE','OTHER_FACT','OTHER_VOICE','LEGACY_UNATTRIBUTED','b'*24]:self.assertNotIn(secret,system)
        app.histories.pop(1,None)

    async def test_only_busy_requests_are_retried(self):
        from drive_memory import DriveMemory
        store=DriveMemory('https://script.google.com/macros/s/example/exec','x'*40)
        store._request_once=AsyncMock(side_effect=[MemoryUnavailable('MEMORY_BUSY'),{'ok':True}])
        with patch('drive_memory.asyncio.sleep',AsyncMock()):
            self.assertEqual(await store.request('append_history'),{'ok':True})
        self.assertEqual(store._request_once.await_count,2)
        store._request_once=AsyncMock(side_effect=MemoryUnavailable('MEMORY_NETWORK_OR_RESPONSE_ERROR'))
        with self.assertRaises(MemoryUnavailable):await store.request('put')
        store._request_once.assert_awaited_once()

    async def test_commands_wait_for_previous_message_but_other_owner_does_not(self):
        archive=DialogueArchive(lambda:None,lambda m:True,MagicMock())
        archive.append=AsyncMock()
        middleware=ArchiveIncoming(archive)
        started=asyncio.Event();release=asyncio.Event();state={};seen=[]
        async def handler(event,data):
            if event.text=='set preference':
                started.set();await release.wait();state['preference']='short'
            elif event.text=='read preference':
                seen.append(state.get('preference'))
            else:seen.append('other user')
        first=asyncio.create_task(middleware(handler,message(1,'set preference'),{}))
        await started.wait()
        second=asyncio.create_task(middleware(handler,message(1,'read preference'),{}))
        await middleware(handler,message(2,'independent'),{})
        await asyncio.sleep(0)
        self.assertEqual(seen,['other user'])
        self.assertFalse(second.done())
        self.assertGreaterEqual(archive.append.await_count,3)
        release.set();await asyncio.gather(first,second)
        self.assertEqual(seen,['other user','short'])
