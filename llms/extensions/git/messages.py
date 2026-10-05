"""Generate editable commit messages from the full staged diff, without chat history or tools."""
import asyncio
import copy
import json
import re
import subprocess
from importlib.resources import files

from aiohttp import web
from llms.db import count_tokens_approx
from .operations import repository_state, validate_paths, validate_repo

MAX_DIFF_BYTES = 1024 * 1024


def staged_diff(roots, path, index_revision):
    from . import GitOutputTooLarge, run_git
    roots, path, root, repo = validate_repo(roots, path)
    revision = repository_state(repo)['indexRevision']
    if index_revision != revision:
        raise web.HTTPConflict(text='Staged changes have changed. Refresh before generating a commit message.')
    try:
        names = run_git(repo, 'diff', '--cached', '--name-only', '--no-renames', '-z', '--')
        if names.returncode:
            raise web.HTTPBadRequest(text='Staged changes are unavailable')
        paths = [name for name in names.stdout.split('\0') if name]
        if not paths:
            raise web.HTTPBadRequest(text='Stage changes before generating a commit message')
        validate_paths(repo, root, paths)
        # One patch concatenates every staged file, including additions, deletions, renames and binary notices.
        result = run_git(repo, 'diff', '--cached', '--no-ext-diff', '--no-textconv', '--no-color', '--unified=3', '--', max_output_bytes=MAX_DIFF_BYTES)
        if result.returncode:
            raise web.HTTPBadRequest(text='Staged diffs are unavailable')
        diff = 'Staged files: ' + json.dumps(paths, ensure_ascii=False) + '\n\n' + result.stdout
        if len(diff.encode('utf-8')) > MAX_DIFF_BYTES:
            raise web.HTTPBadRequest(text='The staged diff is too large to summarize. Stage a smaller set of changes or write the message yourself.')
        if repository_state(repo)['indexRevision'] != revision:
            raise web.HTTPConflict(text='Staged changes changed while reading the diff. Refresh and try again.')
        return {'diff': diff, 'indexRevision': revision}
    except GitOutputTooLarge as exc:
        raise web.HTTPBadRequest(text='The staged diff is too large to summarize. Stage a smaller set of changes or write the message yourself.') from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise web.HTTPBadRequest(text='Unable to read staged diffs') from exc


class CommitMessageGenerator:
    TIMEOUT = 30

    def __init__(self, ctx):
        self.ctx = ctx
        self.semaphore = asyncio.Semaphore(2)

    def template(self):
        defaults = self.ctx.config.get('defaults') or {}
        if 'commit' in defaults:
            return copy.deepcopy(defaults['commit'])
        config = json.loads(files('llms').joinpath('llms.json').read_text(encoding='utf-8'))
        return config['defaults']['commit']

    async def generate(self, snapshot, user):
        chat = self.template()
        if not chat:
            raise web.HTTPBadRequest(text='Commit message generation is disabled in defaults.commit')
        if not isinstance(chat, dict) or not isinstance(chat.get('messages', []), list):
            raise web.HTTPBadRequest(text='Configure a valid defaults.commit model and message template')
        model = chat.get('model')
        provider = next((p for p in self.ctx.get_providers().values() if p.provider_model(model)), None) if isinstance(model, str) and model else None
        if provider is None:
            raise web.HTTPServiceUnavailable(text='The commit message model is unavailable. Configure defaults.commit.model in llms.json.')
        info = provider.model_info(model) or {}
        # Keep custom template instructions, but supply repository content only as user data.
        messages = chat.setdefault('messages', [])
        last = messages[-1] if messages else None
        if last and last.get('role') == 'user' and isinstance(last.get('content'), str):
            prompt = last['content']
            last['content'] = prompt.replace('{diffs}', snapshot['diff']) if '{diffs}' in prompt else prompt + '\n\n' + snapshot['diff']
        else:
            messages.append({'role': 'user', 'content': snapshot['diff']})
        chat['stream'] = False
        for key in ('tools', 'tool_choice', 'metadata', 'title', 'threadId', 'submissionId', 'projectId'):
            chat.pop(key, None)
        context_size = (info.get('limit') or {}).get('context') or 32768
        output_budget = chat.get('max_completion_tokens') or chat.get('max_tokens') or 2048
        if count_tokens_approx(messages) + output_budget > context_size:
            raise web.HTTPBadRequest(text='The full staged diff exceeds the commit model context. Use a larger-context model or stage fewer changes.')
        context = {'purpose': 'git_commit_message', 'user': user, 'tools': 'none', 'nohistory': True, 'chat': chat, 'modelInfo': info}

        async def request():
            async with self.semaphore:
                # Like title generation, bypass agent/workspace/persistence filters for this text-only request.
                return await provider.chat(chat, context=context)
        try:
            response = await asyncio.wait_for(request(), self.TIMEOUT)
        except TimeoutError as exc:
            raise web.HTTPGatewayTimeout(text='Commit message generation timed out. Try again or write a message yourself.') from exc
        except Exception as exc:
            self.ctx.dbg('Commit message generation failed: ' + type(exc).__name__)
            raise web.HTTPBadGateway(text='Unable to generate a commit message. Check the configured model and try again.') from exc
        choices = response.get('choices') if isinstance(response, dict) else None
        first = choices[0] if isinstance(choices, list) and choices else None
        result = first.get('message') if isinstance(first, dict) else None
        message = result.get('content') if isinstance(result, dict) else None
        if not isinstance(message, str):
            raise web.HTTPBadGateway(text='The model did not return a commit message')
        message = re.sub(r'^commit message\s*:\s*', '', message.strip(), flags=re.I)
        if message.startswith('```') and message.endswith('```'):
            message = '\n'.join(message.splitlines()[1:-1]).strip()
        if len(message) > 1 and message[0] == message[-1] and message[0] in ('"', "'"):
            message = message[1:-1].strip()
        if not message or len(message) > 10000 or '\0' in message:
            raise web.HTTPBadGateway(text='The model returned an empty or invalid commit message')
        return message
