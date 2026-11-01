"""Public HTTP resource monitoring without credentials, JS execution or local network access."""
import asyncio
import hashlib
import ipaddress
import re
import socket
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import urlparse,urljoin
import aiohttp
from aiohttp.abc import AbstractResolver


class SiteError(ValueError):pass


def validate_url(url):
    p=urlparse(url)
    if p.scheme!='https' or not p.hostname or p.username or p.password or p.port not in (None,443):raise SiteError('PUBLIC_HTTPS_REQUIRED')
    if p.hostname.lower() in ('localhost','metadata.google.internal') or p.hostname.lower().endswith(('.local','.internal')):raise SiteError('PRIVATE_ADDRESS')
    try:
        address=ipaddress.ip_address(p.hostname)
        if not address.is_global:raise SiteError('PRIVATE_ADDRESS')
    except ValueError as exc:
        if isinstance(exc,SiteError):raise
    return url


class PublicResolver(AbstractResolver):
    async def resolve(self,host,port=0,family=socket.AF_INET):
        records=await asyncio.get_running_loop().getaddrinfo(host,port,family=family,type=socket.SOCK_STREAM)
        if not records:raise SiteError('DNS_EMPTY')
        output=[]
        for fam,_,proto,_,address in records:
            if not ipaddress.ip_address(address[0]).is_global:raise SiteError('PRIVATE_ADDRESS')
            output.append({'hostname':host,'host':address[0],'port':port,'family':fam,'proto':proto,'flags':socket.AI_NUMERICHOST})
        return output
    async def close(self):pass


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.hidden=0;self.parts=[];self.title=[];self.in_title=False
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','nav','footer','noscript','svg'):self.hidden+=1
        if tag=='title':self.in_title=True
        if tag in ('p','div','br','h1','h2','h3','li','article','section'):self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ('script','style','nav','footer','noscript','svg') and self.hidden:self.hidden-=1
        if tag=='title':self.in_title=False
        if tag in ('p','div','h1','h2','h3','li','article','section'):self.parts.append('\n')
    def handle_data(self,data):
        if self.in_title:self.title.append(data)
        elif not self.hidden:self.parts.append(data)


def snapshot_resource(raw,content_type,url):
    if b'<!ENTITY' in raw.upper():raise SiteError('UNSUPPORTED_XML')
    text=raw.decode('utf-8',errors='replace');title=urlparse(url).hostname;lines=[]
    if 'xml' in content_type or text.lstrip().startswith(('<?xml','<rss','<feed')):
        if b'<!DOCTYPE' in raw.upper():raise SiteError('UNSUPPORTED_XML')
        try:root=ET.fromstring(raw)
        except ET.ParseError:raise SiteError('INVALID_FEED') from None
        title=root.findtext('.//channel/title') or root.findtext('{*}title') or title
        items=root.findall('.//item') or root.findall('{*}entry')
        for item in items[:40]:
            name=item.findtext('title') or item.findtext('{*}title') or ''
            link=item.findtext('link')
            if not link:
                el=item.find('{*}link');link=el.attrib.get('href','') if el is not None else ''
            lines.append(re.sub(r'\s+',' ',name).strip()+' '+urljoin(url,link or ''))
    elif 'html' in content_type or '<html' in text[:1000].lower():
        parser=VisibleText();parser.feed(text)
        title=' '.join(parser.title).strip() or title
        lines=[re.sub(r'\s+',' ',line).strip() for line in ''.join(parser.parts).splitlines()]
    elif content_type.startswith('text/') or 'json' in content_type:
        lines=[re.sub(r'\s+',' ',line).strip() for line in text.splitlines()]
    else:raise SiteError('UNSUPPORTED_CONTENT')
    lines=list(dict.fromkeys(line for line in lines if line))
    if not lines:raise SiteError('NO_VISIBLE_CONTENT')
    normalized='\n'.join(lines)
    return {'title':title[:200],'hash':hashlib.sha256(normalized.encode()).hexdigest(),'lines':lines[:80], 'truncated':len(lines)>80}


async def fetch_resource(url):
    validate_url(url)
    connector=aiohttp.TCPConnector(resolver=PublicResolver(),ttl_dns_cache=0)
    async with aiohttp.ClientSession(connector=connector,timeout=aiohttp.ClientTimeout(total=25,connect=8),headers={'User-Agent':'MissyResourceMonitor/1.0'}) as session:
        for _ in range(5):
            validate_url(url)
            async with session.get(url,allow_redirects=False) as response:
                if response.status in (301,302,303,307,308):
                    url=urljoin(url,response.headers.get('Location',''));continue
                if response.status!=200:raise SiteError('SITE_HTTP_'+str(response.status))
                parts=[];size=0
                async for part in response.content.iter_chunked(65536):
                    size+=len(part)
                    if size>2*1024*1024:raise SiteError('RESPONSE_TOO_LARGE')
                    parts.append(part)
                result=snapshot_resource(b''.join(parts),response.headers.get('Content-Type','').lower(),url)
                # Bound retained text independently of HTTP size; full normalized hash remains.
                kept=[];chars=0
                for line in result['lines']:
                    if chars+len(line)>6000:result['truncated']=True;break
                    kept.append(line);chars+=len(line)
                result['lines']=kept;result['url']=url
                return result
    raise SiteError('REDIRECT_LIMIT')


def resource_delta(previous,current,keywords):
    if not previous:return {'changed':False,'matches':False,'summary':'Початковий стан збережено; наступні перевірки шукатимуть зміни.'}
    if previous.get('hash')==current['hash']:return {'changed':False,'matches':False,'summary':'Змін у доступному тексті не виявлено.'}
    old=set(previous.get('lines',[]));new=[line for line in current.get('lines',[]) if line not in old]
    hits=[line for line in new if not keywords or any(word.casefold() in line.casefold() for word in keywords)]
    if hits:summary='\n'.join('• '+line[:350] for line in hits[:8])
    elif keywords:summary='Текст змінився, нових збігів за заданими словами в доступних уривках немає.'
    else:summary='Текст змінився; зміни можуть бути видаленням або поза збереженим уривком.'
    if current.get('truncated'):summary+='\nПеревірка текстова; частину вмісту не включено в короткий знімок.'
    return {'changed':True,'matches':bool(hits) or not keywords,'summary':summary}
