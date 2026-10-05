"""
Ingest pipeline for Gemini file search stores.

    discover -> fetch -> extract -> derive metadata -> diff -> apply

Only `discover` and `fetch` are source-specific; everything after them is shared, so a new
connector is two methods and a config schema rather than a new import path. See INGEST.md.

Dependencies stay at stdlib: `llms` has one runtime dependency and this extension adds one, so
HTML extraction uses `html.parser` and upgrades itself if a better library happens to be
importable, rather than requiring one.
"""

import contextlib
import hashlib
import json
import os
import posixpath
import re
import unicodedata
import zipfile
from datetime import datetime

# Bumping this means "every contentHash in the corpus changes", so it's a deliberate, costed,
# confirmed full re-index rather than a surprise on the next scheduled run (INGEST.md §3.4).
EXTRACTOR_VERSION = "1"

TEXT_EXTS = {
    "md", "mdx", "markdown", "txt", "rst", "adoc", "asciidoc", "csv", "tsv",
    "json", "yaml", "yml", "toml", "ini", "cfg", "log", "sql",
}
HTML_EXTS = {"html", "htm", "xhtml", "cshtml"}
CODE_EXTS = {
    "py", "js", "mjs", "ts", "tsx", "jsx", "cs", "java", "go", "rs", "rb", "php", "kt", "swift",
    "c", "h", "cpp", "hpp", "sh", "ps1", "css", "scss", "vue", "svelte",
}
BINARY_DOC_EXTS = {"pdf", "docx", "pptx", "xlsx"}

DEFAULT_EXCLUDES = [
    "**/.git/**", "**/node_modules/**", "**/__pycache__/**", "**/.venv/**", "**/venv/**",
    "**/dist/**", "**/build/**", "**/.DS_Store", "**/*.lock", "**/.*/**", "**/import.json",
]

MIN_WORDS_DEFAULT = 25


# --------------------------------------------------------------------------------------------
# Globs
# --------------------------------------------------------------------------------------------

def glob_to_regex(pattern):
    """
    Translate a glob to a regex, with `**` spanning directory separators.

    `fnmatch` alone can't express `docs/**/*.md` because its `*` also matches `/`, which makes
    `**` and `*` indistinguishable and every pattern far too greedy.
    """
    i, out = 0, ["(?s)\\A"]
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            j = pattern.index("]", i) if "]" in pattern[i:] else -1
            if j == -1:
                out.append(re.escape(c))
                i += 1
            else:
                out.append(pattern[i : j + 1])
                i = j + 1
        else:
            out.append(re.escape(c))
            i += 1
    out.append("\\Z")
    return re.compile("".join(out))


_glob_cache = {}


def glob_match(path, pattern):
    rx = _glob_cache.get(pattern)
    if rx is None:
        rx = _glob_cache[pattern] = glob_to_regex(pattern)
    return bool(rx.match(path))


def file_patterns(value):
    if value is None:
        return []
    if isinstance(value, str):
        value = [p.strip() for p in value.replace("\n", ",").split(",") if p.strip()]
    if not isinstance(value, list) or any(not isinstance(p, str) or not p.strip() for p in value):
        raise ValueError("Include, exclude and ignore must contain file or folder patterns")
    return [p.replace("\\", "/") for p in value]


def resolve_path(path):
    """A filesystem path reduced to the one form comparisons can trust."""
    return os.path.realpath(os.path.abspath(os.path.expanduser(str(path))))


def within_roots(path, roots):
    """
    True when `path` lands inside one of `roots`.

    Both sides go through realpath, so a symlink planted inside a trusted root cannot be used to
    read outside it. The separator in the prefix test is what stops '/srv/docs-private' from
    matching a root of '/srv/docs'.
    """
    full = resolve_path(path)
    for root in roots or []:
        root = resolve_path(root)
        if full == root or full.startswith(root + os.sep):
            return True
    return False


def matches_any(path, patterns):
    return any(glob_match(path, p) for p in (patterns or []))


# --------------------------------------------------------------------------------------------
# Category (INGEST.md §7)
# --------------------------------------------------------------------------------------------

def derive_category(source_key, root=None, max_depth=None, prefix=None):
    """
    The document's folder path relative to the source root.

    Returns "" for a document at the root - a browsable value rather than a gap - and None when
    the key falls outside `root`, which means it isn't part of this source at all.

    `prefix` nests the whole source under an existing category, which is what "Import into
    guides/auth" means for a folder: the folder's own structure is preserved beneath it.
    """
    p = str(source_key or "").replace("\\", "/").lstrip("/")
    if root:
        r = str(root).strip("/")
        if r:
            if p == r:
                p = ""
            elif p.startswith(r + "/"):
                p = p[len(r) + 1 :]
            else:
                return None
    d = posixpath.dirname(p)
    segs = [s for s in d.split("/") if s]
    if max_depth is not None:
        segs = segs[: int(max_depth)]
    if prefix:
        segs = [s for s in str(prefix).strip("/").split("/") if s] + segs
    return "/".join(segs)


def within_max_depth(source_key, root=None, max_depth=None):
    """Whether a source key is within max depth, measured relative to root when applicable."""
    p = str(source_key or "").replace("\\", "/").lstrip("/")
    if root:
        r = str(root).strip("/")
        if r:
            if p == r:
                p = ""
            elif p.startswith(r + "/"):
                p = p[len(r) + 1 :]
            else:
                # derive_category reports this as "outside root"; max depth has no say here.
                return True
    if max_depth is None or max_depth == "":
        return True
    limit = int(max_depth)
    if limit < 0:
        raise ValueError("maxDepth must be zero or greater")
    directory = posixpath.dirname(p)
    depth = len([s for s in directory.split("/") if s])
    return depth <= limit


def category_ancestors(category):
    if not category:
        return []
    segs = [s for s in str(category).split("/") if s]
    return ["/".join(segs[: i + 1]) for i in range(len(segs))]


# --------------------------------------------------------------------------------------------
# Hashing (INGEST.md §3.2)
# --------------------------------------------------------------------------------------------

def normalise_text(text, volatile=None):
    """
    Deterministic text for hashing: same document, same bytes, different machine, same hash.

    Volatile patterns are removed first so a rotating build id or "last generated" line doesn't
    make every page look edited on every deploy.
    """
    if not text:
        return ""
    s = unicodedata.normalize("NFC", text)
    for pattern in volatile or []:
        try:
            s = re.sub(pattern, "", s)
        except re.error:
            continue
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = "\n".join(line.rstrip() for line in s.split("\n"))
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def content_hash(text, volatile=None):
    return hashlib.sha256(normalise_text(text, volatile).encode("utf-8")).hexdigest()


def canonical_json(meta):
    """Sorted keys, sorted list values, nulls dropped - so iteration order isn't a change."""
    out = {}
    for k in sorted(meta or {}):
        v = meta[k]
        if v is None or v == "" or v == []:
            continue
        out[k] = sorted(v) if isinstance(v, list) else v
    return json.dumps(out, separators=(",", ":"), sort_keys=True, default=str)


def metadata_hash(meta):
    return hashlib.sha256(canonical_json(meta).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------------------------
# Extraction (INGEST.md §8)
# --------------------------------------------------------------------------------------------

FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*\n", re.S)


def parse_frontmatter(text):
    """
    Split a leading `---` block off a markdown document.

    The body must have it removed: leaving it in means every chunk of every page starts with YAML,
    which is noise in retrieval and noise in the excerpt shown under a citation.

    Deliberately a minimal scalar/list parser rather than a YAML dependency - frontmatter in docs
    is overwhelmingly `key: value` and `- item`.
    """
    m = FRONTMATTER_RE.match(text or "")
    if not m:
        return {}, text
    meta, key = {}, None
    for raw in m.group(1).split("\n"):
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.lstrip().startswith("- ") and key:
            meta.setdefault(key, [])
            if isinstance(meta[key], list):
                meta[key].append(_scalar(line.lstrip()[2:]))
            continue
        if ":" in line:
            k, _, v = line.partition(":")
            key = k.strip()
            v = v.strip()
            if v.startswith("[") and v.endswith("]"):
                meta[key] = [_scalar(x) for x in v[1:-1].split(",") if x.strip()]
            elif v:
                meta[key] = _scalar(v)
            else:
                meta[key] = []
    return meta, text[m.end() :]


def _scalar(v):
    v = v.strip().strip("'\"")
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    return v


class _HtmlText:
    """Registered lazily so the good extractor is used when present without requiring it."""

    _impl = None

    @classmethod
    def convert(cls, html, selector=None):
        if cls._impl is None:
            cls._impl = _load_html_impl()
        return cls._impl(html, selector)


def _load_html_impl():
    try:  # optional, and much better at scoping to the real content
        import trafilatura  # type: ignore

        def impl(html, selector=None):
            got = trafilatura.extract(html, include_links=False, include_tables=True)
            return got if got else _stdlib_html_to_text(html, selector)

        return impl
    except ImportError:
        return _stdlib_html_to_text


SKIP_TAGS = {"script", "style", "nav", "header", "footer", "aside", "noscript", "svg", "form", "iframe"}
BLOCK_TAGS = {"p", "div", "section", "article", "li", "tr", "br", "blockquote", "pre", "table"}
HEADING_TAGS = {"h1": "#", "h2": "##", "h3": "###", "h4": "####", "h5": "#####", "h6": "######"}
BOILERPLATE_RE = re.compile(
    r"^(edit this page.*|was this (page )?helpful\??|on this page|table of contents|"
    r"copyright ©.*|all rights reserved.*|we use cookies.*|skip to (main )?content)$",
    re.I,
)


def _stdlib_html_to_text(html, selector=None):
    from html.parser import HTMLParser

    class Parser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.out = []
            self.skip_depth = 0
            self.heading = None
            self.in_body = selector is None
            self.capture_depth = None
            self.depth = 0

        def handle_starttag(self, tag, attrs):
            self.depth += 1
            if selector and self.capture_depth is None and _selector_matches(tag, attrs, selector):
                self.capture_depth = self.depth
                self.in_body = True
            if tag in SKIP_TAGS:
                self.skip_depth += 1
            elif tag in HEADING_TAGS:
                self.heading = HEADING_TAGS[tag]
                self.out.append("\n\n")
            elif tag in BLOCK_TAGS:
                self.out.append("\n")

        def handle_endtag(self, tag):
            if tag in SKIP_TAGS and self.skip_depth:
                self.skip_depth -= 1
            elif tag in HEADING_TAGS:
                self.heading = None
                self.out.append("\n")
            if self.capture_depth is not None and self.depth == self.capture_depth:
                self.capture_depth = None
                self.in_body = False
            self.depth -= 1

        def handle_data(self, data):
            if self.skip_depth or not self.in_body:
                return
            text = data.strip()
            if not text or BOILERPLATE_RE.match(text):
                return
            if self.heading:
                self.out.append(f"{self.heading} {text}")
                self.heading = None
            else:
                self.out.append(text + " ")

    p = Parser()
    # Malformed markup is normal on the web; keep whatever parsed rather than losing the page.
    with contextlib.suppress(Exception):
        p.feed(html)
    text = "".join(p.out)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _selector_matches(tag, attrs, selector):
    """Supports `tag`, `.class` and `#id` - enough to scope a docs site's content region."""
    a = dict(attrs)
    for sel in [s.strip() for s in str(selector).split(",") if s.strip()]:
        if sel.startswith("."):
            if sel[1:] in (a.get("class") or "").split():
                return True
        elif sel.startswith("#"):
            if a.get("id") == sel[1:]:
                return True
        elif sel == tag:
            return True
    return False


def strip_razor_code(text):
    """Remove line-oriented Razor directives/code blocks before treating .cshtml as HTML."""
    output = []
    block_indent = None
    for line in str(text or "").splitlines():
        expanded = line.expandtabs(4)
        stripped = expanded.lstrip()
        indent = len(expanded) - len(stripped)
        if block_indent is not None:
            if stripped.startswith("}") and indent <= block_indent:
                block_indent = None
            continue
        if stripped.startswith("@"):
            continue
        if stripped.startswith("{"):
            # A self-contained brace line has no following block to suppress.
            if "}" not in stripped[1:]:
                block_indent = indent
            continue
        output.append(line)
    return "\n".join(output)


def extract_razor_route(text):
    """Extract the route declared by a leading ``@page \"/route\"`` Razor directive."""
    match = re.search(r'''(?m)^\s*\ufeff?\s*@page\s+["'](?P<route>/[^"']*)["']''', str(text or ""))
    if not match:
        return None
    route = match.group("route")
    return None if "{" in route or "}" in route else route


def ext_of(name):
    return name.rsplit(".", 1)[1].lower() if "." in name else ""


def extract(content, filename, opts=None):
    """
    Bytes -> (text, frontmatter, skip_reason).

    `skip_reason` is set rather than raising, so an unsupported or near-empty document is reported
    in the run summary instead of failing the run.
    """
    opts = opts or {}
    ext = ext_of(filename)

    if ext in BINARY_DOC_EXTS:
        return None, {}, f"unsupported type .{ext}"

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = content.decode("latin-1")
        except Exception:
            return None, {}, "not text"

    front = {}
    if ext in HTML_EXTS:
        if ext == "cshtml":
            route = extract_razor_route(text)
            if route is not None:
                front["route"] = route
            text = strip_razor_code(text)
        text = _HtmlText.convert(text, opts.get("selector"))
    elif ext in TEXT_EXTS or ext in CODE_EXTS or ext == "":
        front, text = parse_frontmatter(text)
    else:
        return None, {}, f"unsupported type .{ext}"

    for pattern in opts.get("strip") or []:
        try:
            text = re.sub(pattern, "", text)
        except re.error:
            continue

    min_words = opts.get("minWords", MIN_WORDS_DEFAULT)
    # Code is dense and legitimately short; prose that short is nav furniture.
    if ext not in CODE_EXTS and min_words and len(text.split()) < int(min_words):
        return None, front, f"under {min_words} words"

    return text, front, None


# --------------------------------------------------------------------------------------------
# Metadata rules (INGEST.md §6)
# --------------------------------------------------------------------------------------------

METADATA_FIELDS = (
    "docType", "status", "locale", "product", "versions", "tags", "sourceUrl", "sourceUpdatedAt",
)
LIST_FIELDS = ("versions", "tags")


_TEMPLATE_RE = re.compile(r"\{(?P<name>\w+)(?::/(?P<pattern>(?:\\.|[^/])*)/)?\}")
_TEMPLATE_KEYS = {
    "category", "fullpath", "path", "pathnoext", "dir", "name", "filename", "ext", "title", "route",
}


def validate_template(template):
    """Validate plain and regex-extracting Source URL placeholders."""
    if not template:
        return
    for match in _TEMPLATE_RE.finditer(template):
        name = match.group("name")
        if name.lower() not in _TEMPLATE_KEYS:
            raise ValueError(f"Unknown Source URL variable '{{{name}}}'")
        pattern = match.group("pattern")
        if pattern is not None:
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(f"Invalid regex for Source URL variable '{{{name}}}': {e}") from e
    remainder = _TEMPLATE_RE.sub("", template)
    if "{" in remainder or "}" in remainder:
        raise ValueError("Source URL contains an invalid or unmatched variable")


def template_values(source_key, category=None, title=None, root=None, route=None):
    """
    What a `sourceUrl` template can interpolate, all derived from where the document came from.

    `docs/guides/auth.md` under root `docs`, category `guides`:

        fullPath docs/guides/auth.md · path guides/auth.md · pathNoExt guides/auth
        dir docs/guides · filename auth.md · name auth
        ext md · category guides · title Auth
    """
    key = str(source_key or "").replace("\\", "/").lstrip("/")
    dirname, _, filename = key.rpartition("/")
    name, dot, ext = filename.rpartition(".")
    if not dot:
        name, ext = filename, ""
    root = str(root or "").replace("\\", "/").strip("/")
    relpath = key
    if root:
        if key == root:
            relpath = ""
        elif key.startswith(root + "/"):
            relpath = key[len(root) + 1 :]
    return {
        "fullpath": key,
        "path": relpath,
        "pathnoext": relpath[: len(relpath) - len(ext) - 1] if ext else relpath,
        "dir": dirname,
        "filename": filename,
        "name": name,
        "ext": ext,
        "category": category or "",
        "title": title or name,
        "route": route or "",
    }


def expand_template(template, values, on_warning=None):
    """
    Expand `{placeholder}` against `values`, case-insensitively.

    This is what makes `sourceUrl` usable at all on a folder or zip import. The column is
    per-document - it's the page a citation links to - but the only ways to set one are a source
    default and a path rule, both of which are single strings applied to everything they match.
    `https://docs.acme.com/{category}/{name}` is the thing people actually mean, and for a docs
    site laid out like its URLs it's exact.

    `{name:/pattern/}` applies a regular expression to the value. Its first capture group is used,
    or the full match when the expression has no capture group. A non-match returns no URL for the
    document, since inventing a plausible citation or failing the entire import are both worse.
    """
    if not template or "{" not in template:
        return template
    validate_template(template)

    unmatched = None

    def replace(match):
        nonlocal unmatched
        name = match.group("name")
        value = str(values.get(name.lower(), match.group(0)) or "")
        if name.lower() == "route" and not value:
            unmatched = "Source URL variable '{route}' is unavailable for this document; omitting Source URL"
            return ""
        pattern = match.group("pattern")
        if pattern is None:
            return value
        extracted = re.search(pattern, value)
        if not extracted:
            unmatched = f"Source URL regex for '{{{name}}}' did not match '{value}'; omitting Source URL"
            return ""
        return (extracted.group(1) or "") if extracted.re.groups else extracted.group(0)

    out = _TEMPLATE_RE.sub(replace, template)
    if unmatched:
        if on_warning:
            on_warning(unmatched)
        return None
    # A placeholder that resolves to nothing (a document in no category) leaves `//` behind.
    scheme, sep, rest = out.partition("://")
    return scheme + sep + re.sub(r"/{2,}", "/", rest) if sep else re.sub(r"/{2,}", "/", out)


def derive_metadata(source_key, rules=None, frontmatter=None, native=None, override=None):
    """
    Resolve one document's metadata, later sources winning over earlier:

        source defaults -> path rules -> frontmatter -> source-native fields -> request override

    List fields accumulate across matching rules; scalars take the first match, so a specific
    rule listed before a general one wins.
    """
    rules = rules or {}
    meta = {}

    for k, v in (rules.get("defaults") or {}).items():
        if k in METADATA_FIELDS:
            meta[k] = v

    matched = []
    for rule in rules.get("rules") or []:
        pattern = rule.get("match")
        if not pattern or not glob_match(source_key, pattern):
            continue
        matched.append(pattern)
        if rule.get("skip"):
            return None, matched
        for k, v in (rule.get("set") or {}).items():
            if k not in METADATA_FIELDS:
                continue
            if k in LIST_FIELDS:
                cur = list(meta.get(k) or [])
                for item in v if isinstance(v, list) else [v]:
                    if item not in cur:
                        cur.append(item)
                meta[k] = cur
            elif k not in meta or k in (rules.get("defaults") or {}):
                meta[k] = v

    allow = (rules.get("frontmatter") or {}).get("allow")
    for k, v in (frontmatter or {}).items():
        if k not in METADATA_FIELDS:
            continue
        if allow is not None and k not in allow:
            continue
        meta[k] = v

    for k, v in (native or {}).items():
        if k in METADATA_FIELDS and v not in (None, "", []):
            meta.setdefault(k, v)

    for k, v in (override or {}).items():
        if k in METADATA_FIELDS and v not in (None, ""):
            meta[k] = v

    for k in LIST_FIELDS:
        if k in meta and not isinstance(meta[k], list):
            meta[k] = [meta[k]]
    return meta, matched


# --------------------------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------------------------

class Item:
    __slots__ = ("key", "title", "etag", "size", "handle", "native")

    def __init__(self, key, title=None, etag=None, size=0, handle=None, native=None):
        self.key = key
        self.title = title or posixpath.basename(key)
        self.etag = etag
        self.size = size
        self.handle = handle
        self.native = native or {}


class Source:
    """A source implements discovery and fetch; the pipeline does everything else."""

    type = "base"
    requires = None  # e.g. "git" - reported by /source-types instead of failing at run time

    def __init__(self, ctx, config=None):
        self.ctx = ctx
        self.config = config or {}

    @classmethod
    def available(cls):
        return True, None

    def discover(self):
        raise NotImplementedError

    def fetch(self, item):
        raise NotImplementedError


class FolderSource(Source):
    type = "folder"

    def discover(self):
        root = os.path.abspath(os.path.expanduser(self.config.get("path") or ""))
        if not os.path.isdir(root):
            raise Exception(f"Not a directory: {root}")
        def read_manifest(directory):
            try:
                with open(os.path.join(directory, "import.json"), encoding="utf-8") as f:
                    cfg = json.load(f)
                if not isinstance(cfg, dict):
                    raise ValueError("import.json must be an object")
                return cfg
            except FileNotFoundError:
                return {}

        manifest = read_manifest(root)
        include = file_patterns(self.config.get("include") or manifest.get("include"))
        exclude = file_patterns(self.config.get("exclude")) + file_patterns(self.config.get("ignore")) + DEFAULT_EXCLUDES
        scoped_excludes = {}

        def ignored(key, patterns):
            return any(glob_match(key, p) or key.startswith(p.rstrip("/") + "/")
                       or glob_match(key + "/x", p.rstrip("/") + "/**") for p in patterns)

        for dirpath, dirnames, filenames in os.walk(root):
            rel_dir = os.path.relpath(dirpath, root).replace("\\", "/")
            rel_dir = "" if rel_dir == "." else rel_dir
            cfg = read_manifest(dirpath)
            local_excludes = file_patterns(cfg.get("exclude")) + file_patterns(cfg.get("ignore"))
            inherited = scoped_excludes.get(posixpath.dirname(rel_dir), []) if rel_dir else []
            scoped = [*inherited, (rel_dir, local_excludes)]
            scoped_excludes[rel_dir] = scoped

            def excluded(key):
                if ignored(key, exclude):
                    return True
                return any(ignored(key[len(base) + 1:] if base else key, pats) for base, pats in scoped)

            dirnames[:] = [
                d for d in sorted(dirnames)
                if not excluded(f"{rel_dir}/{d}".lstrip("/"))
            ]
            for name in sorted(filenames):
                key = f"{rel_dir}/{name}".lstrip("/")
                if excluded(key):
                    continue
                if include and not matches_any(key, include):
                    continue
                full = os.path.join(dirpath, name)
                if not within_roots(full, [root]):
                    continue
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                yield Item(
                    key=key,
                    etag=f"{st.st_mtime_ns}:{st.st_size}",
                    size=st.st_size,
                    handle=full,
                    native={"sourceUpdatedAt": int(st.st_mtime)},
                )

    def fetch(self, item):
        with open(item.handle, "rb") as f:
            return f.read()

    def rules_for(self, item, base_rules):
        # Imported lazily to avoid making the generic extractor depend on the crawler module.
        configured = self.config.get("path")
        if not configured:
            return base_rules
        root = os.path.abspath(os.path.expanduser(configured))
        rel_dir = posixpath.dirname(item.key)
        candidates = [os.path.join(root, "import.json")]
        current = root
        for part in [p for p in rel_dir.split("/") if p]:
            current = os.path.join(current, part)
            candidates.append(os.path.join(current, "import.json"))
        if not any(os.path.isfile(path) for path in candidates):
            return base_rules
        try:
            from . import crawl
        except ImportError:  # standalone extension tests load ingest.py outside its package
            import importlib.util
            spec = importlib.util.spec_from_file_location("gemini_crawl", os.path.join(os.path.dirname(__file__), "crawl.py"))
            crawl = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(crawl)
        manifest = crawl.effective_manifest(root, item.key)
        if self.config.get("metadataSpecified"):
            return crawl.merge_metadata(manifest.get("metadata"), base_rules)
        return crawl.merge_metadata(base_rules, manifest.get("metadata"))


class ZipSource(Source):
    type = "zip"

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        self._zip = None

    def _open(self):
        if self._zip is None:
            path = self.config.get("path")
            if not path or not os.path.exists(path):
                raise Exception(f"Archive not found: {path}")
            self._zip = zipfile.ZipFile(path)
        return self._zip

    def discover(self):
        zf = self._open()
        include = self.config.get("include")
        exclude = list(self.config.get("exclude") or []) + DEFAULT_EXCLUDES
        for info in zf.infolist():
            if info.is_dir():
                continue
            key = info.filename.replace("\\", "/").lstrip("/")
            if "__MACOSX/" in key or matches_any(key, exclude):
                continue
            if include and not matches_any(key, include):
                continue
            native = {}
            if info.date_time and info.date_time[0] >= 1980:
                native["sourceUpdatedAt"] = int(datetime(*info.date_time).timestamp())
            yield Item(
                key=key,
                etag=f"{info.CRC}:{info.file_size}",
                size=info.file_size,
                handle=info.filename,
                native=native,
            )

    def fetch(self, item):
        with self._open().open(item.handle) as f:
            return f.read()

    def rules_for(self, item, base_rules):
        zf = self._open()
        key = item.key.replace("\\", "/").lstrip("/")
        directories = [""]
        current = ""
        for part in [p for p in posixpath.dirname(key).split("/") if p]:
            current = posixpath.join(current, part)
            directories.append(current)
        merged = {}
        try:
            from . import crawl
        except ImportError:
            return base_rules
        names = set(zf.namelist())
        for directory in directories:
            manifest = posixpath.join(directory, "import.json") if directory else "import.json"
            if manifest not in names:
                continue
            try:
                cfg = json.loads(zf.read(manifest).decode("utf-8"))
                merged = crawl.merge_metadata(merged, cfg.get("metadata"))
            except Exception as e:
                raise ValueError(f"Invalid {manifest}: {e}") from e
        return crawl.merge_metadata(merged, base_rules)

    def close(self):
        if self._zip:
            self._zip.close()
            self._zip = None


SOURCE_TYPES = {c.type: c for c in (FolderSource, ZipSource)}


def source_types():
    out = []
    for name, cls in SOURCE_TYPES.items():
        ok, why = cls.available()
        out.append({"type": name, "available": ok, "reason": why, "requires": cls.requires})
    return out


def create_source(ctx, type_name, config):
    cls = SOURCE_TYPES.get(type_name)
    if not cls:
        raise Exception(f"Unknown source type '{type_name}'")
    ok, why = cls.available()
    if not ok:
        raise Exception(f"Source type '{type_name}' is unavailable: {why}")
    return cls(ctx, config)


# --------------------------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------------------------

class Plan:
    """What a run would do. Produced identically for a dry run and a real one."""

    def __init__(self):
        self.add, self.change, self.metadata_only = [], [], []
        self.unchanged, self.removed, self.skipped, self.failed = [], [], [], []
        self.bytes = 0
        self.rules_matched = {}

    def counts(self):
        return {
            "discovered": len(self.add) + len(self.change) + len(self.metadata_only)
            + len(self.unchanged) + len(self.skipped) + len(self.failed),
            "added": len(self.add),
            "changed": len(self.change),
            "metadataOnly": len(self.metadata_only),
            "unchanged": len(self.unchanged),
            "removed": len(self.removed),
            "skipped": len(self.skipped),
            "failed": len(self.failed),
            "bytes": self.bytes,
            "embeds": len(self.add) + len(self.change) + len(self.metadata_only),
        }

    def summary(self, sample=5):
        return {
            **self.counts(),
            "rulesMatched": self.rules_matched,
            "samples": {
                "added": [d["sourceKey"] for d in self.add[:sample]],
                "changed": [d["sourceKey"] for d in self.change[:sample]],
                "removed": [d.get("sourceKey") for d in self.removed[:sample]],
                "skipped": self.skipped[:sample],
                "failed": self.failed[:sample],
            },
            "preview": [
                {k: v for k, v in d.items() if k != "text"} for d in (self.add + self.change)[:sample]
            ],
        }


# A run that would remove more than this stops and asks. A site that moved URL scheme, an expired
# token returning an empty list and a typo'd base path all present as mass deletion (INGEST.md §5).
DELETE_RATIO_LIMIT = 0.2
DELETE_COUNT_LIMIT = 100
# The ratio rail needs a corpus big enough for a proportion to mean anything: in a 4-document
# store a single legitimate deletion is 25%, and prompting for that trains people to click through.
DELETE_RATIO_MIN_CORPUS = 20


def build_plan(source_row, source, existing, override=None, on_progress=None, on_warning=None):
    """
    discover -> fetch -> extract -> derive -> diff, without writing anything.

    `existing` maps sourceKey -> document row. Raises if discovery fails, so the caller can
    refuse to compute deletions from a partial listing.
    """
    plan = Plan()
    cat_cfg = source_row.get("category") or {}
    rules = source_row.get("rules") or {}
    extract_opts = source_row.get("extract") or {}
    volatile = source_row.get("volatile") or []
    extractor_ver = source_row.get("extractorVer") or EXTRACTOR_VERSION
    seen = set()

    for i, item in enumerate(source.discover()):
        if on_progress and i % 25 == 0:
            on_progress(i)
        key = item.key
        if not within_max_depth(key, cat_cfg.get("root"), cat_cfg.get("maxDepth")):
            continue
        seen.add(key)
        prior = existing.get(key)

        category = derive_category(
            key, cat_cfg.get("root"), cat_cfg.get("maxDepth"), cat_cfg.get("prefix")
        )
        if category is None:
            plan.skipped.append({"sourceKey": key, "reason": "outside root"})
            continue

        try:
            raw = source.fetch(item)
        except Exception as e:
            plan.failed.append({"sourceKey": key, "reason": str(e)[:200]})
            continue

        text, front, skip = extract(raw, key, extract_opts)
        if skip:
            plan.skipped.append({"sourceKey": key, "reason": skip})
            continue

        item_rules = source.rules_for(item, rules) if hasattr(source, "rules_for") else rules
        meta, matched = derive_metadata(key, item_rules, front, item.native, override)
        if meta is None:
            plan.skipped.append({"sourceKey": key, "reason": "excluded by rule"})
            continue
        for pattern in matched:
            plan.rules_matched[pattern] = plan.rules_matched.get(pattern, 0) + 1

        meta["category"] = category
        meta["categoryPath"] = category_ancestors(category)
        # After the category is known and before the hash, so a template change reads as a
        # metadata change on re-run. Applies wherever the value came from - a source default or a
        # path rule - because by here they've all resolved to one string.
        if meta.get("sourceUrl"):
            expanded_url = expand_template(
                meta["sourceUrl"], template_values(
                    key, category, item.title, cat_cfg.get("root"), front.get("route")),
                lambda warning: on_warning(f"{key}: {warning}") if on_warning else None,
            )
            if expanded_url is None:
                meta.pop("sourceUrl", None)
            else:
                meta["sourceUrl"] = expanded_url
        if (source_row.get("config") or {}).get("requireSourceUrl") and not str(meta.get("sourceUrl") or "").strip():
            seen.discard(key)
            plan.skipped.append({"sourceKey": key, "reason": "Source URL is required"})
            continue
        c_hash = content_hash(text, volatile)
        m_hash = metadata_hash(meta)

        display_name = front.get("title") or item.title
        if ext_of(key) in HTML_EXTS and not front.get("title"):
            display_name = re.sub(r"\.[^.]+$", ".md", str(display_name), flags=re.I)
        entry = {
            "sourceKey": key,
            "displayName": display_name,
            "size": len(raw),
            "text": text,
            "contentHash": c_hash,
            "metadataHash": m_hash,
            "sourceEtag": item.etag,
            "extractorVer": extractor_ver,
            **meta,
        }

        if not prior:
            plan.add.append(entry)
            plan.bytes += len(raw)
        elif (prior.get("tombstonedAt") or prior.get("state") == "MISSING_FROM_REMOTE"
              or (prior.get("error") and not prior.get("uploadedAt"))
              or prior.get("contentHash") != c_hash or prior.get("extractorVer") != extractor_ver):
            entry["id"] = prior.get("id")
            plan.change.append(entry)
            plan.bytes += len(raw)
        elif prior.get("metadataHash") != m_hash or prior.get("displayName") != display_name:
            entry["id"] = prior.get("id")
            plan.metadata_only.append(entry)
        else:
            plan.unchanged.append(entry)

    for key, doc in existing.items():
        if key not in seen and not doc.get("tombstonedAt"):
            plan.removed.append(doc)

    return plan


def check_delete_rails(plan, existing_count):
    """Returns a refusal message when a run would remove implausibly much, else None."""
    removed = len(plan.removed)
    if not removed or not existing_count:
        return None
    ratio = removed / existing_count
    over_count = removed > DELETE_COUNT_LIMIT
    over_ratio = existing_count >= DELETE_RATIO_MIN_CORPUS and ratio > DELETE_RATIO_LIMIT
    if over_count or over_ratio:
        return (
            f"Refusing to remove {removed} of {existing_count} documents "
            f"({ratio:.0%}). Confirm explicitly if this is intended."
        )
    return None
