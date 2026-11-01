"""Bounded lexical expansion and multi-page recall, without paid embeddings."""
import re
from dialogue_archive import search_terms

GROUPS=[('подарунок','подар','дарував','купив','чашк'),('робота','робот','прац','ваканс'),('зустріч','зустр','домов','побач'),('спогад','згад','колись'),('фото','фотограф','знімок'),('кошти','грош','оплат','бюджет')]

def expanded_query(query):
    words=search_terms(query)
    for group in GROUPS:
        if any(any(w.startswith(g) or g.startswith(w) for g in group) for w in words):words.extend(group)
    return ' '.join(dict.fromkeys(words))[:200]

async def retrieve_history(transport,owner,query,exclude=None,max_pages=3):
    cursor=0;records={};unreadable=0;more=None;expanded=expanded_query(query)
    for _ in range(max_pages):
        result=await transport.request('search_history',owner=str(owner),query=expanded,cursor=cursor)
        unreadable+=result.get('unreadable_count',0)
        for record in result.get('messages',[]):
            if record.get('id')!=exclude:
                old=records.get(record['id'])
                if old is None or record.get('score',0)>old.get('score',0):records[record['id']]=record
        more=result.get('next_cursor')
        if more is None:break
        cursor=more
    values=sorted(records.values(),key=lambda r:(r.get('score',0),r.get('created','')),reverse=True)[:15]
    return {'messages':values,'next_cursor':more,'unreadable_count':unreadable,'expanded_query':expanded}
