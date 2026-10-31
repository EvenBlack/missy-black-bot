"""Byte-preserving image archive helpers. Image decoding is validation only."""
import base64
import hashlib
from io import BytesIO
from PIL import Image, UnidentifiedImageError
from drive_memory import MemoryUnavailable

MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
FORMATS = {'JPEG':('image/jpeg','jpg'), 'PNG':('image/png','png'), 'WEBP':('image/webp','webp'), 'GIF':('image/gif','gif'), 'TIFF':('image/tiff','tiff'), 'BMP':('image/bmp','bmp')}


def inspect_image(data):
    if not data or len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError('IMAGE_SIZE')
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format not in FORMATS or image.width * image.height > 40000000:
                raise ValueError('UNSUPPORTED_IMAGE')
            mime, ext = FORMATS[image.format]
            image.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError('INVALID_IMAGE') from None
    return mime, ext, hashlib.sha256(data).hexdigest()


def safe_filename(name, ext):
    name = (name or '').replace('\\', '/').split('/')[-1]
    name = ''.join(c for c in name if c.isprintable()).strip()[:100]
    return name or 'photo.' + ext


async def archive_image(store, data, owner, filename, caption, original, created):
    mime, ext, digest = inspect_image(data)
    result = await store.request(
        'archive_photo', owner=str(owner), sha256=digest,
        content_b64=base64.b64encode(data).decode('ascii'), mime=mime, ext=ext,
        filename=safe_filename(filename, ext), caption=(caption or '')[:1000],
        original=bool(original), created=created,
    )
    if result.get('sha256') != digest or result.get('size') != len(data) or result.get('archive_id') != digest[:24]:
        raise MemoryUnavailable('INVALID_ARCHIVE_ACKNOWLEDGEMENT')
    return result['archive_id']


async def retrieve_image(store, owner, archive_id):
    result = await store.request('get_photo', owner=str(owner), archive_id=archive_id)
    try:
        data = base64.b64decode(result['content_b64'], validate=True)
        _, ext, digest = inspect_image(data)
        if digest != result['sha256'] or digest[:24] != archive_id:
            raise ValueError('ARCHIVE_CHECKSUM_MISMATCH')
        return data, safe_filename(result.get('filename'), ext), result.get('caption', '')[:700]
    except (KeyError, TypeError, ValueError):
        raise MemoryUnavailable('INVALID_ARCHIVED_IMAGE') from None


async def retrieve_identity(store):
    result = await store.request('get_identity')
    try:
        data = base64.b64decode(result['content_b64'], validate=True)
        _, ext, digest = inspect_image(data)
        if digest != result['sha256'] or digest[:24] != result['archive_id']:
            raise ValueError('IDENTITY_CHECKSUM_MISMATCH')
        return data, safe_filename(result.get('filename'), ext), 'Катруся — основний візуальний образ'
    except (KeyError, TypeError, ValueError):
        raise MemoryUnavailable('INVALID_IDENTITY_IMAGE') from None
