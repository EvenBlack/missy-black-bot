"""Bounded document extraction in a killable subprocess; no model or network."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET

MAX_BYTES=8*1024*1024
MAX_TEXT=300000
class DocumentError(ValueError):pass


def extract_local(path,name):
    ext=name.lower().rsplit('.',1)[-1];chapters=[];warnings=[]
    if ext=='docx':
        with zipfile.ZipFile(path) as archive:
            info=archive.infolist()
            if len(info)>3000 or sum(i.file_size for i in info)>30*1024*1024:raise DocumentError('DOC_EXPANSION_LIMIT')
            raw=archive.read('word/document.xml')
            if b'<!ENTITY' in raw.upper() or b'<!DOCTYPE' in raw.upper():raise DocumentError('DOC_XML')
            root=ET.fromstring(raw);ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            paragraphs=[''.join(t.text or '' for t in p.findall('.//w:t',ns)) for p in root.findall('.//w:p',ns)]
            text='\n'.join(paragraphs)
            if len(text)>MAX_TEXT:raise DocumentError('DOC_TEXT_LIMIT')
            chapters=[('Документ',text)]
        warnings.append('Прочитано основний текст і таблиці; зображення, колонтитули та примітки не включені.')
    elif ext=='pdf':
        # Poppler subprocess avoids an unbounded Python PDF object graph.
        details=subprocess.run(['pdfinfo',str(path)],capture_output=True,timeout=15,check=True).stdout.decode(errors='replace')
        import re
        match=re.search(r'^Pages:\s+(\d+)',details,re.M)
        count=int(match.group(1)) if match else 0
        if not count or count>80:raise DocumentError('DOC_PAGE_LIMIT')
        if re.search(r'^Encrypted:\s+yes',details,re.M):raise DocumentError('DOC_ENCRYPTED')
        ocr=0
        for page in range(1,count+1):
            output=Path(str(path)+'.text')
            subprocess.run(['pdftotext','-f',str(page),'-l',str(page),'-enc','UTF-8',str(path),str(output)],capture_output=True,check=True,timeout=15)
            text=output.read_text('utf-8').strip()
            if len(text)<20 and ocr<5:
                ocr+=1;prefix=str(path)+'.page'
                subprocess.run(['pdftoppm','-f',str(page),'-l',str(page),'-singlefile','-scale-to','1600','-png',str(path),prefix],capture_output=True,check=True,timeout=20)
                text=subprocess.run(['tesseract',prefix+'.png','stdout','-l','ukr+eng'],capture_output=True,check=True,timeout=30,env={**os.environ,'OMP_THREAD_LIMIT':'1'}).stdout.decode('utf-8').strip()
                Path(prefix+'.png').unlink(missing_ok=True)
            if text:chapters.append((f'Сторінка {page}',text))
            else:warnings.append(f'Сторінку {page} не розпізнано.')
            if sum(len(t) for _,t in chapters)>MAX_TEXT:raise DocumentError('DOC_TEXT_LIMIT')
        if ocr:warnings.append('Застосовано OCR: можливі помилки розпізнавання. OCR обмежено 5 сторінками на файл.')
    elif ext in ('png','jpg','jpeg','webp'):
        from PIL import Image
        Image.MAX_IMAGE_PIXELS=16000000
        with Image.open(path) as image:
            image.thumbnail((2400,2400));image.convert('RGB').save(str(path)+'.png')
        text=subprocess.run(['tesseract',str(path)+'.png','stdout','-l','ukr+eng'],capture_output=True,check=True,timeout=40,env={**os.environ,'OMP_THREAD_LIMIT':'1'}).stdout.decode('utf-8').strip()
        chapters=[('Розпізнаний текст',text)];warnings.append('OCR може помилятися; перевіряй важливі реквізити за оригіналом.')
    else:raise DocumentError('DOC_FORMAT')
    if not any(t.strip() for _,t in chapters):raise DocumentError('DOC_NO_TEXT')
    if sum(len(t) for _,t in chapters)>MAX_TEXT:raise DocumentError('DOC_TEXT_LIMIT')
    return {'chapters':chapters,'warnings':warnings}

_slots=asyncio.Semaphore(1)
async def extract_document(data,name):
    if not data or len(data)>MAX_BYTES:raise DocumentError('DOC_SIZE')
    async with _slots:
        with tempfile.TemporaryDirectory(prefix='missy_doc_') as folder:
            source=Path(folder)/'input';source.write_bytes(data)
            process=await asyncio.create_subprocess_exec(sys.executable,str(Path(__file__).resolve()),str(source),name,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,start_new_session=True)
            try:out,_=await asyncio.wait_for(process.communicate(),timeout=150)
            except (asyncio.TimeoutError,asyncio.CancelledError):
                import signal
                os.killpg(process.pid,signal.SIGKILL);await process.wait();raise
            if process.returncode:raise DocumentError('DOC_EXTRACTION_FAILED')
            if len(out)>3*1024*1024:raise DocumentError('DOC_TEXT_LIMIT')
            result=json.loads(out)
            if result.get('error'):raise DocumentError(result['error'])
            return result

if __name__=='__main__':
    # Limit parser memory and CPU on the Linux host. Parent kills wall-time overruns.
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS,(240*1024*1024,240*1024*1024))
        resource.setrlimit(resource.RLIMIT_CPU,(110,115))
        result=extract_local(Path(sys.argv[1]),sys.argv[2])
    except Exception as exc:result={'error':str(exc) if isinstance(exc,DocumentError) else 'DOC_EXTRACTION_FAILED'}
    print(json.dumps(result,ensure_ascii=False))
