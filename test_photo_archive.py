import base64
import hashlib
import unittest
from io import BytesIO
from unittest.mock import AsyncMock
from PIL import Image
from photo_archive import archive_image, retrieve_image, inspect_image, safe_filename
from drive_memory import MemoryUnavailable


def png_bytes():
    b=BytesIO();Image.new('RGB',(20,20),'red').save(b,format='PNG');return b.getvalue()


class ArchiveTests(unittest.IsolatedAsyncioTestCase):
    def test_validation_does_not_modify_image_bytes(self):
        data=png_bytes();before=data[:]
        mime,ext,digest=inspect_image(data)
        self.assertEqual((mime,ext),('image/png','png'))
        self.assertEqual(data,before)
        self.assertEqual(digest,hashlib.sha256(data).hexdigest())

    def test_non_image_and_oversize_are_rejected(self):
        for data in [b'%PDF text',b'',b'x'*(8*1024*1024+1)]:
            with self.assertRaises(ValueError):inspect_image(data)

    def test_filename_does_not_include_local_paths(self):
        self.assertEqual(safe_filename('C:\\private\\image.png','png'),'image.png')
        self.assertEqual(safe_filename('../photo.png','png'),'photo.png')

    async def test_upload_requires_matching_digest_and_size(self):
        data=png_bytes();digest=hashlib.sha256(data).hexdigest();store=AsyncMock()
        store.request.return_value={'archive_id':digest[:24],'size':len(data),'sha256':digest}
        result=await archive_image(store,data,7,'photo.png','birthday',True,'2026-11-01')
        self.assertEqual(result,digest[:24])
        self.assertEqual(base64.b64decode(store.request.call_args.kwargs['content_b64']),data)
        self.assertEqual(store.request.call_args.kwargs['owner'],'7')
        store.request.return_value={'archive_id':digest[:24],'size':len(data),'sha256':'wrong'}
        with self.assertRaises(MemoryUnavailable):await archive_image(store,data,7,'photo.png','',True,'2026-11-01')

    async def test_retrieve_uses_drive_bytes_and_checks_integrity(self):
        data=png_bytes();digest=hashlib.sha256(data).hexdigest();store=AsyncMock()
        store.request.return_value={'content_b64':base64.b64encode(data).decode(),'sha256':digest,'filename':'original.png','caption':'birthday'}
        result=await retrieve_image(store,7,digest[:24])
        self.assertEqual(result,(data,'original.png','birthday'))
        store.request.assert_awaited_once_with('get_photo',owner='7',archive_id=digest[:24])
        with self.assertRaises(MemoryUnavailable):await retrieve_image(store,7,'a'*24)

    async def test_original_and_telegram_copy_are_labelled_differently(self):
        data=png_bytes();digest=hashlib.sha256(data).hexdigest();store=AsyncMock()
        store.request.return_value={'archive_id':digest[:24],'size':len(data),'sha256':digest}
        await archive_image(store,data,8,'photo.png','',False,'2026-11-01')
        self.assertIs(store.request.call_args.kwargs['original'],False)


class HandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_oversize_is_rejected_before_telegram_download(self):
        import os
        os.environ['TELEGRAM_TOKEN']='123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi'
        os.environ['GROQ_API_KEY']='test-key-not-real'
        import app
        from types import SimpleNamespace
        from unittest.mock import patch
        message=SimpleNamespace(from_user=SimpleNamespace(id=7,username='E_n_gram'),answer=AsyncMock(),bot=SimpleNamespace(download=AsyncMock()))
        with patch.object(app,'drive_store',AsyncMock()):
            await app.receive_archived_image(message,SimpleNamespace(file_size=9*1024*1024),True)
        message.bot.download.assert_not_awaited()
        self.assertIn('не збережено',message.answer.await_args.args[0])

    async def test_successful_handler_archives_received_document_bytes(self):
        import app
        from datetime import datetime, timezone
        from types import SimpleNamespace
        from unittest.mock import patch
        data=png_bytes();digest=hashlib.sha256(data).hexdigest()
        async def download(media,destination,timeout):destination.write(data)
        message=SimpleNamespace(from_user=SimpleNamespace(id=7,username='E_n_gram'),answer=AsyncMock(),bot=SimpleNamespace(download=AsyncMock(side_effect=download)),caption='Family photo',date=datetime(2026,11,1,tzinfo=timezone.utc))
        store=AsyncMock();store.request.return_value={'archive_id':digest[:24],'sha256':digest,'size':len(data)}
        with patch.object(app,'drive_store',store):
            await app.receive_archived_image(message,SimpleNamespace(file_size=len(data),file_name='family.png'),True)
        self.assertEqual(base64.b64decode(store.request.call_args.kwargs['content_b64']),data)
        self.assertIn('без зміни',message.answer.await_args.args[0])

    async def test_failed_storage_never_claims_photo_saved(self):
        import app
        from datetime import datetime, timezone
        from types import SimpleNamespace
        from unittest.mock import patch
        data=png_bytes()
        async def download(media,destination,timeout):destination.write(data)
        message=SimpleNamespace(from_user=SimpleNamespace(id=7,username='E_n_gram'),answer=AsyncMock(),bot=SimpleNamespace(download=AsyncMock(side_effect=download)),caption='',date=datetime.now(timezone.utc))
        store=AsyncMock();store.request.side_effect=MemoryUnavailable('offline')
        with patch.object(app,'drive_store',store):
            await app.receive_archived_image(message,SimpleNamespace(file_size=len(data)),False)
        self.assertIn('Не отримала підтвердження',message.answer.await_args.args[0])

if __name__=='__main__':unittest.main()
