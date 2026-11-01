"""Starter Telegram tools; group mutations require explicit owner commands/grants."""
import re
from aiogram.filters import Command
from automation_engine import add_job,own_job,owner_state,AutomationError
from outbound_policy import allow_group_send

SKILLS=[
 'Перегляд Telegram ID','Перегляд відомостей групи','Перевірка прав бота',
 'Шаблони повідомлень','Чернетки розсилок','Розсилка за підтвердженим списком',
 'Заплановані розсилки','Чернетки опитувань','Публікація опитувань після підтвердження',
 'Зведені результати опитувань','Закриття власних опитувань',
 'Чернетки копіювання повідомлень і медіа','Чернетки пересилання з авторством',
 'Закріплення власних публікацій','Відкріплення власних публікацій',
 'Видалення власних публікацій із підтвердженням','Особисті нагадування',
 'Щоденні нагадування','Нагадування з голосового повідомлення',
 'Моніторинг груп за ключовими словами','Моніторинг сайтів і RSS',
 'Звіти на запит','Щоденні звіти','Контроль черги й скасування',
 'Відновлення непідтверджених завдань за явною командою','Аварійна зупинка',
 'Поглиблений вебпошук','Збереження дослідницьких звітів',
]


def register_telegram_tools(features):
    dp=features.dp
    def command(name):
        def decorate(fn):
            async def protected(message):
                if not await features.access(message) or message.chat.type!='private':return
                try:
                    await fn(message)
                    features.engine.wake.set()
                except Exception as exc:await features.reply_error(message,exc)
            dp.message.register(protected,Command(name));return fn
        return decorate
    async def draft(message,kind,body,**extra):
        state,_=await features.store.read();targets=owner_state(state,message.from_user.id)['targets']
        if not targets:raise AutomationError('NO_TARGETS')
        key=await features.store.change(lambda data:add_job(data,message.from_user.id,kind,body,targets=list(targets),sent_targets=[],**extra))
        details='\n'.join(f"• {g['title']} ({gid})" for gid,g in targets.items())
        preview=body+('\nАнонімне опитування:\n'+'\n'.join(f'{i}. {o}' for i,o in enumerate(extra['options'],1)) if kind=='poll' else '')
        await features.send_long(message,f'ЧЕРНЕТКА {kind} — нічого не відправлено в групи.\n{preview}\n{details}\n\nВідправити: /send_draft {key}\nЗапланувати: /schedule_draft {key} | 2026-11-02 12:00\nСкасувати: /cancel {key}')
    @command('telegram_skills')
    async def skills(message):await features.send_long(message,'\n'.join('• '+s for s in SKILLS))
    @command('tg_id')
    async def tg_id(message):await message.answer(f'Tвій Telegram ID: {message.from_user.id}\nID цього приватного чату: {message.chat.id}')
    @command('group_info')
    async def group_info(message):
        key=(message.text or '').partition(' ')[2].strip();state,_=await features.store.read()
        allowed=key in owner_state(state,message.from_user.id)['targets'] or str(message.from_user.id)+':'+key in state['sources']
        if not allowed:raise AutomationError('NOT_A_GROUP')
        group=await features.group_info(int(key),message.from_user.id)
        await message.answer(f"{group['title']}\nID: {group['id']}\nЧитання звичайних повідомлень: {'доступне' if group['readable'] else 'може бути обмежене privacy mode'}")
    @command('template_save')
    async def template_save(message):
        key,sep,text=(message.text or '').partition(' ')[2].partition('|');key=key.strip()
        if not re.fullmatch(r'[\w-]{1,40}',key) or not sep or not text.strip() or len(text.strip().encode('utf-16-le'))//2>3800:raise AutomationError('TEMPLATE_FORMAT')
        def save(state):
            items=owner_state(state,message.from_user.id).setdefault('templates',{})
            if len(items)>=10 and key not in items:raise AutomationError('TEMPLATE_LIMIT')
            items[key]=text.strip()
        await features.store.change(save);await message.answer('Шаблон збережено. Попередній перегляд: /template '+key)
    @command('templates')
    async def templates(message):
        state,_=await features.store.read();items=owner_state(state,message.from_user.id).get('templates',{})
        await message.answer('\n'.join(items) or 'Шаблонів немає. /template_save назва | текст')
    @command('template')
    async def template(message):
        key=(message.text or '').partition(' ')[2].strip();state,_=await features.store.read()
        text=owner_state(state,message.from_user.id).get('templates',{}).get(key)
        if text is None:raise AutomationError('TEMPLATE_NOT_FOUND')
        await features.send_long(message,'ШАБЛОН — нічого не відправлено в групи.\n'+text)
    @command('template_remove')
    async def template_remove(message):
        key=(message.text or '').partition(' ')[2].strip()
        def remove(state):owner_state(state,message.from_user.id).setdefault('templates',{}).pop(key,None)
        await features.store.change(remove);await message.answer('Шаблон видалено.')
    @command('broadcast_template')
    async def broadcast_template(message):
        key=(message.text or '').partition(' ')[2].strip();state,_=await features.store.read()
        text=owner_state(state,message.from_user.id).get('templates',{}).get(key)
        if text is None:raise AutomationError('TEMPLATE_NOT_FOUND')
        await draft(message,'broadcast',text)
    @command('poll_draft')
    async def poll_draft(message):
        parts=[p.strip() for p in (message.text or '').partition(' ')[2].split('|')]
        if not 3<=len(parts)<=11 or not 1<=len(parts[0].encode('utf-16-le'))//2<=250 or any(not 1<=len(p.encode('utf-16-le'))//2<=80 for p in parts[1:]) or len(set(parts[1:]))!=len(parts[1:]):raise AutomationError('POLL_FORMAT')
        await draft(message,'poll',parts[0],options=parts[1:])
    async def stage(message,kind):
        reply=message.reply_to_message
        if not reply:raise AutomationError('REPLY_REQUIRED')
        state,_=await features.store.read()
        if not owner_state(state,message.from_user.id)['targets']:raise AutomationError('NO_TARGETS')
        # Stage an immutable bot-owned preview in the owner's private chat. Later
        # edits to the original user message cannot silently change the approved ad.
        if kind=='copy':staged=await features.bot.copy_message(message.chat.id,message.chat.id,reply.message_id)
        else:staged=await features.bot.forward_message(message.chat.id,message.chat.id,reply.message_id)
        await draft(message,kind,(reply.text or reply.caption or '[Медіаповідомлення]')[:600],source_message_id=staged.message_id)
        await message.answer('Вище — приватна копія для перевірки. Саме її буде відправлено після окремого підтвердження; для forward зберігається авторство.')
    @command('copy_draft')
    async def copy_draft(message):await stage(message,'copy')
    @command('forward_draft')
    async def forward_draft(message):await stage(message,'forward')
    @command('poll_results')
    async def poll_results(message):
        key=(message.text or '').partition(' ')[2].strip();state,_=await features.store.read();job=own_job(state,message.from_user.id,key)
        if job['kind']!='poll':raise AutomationError('JOB_NOT_FOUND')
        lines=[]
        for poll in job.get('polls',{}).values():
            lines.append(poll['question']+f"\nГрупа: {poll['chat_id']} · голосів: {poll['total']} · закрито: {poll['closed']}\n"+'\n'.join(f"{o['text']}: {o['votes']}" for o in poll['options']))
        await features.send_long(message,'\n\n'.join(lines) or 'Підтверджених даних опитування поки немає. Результати оновлюються лише з доступних Telegram-подій.')
    @command('poll_close')
    async def poll_close(message):
        key=(message.text or '').partition(' ')[2].strip();state,_=await features.store.read();job=own_job(state,message.from_user.id,key)
        if job['kind']!='poll':raise AutomationError('JOB_NOT_FOUND')
        count=0
        for poll in job.get('polls',{}).values():
            if poll.get('closed'):continue
            with allow_group_send(poll['chat_id']):closed=await features.bot.stop_poll(int(poll['chat_id']),poll['message_id'])
            def record_closed(data):
                saved=own_job(data,message.from_user.id,key)['polls'][closed.id]
                saved.update(closed=True,total=closed.total_voter_count,options=[{'text':o.text,'votes':o.voter_count} for o in closed.options])
            await features.store.change(record_closed)
            count+=1
        await message.answer(f'Підтверджено закриття опитувань: {count}.')
    async def manage_sent(message,action):
        args=(message.text or '').partition(' ')[2].split()
        if len(args)<2:raise AutomationError('SENT_FORMAT')
        key,target=args[:2];state,_=await features.store.read();job=own_job(state,message.from_user.id,key)
        msg_id=job.get('deliveries',{}).get(target)
        if msg_id is None:raise AutomationError('DELIVERY_NOT_FOUND')
        if action=='delete' and (len(args)!=3 or args[2]!='confirm'):
            await message.answer(f'Це остаточно видалить власне повідомлення {msg_id} у групі {target}. Підтверди: /delete_sent {key} {target} confirm');return
        with allow_group_send(target):
            if action=='pin':await features.bot.pin_chat_message(int(target),msg_id,disable_notification=True)
            elif action=='unpin':await features.bot.unpin_chat_message(int(target),message_id=msg_id)
            else:await features.bot.delete_message(int(target),msg_id)
        if action=='delete':
            def deleted(data):own_job(data,message.from_user.id,key).get('deliveries',{}).pop(target,None)
            await features.store.change(deleted)
        await message.answer('Операцію з власною публікацією підтверджено Telegram.')
    @command('pin_sent')
    async def pin_sent(message):await manage_sent(message,'pin')
    @command('unpin_sent')
    async def unpin_sent(message):await manage_sent(message,'unpin')
    @command('delete_sent')
    async def delete_sent(message):await manage_sent(message,'delete')

    async def poll_update(poll):
        def update(state):
            for job in state['jobs'].values():
                saved=job.get('polls',{}).get(poll.id)
                if saved:
                    saved.update(total=poll.total_voter_count,closed=poll.is_closed,
                        options=[{'text':o.text,'votes':o.voter_count} for o in poll.options])
        try:await features.store.change(update)
        except Exception as exc:features.log('POLL_UPDATE_ERROR',type(exc).__name__)
    dp.poll.register(poll_update)
