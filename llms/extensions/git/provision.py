"""Non-interactive Git provisioning for new, isolated project directories."""
import asyncio
import contextlib
import os
import re
import signal
from urllib.parse import urlsplit

from aiohttp import web


def repository_source(source):
    url = source.get('url', '')
    if not isinstance(url, str) or len(url) > 2048:
        raise web.HTTPBadRequest(text='Enter a valid repository URL.')
    url = url.strip()
    if not url or any(c.isspace() or ord(c) < 32 for c in url) or url.startswith('-'):
        raise web.HTTPBadRequest(text='Enter an HTTPS or SSH repository URL.')
    try:
        parsed = urlsplit(url)
    except ValueError:
        raise web.HTTPBadRequest(text='Enter a valid repository URL.') from None
    scp = re.fullmatch(r'([\w.-]+)@([\w.-]+):([\w./~-]+)', url)
    if not scp:
        try:
            valid = (parsed.scheme in ('https', 'ssh') and parsed.hostname
                     and not parsed.hostname.startswith('-') and parsed.path.strip('/')
                     and not parsed.password and not parsed.query and not parsed.fragment
                     and (not parsed.username or (re.fullmatch(r'[\w.-]+', parsed.username) and not parsed.username.startswith('-')))
                     and (parsed.scheme != 'https' or not parsed.username))
            parsed.port  # Validate port syntax.
        except ValueError:
            valid = False
        if not valid:
            raise web.HTTPBadRequest(text='Use an HTTPS or SSH clone URL without embedded credentials.')
    if parsed.hostname == 'github.com' and not scp and len(parsed.path.strip('/').split('/')) != 2:
        raise web.HTTPBadRequest(text='Use the GitHub repository URL, rather than a file or issue URL.')
    branch = source.get('branch') or None
    if branch is not None:
        if (not isinstance(branch, str) or len(branch) > 200 or branch.startswith(('-', '/'))
                or re.search(r'[\s~^:?*\[\\\x00-\x1f]', branch) or '..' in branch or '@{' in branch
                or branch.endswith(('/', '.', '.lock')) or '//' in branch):
            raise web.HTTPBadRequest(text='Enter a valid branch name.')
    return {'kind': 'clone', 'url': url, 'branch': branch}


class ProvisionError(Exception):
    pass


class GitProvisioner:
    def __init__(self, executable, *, local=False, hosts=None, timeout=600):
        self.executable = executable
        self.local = local
        self.hosts = [host.lower() for host in (hosts if hosts is not None else ['github.com', 'gitlab.com', 'bitbucket.org'])]
        self.timeout = timeout

    def validate(self, source):
        source = repository_source(source)
        hostname = urlsplit(source['url']).hostname
        if not hostname:
            hostname = source['url'].split('@', 1)[1].split(':', 1)[0]
        if not self.local and hostname.lower() not in self.hosts:
            raise web.HTTPBadRequest(text='This Git host is not enabled on this server.')
        if not self.local and not source['url'].startswith('https://'):
            raise web.HTTPBadRequest(text='This server supports HTTPS clones. SSH is available in local mode.')
        return source

    async def provision(self, directory, source, progress, cancelled, child):
        # No repository hooks/templates, interactive prompts, or implicit LFS checkout.
        env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        env.update(GIT_TERMINAL_PROMPT='0', GIT_ASKPASS='', SSH_ASKPASS='', GIT_LFS_SKIP_SMUDGE='1',
                   GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_SSH_COMMAND='ssh -oBatchMode=yes -oStrictHostKeyChecking=yes')
        config = ['-c', 'core.hooksPath=' + os.devnull, '-c', 'core.fsmonitor=false',
                  '-c', 'protocol.allow=never', '-c', 'protocol.https.allow=always',
                  '-c', 'protocol.ssh.allow=always', '-c', 'init.templateDir=',
                  '-c', 'credential.interactive=false']
        if self.local:
            # Local users may use their existing credential helper/SSH identity; do not
            # borrow machine credentials in a hosted multi-user process.
            env.pop('GIT_CONFIG_GLOBAL')
            env.pop('GIT_CONFIG_NOSYSTEM')
        if source['kind'] == 'clone':
            args = ['clone', '--progress', '--template=', '--no-recurse-submodules']
            if source.get('branch'):
                args += ['--branch', source['branch']]
            args += ['--', source['url'], directory]
        else:
            args = ['init', '--quiet', '--template=', directory]

        proc = await asyncio.create_subprocess_exec(
            self.executable, *config, *args, env=env,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name != 'nt')
        child(proc.pid)
        diagnostic = bytearray()

        async def consume():
            while chunk := await proc.stderr.read(1024):
                diagnostic.extend(chunk)
                if len(diagnostic) > 16000:
                    del diagnostic[:-16000]
                # Publish only recognized phases/numbers, never arbitrary remote output.
                output = chunk.decode('utf-8', errors='replace')
                for label in ('Receiving objects', 'Resolving deltas', 'Updating files'):
                    matches = re.findall(re.escape(label) + r':\s*(\d{1,3})%', output)
                    if matches:
                        progress(label, min(100, int(matches[-1])))

        reader = asyncio.create_task(consume())
        waiter = asyncio.create_task(proc.wait())
        try:
            deadline = asyncio.get_running_loop().time() + self.timeout
            while proc.returncode is None:
                if cancelled():
                    raise asyncio.CancelledError
                if asyncio.get_running_loop().time() >= deadline:
                    raise ProvisionError('Cloning took too long. Check your connection and try again.')
                try:
                    await asyncio.wait_for(asyncio.shield(waiter), .25)
                except TimeoutError:
                    pass
            await asyncio.wait_for(asyncio.shield(reader), 5)
            if proc.returncode:
                message = diagnostic.decode('utf-8', errors='replace').lower()
                if any(term in message for term in ('authentication', 'permission denied', 'could not read username', 'terminal prompts disabled')):
                    raise ProvisionError('Authentication required. Configure Git credentials on this machine and retry.'
                                         if self.local else 'Authentication required. This server currently supports public repositories.')
                if 'remote branch' in message:
                    raise ProvisionError('That branch was not found. Check its name or use the default branch.')
                if any(term in message for term in ('repository not found', 'not found', 'does not exist')):
                    raise ProvisionError('Repository not found or access denied. Check the URL and your Git credentials.')
                raise ProvisionError('Unable to clone this repository. Check the URL, connection and access permissions.'
                                     if source['kind'] == 'clone' else 'Unable to initialize Git. Check folder permissions and try again.')
            if source['kind'] == 'new':
                # Works with Git versions predating init --initial-branch.
                branch = await asyncio.create_subprocess_exec(
                    self.executable, *config, '-C', directory, 'symbolic-ref', 'HEAD', 'refs/heads/main',
                    env=env, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                if await branch.wait():
                    raise ProvisionError('Unable to set the initial Git branch.')
        finally:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    if os.name == 'nt':
                        terminator = await asyncio.create_subprocess_exec(
                            'taskkill', '/PID', str(proc.pid), '/T', '/F',
                            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                        await terminator.wait()
                        if proc.returncode is None:
                            proc.kill()
                    else:
                        os.killpg(proc.pid, signal.SIGKILL)
                await proc.wait()
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader
            child(None)
