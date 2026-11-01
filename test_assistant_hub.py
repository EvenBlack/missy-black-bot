import asyncio
import copy
import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from io import BytesIO
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock,MagicMock,patch
from aiogram import Bot,Dispatcher,types
from aiogram.methods import SendMessage,SendDocument
from automation_engine import AutomationStore,AutomationEngine,empty_state,add_job,owner_state,utcnow,parsed,iso,finish_job
from assistant_hub import AssistantHub,overview
from assistant_schedule import next_rule,parse_rule,calendar_export
from assistant_recall import retrieve_history,expanded_query
from document_reader import extract_document,extract_local,DocumentError
from test_automation import Transport

class HubTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dp=Dispatcher();self.bot=Bot('123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi')
        self.transport=Transport();self.store=AutomationStore(lambda:self.transport)
        self.memory={'user_facts':{'1':['old fact']},'user_profiles':{}}
        async def change(fn):fn(self.memory);return copy.deepcopy(self.memory)
        async def read():return copy.deepcopy(self.memory)
        self.calls=[];self.n=10;self.download_data=b''
        async def download(media,destination,timeout):destination.write(self.download_data)
        self.bot.download=AsyncMock(side_effect=download)
        async def access(message):return message.from_user.id in (1,2)
        async def send_long(message,text):await message.answer(text)
        async def reply_error(message,exc):await message.answer('ERROR '+str(exc))
        self.features=SimpleNamespace(dp=self.dp,bot=self.bot,store=self.store,access=access,reply_error=reply_error,report=AsyncMock())
        self.engine=AutomationEngine(self.store,self.bot,MagicMock());self.features.engine=self.engine
        self.hub=AssistantHub(self.features,read,change,lambda:self.transport,send_long,MagicMock())
        async def telegram(bot,method,**kwargs):
            self.calls.append(method)
            return types.Message(message_id=1000+self.n,date=utcnow(),chat=types.Chat(id=int(method.chat_id),type='private'),text=getattr(method,'text',None))
        self.patch=patch.object(self.bot.session,'make_request',AsyncMock(side_effect=telegram));self.patch.start()
    async def asyncTearDown(self):self.patch.stop();await self.bot.session.close()
    async def feed(self,text,owner=1,reply=None):
        self.n+=1
        message=types.Message(message_id=self.n,date=utcnow(),chat=types.Chat(id=owner,type='private'),from_user=types.User(id=owner,is_bot=False,first_name='Test'),text=text,reply_to_message=reply)
        await self.dp.feed_update(self.bot,types.Update(update_id=self.n,message=message))
    def last(self):return self.calls[-1].text
    async def test_menu_tasks_project_and_owner_isolation(self):
        await self.feed('/menu');self.assertIsNotNone(self.calls[-1].reply_markup)
        await self.feed('/project Test | Description')
        await self.feed('/task Important | Test | high')
        code=next(iter(self.transport.data['tasks']['1']))
        await self.feed('📋 Завдання');self.assertIn('Important',self.last())
        await self.feed('/tasks',2);self.assertNotIn('Important',self.last())
        await self.feed('/task_done '+code,2);self.assertIn('не знайдено',self.last())
        self.assertEqual(self.transport.data['tasks']['1'][code]['status'],'open')
        await self.feed('/task_edit '+code+' | Updated');self.assertEqual(self.transport.data['tasks']['1'][code]['text'],'Updated')
        await self.feed('/task_delete '+code);self.assertIn(code,self.transport.data['tasks']['1'])
        await self.feed('/task_delete '+code+' confirm');self.assertNotIn(code,self.transport.data['tasks']['1'])
    async def test_failed_store_never_acknowledges_task(self):
        with patch.object(self.store,'change',AsyncMock(side_effect=OSError('offline'))):await self.feed('/task Test')
        self.assertNotIn('збережено',self.last())
    async def test_memory_edit_delete_and_source(self):
        await self.feed('/memory_add Remember blue cup')
        code=next(iter(self.transport.data['facts']['1']))
        self.assertTrue(self.transport.data['facts']['1'][code]['source'].startswith('telegram:'))
        await self.feed('/memory_edit '+code+' | Green cup')
        self.assertEqual(self.transport.data['facts']['1'][code]['text'],'Green cup')
        await self.feed('/memory_forget '+code);self.assertIn(code,self.transport.data['facts']['1'])
        await self.feed('/memory_forget '+code+' confirm');self.assertNotIn(code,self.transport.data['facts']['1'])
        await self.feed('/memory_edit legacy:1 | Corrected');self.assertEqual(self.memory['user_facts']['1'][0]['text'],'Corrected')
        await self.feed('/memory_forget legacy:1 confirm');self.assertEqual(self.memory['user_facts']['1'],[])
    async def test_event_overlap_and_no_automatic_reminder(self):
        tomorrow=(utcnow().astimezone(__import__('automation_engine').KYIV)+timedelta(days=1)).strftime('%Y-%m-%d')
        await self.feed('/event '+tomorrow+' 12:00 | 60 | First')
        await self.feed('/event '+tomorrow+' 12:30 | 60 | Second')
        self.assertIn('Перетин',self.last());self.assertEqual(self.transport.data['jobs'],{})
        await self.feed('/calendar',2);self.assertNotIn('First',self.last())
        await self.feed('/calendar_export');self.assertIsInstance(self.calls[-1],SendDocument)
    async def test_recurrence_reschedule_and_briefing_are_opt_in(self):
        self.assertFalse(self.transport.data['jobs'])
        await self.feed('/remind_repeat будні 09:00 | Work')
        code=next(iter(self.transport.data['jobs']))
        self.assertEqual(self.transport.data['jobs'][code]['recurrence']['days'],list(range(5)))
        await self.feed('/snooze '+code+' | через 15 хв')
        self.assertLess(parsed(self.transport.data['jobs'][code]['due'])-utcnow(),timedelta(minutes=16))
        await self.feed('/briefing morning 08:00')
        first=[j for j in self.transport.data['jobs'].values() if j['kind']=='briefing'][0]
        await self.feed('/briefing morning off');self.assertEqual(self.transport.data['jobs'][first['id']]['status'],'cancelled')
    async def test_ics_import_owner_scope_and_no_reminder(self):
        data=calendar_export([{'id':'stable','title':'Meeting','start':iso(utcnow()+timedelta(days=1)),'end':iso(utcnow()+timedelta(days=1,hours=1))}])
        message=types.Message(message_id=44,date=utcnow(),chat=types.Chat(id=1,type='private'),from_user=types.User(id=1,is_bot=False,first_name='Test')).as_(self.bot)
        await self.hub.import_calendar(message,data);await self.hub.import_calendar(message,data)
        self.assertEqual(len(self.transport.data['calendar']['1']),1);self.assertFalse(self.transport.data['jobs'])
        self.assertNotIn('2',self.transport.data['calendar'])
    async def test_ics_unsupported_recurrence_not_silently_flattened(self):
        data=b'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:a\r\nDTSTART:20261103T090000Z\r\nDTEND:20261103T100000Z\r\nRRULE:FREQ=DAILY\r\nSUMMARY:Recurring\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n'
        m=types.Message(message_id=44,date=utcnow(),chat=types.Chat(id=1,type='private'),from_user=types.User(id=1,is_bot=False,first_name='Test')).as_(self.bot)
        await self.hub.import_calendar(m,data)
        self.assertFalse(self.transport.data['calendar']['1']);self.assertIn('Пропущено',self.last())
    async def test_workspace_export_contains_only_owner(self):
        await self.feed('/task OwnerOne');await self.feed('/task OwnerTwo',2)
        await self.feed('/workspace_export');method=self.calls[-1]
        data=json.loads(method.document.data)
        self.assertIn('OwnerOne',str(data));self.assertNotIn('OwnerTwo',str(data))
        self.assertIn(hashlib.sha256(method.document.data).hexdigest(),method.caption)
    async def test_health_does_not_leak_other_users_jobs(self):
        self.transport.data['jobs']['other']={'owner':'2','status':'uncertain'}
        checks=await self.hub.health_checks(1)
        self.assertTrue(any('невизначеним' in text and text.endswith('0') for ok,text in checks))
    async def test_public_connection_rejects_private_url_without_fetch(self):
        with patch('assistant_hub.fetch_resource',AsyncMock()) as fetch:
            await self.feed('/connect_site test | https://127.0.0.1')
        fetch.assert_not_awaited();self.assertNotIn('connections',self.transport.data)
    async def test_document_import_uses_existing_drive_text_protocol(self):
        m=types.Message(message_id=44,date=utcnow(),chat=types.Chat(id=1,type='private'),from_user=types.User(id=1,is_bot=False,first_name='Test'),document=types.Document(file_id='file',file_unique_id='unique',file_name='test.pdf')).as_(self.bot)
        with patch('assistant_hub.extract_document',AsyncMock(return_value={'chapters':[('Page 1','Extracted content')],'warnings':[]})),patch('assistant_hub.import_book',AsyncMock(return_value=('a'*24,None,1))):
            await self.hub.import_document(m,b'original','test.pdf')
        doc=self.transport.data['documents']['1'][hashlib.sha256(b'original').hexdigest()[:24]]
        self.assertEqual(doc['file_id'],'file');self.assertEqual(doc['text_id'],'a'*24)
        self.assertIn('окрему копію оригіналу',self.last())
    async def test_document_context_can_not_read_other_owner(self):
        self.transport.data['documents']={'2':{'secret':{'text_id':'a'*24}}}
        owner_state(self.transport.data,1)['active_document']='secret'
        with self.assertRaisesRegex(Exception,'ITEM_NOT_FOUND'):await self.hub.document_context(1,'query')
    async def test_briefing_job_is_durable_private_and_non_duplicate(self):
        await self.feed('/task Test task')
        now=utcnow();code=await self.store.change(lambda s:add_job(s,1,'briefing','',now,period='morning',recurrence={'days':list(range(7)),'time':'08:00'}))
        await self.engine.tick(now)
        self.assertIn('Test task',self.last());self.assertEqual(self.calls[-1].chat_id,1)
        count=len(self.calls);await self.engine.tick(now);self.assertEqual(count,len(self.calls))
        self.assertEqual(self.transport.data['jobs'][code]['status'],'ready')

    async def test_restore_requires_explicit_confirmation_and_never_replays_jobs(self):
        await self.feed('/task Original')
        await self.feed('/workspace_export')
        exported=self.calls[-1].document.data
        await self.feed('/task Added later')
        self.download_data=exported
        reply=types.Message(message_id=88,date=utcnow(),chat=types.Chat(id=1,type='private'),document=types.Document(file_id='export',file_unique_id='export',file_name='missy_workspace.json',file_size=len(exported)))
        await self.feed('/workspace_restore',reply=reply)
        self.assertEqual(len(self.transport.data['tasks']['1']),2)
        code=self.transport.data['owners']['1']['restore_preview']['code']
        await self.feed('/workspace_restore_confirm '+code,2)
        self.assertEqual(len(self.transport.data['tasks']['1']),2)
        await self.feed('/workspace_restore_confirm '+code)
        self.assertEqual(len(self.transport.data['tasks']['1']),1)
        self.assertEqual(self.transport.data['jobs'],{})
        self.assertEqual(len(self.transport.data['owners']['1']['previous_workspace']['data']['tasks']),2)
    async def test_preference_remove_recomputes_style_and_preserves_other_owner(self):
        from dialogue_archive import apply_preference
        apply_preference(self.memory,1,'Відповідай коротко',1)
        apply_preference(self.memory,1,'Пиши докладно',2)
        apply_preference(self.memory,2,'Пиши докладно',2)
        await self.feed('/preference_remove 2')
        self.assertIn('стисло',self.memory['user_profiles']['1']['verbosity'])
        self.assertIn('докладно',self.memory['user_profiles']['2']['verbosity'])

class PureTests(unittest.IsolatedAsyncioTestCase):
    def test_weekdays_skip_weekend_and_spring_dst(self):
        # Friday after target hour -> next Monday.
        now=datetime(2026,11,6,18,tzinfo=timezone.utc)
        due=next_rule({'days':list(range(5)),'time':'09:00'},now)
        self.assertEqual(due,datetime(2026,11,9,7,tzinfo=timezone.utc))
        due=next_rule({'days':[6],'time':'03:30'},datetime(2026,3,28,tzinfo=timezone.utc))
        self.assertEqual(due.astimezone(__import__('automation_engine').KYIV).date().isoformat(),'2026-04-05')
    def test_unicode_ical_folding_roundtrip(self):
        from icalendar import Calendar
        title='Зустріч, важлива; '+('Їжачок '*30)
        raw=calendar_export([{'id':'one','title':title,'start':iso(utcnow()),'end':iso(utcnow()+timedelta(hours=1))}])
        self.assertTrue(all(len(line)<=75 for line in raw.split(b'\r\n')))
        self.assertEqual(str(Calendar.from_ical(raw).walk('VEVENT')[0]['summary']),title)
    async def test_multi_page_recall_owner_and_dedup(self):
        transport=SimpleNamespace(request=AsyncMock(side_effect=[{'messages':[{'id':'user:1','score':2,'created':'x','text':'cup'}],'next_cursor':30},{'messages':[{'id':'user:1','score':1,'created':'x','text':'cup'},{'id':'user:2','score':3,'created':'y','text':'gift'}],'next_cursor':None}]))
        result=await retrieve_history(transport,8,'подарунок')
        self.assertEqual(len(result['messages']),2);self.assertIn('чашк',expanded_query('подарунок'))
        self.assertTrue(all(c.kwargs['owner']=='8' for c in transport.request.call_args_list))
    async def test_docx_real_subprocess_extracts_paragraph_and_table(self):
        buf=BytesIO()
        with zipfile.ZipFile(buf,'w') as z:z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Український текст</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Table cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
        result=await extract_document(buf.getvalue(),'test.docx')
        self.assertIn('Український текст',str(result));self.assertIn('Table cell',str(result))
    async def test_document_size_rejected_before_parser(self):
        with self.assertRaisesRegex(DocumentError,'DOC_SIZE'):await extract_document(b'x'*(8*1024*1024+1),'test.pdf')

class RecoveryValidationTests(unittest.TestCase):
    def test_other_owner_restore_and_delivery_ledger_rejected_or_excluded(self):
        from workspace_recovery import validate_restore
        source={'version':1,'owner':'1','jobs':{'advert':{'kind':'broadcast','status':'ready'}}}
        with self.assertRaisesRegex(Exception,'RESTORE_OWNER'):validate_restore(source,2)
        data=validate_restore(source,1)
        self.assertNotIn('jobs',data)
    def test_restore_checks_timestamps_and_invalid_priority(self):
        from workspace_recovery import validate_restore
        source={'version':1,'owner':'1','tasks':{'a'*12:{'text':'Task','priority':'invalid','status':'open'}}}
        with self.assertRaises(Exception):validate_restore(source,1)

class RealDocumentTests(unittest.IsolatedAsyncioTestCase):
    async def test_pdf_actual_poppler_extracts_text(self):
        # Minimal standards-compliant PDF with a text stream and cross-reference.
        body=b'BT /F1 18 Tf 50 100 Td (Document extraction works reliably.) Tj ET'
        objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',b'<< /Length '+str(len(body)).encode()+b' >>\nstream\n'+body+b'\nendstream']
        data=b'%PDF-1.4\n';offsets=[0]
        for i,obj in enumerate(objects,1):offsets.append(len(data));data+=str(i).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
        xref=len(data);data+=b'xref\n0 6\n0000000000 65535 f \n'+b''.join(f'{o:010d} 00000 n \n'.encode() for o in offsets[1:])+b'trailer\n<< /Root 1 0 R /Size 6 >>\nstartxref\n'+str(xref).encode()+b'\n%%EOF\n'
        result=await extract_document(data,'test.pdf')
        self.assertIn('Document extraction works reliably.',str(result))
    async def test_image_actual_tesseract_recognizes_print(self):
        from PIL import Image,ImageDraw,ImageFont
        image=Image.new('RGB',(1100,180),'white');draw=ImageDraw.Draw(image)
        font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',44)
        draw.text((30,40),'Document reading test 12345',font=font,fill='black')
        buf=BytesIO();image.save(buf,format='PNG')
        result=await extract_document(buf.getvalue(),'scan.png')
        self.assertIn('12345',str(result))
