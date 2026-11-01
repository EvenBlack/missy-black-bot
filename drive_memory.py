"""Google Apps Script memory transport. Never log payloads, secrets or redirect URLs."""
import json
import asyncio
from urllib.parse import urlparse
import aiohttp


class MemoryUnavailable(RuntimeError):
    pass


class RevisionConflict(MemoryUnavailable):
    pass


def validate_memory(data):
    if not isinstance(data, dict):
        raise MemoryUnavailable('INVALID_MEMORY')
    for key, kind in [('факти', list), ('фото', list), ('image_cache', dict)]:
        if not isinstance(data.get(key), kind):
            raise MemoryUnavailable('INVALID_MEMORY')
    return data


class DriveMemory:
    def __init__(self, url, secret):
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.hostname != 'script.google.com' or not parsed.path.startswith('/macros/s/') or not parsed.path.endswith('/exec') or parsed.query or parsed.username or parsed.password or parsed.port:
            raise MemoryUnavailable('INVALID_MEMORY_ENDPOINT')
        if len(secret) < 32:
            raise MemoryUnavailable('INVALID_MEMORY_SECRET')
        self.url, self.secret = url, secret
        self.backup_ready = False
        self.photo_archive_ready = False
        self.voice_ready = False
        self.voice_protocol = 0
        self.archive_protocol = 0
        self.history_ready = False
        self.books_ready = False
        self.automation_ready = False
        self.recovered = False

    async def request(self, action, **fields):
        for attempt in range(3):
            try:
                return await self._request_once(action, **fields)
            except MemoryUnavailable as exc:
                if str(exc) != 'MEMORY_BUSY' or attempt == 2:
                    raise
                # BUSY guarantees the script did not acquire its write lock.
                # Retrying other failures could replay an uncertain non-idempotent write.
                await asyncio.sleep(2 * (attempt + 1))

    async def _request_once(self, action, **fields):
        payload = dict(fields, action=action, secret=self.secret)
        try:
            async with aiohttp.ClientSession(json_serialize=lambda value: json.dumps(value, ensure_ascii=False), timeout=aiohttp.ClientTimeout(total=60 if action in {'archive_photo', 'get_photo', 'get_identity', 'set_identity', 'store_voice', 'list_voice', 'append_history', 'search_history', 'store_book', 'index_book', 'book_context', 'get_book', 'list_books', 'automation_get', 'automation_put'} else 25, connect=8)) as session:
                method, url = 'POST', self.url
                # ContentService redirects POST to a read-only Google-hosted output URL.
                for _ in range(4):
                    kwargs = {'json': payload} if method == 'POST' else {}
                    async with session.request(method, url, allow_redirects=False, **kwargs) as response:
                        if response.status in (301, 302, 303):
                            redirect = response.headers.get('Location', '')
                            parsed = urlparse(redirect)
                            if parsed.scheme != 'https' or parsed.hostname != 'script.googleusercontent.com' or parsed.username or parsed.password or parsed.port:
                                raise MemoryUnavailable('UNSAFE_MEMORY_REDIRECT')
                            method, url = 'GET', redirect
                            continue
                        if response.status != 200:
                            raise MemoryUnavailable('MEMORY_HTTP_ERROR')
                        chunks, size = [], 0
                        async for chunk in response.content.iter_chunked(65536):
                            size += len(chunk)
                            if size > (12 if action in {'get_photo', 'get_identity', 'get_book', 'search_history', 'automation_get'} else 2) * 1024 * 1024:
                                raise MemoryUnavailable('MEMORY_RESPONSE_TOO_LARGE')
                            chunks.append(chunk)
                        result = json.loads(b''.join(chunks))
                        break
                else:
                    raise MemoryUnavailable('MEMORY_REDIRECT_LIMIT')
        except MemoryUnavailable:
            raise
        except Exception:
            # Avoid embedding response bodies, URLs, request objects or credentials.
            raise MemoryUnavailable('MEMORY_NETWORK_OR_RESPONSE_ERROR') from None
        if not isinstance(result, dict) or result.get('ok') is not True:
            if isinstance(result, dict) and result.get('error') == 'REVISION_CONFLICT':
                raise RevisionConflict('REVISION_CONFLICT')
            safe_errors = {'UNAUTHORIZED', 'SETUP_REQUIRED', 'STORAGE_ERROR', 'POST_REQUIRED', 'BUSY', 'INVALID_ACTION', 'INVALID_DATA', 'MEMORY_TOO_LARGE', 'INVALID_REQUEST', 'BACKUP_UNAVAILABLE', 'BACKUP_SETUP_REQUIRED', 'ARCHIVE_SETUP_REQUIRED', 'INVALID_PHOTO', 'PHOTO_NOT_FOUND', 'PHOTO_CONFLICT', 'IDENTITY_SETUP_REQUIRED', 'IDENTITY_NOT_SET', 'VOICE_SETUP_REQUIRED', 'INVALID_VOICE', 'VOICE_CONFLICT', 'VOICE_NOT_FOUND', 'VOICE_KEY_MISMATCH', 'VOICE_AUDIO_SIZE', 'VOICE_AUDIO_CHECKSUM', 'INVALID_HISTORY', 'HISTORY_CONFLICT', 'INVALID_BOOK', 'BOOK_CHECKSUM', 'BOOK_CONFLICT', 'BOOK_NOT_FOUND', 'INVALID_BOOK_INDEX', 'BOOK_INDEX_PENDING', 'AUTOMATION_SETUP_REQUIRED', 'AUTOMATION_STORAGE_INVALID', 'INVALID_AUTOMATION', 'AUTOMATION_TOO_LARGE'}
            code = result.get('error') if isinstance(result, dict) else None
            raise MemoryUnavailable('MEMORY_' + code if isinstance(code, str) and code in safe_errors else 'MEMORY_REQUEST_REJECTED')
        revision = result.get('revision')
        if action in {'get', 'put'} and (type(revision) is not int or revision < 0):
            raise MemoryUnavailable('INVALID_MEMORY_REVISION')
        return result

    async def read(self):
        result = await self.request('get')
        if 'data' not in result:
            raise MemoryUnavailable('MISSING_MEMORY_DATA')
        self.backup_ready = result.get('backup_ready') is True
        self.photo_archive_ready = result.get('photo_archive_ready') is True
        self.voice_ready = result.get('voice_ready') is True
        self.voice_protocol = result.get('voice_protocol', 0)
        self.archive_protocol = result.get('archive_protocol', 0)
        self.history_ready = result.get('history_ready') is True
        self.books_ready = result.get('books_ready') is True
        self.automation_ready = result.get('automation_ready') is True
        self.recovered = result.get('recovered') is True
        data = result['data']
        if data is not None:
            validate_memory(data)
        return data, result['revision']

    async def write(self, data, revision):
        validate_memory(data)
        if len(json.dumps(data, ensure_ascii=False).encode('utf-16-le')) // 2 > 260000:
            raise MemoryUnavailable('MEMORY_TOO_LARGE')
        result = await self.request('put', data=data, expected_revision=revision)
        if result['revision'] != revision + 1:
            raise MemoryUnavailable('INVALID_WRITE_ACKNOWLEDGEMENT')
        return result['revision']
