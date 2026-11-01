"""Explicit Ukrainian phrases -> existing command handlers, never arbitrary model actions."""
import re
from dataclasses import dataclass
from datetime import timedelta
from aiogram.filters import Command
from automation_engine import KYIV,utcnow

VOICE_HELP="""Можна писати або говорити без слеша:
• «Міссі, відкрий меню»
• «Покажи мої завдання» / «Покажи календар»
• «Додай завдання зателефонувати другу»
• «Створи проєкт Моя бібліотека»
• «Додай факт у пам’ять Мій улюблений колір — синій»
• «Нагадай мені через п’ять хвилин перевірити пошту»
• «Нагадай мені завтра о 10:30 про зустріч»
• «Нагадай мені по буднях о 9:00 перевірити пошту»
• «Покажи себе» / «Покажи мої книги»
• «Покажи огляд дня» / «Покажи нагадування»
• «Знайди в історії синю чашку»
• «Зроби поглиблений пошук історії мого міста»
• «Підготуй розсилку Текст оголошення» — лише чернетка.
Підтримуються визначені фрази та близькі варіанти, не довільні формулювання всіх команд. Для незрозумілих параметрів потрібне уточнення. Надсилання в групи, видалення та відновлення — лише точною текстовою командою після перегляду. Голосові зберігаються з розшифровкою; відповіді поки текстом. Голосове як відповідь на фото залишається описом фото, а не командою.
"""

@dataclass(frozen=True)
class Intent:
    command:str=''
    argument:str=''
    hint:str=''

# Only reviewed commands may be reached through this interpreter. In particular,
# confirmation, deletion, restoration, and public sending are absent.
ALLOWED=frozenset('menu menu_hide tasks projects memory preferences calendar books photos notes jobs report overview documents connections health status assistant_help voice_help telegram_skills groups watchlist showself task project memory_add recall deepsearch findbook broadcast remind remind_daily remind_repeat event cancel stop_all'.split())
READ_PHRASES={
    'voice_help':('покажи голосові команди','як керувати голосом','голосові команди'),
    'showself':('покажи себе','покажи свій образ','покажи своє фото'),
    'menu':('відкрий головне меню','покажи головне меню','відкрий меню','покажи меню','головне меню','меню','відкрити меню'),
    'menu_hide':('сховай меню','закрий меню','прибери меню'),
    'tasks':('покажи завдання','покажи мої завдання','мої завдання','список завдань','покажи мої задачі','покажи задачі'),
    'projects':('покажи проєкти','покажи мої проєкти','мої проєкти','список проєктів','покажи проекти'),
    'memory':('відкрий пам’ять','покажи пам’ять','покажи збережені факти','мої збережені факти'),
    'preferences':('покажи мої побажання','покажи налаштування відповідей'),
    'calendar':('відкрий календар','покажи календар','мій календар','покажи мої зустрічі'),
    'books':('відкрий бібліотеку','покажи книги','покажи мої книги','мої книги','моя бібліотека'),
    'photos':('відкрий фотоархів','покажи фотоархів','покажи мої фотографії','мої фотографії'),
    'notes':('покажи нотатки','мої нотатки','покажи голосові нотатки','покажи мої спогади'),
    'jobs':('покажи нагадування','мої нагадування','покажи розклад','мій розклад'),
    'report':('покажи звіт моніторингу','звіт моніторингу'),
    'overview':('покажи огляд дня','огляд дня','що в мене на сьогодні','покажи план дня'),
    'documents':('покажи документи','мої документи','покажи мої документи'),
    'connections':('покажи підключення','мої підключення','покажи інтеграції'),
    'health':('перевір справність','перевір свій стан'),
    'status':('покажи статус','твій статус'),
    'assistant_help':('покажи допомогу','як тобою користуватися','покажи команди','що ти вмієш'),
    'telegram_skills':('покажи вміння телеграм','покажи скіли телеграм'),
    'groups':('покажи групи розсилки','мої групи розсилки'),
    'watchlist':('покажи групи моніторингу','мої групи моніторингу'),
}
NUMBERS={'одну':1,'один':1,'одна':1,'дві':2,'два':2,'три':3,'чотири':4,"п'ять":5,'шість':6,'сім':7,'вісім':8,"дев'ять":9,'десять':10,'одинадцять':11,'дванадцять':12,'тринадцять':13,'чотирнадцять':14,"п'ятнадцять":15,'шістнадцять':16,'сімнадцять':17,'вісімнадцять':18,"дев'ятнадцять":19,'двадцять':20,'тридцять':30,'сорок':40,"п'ятдесят":50,'шістдесят':60,'сто':100}

def canonical(text):return re.sub(r'\s+',' ',text.casefold().replace('ʼ','’').replace("'",'’')).strip()

def cleaned(text):
    value=text.strip()
    # Strip only address/politeness at the boundary; never interpret quoted notes.
    for _ in range(3):
        value=re.sub(r'^(?:(?:міссі|місі|мессі|місія|катрусю|катруся)[,!:\s]+|будь ласка[,!:\s]+)','',value,flags=re.I).strip()
    value=re.sub(r'^(відкрий|покажи|додай|створи),?\s+будь ласка[, ]+',r'\1 ',value,flags=re.I)
    value=re.sub(r'^(?:чи\s+)?можеш\s+(?:мені\s+)?(?:будь ласка[, ]+)?','',value,flags=re.I)
    for old,new in [('показати','покажи'),('відкрити','відкрий'),('додати','додай'),('створити','створи')]:
        value=re.sub(r'^'+old+r'\b',new,value,flags=re.I)
    return re.sub(r'[,!\s]+будь ласка[.!?\s]*$','',value,flags=re.I).strip()

def number(value):
    value=value.casefold().replace('’',"'").replace('ʼ',"'").strip()
    if value.isdigit():return int(value)
    if value in NUMBERS:return NUMBERS[value]
    words=value.split()
    if len(words)==2 and NUMBERS.get(words[0],0) in (20,30,40,50,60) and 0<NUMBERS.get(words[1],0)<10:return NUMBERS[words[0]]+NUMBERS[words[1]]
    return None

def parse_intent(text,now=None):
    if not isinstance(text,str) or len(text)>5000:return None
    now=now or utcnow();value=cleaned(text);normalized=canonical(value).rstrip('.!?')
    # Raw slash commands are routed by aiogram for text. A transcript cannot use
    # them as an alternate path around the reviewed phrase allowlist.
    if value.startswith('/'):
        return Intent(hint='Голосом скажи звичайну фразу, наприклад «відкрий меню». Команди з підтвердженням надсилай текстом.') if normalized else None
    for command,phrases in READ_PHRASES.items():
        if normalized in phrases:return Intent(command)
    spoken=re.fullmatch(r'(?:слеш|slash)\s+(меню|menu|завдання|tasks|календар|calendar)',normalized)
    if spoken:return Intent({'меню':'menu','завдання':'tasks','календар':'calendar'}.get(spoken[1],spoken[1]))
    if normalized=='покажи всі завдання':return Intent('tasks','all')
    if normalized in ('зупини всі завдання','зупини всі розсилки та розклади'):return Intent('stop_all')
    match=re.fullmatch(r'скасуй (?:нагадування|завдання)\s+([a-f0-9]{12})[.!]?',value,re.I)
    if match:return Intent('cancel',match[1].lower())
    # Even a spoken "confirm" must not turn a transcript into public posting.
    if re.match(r'(?:підтверджую|підтверди|відправ|надішли|розішли|видали|зітри|віднови|підтвердити)\b',value,re.I):
        if re.search(r'розсил|оголош|груп|чернет|видал|віднов|підтвердж',value,re.I):
            return Intent(hint='Надсилання в групи, видалення й відновлення потребують точної текстової команди після перегляду. Перевір /jobs або /assistant_help. Голосове підтвердження не виконує ці дії.')
    creates=[(r'(?:додай|створи|запиши)\s+(?:нове\s+)?завдання\s*[:,—-]?\s+(.+)','task'),
        (r'(?:додай|створи)\s+(?:новий\s+)?про[єе]кт\s*[:,—-]?\s+(.+)','project'),
        (r'(?:додай|запиши)\s+факт(?:\s+у\s+пам[’\x27ʼ]?ять)?\s*[:,—-]?\s+(.+)','memory_add'),
        (r'(?:знайди|пошукай)\s+(?:мені\s+)?(?:книгу|книжку|epub)\s+(.+)','findbook'),
        (r'(?:зроби|виконай|проведи)\s+(?:дуже\s+)?(?:поглиблений|глибокий)\s+пошук\s*[:,—-]?\s+(.+)','deepsearch'),
        (r'поглиблений\s+пошук\s*[:,—-]?\s+(.+)','deepsearch'),
        (r'(?:знайди|пошукай)\s+в\s+(?:нашій\s+)?(?:історії|переписці)\s*[:,—-]?\s+(.+)','recall'),
        (r'підготуй\s+(?:чернетку\s+)?розсилк[уи]\s*[:,—-]?\s+(.+)','broadcast')]
    for pattern,command in creates:
        match=re.fullmatch(pattern,value,re.I|re.S)
        if match:
            body=match[1].strip()
            # Structured command separators are not inferred from speech.
            if not body or '|' in body or '\n/' in body:return Intent(hint='Не вдалося однозначно визначити параметри. /assistant_help')
            return Intent(command,body)
    remind=re.match(r'^нагадай\s+(?:мені\s+)?(.+)$',value,re.I|re.S)
    if remind:
        content=remind[1]
        relative=re.fullmatch(r'через\s+(.{1,35}?)\s+(хв(?:илин(?:у|и)?)?|год(?:ин(?:у|и)?)?|дн(?:і|ів)?|день)[, :—-]+(.+)',content,re.I|re.S)
        if relative:
            n=number(relative[1])
            if n is not None:return Intent('remind',f'через {n} {relative[2]} | {relative[3].strip()}')
        absolute=re.fullmatch(r'(сьогодні|завтра|післязавтра|\d{4}-\d{2}-\d{2})\s+(?:о\s+)?(\d{1,2})[:.](\d{2})[, :—-]+(.+)',content,re.I|re.S)
        if absolute:
            day=absolute[1].casefold()
            if day in ('сьогодні','завтра','післязавтра'):day=(now.astimezone(KYIV)+timedelta(days={'сьогодні':0,'завтра':1,'післязавтра':2}[day])).date().isoformat()
            return Intent('remind',f'{day} {int(absolute[2]):02}:{absolute[3]} | {absolute[4].strip()}')
        repeated=re.fullmatch(r'(щодня|кожного дня|по буднях|у будні|кожного понеділка|кожного вівторка|кожної середи|кожного четверга|кожної п’ятниці|кожної суботи|кожної неділі)\s+(?:о\s+)?(\d{1,2})[:.](\d{2})[, :—-]+(.+)',canonical(content),re.I|re.S)
        if repeated:
            days={'щодня':'щодня','кожного дня':'щодня','по буднях':'будні','у будні':'будні','кожного понеділка':'пн','кожного вівторка':'вт','кожної середи':'ср','кожного четверга':'чт','кожної п’ятниці':'пт','кожної суботи':'сб','кожної неділі':'нд'}
            # Slice original body by the matched time, preserving case and spelling.
            body=re.search(r'\d{1,2}[:.]\d{2}[, :—-]+(.+)',content,re.S)[1].strip()
            return Intent('remind_repeat',f'{days[repeated[1]]} {int(repeated[2]):02}:{repeated[3]} | {body}')
        return Intent(hint='Не зрозуміла час нагадування. Скажи «нагадай мені через п’ять хвилин перевірити пошту» або «нагадай мені завтра о 10:30 зателефонувати».')
    if re.match(r'^(?:додай|створи)\s+(?:подію|зустріч)\b',value,re.I):
        return Intent(hint='Для події потрібні дата, час і тривалість: /event 2026-11-05 12:00 | 60 | Зустріч. Голосом можна створити нагадування: «нагадай мені завтра о 12:00 про зустріч».')
    return None

class CommandMessage:
    def __init__(self,message,intent):
        self.original=message;self.text='/'+intent.command+(' '+intent.argument if intent.argument else '')
    def __getattr__(self,name):return getattr(self.original,name)

class NaturalCommands:
    def __init__(self,dp,access,logger):self.dp=dp;self.access=access;self.log=logger
    async def handle(self,message,text):
        if (getattr(message,'chat',None) and getattr(message.chat,'type',None)!='private') or not await self.access(message):return False
        # Normal typed slash commands retain their dedicated Telegram routing.
        if text.lstrip().startswith('/') and not getattr(message,'voice_input',False):return False
        intent=parse_intent(text)
        if intent is None:return False
        if intent.hint:await message.answer(intent.hint);return True
        if intent.command not in ALLOWED:raise ValueError('NATURAL_COMMAND_NOT_ALLOWED')
        target=None
        for handler in self.dp.message.handlers:
            if any(isinstance(f.callback,Command) and intent.command in f.callback.commands for f in handler.filters or []):target=handler;break
        if target is None:
            await message.answer('Ця можливість зараз недоступна. Перевір /assistant_help.');return True
        try:
            self.log('NATURAL_COMMAND',intent.command)  # No transcript/body/identity in logs.
            await target.call(CommandMessage(message,intent))
        except Exception as exc:
            self.log('NATURAL_COMMAND_ERROR',intent.command+' '+type(exc).__name__)
            await message.answer('Не вдалося підтвердити виконання команди. Перевір відповідний список перед повтором; не вважай дію завершеною.')
        return True
