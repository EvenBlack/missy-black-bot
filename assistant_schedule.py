"""Explicit Kyiv recurrence rules and portable personal calendar files."""
import re
from datetime import datetime,timedelta,timezone
from automation_engine import KYIV,AutomationError,utcnow,iso,parsed

DAYS={'пн':0,'вт':1,'ср':2,'чт':3,'пт':4,'сб':5,'нд':6}

def next_rule(rule,now=None):
    now=now or utcnow();local=now.astimezone(KYIV)
    if not isinstance(rule,dict):raise AutomationError('RULE_FORMAT')
    days=rule.get('days');clock=rule.get('time','')
    if not isinstance(days,list) or not days or any(type(d) is not int or d not in range(7) for d in days):raise AutomationError('RULE_FORMAT')
    if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',clock):raise AutomationError('TIME_FORMAT')
    h,m=map(int,clock.split(':'))
    for offset in range(9):
        day=(local+timedelta(days=offset)).date()
        if day.weekday() not in days:continue
        naive=datetime.combine(day,datetime.min.time()).replace(hour=h,minute=m)
        candidate=naive.replace(tzinfo=KYIV,fold=0)
        if candidate.astimezone(timezone.utc).astimezone(KYIV).replace(tzinfo=None)!=naive:continue
        if candidate>now:return candidate.astimezone(timezone.utc)
    raise AutomationError('RULE_FORMAT')

def parse_rule(text):
    parts=text.strip().lower().split()
    if len(parts)!=2:raise AutomationError('RULE_FORMAT')
    label,clock=parts
    if label=='будні':days=list(range(5))
    elif label=='щодня':days=list(range(7))
    else:
        try:days=sorted({DAYS[x] for x in label.split(',')})
        except KeyError:raise AutomationError('RULE_FORMAT') from None
    rule={'days':days,'time':clock};next_rule(rule);return rule

def ical_escape(text):
    return str(text).replace('\\','\\\\').replace('\n','\\n').replace(';','\\;').replace(',','\\,').replace('\r','')

def fold_ical(line):
    out=[];part='';size=0
    for char in line:
        n=len(char.encode('utf-8'))
        if size+n>73:out.append(part);part=' ';size=1
        part+=char;size+=n
    return '\r\n'.join(out+[part])

def calendar_export(events):
    lines=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//Missy//Personal calendar//UK','CALSCALE:GREGORIAN']
    for e in events:
        lines+=['BEGIN:VEVENT','UID:'+ical_escape(e['id'])+'@missy.local','DTSTAMP:'+utcnow().strftime('%Y%m%dT%H%M%SZ'),
            'DTSTART:'+parsed(e['start']).strftime('%Y%m%dT%H%M%SZ'),'DTEND:'+parsed(e['end']).strftime('%Y%m%dT%H%M%SZ'),
            'SUMMARY:'+ical_escape(e['title']),'END:VEVENT']
    lines+=['END:VCALENDAR']
    return ('\r\n'.join(fold_ical(line) for line in lines)+'\r\n').encode('utf-8')
