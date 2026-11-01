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

    async def test_beginning_question_reads_verified_original_with_owner_and_caches_it(self):
        import base64,hashlib
        from test_library_history import epub
        data=epub();digest=hashlib.sha256(data).hexdigest();book_id=digest[:24]
        async def request(action,**kw):
            self.assertEqual(action,'get_book')
            self.assertEqual(kw['owner'],'7')
            self.assertEqual(kw['book_id'],book_id)
            return {'content_b64':base64.b64encode(data).decode(),'sha256':digest,'filename':'book.epub'}
        store=SimpleNamespace(request=AsyncMock(side_effect=request));cache=BookContextCache()
        result=await cache.get(store,7,book_id,'Хто на початку книги?')
        self.assertEqual([c['number'] for c in result['chunks']],[1,2])
        self.assertIn('Фрагмент 1',format_book_context(result))
        await cache.get(store,7,book_id,'Хто на початку книги?')
        await cache.get(store,7,book_id,'Персонажі на початку?')
        self.assertEqual(store.request.await_count,1)

    def test_long_copyright_and_contents_do_not_hide_first_narrative(self):
        from book_library import beginning_chunks
        chunks=[
            {'chapter':'Вихідні дані','text':('ISBN 123. Усі права захищено. '*500)},
            {'chapter':'Зміст','text':('Північ. Південь. '*500)},
            {'chapter':'Перший розділ','text':'— Кеннет, ти мене чуєш? Повертайся до загону, — сказав солдат.\n\n'+('Кеннет уважно оглянув засніжений перевал. '*20)},
            {'chapter':'Перший розділ','text':'— Ласко, перевір дорогу попереду, бо з цією хуртовиною нічого не видно.\n'+('Ласко рушив уперед. '*20)},
            {'chapter':'Перший розділ','text':('Другий солдат наздогнав загін і передав звістку. '*10)}]
        selected=beginning_chunks(chunks)
        self.assertEqual([c['number'] for c in selected],[3,4,5])
        packed=format_book_context({'title':'Книга','selection':'beginning_narrative_candidates','chunks':selected})
        self.assertIn('Кеннет',packed);self.assertIn('Ласко',packed)
        self.assertIn('Фрагмент 5',packed)
        self.assertLessEqual(len(packed.encode()),3000)
        self.assertNotIn('ISBN',packed)

    async def test_beginning_rejects_mismatched_original_checksum(self):
        from drive_memory import MemoryUnavailable
        store=SimpleNamespace(request=AsyncMock(return_value={'content_b64':'YWJj','sha256':'b'*64,'filename':'book.epub'}))
        with self.assertRaisesRegex(MemoryUnavailable,'INVALID_BOOK_ORIGINAL'):
            await BookContextCache().get(store,7,'a'*24,'На початку')

    def test_english_annotation_and_editorial_preface_are_not_story_beginning(self):
        from book_library import beginning_chunks
        chunks=[
            {'chapter':'Annotation','text':'A distant northern border. '+('This book contains linked adventures and an unusual world. '*30)},
            {'chapter':'Автор','text':'Назва книги'},
            {'chapter':'Про сучасну фантастику','text':('Сучасна література має багато цікавих напрямів. '*50)},
            {'chapter':'Про сучасну фантастику','text':('Автор повертається до витоків жанру і досвіду попередників. '*30)},
            {'chapter':'ПІВНІЧ','text':'Сокира і скеля'},
            {'chapter':'Перша оповідь','text':('На перевалі розпочалася хуртовина. '*15)+'\n— Остапе, перевір стежку перед загоном і не поспішай.\n— Гаразд, зараз усе перевірю, — відповів Остап.'},
            {'chapter':'Перша оповідь','text':('Остап обернувся до Марка й уважно вислухав наказ. '*20)},
            {'chapter':'Перша оповідь','text':'— Пане лейтенанте, — Петро підійшов до командира.\n'+('Марко передав Петрові свою десятку і новий наказ. '*15)}]
        selected=beginning_chunks(chunks)
        self.assertEqual([c['number'] for c in selected],[6,7,8])
        context=format_book_context({'title':'Тестова книга','chunks':selected,'total_chunks':8})
        for name in ['Остап','Марко','Петро']:self.assertIn(name,context)
        self.assertNotIn('Annotation',context)
        self.assertNotIn('Про сучасну фантастику',context)
