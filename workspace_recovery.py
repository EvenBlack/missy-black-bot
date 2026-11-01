"""Validate inert workspace restores. Delivery jobs are deliberately never replayed."""
import re
from automation_engine import AutomationError,parsed

SECTIONS={'tasks':150,'projects':40,'facts':60,'calendar':200}

def validate_restore(data,owner):
    if not isinstance(data,dict) or data.get('version')!=1 or data.get('owner')!=str(owner):raise AutomationError('RESTORE_OWNER')
    result={}
    for section,limit in SECTIONS.items():
        items=data.get(section,{})
        if not isinstance(items,dict) or len(items)>limit:raise AutomationError('RESTORE_FORMAT')
        clean={}
        for code,item in items.items():
            if not isinstance(code,str) or not re.fullmatch('[a-f0-9]{12}',code) or not isinstance(item,dict):raise AutomationError('RESTORE_FORMAT')
            if section=='tasks':
                if item.get('status') not in ('open','done') or item.get('priority') not in ('high','normal','low'):raise AutomationError('RESTORE_FORMAT')
                text=item.get('text');project=item.get('project')
                if not isinstance(text,str) or not 1<=len(text)<=1000 or (project is not None and not re.fullmatch('[a-f0-9]{12}',str(project))):raise AutomationError('RESTORE_FORMAT')
                due=item.get('due')
                if due:parsed(due)
                clean[code]={'id':code,'text':text,'project':project,'priority':item['priority'],'due':due,'status':item['status'],'created':item['created']}
            elif section=='projects':
                if not isinstance(item.get('name'),str) or not 1<=len(item['name'])<=100 or item.get('status') not in ('active','archived') or not isinstance(item.get('description'),str) or len(item['description'])>500:raise AutomationError('RESTORE_FORMAT')
                clean[code]={'id':code,'name':item['name'],'description':item['description'],'status':item['status'],'created':item['created']}
            elif section=='facts':
                if not isinstance(item.get('text'),str) or not 1<=len(item['text'])<=1000:raise AutomationError('RESTORE_FORMAT')
                clean[code]={'text':item['text'],'source':'owner_confirmed_restore','created':item['created']}
            else:
                if not isinstance(item.get('title'),str) or not 1<=len(item['title'])<=300 or parsed(item['end'])<=parsed(item['start']):raise AutomationError('RESTORE_FORMAT')
                clean[code]={'id':code,'title':item['title'],'start':item['start'],'end':item['end'],'created':item['created'],'source':'owner_confirmed_restore'}
            parsed(clean[code]['created'])
        result[section]=clean
    for task in result['tasks'].values():
        if task['project'] and task['project'] not in result['projects']:task['project']=None
    return result
