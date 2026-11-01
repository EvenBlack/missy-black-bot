"""Durable personal jobs. No outbound work is authorized by a draft alone."""
import asyncio
import copy
import hashlib
import json
import re
import uuid
import time
from aiogram.exceptions import TelegramRetryAfter
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from outbound_policy import allow_group_send
from drive_memory import MemoryUnavailable,RevisionConflict

KYIV=ZoneInfo('Europe/Kyiv')
DRAFT_KINDS={'broadcast','poll','copy','forward'}


def utcnow():return datetime.now(timezone.utc)
def iso(value):return value.astimezone(timezone.utc).isoformat()
def parsed(value):return datetime.fromisoformat(value).astimezone(timezone.utc)


class AutomationError(ValueError):pass


def parse_when(value,now=None):
    now=now or utcnow();value=value.strip().lower()
    relative=re.fullmatch(r'(?:через\s+)?(\d{1,5})\s*(хв(?:илин(?:у|и)?)?|min|год(?:ин(?:у|и)?)?|h|дн(?:і|ів)?|день|d)',value)
    if relative:
        n=int(relative.group(1));unit=relative.group(2)
        seconds=n*(60 if unit.startswith(('хв','min')) else 3600 if unit.startswith(('год','h')) else 86400)
        if not 60<=seconds<=366*86400:raise AutomationError('TIME_RANGE')
        return now+timedelta(seconds=seconds)
    try:local=datetime.strptime(value,'%Y-%m-%d %H:%M')
    except ValueError:raise AutomationError('TIME_FORMAT') from None
    candidate=local.replace(tzinfo=KYIV)
    # Reject skipped DST times instead of silently shifting the user's request.
    if candidate.astimezone(timezone.utc).astimezone(KYIV).replace(tzinfo=None)!=local:raise AutomationError('NONEXISTENT_TIME')
    if local.replace(tzinfo=KYIV,fold=0).utcoffset()!=local.replace(tzinfo=KYIV,fold=1).utcoffset():raise AutomationError('AMBIGUOUS_TIME')
    if candidate<=now:raise AutomationError('TIME_IN_PAST')
    return candidate.astimezone(timezone.utc)


def next_daily(hhmm,now=None):
    now=now or utcnow()
    if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',hhmm):raise AutomationError('TIME_FORMAT')
    hour,minute=map(int,hhmm.split(':'));local=now.astimezone(KYIV)
    candidate=local.replace(hour=hour,minute=minute,second=0,microsecond=0)
    if candidate<=local:candidate+=timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def empty_state():return {'owners':{},'jobs':{},'sources':{},'matches':{}}


def validate_state(state):
    if not isinstance(state,dict) or any(not isinstance(state.get(k),dict) for k in ('owners','jobs','sources','matches')):raise MemoryUnavailable('INVALID_AUTOMATION_STATE')
    return state


class AutomationStore:
    def __init__(self,get_transport):
        self.get_transport=get_transport;self.cached=None;self.revision=None;self.lock=asyncio.Lock()
    async def read(self):
        transport=self.get_transport()
        if transport is None:raise MemoryUnavailable('AUTOMATION_NOT_CONFIGURED')
        result=await transport.request('automation_get',known_revision=self.revision)
        revision=result.get('revision')
        if type(revision) is not int or revision<0:raise MemoryUnavailable('INVALID_AUTOMATION_REVISION')
        if result.get('unchanged') is True:
            if self.cached is None or revision!=self.revision:raise MemoryUnavailable('INVALID_AUTOMATION_CACHE')
        else:self.cached=validate_state(result.get('data'));self.revision=revision
        return copy.deepcopy(self.cached),revision
    async def change(self,mutate):
        async with self.lock:
            for _ in range(4):
                state,revision=await self.read();before=copy.deepcopy(state)
                answer=mutate(state)
                if state==before:return answer
                if len(json.dumps(state,ensure_ascii=False).encode('utf-16-le'))//2>800000:raise AutomationError('STATE_FULL')
                try:
                    result=await self.get_transport().request('automation_put',expected_revision=revision,data=state)
                except RevisionConflict:continue
                if result.get('revision')!=revision+1:raise MemoryUnavailable('INVALID_AUTOMATION_ACK')
                self.cached=copy.deepcopy(state);self.revision=revision+1
                return answer
        raise MemoryUnavailable('AUTOMATION_CONCURRENT_WRITES')


def owner_state(state,owner):
    return state['owners'].setdefault(str(owner),{'targets':{},'digest_time':None})


def add_job(state,owner,kind,body,due=None,**extra):
    owner=str(owner)
    if kind in DRAFT_KINDS|{'reminder'} and (not body.strip() or len(body.encode('utf-16-le'))//2>3800):raise AutomationError('BODY_SIZE')
    if sum(j.get('owner')==owner and j.get('status') in ('draft','ready','running') for j in state['jobs'].values())>=20:raise AutomationError('TOO_MANY_JOBS')
    key=uuid.uuid4().hex[:12]
    while key in state['jobs']:key=uuid.uuid4().hex[:12]
    state['jobs'][key]={'id':key,'owner':owner,'kind':kind,'body':body,'due':iso(due or utcnow()),'status':'draft' if kind in DRAFT_KINDS else 'ready','created':iso(utcnow()),**extra}
    owner_state(state,owner)
    return key


def own_job(state,owner,key):
    job=state['jobs'].get(key)
    if not job or job.get('owner')!=str(owner):raise AutomationError('JOB_NOT_FOUND')
    return job


def claim_due(state,worker,now):
    # An expired lease might mean Telegram accepted a message just before a crash.
    # Pause it for owner review rather than automatically duplicating an advert.
    for job in state['jobs'].values():
        if job.get('status')=='running' and parsed(job['lease_until'])<=now:
            if job.get('kind')=='site':
                job.update(status='ready',due=iso(now))
            else:
                job['status']='uncertain';job['error']='DELIVERY_NOT_CONFIRMED_AFTER_RESTART';job['notice_sent']=False
    for pending in state['jobs'].values():
        if pending.get('status')=='ready' and pending.get('kind') in DRAFT_KINDS and now-parsed(pending['due'])>timedelta(hours=1):
            pending.update(status='uncertain',error='MISSED_PUBLICATION_TIME',notice_sent=False)
    due=sorted((j for j in state['jobs'].values() if j.get('status')=='ready' and parsed(j['due'])<=now),key=lambda j:(0 if j.get('kind')=='reminder' else 1,j['due']))
    if not due:return None
    job=due[0];job.update(status='running',worker=worker,lease_until=iso(now+timedelta(minutes=5)))
    return copy.deepcopy(job)


def owned_lease(state,key,worker):
    job=state['jobs'].get(key)
    if not job or job.get('status')!='running' or job.get('worker')!=worker:raise AutomationError('LEASE_LOST')
    return job


def finish_job(state,key,worker,now,**fields):
    job=owned_lease(state,key,worker);job.update(fields)
    if job.get('daily'):
        job.update(status='ready',due=iso(next_daily(job['daily'],now)))
    elif job.get('interval_minutes'):
        job.update(status='ready',due=iso(now+timedelta(minutes=job['interval_minutes'])))
    else:job.update(status='done',finished=iso(now))
    job.pop('worker',None);job.pop('lease_until',None);job.pop('inflight',None)
    if job['status']=='done' and job['kind']=='broadcast':job['body']='[Текст у власному архіві переписки]'
    completed=sorted((j for j in state['jobs'].values() if j.get('status') in ('done','cancelled')),key=lambda j:j.get('finished',j['created']))
    for old in completed[:-100]:state['jobs'].pop(old['id'],None)


class AutomationEngine:
    def __init__(self,store,bot,logger,archive=None):
        self.store=store;self.bot=bot;self.logger=logger;self.archive=archive
        self.worker=uuid.uuid4().hex;self.task=None;self.tick_lock=asyncio.Semaphore(2)
        self.owner_send_locks={};self.last_owner_send={};self.wake=asyncio.Event()
    async def send_owner(self,owner,text):
        if self.archive:self.archive.owners.add(int(owner))
        async with self.owner_send_locks.setdefault(str(owner),asyncio.Lock()):
            wait=1.1-(time.monotonic()-self.last_owner_send.get(str(owner),-1000))
            if wait>0:await asyncio.sleep(wait)
            safe_text=text.encode('utf-16-le')[:7800].decode('utf-16-le',errors='ignore')
            message=await self.bot.send_message(int(owner),safe_text)
            self.last_owner_send[str(owner)]=time.monotonic()
            return message
    async def tick(self,now=None):
        async with self.tick_lock:
            now=now or utcnow()
            job=await self.store.change(lambda state:claim_due(state,self.worker,now))
            if not job:
                await self.notify_uncertain()
                return
            try:
                if job['kind']=='reminder':
                    late=(now-parsed(job['due'])).total_seconds()>180
                    text='Нагадування: '+job['body']+('\n[Доставлено із затримкою після простою сервісу]' if late else '')
                    sent=await self.send_owner(job['owner'],text)
                    await self.store.change(lambda state:finish_job(state,job['id'],self.worker,utcnow(),telegram_message_id=sent.message_id))
                elif job['kind'] in DRAFT_KINDS:await self.broadcast(job)
                elif job['kind']=='site':await self.poll_site(job)
                elif job['kind']=='digest':await self.digest(job)
                else:raise AutomationError('UNKNOWN_JOB_KIND')
            except TelegramRetryAfter as exc:
                def reschedule(state):
                    current=owned_lease(state,job['id'],self.worker)
                    current.update(status='ready',due=iso(utcnow()+timedelta(seconds=exc.retry_after+1)))
                    current.pop('worker',None);current.pop('lease_until',None);current.pop('inflight',None)
                await self.store.change(reschedule)
                self.logger('AUTOMATION_RATE_LIMIT',job['id']+' rescheduled after rejection')
            except Exception as exc:
                if isinstance(exc,AutomationError) and str(exc)=='LEASE_LOST' and (self.store.cached or {}).get('jobs',{}).get(job['id'],{}).get('status')=='cancelled':
                    self.logger('AUTOMATION_CANCELLED',job['id']);return
                # Avoid logging payloads, URLs with credentials or ad contents.
                self.logger('AUTOMATION_JOB_ERROR',job['id']+' '+type(exc).__name__)
                def pause(state):
                    current=state['jobs'].get(job['id'])
                    if current and current.get('worker')==self.worker and current.get('status')=='running':
                        current.update(status='uncertain',error=type(exc).__name__,notice_sent=False)
                try:
                    await self.store.change(pause)
                    await self.notify_uncertain()
                except Exception:self.logger('AUTOMATION_STATE_ERROR','Could not persist/report uncertain job')
    async def notify_uncertain(self):
        cached=self.store.cached or {}
        notice=next((j for j in cached.get('jobs',{}).values() if j.get('status')=='uncertain' and not j.get('notice_sent')),None)
        if not notice:return
        reason='час публікації минув більше ніж на годину; потрібне повторне підтвердження' if notice.get('error')=='MISSED_PUBLICATION_TIME' else 'результат дії не підтверджено'
        await self.send_owner(notice['owner'],f"Завдання {notice['id']} призупинене: {reason}. Перевір /jobs. Автоматичного відправлення не буде.")
        def acknowledged(state):
            job=state['jobs'].get(notice['id'])
            if job and job.get('status')=='uncertain':job['notice_sent']=True
        await self.store.change(acknowledged)

    async def broadcast(self,job):
        sent=list(job.get('sent_targets',[]));skipped=list(job.get('skipped_targets',[]))
        for target in job['targets']:
            if target in sent or target in skipped:continue
            def before(state):
                current=owned_lease(state,job['id'],self.worker)
                if target not in owner_state(state,job['owner'])['targets']:raise AutomationError('TARGET_REMOVED')
                current.update(inflight=target,lease_until=iso(utcnow()+timedelta(minutes=5)))
            for attempt in range(3):
                await self.store.change(before)
                try:
                    with allow_group_send(target):
                        if job['kind']=='poll':
                            from aiogram.types import InputPollOption
                            message=await self.bot.send_poll(int(target),job['body'],[InputPollOption(text=o) for o in job['options']],is_anonymous=True)
                        elif job['kind']=='copy':
                            message=await self.bot.copy_message(int(target),int(job['owner']),job['source_message_id'])
                        elif job['kind']=='forward':
                            message=await self.bot.forward_message(int(target),int(job['owner']),job['source_message_id'])
                        else:message=await self.bot.send_message(int(target),job['body'])
                    break
                except TelegramRetryAfter as exc:
                    # Telegram explicitly rejected the request: this retry cannot
                    # duplicate an accepted message. Network errors are not retried.
                    if attempt==2 or exc.retry_after>120:raise
                    await asyncio.sleep(exc.retry_after+0.5)
            sent.append(target)
            def acknowledge(state):
                current=state['jobs'].get(job['id'])
                if not current or current.get('worker')!=self.worker or current.get('status') not in ('running','cancelled'):raise AutomationError('LEASE_LOST')
                current.update(sent_targets=list(sent),last_message_id=message.message_id)
                if current['status']=='running':current['lease_until']=iso(utcnow()+timedelta(minutes=5))
                current.setdefault('deliveries',{})[target]=message.message_id
                poll=getattr(message,'poll',None)
                if poll:
                    current.setdefault('polls',{})[poll.id]={'chat_id':target,'message_id':message.message_id,
                        'question':poll.question,'total':poll.total_voter_count,'closed':poll.is_closed,
                        'options':[{'text':o.text,'votes':o.voter_count} for o in poll.options]}
                current.pop('inflight',None)
            await self.store.change(acknowledge)
            await asyncio.sleep(3.1)
        await self.store.change(lambda state:finish_job(state,job['id'],self.worker,utcnow(),sent_targets=sent))
        await self.send_owner(job['owner'],f"Розсилка {job['id']} завершена. Підтверджених груп: {len(sent)}; пропущено за твоєю командою: {len(skipped)}.")
    async def poll_site(self,job):
        from site_monitor import fetch_resource,resource_delta
        try:
            snapshot=await fetch_resource(job['url'])
        except Exception as exc:
            self.logger('SITE_FETCH_ERROR',job['id']+' '+type(exc).__name__)
            failures=job.get('failures',0)+1
            def retry(state):
                current=owned_lease(state,job['id'],self.worker)
                finish_job(state,job['id'],self.worker,utcnow(),failures=failures,
                    last_attempt=iso(utcnow()),last_report={'summary':'Сайт тимчасово недоступний; зміни не перевірено. Наступна спроба запланована.'})
                current['due']=iso(utcnow()+timedelta(minutes=min(job['interval_minutes'],15*min(4,2**min(failures-1,2)))))
            await self.store.change(retry)
            if job.get('report_requested'):
                await self.send_owner(job['owner'],'Сайт зараз недоступний; перевірку не завершено. Наступна спроба збережена в розкладі.')
                def clear_requested(state):state['jobs'][job['id']]['report_requested']=False
                await self.store.change(clear_requested)
            if failures==3:await self.send_owner(job['owner'],'Три перевірки сайту поспіль не вдалися: '+job['url']+'. Продовжую перевіряти з паузою; останній успішний стан збережено.')
            return
        delta=resource_delta(job.get('snapshot'),snapshot,job.get('keywords',[]))
        def save(state):
            current=owned_lease(state,job['id'],self.worker)
            current.update(snapshot=snapshot,last_checked=iso(utcnow()),last_report=delta,report_requested=False,failures=0)
            if delta.get('changed') and delta.get('matches'):
                items=state['matches'].setdefault(job['owner'],[])
                key='site:'+job['id']+':'+snapshot['hash'][:16]
                if not any(item['id']==key for item in items):
                    items.append({'id':key,'source':job['url'],'text':delta['summary'][:600],'link':job['url'],'created':iso(utcnow())})
                    items[:]=items[-100:]
            finish_job(state,job['id'],self.worker,utcnow())
        await self.store.change(save)
        if job.get('report_requested') or (delta.get('changed') and delta.get('matches')):
            await self.send_owner(job['owner'],'Перевірка сайту '+job['url']+'\n'+delta['summary'][:3000])
    async def digest(self,job):
        state,_=await self.store.read()
        events=state['matches'].get(job['owner'],[])
        seen=set(job.get('reported_event_ids',[]))
        pending=[e for e in events if e['id'] not in seen]
        latest=pending[-12:]
        sources=[j for j in state['jobs'].values() if j.get('owner')==job['owner'] and j.get('kind')=='site']
        lines=['Звіт моніторингу:']
        lines.extend(f"{e['source']}: {e['text'][:180]}\n{e.get('link','')}" for e in latest)
        lines.extend(f"{j['url']}: {j.get('last_report',{}).get('summary','ще не перевірено')[:180]}" for j in sources[:10])
        if not latest:lines.append('Нових збережених збігів немає.')
        if len(pending)>len(latest):lines.append(f'Показано {len(latest)} з {len(pending)} нових збігів. Збережений перелік: /report all')
        sent=await self.send_owner(job['owner'],'\n\n'.join(lines)[:4000])
        await self.store.change(lambda data:finish_job(data,job['id'],self.worker,utcnow(),last_digest=iso(utcnow()),reported_event_ids=list(dict.fromkeys(job.get('reported_event_ids',[])+[e['id'] for e in latest]))[-200:],telegram_message_id=sent.message_id))
    async def worker_loop(self,delay=0):
        if delay:await asyncio.sleep(delay)
        while True:
            wait=30
            try:await self.tick()
            except asyncio.CancelledError:raise
            except MemoryUnavailable as exc:
                self.logger('AUTOMATION_BACKEND_WAIT',str(exc))
                if str(exc) in ('MEMORY_INVALID_ACTION','MEMORY_AUTOMATION_SETUP_REQUIRED'):wait=300
            except Exception as exc:self.logger('AUTOMATION_TICK_ERROR',type(exc).__name__)
            try:await asyncio.wait_for(self.wake.wait(),timeout=wait)
            except asyncio.TimeoutError:pass
            self.wake.clear()
    async def run(self):
        self.logger('AUTOMATION_SCHEDULER','started; best effort on free hosting')
        # A long multi-group send must not monopolize the personal reminder queue.
        await asyncio.gather(self.worker_loop(),self.worker_loop(15))
    def start(self):self.task=asyncio.create_task(self.run());return self.task
    async def stop(self):
        if self.task:
            self.task.cancel()
            try:await self.task
            except asyncio.CancelledError:pass
