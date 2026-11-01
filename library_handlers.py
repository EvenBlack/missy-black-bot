"""Telegram commands for the personal book library. Registered before generic documents."""
import base64
import hashlib
import re
from datetime import datetime, timezone
from io import BytesIO
from aiogram import F, types
from aiogram.filters import Command
from aiogram.types import BufferedInputFile
from book_library import import_book, catalog, download_catalog_book, BookError, MAX_BOOK_BYTES
from drive_memory import MemoryUnavailable


def register_library(dp, get_store, access, change_memory, send_long, logger):
    async def activate(uid,book_id):
        def update(memory): memory.setdefault('user_books',{})[str(uid)]={'active':book_id}
        await change_memory(update)

    async def ingest(message,data,filename,source='Telegram'):
        store=get_store()
        if not store: raise MemoryUnavailable('BOOK_STORAGE_REQUIRED')
        book_id,book,count=await import_book(store,message.from_user.id,data,filename,message.date.isoformat(),source)
        try:
            await activate(message.from_user.id,book_id)
            active='Книгу вибрано для обговорення.'
        except Exception:
            active=f'Книга збережена, але вибрати її автоматично не вдалося. Обери: /book {book_id}'
        await message.answer(f'Книга збережена й текст проіндексований: {book.title}\nАвтор: {book.author or "не вказаний"}\nФрагментів: {count}. {active}\nМожеш запитувати про зміст. Відповіді спиратимуться на знайдені уривки, з номерами фрагментів.\nОригінал: /readbook {book_id}\nБібліотека: /books\nВийти з обговорення: /book off')
        logger('BOOK_INDEXED','Original and searchable text acknowledged')

    async def fail(message,exc):
        logger('BOOK_ERROR',str(exc) if isinstance(exc,(BookError,MemoryUnavailable)) else type(exc).__name__)
        reason={
            'BOOK_SIZE':'Ліміт книги — 8 МіБ.', 'TXT_UTF8_REQUIRED':'TXT має бути в кодуванні UTF-8.',
            'ENCRYPTED_EPUB':'Зашифровані EPUB не підтримуються.',
            'BOOK_TEXT_TOO_LARGE':'Для цього імпорту ліміт тексту — 3 мільйони символів. Розділи книгу на томи.',
            'BOOK_HAS_NO_TEXT':'Не знайдено придатного тексту.',
        }.get(str(exc),'Не вдалося завершити операцію з книгою. Спробуй пізніше або надішли інший EPUB/TXT.')
        await message.answer(reason+' Завершення операції не підтверджено.')

    @dp.message(Command('books'))
    async def books(message:types.Message):
        if not await access(message): return
        try:
            result=await get_store().request('list_books',owner=str(message.from_user.id),query=(message.text or '').partition(' ')[2][:150])
            lines=[f"{b['title']} — {b.get('author') or 'Автор не вказаний'}\n/book {b['book_id']}" for b in result.get('books',[]) if re.fullmatch('[a-f0-9]{24}',str(b.get('book_id','')))]
            if result.get('has_more'): lines.append('Є ще книги. Уточни: /books назва')
            if result.get('unreadable_count'): lines.append('Деякі записи не прочитано; перелік неповний.')
            await send_long(message,'\n\n'.join(lines) or 'Твоя бібліотека порожня за цим запитом. Надішли EPUB або TXT як файл.')
        except Exception as exc: await fail(message,exc)

    @dp.message(Command('book'))
    async def book(message:types.Message):
        if not await access(message): return
        arg=(message.text or '').partition(' ')[2].strip()
        if arg=='off':
            try:
                await activate(message.from_user.id,None)
                await message.answer('Обговорення книги завершено. Повернутися: /books')
            except Exception as exc: await fail(message,exc)
            return
        if not re.fullmatch('[a-f0-9]{24}',arg):
            await message.answer('Обери книгу через /books. Команда /book off завершує обговорення.');return
        try:
            result=await get_store().request('book_context',owner=str(message.from_user.id),book_id=arg,query='')
            await activate(message.from_user.id,arg)
            await message.answer(f"Обрано: {result['title']}. Запитуй про книгу. Для перегляду фрагмента: /passage 1. Вийти: /book off")
        except Exception as exc: await fail(message,exc)

    @dp.message(Command('readbook'))
    async def readbook(message:types.Message):
        if not await access(message): return
        arg=(message.text or '').partition(' ')[2].strip()
        if not re.fullmatch('[a-f0-9]{24}',arg): await message.answer('Обери код книги через /books.');return
        try:
            result=await get_store().request('get_book',owner=str(message.from_user.id),book_id=arg)
            data=base64.b64decode(result['content_b64'],validate=True)
            digest=hashlib.sha256(data).hexdigest()
            if len(data)>MAX_BOOK_BYTES or digest!=result['sha256'] or digest[:24]!=arg: raise MemoryUnavailable('BOOK_CHECKSUM')
            name=str(result.get('filename','book.epub')).replace('\\','/').split('/')[-1][:100]
            await message.answer_document(BufferedInputFile(data,filename=name),caption='Оригінал книги з твоєї бібліотеки Google Drive.')
        except Exception as exc: await fail(message,exc)

    @dp.message(Command('findbook'))
    async def findbook(message:types.Message):
        if not await access(message): return
        query=(message.text or '').partition(' ')[2].strip()
        await search_catalog(message,query)

    async def search_catalog(message,query):
        if not query: await message.answer('Напиши /findbook назва або автор. Пошук у відкритому каталозі Project Gutenberg.');return
        try:
            results=await catalog(query=query)
            lines=[]
            for b in results:
                authors=', '.join(a.get('name','') for a in b.get('authors',[]))
                lines.append(f"{b['title']} — {authors}\nhttps://www.gutenberg.org/ebooks/{b['id']}\n/getbook {b['id']}")
            await send_long(message,'\n\n'.join(lines) or 'У відкритому каталозі EPUB за цим запитом не знайдено. Можеш завантажити власний файл книги.')
        except Exception as exc: await fail(message,exc)

    @dp.message(Command('getbook'))
    async def getbook(message:types.Message):
        if not await access(message): return
        arg=(message.text or '').partition(' ')[2].strip()
        if not re.fullmatch('[0-9]{1,8}',arg): await message.answer('Обери книгу через /findbook.');return
        try:
            results=await catalog(book_id=int(arg))
            if not results: raise BookError('NO_OPEN_EPUB')
            await message.answer('Завантажую EPUB з каталогу й додаю до твоєї бібліотеки…')
            data,url=await download_catalog_book(results[0])
            await ingest(message,data,'gutenberg_'+arg+'.epub',url)
            await message.answer_document(BufferedInputFile(data,filename='gutenberg_'+arg+'.epub'),caption='EPUB із Project Gutenberg. Дані про права та джерело — на сторінці книги в каталозі.')
        except Exception as exc: await fail(message,exc)

    @dp.message(F.document.file_name.regexp(r'(?i).*\.(epub|txt)$'))
    async def document(message:types.Message):
        if not await access(message): return
        if (message.document.file_size or 0)>MAX_BOOK_BYTES: await fail(message,BookError('BOOK_SIZE'));return
        try:
            await message.answer('Зберігаю книгу та індексую її текст…')
            buffer=BytesIO();await message.bot.download(message.document,destination=buffer,timeout=50)
            await ingest(message,buffer.getvalue(),message.document.file_name)
        except Exception as exc: await fail(message,exc)

    return search_catalog
