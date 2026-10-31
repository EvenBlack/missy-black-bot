"""Offline regressions: no production keys, Telegram polling or live API calls."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ['TELEGRAM_TOKEN'] = '123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi'
os.environ['GROQ_API_KEY'] = 'test-key-not-real'
import app

class CoreTests(unittest.TestCase):
    def test_all_real_search_transport_profiles_construct(self):
        # Integration regression: mocks previously hid incompatible primp versions.
        import primp
        from ddgs.http_client import HttpClient
        for profile in HttpClient._impersonates:
            with self.subTest(profile=profile):
                primp.Client(impersonate=profile)

    def test_query_removes_conversation_instructions(self):
        self.assertEqual(app.prepare_web_query('Міссі, знайди останні новини України та дай посилання на джерела'), 'останні новини України')

    def test_news_feed_keeps_date_publisher_and_link(self):
        result = app.parse_news_feed(b'<rss><channel><item><title>News</title><link>https://news.google.com/rss/articles/example</link><pubDate>Sat, 31 Oct 2026 20:00:00 GMT</pubDate><source>Publisher</source></item></channel></rss>')
        self.assertIn('Publisher', result)
        self.assertIn('31 Oct 2026', result)
        self.assertIn('https://news.google.com/', result)

    def test_html_result_nested_snippet_and_redirect(self):
        parser = app.SearchResultsParser()
        parser.feed('<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fpython.org%2F">Python</a><div class="result__snippet">Official <b>Python</b> docs</div>')
        self.assertEqual(parser.items, [{'title':'Python','url':'https://python.org/','snippet':'Official Python docs'}])

    def test_lite_result(self):
        parser = app.SearchResultsParser()
        parser.feed('<a class="result-link" href="https://example.com">Example</a><td class="result-snippet">Useful <b>text</b></td>')
        self.assertEqual(parser.items[0]['snippet'], 'Useful text')

    def test_no_result_links_from_challenge_or_unsafe_url(self):
        parser = app.SearchResultsParser()
        parser.feed('<a class="result__a" href="javascript:alert(1)">bad</a><h1>Challenge</h1>')
        self.assertEqual(parser.items, [])

    def test_mode_switch_persists(self):
        self.assertEqual(app.select_mode(99, 'Міссі, допоможи'), 'colleague')
        self.assertEqual(app.select_mode(99, 'продовжуй'), 'colleague')
        self.assertEqual(app.select_mode(99, 'Катрусю, привіт'), 'friend')

    def test_optional_reasoning_parameter(self):
        with patch.object(app, 'GROQ_MODEL', 'llama-test'):
            self.assertNotIn('reasoning_effort', app.completion_options())
        with patch.object(app, 'GROQ_MODEL', 'openai/gpt-oss-20b'):
            self.assertEqual(app.completion_options()['reasoning_effort'], 'low')

    def test_access_casefold(self):
        self.assertTrue(app.has_access(SimpleNamespace(from_user=SimpleNamespace(username='e_n_GRAM'))))
        self.assertFalse(app.has_access(SimpleNamespace(from_user=SimpleNamespace(username=None))))

    def test_image_detection(self):
        self.assertTrue(app.image_request('Знайди фото кота'))
        self.assertTrue(app.image_request('фото собаки'))
        self.assertFalse(app.image_request('Я люблю фотографувати'))

    def test_memory_corruption_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, 'MEMORY_FILE', Path(tmp)/'memory.json'):
            original = '{"факти": []} extra'
            app.MEMORY_FILE.write_text(original)
            with self.assertRaises(RuntimeError):
                app.load_memory()
            self.assertEqual(app.MEMORY_FILE.read_text(), original)

    def test_atomic_memory_roundtrip_and_unknown_keys(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, 'MEMORY_FILE', Path(tmp)/'sub'/'memory.json'):
            app.save_memory({'факти':['СИНЯ ЗІРКА'], 'legacy':{'keep':True}})
            self.assertEqual(app.load_memory()['факти'], ['СИНЯ ЗІРКА'])
            self.assertEqual(app.load_memory()['legacy'], {'keep':True})
            self.assertFalse(app.MEMORY_FILE.with_suffix('.json.tmp').exists())

    def test_save_failure_propagates(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, 'MEMORY_FILE', Path(tmp)/'memory.json'), patch.object(app.os, 'replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                app.save_memory({'факти': []})

class AsyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch.object(app, 'DDGS')
        self.search_client = patcher.start()
        self.search_client.return_value.text.return_value = []
        self.addCleanup(patcher.stop)

    async def test_news_route_precedes_generic_search(self):
        with patch.object(app, 'news_search', AsyncMock(return_value='dated headlines')) as news:
            self.assertEqual(await app.web_search('Міссі, знайди новини України'), 'dated headlines')
            news.assert_awaited_once_with('новини України')
            self.search_client.assert_not_called()

    async def test_metasearch_source_link(self):
        self.search_client.return_value.text.return_value = [
            {'title':'Python', 'href':'https://python.org/', 'body':'Official docs'}
        ]
        result = await app.web_search('Python')
        self.assertIn('https://python.org/', result)

    async def test_emoji_telegram_limit(self):
        message = SimpleNamespace(answer=AsyncMock())
        text = '🙂'*5000+'кінець'
        await app.send_long(message, text)
        pieces = [call.args[0] for call in message.answer.await_args_list]
        self.assertEqual(''.join(pieces), text)
        self.assertTrue(all(len(x.encode('utf-16-le'))//2 <= 4000 for x in pieces))

    async def test_missing_vision_is_configuration_failure(self):
        with patch.object(app, 'CLOUDFLARE_ACCOUNT_ID', None):
            with self.assertRaisesRegex(RuntimeError, 'VISION_NOT_CONFIGURED'):
                await app.find_image('cat')

    async def test_vision_failure_different_from_irrelevant(self):
        candidate = {'url':'https://example.com/cat.jpg', 'title':'cat', 'source':'test'}
        with patch.object(app, 'CLOUDFLARE_ACCOUNT_ID', 'dummy'), patch.object(app, 'CLOUDFLARE_API_TOKEN', 'dummy'), patch.object(app, 'search_openverse', AsyncMock(return_value=[candidate])), patch.object(app, 'search_wikimedia', AsyncMock(return_value=[])), patch.object(app, 'download_image', AsyncMock(return_value=(b'image','jpg'))), patch.object(app, 'verify_image_with_cloudflare', AsyncMock(return_value=None)):
            with self.assertRaisesRegex(RuntimeError, 'VISION_UNAVAILABLE'):
                await app.find_image('cat')
        with patch.object(app, 'CLOUDFLARE_ACCOUNT_ID', 'dummy'), patch.object(app, 'CLOUDFLARE_API_TOKEN', 'dummy'), patch.object(app, 'search_openverse', AsyncMock(return_value=[candidate])), patch.object(app, 'search_wikimedia', AsyncMock(return_value=[])), patch.object(app, 'download_image', AsyncMock(return_value=(b'image','jpg'))), patch.object(app, 'verify_image_with_cloudflare', AsyncMock(return_value=False)):
            self.assertIsNone(await app.find_image('cat'))

    async def test_search_contains_source_url(self):
        results = [{'url':'https://python.org','title':'Python','snippet':'Official'}]
        with patch.object(app, 'search_endpoint', AsyncMock(return_value=results)):
            result = await app.web_search('Python')
            self.assertIn('https://python.org',result)
            self.assertEqual(result.count('https://python.org'),1)

    async def test_bing_fallback_after_empty_duckduckgo(self):
        class Content:
            async def iter_chunked(self, n):
                for piece in [b'<rss><channel><item><title>Python</title>', b'<link>https://python.org/</link><description>Docs</description></item></channel></rss>']:
                    yield piece
        response = SimpleNamespace(status=200, content=Content())
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=response)
        manager.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.get.return_value = manager
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        with patch.object(app, 'search_endpoint', AsyncMock(return_value=[])), patch.object(app.aiohttp, 'ClientSession', return_value=session):
            result = await app.web_search('Python')
            self.assertIn('https://python.org/', result)
            self.assertEqual(session.get.call_args.args[0], 'https://www.bing.com/search')

    async def test_verified_photo_is_sent_as_original_document(self):
        candidate = {'title':'cat', 'source':'test', 'page_url':'https://example.com/cat'}
        message = SimpleNamespace(
            text='Знайди фото кота',
            from_user=SimpleNamespace(id=123, username='E_n_gram', first_name='Tester'),
            chat=SimpleNamespace(id=123),
            bot=SimpleNamespace(send_chat_action=AsyncMock()),
            answer=AsyncMock(),
            answer_document=AsyncMock(return_value=SimpleNamespace(document=SimpleNamespace(file_id='cached-document'))),
        )
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, 'MEMORY_FILE',Path(tmp)/'memory.json'), patch.object(app,'refine_query',return_value='cat'), patch.object(app,'find_image',AsyncMock(return_value=(b'original-image-bytes','jpg',candidate))):
            await app.handle_text(message)
            sent = message.answer_document.await_args.args[0]
            self.assertEqual(sent.data, b'original-image-bytes')
            self.assertEqual(app.load_memory()['image_cache']['original-vision-v2:cat']['file_id'], 'cached-document')
            self.assertIn('https://example.com/cat', message.answer_document.await_args.kwargs['caption'])

    async def test_memory_command_variants_no_model_or_web(self):
        for text in ['Запам’ятай СИНЯ ЗІРКА', "Запам'ятай СИНЯ ЗІРКА", 'запамʼятай СИНЯ ЗІРКА']:
            message = SimpleNamespace(text=text, from_user=SimpleNamespace(id=1, username='E_n_gram', first_name='Tester'), answer=AsyncMock())
            with tempfile.TemporaryDirectory() as tmp, patch.object(app, 'MEMORY_FILE',Path(tmp)/'memory.json'), patch.object(app,'web_search',AsyncMock()) as search:
                await app.handle_text(message)
                self.assertIn(text, app.load_memory()['факти'][0])
                search.assert_not_awaited()
                self.assertIn('Записала', message.answer.await_args.args[0])

    async def test_concurrent_same_user_serialized_and_history_bounded(self):
        active = 0
        peak = 0
        async def fake(message):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            app.histories[77] = [{'role':'user','content':'x'}]*50
            await asyncio.sleep(0)
            active -= 1
        message = SimpleNamespace(from_user=SimpleNamespace(id=77,username='E_n_gram'))
        with patch.object(app,'handle_text',fake), patch.object(app,'persist_conversation',AsyncMock()):
            await asyncio.gather(app.text_handler(message), app.text_handler(message))
        self.assertEqual(peak,1)
        self.assertEqual(len(app.histories[77]), app.MAX_HISTORY*2)

if __name__ == '__main__':
    unittest.main()
