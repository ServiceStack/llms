import asyncio
import copy
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import web
from llms.extensions.git import install
from llms.extensions.git.messages import CommitMessageGenerator, staged_diff
from llms.extensions.git.operations import git, repository_state


@unittest.skipUnless(shutil.which('git'), 'Git unavailable')
class StagedDiffTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repo = self.directory.name
        self.git('init', '-q')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.com')
        Path(self.repo, 'one.txt').write_text('First staged change\n')
        Path(self.repo, 'two.txt').write_text('Second staged change\n')
        self.git('add', '--', 'one.txt', 'two.txt')

    def tearDown(self):
        self.directory.cleanup()

    def git(self, *args):
        result = git(self.repo, *args, local=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def snapshot(self):
        return staged_diff([self.repo], self.repo, repository_state(self.repo)['indexRevision'])

    def test_full_diff_includes_all_staged_files_and_leaves_repo_untouched(self):
        before = repository_state(self.repo)
        Path(self.repo, 'one.txt').write_text('Later unstaged edit\n')
        Path(self.repo, 'unstaged.txt').write_text('Untracked content\n')
        snapshot = self.snapshot()
        self.assertIn('+First staged change', snapshot['diff'])
        self.assertIn('+Second staged change', snapshot['diff'])
        self.assertNotIn('Later unstaged edit', snapshot['diff'])
        self.assertNotIn('Untracked content', snapshot['diff'])
        self.assertEqual(snapshot['indexRevision'], before['indexRevision'])
        self.assertEqual(repository_state(self.repo), before)

    def test_deleted_renamed_and_binary_changes_are_included(self):
        self.git('commit', '-qm', 'Initial')
        Path(self.repo, 'one.txt').rename(Path(self.repo, 'renamed.txt'))
        Path(self.repo, 'two.txt').unlink()
        Path(self.repo, 'image.png').write_bytes(b'\0binary image\0')
        self.git('add', '-A')
        diff = self.snapshot()['diff']
        for name in ('one.txt', 'renamed.txt', 'two.txt', 'image.png'):
            self.assertIn(name, diff)
        self.assertIn('Binary files', diff)
        self.assertIn('-Second staged change', diff)

    def test_stale_empty_oversized_and_outside_paths_are_rejected(self):
        with self.assertRaises(web.HTTPConflict):
            staged_diff([self.repo], self.repo, 'old-index')
        with patch('llms.extensions.git.messages.MAX_DIFF_BYTES', 16):
            with self.assertRaises(web.HTTPBadRequest):
                self.snapshot()
        if os.name != 'nt':
            Path(self.repo, 'outside.txt').symlink_to('/etc/passwd')
            self.git('add', '--', 'outside.txt')
            with self.assertRaises(web.HTTPForbidden):
                self.snapshot()
            self.git('rm', '--cached', '--', 'outside.txt')
        self.git('rm', '--cached', '--', 'one.txt', 'two.txt')
        with self.assertRaises(web.HTTPBadRequest):
            self.snapshot()

    def test_changing_index_during_read_is_rejected(self):
        from llms.extensions import git as extension
        original = extension.run_git
        def changing_diff(repo, *args, **options):
            result = original(repo, *args, **options)
            if '--unified=3' in args:
                Path(self.repo, 'two.txt').write_text('Changed during read\n')
                self.git('add', '--', 'two.txt')
            return result
        with patch.object(extension, 'run_git', side_effect=changing_diff):
            with self.assertRaises(web.HTTPConflict):
                self.snapshot()


class CommitGenerationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.provider = SimpleNamespace(provider_model=lambda model: model == 'fake', model_info=lambda _: {'limit': {'context': 32768}},
            chat=AsyncMock(return_value={'choices': [{'message': {'content': 'Improve the project workflow'}}]}))
        self.template = {'model': 'fake', 'messages': [{'role': 'system', 'content': 'Custom summary instructions'},
            {'role': 'user', 'content': 'Explain the purpose:\n{diffs}'}], 'stream': True,
            'tools': ['not allowed'], 'metadata': {'projectId': 'wrong'}, 'threadId': 123, 'projectId': 'wrong', 'tool_choice': 'auto'}
        self.ctx = SimpleNamespace(config={'defaults': {'commit': copy.deepcopy(self.template)}},
            get_providers=lambda: {'fake': self.provider}, dbg=Mock())
        self.generator = CommitMessageGenerator(self.ctx)
        self.snapshot = {'diff': 'diff one\n-old\n+new\ndiff two\n+second file\n', 'indexRevision': 'reviewed-index'}

    async def test_custom_template_all_diffs_and_isolated_provider_request(self):
        message = await self.generator.generate(self.snapshot, 'alice')
        self.assertEqual(message, 'Improve the project workflow')
        chat = self.provider.chat.call_args.args[0]
        context = self.provider.chat.call_args.kwargs['context']
        self.assertEqual(chat['messages'][0]['content'], 'Custom summary instructions')
        self.assertEqual(chat['messages'][1]['content'], 'Explain the purpose:\n' + self.snapshot['diff'])
        self.assertFalse(chat['stream'])
        for key in ('tools', 'metadata', 'threadId', 'projectId', 'tool_choice'):
            self.assertNotIn(key, chat)
        self.assertEqual((context['user'], context['tools'], context['nohistory']), ('alice', 'none', True))
        self.assertEqual(context['purpose'], 'git_commit_message')
        self.assertEqual(self.ctx.config['defaults']['commit'], self.template)

    async def test_user_prompt_without_placeholder_gets_diff_appended(self):
        self.ctx.config['defaults']['commit']['messages'][-1]['content'] = 'Write a release-focused subject'
        await self.generator.generate(self.snapshot, None)
        self.assertEqual(self.provider.chat.call_args.args[0]['messages'][-1]['content'],
                         'Write a release-focused subject\n\n' + self.snapshot['diff'])

    async def test_context_overflow_preserves_full_diff_and_does_not_call_model(self):
        self.provider.model_info = lambda _: {'limit': {'context': 32}}
        with self.assertRaises(web.HTTPBadRequest):
            await self.generator.generate(self.snapshot, 'alice')
        self.provider.chat.assert_not_called()

    async def test_failure_timeout_and_invalid_outputs(self):
        self.provider.chat.side_effect = ConnectionError('secret provider diagnostic')
        with self.assertRaises(web.HTTPBadGateway) as error:
            await self.generator.generate(self.snapshot, 'alice')
        self.assertNotIn('secret', error.exception.text)
        self.provider.chat.side_effect = None
        for response in ({'choices': []}, {'choices': [{'message': {'content': ''}}]}, None):
            self.provider.chat.return_value = response
            with self.assertRaises(web.HTTPBadGateway):
                await self.generator.generate(self.snapshot, 'alice')
        async def slow(*args, **kwargs):
            await asyncio.sleep(1)
        self.provider.chat.side_effect = slow
        self.generator.TIMEOUT = .001
        with self.assertRaises(web.HTTPGatewayTimeout):
            await self.generator.generate(self.snapshot, 'alice')

    async def test_template_fallback_explicit_disable_and_missing_model(self):
        del self.ctx.config['defaults']['commit']
        template = self.generator.template()
        self.assertEqual(template['model'], 'openai/gpt-oss-120b')
        self.assertIn('{diffs}', template['messages'][-1]['content'])
        self.ctx.config['defaults']['commit'] = None
        with self.assertRaises(web.HTTPBadRequest):
            await self.generator.generate(self.snapshot, 'alice')
        self.ctx.config['defaults']['commit'] = {'model': 'missing', 'messages': []}
        with self.assertRaises(web.HTTPServiceUnavailable):
            await self.generator.generate(self.snapshot, 'alice')


class CommitMessageRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_project_and_home_scope_authentication_and_stale_results(self):
        ctx = Mock(config={'defaults': {'commit': {'model': 'fake', 'messages': []}}})
        ctx.check_auth.return_value = (True, None)
        ctx.get_username.return_value = 'alice'
        ctx.is_admin.return_value = False
        ctx.projects.resolve_explorer_workspace.return_value = {'directories': ['/allowed']}
        install(ctx)
        handler = next(call.args[1] for call in ctx.add_post.call_args_list if call.args[0] == 'repositories/message')
        for project in ('p', None):
            body = {'projectId': project, 'path': '/allowed', 'indexRevision': 'reviewed-index'}
            async def json_body():
                return body
            request = SimpleNamespace(json=json_body)
            with patch('llms.extensions.git.messages.staged_diff', return_value={'diff': 'All changes', 'indexRevision': 'reviewed-index'}) as diff, \
                 patch.object(CommitMessageGenerator, 'generate', new_callable=AsyncMock, return_value='Improve project') as generate, \
                 patch('llms.extensions.git.git_directory', return_value=(['/allowed'], '/allowed', '/allowed', '/allowed')), \
                 patch('llms.extensions.git.operations.repository_state', return_value={'indexRevision': 'reviewed-index'}) as state:
                response = await handler(request)
                self.assertIn('Improve project', response.text)
                diff.assert_called_once_with(['/allowed'], '/allowed', 'reviewed-index')
                generate.assert_awaited_once_with({'diff': 'All changes', 'indexRevision': 'reviewed-index'}, 'alice')
                ctx.projects.resolve_explorer_workspace.assert_called_with(project, 'alice', is_admin=False)
                state.return_value = {'indexRevision': 'changed-index'}
                with self.assertRaises(web.HTTPConflict):
                    await handler(request)
        ctx.check_auth.return_value = (False, None)
        ctx.error_auth_required = {}
        self.assertEqual((await handler(request)).status, 401)
