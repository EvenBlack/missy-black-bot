"""Conservative no-dependency prompt budget for the account's 8k TPM ceiling."""
import json

INPUT_BYTES = 6000
OUTPUT_TOKENS = 1024


def clip_bytes(text, limit):
    return str(text).encode('utf-8')[:max(0,limit)].decode('utf-8',errors='ignore')


def bounded_prompt(base, current, sections, history):
    # Byte-level BPE cannot produce more text tokens than UTF-8 bytes. Reserve
    # 512 tokens for framing and 1024 output: 6000+512+1024 remains below 8000.
    base=clip_bytes(base,1600)
    original_current=str(current)
    current=clip_bytes(original_current,1760)
    if len(current.encode())<len(original_current.encode()):
        current+=' [Запит скорочено]'
    system=base
    remaining=INPUT_BYTES-len(base.encode())-len(current.encode())
    for label,value,cap in sections:
        if not value or remaining<80:continue
        prefix='\n'+label+':\n'
        allowance=min(cap,remaining-len(prefix.encode())-35)
        if allowance<40:continue
        text=clip_bytes(value,allowance)
        if len(text.encode())<len(str(value).encode()):text+='\n[Уривок контексту]'
        addition=prefix+text
        system+=addition;remaining-=len(addition.encode())
    previous=[]
    for item in reversed(history[:-1]):
        if item.get('role') not in ('user','assistant') or remaining<100:continue
        text=clip_bytes(item.get('content',''),min(400,remaining))
        if not text:continue
        previous.append({'role':item['role'],'content':text});remaining-=len(text.encode())
        if len(previous)>=4:break
    result=[{'role':'system','content':system}]+list(reversed(previous))+[{'role':'user','content':current}]
    assert sum(len(m['content'].encode()) for m in result)<=INPUT_BYTES
    return result


def format_book_context(result):
    lines=[f"Книга: {result.get('title','')}; добір: {result.get('selection','')}; фрагментів у книзі: {result.get('total_chunks',0)}"]
    for chunk in result.get('chunks',[]):
        lines.append(f"Фрагмент {chunk.get('number')}: {chunk.get('chapter','')}\n{chunk.get('text','')}")
    return '\n\n'.join(lines)


def model_error(exc):
    code=getattr(exc,'status_code',None)
    if code==413:return 'Контекст завеликий для поточного ліміту моделі. Уточни запит або обери окремий фрагмент: /passage номер.'
    if code==429:return 'Досягнуто ліміту запитів або токенів Groq. Спробуй трохи пізніше; книга та історія залишаються в Google Drive.'
    return "Зараз не можу зв'язатися з мовною моделлю. Спробуй трохи пізніше."
