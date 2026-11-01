import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from prompt_budget import bounded_prompt,INPUT_BYTES,OUTPUT_TOKENS,model_error,format_book_context
from book_library import BookContextCache

class PromptTests(unittest.IsolatedAsyncioTestCase):
    def test_large_ukrainian_book_and_history_fit_budget_without_losing_current_question(self):
        question='Які персонажі з’являються на початку завантаженої книги?'
        history=[{'role':'user' if n%2==0 else 'assistant','content':'Українська історія 🇺🇦 '*1000} for n in range(30)]
        history.append({'role':'user','content':question})
        payload=bounded_prompt('Ти україномовна асистентка. Не вигадуй джерела.',question,[
            ('Побажання','Надалі відповідай коротко',450),
            ('Книга','Фрагмент 3: Перший розділ\n'+'Роберт побачив вартового. '*10000,3300),
            ('Спогади','Синя чашка '*3000,3000)],history)
        self.assertLessEqual(sum(len(m['content'].encode()) for m in payload),INPUT_BYTES)
        self.assertLess(INPUT_BYTES+OUTPUT_TOKENS+512,8000)
        self.assertEqual(payload[-1]['content'],question)
        self.assertIn('Фрагмент 3',payload[0]['content'])
        self.assertIn('Надалі відповідай коротко',payload[0]['content'])
        self.assertIn('[Уривок контексту]',payload[0]['content'])

    def test_long_current_message_is_explicitly_marked_and_unicode_valid(self):
        payload=bounded_prompt('x'*10000,'🇺🇦'*4000,[],[])
        self.assertIn('Запит скорочено',payload[-1]['content'])
        self.assertLessEqual(sum(len(m['content'].encode()) for m in payload),INPUT_BYTES)
        self.assertNotIn('\ufffd',payload[-1]['content'])

    def test_provider_size_and_quota_errors_are_not_presented_as_connection_failure(self):
        self.assertIn('Контекст завеликий',model_error(SimpleNamespace(status_code=413)))
        self.assertIn('Досягнуто ліміту',model_error(SimpleNamespace(status_code=429)))
        self.assertIn('зв’язатися'.replace('’',"'"),model_error(Exception()))

    async def test_beginning_question_fetches_first_fragments_with_owner_and_caches_them(self):
        async def request(action,**kw):
            self.assertEqual(kw['owner'],'7')
            self.assertEqual(kw['book_id'],'a'*24)
            return {'title':'Book','total_chunks':200,'chunks':[{'number':kw['chunk_number'],'chapter':'Розділ','text':'Персонаж Іван'}]}
        store=SimpleNamespace(request=AsyncMock(side_effect=request));cache=BookContextCache()
        result=await cache.get(store,7,'a'*24,'Хто на початку книги?')
        self.assertEqual([c['number'] for c in result['chunks']],[1,2,3,4])
        self.assertIn('Фрагмент 1',format_book_context(result))
        await cache.get(store,7,'a'*24,'Хто на початку книги?')
        self.assertEqual(store.request.await_count,4)
