import unittest
from unittest.mock import AsyncMock
from deep_research import research,research_prompt,report_text,canonical_url,validate_summary


class ResearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_multiple_queries_providers_dedup_and_actual_page_status(self):
        async def first(q):return [{'url':'https://one.example/article?utm_source=test','title':'Source One','snippet':'Public fact'},{'url':'https://two.example/article','title':'Source Two','snippet':'Search excerpt'}]
        async def second(q):return [{'url':'https://one.example/article','title':'Duplicate','snippet':'Duplicate'},{'url':'ftp://bad.example','title':'bad'}]
        async def fetch(url):
            if 'two.example' in url:raise OSError('unavailable')
            return {'title':'Source One','lines':['Public fact from accessible page, with relevant context.']}
        a,b=AsyncMock(side_effect=first),AsyncMock(side_effect=second)
        result=await research('Public event',{'first':a,'second':b},fetcher=fetch)
        self.assertEqual(a.await_count,3);self.assertEqual(b.await_count,3)
        self.assertEqual(len(result['sources']),2)
        self.assertEqual(result['sources'][0]['providers'],['first','second'])
        self.assertTrue(result['sources'][0]['page_read']);self.assertFalse(result['sources'][1]['page_read'])
        text=report_text(result,'Summary [1]')
        self.assertIn('тільки пошуковий уривок',text)
        prompt=research_prompt(result)
        self.assertLessEqual(sum(len(m['content'].encode()) for m in prompt),6000)
        summary=validate_summary('Fact [1], wrong [99] https://invented.example/data',result)
        self.assertIn('Fact [1]',summary);self.assertNotIn('https://invented',summary);self.assertNotIn('[99]',summary)

    async def test_provider_failure_does_not_invent_sources_or_read_captcha(self):
        async def failed(q):raise OSError('offline')
        result=await research('Event',{'failed':failed},fetcher=AsyncMock())
        self.assertFalse(result['sources']);self.assertTrue(result['failures'])
        async def search(q):return [{'url':'https://one.example','title':'Page','snippet':'Public result'}]
        fetch=AsyncMock(return_value={'title':'Just a moment','lines':['Verify you are human captcha']})
        result=await research('Event',{'source':search},fetcher=fetch)
        self.assertFalse(result['sources'][0]['page_read'])

    def test_url_credentials_and_unsupported_protocols_are_excluded(self):
        self.assertEqual(canonical_url('https://user:pass@example.org/a'),'')
        self.assertEqual(canonical_url('javascript:alert(1)'),'')
        self.assertEqual(canonical_url('https://example.org/a?utm_source=x&year=2026#x'),'https://example.org/a?year=2026')
