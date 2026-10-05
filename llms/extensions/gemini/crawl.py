"""Per-user crawl workspaces and hierarchical import.json manifests."""

import asyncio
import hashlib
import json
import os
import posixpath
import re
import shutil
import tempfile
import urllib.robotparser
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit

from . import ingest

MANIFEST = "import.json"
DEFAULT_QUERY_EXCLUDES = ["utm_*", "fbclid", "gclid", "ref", "session", "token"]
CRAWL_RULE_SCHEMA = {
    "title": "Crawl rules",
    "description": "Ordered rules; the first matching rule wins.",
    "type": "array",
    "items": {
        "title": "Rule",
        "oneOf": [
            {
                "title": "Path rule",
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "match": {"title": "Path glob", "type": "string", "minLength": 1,
                              "examples": ["/archives/**"],
                              "description": "Uses the same glob syntax as folder imports."},
                    "action": {"title": "Action", "type": "string",
                               "enum": ["exclude", "followOnly", "save"],
                               "x-enumNames": ["Exclude", "Follow links only", "Save page"],
                               "default": "exclude"},
                },
                "required": ["match", "action"],
            },
            {
                "title": "Query-string rule",
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "queryString": {"title": "Has query string", "type": "boolean", "const": True},
                    "action": {"title": "Action", "type": "string",
                               "enum": ["exclude", "followOnly", "save"],
                               "x-enumNames": ["Exclude", "Follow links only", "Save page"],
                               "default": "exclude"},
                },
                "required": ["queryString", "action"],
            },
        ],
    },
}
TRANSFORM_SCHEMA = {
    "title": "Regex transforms",
    "description": "Applied in order to matching generated Markdown files.",
    "type": "array",
    "items": {
        "title": "Transform",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "match": {"title": "File glob", "type": "string", "default": "**/*.md",
                      "examples": ["**/*.md"], "description": "Optional; defaults to every Markdown file."},
            "pattern": {"title": "Regex pattern", "type": "string", "minLength": 1,
                        "x-widget": "textarea", "examples": ["Version: (v\\d+)"],
                        "description": "Example: Version: (v\\d+) captures the version for use in the replacement."},
            "replacement": {"title": "Replacement", "type": "string", "default": "",
                            "x-widget": "textarea",
                            "examples": ["Release \\1"],
                            "description": "Example: Release \\1 inserts capture group 1. Named groups can use \\g<name>."},
            "flags": {"title": "Flags", "type": "string", "default": "g",
                      "pattern": "^[gims]*$", "examples": ["gim"],
                      "description": "g global, i ignore case, m multiline, s . matches lines"},
        },
        "required": ["pattern"],
    },
}


def validate_crawl_rules(rules):
    if not isinstance(rules, list):
        raise ValueError("Crawl rules must be an array")
    for i, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ValueError(f"Crawl rule {i + 1} must be an object")
        if rule.get("action") not in ("exclude", "followOnly", "save"):
            raise ValueError(f"Crawl rule {i + 1} has an invalid action")
        if not rule.get("match") and rule.get("queryString") is not True:
            raise ValueError(f"Crawl rule {i + 1} needs a path glob or Has query string")
        if rule.get("match") is not None and not isinstance(rule.get("match"), str):
            raise ValueError(f"Crawl rule {i + 1} path glob must be text")
    return rules


def validate_transforms(transforms):
    if not isinstance(transforms, list):
        raise ValueError("Regex transforms must be an array")
    for i, rule in enumerate(transforms):
        if not isinstance(rule, dict):
            raise ValueError(f"Regex transform {i + 1} must be an object")
        pattern = rule.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            raise ValueError(f"Regex transform {i + 1} needs a pattern")
        flags_text = rule.get("flags", "g")
        if not isinstance(flags_text, str) or re.search(r"[^gims]", flags_text):
            raise ValueError(f"Regex transform {i + 1} has unsupported flags")
        flags = sum({"i": re.I, "m": re.M, "s": re.S}.get(x, 0) for x in flags_text)
        try:
            compiled = re.compile(pattern, flags)
        except re.error as e:
            raise ValueError(f"Regex transform {i + 1} is invalid: {e}") from e
        try:
            # Replacement templates are parsed independently of whether the pattern matches,
            # catching invalid backreferences before any crawled file can be changed.
            compiled.sub(rule.get("replacement") or "", "", count=1)
        except re.error as e:
            raise ValueError(f"Regex transform {i + 1} replacement is invalid: {e}") from e
    return transforms


def site_name(url):
    """Filesystem-safe default derived from host[:port]."""
    host = (urlsplit(str(url or "").strip()).netloc or "").lower()
    host = host.rsplit("@", 1)[-1].replace(":", "-")
    return re.sub(r"[^a-z0-9._-]+", "-", host).strip("-.") or "website"


def safe_name(value):
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip("-.")
    if not value or value in (".", ".."):
        raise ValueError("Import name is required")
    return value


def imports_root(ctx, user=None):
    return os.path.join(ctx.get_user_path(user=user), "gemini", "imports")


def workspace_path(ctx, user, name):
    root = os.path.realpath(imports_root(ctx, user))
    path = os.path.realpath(os.path.join(root, safe_name(name)))
    if os.path.commonpath((root, path)) != root:
        raise ValueError("Import path escapes the user's imports folder")
    return path


def legacy_workspace(ctx, user, manifest_path):
    root = os.path.dirname(os.path.realpath(manifest_path))
    owned_root = os.path.realpath(imports_root(ctx, user))
    return (os.path.dirname(root) == owned_root
            and bool(re.fullmatch(r".+-[0-9a-f]{10}", os.path.basename(root)))
            and bool(read_json(manifest_path).get("importedFrom")))


def workspace_target(ctx, user, manifest_path):
    """One stable directory per named crawl, regardless of the imported manifest's location."""
    manifest_path = os.path.realpath(manifest_path)
    root = os.path.dirname(manifest_path)
    owned_root = os.path.realpath(imports_root(ctx, user))
    cfg = read_json(manifest_path)
    if not (cfg.get("crawl") or {}).get("url"):
        return root
    try:
        if os.path.commonpath((owned_root, root)) == owned_root:
            if legacy_workspace(ctx, user, manifest_path):
                name = cfg["crawl"].get("name") or site_name(cfg["crawl"]["url"])
                if name == os.path.basename(root):
                    name = name[:-11]
                return workspace_path(ctx, user, name)
            return root
    except ValueError:
        pass
    return workspace_path(ctx, user, cfg["crawl"].get("name") or site_name(cfg["crawl"]["url"]))


def legacy_manifests(ctx, user, target):
    root = imports_root(ctx, user)
    if not os.path.isdir(root):
        return []
    manifests = []
    for entry in os.scandir(root):
        path = os.path.join(entry.path, MANIFEST)
        if entry.is_dir() and legacy_workspace(ctx, user, path) and workspace_target(ctx, user, path) == target:
            manifests.append(path)
    return manifests


def import_workspace(ctx, user, manifest_path):
    """Refresh the named private workspace; repeat imports replace its previous contents."""
    manifest_path = os.path.realpath(manifest_path)
    root = os.path.dirname(manifest_path)
    cfg = read_json(manifest_path)
    if not (cfg.get("crawl") or {}).get("url"):
        return manifest_path
    owned_root = os.path.realpath(imports_root(ctx, user))
    destination = workspace_target(ctx, user, manifest_path)
    if destination == root:
        return manifest_path
    try:
        inside_original = os.path.commonpath((root, destination)) == root
    except ValueError:
        inside_original = False
    if inside_original:
        raise ValueError("A crawl workspace cannot contain the user's private imports directory")
    copied_manifest = os.path.join(destination, MANIFEST)
    config = (cfg.get("source") or {}).get("config") or {}
    input_path = os.path.realpath(os.path.join(root, os.path.expanduser(config.get("path") or ".")))
    if os.path.commonpath((root, input_path)) != root:
        raise ValueError("The crawl input folder must be inside its manifest workspace")
    os.makedirs(owned_root, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".import-", dir=owned_root) as staging:
        copy = os.path.join(staging, "workspace")
        # Links could keep the copy attached to original files or an unrelated directory.
        shutil.copytree(root, copy, ignore=lambda directory, names: [
            entry for entry in names if os.path.islink(os.path.join(directory, entry)) or entry == MANIFEST + ".tmp"])
        cfg["importedFrom"] = cfg.get("importedFrom") if legacy_workspace(ctx, user, manifest_path) else manifest_path
        cfg["crawl"]["name"] = os.path.basename(destination)
        cfg["source"] = {**(cfg.get("source") or {}),
            "name": (cfg.get("source") or {}).get("name") or "Import " + os.path.basename(root),
            "config": {**config, "path": os.path.relpath(input_path, root).replace("\\", "/")}}
        write_json(os.path.join(copy, MANIFEST), cfg)
        backup = os.path.join(staging, "previous")
        if os.path.exists(destination):
            os.replace(destination, backup)
        try:
            os.replace(copy, destination)
        except BaseException:
            if os.path.exists(backup):
                os.replace(backup, destination)
            raise
    return copied_manifest


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            value = json.load(f)
        if not isinstance(value, dict):
            raise ValueError(f"{path} must contain a JSON object")
        return value
    except FileNotFoundError:
        return {}


def write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def merge_metadata(parent, child):
    """A child manifest overwrites defaults and appends its more-specific path rules."""
    parent, child = parent or {}, child or {}
    return {
        "defaults": {**(parent.get("defaults") or {}), **(child.get("defaults") or {})},
        "rules": [*(parent.get("rules") or []), *(child.get("rules") or [])],
        **({"frontmatter": child.get("frontmatter", parent.get("frontmatter"))}
           if child.get("frontmatter", parent.get("frontmatter")) is not None else {}),
    }


def effective_manifest(root, relative_file=""):
    """Merge root -> nearest directory manifests for one file."""
    root = os.path.realpath(root)
    rel_dir = posixpath.dirname(str(relative_file).replace("\\", "/"))
    parts = [p for p in rel_dir.split("/") if p]
    current, merged = root, {}
    for part in [None, *parts]:
        if part is not None:
            current = os.path.join(current, part)
        cfg = read_json(os.path.join(current, MANIFEST))
        if cfg:
            merged = {
                **merged,
                **{k: v for k, v in cfg.items() if k not in ("metadata", "transforms")},
                "metadata": merge_metadata(merged.get("metadata"), cfg.get("metadata")),
                "transforms": [*(merged.get("transforms") or []), *(cfg.get("transforms") or [])],
            }
    return merged


def save_metadata(root, metadata):
    """Update only metadata, preserving crawl and transform configuration."""
    path = os.path.join(root, MANIFEST)
    cfg = read_json(path)
    cfg["version"] = cfg.get("version") or 1
    cfg["metadata"] = metadata or {"defaults": {}, "rules": []}
    write_json(path, cfg)
    return cfg


def list_imports(ctx, user=None):
    root = imports_root(ctx, user)
    os.makedirs(root, exist_ok=True)
    out = []
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            continue
        cfg = read_json(os.path.join(path, MANIFEST))
        pages = len(list_crawled_pages(path))
        out.append({"name": name, "path": path, "pages": pages, "config": cfg})
    return out


def list_crawled_pages(root):
    """Return existing generated Markdown paths recorded by the crawl manifest."""
    root = os.path.realpath(root)
    if not os.path.isdir(root):
        return []
    generated = (read_json(os.path.join(root, MANIFEST)).get("crawl") or {}).get("generated") or []
    pages = []
    for relative_path in generated:
        relative_path = str(relative_path or "").replace("\\", "/").lstrip("/")
        full = os.path.realpath(os.path.join(root, *relative_path.split("/")))
        if (relative_path.endswith(".md") and os.path.commonpath((root, full)) == root
                and os.path.isfile(full)):
            pages.append(relative_path)
    return sorted(set(pages))


def read_crawled_page(root, relative_path):
    """Read one generated page, constrained to the selected crawl workspace."""
    root = os.path.realpath(root)
    relative_path = str(relative_path or "").replace("\\", "/").lstrip("/")
    if not relative_path or not relative_path.endswith(".md"):
        raise ValueError("A crawled page path is required")
    if relative_path not in set(list_crawled_pages(root)):
        raise ValueError("Crawled page was not found")
    full = os.path.realpath(os.path.join(root, *relative_path.split("/")))
    if os.path.commonpath((root, full)) != root or not os.path.isfile(full):
        raise ValueError("Crawled page was not found")
    with open(full, encoding="utf-8") as f:
        return f.read()


def apply_transforms(root, transforms):
    validate_transforms(transforms or [])
    changed = 0
    for dirpath, _, files in os.walk(root):
        for filename in files:
            if not filename.endswith(".md"):
                continue
            full = os.path.join(dirpath, filename)
            rel = os.path.relpath(full, root).replace("\\", "/")
            with open(full, encoding="utf-8") as f:
                text = f.read()
            updated = text
            for rule in transforms or []:
                if rule.get("match") and not ingest.glob_match(rel, rule["match"]):
                    continue
                flags = sum({"i": re.I, "m": re.M, "s": re.S}.get(x, 0) for x in rule.get("flags", ""))
                updated = re.sub(rule.get("pattern") or "", rule.get("replacement") or "", updated,
                                 count=0 if "g" in rule.get("flags", "g") else 1, flags=flags)
            if updated != text:
                with open(full, "w", encoding="utf-8") as f:
                    f.write(updated)
                changed += 1
    return changed


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.title, self.description, self.tags = [], "", "", []
        self.robots, self.canonical = set(), None
        self._title = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href"):
            rel = {x.lower() for x in (attrs.get("rel") or "").split()}
            self.links.append((attrs["href"], "nofollow" in rel))
        elif tag == "link" and "canonical" in (attrs.get("rel") or "").lower().split():
            self.canonical = attrs.get("href")
        elif tag == "title":
            self._title = True
        elif tag == "meta":
            key = (attrs.get("name") or attrs.get("property") or "").lower()
            if key in ("description", "og:description") and not self.description:
                self.description = attrs.get("content") or ""
            if key in ("keywords", "tags"):
                self.tags.extend(x.strip() for x in (attrs.get("content") or "").split(",") if x.strip())
            if key in ("robots", "googlebot"):
                self.robots.update(x.strip().lower() for x in (attrs.get("content") or "").split(","))

    def handle_endtag(self, tag):
        if tag == "title": self._title = False

    def handle_data(self, data):
        if self._title: self.title += data


def page_relative_path(url):
    parsed = urlsplit(url)
    path = parsed.path.strip("/")
    if not path:
        base = "index"
    elif parsed.path.endswith("/"):
        base = posixpath.join(path, "index")
    else:
        base = posixpath.splitext(path)[0]
    if parsed.query:
        suffix = hashlib.sha256(parsed.query.encode()).hexdigest()[:10]
        base = f"{base}--{suffix}"
    return f"{base}.md"


def frontmatter(meta):
    lines = ["---"]
    for key, value in meta.items():
        if value in (None, "", []): continue
        lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    return "\n".join([*lines, "---", ""])


def _matches(value, patterns):
    return any(ingest.glob_match(value, pattern) for pattern in patterns or [])


def origin_of(value):
    p = urlsplit(str(value))
    port = p.port or (443 if p.scheme.lower() == "https" else 80)
    return p.scheme.lower(), (p.hostname or "").lower(), port


def canonical_url(raw, start, options):
    """Normalize a discovered URL and reject anything outside the configured crawl scope."""
    joined = urljoin(start, str(raw or "").strip())
    parsed = urlsplit(joined)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        return None
    start_origin = origin_of(start)
    allowed_hosts = {str(x).lower() for x in options.get("allowedHosts") or []}
    host_port = parsed.netloc.rsplit("@", 1)[-1].lower()
    if options.get("sameOrigin", True) and origin_of(joined) != start_origin and host_port not in allowed_hosts:
        return None
    base = urlsplit(start).path.rstrip("/")
    if base and not (parsed.path == base or parsed.path.startswith(base + "/")):
        return None
    path = parsed.path or "/"
    if options.get("include") and not _matches(path, options["include"]):
        return None
    if _matches(path, options.get("exclude")):
        return None

    query_cfg = options.get("query") or {}
    mode = query_cfg.get("mode", "ignore")
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if mode == "ignore":
        pairs = []
    else:
        allowed = query_cfg.get("allow") or []
        excluded = query_cfg.get("exclude", DEFAULT_QUERY_EXCLUDES)
        if mode == "allow":
            pairs = [(k, v) for k, v in pairs if _matches(k, allowed)]
        pairs = [(k, v) for k, v in pairs if not _matches(k, excluded)]
    from urllib.parse import urlencode
    query = urlencode(sorted(pairs), doseq=True)
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, query, ""))


def crawl_action(url, options):
    """First matching ordered rule wins; normal pages are fetched and saved."""
    parsed = urlsplit(url)
    for rule in options.get("rules") or []:
        if rule.get("match") and not ingest.glob_match(parsed.path or "/", rule["match"]):
            continue
        if "queryString" in rule and bool(parsed.query) != bool(rule["queryString"]):
            continue
        return rule.get("action") or "save"
    return "save"


async def crawl_site(ctx, user, options, *, target_path=None):
    import aiohttp
    start = str(options.get("url") or "").strip()
    parsed = urlsplit(start)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("A valid http:// or https:// URL is required")
    name = safe_name(options.get("name") or site_name(start))
    # Explicit targets come from an authorized saved manifest, never from crawl options.
    root = os.path.realpath(target_path) if target_path else workspace_path(ctx, user, name)
    os.makedirs(root, exist_ok=True)
    cfg_path = os.path.join(root, MANIFEST)
    cfg = read_json(cfg_path)
    previously_generated = set((cfg.get("crawl") or {}).get("generated") or [])
    options = {"sameOrigin": True, "respectRobots": True, "respectNoIndex": True,
               "followNoFollow": False, "useCanonical": True, "dedupeContent": True,
               "contentTypes": ["text/html"], **options, "url": start, "name": name}
    validate_crawl_rules(options.get("rules") or [])
    max_pages = min(max(int(options.get("maxPages") or 500), 1), 10000)
    max_depth = min(max(int(options.get("maxDepth") if options.get("maxDepth") not in (None, "") else 10), 0), 100)
    start_url = canonical_url(start, start, options)
    if not start_url:
        raise ValueError("The start URL is excluded by the crawl rules")
    pending, queued, seen, pages = [(start_url, 0)], {start_url}, set(), []
    saved_urls, content_hashes = set(), set()
    query_variants, requests = {}, 0
    max_requests = min(max(int(options.get("maxRequests") or max_pages * 5), max_pages), 50000)
    timeout = aiohttp.ClientTimeout(total=30)
    headers = {"User-Agent": "llms-gemini-crawler/1.0"}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        robots = urllib.robotparser.RobotFileParser()
        robots.set_url(urljoin(start_url, "/robots.txt"))
        if options.get("respectRobots", True):
            try:
                async with session.get(robots.url) as response:
                    robots.parse((await response.text(errors="replace")).splitlines() if response.status < 400 else [])
            except Exception:
                robots.parse([])
        while pending and len(pages) < max_pages and requests < max_requests:
            url, depth = pending.pop(0)
            if url in seen: continue
            seen.add(url)
            if options.get("respectRobots", True) and not robots.can_fetch(headers["User-Agent"], url):
                continue
            action = crawl_action(url, options)
            if action == "exclude":
                continue
            requests += 1
            async with session.get(url, allow_redirects=True) as response:
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                if response.status >= 400 and response.status not in (404, 410):
                    raise ValueError(f"Crawl failed for {url}: HTTP {response.status}")
                if response.status >= 400 or not _matches(content_type, options.get("contentTypes") or ["text/html"]):
                    continue
                final = response.url
                final_url = canonical_url(str(final), start, options)
                if not final_url:
                    continue
                html = await response.text(errors="replace")
            parser = PageParser()
            parser.feed(html)
            page_url = final_url
            if options.get("useCanonical", True) and parser.canonical:
                page_url = canonical_url(parser.canonical, final_url, options) or final_url
            text = ingest._HtmlText.convert(html, options.get("selector"))
            noindex = "noindex" in parser.robots and options.get("respectNoIndex", True)
            digest = hashlib.sha256(text.strip().encode()).hexdigest() if text.strip() else None
            duplicate = digest in content_hashes if digest and options.get("dedupeContent", True) else False
            if action != "followOnly" and not noindex and text.strip() and page_url not in saved_urls and not duplicate:
                split = urlsplit(page_url)
                meta = {"title": parser.title.strip(), "sourceUrl": page_url, "path": split.path,
                        "queryString": split.query, "description": parser.description, "tags": parser.tags}
                rel = page_relative_path(page_url)
                full = os.path.join(root, *rel.split("/"))
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "w", encoding="utf-8") as f:
                    f.write(frontmatter(meta) + text.strip() + "\n")
                pages.append(rel)
                saved_urls.add(page_url)
                if digest: content_hashes.add(digest)
            page_nofollow = "nofollow" in parser.robots and not options.get("followNoFollow", False)
            if depth >= max_depth or page_nofollow:
                continue
            for href, link_nofollow in parser.links:
                if link_nofollow and not options.get("followNoFollow", False):
                    continue
                clean = canonical_url(href, final_url, options)
                if clean and clean not in seen and clean not in queued and crawl_action(clean, options) != "exclude":
                    split_clean = urlsplit(clean)
                    if split_clean.query:
                        variant_key = (split_clean.scheme, split_clean.netloc, split_clean.path)
                        limit = int((options.get("query") or {}).get("maxVariantsPerPath") or 5)
                        if query_variants.get(variant_key, 0) >= limit:
                            continue
                        query_variants[variant_key] = query_variants.get(variant_key, 0) + 1
                    queued.add(clean)
                    pending.append((clean, depth + 1))
    # Once a crawl has recorded ownership of its generated pages, a later crawl removes pages
    # that are no longer reachable or are newly excluded. Hand-authored Markdown is untouched.
    for rel in previously_generated - set(pages):
        full = os.path.realpath(os.path.join(root, *rel.split("/")))
        if os.path.commonpath((os.path.realpath(root), full)) == os.path.realpath(root) and os.path.isfile(full):
            os.remove(full)
    cfg.update({"version": 1, "crawl": {**(cfg.get("crawl") or {}), **options, "generated": pages}})
    cfg.setdefault("metadata", {"defaults": {}, "rules": []})
    cfg.setdefault("transforms", [])
    write_json(cfg_path, cfg)
    return {"name": name, "path": root, "pages": len(pages), "config": cfg}
