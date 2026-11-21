import asyncio
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from llms.extensions.gemini import reconcile
from llms.extensions.gemini.client import GeminiApiError, _object
from llms.extensions.gemini.db import GeminiDB, to_custom_metadata
from llms.extensions.gemini.upload_worker import UploadWorker


class Context:
    def __init__(self, root):
        self.root = Path(root)
        self.debug = False
    def get_cache_path(self, path):
        return str(self.root / path)
    def log(self, *args):
        pass
    def dbg(self, *args):
        pass
    def err(self, *args):
        pass
    def error_message(self, error):
        return str(error)


class Database:
    custom_metadata_dto = GeminiDB.custom_metadata_dto
    def __init__(self, rows):
        self.rows = {d['id']: copy.deepcopy(d) for d in rows}
    def clone(self):
        return self
    def get_document(self, ident, user=None):
        doc = self.rows.get(ident)
        return copy.deepcopy(doc) if doc and doc.get('user') == user else None
    async def update_document_async(self, ident, patch, user=None):
        self.update_document(ident, patch, user)
    def update_document(self, ident, patch, user=None, wait_for_commit=False):
        assert self.rows[ident]['user'] == user
        self.rows[ident].update(copy.deepcopy(patch))
        self.rows[ident]['updatedAt'] += 1


class GeminiSyncRepairTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ctx = Context(self.temp.name)
        self.deleted = []
        self.fail = False
        def delete(name, config):
            if self.fail:
                raise GeminiApiError(503, 'cleanup unavailable')
            self.deleted.append(name)
        self.client = SimpleNamespace(file_search_stores=SimpleNamespace(documents=SimpleNamespace(delete=delete)))
    def tearDown(self):
        self.temp.cleanup()
    def document(self, ident, name=None):
        return {'id': ident, 'user': 'alice', 'filestoreId': 1, 'name': name or f'remote/{ident}',
                'displayName': f'{ident}.md', 'hash': 'shared-content', 'category': 'docs',
                'uploadedAt': 'completed', 'updatedAt': 1, 'state': 'DUPLICATE_FILE'}
    def remote(self, local, name=None, state='STATE_ACTIVE', content_hash=None):
        metadata = to_custom_metadata(local)
        if content_hash:
            metadata = [dict(item, string_value=content_hash) if item['key'] == 'hash' else item for item in metadata]
        return _object({'name': name or local['name'], 'display_name': local['displayName'],
                        'state': state, 'custom_metadata': metadata})
    def cache(self, doc):
        doc['url'] = f'/~cache/{doc["id"]}.md'
        (self.ctx.root / f'{doc["id"]}.md').write_text('source')
    async def sync(self, db, remotes):
        return await reconcile.repair_documents(self.ctx, db, self.client,
            copy.deepcopy(list(db.rows.values())), remotes, 'alice')

    async def test_sync_keeps_healthy_copy_and_removes_failed_and_active_duplicates(self):
        doc = self.document(1); doc.update(error='old error', uploadOperationName='operations/old')
        db = Database([doc])
        result = await self.sync(db, [self.remote(doc), self.remote(doc, 'remote/failed', 'STATE_FAILED'),
                                     self.remote(doc, 'remote/extra')])
        self.assertCountEqual(self.deleted, ['remote/failed', 'remote/extra'])
        self.assertEqual(db.rows[1]['name'], doc['name'])
        self.assertEqual(db.rows[1]['state'], 'STATE_ACTIVE')
        self.assertIsNone(db.rows[1]['error'])
        self.assertIsNone(db.rows[1]['uploadOperationName'])
        self.assertEqual(result['removed'], 2)
        self.assertFalse(result['duplicate_docs'])
        self.assertFalse(result['pending_uploads'])

    async def test_same_hash_different_identities_and_unknown_remote_copy_are_preserved(self):
        a, b, other = self.document(1), self.document(2), self.document(99)
        db = Database([a, b])
        result = await self.sync(db, [self.remote(a), self.remote(b), self.remote(other)])
        self.assertFalse(self.deleted)
        self.assertEqual(len(result['local_missing']), 1)
        self.assertFalse(result['duplicate_docs'])
        self.assertEqual(db.rows[1]['name'], a['name'])
        self.assertEqual(db.rows[2]['name'], b['name'])

    async def test_failed_uploads_get_fresh_receipts_without_deleting_fallbacks(self):
        failed, changed = self.document(1), self.document(2)
        self.cache(failed); self.cache(changed)
        failed.update(error='timeout', uploadedAt=None, uploadOperationName='operations/failed', uploadOperationHash='receipt')
        stale = self.remote(changed); stale['display_name'] = 'Old title'
        changed.update(uploadedAt=None, category='edited')
        db = Database([failed, changed])
        result = await self.sync(db, [self.remote(failed, state='STATE_FAILED'),
            self.remote(failed, 'remote/failed-extra', 'STATE_FAILED'),
            self.remote(failed, 'remote/healthy-old-content', content_hash='old-content'), stale,
            self.remote(changed, 'remote/old-version', content_hash='old-content')])
        self.assertFalse(self.deleted)
        self.assertIsNone(db.rows[1]['uploadOperationName'])
        self.assertIsNone(db.rows[1]['error'])
        self.assertIn('remote/failed-extra', db.rows[1]['pendingDeleteNames'])
        self.assertEqual(db.rows[1]['name'], 'remote/healthy-old-content')
        self.assertEqual(db.rows[2]['displayName'], changed['displayName'])
        self.assertEqual(db.rows[2]['category'], 'edited')
        self.assertEqual(db.rows[2]['name'], changed['name'])
        self.assertEqual(len(result['pending_uploads']), 2)

    async def test_delete_failure_remains_visible_and_is_retried_by_the_next_sync(self):
        doc = self.document(1); db = Database([doc])
        remotes = [self.remote(doc), self.remote(doc, 'remote/extra')]
        self.fail = True
        result = await self.sync(db, remotes)
        self.assertEqual(db.rows[1]['pendingDeleteNames'], ['remote/extra'])
        self.assertEqual(db.rows[1]['state'], 'DUPLICATE_FILE')
        self.assertEqual(len(result['errors']), 1)
        self.fail = False
        result = await self.sync(db, remotes)
        self.assertIsNone(db.rows[1]['pendingDeleteNames'])
        self.assertEqual(db.rows[1]['state'], 'STATE_ACTIVE')
        self.assertFalse(result['errors'])

    async def test_inflight_upload_does_not_block_sync_or_lose_its_receipt(self):
        doc = self.document(1); doc.update(uploadedAt=None, uploadOperationName='operations/inflight')
        db = Database([doc]); lock = reconcile.document_lock(self.ctx, 1)
        lock.acquire()
        try:
            result = await asyncio.wait_for(self.sync(db, [self.remote(doc)]), 1)
            self.assertEqual(db.rows[1]['uploadOperationName'], 'operations/inflight')
            self.assertEqual(len(result['pending_uploads']), 1)
            self.assertFalse(self.deleted)
        finally:
            lock.release()

    async def test_missing_cache_does_not_delete_the_only_available_remote_copy(self):
        doc = self.document(1); db = Database([doc])
        result = await self.sync(db, [self.remote(doc, state='STATE_FAILED')])
        self.assertFalse(self.deleted)
        self.assertFalse(result['pending_uploads'])
        self.assertIn('local cached file is missing', result['errors'][0])

    async def test_worker_cleans_all_superseded_copies_only_after_replacement_completes(self):
        doc = self.document(1); doc.update(uploadedAt=None, pendingDeleteNames=['remote/old', 'remote/current'], name='remote/current')
        db = Database([doc]); worker = UploadWorker(self.ctx, db, self.client)
        worker._cleanup_remote_copies(doc, db)
        self.assertFalse(self.deleted)
        doc['uploadedAt'] = 'done'; db.rows[1] = copy.deepcopy(doc)
        self.fail = True; worker._cleanup_remote_copies(doc, db)
        self.assertEqual(db.rows[1]['pendingDeleteNames'], ['remote/old'])
        self.fail = False; worker._cleanup_remote_copies(db.get_document(1, 'alice'), db)
        self.assertEqual(self.deleted, ['remote/old'])
        self.assertIsNone(db.rows[1]['pendingDeleteNames'])

    async def test_running_worker_picks_up_an_already_processed_row_requeued_by_sync(self):
        doc = self.document(1); db = Database([doc]); worker = UploadWorker(self.ctx, db, self.client)
        calls, processed = [], []
        def pending(limit, exclude_ids):
            calls.append(1)
            if len(calls) == 2:
                worker.start()  # Requests another pass while the worker still owns this run.
                return []
            return [doc] if len(calls) <= 3 and doc['id'] not in exclude_ids else []
        db.get_pending_documents = pending
        db.get_pending_remote_deletes = lambda: []
        worker.process_doc = lambda doc, db: processed.append(doc['id'])
        worker._process = lambda doc: processed.append(doc['id'])
        worker.refresh_filestores = lambda ids: None
        worker.running = True
        worker.run()
        self.assertEqual(processed, [1, 1])
        self.assertFalse(worker.running)

    async def test_cleanup_receipts_are_added_to_existing_sqlite_schema_and_hidden_from_dto(self):
        path = self.ctx.root / 'gemini.sqlite'
        db = GeminiDB(self.ctx, path); db.db.close()
        with sqlite3.connect(path) as connection:
            connection.execute('ALTER TABLE document DROP COLUMN pendingDeleteNames')
        db = GeminiDB(self.ctx, path)
        try:
            ident = await db.create_document_async({'filestoreId': 1, 'displayName': 'one',
                'pendingDeleteNames': ['remote/obsolete']}, user='alice')
            self.assertIsNotNone(db.get_document(ident, 'alice')['pendingDeleteNames'])
            dto = db.to_dto(db.get_document(ident, 'alice'), ['pendingDeleteNames'])
            self.assertNotIn('pendingDeleteNames', dto)
        finally:
            db.db.close()


if __name__ == '__main__':
    unittest.main()
