"""Bounded EPUB/TXT ingestion, searchable chunks and public-domain catalog access."""
import asyncio
import base64
import hashlib
import json
import posixpath
import re
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from urllib.parse import urlparse, unquote
import xml.etree.ElementTree as ET
import aiohttp
from drive_memory import MemoryUnavailable

MAX_BOOK_BYTES = 8*1024*1024
MAX_BOOK_TEXT = 3000000
PARSER_VERSION = 1


class BookError(ValueError):
    pass


@dataclass
class Book:
    title: str
    author: str
    ext: str
    chapters: list

    def chunks(self):
        chunks=[]
        for title,text in self.chapters:
            # Deterministic overlapping windows support retrieval without losing boundaries.
            for start in range(0,len(text),4500):
                chunks.append({'chapter':title[:200], 'text':text[start:start+5000]})
        return chunks


class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts=[]; self.hidden=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style'): self.hidden+=1
        if tag in ('p','div','br','h1','h2','h3','li','section'): self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ('script','style') and self.hidden: self.hidden-=1
        if tag in ('p','div','h1','h2','h3','li','section'): self.parts.append('\n')
    def handle_data(self,data):
        if not self.hidden: self.parts.append(data)
    def text(self):
        return re.sub(r'\n\s*\n+', '\n\n', ''.join(self.parts)).strip()


def xml(data):
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        # EPUB XHTML may have a harmless HTML DOCTYPE; remove the declaration,
        # never expand internal/external entities in XML package metadata.
        raise BookError('UNSUPPORTED_XML_DECLARATION')
    try: return ET.fromstring(data)
    except ET.ParseError: raise BookError('INVALID_EPUB_XML') from None


def parse_book(data,filename):
    if not data or len(data)>MAX_BOOK_BYTES: raise BookError('BOOK_SIZE')
    ext=filename.lower().rsplit('.',1)[-1]
    if ext=='txt':
        try: text=data.decode('utf-8-sig')
        except UnicodeDecodeError: raise BookError('TXT_UTF8_REQUIRED') from None
        book=Book(filename.rsplit('.',1)[0], '', 'txt', [('Текст',text.strip())])
    elif ext=='epub':
        try:
            with zipfile.ZipFile(BytesIO(data)) as z:
                infos=z.infolist()
                if len(infos)>5000 or len({i.filename for i in infos})!=len(infos) or sum(i.file_size for i in infos)>50*1024*1024:
                    raise BookError('EPUB_EXPANSION_LIMIT')
                if any(i.flag_bits&1 or i.file_size>10*1024*1024 for i in infos): raise BookError('ENCRYPTED_OR_LARGE_EPUB')
                if 'META-INF/encryption.xml' in z.namelist(): raise BookError('ENCRYPTED_EPUB')
                container=xml(z.read('META-INF/container.xml'))
                rootfile=container.find('.//{*}rootfile')
                if rootfile is None: raise BookError('INVALID_EPUB_PACKAGE')
                opf=rootfile.attrib.get('full-path','')
                package=xml(z.read(opf))
                title=package.find('.//{*}metadata/{*}title');author=package.find('.//{*}metadata/{*}creator')
                manifest={item.attrib['id']:item for item in package.findall('.//{*}manifest/{*}item') if 'id' in item.attrib}
                chapters=[]; total=0
                for ref in package.findall('.//{*}spine/{*}itemref'):
                    item=manifest.get(ref.attrib.get('idref'))
                    if item is None or item.attrib.get('media-type') not in ('application/xhtml+xml','text/html'): continue
                    href=unquote(item.attrib.get('href','').split('#')[0])
                    if urlparse(href).scheme or href.startswith('/'): raise BookError('INVALID_EPUB_PATH')
                    path=posixpath.normpath(posixpath.join(posixpath.dirname(opf),href))
                    if path.startswith('../'): raise BookError('INVALID_EPUB_PATH')
                    raw=z.read(path).decode('utf-8-sig')
                    html=PlainHTML();html.feed(raw);text=html.text()
                    if not text: continue
                    total+=len(text)
                    if total>MAX_BOOK_TEXT: raise BookError('BOOK_TEXT_TOO_LARGE')
                    chapter_title=next((line.strip() for line in text.splitlines() if line.strip()),'Розділ')[:200]
                    chapters.append((chapter_title,text))
                book=Book(''.join(title.itertext()) if title is not None else filename,
                          ''.join(author.itertext()) if author is not None else '', 'epub',chapters)
        except BookError: raise
        except (zipfile.BadZipFile,KeyError,UnicodeDecodeError,OSError,RuntimeError): raise BookError('INVALID_EPUB') from None
    else: raise BookError('SUPPORTED_EPUB_TXT')
    if not book.chapters or not any(t.strip() for _,t in book.chapters): raise BookError('BOOK_HAS_NO_TEXT')
    if sum(len(t) for _,t in book.chapters)>MAX_BOOK_TEXT: raise BookError('BOOK_TEXT_TOO_LARGE')
    return book


async def import_book(store,owner,data,filename,created,source='Telegram'):
    book=await asyncio.to_thread(parse_book,data,filename)
    digest=hashlib.sha256(data).hexdigest();book_id=digest[:24]
    result=await store.request('store_book',owner=str(owner),book_id=book_id,sha256=digest,
        content_b64=base64.b64encode(data).decode('ascii'),ext=book.ext,
        title=book.title[:300],author=book.author[:300],filename=filename[:100],created=created,source=source[:500])
    if result.get('sha256')!=digest or result.get('size')!=len(data): raise MemoryUnavailable('INVALID_BOOK_UPLOAD_ACK')
    chunks=book.chunks()
    # One index write, independent of original binary size; no model calls on import.
    index=json.dumps({'version':PARSER_VERSION,'chunks':chunks},ensure_ascii=False,separators=(',',':'))
    index_digest=hashlib.sha256(index.encode()).hexdigest()
    result=await store.request('index_book',owner=str(owner),book_id=book_id,index_json=index,index_digest=index_digest)
    if result.get('book_id')!=book_id or result.get('index_digest')!=index_digest or result.get('chunks')!=len(chunks): raise MemoryUnavailable('INVALID_BOOK_INDEX_ACK')
    return book_id,book,len(chunks)


class BookContextCache:
    """Short local cache scoped by owner, book hash and query; original index is durable."""
    def __init__(self): self.values={}
    async def get(self,store,owner,book_id,query):
        import time
        key=(str(owner),book_id,query[:200])
        cached=self.values.get(key)
        if cached and cached[0]>time.monotonic(): return cached[1]
        if re.search(r'на початку|початок|перш(?:их|і|ий) (?:розділ|сторін)',query,re.I):
            pieces=await asyncio.gather(*(store.request('book_context',owner=str(owner),book_id=book_id,query='',chunk_number=n) for n in range(1,5)))
            result=dict(pieces[0])
            result['selection']='beginning_fragments'
            all_chunks=[chunk for piece in pieces for chunk in piece.get('chunks',[])]
            result['chunks']=[chunk for chunk in all_chunks if len(chunk.get('text',''))>150] or all_chunks
        else:
            result=await store.request('book_context',owner=str(owner),book_id=book_id,query=query[:200])
        self.values[key]=(time.monotonic()+300,result)
        if len(self.values)>64: self.values.pop(next(iter(self.values)))
        return result


async def catalog(query=None,book_id=None):
    url='https://gutendex.com/books/' + (str(book_id)+'/' if book_id is not None else '')
    params={'search':query[:150],'copyright':'false','mime_type':'application/epub+zip'} if query else None
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25,connect=8)) as session:
        async with session.get(url,params=params) as response:
            if response.status!=200: raise BookError('CATALOG_UNAVAILABLE')
            if response.content_length and response.content_length>2000000: raise BookError('CATALOG_RESPONSE_SIZE')
            body=await response.content.read(2000001)
            if len(body)>2000000: raise BookError('CATALOG_RESPONSE_SIZE')
            payload=json.loads(body)
    books=payload.get('results',[]) if book_id is None else [payload]
    return [b for b in books if b.get('copyright') is False and isinstance(b.get('id'),int) and b.get('formats',{}).get('application/epub+zip')][:5]


def trusted_book_url(url):
    p=urlparse(url)
    return p.scheme=='https' and p.hostname in {'www.gutenberg.org','gutenberg.org'} and not p.username and not p.password and p.port in (None,443)


async def download_catalog_book(book):
    url=book['formats']['application/epub+zip']
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60,connect=8)) as session:
        for _ in range(5):
            if not trusted_book_url(url): raise BookError('UNTRUSTED_BOOK_SOURCE')
            async with session.get(url,allow_redirects=False) as response:
                if response.status in (301,302,303,307,308):
                    from urllib.parse import urljoin
                    url=urljoin(url,response.headers.get('Location',''));continue
                if response.status!=200: raise BookError('BOOK_DOWNLOAD_UNAVAILABLE')
                parts=[];size=0
                async for part in response.content.iter_chunked(65536):
                    size+=len(part)
                    if size>MAX_BOOK_BYTES: raise BookError('BOOK_SIZE')
                    parts.append(part)
                return b''.join(parts),url
    raise BookError('BOOK_REDIRECT_LIMIT')
