"""Export project output for an independent static file server."""

import datetime
import html
import os
import re
import shutil
import stat
import tempfile
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path
from threading import Lock
from urllib.parse import quote, urlsplit

from aiohttp import web

_publication_lock = Lock()
DEFAULT_BASE_URL = ""


@contextmanager
def publication_lock(directory):
    # Separate from the project metadata lock: that lock has its own reentrancy state.
    with _publication_lock:
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, ".publish.lock"), "a+b") as lock:
            if os.name == "nt":
                import msvcrt

                lock.write(b"0")
                lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def within(path, root):
    path, root = (os.path.normcase(os.path.realpath(p)) for p in (path, root))
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def component(value):
    if (
        not isinstance(value, str)
        or not value
        or value in (".", "..")
        or re.search(r'[/\\\x00-\x1f<>:"|?*]', value)
        or value.endswith((" ", "."))
        or re.fullmatch(r"(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", value, re.I)
    ):
        raise web.HTTPBadRequest(text="Invalid static publication directory name")
    return value


def settings(config, startup_directory):
    config = config if isinstance(config, dict) else {}
    value = config
    if not isinstance(value, dict):
        raise ValueError("share_static config must be an object")
    enabled = value.get("enabled", True)
    directory = value.get("directory", "./p")
    if not isinstance(enabled, bool):
        raise ValueError("share_static.enabled must be a boolean")
    if not isinstance(directory, str) or not directory.strip() or "\x00" in directory:
        raise ValueError("share_static.directory must be a non-empty filesystem path")
    base = value.get("basePath", "/p/")
    if (
        not isinstance(base, str)
        or not base.startswith("/")
        or base.startswith("//")
        or any(c in base for c in ("?", "#", "\\"))
        or any(ord(c) < 32 for c in base)
        or any(p in (".", "..") for p in base.split("/"))
    ):
        raise ValueError("share_static.basePath must be an absolute URL path")
    base_url = value.get("baseUrl", DEFAULT_BASE_URL)
    if base_url is not None and not isinstance(base_url, str):
        raise ValueError("share_static.baseUrl must be an HTTP(S) URL")
    base_url = (base_url or "").rstrip("/")
    if base_url:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or "\\" in base_url
            or any(c.isspace() for c in base_url)
            or any(p in (".", "..") for p in parsed.path.split("/"))
        ):
            raise ValueError("share_static.baseUrl must be an HTTP(S) URL")
        # The HTML base path must match the URL at which the static files are mounted.
        base = parsed.path or "/"
    return {
        "enabled": enabled,
        "directory": os.path.abspath(os.path.join(startup_directory, directory)),
        "basePath": base.rstrip("/") + "/",
        "baseUrl": base_url or (None if value.get("baseUrl", DEFAULT_BASE_URL) is None else ""),
    }


# Tokenize complete attributes, so a src= string inside another attribute isn't edited.
_ATTRIBUTE = re.compile(r"""([^\s=<>/]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?""")


class IndexTags(HTMLParser):
    def __init__(self, contents):
        super().__init__(convert_charrefs=False)
        self.offsets = [0]
        for match in re.finditer("\n", contents):
            self.offsets.append(match.end())
        self.tags = []
        self.feed(contents)

    def handle_starttag(self, tag, attrs):
        line, column = self.getpos()
        self.tags.append((tag, self.offsets[line - 1] + column, self.get_starttag_text()))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)


def rewrite_index(contents, base_path):
    tags = IndexTags(contents).tags
    if any(tag == "base" for tag, _, _ in tags):
        return contents
    edits = []
    base = '\n    <base href="' + html.escape(base_path, quote=True) + '">'
    head = next((entry for entry in tags if entry[0] == "head"), None)
    if head:
        edits.append((head[1] + len(head[2]), head[1] + len(head[2]), base))
    else:
        root = next((entry for entry in tags if entry[0] == "html"), None)
        offset = root[1] + len(root[2]) if root else 0
        edits.append((offset, offset, "<head>" + base + "\n</head>"))
    for _tag, offset, raw in tags:
        start = re.match(r"<\s*[^\s/>]+", raw).end()
        for match in _ATTRIBUTE.finditer(raw, start):
            if match.group(1).lower() not in ("src", "href"):
                continue
            group = next((i for i in (2, 3, 4) if match.group(i) is not None), None)
            if group is None:
                continue
            value = html.unescape(match.group(group))
            if value.startswith("/") and not value.startswith("//"):
                # Encode a quoted value even when the source attribute was unquoted.
                replacement = '"' + html.escape(value[1:], quote=True) + '"'
                attr = raw[match.start() : match.end()]
                eq = attr.index("=")
                edits.append((offset + match.start(), offset + match.end(), attr[: eq + 1] + replacement))
    for start, end, replacement in sorted(edits, reverse=True):
        contents = contents[:start] + replacement + contents[end:]
    return contents


def no_links(path, root):
    """Reject link/junction components from root through path, including missing leaves."""
    root, path = Path(os.path.abspath(root)), Path(os.path.abspath(path))
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise web.HTTPBadRequest(text="Publication path is outside its configured root") from None
    current = root
    for part in ("", *relative.parts):
        current = current / part
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise web.HTTPBadRequest(text=f"Static publishing does not support symlinks or junctions: {current}. Publish a build folder containing regular files instead.")
    if not within(path, root):
        raise web.HTTPBadRequest(text="Publication path is outside its configured root")


def is_excluded(name):
    """Hidden files and folders, whose names start with '.', are never published: they're repositories, secrets
    like .env, and tool settings, which a static server that doesn't hide them would serve."""
    return name.startswith(".")


def copy_output(source, stage):
    def failed(error):
        raise error

    for root, dirs, files in os.walk(source, followlinks=False, onerror=failed):
        # Skipped before they're checked, so nothing in them can fail the publication
        dirs[:] = [name for name in dirs if not is_excluded(name)]
        files = [name for name in files if not is_excluded(name)]
        no_links(root, source)
        relative = os.path.relpath(root, source)
        target = stage / relative
        target.mkdir(parents=True, exist_ok=True)
        for name in dirs + files:
            path = Path(root) / name
            no_links(path, source)
            mode = path.stat().st_mode
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise web.HTTPBadRequest(text="Only regular files and directories can be published")
            if name in files:
                shutil.copyfile(path, target / name, follow_symlinks=False)
                no_links(target / name, stage)


def publish_folder(ctx, user, project_id, config):
    """Stage and commit files and metadata under a cross-process publication lock."""
    if not config["enabled"]:
        raise web.HTTPForbidden(text="Static folder publishing is disabled")
    # This lock and all administrative state stay outside the public tree.
    with publication_lock(os.path.join(ctx.get_user_path(user), "share_static", "state")):
        project = next((p for p in ctx.projects.get_user_projects(user) if p.get("id") == project_id), None)
        if not project:
            raise web.HTTPNotFound(text="Project not found")
        folder = component(project.get("folder"))
        username = component(user or "default")
        workspace = Path(ctx.projects.resolve_workspace(project_id, user)["directories"][0])
        output = project.get("publish")
        if output is None:
            raise web.HTTPBadRequest(text="No publish directory configured for the project")
        if not isinstance(output, str) or os.path.isabs(output) or ".." in re.split(r"[/\\]", output):
            raise web.HTTPBadRequest(text="Publish directory must be within the project folder")
        source = workspace.joinpath(*re.split(r"[/\\]", output))
        no_links(source, workspace)
        if not source.is_dir():
            raise web.HTTPBadRequest(text=f"Publish directory does not exist: {source}. Build the project first or select an existing Build Directory.")
        root = Path(config["directory"])
        destination = root / username / folder
        no_links(destination, root)
        if within(destination, source) or within(source, destination):
            raise web.HTTPBadRequest(text=f"Publication source and destination must not overlap. Source: {source}. Destination: {destination}.")
        previous = project.get("staticPublication") or {}
        if destination.exists() and (not destination.is_dir() or previous.get("publishedPath") != str(destination)):
            raise web.HTTPConflict(text=f"Destination already exists and is not this project's publication: {destination}. Choose a different static publishing directory or move the existing folder before retrying.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        # A private staging directory on the destination filesystem supports rename/rollback.
        transaction = Path(tempfile.mkdtemp(prefix=".publish-", dir=destination.parent))
        stage, backup = transaction / "output", transaction / "previous"
        installed = False
        committed = False
        try:
            stage.mkdir()
            copy_output(source, stage)
            relative_url = quote(username, safe="") + "/" + quote(folder, safe="") + "/"
            path = config["basePath"] + relative_url
            for index in stage.iterdir():
                if index.is_file() and index.name.lower() == "index.html":
                    index.write_text(rewrite_index(index.read_text(encoding="utf-8-sig"), path), encoding="utf-8")
            result = {
                "publishedPath": str(destination),
                "urlPath": path,
                "publishedUrl": config["baseUrl"] + "/" + relative_url if config["baseUrl"] else None,
                "publishedAt": datetime.datetime.now(datetime.UTC).isoformat(),
            }
            no_links(destination, root)
            if destination.exists():
                os.rename(destination, backup)
            os.rename(stage, destination)
            installed = True
            ctx.projects.update_publication(
                project_id,
                {"staticPublication": result},
                user,
                expected={"folder": project.get("folder"), "publish": output},
            )
            committed = True
            return result
        except BaseException:
            if installed:
                shutil.rmtree(destination)
            if backup.exists():
                os.rename(backup, destination)
            raise
        finally:
            # If rollback itself fails, retain the backup for recovery.
            if not backup.exists() or committed:
                shutil.rmtree(transaction)
