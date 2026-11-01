"""Explicit Telegram controls for personal jobs, selected groups and monitors."""
import copy
import re
from datetime import timedelta
from aiogram import types
from aiogram.filters import Command
from automation_engine import (AutomationStore,AutomationEngine,AutomationError,DRAFT_KINDS,add_job,own_job,owner_state,
    parse_when,next_daily,utcnow,iso,parsed,KYIV)
from outbound_policy import allow_group_send
from site_monitor import validate_url,fetch_resource

HELP=(
 'Нагадування (час Києва):\n/remind через 15 хв | Перевірити пошту\n/remind 2026-11-02 10:30 | Зустріч\n'
 '/remind_daily 09:00 | План на день\n/jobs\n/cancel КОД\n\n'
 'Розсилка: додай бота до дозволеної групи й напиши там /missy_group.\n'
 '/groups\n/group_remove ID\n/broadcast Текст оголошення\n'
 'Після перегляду: /broadcast_send КОД або /broadcast_at КОД | 2026-11-02 12:00\n\n'
 'Моніторинг груп: /watch_group ID | слово1, слово2 (або в групі /watch_here слово1, слово2).\n'
 '/watchlist\n/unwatch ID\n\n'
 'Сайт або RSS: /watch_site https://example.org/news | 60 | слово1, слово2\n'
 '/site_report КОД\n/site_check КОД\n/digest 19:00\n/report\n\n'
 'Розклад зберігається в Google Drive. На безкоштовному хостингу можливі затримки під час сну або збоїв сервісу.'
)


def keywords(value):
    return list(dict.fromkeys(w.strip().casefold()[:80] for w in value.split(',') if w.strip()))[:15]


def status(member):return str(getattr(member.status,'value',member.status))


class AutomationFeatures:
    def __init__(self,dp,bot,get_transport,access,has_access,send_long,logger,archive):
        self.dp=dp;self.bot=bot;self.access=access;self.has_access=has_access;self.send_long=send_long;self.log=logger
        self.store=AutomationStore(get_transport);self.engine=AutomationEngine(self.store,bot,logger,archive)
        self.last_alert={}
        self.register()
        from telegram_tools import register_telegram_tools
        register_telegram_tools(self)
    async def reply_error(self,message,exc):
        self.log('AUTOMATION_COMMAND_ERROR',type(exc).__name__)
        if str(exc) in ('MEMORY_INVALID_ACTION','MEMORY_AUTOMATION_SETUP_REQUIRED'):
            await message.answer('Для цієї можливості потрібне одноразове оновлення Google Apps Script. Черга не створена; після оновлення сховища повтори команду.');return
        known={
            'POLL_FORMAT':'Приклад: /poll_draft Питання | Варіант 1 | Варіант 2. Потрібно 2–10 різних варіантів.',
            'TEMPLATE_FORMAT':'Приклад: /template_save назва | текст (до 3800 символів).',
            'TEMPLATE_LIMIT':'Зберігаю до 10 шаблонів на користувача.',
            'TEMPLATE_NOT_FOUND':'Такого шаблону немає у твоєму списку.',
            'REPLY_REQUIRED':'Надішли команду відповіддю на повідомлення у цьому приватному чаті.',
            'DELIVERY_NOT_FOUND':'Немає підтвердженої власної публікації цього завдання в цій групі.',
            'SENT_FORMAT':'Потрібні код завдання та ID групи.',
            'BODY_SIZE':'Текст має бути непорожній і до 3800 символів (емодзі можуть займати два). Нічого не обрізаю й не надсилаю.',
            'INTERVAL_RANGE':'Інтервал перевірки сайту — від 15 хвилин до 7 днів.',
            'KEYWORDS_REQUIRED':'Вкажи хоча б одне слово для моніторингу.',
            'TOO_MANY_GROUPS':'До 30 груп для розсилки та до 20 груп для моніторингу на користувача.',
            'SITE_FORMAT':'Приклад: /watch_site https://example.org/news | 60 | слово1, слово2',
            'RESUME_FORMAT':'Використай /jobs для перегляду непідтвердженого результату, а потім /resume_job КОД resend або skip.',
            'TIME_FORMAT':'Вкажи «через 15 хв» або дату й час: 2026-11-02 10:30 (Київ).',
            'TIME_IN_PAST':'Цей час уже минув.', 'NONEXISTENT_TIME':'Цього місцевого часу немає через перехід годинника.',
            'AMBIGUOUS_TIME':'Цей час повторюється через перехід годинника. Обери інший час.',
            'TIME_RANGE':'Нагадування: від 1 хвилини до 366 днів.',
            'JOB_NOT_FOUND':'Такого завдання у твоєму списку немає.', 'TOO_MANY_JOBS':'Ліміт — 20 активних завдань на користувача.',
            'NO_TARGETS':'Спочатку додай дозволені групи: команда /missy_group у відповідній групі.',
            'ALREADY_CONFIRMED':'Розсилка вже підтверджена або завершена; повторно її не відправляю.',
            'NOT_A_GROUP':'Потрібна група або супергрупа.', 'BOT_NOT_MEMBER':'Бота немає в цій групі або йому заборонено писати.',
            'OWNER_NOT_MEMBER':'Не вдалося підтвердити твою участь. Додай групу командою /missy_group у ній самій.',
            'STATE_FULL':'Сховище завдань заповнене. Видали непотрібні завдання.',
            'PUBLIC_HTTPS_REQUIRED':'Для моніторингу потрібна публічна HTTPS-адреса без логіна й пароля.',
            'PRIVATE_ADDRESS':'Локальні й внутрішні адреси не підтримуються.',
        }
        await message.answer(known.get(str(exc),'Операцію не підтверджено. Перевір /automation або спробуй пізніше.'))
    async def group_info(self,chat_id,owner,from_group=False):
        chat=await self.bot.get_chat(chat_id)
        if chat.type not in ('group','supergroup'):raise AutomationError('NOT_A_GROUP')
        me=await self.bot.get_me();member=await self.bot.get_chat_member(chat.id,me.id)
        if status(member) in ('left','kicked') or (status(member)=='restricted' and not getattr(member,'can_send_messages',False)):raise AutomationError('BOT_NOT_MEMBER')
        if status(member) not in ('administrator','creator') and getattr(chat,'permissions',None) and chat.permissions.can_send_messages is False:raise AutomationError('BOT_NOT_MEMBER')
        if not from_group:
            person=await self.bot.get_chat_member(chat.id,int(owner))
            if status(person) in ('left','kicked'):raise AutomationError('OWNER_NOT_MEMBER')
        return {'id':str(chat.id),'title':str(chat.title or chat.id)[:120],
            'readable':status(member) in ('administrator','creator') or getattr(me,'can_read_all_group_messages',False) is True}
    async def register_target(self,message,chat_id,from_group=False):
        meta=await self.group_info(chat_id,message.from_user.id,from_group)
        def add(state):
            targets=owner_state(state,message.from_user.id)['targets']
            if len(targets)>=30 and meta['id'] not in targets:raise AutomationError('TOO_MANY_GROUPS')
            targets[meta['id']]=meta
        await self.store.change(add)
        await message.answer(f"Групу додано до твого переліку розсилки: {meta['title']} ({meta['id']}). Оголошення ще не надсилається; чернетка й відправлення — у приватному чаті.")
    async def register_watch(self,message,chat_id,words,from_group=False):
        if not words:raise AutomationError('KEYWORDS_REQUIRED')
        meta=await self.group_info(chat_id,message.from_user.id,from_group)
        key=str(message.from_user.id)+':'+meta['id']
        def add(state):
            if sum(s.get('owner')==str(message.from_user.id) for s in state['sources'].values())>=20 and key not in state['sources']:raise AutomationError('TOO_MANY_GROUPS')
            state['sources'][key]={**meta,'owner':str(message.from_user.id),'keywords':words,'enabled':True,'created':iso(utcnow())}
            owner_state(state,message.from_user.id)
        await self.store.change(add)
        notice='' if meta['readable'] else '\nУвага: зараз звичайні повідомлення можуть бути недоступні. Бот має бути адміністратором або privacy mode слід вимкнути в BotFather.'
        await message.answer(f"Моніторинг нових доступних повідомлень увімкнено: {meta['title']}. Слова: {', '.join(words)}. Старої історії Telegram бот не завантажує."+notice)
    async def handle_group(self,message,allow_commands=True):
        if allow_commands and message.from_user and self.has_access(message):
            text=message.text or '';command=text.split(maxsplit=1)[0].split('@')[0] if text else ''
            if command in ('/missy_group','/watch_here'):
                try:
                    with allow_group_send(message.chat.id):
                        if command=='/missy_group':await self.register_target(message,message.chat.id,True)
                        else:await self.register_watch(message,message.chat.id,keywords(text.partition(' ')[2]),True)
                except Exception as exc:
                    with allow_group_send(message.chat.id):await self.reply_error(message,exc)
                return
        if not message.text and not message.caption:return
        text=message.text or message.caption
        if message.from_user and message.from_user.is_bot:return
        try:
            if self.store.cached is None:await self.store.read()
            sources=[s for s in self.store.cached['sources'].values() if s.get('enabled') and s.get('id')==str(message.chat.id)]
            for source in sources:
                if not any(w in text.casefold() for w in source['keywords']):continue
                owner=source['owner'];event_id=str(message.chat.id)+':'+str(message.message_id)
                link=''
                if message.chat.username:link=f'https://t.me/{message.chat.username}/{message.message_id}'
                elif message.chat.type=='supergroup':link=f"https://t.me/c/{str(message.chat.id).removeprefix('-100')}/{message.message_id}"
                event={'id':event_id,'source':source['title'],'text':text[:600],'link':link,'created':message.date.isoformat()}
                def save(state):
                    existing=state['sources'].get(owner+':'+str(message.chat.id))
                    if not existing or not existing.get('enabled') or not any(w in text.casefold() for w in existing['keywords']):return False
                    items=state['matches'].setdefault(owner,[])
                    if any(i['id']==event_id for i in items):return False
                    items.append(event);items[:]=items[-100:];existing['last_match']=iso(utcnow())
                    return True
                stored=await self.store.change(save)
                last=self.last_alert.get((owner,source['id']))
                if stored and (last is None or (utcnow()-last).total_seconds()>=60):
                    self.last_alert[(owner,source['id'])]=utcnow()
                    await self.engine.send_owner(owner,'Збіг у групі '+source['title']+'\n'+text[:2200]+'\n'+link)
        except Exception as exc:self.log('GROUP_MONITOR_ERROR',type(exc).__name__)
    async def report(self,message):
        state,_=await self.store.read();owner=str(message.from_user.id)
        lines=['Останні збережені збіги:']
        count=100 if (message.text or '').partition(' ')[2].strip()=='all' else 10
        lines.extend(f"{e['source']}: {e['text'][:250]}\n{e.get('link','')}" for e in state['matches'].get(owner,[])[-count:])
        lines.extend(f"{j['id']} · {j['url']}\n{j.get('last_report',{}).get('summary','ще не перевірено')[:500]}" for j in state['jobs'].values() if j.get('owner')==owner and j.get('kind')=='site')
        await self.send_long(message,'\n\n'.join(lines) if len(lines)>1 else 'Нових збережених збігів поки немає.')
    async def try_natural(self,message,text):
        cleaned=re.sub(r'^(?:міссі|місі|катрусю|катруся)[,!:\s]+','',text.strip(),flags=re.I)
        if re.match(r'нагадай(?:\s|$)',cleaned,re.I):
            content=re.sub(r'^нагадай\s+(?:мені\s+)?','',cleaned,flags=re.I)
            match=re.match(r'^(через\s+\d+\s*(?:хв(?:илин(?:у|и)?)?|год(?:ин(?:у|и)?)?|дн(?:і|ів)?|день))[, :]+(.+)$',content,re.I|re.S)
            absolute=re.match(r'^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})[, :]+(.+)$',content,re.S)
            match=match or absolute
            if not match:
                await message.answer('Напиши, наприклад: «Нагадай мені через 15 хв перевірити пошту» або /remind 2026-11-02 10:30 | Зустріч. Без зрозумілого часу нагадування не створюю.')
                return True
            try:
                due=parse_when(match.group(1));body=match.group(2).strip()
                key=await self.store.change(lambda state:add_job(state,message.from_user.id,'reminder',body,due))
                self.engine.wake.set()
                await message.answer(f'Нагадування збережено на {due.astimezone(KYIV):%d.%m.%Y %H:%M} (Київ). /cancel {key}\nНа безкоштовному хостингу можлива затримка під час простою.')
            except Exception as exc:await self.reply_error(message,exc)
            return True
        if re.search(r'розішли|зроби розсилку|запусти розсилку',cleaned,re.I):
            await message.answer('Спочатку створи чернетку: /broadcast текст. Я покажу точний текст і перелік груп; відправлення буде лише після твоєї окремої команди /broadcast_send КОД.')
            return True
        return False

    def register(self):
        def command(name):
            def decorate(fn):
                async def protected(message):
                    if not await self.access(message):return
                    if message.chat.type!='private':return
                    try:
                        await fn(message)
                        self.engine.wake.set()
                    except Exception as exc:await self.reply_error(message,exc)
                self.dp.message.register(protected,Command(name));return fn
            return decorate
        @command('automation')
        async def help_command(message):await message.answer(HELP)
        @command('remind')
        async def remind(message):
            arg=(message.text or '').partition(' ')[2];when,sep,text=arg.partition('|')
            if not sep or not text.strip():raise AutomationError('TIME_FORMAT')
            due=parse_when(when)
            key=await self.store.change(lambda state:add_job(state,message.from_user.id,'reminder',text.strip(),due))
            await message.answer(f"Нагадування збережено: {due.astimezone(KYIV):%d.%m.%Y %H:%M} (Київ).\nКод: {key}\nСкасувати: /cancel {key}\nНа безкоштовному хостингу можлива затримка під час простою.")
        @command('remind_daily')
        async def remind_daily(message):
            when,sep,text=(message.text or '').partition(' ')[2].partition('|');when=when.strip()
            if not sep or not text.strip():raise AutomationError('TIME_FORMAT')
            due=next_daily(when)
            key=await self.store.change(lambda state:add_job(state,message.from_user.id,'reminder',text.strip(),due,daily=when))
            await message.answer(f'Щоденне нагадування збережено на {when} (Київ). /cancel {key}')
        @command('jobs')
        async def jobs(message):
            state,_=await self.store.read();owner=str(message.from_user.id)
            lines=[f"{j['id']} · {j['kind']} · {j['status']}\n{parsed(j['due']).astimezone(KYIV):%d.%m.%Y %H:%M} · {j.get('body','')[:100]}\n/cancel {j['id']}" for j in state['jobs'].values() if j.get('owner')==owner and j.get('status')!='cancelled']
            for job in state['jobs'].values():
                if job.get('owner')==owner and job.get('status')=='uncertain':
                    lines.append(f"{job['id']}: підтверджено груп {len(job.get('sent_targets',[]))}; непідтверджений адресат: {job.get('inflight','особисте повідомлення')}.\nПовтор може дублювати вже доставлене. Явно повторити: /resume_job {job['id']} resend\nПропустити непідтверджене: /resume_job {job['id']} skip")
            await self.send_long(message,'\n\n'.join(lines[-30:]) or 'Збережених завдань немає.')
        @command('cancel')
        async def cancel(message):
            key=(message.text or '').partition(' ')[2].strip()
            def change(state):own_job(state,message.from_user.id,key)['status']='cancelled'
            await self.store.change(change)
            await message.answer('Завдання скасовано. Вже надіслані повідомлення це не відкликає.')
        @command('resume_job')
        async def resume_job(message):
            args=(message.text or '').partition(' ')[2].split()
            if len(args)!=2 or args[1] not in ('resend','skip'):raise AutomationError('RESUME_FORMAT')
            key,mode=args
            def resume(state):
                job=own_job(state,message.from_user.id,key)
                if job['status']!='uncertain':raise AutomationError('ALREADY_CONFIRMED')
                if mode=='skip' and (job['kind'] not in DRAFT_KINDS or not job.get('inflight')):job['status']='cancelled';return
                if mode=='skip' and job.get('inflight'):
                    job.setdefault('skipped_targets',[]).append(job['inflight'])
                job.update(status='ready',due=iso(utcnow()))
                job.pop('worker',None);job.pop('lease_until',None);job.pop('inflight',None)
            await self.store.change(resume)
            await message.answer('Твою команду оброблено. Статус і підтверджені відправлення: /jobs')
        @command('group_add')
        async def group_add(message):
            arg=(message.text or '').partition(' ')[2].strip()
            if not re.fullmatch(r'-\d{5,20}',arg):raise AutomationError('NOT_A_GROUP')
            await self.register_target(message,int(arg))
        @command('groups')
        async def groups(message):
            state,_=await self.store.read();targets=owner_state(state,message.from_user.id)['targets']
            await self.send_long(message,'\n'.join(f"{g['title']} · {key}" for key,g in targets.items()) or 'Груп для розсилки немає. Додай бота до групи та напиши там /missy_group.')
        @command('group_remove')
        async def group_remove(message):
            key=(message.text or '').partition(' ')[2].strip()
            def remove(state):owner_state(state,message.from_user.id)['targets'].pop(key,None)
            await self.store.change(remove);await message.answer('Групу виключено з майбутніх відправлень.')
        @command('broadcast')
        async def broadcast(message):
            text=(message.text or '').partition(' ')[2].strip()
            if not text:raise AutomationError('EMPTY_BROADCAST')
            state,_=await self.store.read();targets=owner_state(state,message.from_user.id)['targets']
            if not targets:raise AutomationError('NO_TARGETS')
            snapshot=list(targets)
            key=await self.store.change(lambda data:add_job(data,message.from_user.id,'broadcast',text,targets=snapshot,sent_targets=[]))
            await self.send_long(message,'ЧЕРНЕТКА — ще не надіслано.\nГрупи: '+', '.join(g['title'] for g in targets.values())+'\n\n'+text+f'\n\nВідправити: /broadcast_send {key}\nЗапланувати: /broadcast_at {key} | 2026-11-02 12:00\nСкасувати: /cancel {key}')
        async def confirm(message,scheduled=False):
            arg=(message.text or '').partition(' ')[2];key,_,when=arg.partition('|');key=key.strip()
            due=parse_when(when) if scheduled else utcnow()
            def change(state):
                job=own_job(state,message.from_user.id,key)
                if job.get('kind') not in DRAFT_KINDS or job.get('status')!='draft':raise AutomationError('ALREADY_CONFIRMED')
                job.update(status='ready',due=iso(due))
            await self.store.change(change)
            await message.answer(f'Розсилку {key} підтверджено на {due.astimezone(KYIV):%d.%m.%Y %H:%M} (Київ). Статус: /jobs')
        @command('broadcast_send')
        async def broadcast_send(message):await confirm(message)
        @command('send_draft')
        async def send_draft(message):await confirm(message)
        @command('schedule_draft')
        async def schedule_draft(message):await confirm(message,True)
        @command('broadcast_at')
        async def broadcast_at(message):await confirm(message,True)
        @command('watch_group')
        async def watch_group(message):
            target,sep,words=(message.text or '').partition(' ')[2].partition('|')
            if not sep or not re.fullmatch(r'-\d{5,20}',target.strip()):raise AutomationError('NOT_A_GROUP')
            await self.register_watch(message,int(target),keywords(words))
        @command('watchlist')
        async def watchlist(message):
            state,_=await self.store.read()
            rows=[f"{s['title']} · {s['id']}\n{', '.join(s['keywords'])}\nОстанній збіг: {s.get('last_match','ще немає')}" for s in state['sources'].values() if s['owner']==str(message.from_user.id) and s.get('enabled')]
            await self.send_long(message,'\n\n'.join(rows) or 'Груп для моніторингу немає.')
        @command('unwatch')
        async def unwatch(message):
            key=str(message.from_user.id)+':'+(message.text or '').partition(' ')[2].strip()
            def change(state):state['sources'].pop(key,None)
            await self.store.change(change);await message.answer('Моніторинг групи вимкнено.')
        @command('watch_site')
        async def watch_site(message):
            args=[x.strip() for x in (message.text or '').partition(' ')[2].split('|')]
            if len(args)<2:raise AutomationError('SITE_FORMAT')
            url=validate_url(args[0]);minutes=int(args[1])
            if not 15<=minutes<=10080:raise AutomationError('INTERVAL_RANGE')
            snapshot=await fetch_resource(url)
            key=await self.store.change(lambda state:add_job(state,message.from_user.id,'site','',utcnow()+timedelta(minutes=minutes),url=url,interval_minutes=minutes,keywords=keywords(args[2]) if len(args)>2 else [],snapshot=snapshot,last_checked=iso(utcnow()),last_report={'summary':'Початковий стан збережено.'}))
            await message.answer(f'Сайт перевірено й додано: {snapshot["title"]}. Інтервал — {minutes} хв.\nЗвіт: /site_report {key}\nПеревірити зараз: /site_check {key}\nВимкнути: /cancel {key}')
        @command('site_report')
        async def site_report(message):
            key=(message.text or '').partition(' ')[2].strip();state,_=await self.store.read();job=own_job(state,message.from_user.id,key)
            if job['kind']!='site':raise AutomationError('JOB_NOT_FOUND')
            await self.send_long(message,f"{job['url']}\nОстання перевірка: {job.get('last_checked','ще немає')}\n{job.get('last_report',{}).get('summary','Звіту немає.')}")
        @command('site_check')
        async def site_check(message):
            key=(message.text or '').partition(' ')[2].strip()
            def change(state):
                job=own_job(state,message.from_user.id,key)
                if job['kind']!='site' or job['status']=='running':raise AutomationError('ALREADY_RUNNING')
                job.update(status='ready',due=iso(utcnow()),report_requested=True)
            await self.store.change(change);await message.answer('Позачергову перевірку поставлено в чергу. Результат надішлю сюди.')
        @command('digest')
        async def digest(message):
            when=(message.text or '').partition(' ')[2].strip();due=next_daily(when)
            def change(state):
                for job in state['jobs'].values():
                    if job.get('owner')==str(message.from_user.id) and job.get('kind')=='digest' and job.get('status')!='running':job['status']='cancelled'
                return add_job(state,message.from_user.id,'digest','',due,daily=when)
            key=await self.store.change(change);await message.answer(f'Щоденний звіт збережено на {when} (Київ). /cancel {key}')
        @command('report')
        async def report_command(message):await self.report(message)
        @command('stop_all')
        async def stop_all(message):
            owner=str(message.from_user.id)
            def stop(state):
                for job in state['jobs'].values():
                    if job.get('owner')==owner and job.get('status') in ('ready','running','draft','uncertain'):job['status']='cancelled'
                for source in state['sources'].values():
                    if source.get('owner')==owner:source['enabled']=False
            await self.store.change(stop)
            await message.answer('Твої розсилки, розклад і моніторинг зупинено. Уже надіслане або відправлення, яке було в процесі доставки, це не відкликає.')
        @command('telegram_status')
        async def telegram_status(message):
            me=await self.bot.get_me()
            state,_=await self.store.read();owner=str(message.from_user.id)
            targets=owner_state(state,owner)['targets']
            lines=[f"Privacy mode: {'вимкнений' if getattr(me,'can_read_all_group_messages',False) else 'увімкнений або не підтверджено'}. Груп у переліку розсилки: {len(targets)}."]
            for key in list(targets)[:10]:
                try:
                    meta=await self.group_info(int(key),message.from_user.id)
                    lines.append(f"{meta['title']}: бот є в групі; читання всіх звичайних повідомлень {'доступне' if meta['readable'] else 'може бути обмежене'}.")
                except Exception:lines.append(f'{key}: права або доступ не підтверджено.')
            await self.send_long(message,'\n'.join(lines))
