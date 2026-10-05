import asyncio
import copy
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from llms.execution_context import workspace_scope
from llms.extensions.app.db import AppDB
from llms.extensions.app.titles import TitleWorker, normalize_title
from tests.test_app_db_provider import MockContext


class ThreadContracts(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.directory.name, 'app.sqlite')
        self.ctx = MockContext(self.path)
        self.db = AppDB(self.ctx, self.path)

    def tearDown(self):
        self.db.close()
        self.directory.cleanup()

    def create(self, project=None, user='alice'):
        return self.db.create_thread({'title': 'New Chat', 'projectId': project,
                                     'messages': [{'role': 'user', 'content': 'Hello', 'timestamp': 1}]}, user)

    def test_scoped_keyset_pages_without_history(self):
        ids = [self.create('a') for _ in range(26)]
        self.create('b')
        self.create(None)
        self.create('a', 'bob')
        first = self.db.sidebar_page('alice', 'a', 5)
        second = self.db.sidebar_page('alice', 'a', 10, first['nextCursor'])
        self.assertEqual([x['id'] for x in first['items']], list(reversed(ids))[:5])
        self.assertEqual(len(second['items']), 10)
        self.assertFalse({x['id'] for x in first['items']} & {x['id'] for x in second['items']})
        self.assertNotIn('messages', first['items'][0])
        self.assertEqual(len(self.db.sidebar_page('alice')['items']), 1)
        with self.assertRaises(ValueError):
            self.db.sidebar_page('alice', 'b', 10, first['nextCursor'])
        for limit in ('x', 0, 101):
            with self.assertRaises(ValueError):
                self.db.sidebar_page('alice', limit=limit)

    def test_sidebar_preview_stats_and_canonical_count(self):
        tid = self.create('a')
        self.db.update_thread(tid, {'model': 'test-model', 'inputTokens': 120,
                                   'outputTokens': 30, 'cost': 0.0}, 'alice')
        with self.db.create_writer_connection() as conn:
            conn.execute("UPDATE thread SET messages='[]' WHERE id=?", (tid,))
            conn.commit()
        row = self.db.sidebar_page('alice', 'a')['items'][0]
        self.assertEqual(row['messageCount'], 1)
        self.assertEqual(row['model'], 'test-model')
        self.assertEqual((row['inputTokens'], row['outputTokens'], row['cost']), (120, 30, 0.0))
        self.assertNotIn('messages', row)

    def test_legacy_reads_import_once_and_preserve_history(self):
        tid = self.create('a')
        legacy = json.dumps([{'role': 'user', 'content': 'Plan'},
                             {'role': 'assistant', 'content': 'Saved plan'}])
        with self.db.create_writer_connection() as conn:
            conn.execute('DELETE FROM chat_message WHERE threadId=?', (tid,))
            conn.execute('UPDATE thread SET messages=? WHERE id=?', (legacy, tid))
            conn.commit()
        self.assertEqual(len(self.db.get_chat_message_window(str(tid))), 2)
        self.assertEqual(self.db.get_chat_message_bounds(tid)['messageCount'], 2)
        self.assertEqual(len(self.db.get_chat_message_page(tid)), 2)
        self.assertEqual(self.db.get_thread(tid, 'alice')['messages'], legacy)
        with self.db.create_writer_connection() as conn:
            conn.execute('UPDATE chat_message SET active=0 WHERE threadId=?', (tid,))
            conn.commit()
        self.assertEqual(self.db.get_chat_message_window(tid), [])
        self.assertEqual(self.db.get_chat_message_bounds(tid)['messageCount'], 0)

    def test_project_folders_require_messages(self):
        self.db.create_thread({'title': 'Empty', 'projectId': 'empty', 'messages': []}, 'alice')
        active = self.create('active')
        self.db.create_thread({'title': 'New empty chat', 'projectId': 'active', 'messages': []}, 'alice')
        self.create('other-user', 'bob')
        self.assertEqual(self.db.project_ids_with_messages('alice'), {'active'})
        self.assertEqual(self.db.project_ids_with_messages('bob'), {'other-user'})
        self.assertEqual([row['id'] for row in self.db.sidebar_page('alice', 'active')['items']], [active])
        # Compatibility history still counts if normalized messages have not been imported.
        legacy = self.db.create_thread({'title': 'Legacy', 'projectId': 'legacy',
                                        'messages': [{'role': 'user', 'content': 'Hello'}]}, 'alice')
        with self.db.create_writer_connection() as conn:
            conn.execute('DELETE FROM chat_message WHERE threadId=?', (legacy,))
            conn.commit()
        self.assertEqual(self.db.project_ids_with_messages('alice'), {'active', 'legacy'})
        self.assertEqual([row['id'] for row in self.db.sidebar_page('alice', 'legacy')['items']], [legacy])

    def test_rename_and_move_do_not_touch_history_or_activity(self):
        tid = self.create('a')
        before = self.db.get_thread(tid, 'alice')
        canonical = self.db.get_chat_messages(tid)
        self.assertEqual(self.db.rename_thread(tid, 'Manual title', 'bob'), 0)
        self.assertEqual(self.db.rename_thread(tid, 'Manual title', 'alice'), 1)
        self.assertEqual(self.db.move_thread(tid, 'b', 0, 'alice'), 1)
        self.assertEqual(self.db.move_thread(tid, None, 0, 'alice'), 0)
        after = self.db.get_thread(tid, 'alice')
        self.assertEqual(before['messages'], after['messages'])
        self.assertEqual(before['lastActivityAt'], after['lastActivityAt'])
        self.assertEqual(canonical, self.db.get_chat_messages(tid))
        self.assertEqual(after['metadataVersion'], 2)
        self.assertEqual(after['titleSource'], 'manual')

    def test_active_run_prevents_move_and_orphans_reconcile(self):
        tid = self.create('a')
        self.db.create_agent_run(tid, 'alice', 'model')
        self.assertEqual(self.db.move_thread(tid, 'b', 0, 'alice'), 0)
        self.db.reconcile_projects([], 'bob')
        self.assertEqual(self.db.get_thread(tid, 'alice')['projectId'], 'a')
        self.db.reconcile_projects([], 'alice')
        self.assertIsNone(self.db.get_thread(tid, 'alice')['projectId'])

    def test_sidebar_reports_only_active_run_status(self):
        tid = self.create('a')
        run_id = self.db.create_agent_run(tid, 'alice', 'model')
        self.assertEqual(self.db.sidebar_page('alice', 'a')['items'][0]['runStatus'], 'queued')
        self.db.update_agent_run(run_id, {'status': 'failed'})
        self.assertIsNone(self.db.sidebar_page('alice', 'a')['items'][0]['runStatus'])

    def test_reopen_preserves_membership_title_and_activity(self):
        tid = self.create('a')
        before = self.db.get_thread(tid, 'alice')
        self.db.close()
        self.db = AppDB(self.ctx, self.path)
        after = self.db.get_thread(tid, 'alice')
        for field in ('projectId', 'title', 'titleSource', 'lastActivityAt', 'messages'):
            self.assertEqual(after[field], before[field])

    def test_title_job_manual_rename_wins(self):
        test = self
        class Provider:
            def provider_model(self, model): return True
            def model_info(self, model): return {}
            async def chat(self, request, context):
                test.assertNotIn('threadId', context)
                test.assertNotIn('tools', request)
                test.db.rename_thread(tid, 'Manual wins', 'alice')
                return {'choices': [{'message': {'content': 'Generated title'}}]}
        self.ctx.config['defaults']['summarize'] = {'model': 'fake', 'messages': []}
        self.ctx.get_providers = lambda: {'fake': Provider()}
        tid = self.db.create_thread({'title': 'fallback', 'titleSource': 'fallback'}, 'alice')
        worker = TitleWorker(self.db, self.ctx, lambda _: None)
        thread = self.db.get_thread(tid, 'alice')
        messages = [{'role': 'user', 'content': 'First prompt'}]

        async def run():
            first = worker.enqueue(thread, messages, 'alice')
            # A retried submission must not start a second title request.
            self.assertIsNone(worker.enqueue(thread, messages, 'alice'))
            await first
        asyncio.run(run())
        self.assertEqual(self.db.get_thread(tid, 'alice')['title'], 'Manual wins')

    def test_title_generated_and_resumed_after_restart(self):
        requests = []
        class Provider:
            def provider_model(self, model): return True
            def model_info(self, model): return {}
            async def chat(self, request, context):
                requests.append(request['messages'][-1]['content'])
                return {'choices': [{'message': {'content': '"Generated title"'}}]}
        self.ctx.config['defaults']['summarize'] = {'model': 'fake', 'messages': []}
        self.ctx.get_providers = lambda: {'fake': Provider()}
        tid = self.db.create_thread({'title': 'fallback', 'titleSource': 'fallback',
                                     'messages': [{'role': 'user', 'content': 'Plan a trip', 'timestamp': 1}]}, 'alice')
        # Simulate a restart after the title was claimed but before it completed.
        TitleWorker(self.db, self.ctx, lambda _: None).set_status(tid, 'pending')
        worker = TitleWorker(self.db, self.ctx, lambda _: None)

        async def run():
            worker.start()
            await asyncio.gather(*worker.tasks)
        asyncio.run(run())
        thread = self.db.get_thread(tid, 'alice')
        self.assertEqual(requests, ['Plan a trip'])
        self.assertEqual((thread['title'], thread['titleSource'], thread['titleStatus']),
                         ('Generated title', 'generated', 'complete'))

    def test_missing_summarize_uses_packaged_template(self):
        # configs created before titles existed have no defaults.summarize
        self.ctx.config['defaults'].pop('summarize', None)
        template = TitleWorker(self.db, self.ctx, lambda _: None).template()
        self.assertEqual(template['model'], 'openai/gpt-oss-120b')
        template['model'] = 'changed'  # callers get their own copy
        self.assertEqual(TitleWorker(self.db, self.ctx, lambda _: None).template()['model'], 'openai/gpt-oss-120b')
        # an explicit null still disables titles
        self.ctx.config['defaults']['summarize'] = None
        self.assertIsNone(TitleWorker(self.db, self.ctx, lambda _: None).template())

    def test_title_normalization(self):
        self.assertEqual(normalize_title('Title: "A\n useful title"'), 'A useful title')
        self.assertIsNone(normalize_title(''))


class SidebarBootstrap(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import llms.extensions.app as app
        self.app = app
        self.original_db = app.g_db
        self.directory = tempfile.TemporaryDirectory()
        self.ctx = MockContext(os.path.join(self.directory.name, 'app.sqlite'))
        self.db = AppDB(self.ctx, self.ctx.db_path)
        app.g_db = self.db
        self.routes = {}
        self.ctx.add_get = lambda path, handler, **_: self.routes.update({path: handler})
        self.projects = [
            {'id': 'one', 'name': 'One'},
            {'id': 'two', 'name': 'Two'},
            {'id': 'empty', 'name': 'Empty'},
            {'id': 'hidden', 'name': 'Hidden', 'showInSidebar': False},
        ]
        self.ctx.projects = SimpleNamespace(get_user_projects=lambda user: self.projects)
        app.install(self.ctx)

    def tearDown(self):
        self.db.close()
        self.app.g_db = self.original_db
        self.directory.cleanup()

    async def test_initial_response_pages_each_visible_project_and_reports_membership(self):
        ids = {}
        for project, count in [('one', 7), ('two', 3), ('hidden', 1)]:
            ids[project] = [self.db.create_thread({
                'title': f'{project} {i}', 'projectId': project,
                'messages': [{'role': 'user', 'content': 'hello', 'timestamp': i + 1}],
            }, 'test_user') for i in range(count)]
        self.db.create_thread({'title': 'Empty', 'projectId': 'empty', 'messages': []}, 'test_user')
        response = await self.routes['thread-sidebar'](SimpleNamespace())
        payload = json.loads(response.text)
        self.assertEqual([group['id'] for group in payload['projects']], ['one', 'two'])
        self.assertEqual([row['id'] for row in payload['projects'][0]['items']], list(reversed(ids['one']))[:5])
        self.assertTrue(payload['projects'][0]['hasMore'])
        self.assertEqual(len(payload['projects'][1]['items']), 3)
        self.assertTrue(all(row['projectId'] == group['id'] for group in payload['projects']
                            for row in group['items']))


class SharedContracts(unittest.IsolatedAsyncioTestCase):
    async def test_repository_write_blocks_submission_before_history_changes(self):
        import importlib
        from aiohttp import web
        import llms.extensions.app as app_extension
        from llms.workspace_operations import workspace_submission_lock
        main = importlib.import_module('llms.main')
        original_app, original_db = main.g_app, app_extension.g_db
        with tempfile.TemporaryDirectory() as directory:
            host = main.AppExtensions(SimpleNamespace(), {})
            host.config = {'defaults': {}}
            host.get_user_path = lambda user=None: directory
            host.get_username = lambda request: 'alice'
            host.check_auth = lambda request: (True, None)
            context = main.ExtensionContext(host, 'app')
            database = AppDB(context, os.path.join(directory, 'app.sqlite'))
            app_extension.g_db = database
            try:
                app_extension.install(context)
                thread_id = database.create_thread({'title': 'Chat', 'messages': [{'role': 'user', 'content': 'Original'}]}, 'alice')
                before = database.get_thread(thread_id, 'alice')
                messages = database.get_chat_messages(thread_id)
                post = {path: handler for path, handler, _ in host.server_add_post}
                # The submission must fail before even parsing a body or accepting a submissionId.
                request = SimpleNamespace(match_info={'id': str(thread_id)})
                with workspace_submission_lock(directory):
                    with self.assertRaises(web.HTTPConflict):
                        await post['/ext/app/threads/{id}/chat'](request)
                self.assertEqual(database.get_thread(thread_id, 'alice'), before)
                self.assertEqual(database.get_chat_messages(thread_id), messages)
                self.assertIsNone(database.get_active_agent_run(thread_id, 'alice'))
            finally:
                database.close()
                main.g_app, app_extension.g_db = original_app, original_db

    async def test_project_saves_notify_sidebar_across_extension_contexts(self):
        import importlib
        import llms.extensions.app as app_extension
        from llms.extensions.projects import install as install_projects

        main = importlib.import_module('llms.main')
        original_app, original_db = main.g_app, app_extension.g_db
        with tempfile.TemporaryDirectory() as directory:
            host = main.AppExtensions(SimpleNamespace(), {})
            host.config = {'defaults': {}}
            host.get_user_path = lambda user=None: directory
            host.get_username = lambda request: 'alice'
            host.get_user_pref = lambda key, user=None: None
            app_ctx = main.ExtensionContext(host, 'app')
            project_ctx = main.ExtensionContext(host, 'projects')
            database = AppDB(app_ctx, os.path.join(directory, 'app.sqlite'))
            app_extension.g_db = database
            try:
                # Production uses a separate context per extension, sharing only the host.
                install_projects(project_ctx)
                app_extension.install(app_ctx)
                post = {path: handler for path, handler, _ in host.server_add_post}
                get = {path: handler for path, handler, _ in host.server_add_get}
                patch_routes = {path: handler for path, handler, _ in host.server_add_patch}

                def request(data, **match_info):
                    async def body():
                        return data
                    return SimpleNamespace(json=body, match_info=match_info)

                project = {'name': 'One', 'folder': 'one', 'showInSidebar': True}
                response = await post['/ext/projects/save/{name}'](request(project, name='One'))
                project = json.loads(response.text)[0]
                thread_id = database.create_thread({
                    'title': 'Chat', 'projectId': project['id'],
                    'messages': [{'role': 'user', 'content': 'Hello'}],
                }, 'alice')
                before = database.get_thread(thread_id, 'alice')

                async def sidebar():
                    return json.loads((await get['/ext/app/thread-sidebar'](SimpleNamespace())).text)

                self.assertEqual(len((await sidebar())['projects']), 1)
                for save_path in ('/ext/projects/save/{name}', '/ext/projects/projects.json'):
                    with self.subTest(save_path=save_path):
                        await patch_routes['/ext/projects/sidebar/{id}'](
                            request({'showInSidebar': False}, id=project['id']))
                        hidden = await sidebar()
                        self.assertEqual(hidden['projects'], [])
                        signal = app_extension.sidebar_signal.event
                        updates = asyncio.create_task(get['/ext/app/thread-sidebar/updates'](
                            SimpleNamespace(query={'sig': hidden['revision']})))
                        await asyncio.sleep(0)  # Let the subscriber capture the pre-save signal.
                        try:
                            restored = {**project, 'showInSidebar': True}
                            data = [restored] if save_path.endswith('projects.json') else restored
                            await post[save_path](request(data, name='One'))
                            self.assertTrue(signal.is_set(), 'Saving must wake sidebar subscribers')
                            response = await asyncio.wait_for(updates, 3)
                        finally:
                            if not updates.done():
                                updates.cancel()
                                await asyncio.gather(updates, return_exceptions=True)
                        self.assertNotEqual(json.loads(response.text)['revision'], hidden['revision'])
                        visible = await sidebar()
                        self.assertEqual([group['id'] for group in visible['projects']], [project['id']])
                        self.assertEqual(visible['projects'][0]['items'][0]['id'], thread_id)
                after = database.get_thread(thread_id, 'alice')
                self.assertEqual(before['messages'], after['messages'])
                self.assertEqual(before['lastActivityAt'], after['lastActivityAt'])
                workspace = project_ctx.projects.resolve_workspace(project['id'], 'alice')
                run_id = database.create_agent_run(thread_id, 'alice', 'test', workspace=workspace)
                run_before = database.get_agent_run(run_id, 'alice')
                visible = await sidebar()
                signal = app_extension.sidebar_signal.event
                await patch_routes['/ext/projects/archive/{id}'](
                    request({'archived': True}, id=project['id']))
                self.assertTrue(signal.is_set(), 'Archiving must wake sidebar subscribers')
                archived = await sidebar()
                self.assertEqual(archived['projects'], [])
                self.assertEqual(archived['unassigned']['items'], [])
                self.assertNotEqual(visible['revision'], archived['revision'])
                after = database.get_thread(thread_id, 'alice')
                self.assertEqual(after['projectId'], project['id'])
                self.assertEqual(before['messages'], after['messages'])
                self.assertEqual(before['lastActivityAt'], after['lastActivityAt'])
                self.assertEqual(database.get_agent_run(run_id, 'alice'), run_before)
                self.assertEqual(project_ctx.projects.resolve_workspace(project['id'], 'alice'), workspace)
                await patch_routes['/ext/projects/archive/{id}'](
                    request({'archived': False}, id=project['id']))
                self.assertEqual((await sidebar())['projects'][0]['items'][0]['id'], thread_id)
            finally:
                database.close()
                main.g_app, app_extension.g_db = original_app, original_db

    async def test_workspace_context_isolated_across_tasks(self):
        from llms.main import AppExtensions
        app = object.__new__(AppExtensions)
        app.allowed_directories = {'alice': ['/legacy']}
        async def run(path):
            token = workspace_scope.set({'user': 'alice', 'directories': [path]})
            try:
                await asyncio.sleep(0)
                self.assertEqual(app.get_allowed_directories('alice'), [path])
                self.assertEqual(await asyncio.to_thread(app.get_allowed_directories), [path])
            finally:
                workspace_scope.reset(token)
        await asyncio.gather(run('/a'), run('/b'))
        self.assertEqual(app.get_allowed_directories('alice'), ['/legacy'])

    def test_nested_template_copy(self):
        from llms.main import g_chat_request
        template = {'model': 'test', 'messages': [{'role':'user', 'content':[{'type':'text','text':''}]}]}
        original = copy.deepcopy(template)
        with patch('llms.main.g_config', {'defaults': {'summarize': template}}):
            a = g_chat_request('summarize', 'A')
            b = g_chat_request('summarize', 'B')
        self.assertEqual(a['messages'][0]['content'][0]['text'], 'A')
        self.assertEqual(b['messages'][0]['content'][0]['text'], 'B')
        self.assertEqual(template, original)
