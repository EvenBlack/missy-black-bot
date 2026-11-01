import unittest
from types import SimpleNamespace
from account_identity import CONFIRMED_ACCOUNTS,configure_admin,seed_identities,pin_identity,identity_instruction,expected_identity
from prompt_budget import book_prompt


def account(uid,username,name='Telegram display name'):
    return SimpleNamespace(from_user=SimpleNamespace(id=uid,username=username,first_name=name))


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.saved={key:dict(value) for key,value in CONFIRMED_ACCOUNTS.items()}
        configure_admin('test_admin','Test Creator')
    def tearDown(self):
        CONFIRMED_ACCOUNTS.clear();CONFIRMED_ACCOUNTS.update(self.saved)

    def test_confirmed_identity_is_separate_from_preferences_and_other_account(self):
        memory={};seed_identities(memory)
        owner=account(1,'TEST_ADMIN');other=account(2,'second_user','Second User')
        pin_identity(memory,owner);pin_identity(memory,other)
        self.assertEqual(memory['account_identities']['1']['grammatical_gender'],'male')
        self.assertTrue(memory['account_identities']['1']['creator'])
        self.assertEqual(memory['account_identities']['2']['name'],'Second User')
        self.assertFalse(memory['account_identities']['2']['creator'])
        memory['user_profiles']={}
        self.assertIn('чоловік',identity_instruction(memory,owner))
        self.assertNotIn('Test Creator',identity_instruction(memory,other))

    def test_confirmed_gender_overrides_legacy_generated_guess(self):
        owner=account(1,'test_admin')
        memory={'account_identities':{'1':{'name':'Incorrect Name','grammatical_gender':'female'}}}
        instruction=identity_instruction(memory,owner)
        self.assertIn('Test Creator',instruction);self.assertIn('чоловічому роді',instruction)
        self.assertNotIn('Incorrect Name',instruction)
        self.assertFalse(expected_identity(account(3,'someone_else'))['creator'])

    def test_identity_and_json_contract_survive_book_prompt_budget(self):
        identity=identity_instruction({},account(1,'test_admin'))
        context='Фрагмент 7: Початок\n'+'Персонаж ішов дорогою. '*1000
        payload=book_prompt('Хто на початку?',context,'Пиши коротко',[],characters=True,identity=identity)
        system=payload[0]['content']
        self.assertIn('Test Creator',system);self.assertIn('чоловічому роді',system)
        self.assertIn('Поверни лише JSON',system)
        self.assertIn('characters',system)
        self.assertLessEqual(sum(len(m['content'].encode()) for m in payload),6000)
