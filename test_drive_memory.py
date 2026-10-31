import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

os.environ['TELEGRAM_TOKEN'] = '123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi'
os.environ['GROQ_API_KEY'] = 'test-key-not-real'
os.environ.pop('DRIVE_MEMORY_URL', None)
os.environ.pop('DRIVE_MEMORY_SECRET', None)
import app
from drive_memory import DriveMemory, MemoryUnavailable, RevisionConflict


def sample():
    return {'факти':['existing'], 'фото':[], 'image_cache':{}, 'legacy':{'keep':True}}


class TransportTests(unittest.IsolatedAsyncioTestCase):
    def test_endpoint_and_key_validation(self):
        for url in ['http://script.google.com/macros/s/a/exec','https://evil.example/macros/s/a/exec','https://script.google.com/macros/s/a/exec?secret=bad']:
            with self.assertRaises(MemoryUnavailable):
                DriveMemory(url,'x'*64)
        with self.assertRaises(MemoryUnavailable):
            DriveMemory('https://script.google.com/macros/s/a/exec','short')

    async def test_read_rejects_invalid_schema(self):
        store=DriveMemory('https://script.google.com/macros/s/a/exec','x'*64)
        with patch.object(store,'request',AsyncMock(return_value={'revision':1,'data':{'факти':None}})):
            with self.assertRaises(MemoryUnavailable): await store.read()

    async def test_null_and_missing_are_different(self):
        store=DriveMemory('https://script.google.com/macros/s/a/exec','x'*64)
        with patch.object(store,'request',AsyncMock(return_value={'revision':0,'data':None})):
            self.assertEqual(await store.read(),(None,0))
        with patch.object(store,'request',AsyncMock(return_value={'revision':0})):
            with self.assertRaises(MemoryUnavailable): await store.read()

    async def test_write_requires_next_revision_ack(self):
        store=DriveMemory('https://script.google.com/macros/s/a/exec','x'*64)
        with patch.object(store,'request',AsyncMock(return_value={'revision':5})):
            with self.assertRaises(MemoryUnavailable): await store.write(sample(),5)
        with patch.object(store,'request',AsyncMock(return_value={'revision':6})):
            self.assertEqual(await store.write(sample(),5),6)


class PersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_remote_data_wins_over_local_seed(self):
        store=AsyncMock()
        store.read.return_value=(sample(),4)
        with patch.object(app,'drive_store',store),patch.object(app,'load_memory',side_effect=AssertionError('must not read seed')):
            await app.initialize_memory()
        store.write.assert_not_awaited()

    async def test_empty_remote_bootstraps_once(self):
        store=AsyncMock()
        store.read.return_value=(None,0)
        data=sample()
        with patch.object(app,'drive_store',store),patch.object(app,'load_memory',return_value=data):
            await app.initialize_memory()
        store.write.assert_awaited_once_with(data,0)

    async def test_network_failure_never_falls_back_to_local_save(self):
        store=AsyncMock()
        store.read.side_effect=MemoryUnavailable('offline')
        with patch.object(app,'drive_store',store),patch.object(app,'save_memory') as save:
            with self.assertRaises(MemoryUnavailable): await app.change_memory(lambda d:d['факти'].append('new'))
            save.assert_not_called()

    async def test_conflict_replays_callback_on_fresh_snapshot(self):
        store=AsyncMock()
        concurrent=sample();concurrent['факти'].append('other-instance')
        store.read.side_effect=[(sample(),1),(concurrent,2)]
        store.write.side_effect=[RevisionConflict('conflict'),3]
        with patch.object(app,'drive_store',store):
            data=await app.change_memory(lambda d:d['факти'].append('ours'))
        self.assertEqual(data['факти'],['existing','other-instance','ours'])
        self.assertEqual(data['legacy'],{'keep':True})

    async def test_restart_reads_acknowledged_remote_fact(self):
        durable=sample(); revision=1
        store=AsyncMock()
        async def read():return copy.deepcopy(durable),revision
        async def write(data,expected):
            nonlocal durable,revision
            self.assertEqual(expected,revision)
            durable=copy.deepcopy(data);revision+=1;return revision
        store.read.side_effect=read;store.write.side_effect=write
        with patch.object(app,'drive_store',store),tempfile.TemporaryDirectory() as tmp,patch.object(app,'MEMORY_FILE',Path(tmp)/'missing.json'):
            await app.change_memory(lambda d:d['факти'].append('СИНЯ ЗІРКА'))
            await app.initialize_memory()
            self.assertEqual((await app.read_memory())['факти'][-1],'СИНЯ ЗІРКА')

    async def test_permanent_conflict_fails_after_bounded_attempts(self):
        store=AsyncMock();store.read.side_effect=lambda:None
        store.read.side_effect=None;store.read.return_value=(sample(),1)
        store.write.side_effect=RevisionConflict('conflict')
        with patch.object(app,'drive_store',store):
            with self.assertRaises(MemoryUnavailable): await app.change_memory(lambda d:None)
        self.assertEqual(store.write.await_count,3)

if __name__=='__main__': unittest.main()
