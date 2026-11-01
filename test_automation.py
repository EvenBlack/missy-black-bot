import asyncio
import copy
import json
import socket
import unittest
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock,MagicMock,patch
from aiogram import Bot,Dispatcher,types
from aiogram.methods import SendMessage
from automation_engine import (AutomationStore,AutomationEngine,AutomationError,empty_state,owner_state,
    add_job,own_job,claim_due,finish_job,parse_when,next_daily,utcnow,iso)
from automation_handlers import AutomationFeatures
from outbound_policy import OutboundAuthorization,OutboundDenied,allow_group_send
from site_monitor import validate_url,snapshot_resource,resource_delta,PublicResolver,SiteError
from drive_memory import RevisionConflict,MemoryUnavailable


class Transport:
    def __init__(self):self.data=empty_state();self.revision=0;self.calls=[]
    async def request(self,action,**kw):
        self.calls.append((action,copy.deepcopy(kw)))
        if action=='automation_get':
            if kw.get('known_revision')==self.revision:return {'revision':self.revision,'unchanged':True}
            return {'revision':self.revision,'data':copy.deepcopy(self.data)}
        if action=='automation_put':
            if kw['expected_revision']!=self.revision:raise RevisionConflict('REVISION_CONFLICT')
            self.data=copy.deepcopy(kw['data']);self.revision+=1;return {'revision':self.revision}
        raise AssertionError(action)


def setup():
    transport=Transport();store=AutomationStore(lambda:transport)
    bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=100)))
    engine=AutomationEngine(store,bot,MagicMock());return transport,store,bot,engine


class CoreTests(unittest.IsolatedAsyncioTestCase):
    def test_dates_relative_absolute_and_dst(self):
        now=datetime(2026,11,1,tzinfo=timezone.utc)
        self.assertEqual(parse_when('через 15 хв',now),now+timedelta(minutes=15))
        self.assertEqual(parse_when('2026-11-01 10:30',now).hour,8)
        with self.assertRaisesRegex(AutomationError,'TIME_IN_PAST'):parse_when('2026-10-31 10:30',now)
        with self.assertRaisesRegex(AutomationError,'AMBIGUOUS_TIME'):parse_when('2026-10-25 03:30',datetime(2026,10,24,tzinfo=timezone.utc))
        with self.assertRaisesRegex(AutomationError,'NONEXISTENT_TIME'):parse_when('2026-03-29 03:30',datetime(2026,3,28,tzinfo=timezone.utc))
        self.assertGreater(next_daily('09:00',now),now)

    async def test_draft_never_sends_and_confirmed_broadcast_uses_snapshot(self):
        transport,store,bot,engine=setup();now=utcnow()
        def draft(state):
            owner_state(state,1)['targets']={'-10001':{'title':'Approved A'},'-10002':{'title':'Approved B'}}
            return add_job(state,1,'broadcast','Exact ad',now,targets=['-10001'],sent_targets=[])
        key=await store.change(draft)
        await engine.tick(now);bot.send_message.assert_not_awaited()
        await store.change(lambda state:own_job(state,1,key).update(status='ready'))
        with patch('automation_engine.asyncio.sleep',AsyncMock()):await engine.tick(now+timedelta(seconds=1))
        self.assertEqual(bot.send_message.call_args_list[0].args,(-10001,'Exact ad'))
        self.assertFalse(any(c.args[0]==-10002 for c in bot.send_message.call_args_list))
        self.assertEqual(transport.data['jobs'][key]['status'],'done')
        count=bot.send_message.await_count;await engine.tick(now+timedelta(minutes=1))
        self.assertEqual(bot.send_message.await_count,count)

    async def test_reminder_survives_new_store_and_is_not_sent_twice(self):
        transport,store,bot,engine=setup();now=utcnow()
        key=await store.change(lambda state:add_job(state,7,'reminder','Read notes',now-timedelta(minutes=5)))
        restarted=AutomationEngine(AutomationStore(lambda:transport),bot,MagicMock())
        await restarted.tick(now)
        self.assertIn('Read notes',bot.send_message.call_args.args[1])
        self.assertIn('затримкою',bot.send_message.call_args.args[1])
        await restarted.tick(now+timedelta(minutes=1));self.assertEqual(bot.send_message.await_count,1)
        self.assertEqual(transport.data['jobs'][key]['status'],'done')

    async def test_worker_lease_and_expired_delivery_pause_prevent_ad_replay(self):
        state=empty_state();now=utcnow()
        key=add_job(state,1,'broadcast','Ad',now,targets=['-10001']);state['jobs'][key]['status']='ready'
        claimed=claim_due(state,'worker-one',now)
        self.assertEqual(claimed['id'],key)
        self.assertIsNone(claim_due(state,'worker-two',now))
        self.assertIsNone(claim_due(state,'worker-two',now+timedelta(minutes=6)))
        self.assertEqual(state['jobs'][key]['status'],'uncertain')

    async def test_sent_but_unacknowledged_message_is_not_automatically_replayed(self):
        transport,store,bot,engine=setup();now=utcnow()
        key=await store.change(lambda state:add_job(state,1,'reminder','Test',now))
        real=transport.request
        failed=False
        async def fail_after_send(action,**kw):
            nonlocal failed
            if action=='automation_put' and kw['data']['jobs'][key]['status']=='done' and not failed:
                failed=True;raise MemoryUnavailable('network error after delivery')
            return await real(action,**kw)
        transport.request=fail_after_send
        await engine.tick(now)
        self.assertEqual(transport.data['jobs'][key]['status'],'uncertain')
        reminder_calls=[c for c in bot.send_message.call_args_list if c.args[1].startswith('Нагадування:')]
        self.assertEqual(len(reminder_calls),1)
        await engine.tick(now+timedelta(minutes=1))
        self.assertEqual(len([c for c in bot.send_message.call_args_list if c.args[1].startswith('Нагадування:')]),1)

    async def test_removed_target_and_owner_isolation(self):
        transport,store,bot,engine=setup();now=utcnow()
        key=await store.change(lambda state:add_job(state,1,'broadcast','Ad',now,targets=['-10001']))
        with self.assertRaises(AutomationError):own_job(transport.data,2,key)
        await store.change(lambda state:own_job(state,1,key).update(status='ready'))
        await engine.tick(now)
        self.assertFalse(any(c.args[0]<0 for c in bot.send_message.call_args_list))
        self.assertEqual(transport.data['jobs'][key]['status'],'uncertain')

    async def test_cached_poll_has_no_full_payload_and_cas_retries(self):
        transport,store,_,_=setup()
        await store.read();await store.read()
        self.assertEqual(transport.calls[-1][1]['known_revision'],0)
        real=transport.request;once=True
        async def conflict(action,**kw):
            nonlocal once
            if action=='automation_put' and once:once=False;raise RevisionConflict('REVISION_CONFLICT')
            return await real(action,**kw)
        transport.request=conflict
        key=await store.change(lambda state:add_job(state,1,'reminder','A',utcnow()))
        self.assertIn(key,transport.data['jobs'])

    async def test_explicit_group_grant_blocks_unapproved_send_forward_and_copy(self):
        middleware=OutboundAuthorization();request=AsyncMock(return_value=True)
        with self.assertRaises(OutboundDenied):await middleware(request,None,SendMessage(chat_id=-10001,text='ad'))
        request.assert_not_awaited()
        with allow_group_send(-10001):
            await middleware(request,None,SendMessage(chat_id=-10001,text='ad'))
            with self.assertRaises(OutboundDenied):await middleware(request,None,SendMessage(chat_id=-10002,text='ad'))
        with self.assertRaises(OutboundDenied):await middleware(request,None,SendMessage(chat_id=-10001,text='ad'))
        await middleware(request,None,SendMessage(chat_id=1,text='private reply'))


class SiteTests(unittest.IsolatedAsyncioTestCase):
    def test_public_https_and_internal_addresses(self):
        for url in ['http://example.org','https://localhost','https://127.0.0.1','https://10.1.1.1','https://[::1]','https://169.254.169.254','https://user:pass@example.org']:
            with self.subTest(url=url),self.assertRaises((SiteError,ValueError)):validate_url(url)
        self.assertEqual(validate_url('https://example.org/news'),'https://example.org/news')

    async def test_dns_rebinding_to_private_network_is_blocked(self):
        loop=asyncio.get_running_loop()
        addresses=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))]
        with patch.object(loop,'getaddrinfo',AsyncMock(return_value=addresses)),self.assertRaisesRegex(SiteError,'PRIVATE_ADDRESS'):
            await PublicResolver().resolve('public.example',443)

    def test_rss_changes_are_deduplicated_and_filtered(self):
        old=snapshot_resource(b'<rss><channel><title>News</title><item><title>Old</title><link>https://example.org/old</link></item></channel></rss>','application/rss+xml','https://example.org/feed')
        new=snapshot_resource('<rss><channel><title>News</title><item><title>Новини про Україну</title><link>https://example.org/new</link></item><item><title>Old</title><link>https://example.org/old</link></item></channel></rss>'.encode(),'application/rss+xml','https://example.org/feed')
        self.assertFalse(resource_delta(None,new,[])['changed'])
        self.assertFalse(resource_delta(new,new,[])['changed'])
        result=resource_delta(old,new,['україн'])
        self.assertTrue(result['matches']);self.assertIn('https://example.org/new',result['summary'])
        self.assertFalse(resource_delta(old,new,['спорт'])['matches'])

    def test_html_scripts_navigation_and_entity_expansion_are_not_ingested(self):
        result=snapshot_resource(b'<html><head><title>Title</title></head><body><nav>menu</nav><script>secret code</script><article><p>Actual news.</p></article></body></html>','text/html','https://example.org')
        self.assertEqual(result['lines'],['Actual news.'])
        with self.assertRaises(SiteError):snapshot_resource(b'<!DOCTYPE x [<!ENTITY x "bad">]><rss/>','application/xml','https://example.org')


class HandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_dispatcher_draft_and_confirmation_are_separate(self):
        dp=Dispatcher();bot=Bot('123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi')
        archive=SimpleNamespace(owners=set());features=AutomationFeatures(dp,bot,lambda:None,AsyncMock(return_value=True),lambda m:True,AsyncMock(),MagicMock(),archive)
        transport=Transport();features.store=AutomationStore(lambda:transport);features.engine.store=features.store
        await features.store.change(lambda state:owner_state(state,1)['targets'].update({'-10001':{'title':'Selected group'}}))
        calls=[]
        async def telegram(bot,method,timeout=None):
            calls.append(method)
            return types.Message(message_id=99,date=utcnow(),chat=types.Chat(id=method.chat_id,type='private'),text=getattr(method,'text',''))
        async def feed(text,n):
            message=types.Message(message_id=n,date=utcnow(),chat=types.Chat(id=1,type='private'),from_user=types.User(id=1,is_bot=False,first_name='Test'),text=text)
            await dp.feed_update(bot,types.Update(update_id=n,message=message))
        try:
            with patch.object(bot.session,'make_request',AsyncMock(side_effect=telegram)):
                await feed('/broadcast Exact announcement',1)
                key=next(iter(transport.data['jobs']))
                self.assertEqual(transport.data['jobs'][key]['status'],'draft')
                self.assertFalse(any(int(c.chat_id)<0 for c in calls))
                await feed('/broadcast_send '+key,2)
                self.assertEqual(transport.data['jobs'][key]['status'],'ready')
                await feed('/broadcast_send '+key,3)
                self.assertIn('вже підтверджена',calls[-1].text)
                await feed('/stop_all',4)
                self.assertEqual(transport.data['jobs'][key]['status'],'cancelled')
        finally:await bot.session.close()

class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_site_fetch_failure_reschedules_without_discarding_baseline(self):
        transport,store,bot,engine=setup();now=utcnow()
        snapshot={'hash':'old','lines':['old text']}
        key=await store.change(lambda state:add_job(state,1,'site','',now,url='https://example.org',interval_minutes=60,snapshot=snapshot))
        with patch('site_monitor.fetch_resource',AsyncMock(side_effect=OSError('offline'))):await engine.tick(now)
        job=transport.data['jobs'][key]
        self.assertEqual(job['status'],'ready');self.assertEqual(job['snapshot'],snapshot)
        self.assertIn('недоступний',job['last_report']['summary'])
        self.assertEqual(job['failures'],1)
        bot.send_message.assert_not_awaited()

    async def test_site_expired_read_lease_is_safe_to_resume(self):
        state=empty_state();now=utcnow()
        key=add_job(state,1,'site','',now,url='https://example.org',interval_minutes=60)
        claim_due(state,'old',now)
        result=claim_due(state,'new',now+timedelta(minutes=6))
        self.assertEqual(result['id'],key);self.assertEqual(result['worker'],'new')

    async def test_broadcast_does_not_block_other_owners_reminder(self):
        transport,store,bot,engine=setup();now=utcnow();started=asyncio.Event();release=asyncio.Event()
        def broadcast(state):
            owner_state(state,1)['targets']={'-10001':{'title':'Group'}}
            key=add_job(state,1,'broadcast','Ad',now,targets=['-10001']);state['jobs'][key]['status']='ready';return key
        await store.change(broadcast)
        sent=[]
        async def send(chat,text):
            if chat<0:started.set();await release.wait()
            sent.append(chat);return SimpleNamespace(message_id=100)
        bot.send_message.side_effect=send
        with patch('automation_engine.asyncio.sleep',AsyncMock()):
            first=asyncio.create_task(engine.tick(now));await started.wait()
            key=await store.change(lambda state:add_job(state,2,'reminder','Independent reminder',now))
            await engine.tick(now)
            self.assertIn(2,sent);self.assertEqual(transport.data['jobs'][key]['status'],'done')
            release.set();await first

    async def test_known_telegram_rate_rejection_keeps_unsent_job_scheduled(self):
        from aiogram.exceptions import TelegramRetryAfter
        transport,store,bot,engine=setup();now=utcnow()
        key=await store.change(lambda state:add_job(state,1,'reminder','Later',now))
        bot.send_message.side_effect=TelegramRetryAfter(method=SendMessage(chat_id=1,text='Later'),message='Too many requests',retry_after=125)
        await engine.tick(now)
        self.assertEqual(transport.data['jobs'][key]['status'],'ready')
        self.assertGreater(datetime.fromisoformat(transport.data['jobs'][key]['due']),now)

    async def test_group_monitor_only_reports_registered_keywords_to_owner(self):
        dp=Dispatcher();bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=90)))
        features=AutomationFeatures(dp,bot,lambda:None,AsyncMock(return_value=True),lambda m:False,AsyncMock(),MagicMock(),SimpleNamespace(owners=set()))
        transport=Transport();features.store=AutomationStore(lambda:transport);features.engine.store=features.store
        def watch(state):state['sources']['1:-100001']={'owner':'1','id':'-100001','title':'Registered source','keywords':['україн'],'enabled':True}
        await features.store.change(watch)
        def incoming(chat,text,n):return types.Message(message_id=n,date=utcnow(),chat=types.Chat(id=chat,type='supergroup',title='Group'),from_user=types.User(id=9,is_bot=False,first_name='Participant'),text=text)
        await features.handle_group(incoming(-100002,'Новини України',1));bot.send_message.assert_not_awaited()
        await features.handle_group(incoming(-100001,'Спорт',2));bot.send_message.assert_not_awaited()
        await features.handle_group(incoming(-100001,'Новини України',3))
        self.assertEqual(bot.send_message.call_args.args[0],1)
        self.assertEqual(len(transport.data['matches']['1']),1)
        await features.handle_group(incoming(-100001,'Новини України',3))
        self.assertEqual(bot.send_message.await_count,1)

    def test_oversized_body_is_rejected_instead_of_silently_changing_the_ad(self):
        with self.assertRaisesRegex(AutomationError,'BODY_SIZE'):add_job(empty_state(),1,'broadcast','x'*3801,utcnow())

class TelegramToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dp=Dispatcher();self.bot=Bot('123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi')
        self.bot.session.middleware(OutboundAuthorization())
        self.features=AutomationFeatures(self.dp,self.bot,lambda:None,AsyncMock(return_value=True),lambda m:True,AsyncMock(),MagicMock(),SimpleNamespace(owners=set()))
        self.transport=Transport();self.features.store=AutomationStore(lambda:self.transport);self.features.engine.store=self.features.store
        await self.features.store.change(lambda state:owner_state(state,1)['targets'].update({'-10001':{'title':'Group'}}))
        self.calls=[];self.counter=100
        def poll(closed=False,total=0):return types.Poll(id='test-poll',question='Question',options=[types.PollOption(persistent_id='a',text='A',voter_count=total),types.PollOption(persistent_id='b',text='B',voter_count=0)],total_voter_count=total,is_closed=closed,is_anonymous=True,type='regular',allows_multiple_answers=False,allows_revoting=True,members_only=False)
        self.poll=poll
        async def request(bot,method,timeout=None):
            self.calls.append(method);self.counter+=1;name=method.__api_method__
            if name=='copyMessage':return types.MessageId(message_id=self.counter)
            if name=='stopPoll':return poll(True,2)
            if name in ('deleteMessage','pinChatMessage','unpinChatMessage'):return True
            chat=int(getattr(method,'chat_id',1))
            return types.Message(message_id=self.counter,date=utcnow(),chat=types.Chat(id=chat,type='private' if chat>0 else 'supergroup'),text=getattr(method,'text','staged message'),poll=poll() if name=='sendPoll' else None)
        self.patch=patch.object(self.bot.session,'make_request',AsyncMock(side_effect=request));self.patch.start()
    async def asyncTearDown(self):self.patch.stop();await self.bot.session.close()
    async def feed(self,text,owner=1,reply=None):
        self.counter+=1
        message=types.Message(message_id=self.counter,date=utcnow(),chat=types.Chat(id=owner,type='private'),from_user=types.User(id=owner,is_bot=False,first_name='Test'),text=text,reply_to_message=reply)
        await self.dp.feed_update(self.bot,types.Update(update_id=self.counter,message=message))

    async def test_template_edit_cannot_change_already_prepared_draft(self):
        await self.feed('/template_save ad | Original text')
        await self.feed('/broadcast_template ad')
        key=next(iter(self.transport.data['jobs']))
        await self.feed('/template_save ad | Changed later')
        self.assertEqual(self.transport.data['jobs'][key]['body'],'Original text')
        self.assertEqual(self.transport.data['jobs'][key]['status'],'draft')
        self.assertFalse(any(int(c.chat_id)<0 for c in self.calls))

    async def test_poll_needs_confirmation_updates_results_and_closes(self):
        await self.feed('/poll_draft Question | A | B')
        key=next(iter(self.transport.data['jobs']))
        self.assertFalse(any(c.__api_method__=='sendPoll' for c in self.calls))
        preview=self.features.send_long.call_args.args[1]
        self.assertLess(preview.index('1. A'),preview.index('/send_draft'))
        await self.feed('/send_draft '+key)
        with patch('automation_engine.asyncio.sleep',AsyncMock()):await self.features.engine.tick(utcnow()+timedelta(seconds=1))
        self.assertEqual(self.transport.data['jobs'][key]['status'],'done')
        self.assertTrue(any(c.__api_method__=='sendPoll' and c.chat_id==-10001 for c in self.calls))
        await self.dp.feed_update(self.bot,types.Update(update_id=999,poll=self.poll(False,2)))
        self.assertEqual(self.transport.data['jobs'][key]['polls']['test-poll']['total'],2)
        await self.feed('/poll_close '+key)
        self.assertTrue(self.transport.data['jobs'][key]['polls']['test-poll']['closed'])

    async def test_copy_uses_bot_owned_staging_not_editable_original(self):
        source=types.Message(message_id=20,date=utcnow(),chat=types.Chat(id=1,type='private'),text='User original')
        await self.feed('/copy_draft',reply=source)
        key=next(iter(self.transport.data['jobs']));job=self.transport.data['jobs'][key]
        self.assertNotEqual(job['source_message_id'],20)
        self.assertTrue(all(c.chat_id>0 for c in self.calls))
        await self.feed('/send_draft '+key)
        with patch('automation_engine.asyncio.sleep',AsyncMock()):await self.features.engine.tick(utcnow()+timedelta(seconds=1))
        copied=[c for c in self.calls if c.__api_method__=='copyMessage' and c.chat_id==-10001]
        self.assertEqual(copied[0].message_id,job['source_message_id'])

    async def test_delete_own_sent_message_requires_confirmation_and_owner(self):
        def sent(state):
            key=add_job(state,1,'broadcast','Ad',utcnow(),targets=['-10001'])
            state['jobs'][key].update(status='done',deliveries={'-10001':555});return key
        key=await self.features.store.change(sent)
        await self.feed('/delete_sent '+key+' -10001')
        self.assertFalse(any(c.__api_method__=='deleteMessage' for c in self.calls))
        await self.feed('/delete_sent '+key+' -10001 confirm',owner=2)
        self.assertFalse(any(c.__api_method__=='deleteMessage' for c in self.calls))
        await self.feed('/delete_sent '+key+' -10001 confirm')
        deleted=[c for c in self.calls if c.__api_method__=='deleteMessage']
        self.assertEqual(len(deleted),1);self.assertEqual(deleted[0].message_id,555)

    async def test_natural_reminder_requires_persistent_ack(self):
        message=types.Message(message_id=50,date=utcnow(),chat=types.Chat(id=1,type='private'),from_user=types.User(id=1,is_bot=False,first_name='Test'),text='Нагадай мені через 15 хв перевірити пошту').as_(self.bot)
        self.assertTrue(await self.features.try_natural(message,message.text))
        job=next(iter(self.transport.data['jobs'].values()))
        self.assertEqual(job['kind'],'reminder');self.assertEqual(job['body'],'перевірити пошту')
        self.assertEqual(job['owner'],'1');self.assertEqual(job['status'],'ready')
        self.assertFalse(any(c.chat_id<0 for c in self.calls))

    async def test_stale_scheduled_publication_is_not_sent_after_long_downtime(self):
        transport,store,bot,engine=setup();now=utcnow()
        def create(state):
            owner_state(state,1)['targets']={'-10001':{'title':'Group'}}
            key=add_job(state,1,'broadcast','Ad',now-timedelta(hours=2),targets=['-10001'])
            state['jobs'][key]['status']='ready';return key
        key=await store.change(create);await engine.tick(now)
        self.assertEqual(transport.data['jobs'][key]['error'],'MISSED_PUBLICATION_TIME')
        self.assertFalse(any(c.args[0]<0 for c in bot.send_message.call_args_list))
