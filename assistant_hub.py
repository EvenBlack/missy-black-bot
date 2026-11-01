"""Owner-scoped assistant workspace: explicit commands, no unsolicited publication."""
import asyncio
import copy
import hashlib
import json
import re
import shutil
import uuid
from datetime import timedelta
from io import BytesIO
from urllib.parse import urlparse
from aiogram import F,types
from aiogram.filters import Command
from aiogram.types import BufferedInputFile,KeyboardButton,ReplyKeyboardMarkup
from automation_engine import AutomationError,owner_state,own_job,add_job,parse_when,utcnow,iso,parsed,KYIV,finish_job
from assistant_schedule import parse_rule,next_rule,calendar_export
from assistant_recall import retrieve_history
from dialogue_archive import apply_preference
from book_library import import_book
from document_reader import extract_document,DocumentError
from site_monitor import validate_url,fetch_resource

MENU={'📋 Завдання':'tasks','📁 Проєкти':'projects','🧠 Пам’ять':'memory','🗓 Календар':'calendar','📚 Книги':'books','🖼 Фото':'photos','🎙 Нотатки':'notes','⏰ Розклад':'jobs','🌐 Моніторинг':'report','🛠 Допомога':'assistant_help','🩺 Стан':'health','⚙️ Інтеграції':'connections'}
HELP='''Особистий помічник — час Києва
/menu — кнопки; /menu_hide — сховати
/tasks [all]; /task Текст | Проєкт | high | 2026-11-05 12:00
/task_done КОД; /task_edit КОД | Новий текст; /task_delete КОД confirm
/projects; /project Назва | Опис; /project_archive КОД
/memory; /memory_add Факт; /memory_edit КОД | Виправлений факт
/memory_forget КОД confirm; /memory_export
/recall Запит — пошук із розширенням слів у кількох сторінках архіву
/calendar; /event 2026-11-05 12:00 | 60 | Зустріч
/event_delete КОД confirm; /calendar_export
Імпорт календаря: надішли .ics (без автоматичного додавання нагадувань).
/remind_repeat будні 09:00 | Текст
/remind_repeat пн,ср,пт 18:00 | Текст
/snooze КОД | через 15 хв — разове перенесення; /reschedule КОД | 2026-11-05 12:00
/briefing morning 08:00; /briefing evening 20:00; /briefing morning off
/overview — огляд зараз
Документи: надішли PDF/DOCX; /documents; /doc КОД; /doc off; /document КОД
/ocr — відповіддю на фото/зображення для розпізнавання
/connect_site назва | https://example.org/data.json — публічне читання
/connections; /site_read назва; /disconnect_site назва
/health; /health_on 12:00; /health_off; /workspace_export
/workspace_restore — відповіддю на JSON-експорт; лише після попереднього перегляду й підтвердження
Календар тут внутрішній. Зовнішній календар і дії в акаунтах сайтів потребують окремого адаптера та авторизації. Паролі в чат не надсилай.
'''

def key():return uuid.uuid4().hex[:12]
def arg(message):return (message.text or '').partition(' ')[2].strip()
def owned_items(state,section,owner):return state.setdefault(section,{}).setdefault(str(owner),{})
def find_item(state,section,owner,code):
    item=owned_items(state,section,owner).get(code)
    if not item:raise AutomationError('ITEM_NOT_FOUND')
    return item

def text_limit(value,maximum=1000):
    value=value.strip()
    if not value or len(value)>maximum:raise AutomationError('TEXT_LIMIT')
    return value

def overview(state,owner,now=None):
    now=now or utcnow();uid=str(owner)
    tasks=[t for t in state.get('tasks',{}).get(uid,{}).values() if t['status']=='open']
    tasks.sort(key=lambda t:(0 if t.get('priority')=='high' else 1,t.get('due') or '9999',t['created']))
    events=[e for e in state.get('calendar',{}).get(uid,{}).values() if parsed(e['end'])>=now and parsed(e['start'])<=now+timedelta(days=1)]
    lines=[f'Огляд · {now.astimezone(KYIV):%d.%m.%Y %H:%M} (Київ)',f'Відкритих завдань: {len(tasks)}']
    for task in tasks[:8]:
        due=(' · '+parsed(task['due']).astimezone(KYIV).strftime('%d.%m %H:%M')) if task.get('due') else ''
        lines.append(f"• {task['text'][:200]}{due}")
    lines.append('Події найближчих 24 годин:')
    lines.extend(f"• {parsed(e['start']).astimezone(KYIV):%d.%m %H:%M} — {e['title'][:200]}" for e in sorted(events,key=lambda e:e['start'])[:8])
    if not events:lines.append('Немає збережених подій.')
    matches=state.get('matches',{}).get(uid,[])
    recent=[m for m in matches if parsed(m['created'])>=now-timedelta(days=1)]
    lines.append(f'Збережених збігів моніторингу за 24 години: {len(recent)}. /report')
    return '\n'.join(lines)

class AssistantHub:
    def __init__(self,features,read_memory,change_memory,get_transport,send_long,logger):
        self.f=features;self.dp=features.dp;self.store=features.store;self.read_memory=read_memory;self.change_memory=change_memory
        self.get_transport=get_transport;self.send_long=send_long;self.log=logger
        features.engine.assistant=self
        self.register()

    async def failure(self,message,exc):
        errors={'ITEM_NOT_FOUND':'Запис не знайдено у твоєму сховищі.','TEXT_LIMIT':'Потрібен непорожній текст до 1000 символів.',
            'RULE_FORMAT':'Приклад: /remind_repeat будні 09:00 | Текст або пн,ср,пт 18:00 | Текст.',
            'LIMIT':'Досягнуто ліміт цього розділу. Видали непотрібні записи.',
            'CALENDAR_FORMAT':'Приклад: /event 2026-11-05 12:00 | 60 | Зустріч. Тривалість — 1–1440 хв.',
            'DOC_SIZE':'Ліміт документа — 8 МіБ.','DOC_PAGE_LIMIT':'PDF має містити від 1 до 80 сторінок.',
            'DOC_TEXT_LIMIT':'Ліміт розпізнаного тексту — 300 тисяч символів. Розділи файл.',
            'DOC_NO_TEXT':'Не вдалося отримати текст. Спробуй чіткіший скан.',
            'DOC_EXTRACTION_FAILED':'Не вдалося прочитати документ. Файл може бути пошкоджений, захищений або надто складний.',
            'DOC_ENCRYPTED':'PDF із захистом паролем поки не підтримується.',
            'RESTORE_OWNER':'Файл має бути власним експортом цього Telegram-користувача.',
            'RESTORE_FORMAT':'Формат даних для відновлення некоректний. Поточні дані не змінено.',
            'PROJECT_EXISTS':'Проєкт із такою назвою вже є. Переглянь /projects.',
            'TASK_FORMAT':'Приклад: /task Текст | Назва проєкту | high | 2026-11-05 12:00. Додаткові поля можна пропустити.',
            'PRIORITY_FORMAT':'Пріоритет: high, normal або low.',
            'REMINDER_NOT_MOVABLE':'Можна переносити лише власне нагадування, яке очікує або вже виконане. Поточне чи непідтверджене спочатку перевір у /jobs.',
            'CONNECTION_FORMAT':'Приклад: /connect_site news | https://example.org/news . Назва — латиниця, цифри та _.',
            'PRIVATE_ONLY':'Ця дія доступна лише в особистому чаті.'}
        if str(exc) in errors:await message.answer(errors[str(exc)])
        else:await self.f.reply_error(message,exc)

    def command(self,name):
        def register(fn):
            async def wrapped(message):
                if not await self.f.access(message):return
                if message.chat.type!='private':return
                try:await fn(message);self.f.engine.wake.set()
                except Exception as exc:
                    self.log('ASSISTANT_COMMAND_ERROR',name+' '+type(exc).__name__)
                    await self.failure(message,exc)
            self.dp.message.register(wrapped,Command(name));return wrapped
        return register

    async def run_scheduled(self,job):
        engine=self.f.engine
        if job['kind']=='briefing':
            state,_=await self.store.read();text=overview(state,job['owner'])
            await engine.send_owner(job['owner'],('Ранковий огляд\n' if job['period']=='morning' else 'Вечірній огляд\n')+text)
        elif job['kind']=='health':
            checks=await self.health_checks(job['owner'])
            bad=[line for ok,line in checks if not ok]
            if bad:await engine.send_owner(job['owner'],'Перевірка справності:\n'+'\n'.join(bad))
        else:raise AutomationError('UNKNOWN_JOB_KIND')
        await self.store.change(lambda s:finish_job(s,job['id'],engine.worker,utcnow(),last_checked=iso(utcnow())))

    async def health_checks(self,owner):
        transport=self.get_transport();checks=[]
        try:
            result=await transport.request('get')
            data=result.get('data')
            checks.append((isinstance(data,dict),'Основна пам’ять: '+('відповідає' if isinstance(data,dict) else 'некоректна відповідь')))
            checks.append((result.get('backup_ready') is True,'Резервна копія пам’яті: '+('налаштована; вміст копії окремо не перевірено' if result.get('backup_ready') else 'не підтверджена')))
        except Exception:checks.append((False,'Основна пам’ять: недоступна'))
        try:
            state,_=await self.store.read()
            checks.append((True,'Сховище завдань: прочитано'))
            uncertain=sum(j.get('status')=='uncertain' and j.get('owner')==str(owner) for j in state['jobs'].values())
            checks.append((uncertain==0,f'Твоїх завдань із невизначеним результатом: {uncertain}'))
        except Exception:checks.append((False,'Сховище завдань: недоступне'))
        available=all(shutil.which(p) for p in ('pdfinfo','pdftotext','pdftoppm','tesseract'))
        checks.append((available,'PDF/OCR інструменти: '+('доступні' if available else 'не встановлені')))
        return checks

    async def document_context(self,owner,query,state=None):
        if state is None:state,_=await self.store.read()
        uid=str(owner)
        selected=owner_state(state,uid).get('active_document')
        if not selected:return None
        item=find_item(state,'documents',uid,selected)
        result=await self.get_transport().request('book_context',owner=uid,book_id=item['text_id'],query=query[:200])
        result['title']='Документ: '+item['filename']
        return result

    async def import_document(self,message,data,name):
        await message.answer('Читаю документ і зберігаю доступний текст…')
        result=await extract_document(data,name)
        text='\n\n'.join(f'[{title}]\n{body}' for title,body in result['chapters'])
        # Existing verified TXT storage works without another Apps Script update.
        doc_id=hashlib.sha256(data).hexdigest()[:24]
        text_id,_,count=await import_book(self.get_transport(),message.from_user.id,text.encode('utf-8'),name+'.txt',message.date.isoformat(),'Extracted document text; original remains in Telegram')
        media=message.document or (message.photo[-1] if message.photo else None)
        def save(state):
            items=owned_items(state,'documents',message.from_user.id)
            if len(items)>=50 and doc_id not in items:raise AutomationError('LIMIT')
            items[doc_id]={'id':doc_id,'filename':name[:100],'text_id':text_id,'file_id':media.file_id if media else None,
                'created':iso(utcnow()),'warnings':result['warnings'][:85]}
            owner_state(state,message.from_user.id)['active_document']=doc_id
        await self.store.change(save)
        await self.change_memory(lambda m:m.setdefault('user_books',{}).setdefault(str(message.from_user.id),{}).update(active=None))
        await message.answer(f'Текст документа збережено в Google Drive, фрагментів: {count}. Документ вибрано для обговорення.\nОригінал залишається в Telegram; окрему копію оригіналу в Drive не створено.\n/document {doc_id}\nВийти: /doc off\n'+'\n'.join(result['warnings'][:8]))

    def register(self):
        command=self.command
        @command('assistant_help')
        async def help_(m):await self.send_long(m,HELP)
        @command('menu')
        async def menu(m):
            labels=list(MENU);rows=[[KeyboardButton(text=t) for t in labels[i:i+2]] for i in range(0,len(labels),2)]
            await m.answer('Меню Міссі. Для дій із параметрами: /assistant_help',reply_markup=ReplyKeyboardMarkup(keyboard=rows,resize_keyboard=True,is_persistent=True))
        @command('menu_hide')
        async def hide(m):await m.answer('Меню приховано. Повернути: /menu',reply_markup=types.ReplyKeyboardRemove())
        @command('projects')
        async def projects(m):
            state,_=await self.store.read();items=owned_items(state,'projects',m.from_user.id)
            await self.send_long(m,'\n\n'.join(f"{p['name']} · {p['status']}\n{p['description']}\n{p['id']}" for p in items.values()) or 'Проєктів немає. /project Назва | Опис')
        @command('project')
        async def project(m):
            parts=arg(m).split('|',1);name=text_limit(parts[0],100);desc=text_limit(parts[1],500) if len(parts)>1 and parts[1].strip() else '';code=key()
            def save(s):
                items=owned_items(s,'projects',m.from_user.id)
                if len(items)>=40:raise AutomationError('LIMIT')
                if any(p['name'].casefold()==name.casefold() for p in items.values()):raise AutomationError('PROJECT_EXISTS')
                items[code]={'id':code,'name':name,'description':desc,'status':'active','created':iso(utcnow())}
            await self.store.change(save);await m.answer(f'Проєкт збережено: {name}\n{code}')
        @command('project_archive')
        async def archive(m):
            await self.store.change(lambda s:find_item(s,'projects',m.from_user.id,arg(m)).update(status='archived'))
            await m.answer('Проєкт архівовано. Завдання збережені.')
        @command('task')
        async def task(m):
            parts=[p.strip() for p in arg(m).split('|')]
            if len(parts)>4:raise AutomationError('TASK_FORMAT')
            text=text_limit(parts[0]);project=parts[1] if len(parts)>1 else '';priority=parts[2] if len(parts)>2 and parts[2] else 'normal'
            if priority not in ('high','normal','low'):raise AutomationError('PRIORITY_FORMAT')
            due=iso(parse_when(parts[3])) if len(parts)>3 and parts[3] else None;code=key()
            def save(s):
                items=owned_items(s,'tasks',m.from_user.id)
                if len(items)>=150:raise AutomationError('LIMIT')
                project_id=None
                if project:
                    found=next((p for p in owned_items(s,'projects',m.from_user.id).values() if p['status']=='active' and project in (p['id'],p['name'])),None)
                    if not found:raise AutomationError('ITEM_NOT_FOUND')
                    project_id=found['id']
                items[code]={'id':code,'text':text,'project':project_id,'priority':priority,'due':due,'status':'open','created':iso(utcnow())}
            await self.store.change(save);await m.answer(f'Завдання збережено: {text}\nКод: {code}\n/task_done {code}\nСтрок сам по собі не створює нагадування; для нього є /remind.')
        @command('tasks')
        async def tasks(m):
            state,_=await self.store.read();items=owned_items(state,'tasks',m.from_user.id)
            values=[t for t in items.values() if arg(m)=='all' or t['status']=='open']
            values.sort(key=lambda t:(0 if t['priority']=='high' else 1,t.get('due') or '9999',t['created']))
            await self.send_long(m,'\n\n'.join(f"{t['text']} · {t['priority']} · {t['status']}\n"+(f"Строк: {parsed(t['due']).astimezone(KYIV):%d.%m.%Y %H:%M}\n" if t.get('due') else '')+f"/task_done {t['id']}" for t in values) or 'Відкритих завдань немає. /task Текст')
        @command('task_done')
        async def done(m):
            await self.store.change(lambda s:find_item(s,'tasks',m.from_user.id,arg(m)).update(status='done',finished=iso(utcnow())))
            await m.answer('Завдання виконане.')
        @command('task_edit')
        async def edit(m):
            code,sep,text=arg(m).partition('|')
            if not sep:raise AutomationError('TEXT_LIMIT')
            text=text_limit(text)
            await self.store.change(lambda s:find_item(s,'tasks',m.from_user.id,code.strip()).update(text=text))
            await m.answer('Завдання оновлено.')
        @command('task_delete')
        async def delete(m):
            bits=arg(m).split();code=bits[0] if bits else ''
            state,_=await self.store.read();item=find_item(state,'tasks',m.from_user.id,code)
            if bits[1:]!=['confirm']:await m.answer(f"Видалити завдання «{item['text']}»?\n/task_delete {code} confirm");return
            def remove(s):find_item(s,'tasks',m.from_user.id,code);owned_items(s,'tasks',m.from_user.id).pop(code)
            await self.store.change(remove);await m.answer('Завдання видалено.')

        @command('memory')
        async def memory(m):
            state,_=await self.store.read();mem=await self.read_memory();items=owned_items(state,'facts',m.from_user.id)
            legacy=mem.get('user_facts',{}).get(str(m.from_user.id),[])
            lines=['Збережені факти (не повний архів переписки):']
            lines.extend(f"{code}: {v['text']}\nДжерело: {v['source']} · {v['created']}" for code,v in items.items())
            lines.extend(f"legacy:{i+1}: {v['text'] if isinstance(v,dict) else v}" for i,v in enumerate(legacy))
            profile=mem.get('user_profiles',{}).get(str(m.from_user.id),{})
            lines.extend(f"Побажання {v.get('source','?')}: {v['text']}\n/preference_remove {v.get('source','?')}" for v in profile.get('feedback',[]))
            lines.append('Побажання: /preferences. Дії: /memory_add, /memory_edit, /memory_forget. Видалення факту не стирає історію чи резервні копії.')
            await self.send_long(m,'\n\n'.join(lines))
        @command('preference_remove')
        async def preference_remove(m):
            source=arg(m)
            def remove(mem):
                uid=str(m.from_user.id);profile=mem.get('user_profiles',{}).get(uid,{})
                old=profile.get('feedback',[])
                kept=[p for p in old if str(p.get('source'))!=source]
                if len(kept)==len(old):raise AutomationError('ITEM_NOT_FOUND')
                mem.setdefault('user_profiles',{})[uid]={'feedback':[]}
                for item in kept:apply_preference(mem,uid,item['text'],item['source'])
            await self.change_memory(remove)
            await m.answer('Побажання вилучено з активного профілю. Архів повідомлення залишився.')
        @command('memory_add')
        async def memory_add(m):
            text=text_limit(arg(m));code=key()
            def save(s):
                items=owned_items(s,'facts',m.from_user.id)
                if len(items)>=60:raise AutomationError('LIMIT')
                items[code]={'text':text,'source':f'telegram:{m.message_id}','created':iso(utcnow())}
            await self.store.change(save);await m.answer(f'Факт збережено. /memory_edit {code} | Виправлений текст')
        async def modify_fact(m,forget=False):
            raw=arg(m);code,sep,text=raw.partition('|');code=code.strip()
            if forget:
                bits=raw.split();code=bits[0] if bits else ''
                if bits[1:]!=['confirm']:await m.answer(f'Припинити використання факту? /memory_forget {code} confirm\nІсторія та резервні копії залишаться.');return
            else:
                if not sep:raise AutomationError('TEXT_LIMIT')
                text=text_limit(text)
            if code.startswith('legacy:'):
                index=int(code.partition(':')[2])-1
                def update(mem):
                    items=mem.get('user_facts',{}).get(str(m.from_user.id),[])
                    if index<0 or index>=len(items):raise AutomationError('ITEM_NOT_FOUND')
                    if forget:items.pop(index)
                    else:items[index]={'text':text,'source':f'telegram:{m.message_id}','updated':iso(utcnow())}
                await self.change_memory(update)
            else:
                def update(s):
                    item=find_item(s,'facts',m.from_user.id,code)
                    if forget:owned_items(s,'facts',m.from_user.id).pop(code)
                    else:item.update(text=text,source=f'telegram:{m.message_id}',updated=iso(utcnow()))
                await self.store.change(update)
            await m.answer('Факт вилучено з активної пам’яті; історія та копії залишилися.' if forget else 'Факт виправлено.')
        @command('memory_edit')
        async def memory_edit(m):await modify_fact(m)
        @command('memory_forget')
        async def memory_forget(m):await modify_fact(m,True)
        @command('memory_export')
        async def memory_export(m):
            state,_=await self.store.read();mem=await self.read_memory();uid=str(m.from_user.id)
            data={'facts':state.get('facts',{}).get(uid,{}),'legacy_facts':mem.get('user_facts',{}).get(uid,[]),'preferences':mem.get('user_profiles',{}).get(uid,{}),'created':iso(utcnow())}
            await m.answer_document(BufferedInputFile(json.dumps(data,ensure_ascii=False,indent=2).encode(),filename='my_memory.json'),caption='Твої факти й побажання. Повна переписка сюди не включена.')
        @command('recall')
        async def recall(m):
            query=text_limit(arg(m),200)
            result=await retrieve_history(self.get_transport(),m.from_user.id,query,exclude=f'user:{m.message_id}')
            lines=[f"{r['created']} · {r['role']}\n{r['text'][:1400]}" for r in result['messages']]
            lines.append('Це пошук за словами та їх розширеннями; можливі нерелевантні збіги.')
            if result.get('next_cursor') is not None:lines.append(f"Архів перевірено частково. /history after:{result['next_cursor']} {query}")
            if result.get('unreadable_count'):lines.append('Частину файлів не прочитано.')
            await self.send_long(m,'\n\n'.join(lines))

        @command('event')
        async def event(m):
            parts=[p.strip() for p in arg(m).split('|',2)]
            if len(parts)!=3 or not parts[1].isdigit() or not 1<=int(parts[1])<=1440:raise AutomationError('CALENDAR_FORMAT')
            start=parse_when(parts[0]);end=start+timedelta(minutes=int(parts[1]));title=text_limit(parts[2],300);code=key()
            def save(s):
                items=owned_items(s,'calendar',m.from_user.id)
                if len(items)>=200:raise AutomationError('LIMIT')
                overlaps=[e['title'] for e in items.values() if parsed(e['start'])<end and parsed(e['end'])>start]
                items[code]={'id':code,'title':title,'start':iso(start),'end':iso(end),'created':iso(utcnow()),'source':'telegram'}
                return overlaps
            overlaps=await self.store.change(save)
            await m.answer(f'Подію збережено: {title}\n{start.astimezone(KYIV):%d.%m.%Y %H:%M} (Київ)\nКод: {code}\nНагадування окремо: /remind.'+('\nПеретин у часі: '+', '.join(overlaps) if overlaps else ''))
        @command('calendar')
        async def calendar(m):
            state,_=await self.store.read();items=owned_items(state,'calendar',m.from_user.id)
            future=[e for e in items.values() if parsed(e['end'])>=utcnow()]
            future.sort(key=lambda e:e['start'])
            await self.send_long(m,'Внутрішній календар Міссі:\n\n'+'\n\n'.join(f"{parsed(e['start']).astimezone(KYIV):%d.%m.%Y %H:%M} — {e['title']}\n{e['id']}" for e in future[:50])+'\n/event Дата Час | Хвилини | Назва\n/calendar_export')
        @command('event_delete')
        async def event_delete(m):
            bits=arg(m).split();code=bits[0] if bits else ''
            state,_=await self.store.read();find_item(state,'calendar',m.from_user.id,code)
            if bits[1:]!=['confirm']:await m.answer(f'Видалити подію? /event_delete {code} confirm');return
            def remove(s):find_item(s,'calendar',m.from_user.id,code);owned_items(s,'calendar',m.from_user.id).pop(code)
            await self.store.change(remove);await m.answer('Подію видалено з внутрішнього календаря.')
        @command('calendar_export')
        async def export_calendar(m):
            state,_=await self.store.read();items=owned_items(state,'calendar',m.from_user.id)
            await m.answer_document(BufferedInputFile(calendar_export(items.values()),filename='missy_calendar.ics'),caption='Файл внутрішнього календаря для імпорту. Це не двостороння синхронізація.')
        @command('remind_repeat')
        async def repeat(m):
            schedule,sep,body=arg(m).partition('|')
            if not sep:raise AutomationError('RULE_FORMAT')
            rule=parse_rule(schedule);due=next_rule(rule)
            code=await self.store.change(lambda s:add_job(s,m.from_user.id,'reminder',text_limit(body),due,recurrence=rule))
            await m.answer(f'Повторюване нагадування збережено: {due.astimezone(KYIV):%d.%m.%Y %H:%M}. /cancel {code}\nНа безкоштовному хостингу можливі затримки.')
        async def move(m):
            code,sep,when=arg(m).partition('|')
            if not sep:raise AutomationError('TIME_FORMAT')
            due=parse_when(when)
            def save(s):
                job=own_job(s,m.from_user.id,code.strip())
                if job['kind']!='reminder' or job['status'] not in ('ready','done'):raise AutomationError('REMINDER_NOT_MOVABLE')
                job.update(status='ready',due=iso(due));job.pop('telegram_message_id',None)
            await self.store.change(save);await m.answer(f'Нагадування перенесено на {due.astimezone(KYIV):%d.%m.%Y %H:%M}. Повторюване правило збережено, якщо було задане.')
        command('snooze')(move);command('reschedule')(move)
        @command('briefing')
        async def briefing(m):
            bits=arg(m).split()
            if len(bits)!=2 or bits[0] not in ('morning','evening'):await m.answer('/briefing morning 08:00 або /briefing evening 20:00; вимкнення: /briefing morning off');return
            period,clock=bits
            if clock!='off':rule=parse_rule('щодня '+clock)
            def save(s):
                old=[j for j in s['jobs'].values() if j.get('owner')==str(m.from_user.id) and j['kind']=='briefing' and j.get('period')==period]
                for j in old:j['status']='cancelled'
                if clock!='off':return add_job(s,m.from_user.id,'briefing','',next_rule(rule),recurrence=rule,period=period)
            code=await self.store.change(save)
            await m.answer('Огляд вимкнено.' if clock=='off' else f'Особистий огляд збережено: {clock} (Київ). /cancel {code}')
        @command('overview')
        async def show_overview(m):
            state,_=await self.store.read();await self.send_long(m,overview(state,m.from_user.id))

        @command('documents')
        async def documents(m):
            state,_=await self.store.read();items=owned_items(state,'documents',m.from_user.id)
            await self.send_long(m,'\n\n'.join(f"{d['filename']}\n/doc {d['id']}\n/document {d['id']}" for d in items.values()) or 'Надішли PDF/DOCX як файл. Для фото — /ocr відповіддю на зображення.')
        @command('doc')
        async def doc(m):
            code=arg(m)
            def save(s):
                if code!='off':find_item(s,'documents',m.from_user.id,code)
                owner_state(s,m.from_user.id)['active_document']=None if code=='off' else code
            await self.store.change(save)
            if code!='off':await self.change_memory(lambda mem:mem.setdefault('user_books',{}).setdefault(str(m.from_user.id),{}).update(active=None))
            await m.answer('Обговорення документа завершено.' if code=='off' else 'Документ вибрано. Запитуй про його текст; відповіді спиратимуться на уривки.')
        @command('document')
        async def original(m):
            state,_=await self.store.read();d=find_item(state,'documents',m.from_user.id,arg(m))
            await m.answer_document(d['file_id'],caption='Оригінал із Telegram. У Drive збережено витягнутий текст.')
        @command('ocr')
        async def ocr(m):
            reply=m.reply_to_message
            if not reply or not (reply.photo or reply.document):await m.answer('Надішли /ocr відповіддю на фото або зображення-файл.');return
            media=reply.document or reply.photo[-1];name=(getattr(media,'file_name',None) or 'scan.jpg')
            if (media.file_size or 0)>8*1024*1024:raise DocumentError('DOC_SIZE')
            buf=BytesIO();await m.bot.download(media,destination=buf,timeout=50)
            result=await extract_document(buf.getvalue(),name)
            text='\n\n'.join(t for _,t in result['chapters'])
            text_id,_,_=await import_book(self.get_transport(),m.from_user.id,text.encode(),'ocr_'+hashlib.sha256(buf.getvalue()).hexdigest()[:12]+'.txt',m.date.isoformat(),'OCR from Telegram image')
            await m.answer_document(BufferedInputFile(text.encode(),filename='recognized_text.txt'),caption=f'Розпізнаний текст збережено у Drive. /readbook {text_id}\nOCR може помилятися.')

        @command('connections')
        async def connections(m):
            state,_=await self.store.read();items=owned_items(state,'connections',m.from_user.id)
            await self.send_long(m,'Публічні джерела для читання:\n'+'\n'.join(f"{n}: {v['url']}\n/site_read {n}" for n,v in items.items())+'\n/connect_site назва | HTTPS-адреса\nЗовнішні акаунти не авторизовані. Публікації та зміни на сайтах не підключені.')
        @command('connect_site')
        async def connect(m):
            name,sep,url=arg(m).partition('|');name=name.strip();url=url.strip()
            if not sep or not re.fullmatch('[a-zA-Z0-9_]{1,32}',name):raise AutomationError('CONNECTION_FORMAT')
            validate_url(url);snapshot=await fetch_resource(url)
            def save(s):
                items=owned_items(s,'connections',m.from_user.id)
                if len(items)>=20 and name not in items:raise AutomationError('LIMIT')
                items[name]={'url':url,'title':snapshot['title'],'created':iso(utcnow()),'mode':'public_read_only'}
            await self.store.change(save);await m.answer(f'Джерело для публічного читання збережено: {name}. /site_read {name}\nАвтоматичного моніторингу ще немає; для нього /watch_site.')
        @command('site_read')
        async def site_read(m):
            state,_=await self.store.read();item=find_item(state,'connections',m.from_user.id,arg(m));snapshot=await fetch_resource(item['url'])
            text=snapshot['title']+'\n'+snapshot['url']+'\n\n'+'\n'.join(snapshot['lines'])
            if snapshot['truncated']:text+='\n[Показано обмежений текстовий знімок]'
            await self.send_long(m,text)
        @command('disconnect_site')
        async def disconnect(m):
            def remove(s):find_item(s,'connections',m.from_user.id,arg(m));owned_items(s,'connections',m.from_user.id).pop(arg(m))
            await self.store.change(remove);await m.answer('Джерело читання видалено. Окремі завдання /watch_site цим не скасовуються; перевір /jobs.')
        @command('health')
        async def health(m):
            checks=await self.health_checks(m.from_user.id);await m.answer('\n'.join(('✓ ' if ok else '⚠ ')+line for ok,line in checks)+'\nЦе перевірка сховищ та інструментів; зовнішню мовну модель і відновлення копії не тестує.')
        @command('health_on')
        async def health_on(m):
            clock=arg(m);rule=parse_rule('щодня '+clock)
            def save(s):
                for j in s['jobs'].values():
                    if j.get('owner')==str(m.from_user.id) and j['kind']=='health':j['status']='cancelled'
                return add_job(s,m.from_user.id,'health','',next_rule(rule),recurrence=rule)
            code=await self.store.change(save);await m.answer(f'Щоденна перевірка о {clock}. Повідомлю лише про виявлені проблеми. /cancel {code}')
        @command('health_off')
        async def health_off(m):
            def stop(s):
                for j in s['jobs'].values():
                    if j.get('owner')==str(m.from_user.id) and j['kind']=='health':j['status']='cancelled'
            await self.store.change(stop);await m.answer('Планові перевірки вимкнено.')
        @command('workspace_export')
        async def workspace_export(m):
            state,_=await self.store.read();uid=str(m.from_user.id)
            data={'version':1,'created':iso(utcnow()),'owner':uid,'scope':'assistant workspace only; excludes full history, binary archives and secrets',
                'owner_settings':state['owners'].get(uid,{}),'jobs':{k:v for k,v in state['jobs'].items() if v.get('owner')==uid},
                'sources':{k:v for k,v in state['sources'].items() if v.get('owner')==uid}}
            for section in ('tasks','projects','facts','calendar','documents','connections','matches','research_reports'):data[section]=copy.deepcopy(state.get(section,{}).get(uid,{}))
            payload=json.dumps(data,ensure_ascii=False,indent=2).encode();digest=hashlib.sha256(payload).hexdigest()
            await m.answer_document(BufferedInputFile(payload,filename='missy_workspace.json'),caption='Особистий експорт робочих даних. Це не повна копія Drive. SHA-256: '+digest)

        @command('workspace_restore')
        async def restore_preview(m):
            reply=m.reply_to_message
            if not reply or not reply.document:await m.answer('Надішли /workspace_restore відповіддю на власний missy_workspace.json. Спочатку буде лише попередній перегляд.');return
            if (reply.document.file_size or 0)>300000:raise AutomationError('LIMIT')
            buffer=BytesIO();await m.bot.download(reply.document,destination=buffer,timeout=30)
            if len(buffer.getvalue())>300000:raise AutomationError('LIMIT')
            from workspace_recovery import validate_restore
            data=validate_restore(json.loads(buffer.getvalue()),m.from_user.id)
            code=key()
            def stage(s):owner_state(s,m.from_user.id)['restore_preview']={'code':code,'data':data,'expires':iso(utcnow()+timedelta(minutes=15))}
            await self.store.change(stage)
            await m.answer('Попередній перегляд відновлення: '+', '.join(f'{section}: {len(items)}' for section,items in data.items())+f'\nПідтвердження замінить ці чотири розділи твоїх даних. Черга надсилань, нагадування й розсилки не відновлюються.\n/workspace_restore_confirm {code}\nПідтвердження чинне 15 хвилин.')
        @command('workspace_restore_confirm')
        async def restore_confirm(m):
            from workspace_recovery import validate_restore
            def apply(s):
                owner=owner_state(s,m.from_user.id);preview=owner.get('restore_preview')
                if not preview or preview['code']!=arg(m) or parsed(preview['expires'])<=utcnow():raise AutomationError('ITEM_NOT_FOUND')
                data=validate_restore({'version':1,'owner':str(m.from_user.id),**preview['data']},m.from_user.id)
                # Keep the immediately previous inert sections on Drive for review/export.
                owner['previous_workspace']={'created':iso(utcnow()),'data':{section:copy.deepcopy(s.get(section,{}).get(str(m.from_user.id),{})) for section in data}}
                for section,items in data.items():s.setdefault(section,{})[str(m.from_user.id)]=items
                owner.pop('restore_preview',None)
            await self.store.change(apply)
            await m.answer('Завдання, проєкти, факти й календар відновлено. Нагадування, розсилки та інші зовнішні дії не запускалися. Попередні дані збережено у стані робочого простору.')

        async def incoming_document(m):
            if not await self.f.access(m) or m.chat.type!='private':return
            try:
                if (m.document.file_size or 0)>8*1024*1024:raise DocumentError('DOC_SIZE')
                buf=BytesIO();await m.bot.download(m.document,destination=buf,timeout=50)
                if m.document.file_name.lower().endswith('.ics'):await self.import_calendar(m,buf.getvalue())
                else:await self.import_document(m,buf.getvalue(),m.document.file_name)
            except Exception as exc:self.log('DOCUMENT_ERROR',type(exc).__name__);await self.failure(m,exc)
        self.dp.message.register(incoming_document,F.document.file_name.regexp(r'(?i).*\.(pdf|docx|ics)$'))
        async def menu_press(m):
            if not await self.f.access(m) or m.chat.type!='private':return
            name=MENU[m.text]
            # Dispatch only the static allowlisted read/help actions; no fake Telegram update.
            handlers={'tasks':tasks,'projects':projects,'memory':memory,'calendar':calendar,'health':health,'connections':connections,'assistant_help':help_}
            if name in handlers:
                proxy=MenuMessage(m,'/'+name)
                await handlers[name](proxy)
            elif name=='jobs':await m.answer('Перелік розкладу: /jobs\nНове нагадування: /remind через 15 хв | Текст')
            elif name=='report':await self.f.report(m)
            else:await m.answer({'books':'Бібліотека: /books\nПошук: /findbook назва','photos':'Фотоархів: /photos\nОсновний образ: «Катрусю, покажи себе»','notes':'Голосові нотатки: /notes\nПошук: /notes слово'}[name])
        self.dp.message.register(menu_press,F.text.in_(set(MENU)))

    async def import_calendar(self,m,data):
        if len(data)>1024*1024:raise AutomationError('LIMIT')
        from icalendar import Calendar
        calendar=Calendar.from_ical(data);events=[];warnings=0;now=utcnow()
        components=list(calendar.walk('VEVENT'))
        if len(components)>200:raise AutomationError('LIMIT')
        for event in components:
            # Do not silently import recurring or floating/all-day events incorrectly.
            if str(event.get('status','')).upper()=='CANCELLED':warnings+=1;continue
            if any(k in event for k in ('RRULE','RDATE','EXDATE','RECURRENCE-ID')):warnings+=1;continue
            start=event.decoded('dtstart',None);end=event.decoded('dtend',None)
            from datetime import datetime
            if not isinstance(start,datetime) or not start.tzinfo or not isinstance(end,datetime) or not end.tzinfo or end<=start:warnings+=1;continue
            if len(str(event.get('summary',''))) >300:warnings+=1;continue
            uid=str(event.get('uid',''))
            code=hashlib.sha256((uid+'\0'+iso(start)+'\0'+iso(end)).encode()).hexdigest()[:12] if uid else key()
            events.append({'id':code,'title':str(event.get('summary','Подія')),'start':iso(start),'end':iso(end),'created':iso(now),'source':'ics'})
        def save(s):
            items=owned_items(s,'calendar',m.from_user.id)
            if len(set(items)|{e['id'] for e in events})>200:raise AutomationError('LIMIT')
            for e in events:items[e['id']]=e
        await self.store.change(save)
        await m.answer(f'Імпортовано подій: {len(events)}. Пропущено непідтримуваних (повторювані, на весь день або без часового поясу): {warnings}.\nНагадування не створювалися. /calendar')

class MenuMessage:
    def __init__(self,message,text):self.original=message;self.text=text
    def __getattr__(self,name):return getattr(self.original,name)
