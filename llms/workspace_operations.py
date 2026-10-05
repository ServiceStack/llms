"""Cross-process coordination between local Git writes and agent submissions."""
from contextlib import contextmanager
import hashlib
import os

from aiohttp import web


@contextmanager
def operation_lock(directory, key, shared=False):
    os.makedirs(directory, exist_ok=True)
    filename = hashlib.sha256(key.encode()).hexdigest() + '.lock'
    path = os.path.join(directory, filename)
    if os.path.islink(path):
        raise web.HTTPForbidden(text='Invalid repository operation lock')
    with open(path, 'a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            if stream.tell() == 0:
                stream.write(b'0')
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise web.HTTPConflict(text='A repository operation is in progress. Try again shortly.') from exc
        else:
            import fcntl
            try:
                fcntl.flock(stream, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise web.HTTPConflict(text='A repository operation is in progress. Try again shortly.') from exc
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def workspace_submission_lock(data_root, shared=False):
    # Also covers repositories shared by projects or users. Held only during submission/write.
    return operation_lock(os.path.join(data_root, '.workspace-locks'), 'agent-submissions', shared=shared)
