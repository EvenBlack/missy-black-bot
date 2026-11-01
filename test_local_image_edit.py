import os
import unittest
from io import BytesIO
from types import SimpleNamespace
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from PIL import Image
from local_image_edit import edit_image, parse_edit, EditError


def make_image(size=(200,150),mode='RGB',color=(100,100,100)):
    b=BytesIO();Image.new(mode,size,color).save(b,format='PNG');return b.getvalue()


def pixel(data):
    with Image.open(BytesIO(data)) as image:return image.getpixel((0,0))


class LocalEditTests(unittest.TestCase):
    def test_brightness_changes_pixels_and_keeps_source(self):
        source=make_image();copy=source[:]
        lighter,_,_=edit_image(source,'світліше')
        darker,_,_=edit_image(source,'темніше')
        self.assertGreater(pixel(lighter)[0],100)
        self.assertLess(pixel(darker)[0],100)
        self.assertEqual(source,copy)

    def test_warmth_and_coolness_change_channels(self):
        warm,_,_=edit_image(make_image(),'тепліше')
        cool,_,_=edit_image(make_image(),'холодніше')
        self.assertGreater(pixel(warm)[0],pixel(warm)[2])
        self.assertLess(pixel(cool)[0],pixel(cool)[2])

    def test_alpha_is_preserved(self):
        result,_,_=edit_image(make_image(mode='RGBA',color=(100,100,100,80)),'світліше')
        self.assertEqual(pixel(result)[3],80)

    def test_contrast_expands_pixel_range(self):
        image=Image.new('RGB',(2,1));image.putdata([(80,80,80),(180,180,180)])
        buffer=BytesIO();image.save(buffer,format='PNG')
        result,_,_=edit_image(buffer.getvalue(),'контраст 1.5')
        with Image.open(BytesIO(result)) as edited:
            self.assertGreater(edited.getpixel((1,0))[0]-edited.getpixel((0,0))[0],100)

    def test_crop_is_centered_and_has_requested_ratio(self):
        image=Image.new('RGB',(400,300),'red');image.paste((0,255,0),(50,0,350,300))
        buffer=BytesIO();image.save(buffer,format='PNG')
        result,_,_=edit_image(buffer.getvalue(),'обрізати 1:1')
        with Image.open(BytesIO(result)) as edited:
            self.assertEqual(edited.size,(300,300))
            self.assertEqual(edited.getpixel((0,0)),(0,255,0))

    def test_ukrainian_annotation_renders_and_preserves_top(self):
        source=make_image((600,400))
        result,_,_=edit_image(source,'напис Привіт, Україно! Ї Є Ґ')
        self.assertEqual(pixel(result),pixel(source))
        with Image.open(BytesIO(result)) as edited:
            self.assertNotEqual(edited.getpixel((2,398)),(100,100,100))

    def test_bounds_and_invalid_commands(self):
        for command in ['контраст nan','контраст inf','контраст 100','обрізати 0:1','обрізати 1:100','напис','зміни одяг']:
            with self.subTest(command=command),self.assertRaises(EditError):parse_edit(command)
        with self.assertRaises(EditError):edit_image(b'not an image','світліше')

    def test_large_image_is_resized_with_notice_flag(self):
        result,_,resized=edit_image(make_image((3200,100)),'світліше')
        self.assertTrue(resized)
        with Image.open(BytesIO(result)) as edited:self.assertEqual(edited.width,3072)

    def test_animated_images_are_not_silently_flattened(self):
        buffer=BytesIO();first=Image.new('RGB',(10,10),'red');second=Image.new('RGB',(10,10),'blue')
        first.save(buffer,format='GIF',save_all=True,append_images=[second],duration=100)
        with self.assertRaises(EditError):edit_image(buffer.getvalue(),'тепліше')

    def test_missing_font_is_an_explicit_error(self):
        with patch('local_image_edit.FONT_PATH','/missing-font.ttf'):
            with self.assertRaises(EditError):edit_image(make_image(),'напис Україна')


class EditHandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        os.environ['TELEGRAM_TOKEN']='123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi'
        os.environ['GROQ_API_KEY']='test-key-not-real'
        import app
        self.app=app

    async def test_identity_edit_does_not_call_ai_or_replace_identity(self):
        app=self.app;data=make_image()
        message=SimpleNamespace(from_user=SimpleNamespace(id=7,username='E_n_gram'),text='/edit себе світліше',reply_to_message=None,date=datetime.now(timezone.utc),answer=AsyncMock(),answer_document=AsyncMock())
        store=AsyncMock()
        with patch.object(app,'drive_store',store),patch.object(app,'retrieve_identity',AsyncMock(return_value=(data,'Katrusia.png',''))),patch.object(app,'archive_image',AsyncMock(return_value='a'*24)),patch.object(app.groq_client.chat.completions,'create') as model,patch.object(app,'web_search',AsyncMock()) as search:
            await app.edit_command(message)
            model.assert_not_called();search.assert_not_awaited();store.request.assert_not_awaited()
        self.assertGreater(pixel(message.answer_document.await_args.args[0].data)[0],100)
        self.assertEqual(pixel(data)[0],100)
        self.assertIn('основний образ не змінено',message.answer_document.await_args.kwargs['caption'])

    async def test_help_does_not_require_any_storage_or_model_call(self):
        app=self.app;message=SimpleNamespace(from_user=SimpleNamespace(id=7,username='E_n_gram'),text='/edit',answer=AsyncMock())
        with patch.object(app,'drive_store',AsyncMock()) as store:
            await app.edit_command(message);store.request.assert_not_awaited()
        self.assertIn('/edit світліше',message.answer.await_args.args[0])



class CaptionCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_edit_caption_uses_attached_image(self):
        import app
        source=make_image()
        async def download(media,destination,timeout):destination.write(source)
        message=SimpleNamespace(from_user=SimpleNamespace(id=7,username='E_n_gram'),text=None,caption='/edit світліше',reply_to_message=None,document=SimpleNamespace(file_size=len(source)),photo=None,date=datetime.now(timezone.utc),bot=SimpleNamespace(download=AsyncMock(side_effect=download)),answer=AsyncMock(),answer_document=AsyncMock())
        with patch.object(app,'drive_store',None):await app.edit_command(message)
        self.assertGreater(pixel(message.answer_document.await_args.args[0].data)[0],100)

    def test_command_text_accepts_text_caption_or_neither(self):
        import app
        self.assertEqual(app.command_text(SimpleNamespace(text=None,caption='/photos')),'/photos')
        self.assertEqual(app.command_text(SimpleNamespace(text='/edit',caption=None)),'/edit')
        self.assertEqual(app.command_text(SimpleNamespace(text=None,caption=None)),'')

if __name__=='__main__':unittest.main()
