import asyncio
import hashlib
import inspect
import io
import json
import mimetypes
import os
import posixpath
import re
import shutil
import time
import zipfile
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from aiohttp import web
from . import ingest
from . import crawl
from . import import_manifest
from . import assistants
from . import search
from . import db as g_db_module
from .db import GeminiDB, category_ancestors
from .client import GeminiApiError, GeminiClient
from .upload_worker import UploadWorker
from .search_worker import SearchWorker

g_db = None
g_client = None
g_worker = None
g_search_worker = None

# Checked in order, first one set wins. Mirrors the `google` provider's precedence in
# providers.json, so a deployment that configured chat with GOOGLE_API_KEY doesn't
# silently get this extension disabled.
API_KEY_ENV = ("GOOGLE_API_KEY", "GEMINI_API_KEY")

# Deployment-wide extension config. Lives beside the anonymous user's data because `default`
# is the tail of the preference cascade, which makes it the global tier rather than a person's.
GLOBAL_CONFIG_NAME = "config.json"
g_config_cache = {"mtime": None, "data": {}}

# Metadata a file upload may carry, as query params or form fields. Keeps the Upload tab
# consistent with every other import option instead of being the one that arrives unlabelled.
UPLOAD_METADATA_FIELDS = (
    "category", "sourceUrl", "docType", "status", "locale", "product", "versions", "tags",
)


def metadata_from(values):
    out = {}
    for field in UPLOAD_METADATA_FIELDS:
        raw = values.get(field)
        if raw is None or raw == "":
            continue
        if field in ("versions", "tags"):
            out[field] = [v.strip() for v in str(raw).split(",") if v.strip()]
        else:
            out[field] = raw
    return out


def install(ctx):
    global g_client, g_worker, g_search_worker

    api_key = next((os.getenv(name) for name in API_KEY_ENV if os.getenv(name)), None)
    if not api_key:
        ctx.log(f"{' or '.join(API_KEY_ENV)} is not configured")
        ctx.disabled = True
        return

    def get_db():
        global g_db
        if g_db is None and GeminiDB:
            try:
                db_path = os.path.join(ctx.get_user_path(), "gemini", "gemini.sqlite")
                g_db = GeminiDB(ctx, db_path)
                ctx.register_shutdown_handler(g_db.db.close)
            except Exception as e:
                ctx.err("Failed to init GeminiDB", e)
        return g_db

    if not get_db():
        return

    g_client = GeminiClient(api_key=api_key)
    g_worker = UploadWorker(ctx, g_db, g_client)
    g_search_worker = SearchWorker(ctx, g_db)

    # Which role a caller needs for write operations. Unset (the default) means any signed-in
    # user, which is what a single-team deployment wants; set it to e.g. "Admin" or a custom
    # role to restrict ingest and metadata changes to the people who curate the corpus.
    write_role = os.getenv("GEMINI_WRITE_ROLE") or (ctx.get_config() or {}).get("gemini_write_role")

    def auth_error(request, role=None):
        """
        Returns a 401/403 response when the caller may not perform a write.

        Extension routes are not auth-gated by the server, and an anonymous caller resolves
        to `user IS NULL` - the shared scope every deployment starts with. Without this an
        unauthenticated request can create, upload to, sync and DELETE the shared filestores.
        Reads are left open: the null-user scope is the deliberate "shared with everyone here"
        bucket, and gating it would break existing single-user setups.
        """
        if not ctx.is_auth_enabled():
            return None
        if not ctx.get_username(request):
            return web.json_response(ctx.error_auth_required, status=401)
        required = role or write_role
        if not required:
            return None
        if required == "Admin" and ctx.is_admin(request):
            return None
        session = ctx.get_session(request) or {}
        if required in (session.get("roles") or []) or "Admin" in (session.get("roles") or []):
            return None
        return web.json_response(
            {"error": {"errorCode": "Forbidden", "message": f"Requires the '{required}' role"}},
            status=403,
        )

    def signed_in_error(request):
        """Assistant prompts and deployment IDs are private even when ordinary catalogue reads are shared."""
        if ctx.is_auth_enabled() and not ctx.get_username(request):
            return web.json_response(ctx.error_auth_required, status=401)
        return None

    def global_config_path():
        # `default` is the anonymous user and the tail of the preference cascade, so this file is
        # the deployment-wide tier rather than one person's settings. get_user_path() with no
        # argument already resolves to user/default.
        return os.path.join(ctx.get_user_path(), GLOBAL_CONFIG_NAME)

    def global_config():
        """
        Deployment-wide config, re-read whenever the file changes so an operator adding an
        import root does not have to restart the server.
        """
        path = global_config_path()
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            g_config_cache["mtime"], g_config_cache["data"] = None, {}
            return {}
        if g_config_cache["mtime"] != mtime:
            try:
                with open(path, encoding="utf-8") as f:
                    g_config_cache["data"] = json.load(f) or {}
            except Exception as e:
                # Reading a malformed config as "{}" silently changes who can import what, so
                # say it out loud and keep the last copy that parsed.
                ctx.log(f"gemini: could not parse {path}: {e}")
            g_config_cache["mtime"] = mtime
        return g_config_cache["data"]

    def normalize_root(raw):
        """
        A root as it should be stored and shown: trimmed, and without a trailing slash.

        `/srv/docs/` resolves to the same folder as `/srv/docs`, but keeping both spellings
        made the UI render value + resolved as two different paths. `/` is the exception - it
        is nothing but slashes, and stripping them would leave an empty root.
        """
        raw = str(raw or "").strip()
        if len(raw) > 1:
            raw = raw.rstrip("/")
        return raw

    def configured_import_roots():
        """The roots exactly as written in config (normalized), plus whether they came from there at all."""
        cfg = global_config()
        roots = (cfg.get("gemini") or {}).get("importRoots")
        if roots is None:
            roots = cfg.get("importRoots")
        if roots is None:
            return [], False
        return [normalize_root(r) for r in (roots or []) if normalize_root(r)], True

    def save_import_roots(roots):
        """
        Write `gemini.importRoots`, preserving everything else in the file.

        Stored as written rather than resolved, so an operator's `~/docs` stays portable and
        `$WORKSPACE` keeps tracking the workspace.
        """
        path = global_config_path()
        cfg = {}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    cfg = json.load(f) or {}
            except Exception as e:
                # Overwriting a file that failed to parse would discard whatever else is in it.
                raise Exception(f"Refusing to overwrite {path}: it does not parse ({e})") from e
        section = cfg.get("gemini")
        if not isinstance(section, dict):
            section = {}
        section["importRoots"] = roots
        cfg["gemini"] = section
        # A stale top-level key would keep winning for readers that check it first.
        cfg.pop("importRoots", None)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
        g_config_cache["mtime"] = None
        return cfg

    def describe_root(raw):
        """What the UI needs to show a root honestly: where it lands, and whether that's a trap."""
        resolved = raw
        if raw.startswith("$") and hasattr(ctx, "resolve_directory"):
            resolved = ctx.resolve_directory(raw) or ""
        resolved = ingest.resolve_path(resolved) if resolved else ""
        home = ingest.resolve_path("~")
        return {
            "value": raw,
            "resolved": resolved,
            "exists": bool(resolved) and os.path.isdir(resolved),
            # A root at / or at the home directory makes the rail decorative. Not refused - an
            # admin may mean it - but never applied silently.
            "broad": bool(resolved) and (resolved == os.sep or resolved == home),
        }

    def resolve_roots(raw):
        """Resolve a list of configured paths, expanding the server's $WORKSPACE/$TEMP aliases."""
        out = []
        for r in raw or []:
            r = str(r)
            if r.startswith("$") and hasattr(ctx, "resolve_directory"):
                r = ctx.resolve_directory(r)
                if not r:
                    continue
            out.append(ingest.resolve_path(r))
        return sorted(set(out))

    def allowed_directories(user=None):
        """The directories the server grants this user ($WORKSPACE, $TEMP and friends), never another user's."""
        if not hasattr(ctx, "resolve_allowed_directories"):
            return []
        return resolve_roots(ctx.resolve_allowed_directories(user))

    def trusted_import_roots(user=None):
        """
        Every folder a non-admin may import from, as resolved absolute paths.

        The union of `gemini.importRoots` in the global config and the directories the server
        already grants. A union rather than an override because the two answer the same question
        - "where may this deployment read from" - and configuring one should not silently revoke
        the other. It also means the list the UI shows is the list that is actually enforced.
        """
        configured, _ = configured_import_roots()
        return sorted(set(resolve_roots(configured)) | set(allowed_directories(user)))

    def assert_source_allowed(source, request=None):
        """
        A source that reads the filesystem is confined to the folders the deployment trusts.
        An ingest source is not a licence to read /etc.

        Admins are exempt: it is their machine, and the preview step lists every file before
        anything is read into a store. `is_admin()` is True whenever auth is disabled, which is
        what lets a single-user install import from anywhere without configuring it first.
        """
        config = source.get("config") or {}
        path = config.get("path")
        if not path:
            return
        if request is not None and ctx.is_admin(request):
            return
        # Crawl workspaces are created by this extension inside the caller's own user folder.
        # They are always a valid source for that same caller, regardless of deployment roots.
        user_imports = crawl.imports_root(ctx, ctx.get_username(request) if request is not None else None)
        if ingest.within_roots(path, [user_imports]):
            return
        roots = trusted_import_roots(ctx.get_username(request) if request is not None else None)
        if not roots:
            raise Exception(
                "No import folders are configured. An Admin can list them under "
                f'"gemini": {{"importRoots": [...]}} in {global_config_path()}'
            )
        if ingest.within_roots(path, roots):
            return
        # Name the roots: "outside the allowed directories" leaves the caller with nowhere to
        # go, and GET source-types discloses them to this same caller anyway.
        raise Exception(
            f"'{ingest.resolve_path(path)}' is outside the folders you may import from. "
            "Allowed: " + ", ".join(roots)
        )

    # Filter operators the schema depends on, with what each one enables. Unproven capabilities
    # are assumed present so nothing is disabled by default; a probe that fails downgrades them.
    capability_probes = [
        ('docType="guide"', "equality", "baseline (documented)"),
        ('docType="guide" AND status="published"', "and", "combining facets"),
        ('versions:"v8"', "listHas", "versions and tags as lists; categoryPath subtree filtering"),
        ("sortKey > 1700000000", "numeric", "staleness filtering on updatedAt"),
        ('docType="guide" OR docType="faq"', "or", "multi-select facets"),
        ('NOT status="deprecated"', "not", "excluding deprecated content by default"),
    ]
    capabilities_path = os.path.join(ctx.get_user_path(), "gemini", "capabilities.json")

    def load_capabilities():
        try:
            if os.path.exists(capabilities_path):
                with open(capabilities_path, encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            ctx.err("Failed reading capabilities", e)
        return {
            "probed": False,
            "note": "Not probed. Assuming full AIP-160 support; POST capabilities/probe to verify.",
            "operators": {key: True for _, key, _ in capability_probes},
            "enables": {key: why for _, key, why in capability_probes},
        }

    def save_capabilities(result):
        try:
            os.makedirs(os.path.dirname(capabilities_path), exist_ok=True)
            with open(capabilities_path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)
        except Exception as e:
            ctx.err("Failed saving capabilities", e)

    def run_capability_probe():
        """
        Determine which filter operators this deployment actually supports.

        Two fixtures with contrasting metadata, and a question that retrieves both. An operator is
        judged by *which* document comes back, not by whether anything does: a filter that returns
        both is being ignored, one that returns neither is a retrieval miss rather than a rejection.

        Retrieval through generate_content is nondeterministic, so each expression is retried and a
        no-filter baseline gates the whole run - without it a quiet retrieval failure reads as
        "every operator is unsupported", which is how the first version of this got it wrong.
        """
        import tempfile

        # The same values under several key spellings, so a filter that fails can be attributed
        # to the key rather than the operator. camelCase is what the schema uses today.
        def meta(doc_type, status, sort_key, versions):
            return [
                {"key": "docType", "string_value": doc_type},      # camelCase
                {"key": "doctype", "string_value": doc_type},      # lowercase
                {"key": "doc_type", "string_value": doc_type},     # snake_case
                {"key": "status", "string_value": status},         # lowercase control
                {"key": "sortKey", "numeric_value": sort_key},     # camelCase numeric
                {"key": "sortkey", "numeric_value": sort_key},     # lowercase numeric
                {"key": "versions", "string_list_value": {"values": versions}},
            ]

        fixtures = [
            {
                "key": "alpha",
                "text": "The Alpha widget costs exactly one hundred dollars and ships from Perth.",
                "meta": meta("guide", "published", 1755648000, ["v7", "v8"]),
            },
            {
                "key": "beta",
                "text": "The Beta widget costs exactly two hundred dollars and ships from Sydney.",
                "meta": meta("faq", "deprecated", 1600000000, ["v6"]),
            },
        ]
        # (expression, expected fixture keys, operator, what it enables)
        checks = [
            ('status="published"', {"alpha"}, "equality", "baseline equality, lowercase key"),
            ('docType="guide"', {"alpha"}, "keyCamel", "camelCase keys - what the schema uses"),
            ('doctype="guide"', {"alpha"}, "keyLower", "all-lowercase keys"),
            ('doc_type="guide"', {"alpha"}, "keySnake", "snake_case keys"),
            ('versions:"v8"', {"alpha"}, "listHas",
             "versions and tags as lists; categoryPath subtree filtering"),
            ("sortkey > 1700000000", {"alpha"}, "numeric", "staleness filtering on updatedAt"),
            ("sortKey > 1700000000", {"alpha"}, "numericCamel", "numeric comparison on a camelCase key"),
            ('status="published" AND versions:"v8"', {"alpha"}, "and", "combining facets"),
            ('status="published" OR status="deprecated"', {"alpha", "beta"}, "or", "multi-select facets"),
            ('NOT status="deprecated"', {"alpha"}, "not", "excluding deprecated content by default"),
        ]
        question = "According to the documents, list every widget and exactly what it costs."
        model = os.getenv("GEMINI_PROBE_MODEL", "gemini-flash-latest")

        def cited(metadata_filter, attempts=3):
            """Fixture keys the answer was grounded in, best of N (retrieval is nondeterministic)."""
            best = set()
            for attempt in range(attempts):
                file_search = {"file_search_store_names": [store.name], "top_k": 10}
                if metadata_filter:
                    file_search["metadata_filter"] = metadata_filter
                try:
                    res = g_client.models.generate_content(
                        model=model, contents=question, config={"tools": [{"file_search": file_search}]}
                    )
                except Exception as e:
                    ctx.dbg(f"Probe '{metadata_filter}' errored: {e}")
                    return set(), str(e)[:200]
                found = set()
                for cand in res.candidates or []:
                    gm = getattr(cand, "grounding_metadata", None)
                    for chunk in (getattr(gm, "grounding_chunks", None) or []) if gm else []:
                        rc = getattr(chunk, "retrieved_context", None)
                        title = ((getattr(rc, "title", "") or "") if rc else "").lower()
                        for fx in fixtures:
                            if fx["key"] in title:
                                found.add(fx["key"])
                if len(found) > len(best):
                    best = found
                if attempt < attempts - 1 and not found:
                    time.sleep(2)
            return best, None

        store = g_client.file_search_stores.create(config={"display_name": "llms-filter-probe"})
        operators, detail = {}, {}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                for fx in fixtures:
                    path = os.path.join(tmp, f"{fx['key']}.txt")
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(fx["text"])
                    op = g_client.file_search_stores.upload_to_file_search_store(
                        file_search_store_name=store.name,
                        file=path,
                        config={"display_name": f"{fx['key']}.txt", "custom_metadata": fx["meta"]},
                    )
                    while not op.done:
                        time.sleep(3)
                        op = g_client.operations.get(op)
                    if op.error:
                        raise Exception(op.error.message)
                time.sleep(8)

                # Gate on the baseline: if an unfiltered query can't retrieve both fixtures,
                # nothing below can be trusted.
                baseline, err = cited(None, attempts=4)
                if len(baseline) < len(fixtures):
                    return {
                        "probed": False,
                        "probedAt": datetime.now().isoformat(" "),
                        "error": (
                            f"Baseline retrieval returned {sorted(baseline) or 'nothing'} instead of "
                            f"both fixtures{': ' + err if err else ''}. Filter results would be "
                            "meaningless, so nothing was concluded."
                        ),
                        "operators": {key: True for _, _, key, _ in checks},
                        "enables": {key: why for _, _, key, why in checks},
                    }

                for expr, want, key, _ in checks:
                    got, err = cited(expr)
                    operators[key] = got == want
                    detail[key] = {
                        "expression": expr,
                        "expected": sorted(want),
                        "got": sorted(got),
                        "verdict": (
                            "ok" if got == want
                            else "error" if err
                            else "filter ignored" if got == {f["key"] for f in fixtures}
                            else "rejected or no match"
                        ),
                        **({"error": err} if err else {}),
                    }
        finally:
            try:
                g_client.file_search_stores.delete(name=store.name, config={"force": True})
            except Exception as e:
                ctx.err("Failed deleting probe store", e)

        return {
            "probed": True,
            "probedAt": datetime.now().isoformat(" "),
            "model": model,
            "operators": operators,
            "enables": {key: why for _, _, key, why in checks},
            "detail": detail,
        }

    def filestore_dto(row):
        return row and g_db.to_dto(row, ["metadata", "facets"])

    def source_dto(row):
        return row and g_db.to_dto(
            row,
            ["config", "category", "rules", "include", "exclude", "extract", "chunking", "volatile", "cursor"],
        )

    def is_saved_source(row):
        return bool(row.get("lastRunId") or (source_dto(row).get("config") or {}).get("saved"))

    def editable_source(row, request):
        source = source_dto(row)
        path = (source.get("config") or {}).get("manifestPath")
        if not path and source.get("type") == "folder" and source.get("config", {}).get("path"):
            candidate = os.path.join(source["config"]["path"], crawl.MANIFEST)
            if os.path.isfile(candidate):
                path = candidate
        if path:
            assert_source_allowed({"config": {"path": path}}, request)
            source = import_manifest.load(path, source)
            cfg = crawl.read_json(path)
            source["importOptions"] = {key: cfg[key] for key in ("crawl", "transforms") if key in cfg}
        if is_saved_source(row):
            source["config"] = {**source.get("config", {}), "saved": True}
        assert_source_allowed(source, request)
        return source

    def save_source_settings(source, request, options=None):
        assert_source_allowed(source, request)
        path = (source.get("config") or {}).get("manifestPath")
        if path:
            assert_source_allowed({"config": {"path": path}}, request)
        if source.get("type") not in ingest.SOURCE_TYPES:
            raise ValueError("Unknown source type")
        path = import_manifest.save(source, options)
        source["config"] = {**source["config"], "manifestPath": path, "metadataSpecified": False, "saved": True}
        return source

    import_registration_locks = {}

    async def register_import_manifest(path, filestore_id, user, request):
        async with import_registration_locks.setdefault(user, asyncio.Lock()):
            return await _register_import_manifest(path, filestore_id, user, request)

    async def _register_import_manifest(path, filestore_id, user, request):
        if not g_db.get_filestore(int(filestore_id), user=user):
            raise ValueError("Filestore does not exist")
        assert_source_allowed({"config": {"path": path}}, request)
        source = import_manifest.load(path)
        assert_source_allowed(source, request)
        original_path = source["config"]["manifestPath"]
        cfg = crawl.read_json(original_path)
        if (cfg.get("crawl") or {}).get("url"):
            assert_source_allowed({"config": {"path": os.path.dirname(original_path)}}, request)
            path = await asyncio.to_thread(crawl.import_workspace, ctx, user, original_path)
            source = import_manifest.load(path)
            assert_source_allowed(source, request)
        manifest_path = source["config"]["manifestPath"]
        if (cfg.get("crawl") or {}).get("url"):
            # Migrate the previous hashed workspaces without changing source/document IDs.
            aliases = await asyncio.to_thread(crawl.legacy_manifests, ctx, user, os.path.dirname(manifest_path))
            for alias in aliases:
                old_root, new_root = os.path.dirname(alias), os.path.dirname(manifest_path)
                for row in g_db.query_sources({}, user=user):
                    config = source_dto(row).get("config") or {}
                    candidate = config.get("manifestPath") or os.path.join(config.get("path") or "", crawl.MANIFEST)
                    if ingest.resolve_path(candidate) == alias:
                        relative = os.path.relpath(config.get("path") or old_root, old_root)
                        await g_db.update_source_async(row["id"], {"config": {**config,
                            "path": os.path.join(new_root, relative), "manifestPath": manifest_path, "saved": True}}, user=user)
                await g_db.relocate_source_documents_async(alias, manifest_path, user=user)
                await asyncio.to_thread(shutil.rmtree, old_root)
        for row in g_db.query_sources({"filestoreId": int(filestore_id)}, user=user):
            config = source_dto(row).get("config") or {}
            candidate = config.get("manifestPath")
            if not candidate and row.get("type") == "folder" and config.get("path"):
                candidate = os.path.join(config["path"], crawl.MANIFEST)
            same_workspace = False
            if candidate and (cfg.get("crawl") or {}).get("url"):
                try:
                    assert_source_allowed({"config": {"path": candidate}}, request)
                    same_workspace = crawl.workspace_target(ctx, user, candidate) == os.path.dirname(manifest_path)
                except Exception:
                    pass
            if candidate and (ingest.resolve_path(candidate) in (manifest_path, original_path) or same_workspace):
                if ingest.resolve_path(candidate) != manifest_path or original_path != manifest_path:
                    source = save_source_settings(source, request)
                    await g_db.update_source_async(row["id"], {
                        key: source[key] for key in (*import_manifest.SOURCE_FIELDS, "config", "rules") if key in source}, user=user)
                    await g_db.relocate_source_documents_async(ingest.resolve_path(candidate), manifest_path, user=user)
                elif not is_saved_source(row):
                    await g_db.update_source_async(row["id"], {"config": {**config, "manifestPath": manifest_path, "saved": True}}, user=user)
                return editable_source(g_db.get_source(row["id"], user=user), request)
        if saved_source_name_conflict(filestore_id, source.get("name"), user):
            raise ValueError(f"A saved import named '{source['name']}' already exists")
        source.update({"filestoreId": int(filestore_id), "enabled": 1})
        source.setdefault("onDelete", "tombstone")
        source.setdefault("extractorVer", ingest.EXTRACTOR_VERSION)
        source = save_source_settings(source, request)
        source_id = await g_db.create_source_async(source, user=user)
        await g_db.attach_source_documents_async(source_id, filestore_id, manifest_path, user=user)
        return editable_source(g_db.get_source(source_id, user=user), request)

    source_run_locks = {}

    async def run_source_pipeline(source_row, user, **options):
        # A preview, Run, and Sync Store can target the same saved import concurrently.
        # Build the next diff only after the preceding run's document writes commit.
        key = (user, source_row.get("id"))
        lock = source_run_locks.setdefault(key, asyncio.Lock())
        async with lock:
            return await _run_source_pipeline(source_row, user, **options)

    async def _run_source_pipeline(source_row, user, dry_run=True, override=None, confirm_deletes=False,
                                  selected_adds=None, add_all=True, request=None, refresh_crawl=False,
                                  start_uploads=True):
        """
        discover -> fetch -> extract -> derive -> diff, then (unless dry run) apply.

        Applying writes documents locally and queues them; the upload worker does the spending.
        A run that fails during discovery never computes deletions, so a half-finished crawl
        can't conclude the other half was deleted.
        """
        manifest_path = (source_row.get("config") or {}).get("manifestPath")
        if not manifest_path and is_saved_source(source_row) and source_row.get("type") == "folder":
            candidate = os.path.join(source_row["config"]["path"], crawl.MANIFEST)
            if os.path.isfile(candidate):
                manifest_path = candidate
        if manifest_path:
            assert_source_allowed({"config": {"path": manifest_path}}, request)
            if (crawl.read_json(manifest_path).get("crawl") or {}).get("url"):
                source_row = await register_import_manifest(manifest_path, source_row["filestoreId"], user, request)
                manifest_path = source_row["config"]["manifestPath"]
            if is_saved_source(source_row):
                source_row = import_manifest.load(manifest_path, source_row)
        assert_source_allowed(source_row, request)
        filestore_id = int(source_row.get("filestoreId"))
        if refresh_crawl and source_row.get("type") == "folder":
            path = (source_row.get("config") or {}).get("path")
            cfg = crawl.read_json(manifest_path or os.path.join(path, crawl.MANIFEST))
            if (cfg.get("crawl") or {}).get("url"):
                root = os.path.dirname(manifest_path) if manifest_path else path
                assert_source_allowed({"config": {"path": root}}, request)
                options = {**cfg["crawl"], "name": cfg["crawl"].get("name") or os.path.basename(root)}
                await crawl.crawl_site(ctx, user, options, target_path=root)
                crawl.apply_transforms(root, cfg.get("transforms") or [])
        source = ingest.create_source(ctx, source_row.get("type"), source_row.get("config") or {})
        existing = g_db.documents_by_source_key(filestore_id, source_row.get("id"), user=user)

        run_id = await g_db.create_run_async(
            {"sourceId": source_row.get("id"), "status": "preview" if dry_run else "running", "dryRun": 1 if dry_run else 0},
            user=user,
        )

        try:
            plan = await asyncio.get_running_loop().run_in_executor(
                None, lambda: ingest.build_plan(
                    source_row, source, existing, override,
                    on_warning=lambda warning: ctx.log(f"Warning: {warning}"),
                )
            )
        except Exception as e:
            g_db.update_run(run_id, {"status": "failed", "completedAt": datetime.now(),
                                     "error": ctx.error_message(e)}, user=user)
            raise
        finally:
            if hasattr(source, "close"):
                source.close()

        new_source_documents = []
        if not add_all:
            selected_adds = set(selected_adds or [])
            retained = []
            for entry in plan.add:
                key = f"{source_row.get('id')}:{entry['sourceKey']}"
                if key in selected_adds:
                    retained.append(entry)
                else:
                    new_source_documents.append({
                        **{k: v for k, v in entry.items() if k != "text"},
                        "key": key, "sourceId": source_row.get("id"),
                        "sourceName": source_row.get("name"),
                    })
            plan.add = retained

        summary = plan.summary()
        # Unchanged means the source matches the local cache, not that Gemini has received it.
        # Preserve the existing queue entries and report them so Run can resume their uploads.
        summary["pendingUploads"] = sum(
            not existing[entry["sourceKey"]].get("uploadedAt")
            and not existing[entry["sourceKey"]].get("error")
            for entry in plan.unchanged)
        summary["newSourceDocuments"] = new_source_documents
        summary["newSourceCount"] = len(new_source_documents)
        refusal = None if confirm_deletes else ingest.check_delete_rails(plan, len(existing))
        if refusal:
            summary["deleteRefused"] = refusal
            plan.removed = []

        if dry_run:
            g_db.update_run(run_id, {"status": "preview", "completedAt": datetime.now(),
                                     "plan": summary, **plan.counts()}, user=user)
            return {"runId": run_id, "dryRun": True, **summary}

        applied = await apply_plan(plan, source_row, filestore_id, user)
        delete_errors = applied.get("deleteErrors") or []
        error = "; ".join(entry["error"] for entry in delete_errors) or None
        g_db.update_run(run_id, {"status": "completed", "completedAt": datetime.now(),
                                 "plan": {**summary, **applied}, "error": error, **plan.counts()}, user=user)
        await g_db.update_source_async(source_row.get("id"), {"lastRunId": run_id, "lastRunAt": datetime.now(),
                                                             "error": error}, user=user)
        if start_uploads:
            g_worker.start()
        return {"runId": run_id, "sourceId": source_row.get("id"), "dryRun": False, **summary, **applied,
                "uploadTotal": applied["queued"] + summary["pendingUploads"],
                "newSourceDocuments": new_source_documents}

    async def apply_plan(plan, source_row, filestore_id, user):
        """Write the plan locally and queue it; nothing reaches Gemini until the worker runs."""
        source_id = source_row.get("id")
        queued = 0

        for entry in plan.add + plan.change + plan.metadata_only:
            text = entry.get("text") or ""
            content = text.encode("utf-8")
            sha256_hash = hashlib.sha256(content).hexdigest()
            ext = ingest.ext_of(entry["sourceKey"]) or "txt"
            # Extracted text is what gets indexed, so HTML is cached as the markdown it became.
            if ext in ingest.HTML_EXTS:
                ext = "md"
            save_filename = f"{sha256_hash}.{ext}"
            relative_path = f"{sha256_hash[:2]}/{save_filename}"
            full_path = ctx.get_cache_path(relative_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "wb") as f:
                f.write(content)

            doc = {
                "filestoreId": filestore_id,
                "sourceId": source_id,
                "sourceManifestPath": (source_row.get("config") or {}).get("manifestPath"),
                "sourceKey": entry["sourceKey"],
                "sourceEtag": entry.get("sourceEtag"),
                "displayName": entry.get("displayName"),
                "filename": save_filename,
                "url": f"/~cache/{relative_path}",
                "hash": sha256_hash,
                "size": len(content),
                "mimeType": "text/markdown" if ext == "md" else ctx.get_file_mime_type(entry["sourceKey"]),
                "contentHash": entry.get("contentHash"),
                "metadataHash": entry.get("metadataHash"),
                "extractorVer": entry.get("extractorVer"),
                "tombstonedAt": None,
                "error": None,
                "uploadedAt": None,  # queues it for the worker
            }
            for field in ("category", "categoryPath", "docType", "status", "locale", "product",
                          "versions", "tags", "sourceUrl", "sourceUpdatedAt"):
                if field in entry:
                    # Root-level documents use one representation in storage. Older imports wrote
                    # an empty string, so query/facet compatibility remains in db.py, but new rows
                    # are NULL and naturally participate in the uncategorised filter.
                    doc[field] = None if field == "category" and not entry[field] else entry[field]
            doc["searchHash"] = search.desired_hash(doc)

            existing_id = entry.get("id")
            if existing_id:
                # Keep the remote name so the upload worker can remove the superseded copy
                # only after its replacement succeeds.
                await g_db.update_document_async(existing_id, doc, user=user)
            else:
                await g_db.create_document_async(doc, user=user)
            queued += 1

        on_delete = source_row.get("onDelete") or "tombstone"
        removed = 0
        delete_errors = []
        for doc in plan.removed:
            if on_delete == "ignore":
                continue
            if doc.get("name"):
                try:
                    await asyncio.to_thread(g_client.file_search_stores.documents.delete,
                                            name=doc.get("name"), config={"force": True}, timeout=60)
                except GeminiApiError as e:
                    if e.code != 404:
                        ctx.err(f"Could not delete {doc.get('name')}", e)
                        delete_errors.append({"id": doc["id"], "displayName": doc.get("displayName"),
                                              "error": ctx.error_message(e)})
                        continue
                except Exception as e:
                    ctx.err(f"Could not delete {doc.get('name')}", e)
                    delete_errors.append({"id": doc["id"], "displayName": doc.get("displayName"),
                                          "error": ctx.error_message(e)})
                    continue
            if on_delete == "remove":
                await g_db.delete_document_async(doc.get("id"), user=user)
            else:
                g_db.remove_search_document(doc.get("id"))
                await g_db.update_document_async(
                    doc.get("id"), {"tombstonedAt": datetime.now(), "name": None, "state": "REMOVED_UPSTREAM"},
                    user=user,
                )
            removed += 1

        g_search_worker.start()
        return {"queued": queued, "searchQueued": queued, "removedApplied": removed, "deleteErrors": delete_errors}

    def document_dto(row):
        # SQLite stores list metadata as JSON text. Decode it at the API boundary so the UI gets
        # arrays (and renders `autoquery`) rather than JSON source text (`["autoquery"]`).
        if not row:
            return row
        dto = g_db.to_dto(row, ["metadata", "customMetadata", "categoryPath", "versions", "tags"])
        url = row.get("url") or ""
        dto["localFileExists"] = url.startswith("/~cache/") and os.path.isfile(
            ctx.get_cache_path(url[len("/~cache/"):]))
        return dto

    async def query_filestores(request):
        user = ctx.get_username(request)
        rows = g_db.query_filestores(request.query, user=user)
        for row in rows:
            if row.get("activeDocumentsCount") is None or row.get("sizeBytes") is None:
                store_name = row.get("name")
                fs_id = row.get("id")
                updated = False
                if store_name and g_client:
                    try:
                        res = g_client.file_search_stores.get(name=store_name)
                        if res:
                            row["activeDocumentsCount"] = res.active_documents_count
                            row["pendingDocumentsCount"] = res.pending_documents_count
                            row["failedDocumentsCount"] = res.failed_documents_count
                            row["sizeBytes"] = res.size_bytes
                            g_db.update_filestore(
                                fs_id,
                                {
                                    "activeDocumentsCount": res.active_documents_count,
                                    "pendingDocumentsCount": res.pending_documents_count,
                                    "failedDocumentsCount": res.failed_documents_count,
                                    "sizeBytes": res.size_bytes,
                                },
                                user=user,
                            )
                            updated = True
                    except Exception as e:
                        ctx.err(f"Failed to fetch filestore stats from Gemini for {store_name}", e)
                if not updated and (row.get("activeDocumentsCount") is None or row.get("sizeBytes") is None):
                    stats = g_db.get_filestore_stats(fs_id, user=user)
                    if stats:
                        if row.get("activeDocumentsCount") is None:
                            row["activeDocumentsCount"] = stats.get("count", 0)
                        if row.get("sizeBytes") is None:
                            row["sizeBytes"] = stats.get("size", 0)
        dtos = [filestore_dto(row) for row in rows]
        return web.json_response(dtos)

    ctx.add_get("filestores", query_filestores)

    async def create_filestore(request):
        denied = auth_error(request)
        if denied:
            return denied
        user = ctx.get_username(request)
        filestore = await request.json()
        display_name = filestore.get("displayName")
        if not display_name:
            raise Exception("displayName is required")

        ctx.dbg(f"Creating filestore {display_name} in Gemini...")
        result = g_client.file_search_stores.create(config={"display_name": display_name})
        ctx.dbg(result or None)
        if result:
            filestore.update(
                {
                    "name": result.name,
                    "displayName": result.display_name,
                    "createTime": result.create_time,
                    "updateTime": result.update_time,
                    "activeDocumentsCount": result.active_documents_count,
                    "pendingDocumentsCount": result.pending_documents_count,
                    "failedDocumentsCount": result.failed_documents_count,
                    "sizeBytes": result.size_bytes,
                }
            )
            id = await g_db.create_filestore_async(filestore, user=user)
            row = g_db.get_filestore(id, user=user)
        else:
            raise Exception("Failed to create filestore in Gemini")

        return web.json_response(filestore_dto(row) if row else "")

    ctx.add_post("filestores", create_filestore)

    async def filestore_delete_summary(request):
        denied = auth_error(request)
        if denied:
            return denied
        id = request.match_info["id"]
        summary = g_db.filestore_delete_summary(id, user=ctx.get_username(request))
        if not summary:
            raise web.HTTPNotFound(text="File Store does not exist")
        if summary.get("name"):
            try:
                remote = g_client.file_search_stores.get(name=summary["name"])
                summary.update({
                    "remoteStoreExists": True,
                    "remoteDocuments": sum(int(value or 0) for value in (
                        remote.active_documents_count,
                        remote.pending_documents_count,
                        remote.failed_documents_count,
                    )),
                    "remoteDocumentBytes": int(remote.size_bytes or 0),
                })
            except GeminiApiError as e:
                if e.code == 404:
                    summary.update({
                        "remoteStoreExists": False,
                        "remoteDocuments": 0,
                        "remoteDocumentBytes": 0,
                    })
                else:
                    ctx.err(f"Could not refresh delete summary for {summary['name']}", e)
            except Exception as e:
                # The stored counts are still a useful confirmation preview. A transient stats
                # failure must not make an already-unavailable remote store impossible to clean up.
                ctx.err(f"Could not refresh delete summary for {summary['name']}", e)
        return web.json_response(summary)

    ctx.add_get("filestores/{id}/delete-summary", filestore_delete_summary)

    async def delete_filestore(request):
        denied = auth_error(request)
        if denied:
            return denied
        id = request.match_info["id"]
        user = ctx.get_username(request)
        row = g_db.get_filestore(id, user=user)
        if not row:
            raise web.HTTPNotFound(text="File Store does not exist")

        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            body = {}
        confirmation = body.get("confirm") if isinstance(body, dict) else None
        display_name = str(row.get("displayName") or "")
        if confirmation != display_name:
            return web.json_response(ctx.create_error_response(
                f'Type "{display_name}" to confirm permanent deletion', "ConfirmationRequired"), status=400)

        name = row.get("name")
        if name:
            ctx.dbg(f"Deleting filestore {name} in Gemini...")
            try:
                g_client.file_search_stores.delete(name=name, config={"force": True})
            except GeminiApiError as e:
                if e.code == 404:
                    ctx.dbg(f"Filestore {name} was already deleted in Gemini")
                else:
                    raise
        else:
            ctx.dbg(f"Filestore {id} has no name, skipping Gemini deletion...")

        ctx.dbg(f"Filestore {name} deleted in Gemini, removing all related local data...")
        impact = g_db.delete_filestore(id, user=user)
        if not impact:
            raise web.HTTPNotFound(text="File Store does not exist")
        return web.json_response({"deleted": impact})

    ctx.add_delete("filestores/{id}", delete_filestore)

    def cache_bytes(content, filename, mimetype):
        """Write to the content-addressed cache and return (relativePath, url, savedName)."""
        sha256_hash = hashlib.sha256(content).hexdigest()
        ext = filename.rsplit(".", 1)[1].lower() if "." in filename else ""
        if not ext:
            ext = (mimetypes.guess_extension(mimetype) or "").lstrip(".")
        save_filename = f"{sha256_hash}.{ext}" if ext else sha256_hash
        relative_path = f"{sha256_hash[:2]}/{save_filename}"
        full_path = ctx.get_cache_path(relative_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "wb") as f:
            f.write(content)
        with open(os.path.splitext(full_path)[0] + ".info.json", "w") as f:
            json.dump(
                {"date": int(time.time()), "url": f"/~cache/{relative_path}", "size": len(content),
                 "type": mimetype, "name": filename},
                f,
            )
        return sha256_hash, save_filename, f"/~cache/{relative_path}"

    def expand_zip(content, base_category):
        return expand_zip_with_metadata(content, base_category)

    def expand_zip_with_metadata(content, base_category, override_metadata=None):
        """
        Turn an uploaded archive into the documents inside it.

        A zip is a transport, not a document - indexing the archive itself would produce one
        useless blob. Entries keep their internal folder structure as their category, and a single
        wrapper directory (what every GitHub "Download ZIP" produces) is stripped so categories
        don't all start with `repo-main/`.
        """
        def merge_rules(parent, child):
            parent, child = parent or {}, child or {}
            return {
                "defaults": {**(parent.get("defaults") or {}), **(child.get("defaults") or {})},
                "rules": [*(parent.get("rules") or []), *(child.get("rules") or [])],
            }

        def keep(name):
            key = name.replace("\\", "/").lstrip("/")
            return not ("__MACOSX/" in key or ingest.matches_any(key, ingest.DEFAULT_EXCLUDES))

        entries = []
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            # Junk is excluded *before* looking for a wrapper: a stray __MACOSX/ entry would
            # otherwise make the archive look like it has two roots and defeat the strip.
            names = [i.filename for i in zf.infolist() if not i.is_dir() and keep(i.filename)]
            tops = {n.split("/", 1)[0] for n in names if "/" in n}
            wrapper = next(iter(tops)) if len(tops) == 1 and all("/" in n for n in names) else None

            normalized = {}
            for info in zf.infolist():
                key = info.filename.replace("\\", "/").lstrip("/")
                if wrapper and key.startswith(wrapper + "/"):
                    key = key[len(wrapper) + 1 :]
                normalized[key] = info

            manifests = {}
            for key, info in normalized.items():
                if key == "import.json" or key.endswith("/import.json"):
                    try:
                        manifests[posixpath.dirname(key)] = json.loads(zf.read(info.filename).decode("utf-8"))
                    except Exception as e:
                        raise Exception(f"Invalid {key}: {e}") from e

            for info in zf.infolist():
                if info.is_dir() or not keep(info.filename):
                    continue
                key = info.filename.replace("\\", "/").lstrip("/")
                if wrapper and key.startswith(wrapper + "/"):
                    key = key[len(wrapper) + 1 :]
                if not key:
                    continue
                raw = zf.read(info.filename)

                # Same extraction the ingest pipeline uses, so a zip and a folder of the same
                # content produce identical documents. An unsupported type (a PDF) is passed
                # through as-is for Gemini to handle rather than dropped.
                text, front, skip = ingest.extract(raw, key)
                if skip and skip.startswith("unsupported"):
                    payload, name = raw, key
                elif skip:
                    ctx.dbg(f"Skipping {key} from archive: {skip}")
                    continue
                else:
                    payload = text.encode("utf-8")
                    name = key[:-len(key.split(".")[-1]) - 1] + ".md" if ingest.ext_of(key) in ingest.HTML_EXTS else key

                folder = posixpath.dirname(key)
                inherited = {}
                current = ""
                for directory in ["", *[posixpath.join(*folder.split("/")[:i]) for i in range(1, len(folder.split("/")) + 1) if folder]]:
                    cfg = manifests.get(directory) or {}
                    inherited = merge_rules(inherited, cfg.get("metadata"))
                page_meta, _ = ingest.derive_metadata(key, inherited, front, override=override_metadata)
                parts = [p for p in (base_category, folder) if p]
                entries.append({
                    "key": key,
                    "displayName": front.get("title") or posixpath.basename(name),
                    "content": payload,
                    "category": "/".join(parts) or None,
                    "metadata": page_meta or {},
                    "route": front.get("route"),
                })
        return entries

    async def upload_to_filestore(request):
        denied = auth_error(request)
        if denied:
            return denied
        user = ctx.get_username(request)
        id = request.match_info["id"]
        ctx.log(f"upload_to_filestore {id} {user if user else ''}")
        # Metadata travels with the upload, so a file arrives labelled rather than needing a bulk
        # backfill afterwards - the same rule that makes ingest the place metadata comes from.
        meta = metadata_from(request.query)
        category = meta.pop("category", None)
        require_source_url = str(request.query.get("requireSourceUrl") or "").lower() in ("1", "true", "yes", "on")

        filestore = g_db.get_filestore(id, user=user)
        if not filestore:
            raise Exception("Filestore does not exist")

        doc_ids = []
        reader = await request.multipart()

        field = await reader.next()
        while field:
            # A metadata field applies to every file that follows it in the request until the next
            # one overrides it, so one POST can carry a whole set with differing values. The query
            # string sets the request-wide default.
            if field.name in UPLOAD_METADATA_FIELDS:
                value = (await field.read(decode=True)).decode("utf-8").strip() or None
                if field.name == "category":
                    category = value
                elif field.name in ("versions", "tags"):
                    meta[field.name] = [v.strip() for v in (value or "").split(",") if v.strip()]
                else:
                    meta[field.name] = value
                field = await reader.next()
                continue

            if field.name == "requireSourceUrl":
                require_source_url = (await field.read(decode=True)).decode("utf-8").strip().lower() in ("1", "true", "yes", "on")
                field = await reader.next()
                continue

            if (field.name != "file" and not field.name.startswith("file")) or not field.filename:
                field = await reader.next()
                continue

            filename = field.filename
            content = await field.read()

            if filename.lower().endswith(".zip"):
                entries = expand_zip_with_metadata(content, category, meta)
                ctx.log(f"Expanded {filename} into {len(entries)} document(s)")
            else:
                ext = ingest.ext_of(filename)
                if ext in ingest.HTML_EXTS:
                    text, front, skip = ingest.extract(content, filename, {"minWords": 0})
                    if skip:
                        ctx.log(f"Skipping {filename}: {skip}")
                        entries = []
                    else:
                        markdown_name = filename.rsplit(".", 1)[0] + ".md"
                        entries = [{"key": markdown_name, "displayName": front.get("title") or markdown_name,
                                    "content": text.encode("utf-8"), "category": category,
                                    "route": front.get("route")}]
                else:
                    entries = [{"key": filename, "displayName": filename, "content": content,
                                "category": category}]

            for entry in entries:
                mimetype = ctx.get_file_mime_type(entry["displayName"])
                sha, save_filename, url = cache_bytes(entry["content"], entry["displayName"], mimetype)
                doc = {
                    "filename": save_filename,
                    "url": url,
                    "hash": sha,
                    "size": len(entry["content"]),
                    "displayName": entry["displayName"],
                    "mimeType": mimetype,
                    "filestoreId": int(id),
                    "sourceKey": entry["key"],
                    "category": entry["category"],
                    "categoryPath": category_ancestors(entry["category"]),
                    "contentHash": ingest.content_hash(
                        entry["content"].decode("utf-8", "ignore") if len(entry["content"]) < 5_000_000 else ""
                    ),
                    "error": None,
                    "uploadedAt": None,
                    **entry.get("metadata", meta),
                }
                # Uploads don't run through build_plan, so the same expansion has to happen here
                # or a `{category}/{name}` template would be stored with its braces intact.
                if doc.get("sourceUrl"):
                    expanded_url = ingest.expand_template(
                        doc["sourceUrl"],
                        ingest.template_values(entry["key"], entry["category"], entry["displayName"],
                                               route=entry.get("route")),
                        lambda warning: ctx.log(f"Warning: {entry['key']}: {warning}"),
                    )
                    if expanded_url is None:
                        doc.pop("sourceUrl", None)
                    else:
                        doc["sourceUrl"] = expanded_url
                if require_source_url and not str(doc.get("sourceUrl") or "").strip():
                    ctx.log(f"Skipping {entry['key']}: Source URL is required")
                    continue
                doc["searchHash"] = search.desired_hash(doc)
                # Re-uploading the same path replaces in place. Identity is the source key, so a
                # second upload of the same archive updates rather than duplicating - and the
                # unique index would reject an insert anyway.
                prior = g_db.find_document({"filestoreId": int(id), "sourceKey": entry["key"]}, user=user)
                if prior:
                    if prior.get("name"):
                        try:
                            g_client.file_search_stores.documents.delete(
                                name=prior.get("name"), config={"force": True})
                        except GeminiApiError as e:
                            if e.code != 404:
                                ctx.err(f"Could not delete {prior.get('name')}", e)
                        except Exception as e:
                            ctx.err(f"Could not delete {prior.get('name')}", e)
                    doc["name"] = None
                    await g_db.update_document_async(prior.get("id"), doc, user=user)
                    doc_ids.append(prior.get("id"))
                else:
                    doc_ids.append(await g_db.create_document_async(doc, user=user))

            field = await reader.next()

        docs = g_db.query_documents({"ids_in": doc_ids}, user=user) if doc_ids else []
        g_worker.start()
        g_search_worker.start()

        return web.json_response(docs)

    ctx.add_post("filestores/{id}/upload", upload_to_filestore)

    async def query_documents(request):
        rows = g_db.query_documents(request.query, user=ctx.get_username(request))
        dtos = [document_dto(row) for row in rows]
        return web.json_response(dtos)

    ctx.add_get("documents", query_documents)

    async def count_documents(request):
        """
        How many documents the *current* query matches.

        Paging used to infer this from a separate category list, which knew nothing about search,
        facet filters or the root's no-category rule - so the page count routinely disagreed with
        the rows on screen. Counting the same filter the rows come from is the only way the two
        can agree.
        """
        return web.json_response({"count": g_db.count_documents(request.query, user=ctx.get_username(request))})

    ctx.add_get("documents/count", count_documents)

    manual_deletion_locks = {}
    root_delete_message = "Cannot delete the filestore root or all its documents. Delete the filestore using its confirmation dialog instead."

    def assert_safe_document_delete(docs, user, selector=None):
        if selector is not None and "categoryUnder" in selector:
            path = str(selector.get("categoryUnder") or "").strip().strip("/\\")
            if not path or path in (".", ".."):
                raise ValueError(root_delete_message)
        selected_by_store = {}
        for row in docs:
            if row.get("filestoreId"):
                selected_by_store.setdefault(row["filestoreId"], set()).add(row["id"])
        for store_id, ids in selected_by_store.items():
            if len(ids) >= g_db.count_documents({"filestoreId": store_id}, user=user):
                raise ValueError(root_delete_message)
            visible_ids = {row["id"] for row in docs
                           if row.get("filestoreId") == store_id and not row.get("tombstonedAt")}
            if visible_ids and len(visible_ids) >= g_db.count_documents(
                    {"filestoreId": store_id, "null": "tombstonedAt"}, user=user):
                raise ValueError(root_delete_message)

    async def remove_document(row, user=None):
        """
        Delete one document from Gemini and locally.

        Shared by the single-row delete and the bulk one so a selection of 40 can't drift from
        what deleting them one at a time would have done.
        """
        if row.get("name"):
            try:
                await asyncio.to_thread(g_client.file_search_stores.documents.delete,
                                        name=row.get("name"), config={"force": True}, timeout=60)
            except GeminiApiError as e:
                if e.code == 404:
                    ctx.dbg(f"Document {row.get('name')} already deleted in Gemini")
                else:
                    raise Exception(
                        f"Could not delete document {row.get('name')}: {str(e) or e.status}"
                    ) from e
        # Only report completion after the local deletion has committed, so an immediate
        # refresh cannot bring back rows whose database writes were merely queued.
        await g_db.delete_document_async(row.get("id"), user=user)

    def refresh_filestore_stats(filestore_id, user=None):
        """Re-read the counts Gemini holds for a store, after something changed how many there are."""
        filestore = g_db.get_filestore(filestore_id, user=user)
        if not (filestore and filestore.get("name") and g_client):
            return
        try:
            res = g_client.file_search_stores.get(name=filestore.get("name"), timeout=30)
            if res:
                g_db.update_filestore(
                    filestore_id,
                    {
                        "activeDocumentsCount": res.active_documents_count,
                        "pendingDocumentsCount": res.pending_documents_count,
                        "failedDocumentsCount": res.failed_documents_count,
                        "sizeBytes": res.size_bytes,
                    },
                    user=user,
                )
        except Exception as e:
            ctx.err("Failed to update filestore stats after doc deletion", e)

    async def delete_document(request):
        denied = auth_error(request)
        if denied:
            return denied
        id = request.match_info["id"]
        user = ctx.get_username(request)
        async with manual_deletion_locks.setdefault(user, asyncio.Lock()):
            row = g_db.get_document(id, user=user)
            if not row:
                raise Exception("Document does not exist")
            assert_safe_document_delete([row], user)
            await remove_document(row, user=user)
            if row.get("filestoreId"):
                await asyncio.to_thread(refresh_filestore_stats, row.get("filestoreId"), user=user)
        return web.json_response({})

    ctx.add_delete("documents/{id}", delete_document)

    def doc_to_dto(doc):
        # Extract serializable dict from the document result
        return {
            "name": doc.name,
            "displayName": doc.display_name,
            "mimeType": doc.mime_type,
            "sizeBytes": doc.size_bytes,
            "createTime": doc.create_time.isoformat(),
            "updateTime": doc.update_time.isoformat(),
            "state": doc.state,
            "customMetadata": g_db.custom_metadata_dto(doc.custom_metadata),
        }

    async def filestore_documents(request):
        id = request.match_info["id"]
        user = ctx.get_username(request)
        filestore = g_db.get_filestore(int(id), user=user)

        if not filestore:
            raise Exception("Filestore does not exist")

        # Call Gemini API to list documents
        pager = g_client.file_search_stores.documents.list(parent=filestore.get("name"))
        documents = []
        for doc in pager:
            documents.append(doc_to_dto(doc))
        return web.json_response(documents)

    ctx.add_get("filestores/{id}/documents", filestore_documents)

    async def filestore_categories(request):
        id = request.match_info["id"]
        user = ctx.get_username(request)
        categories = g_db.document_categories(int(id), user=user)
        return web.json_response(categories)

    ctx.add_get("filestores/{id}/categories", filestore_categories)

    async def upload_document(request):
        denied = auth_error(request)
        if denied:
            return denied
        user = ctx.get_username(request)
        id = int(request.match_info["id"])
        doc = g_db.get_document(int(id), user=user)
        if not doc:
            raise Exception("Document does not exist")

        # The old copy used to be deleted here, before the upload. The worker now removes it
        # after the replacement is live, so a re-upload that fails no longer leaves the store
        # without the document at all.
        await g_db.update_document_async(id, {"error": None, "uploadedAt": None}, user=user)
        g_worker.start()
        while g_worker.running:
            await asyncio.sleep(2)
            doc = g_db.get_document(id, user=user)
            if doc.get("uploadedAt") or doc.get("error"):
                return web.json_response(document_dto(doc))

        return web.json_response(document_dto(doc))

    ctx.add_post("documents/{id}/upload", upload_document)

    async def sync_filestore_documents(request):
        denied = auth_error(request)
        if denied:
            return denied
        id = request.match_info["id"]
        user = ctx.get_username(request)
        filestore = g_db.get_filestore(int(id), user=user)
        if not filestore:
            raise Exception("Filestore does not exist")
        try:
            body = await request.json()
        except Exception:
            body = {}
        selected_adds = set(body.get("sourceDocuments") or [])
        add_all = bool(body.get("addAllSourceDocuments", not selected_adds))

        # Re-run recurring imports first, so Store Sync detects and queues changes to the
        # original files as well as reconciling the resulting local catalogue with Gemini.
        source_changes = []
        source_change_count = 0
        new_source_documents = []
        source_errors = []
        sources_synced = 0
        for source_row in g_db.query_sources({"filestoreId": int(id)}, user=user):
            if not is_saved_source(source_row) or not source_row.get("enabled", 1):
                continue
            try:
                result = await run_source_pipeline(source_dto(source_row), user, dry_run=False,
                                                   selected_adds=selected_adds, add_all=add_all,
                                                   request=request, refresh_crawl=True, start_uploads=False)
                sources_synced += 1
                if result.get("deleteRefused"):
                    source_errors.append(f"{source_row.get('name')}: {result['deleteRefused']}")
                if result.get("failed"):
                    source_errors.append(f"{source_row.get('name')}: {result['failed']} files could not be read")
                source_errors.extend(f"{source_row.get('name')}: {entry['displayName']}: {entry['error']}"
                                     for entry in result.get("deleteErrors") or [])
                if not (source_dto(source_row).get("config") or {}).get("manifestPath") and source_row.get("type") == "folder":
                    saved = source_dto(source_row)
                    candidate = os.path.join(saved["config"]["path"], crawl.MANIFEST)
                    if os.path.isfile(candidate):
                        saved = import_manifest.load(candidate, saved)
                    path = import_manifest.save(saved)
                    g_db.update_source(saved["id"], {"config": {**saved["config"], "manifestPath": path}}, user=user)
                source_change_count += sum(int(result.get(k) or 0) for k in ("added", "changed", "metadataOnly", "removed"))
                samples = result.get("samples") or {}
                for key in ("added", "changed", "removed"):
                    source_changes.extend(samples.get(key) or [])
                new_source_documents.extend(result.get("newSourceDocuments") or [])
            except Exception as e:
                ctx.err(f"Could not sync saved import {source_row.get('id')}", e)
                error = ctx.error_message(e)
                g_db.update_source(source_row.get("id"), {"error": error}, user=user)
                source_errors.append(f"{source_row.get('name')}: {error}")

        # Build hash lookup for all local documents
        local_doc_hashes = {}
        local_doc_names = {}
        local_doc_ids = {}
        local_docs = []
        for doc in g_db.query_documents_all({"filestoreId": int(id)}, user=user):
            if doc.get("tombstonedAt"):
                continue
            local_docs.append(doc)
            local_doc_hashes[doc.get("hash")] = doc
            local_doc_names[doc.get("name")] = doc
            local_doc_ids[doc["id"]] = doc

        ctx.log(f"Found {len(local_docs)} local documents in database")
        ctx.log(f"Local hashes available: {len(local_doc_hashes)}")

        local_missing = []
        remote_missing = []
        pending_uploads = []
        missing_metadata = []
        metadata_mismatch = []
        unmatched = []
        remote_copy_counts = {}

        def extract_custom_metadata(doc):
            remote_id = None
            remote_hash = None
            if doc.custom_metadata:
                for item in doc.custom_metadata:
                    if item.key == "id" and item.numeric_value:
                        remote_id = int(item.numeric_value)
                    elif item.key == "hash" and item.string_value:
                        remote_hash = item.string_value
            return remote_id, remote_hash

        pager = g_client.file_search_stores.documents.list(parent=filestore.get("name"))

        # Separate source documents may have identical content but different metadata.
        seen_local_ids = set()

        # Track stats for debugging
        matched_by_hash = 0
        remote_docs = 0

        # Extract documents from the result
        for doc in pager:
            remote_docs += 1
            remote_id, remote_hash = extract_custom_metadata(doc)

            # Match by hash or name
            identity_doc = local_doc_ids.get(remote_id)
            local_doc = (local_doc_names.get(doc.name)
                         or (identity_doc if identity_doc and identity_doc.get("hash") == remote_hash else None)
                         or local_doc_hashes.get(remote_hash))
            info = f"name={doc.name}, display={doc.display_name}, size={doc.size_bytes}, hash={remote_hash}"
            doc_context = {"doc": doc, "local": local_doc}

            if not local_doc:
                local_missing.append(doc)
                ctx.dbg(f"Remote doc not found locally: {info}")
                continue

            if not local_doc.get("uploadedAt") and not local_doc.get("error"):
                # This is the old remote copy of a queued replacement. Do not adopt it and
                # erase the queue state or the source's new title/metadata.
                continue

            seen_local_ids.add(local_doc["id"])
            if not remote_hash or not remote_id:
                missing_metadata.append(doc_context)
                ctx.dbg(f"Remote doc missing metadata: {info}")
                continue

            matched_by_hash += 1

            # Update local doc with remote name if missing
            new_dto = {
                "name": doc.name,
                "displayName": doc.display_name,
                "sizeBytes": doc.size_bytes,
                "mimeType": doc.mime_type,
                "createTime": doc.create_time.isoformat(" ") if doc.create_time else None,
                "updateTime": doc.update_time.isoformat(" ") if doc.update_time else None,
                "state": doc.state,
                "customMetadata": json.dumps(g_db.custom_metadata_dto(doc.custom_metadata)),
            }
            if doc.state == "STATE_ACTIVE" and not local_doc.get("uploadedAt"):
                new_dto["uploadedAt"] = datetime.now()
                new_dto["startedAt"] = None
            unmatched_fields = []
            for key, value in new_dto.items():
                local_value = local_doc.get(key)
                if local_value != value:
                    unmatched_fields.append(key)

            if len(unmatched_fields) > 0:
                ctx.dbg(
                    f"Updating local doc {local_doc.get('category')}/{local_doc.get('displayName')} unmatched fields: {unmatched_fields}"
                )
                unmatched.append(doc_context)
                await g_db.update_document_async(local_doc.get("id"), new_dto, user=user)

            # Verify that remote_id matches the local document id
            if (local_doc.get("id") != remote_id or local_doc.get("hash") != remote_hash
                    or g_db_module.metadata_differs({**local_doc, "customMetadata": new_dto["customMetadata"]})):
                # Metadata id doesn't match the document with this hash
                ctx.dbg(
                    f"Metadata mismatch: id={local_doc.get('id')}|{remote_id}, hash={local_doc.get('hash')}|{remote_hash}"
                )
                metadata_mismatch.append(doc_context)

            # Track hash occurrences to detect duplicates
            remote_copy_counts[local_doc["id"]] = remote_copy_counts.get(local_doc["id"], 0) + 1

        # Find local documents that don't exist in remote
        for local_doc in local_docs:
            local_hash = local_doc.get("hash")
            if local_hash and local_doc["id"] not in seen_local_ids:
                # A document which has never completed an upload is queued work, not remote
                # drift. Keep its queue state intact and make sure the worker is awake below.
                if (not local_doc.get("uploadedAt") and not local_doc.get("error")
                        and not local_doc.get("tombstonedAt")):
                    pending_uploads.append(local_doc)
                else:
                    remote_missing.append(local_doc)

        total_remote = matched_by_hash + len(local_missing)

        duplicate_docs = [local_doc_ids[id] for id, count in remote_copy_counts.items() if count > 1]

        for d in remote_missing:
            g_db.update_document(d.get("id"), {"state": "MISSING_FROM_REMOTE"}, user=user)
        for d in missing_metadata:
            local_doc = d.get("local")
            g_db.update_document(local_doc.get("id"), {"state": "MISSING_METADATA"}, user=user)
        for d in metadata_mismatch:
            local_doc = d.get("local")
            g_db.update_document(local_doc.get("id"), {"state": "METADATA_MISMATCH"}, user=user)
        for d in duplicate_docs:
            g_db.update_document(d.get("id"), {"state": "DUPLICATE_FILE"}, user=user)

        # Missing remote copies and broken identity metadata can be repaired using the cache.
        repairs = [*remote_missing, *[d["local"] for d in missing_metadata + metadata_mismatch]]
        for local_doc in repairs:
            url = local_doc.get("url") or ""
            if url.startswith("/~cache/") and os.path.isfile(ctx.get_cache_path(url[len("/~cache/"):])):
                patch = {"uploadedAt": None, "startedAt": None, "error": None}
                if local_doc in remote_missing:
                    patch["name"] = None
                await g_db.update_document_async(local_doc["id"], patch, user=user)
                if local_doc not in pending_uploads:
                    pending_uploads.append(local_doc)

        try:
            store_info = g_client.file_search_stores.get(name=filestore.get("name"))
            if store_info:
                g_db.update_filestore(
                    int(id),
                    {
                        "displayName": store_info.display_name,
                        "createTime": store_info.create_time,
                        "updateTime": store_info.update_time,
                        "activeDocumentsCount": store_info.active_documents_count,
                        "pendingDocumentsCount": store_info.pending_documents_count,
                        "failedDocumentsCount": store_info.failed_documents_count,
                        "sizeBytes": store_info.size_bytes,
                    },
                    user=user,
                )
        except Exception as e:
            ctx.err("Failed to update filestore stats during sync", e)

        ctx.log(
            f"Sync complete: total_remote={total_remote}, local_docs={len(local_docs)}, matched={matched_by_hash}, missing_metadata={len(missing_metadata)}, unmatched={len(local_missing)}"
        )
        # Store sync also repairs the independent local index. The worker derives signatures for
        # documents created before Search existed and resumes any interrupted section rebuilds.
        g_search_worker.start()
        if pending_uploads:
            ctx.log(f"Store sync found {len(pending_uploads)} queued Gemini uploads; "
                    "ensuring UploadWorker is running")
            g_worker.start()
        search_status = g_db.search_stats(int(id), user=user)

        def doc_filename(doc):
            if isinstance(doc, dict):
                return f"{doc.get('category')}/{doc.get('displayName')}"
            else:
                category = None
                for meta in doc.custom_metadata or []:
                    if meta.key == "category" and meta.string_value:
                        category = meta.string_value
                        return f"{category}/{doc.display_name}"
                return doc.display_name

        return web.json_response(
            {
                "Missing from Local": {
                    "count": len(local_missing),
                    "docs": [doc_filename(d) for d in local_missing[:5]],
                },
                "Missing from Gemini": {
                    "count": len(remote_missing),
                    "docs": [doc_filename(d) for d in remote_missing[:5]],
                },
                "Missing Metadata": {
                    "count": len(missing_metadata),
                    "docs": [doc_filename(d.get("doc")) for d in missing_metadata[:5]],
                },
                "Metadata Mismatch": {
                    "count": len(metadata_mismatch),
                    "docs": [doc_filename(d.get("doc")) for d in metadata_mismatch[:5]],
                },
                "Unmatched Fields": {
                    "count": len(unmatched),
                    "docs": [doc_filename(d.get("doc")) for d in unmatched[:5]],
                },
                "Duplicate Documents": {
                    "count": len(duplicate_docs),
                    "docs": [doc_filename(d) for d in duplicate_docs[:5]],
                },
                "Source Changes": {
                    "count": source_change_count,
                    "docs": source_changes[:5],
                },
                "Source Errors": {"count": len(source_errors), "docs": source_errors},
                "Saved Imports": {"count": sources_synced, "docs": []},
                "Pending Uploads": {
                    "count": len(pending_uploads),
                    "docs": [doc_filename(d) for d in pending_uploads[:5]],
                },
                "New Source Documents": {
                    "count": len(new_source_documents),
                    "docs": [d.get("sourceKey") for d in new_source_documents[:5]],
                    "items": new_source_documents,
                },
                "newSourceDocuments": new_source_documents,
                "Summary": {
                    "Local Documents": len(local_docs),
                    "Remote Documents": remote_docs,
                    "Matched Documents": matched_by_hash,
                },
                "Local Search": {
                    "Indexed Documents": int(search_status.get("indexed") or 0),
                    "Pending Documents": int(search_status.get("pending") or 0),
                    "Failed Documents": int(search_status.get("failed") or 0),
                    "Sections": int(search_status.get("sections") or 0),
                    "Provider": search_status.get("provider"),
                },
            }
        )

    ctx.add_post("filestores/{id}/sync", sync_filestore_documents)

    async def prune_filestore(request):
        """
        Remove the extra copies of a document a re-index left in Gemini.

        There is no API to replace a document, so an upload only ever adds one. Until the worker
        learned to delete the copy it supersedes, every push produced a second Gemini document
        carrying the same content hash - which is what sync reports as DUPLICATE_FILE. A local
        row can only point at one of them, so the rest are unreachable, still counted against the
        store, and still returned by search.

        Keeps the newest copy of each hash: that's the one the last upload produced, so it's the
        one whose metadata is current. The local row is pointed at it, because sync adopts the
        name of whichever copy it saw last and that may be one about to be deleted.

        Local rows marked MISSING_FROM_REMOTE are stale references to Gemini documents that no
        longer exist and are removed as well. `?dryRun=1` reports everything without changing it.
        """
        denied = auth_error(request)
        if denied:
            return denied
        user = ctx.get_username(request)
        id = int(request.match_info["id"])
        filestore = g_db.get_filestore(id, user=user)
        if not filestore or not filestore.get("name"):
            raise Exception("Filestore does not exist")
        dry_run = request.query.get("dryRun") not in (None, "", "0", "false")

        by_hash = {}
        for doc in g_client.file_search_stores.documents.list(parent=filestore.get("name")):
            doc_hash = next(
                (m.string_value for m in (doc.custom_metadata or []) if m.key == "hash" and m.string_value),
                None,
            )
            # No hash, no way to tell it apart from a legitimately distinct document. Sync reports
            # those separately as MISSING_METADATA.
            if doc_hash:
                by_hash.setdefault(doc_hash, []).append(doc)

        documents, removed, errors = 0, [], []
        for doc_hash, copies in by_hash.items():
            if len(copies) < 2:
                continue
            documents += 1
            copies.sort(key=lambda d: d.create_time.timestamp() if d.create_time else 0, reverse=True)
            keep, extra = copies[0], copies[1:]

            local = g_db.find_document({"filestoreId": id, "hash": doc_hash}, user=user)
            if local and local.get("name") != keep.name and not dry_run:
                g_db.update_document(local.get("id"), {"name": keep.name}, user=user)

            for doc in extra:
                if dry_run:
                    removed.append(doc.display_name)
                    continue
                try:
                    g_client.file_search_stores.documents.delete(name=doc.name, config={"force": True})
                    removed.append(doc.display_name)
                except Exception as e:
                    errors.append({"name": doc.name, "error": ctx.error_message(e)})

        stale = [
            doc for doc in g_db.query_documents_all({"filestoreId": id}, user=user)
            if doc.get("state") == "MISSING_FROM_REMOTE" and doc.get("uploadedAt")
        ]
        if not dry_run:
            for doc in stale:
                g_db.delete_document(doc.get("id"), user=user)

        if (removed or stale) and not dry_run:
            refresh_filestore_stats(id, user=user)
        ctx.log(f"Prune {'(dry run) ' if dry_run else ''}{filestore.get('displayName')}: "
                f"{len(removed)} extra copies across {documents} documents")
        return web.json_response({
            "dryRun": dry_run,
            "documents": documents,
            "removed": len(removed),
            "staleRemoved": len(stale),
            "samples": removed[:5],
            "errors": errors,
        })

    ctx.add_post("filestores/{id}/prune", prune_filestore)

    # --- facets -------------------------------------------------------------------------

    async def filestore_facets(request):
        """
        Distinct metadata values with counts, derived from the documents themselves.

        Supersedes /categories (kept as an alias): one endpoint feeds the facet rail, the
        autocomplete and the coverage strip, so they can't disagree about what values exist.
        """
        id = int(request.match_info["id"])
        user = ctx.get_username(request)
        fields = request.query.get("fields")
        fields = [f.strip() for f in fields.split(",") if f.strip()] if fields else None
        total = g_db.get_filestore_stats(id, user=user) or {}
        return web.json_response(
            {
                "total": total.get("count", 0),
                "facets": g_db.document_facets(id, fields, user=user),
            }
        )

    ctx.add_get("filestores/{id}/facets", filestore_facets)

    # --- bulk metadata ------------------------------------------------------------------

    def bulk_selector(body):
        """Either {ids:[...]} or {filter:{...}} — the two ways the UI describes a selection."""
        selector = dict(body.get("filter") or {})
        if body.get("ids"):
            selector["ids"] = body["ids"]
        if not selector:
            raise Exception("Either 'ids' or 'filter' is required")
        return selector

    def bulk_change_list(body):
        """
        Normalise to a list of {field, op, value}.

        The single-field form is still accepted so a caller editing one field doesn't have to
        wrap it, but everything downstream sees a list - which is what makes the preview count
        documents rather than field edits.
        """
        changes = body.get("changes")
        if changes is None:
            changes = [{"field": body.get("field"), "op": body.get("op", "fill"), "value": body.get("value")}]
        out = []
        for c in changes:
            field = c.get("field")
            op = c.get("op", "fill")
            if not field:
                raise Exception("field is required")
            if field not in g_db.BULK_COLUMNS:
                raise Exception(f"'{field}' is not a bulk-editable column")
            if op not in g_db.BULK_OPS:
                raise Exception(f"Unknown op '{op}'")
            out.append({"field": field, "op": op, "value": c.get("value")})
        if not out:
            raise Exception("'changes' is required")
        return out

    async def bulk_documents(request):
        """
        Apply metadata changes to many documents at once.

        Writes locally only: the documents become *pending* and Undo is free until a re-index,
        which is what makes an expensive operation safe to attempt. `dryRun` returns the same
        counts without writing, produced by the same code that does the work.
        """
        denied = auth_error(request)
        if denied:
            return denied
        user = ctx.get_username(request)
        body = await request.json()
        changes = bulk_change_list(body)

        docs = g_db.bulk_select(bulk_selector(body), user=user)
        preview = g_db.bulk_preview(docs, changes)
        if body.get("dryRun"):
            return web.json_response({**preview, "dryRun": True})

        changed = g_db.bulk_update(docs, changes, user=user)
        if changed:
            # bulk_update deliberately batches on the DB writer thread. Search signatures must be
            # computed after those metadata writes commit or a fast read can hash the old row.
            await asyncio.get_running_loop().run_in_executor(None, g_db.db.task_queue.join)
        for document_id in changed:
            document = g_db.get_document(document_id, user=user)
            if document:
                await g_db.update_document_async(
                    document_id, {"searchHash": search.desired_hash(document)}, user=user)
        if changed:
            g_search_worker.start()
        return web.json_response({**preview, "changed": len(changed), "ids": changed,
                                  "searchQueued": len(changed)})

    ctx.add_post("documents/bulk", bulk_documents)

    async def summarise_documents(request):
        """
        What a selection currently holds, per field — what the bulk editor shows before you type.

        Answered from the documents themselves rather than from the store-wide facets, which
        describe the store and so can't say what these 412 documents have in common.
        """
        denied = auth_error(request)
        if denied:
            return denied
        user = ctx.get_username(request)
        body = await request.json()
        fields = body.get("fields")
        selector = bulk_selector(body)
        docs = g_db.bulk_select(selector, user=user, include_tombstoned=True)
        summary = g_db.document_summary(docs, fields)
        try:
            assert_safe_document_delete(docs, user, selector)
            summary["deleteAllowed"] = True
        except ValueError as e:
            summary.update({"deleteAllowed": False, "deleteError": str(e)})
        return web.json_response(summary)

    ctx.add_post("documents/summary", summarise_documents)

    async def delete_documents(request):
        """
        Delete a selection. Unlike a metadata edit this is not undoable, so the client confirms
        against the count this same selector produces.

        One document failing to delete doesn't abandon the rest - the failures come back named,
        because the useful answer to "did that work" is "all but these two".
        """
        denied = auth_error(request)
        if denied:
            return denied
        user = ctx.get_username(request)
        body = await request.json()
        selector = bulk_selector(body)
        # Serialize manual deletions so two individually safe requests cannot jointly empty
        # the store. Check the current selection again inside the lock, before any remote call.
        async with manual_deletion_locks.setdefault(user, asyncio.Lock()):
            docs = g_db.bulk_select(selector, user=user, include_tombstoned=True)
            assert_safe_document_delete(docs, user, selector)
            return await delete_selected_documents(docs, user)

    async def delete_selected_documents(docs, user):

        # Gemini's client is synchronous. Keep network waits off the event loop and bound
        # concurrency so large folder deletions leave the app and progress reads responsive.
        limit = asyncio.Semaphore(4)

        async def delete_one(row):
            async with limit:
                try:
                    await remove_document(row, user=user)
                    return None
                except Exception as e:
                    return {"id": row.get("id"), "displayName": row.get("displayName"),
                            "error": ctx.error_message(e)}

        results = await asyncio.gather(*(delete_one(row) for row in docs))
        deleted = [row.get("id") for row, error in zip(docs, results) if error is None]
        errors = [error for error in results if error is not None]
        stores = {row.get("filestoreId") for row, error in zip(docs, results)
                  if error is None and row.get("filestoreId")}
        for filestore_id in stores:
            await asyncio.to_thread(refresh_filestore_stats, filestore_id, user=user)
        return web.json_response({"selected": len(docs), "deleted": len(deleted), "ids": deleted, "errors": errors})

    ctx.add_post("documents/delete", delete_documents)

    async def pending_documents(request):
        """Documents whose local metadata no longer matches the copy Gemini holds."""
        user = ctx.get_username(request)
        filestore_id = request.query.get("filestoreId")
        rows = g_db.pending_documents(filestore_id, user=user)
        uploading = g_db.count_documents(
            {"filestoreId": filestore_id, "null": "uploadedAt,error,tombstonedAt"}, user=user
        ) if filestore_id else 0
        # Break it down by what actually changed. A bare count gives no reason to spend a
        # re-index; "42 documents have a changed doc_type" is a reason.
        fields = {}
        never_pushed = 0
        for r in rows:
            if not r.get("customMetadata"):
                never_pushed += 1
            for key in g_db_module.metadata_diff_fields(r):
                fields[key] = fields.get(key, 0) + 1
        return web.json_response({
            "count": len(rows),
            "uploading": uploading,
            "sources": g_db.source_upload_counts(filestore_id, user=user) if filestore_id else {},
            "ids": [r.get("id") for r in rows],
            "fields": [{"field": k, "count": v} for k, v in sorted(fields.items(), key=lambda kv: -kv[1])],
            "neverPushed": never_pushed,
            "worker": g_worker.status(),
        })

    ctx.add_get("documents/pending", pending_documents)

    async def reindex_documents(request):
        """
        Push pending metadata to Gemini. The deliberate, costed step - before this, Undo is free.
        """
        denied = auth_error(request)
        if denied:
            return denied
        user = ctx.get_username(request)
        id = int(request.match_info["id"])
        body = await request.json() if request.can_read_body else {}
        ids = body.get("ids")

        rows = g_db.pending_documents(id, user=user)
        missing_uploads = [
            row for row in g_db.query_documents_all({"filestoreId": id}, user=user)
            if row.get("state") == "MISSING_FROM_REMOTE" and not row.get("tombstonedAt")
        ]
        if ids:
            wanted = {int(i) for i in ids}
            rows = [r for r in rows if r.get("id") in wanted]
            missing_uploads = [r for r in missing_uploads if r.get("id") in wanted]

        for row in rows:
            # Clearing uploadedAt is what puts it back in the worker's queue. The worker removes
            # the copy each upload supersedes - an upload on its own only ever adds.
            await g_db.update_document_async(row.get("id"), {"error": None, "uploadedAt": None}, user=user)
        for row in missing_uploads:
            await g_db.update_document_async(row.get("id"), {
                "error": None, "uploadedAt": None, "startedAt": None, "state": "STATE_PENDING",
            }, user=user)
        queued_ids = list(dict.fromkeys(
            [r.get("id") for r in rows] + [r.get("id") for r in missing_uploads]
        ))
        g_worker.start()
        ctx.log(f"Queued {len(queued_ids)} Gemini documents for upload "
                f"({len(missing_uploads)} recovered from MISSING_FROM_REMOTE)")
        return web.json_response({"queued": len(queued_ids), "recovered": len(missing_uploads),
                                  "ids": queued_ids})

    ctx.add_post("filestores/{id}/reindex", reindex_documents)

    async def resume_uploads(request):
        denied = auth_error(request)
        if denied:
            return denied
        user = ctx.get_username(request)
        store_id = int(request.match_info["id"])
        if not g_db.get_filestore(store_id, user=user):
            raise ValueError("Filestore does not exist")
        body = await request.json() if request.can_read_body else {}
        query = {"filestoreId": store_id, "null": "uploadedAt,error,tombstonedAt"}
        for field in ("categoryUnder", "category", "ids"):
            if field in body:
                query[field] = body[field]
        rows = g_db.bulk_select(query, user=user)
        # Starting the existing queue does not clear UploadedAt, change metadata, or create rows.
        # Healthy documents are never re-embedded by this action.
        if rows:
            g_worker.start()
        return web.json_response({"queued": len(rows), "ids": [row["id"] for row in rows],
                                  "worker": g_worker.status()})

    ctx.add_post("filestores/{id}/resume-uploads", resume_uploads)

    async def worker_status(request):
        return web.json_response(g_worker.status())

    ctx.add_get("worker", worker_status)

    async def cancel_worker(request):
        denied = auth_error(request)
        if denied:
            return denied
        g_worker.cancel()
        return web.json_response(g_worker.status())

    ctx.add_post("worker/cancel", cancel_worker)

    # --- trusted import folders ----------------------------------------------------------

    async def get_import_roots(request):
        """
        Which folders non-admins may import from, and whether the caller may change them.

        Readable by anyone who can reach the import UI: the roots are already disclosed by
        `source-types`, and a caller who cannot see them cannot tell a rejected path from a
        misconfigured one.
        """
        raw, configured = configured_import_roots()
        return web.json_response({
            "path": global_config_path(),
            "configured": configured,
            "isAdmin": ctx.is_admin(request),
            "roots": [describe_root(r) for r in raw],
            # What is in force right now, which is the server default until the file exists.
            "effective": trusted_import_roots(ctx.get_username(request)),
        })

    ctx.add_get("config/import-roots", get_import_roots)

    async def put_import_roots(request):
        denied = auth_error(request, "Admin")
        if denied:
            return denied
        body = await request.json() if request.can_read_body else {}
        roots = body.get("roots")
        if not isinstance(roots, list):
            raise Exception("'roots' must be a list of folder paths")
        cleaned = []
        for r in roots:
            r = normalize_root(r)
            if not r or r in cleaned:
                continue
            cleaned.append(r)
        save_import_roots(cleaned)
        return web.json_response({
            "path": global_config_path(),
            "configured": True,
            "isAdmin": True,
            "roots": [describe_root(r) for r in cleaned],
            "effective": trusted_import_roots(ctx.get_username(request)),
        })

    ctx.add_post("config/import-roots", put_import_roots)

    # --- sources ------------------------------------------------------------------------

    def saved_source_name_conflict(filestore_id, name, user, exclude_id=None):
        """Return a saved source with the same user-facing name, ignoring case and space."""
        wanted = str(name or "").strip().casefold()
        if not wanted:
            return None
        return next(
            (
                row
                for row in g_db.query_sources({"filestoreId": int(filestore_id)}, user=user)
                if is_saved_source(row)
                and row.get("id") != exclude_id
                and str(row.get("name") or "").strip().casefold() == wanted
            ),
            None,
        )

    async def get_source_types(request):
        """Reports availability so the UI can say 'needs git on PATH' rather than failing later."""
        types = ingest.source_types()
        # A path field the user can only satisfy by guessing is a broken field. Ship the roots
        # assert_source_allowed() will check against - split by where each one comes from, since
        # "an Admin put this in config" and "the server grants this" are different answers to
        # "why can I read here" - and say whether this caller is held to them at all.
        configured, _ = configured_import_roots()
        for t in types:
            if t.get("type") == "folder":
                user_imports = crawl.imports_root(ctx, ctx.get_username(request))
                t["roots"] = {
                    "trusted": resolve_roots(configured),
                    "allowed": allowed_directories(ctx.get_username(request)),
                    "imports": [user_imports],
                    "all": sorted(set(trusted_import_roots(ctx.get_username(request))) | {user_imports}),
                }
                t["unrestricted"] = ctx.is_admin(request)
        return web.json_response(types)

    ctx.add_get("source-types", get_source_types)

    # --- staged web imports -------------------------------------------------------------

    async def list_crawl_imports(request):
        return web.json_response(crawl.list_imports(ctx, ctx.get_username(request)))

    ctx.add_get("imports", list_crawl_imports)

    async def crawl_import_schema(request):
        return web.json_response({"rules": crawl.CRAWL_RULE_SCHEMA, "transforms": crawl.TRANSFORM_SCHEMA})

    # Register static paths before /imports/{name} so aiohttp cannot treat "schema" as a name.
    ctx.add_get("imports/schema", crawl_import_schema)

    async def browse_import_manifests(request):
        denied = auth_error(request)
        if denied:
            return denied
        default_path = crawl.imports_root(ctx, ctx.get_username(request))
        if not request.query.get("path"):
            os.makedirs(default_path, exist_ok=True)
        path = ingest.resolve_path(request.query.get("path") or default_path)
        assert_source_allowed({"config": {"path": path}}, request)
        entries = []
        for entry in sorted(os.scandir(path), key=lambda item: (item.is_dir(), item.name.lower())):
            if not entry.is_dir() and entry.name != crawl.MANIFEST:
                continue
            try:
                assert_source_allowed({"config": {"path": entry.path}}, request)
            except Exception:
                continue
            entries.append({"name": entry.name, "path": entry.path, "directory": entry.is_dir()})
        parent = os.path.dirname(path)
        try:
            assert_source_allowed({"config": {"path": parent}}, request)
        except Exception:
            parent = None
        return web.json_response({"path": path, "parent": parent if parent != path else None, "entries": entries})

    ctx.add_get("imports/browse", browse_import_manifests)

    async def load_import_manifest(request):
        denied = auth_error(request)
        if denied:
            return denied
        body = await request.json()
        path = body.get("path")
        if not path:
            raise ValueError("An import.json path is required")
        assert_source_allowed({"config": {"path": path}}, request)
        source = import_manifest.load(path)
        assert_source_allowed(source, request)
        cfg = crawl.read_json(ingest.resolve_path(path))
        source["importOptions"] = {key: cfg[key] for key in ("crawl", "transforms") if key in cfg}
        return web.json_response(source)

    ctx.add_post("imports/load", load_import_manifest)

    async def load_saved_import(request):
        denied = auth_error(request)
        if denied:
            return denied
        body = await request.json()
        if not body.get("path") or not body.get("filestoreId"):
            raise ValueError("An import.json path and filestoreId are required")
        source = await register_import_manifest(body["path"], body["filestoreId"], ctx.get_username(request), request)
        return web.json_response(source)

    ctx.add_post("sources/load", load_saved_import)

    async def get_crawl_import(request):
        name = request.match_info["name"]
        path = crawl.workspace_path(ctx, ctx.get_username(request), name)
        if not os.path.isdir(path):
            raise Exception("Import does not exist")
        cfg = crawl.read_json(os.path.join(path, crawl.MANIFEST))
        pages = len(crawl.list_crawled_pages(path))
        return web.json_response({"name": name, "path": path, "pages": pages, "config": cfg})

    ctx.add_get("imports/{name}", get_crawl_import)

    async def selected_crawl_source(request, source_id, filestore_id=None):
        row = g_db.get_source(int(source_id), user=ctx.get_username(request))
        if not row:
            raise ValueError("Source does not exist")
        source = editable_source(row, request)
        if filestore_id is not None and int(source["filestoreId"]) != int(filestore_id):
            raise ValueError("Saved import does not belong to this filestore")
        if source.get("type") != "folder" or not (source.get("importOptions") or {}).get("crawl", {}).get("url"):
            raise ValueError("Saved import is not a web crawl")
        source = await register_import_manifest(source["config"]["manifestPath"], source["filestoreId"], ctx.get_username(request), request)
        root = os.path.dirname(source["config"]["manifestPath"])
        assert_source_allowed({"config": {"path": root}}, request)
        return source, root

    async def crawl_request_path(request):
        if request.query.get("sourceId"):
            return (await selected_crawl_source(request, request.query["sourceId"]))[1]
        return crawl.workspace_path(ctx, ctx.get_username(request), request.match_info["name"])

    async def list_crawl_pages(request):
        denied = auth_error(request)
        if denied:
            return denied
        path = await crawl_request_path(request)
        return web.json_response({"pages": crawl.list_crawled_pages(path)})

    ctx.add_get("imports/{name}/pages", list_crawl_pages)

    async def get_crawl_page(request):
        denied = auth_error(request)
        if denied:
            return denied
        path = await crawl_request_path(request)
        try:
            content = crawl.read_crawled_page(path, request.query.get("path"))
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response({"path": request.query.get("path"), "content": content})

    ctx.add_get("imports/{name}/page", get_crawl_page)

    async def start_crawl(request):
        denied = auth_error(request)
        if denied:
            return denied
        body = await request.json()
        filestore_id = body.pop("filestoreId", None)
        source_id = body.pop("sourceId", None)
        settings = body.pop("sourceSettings", None)
        if settings is not None and not isinstance(settings, dict):
            raise ValueError("sourceSettings must be an object")
        user = ctx.get_username(request)
        if filestore_id and not g_db.get_filestore(int(filestore_id), user=user):
            raise ValueError("Filestore does not exist")
        source, root = await selected_crawl_source(request, source_id, filestore_id) if source_id else (None, None)
        if source:
            filestore_id = source["filestoreId"]
            body = {**source["importOptions"]["crawl"], **body}
            result = await crawl.crawl_site(ctx, user, body, target_path=root)
        else:
            result = await crawl.crawl_site(ctx, user, body)
        if settings is not None:
            updated_source = import_manifest.load(os.path.join(result["path"], crawl.MANIFEST))
            updated_source.update({key: value for key, value in settings.items() if key in (*import_manifest.SOURCE_FIELDS, "rules")})
            config = {key: value for key, value in (settings.get("config") or {}).items()
                      if key not in ("path", "manifestPath", "saved", "metadataSpecified")}
            updated_source["config"] = {**updated_source["config"], **config, "metadataSpecified": "rules" in settings}
            save_source_settings(updated_source, request)
        if filestore_id:
            result["source"] = (editable_source(g_db.get_source(source["id"], user=user), request) if source
                else await register_import_manifest(os.path.join(result["path"], crawl.MANIFEST), filestore_id, user, request))
        return web.json_response(result)

    ctx.add_post("imports/crawl", start_crawl)

    async def save_crawl_config(request):
        denied = auth_error(request)
        if denied:
            return denied
        name = request.match_info["name"]
        path = crawl.workspace_path(ctx, ctx.get_username(request), name)
        if not os.path.isdir(path):
            raise Exception("Import does not exist")
        cfg = await request.json()
        crawl.write_json(os.path.join(path, crawl.MANIFEST), cfg)
        return web.json_response({"name": name, "path": path, "config": cfg})

    ctx.add_put("imports/{name}", save_crawl_config)

    async def transform_crawl_import(request):
        denied = auth_error(request)
        if denied:
            return denied
        name = request.match_info["name"]
        path = await crawl_request_path(request)
        cfg_path = os.path.join(path, crawl.MANIFEST)
        cfg = crawl.read_json(cfg_path)
        body = await request.json()
        transforms = body.get("transforms", cfg.get("transforms") or [])
        changed = crawl.apply_transforms(path, transforms)
        cfg["transforms"] = transforms
        crawl.write_json(cfg_path, cfg)
        return web.json_response({"name": name, "path": path, "changed": changed, "config": cfg})

    ctx.add_post("imports/{name}/transform", transform_crawl_import)

    async def query_sources(request):
        user = ctx.get_username(request)
        rows = g_db.query_sources(request.query, user=user)
        # POST /sources creates a provisional row so the common pipeline has an identity during
        # preview. Only an explicit save or a completed import makes it a saved source.
        rows = [row for row in rows if is_saved_source(row)]
        sources = []
        for row in rows:
            try:
                sources.append(editable_source(row, request))
            except Exception as e:
                sources.append({**source_dto(row), "error": ctx.error_message(e)})
        return web.json_response(sources)

    ctx.add_get("sources", query_sources)

    async def get_source(request):
        denied = auth_error(request)
        if denied:
            return denied
        row = g_db.get_source(int(request.match_info["id"]), user=ctx.get_username(request))
        if not row:
            raise ValueError("Source does not exist")
        source = editable_source(row, request)
        if (source.get("importOptions") or {}).get("crawl", {}).get("url"):
            source = await register_import_manifest(source["config"]["manifestPath"], source["filestoreId"], ctx.get_username(request), request)
        return web.json_response(source)

    ctx.add_get("sources/{id}", get_source)

    async def create_source(request):
        denied = auth_error(request)
        if denied:
            return denied
        user = ctx.get_username(request)
        source = await request.json()
        save_config = source.pop("saveConfig", False)
        options = source.pop("importOptions", None)
        for key in ("id", "lastRunId", "lastRunAt", "user", "createdAt", "updatedAt"):
            source.pop(key, None)
        if not source.get("filestoreId"):
            raise Exception("filestoreId is required")
        if source.get("type") not in ingest.SOURCE_TYPES:
            raise Exception(f"Unknown source type '{source.get('type')}'")
        if not g_db.get_filestore(int(source["filestoreId"]), user=user):
            raise Exception("Filestore does not exist")
        source["name"] = str(source.get("name") or "").strip()
        if saved_source_name_conflict(source["filestoreId"], source["name"], user):
            raise Exception(f"A saved import named '{source['name']}' already exists")
        assert_source_allowed(source, request)
        source.setdefault("enabled", 1)
        source.setdefault("onDelete", "tombstone")
        source.setdefault("extractorVer", ingest.EXTRACTOR_VERSION)
        if save_config:
            source = save_source_settings(source, request, options)
        id = await g_db.create_source_async(source, user=user)
        if save_config:
            await g_db.attach_source_documents_async(id, source["filestoreId"], source["config"]["manifestPath"], user=user)
        return web.json_response(source_dto(g_db.get_source(id, user=user)))

    ctx.add_post("sources", create_source)

    async def update_source(request):
        denied = auth_error(request)
        if denied:
            return denied
        user = ctx.get_username(request)
        id = int(request.match_info["id"])
        if not g_db.get_source(id, user=user):
            raise Exception("Source does not exist")
        patch = await request.json()
        save_config = patch.pop("saveConfig", False)
        options = patch.pop("importOptions", None)
        patch = {key: value for key, value in patch.items() if key in (
            *import_manifest.SOURCE_FIELDS, "config", "rules", "enabled")}
        current = editable_source(g_db.get_source(id, user=user), request) if save_config else source_dto(g_db.get_source(id, user=user))
        if save_config and (current.get("importOptions") or {}).get("crawl", {}).get("url"):
            original_config = current["config"]
            current = await register_import_manifest(original_config["manifestPath"], current["filestoreId"], user, request)
            if original_config["manifestPath"] != current["config"]["manifestPath"] and "config" in patch:
                original_root = os.path.dirname(original_config["manifestPath"])
                input_path = ingest.resolve_path(patch["config"].get("path") or original_config["path"])
                if not ingest.within_roots(input_path, [original_root]):
                    raise ValueError("The crawl input folder must be inside its manifest workspace")
                patch["config"] = {**patch["config"], "manifestPath": current["config"]["manifestPath"],
                    "path": os.path.join(os.path.dirname(current["config"]["manifestPath"]), os.path.relpath(input_path, original_root))}
        new_name = str(patch.get("name", current.get("name") or "")).strip()
        if is_saved_source(current) and saved_source_name_conflict(
            current["filestoreId"], new_name, user, exclude_id=id
        ):
            raise Exception(f"A saved import named '{new_name}' already exists")
        if "name" in patch:
            patch["name"] = new_name
        assert_source_allowed(patch, request)
        if save_config:
            merged = {**current, **patch}
            if "rules" in patch:
                merged["config"] = {**merged["config"], "metadataSpecified": True}
            merged = save_source_settings(merged, request, options)
            patch = {key: merged[key] for key in (*import_manifest.SOURCE_FIELDS, "config", "rules", "enabled") if key in merged}
        await g_db.update_source_async(id, patch, user=user)
        return web.json_response(editable_source(g_db.get_source(id, user=user), request) if save_config else source_dto(g_db.get_source(id, user=user)))

    ctx.add_patch("sources/{id}", update_source)

    async def delete_source(request):
        denied = auth_error(request)
        if denied:
            return denied
        user = ctx.get_username(request)
        id = int(request.match_info["id"])
        source = g_db.get_source(id, user=user)
        if not source:
            raise Exception("Source does not exist")
        # Removing a saved import detaches its documents and leaves its manifest on disk.
        if request.query.get("documents", "keep") == "keep":
            config = source_dto(source).get("config") or {}
            manifest_path = config.get("manifestPath")
            if not manifest_path and source.get("type") == "folder" and config.get("path"):
                manifest_path = os.path.join(config["path"], crawl.MANIFEST)
            await g_db.detach_source_documents_async(id, user=user,
                manifest_path=ingest.resolve_path(manifest_path) if manifest_path else None)
        await g_db.delete_source_async(id, user=user)
        return web.json_response({})

    ctx.add_delete("sources/{id}", delete_source)

    async def source_runs(request):
        user = ctx.get_username(request)
        id = int(request.match_info["id"])
        return web.json_response(
            [g_db.to_dto(r, ["plan", "log"]) for r in g_db.query_runs(id, user=user)]
        )

    ctx.add_get("sources/{id}/runs", source_runs)

    async def run_source(request):
        """
        Run a source. `dryRun` (the default for a source that has never run) previews what would
        happen without writing or spending anything.
        """
        denied = auth_error(request)
        if denied:
            return denied
        user = ctx.get_username(request)
        id = int(request.match_info["id"])
        source_row = g_db.get_source(id, user=user)
        if not source_row:
            raise Exception("Source does not exist")
        source_row = g_db.to_dto(
            source_row, ["config", "category", "rules", "include", "exclude", "extract", "chunking", "volatile", "cursor"]
        )
        # Re-check on every run, not just at create: otherwise a source an Admin saved becomes a
        # standing way for anyone else to read that path.
        assert_source_allowed(source_row, request)

        body = await request.json() if request.can_read_body else {}
        dry_run = body.get("dryRun", not source_row.get("lastRunId"))
        if not dry_run and saved_source_name_conflict(
            source_row["filestoreId"], source_row.get("name"), user, exclude_id=id
        ):
            raise Exception(f"A saved import named '{source_row.get('name')}' already exists")
        result = await run_source_pipeline(source_row, user, dry_run=dry_run, override=body.get("set"),
                                           confirm_deletes=bool(body.get("confirmDeletes")), request=request)
        if not dry_run and body.get("saveConfig"):
            source_row = source_dto(g_db.get_source(id, user=user))
            current_source = source_row
            if source_row.get("lastRunId") and source_row["config"].get("manifestPath"):
                current_source = import_manifest.load(source_row["config"]["manifestPath"], source_row)
            path = import_manifest.save(current_source)
            config = {**source_row.get("config", {}), "manifestPath": path, "metadataSpecified": False}
            await g_db.update_source_async(id, {"config": config}, user=user)
        return web.json_response(result)

    ctx.add_post("sources/{id}/run", run_source)

    # --- Local Search + published Search widgets --------------------------------------

    search_limiter = assistants.MinuteLimiter()
    search_click_limiter = assistants.MinuteLimiter()
    search_page_view_limiter = assistants.MinuteLimiter()
    search_widget_path = os.path.join(os.path.dirname(__file__), "ui", "search-widget.js")
    marked_path = os.path.join(os.path.dirname(inspect.getfile(ctx.__class__)), "ui", "lib", "marked.min.mjs")

    def search_analytics_excluded(config, request, page_url=None):
        analytics = config["analytics"]
        user_agent = request.headers.get("User-Agent")
        return bool(
            (analytics["excludeBots"] and search.is_bot(user_agent))
            or search.is_denied_user_agent(user_agent, analytics["deniedUserAgents"])
            or search.is_denied_ip(request.remote, analytics["deniedIpRanges"])
            or search.is_excluded_path(page_url, analytics["excludedPaths"])
        )

    def search_widget_dto(row, request):
        if not row:
            return None
        dto = dict(row)
        dto["config"] = search.normalize_config(dto.get("config"))
        dto["published"] = bool(dto.get("publishedAt") and dto.get("enabled"))
        dto["requestIp"] = request.remote
        src = f"{request_base_url(request)}/ext/gemini/public/searches/widget.js?g={dto['publicId']}"
        dto["scriptUrl"] = src
        dto["embedCode"] = f'<script src="{src}" async></script>'
        return dto

    async def list_search_widgets(request):
        denied = signed_in_error(request)
        if denied:
            return denied
        user = ctx.get_username(request)
        filestore_id = int(request.match_info["id"])
        if not g_db.get_filestore(filestore_id, user=user):
            raise web.HTTPNotFound(text="File Store does not exist")
        rows = g_db.query_search_widgets(filestore_id, user=user, include_archived=True)
        return web.json_response([search_widget_dto(row, request) for row in rows])

    ctx.add_get("filestores/{id}/searches", list_search_widgets)

    async def create_search_widget(request):
        denied = auth_error(request)
        if denied:
            return denied
        user = ctx.get_username(request)
        filestore_id = int(request.match_info["id"])
        if not g_db.get_filestore(filestore_id, user=user):
            raise web.HTTPNotFound(text="File Store does not exist")
        body = await request.json()
        name = str(body.get("name") or "").strip()[:200]
        if not name:
            return web.json_response(ctx.create_error_response("Name is required", "ValidationError"), status=400)
        if g_db.search_widget_name_exists(filestore_id, name, user=user):
            return web.json_response(ctx.create_error_response(
                f"A Search widget named '{name}' already exists", "AlreadyExists"), status=409)
        try:
            config = search.validate_config(body.get("config"))
        except ValueError as error:
            return web.json_response(ctx.create_error_response(str(error), "ValidationError"), status=400)
        publish = bool(body.get("published"))
        widget_id = await g_db.create_search_widget_async({
            "filestoreId": filestore_id,
            "name": name,
            "publicId": search.new_public_id(),
            "enabled": 1,
            "publishedAt": datetime.now() if publish else None,
            "config": config,
        }, user=user)
        if publish:
            await g_db.update_filestore_async(filestore_id, {"visibility": "public"}, user=user)
        return web.json_response(search_widget_dto(g_db.get_search_widget(widget_id, user=user), request))

    ctx.add_post("filestores/{id}/searches", create_search_widget)

    async def get_search_widget(request):
        denied = signed_in_error(request)
        if denied:
            return denied
        row = g_db.get_search_widget(int(request.match_info["id"]), user=ctx.get_username(request))
        if not row:
            raise web.HTTPNotFound(text="Search widget does not exist")
        return web.json_response(search_widget_dto(row, request))

    ctx.add_get("searches/{id}", get_search_widget)

    async def search_diagnostics(request):
        denied = signed_in_error(request)
        if denied:
            return denied
        user = ctx.get_username(request)
        widget = g_db.get_search_widget(int(request.match_info["id"]), user=user)
        if not widget:
            raise web.HTTPNotFound(text="Search widget does not exist")
        store = g_db.get_filestore(widget["filestoreId"], user=widget.get("user"))
        stats = g_db.search_stats(widget["filestoreId"], user=widget.get("user"))
        config = search.normalize_config(widget.get("config"))
        published = bool(widget.get("enabled") and widget.get("publishedAt"))
        checks = [
            {"name":"Published", "status":"pass" if published else "fail", "message":"The public Search deployment is enabled." if published else "Publish this Search before embedding it."},
            {"name":"Public File Store", "status":"pass" if store and store.get("visibility") == "public" else "fail", "message":"Anonymous read access is enabled." if store and store.get("visibility") == "public" else "The File Store must be public."},
            {"name":"Search index", "status":"warn" if stats.get("failed") else "pass" if stats.get("indexed") else "fail", "message":f"{stats.get('indexed', 0)} indexed, {stats.get('pending', 0)} pending, {stats.get('failed', 0)} failed documents."},
            {"name":"Allowed origins", "status":"pass" if config["hosting"]["allowedOrigins"] else "warn", "message":f"Restricted to {len(config['hosting']['allowedOrigins'])} configured origin(s)." if config["hosting"]["allowedOrigins"] else "All origins are currently allowed."},
            {"name":"Widget endpoint", "status":"pass" if published else "fail", "message":f"{request_base_url(request)}/ext/gemini/public/searches/widget.js?g={widget['publicId']}"},
        ]
        return web.json_response({"ready": all(x["status"] != "fail" for x in checks), "checks": checks})

    ctx.add_get("searches/{id}/diagnostics", search_diagnostics)

    async def search_analytics(request):
        denied = signed_in_error(request)
        if denied:
            return denied
        widget = g_db.get_search_widget(
            int(request.match_info["id"]), user=ctx.get_username(request))
        if not widget:
            raise web.HTTPNotFound(text="Search widget does not exist")
        config = search.normalize_config(widget.get("config"))
        g_db.clear_search_analytics(
            int(request.match_info["id"]), user=ctx.get_username(request),
            before=datetime.now() - timedelta(days=config["analytics"]["retentionDays"]))
        result = g_db.search_analytics(
            int(request.match_info["id"]), user=ctx.get_username(request),
            group_take=request.query.get("groupTake", 50), recent_take=request.query.get("recentTake", 100),
        )
        if result is None:
            raise web.HTTPNotFound(text="Search widget does not exist")
        result["trafficEnabled"] = config["analytics"]["enabled"]
        result["requestIp"] = request.remote
        result["traffic"] = g_db.search_traffic_analytics(
            int(request.match_info["id"]), user=ctx.get_username(request),
            period=request.query.get("period", "30d"),
            recent_skip=request.query.get("visitorSkip", 0),
            recent_take=request.query.get("visitorTake", 10))
        return web.json_response(result)

    ctx.add_get("searches/{id}/analytics", search_analytics)

    async def clear_search_analytics(request):
        denied = auth_error(request)
        if denied:
            return denied
        result = g_db.clear_search_analytics(
            int(request.match_info["id"]), user=ctx.get_username(request))
        if result is None:
            raise web.HTTPNotFound(text="Search widget does not exist")
        return web.json_response(result)

    ctx.add_post("searches/{id}/analytics/clear", clear_search_analytics)

    async def update_search_widget(request):
        denied = auth_error(request)
        if denied:
            return denied
        user = ctx.get_username(request)
        widget_id = int(request.match_info["id"])
        current = g_db.get_search_widget(widget_id, user=user)
        if not current:
            raise web.HTTPNotFound(text="Search widget does not exist")
        if current.get("enabled") == 0:
            return web.json_response(ctx.create_error_response(
                "Restore this Search widget before editing or publishing it", "SearchArchived"), status=409)
        body = await request.json()
        name = str(body.get("name", current.get("name")) or "").strip()[:200]
        if not name:
            return web.json_response(ctx.create_error_response("Name is required", "ValidationError"), status=400)
        if g_db.search_widget_name_exists(current["filestoreId"], name, user=user, exclude_id=widget_id):
            return web.json_response(ctx.create_error_response(
                f"A Search widget named '{name}' already exists", "AlreadyExists"), status=409)
        try:
            config = search.validate_config(body.get("config", current.get("config")))
        except ValueError as error:
            return web.json_response(ctx.create_error_response(str(error), "ValidationError"), status=400)
        published = bool(body.get("published", current.get("publishedAt") is not None))
        public_id = search.new_public_id() if body.get("regeneratePublicId") else current["publicId"]
        await g_db.update_search_widget_async(widget_id, {
            "name": name,
            "publicId": public_id,
            "enabled": current.get("enabled", 1),
            "publishedAt": current.get("publishedAt") or datetime.now() if published else None,
            "config": config,
        }, user=user)
        if published:
            await g_db.update_filestore_async(current["filestoreId"], {"visibility": "public"}, user=user)
        return web.json_response(search_widget_dto(g_db.get_search_widget(widget_id, user=user), request))

    ctx.add_put("searches/{id}", update_search_widget)

    async def archive_search_widget(request):
        denied = auth_error(request)
        if denied:
            return denied
        found = await g_db.archive_search_widget_async(
            int(request.match_info["id"]), user=ctx.get_username(request))
        if not found:
            raise web.HTTPNotFound(text="Search widget does not exist")
        return web.json_response({"archived": True})

    ctx.add_delete("searches/{id}", archive_search_widget)

    async def restore_search_widget(request):
        denied = auth_error(request)
        if denied:
            return denied
        try:
            restored = await g_db.restore_search_widget_async(
                int(request.match_info["id"]), user=ctx.get_username(request))
        except ValueError as error:
            return web.json_response(ctx.create_error_response(str(error), "AlreadyExists"), status=409)
        if not restored:
            raise web.HTTPNotFound(text="Search widget does not exist")
        return web.json_response(search_widget_dto(restored, request))

    ctx.add_post("searches/{id}/restore", restore_search_widget)

    async def delete_search_widget(request):
        denied = auth_error(request)
        if denied:
            return denied
        user = ctx.get_username(request)
        widget = g_db.get_search_widget(int(request.match_info["id"]), user=user)
        if not widget:
            raise web.HTTPNotFound(text="Search widget does not exist")
        try:
            body = await request.json()
        except Exception:
            body = {}
        if body.get("confirm") != widget["name"]:
            return web.json_response(ctx.create_error_response(
                f'Type "{widget["name"]}" to confirm permanent deletion',
                "ConfirmationRequired"), status=400)
        g_db.delete_search_widget(widget["id"], user=user, confirmation=body.get("confirm"))
        return web.json_response({"deleted": True})

    ctx.add_delete("searches/{id}/permanent", delete_search_widget)

    def search_result_parts(value, query, marked=False):
        value = str(value or "")
        if marked and "\x01" in value:
            parts, matched = [], False
            for piece in re.split(r"([\x01\x02])", value):
                if piece == "\x01":
                    matched = True
                elif piece == "\x02":
                    matched = False
                elif piece:
                    parts.append({"text": piece, "match": matched})
            return parts
        tokens = [re.escape(x) for x in re.findall(r"[\w]+", str(query or ""), re.UNICODE)[:10]]
        if not tokens:
            return [{"text": value, "match": False}]
        split = re.split(f"({'|'.join(tokens)})", value, flags=re.I)
        wanted = re.compile(f"^(?:{'|'.join(tokens)})$", re.I)
        return [{"text": piece, "match": bool(wanted.match(piece))} for piece in split if piece]

    def grouped_search_results(rows, query, group_limit=8, preview_url=None):
        groups, ordered = {}, []
        for row in rows:
            raw_url = str(row.get("url") or "")
            cached = raw_url.startswith("/~cache/")
            url = None if cached else raw_url or None
            can_preview = cached and re.search(r"\.md(?:#|$)", raw_url, re.I) is not None
            document_preview_url = preview_url(row) if can_preview and preview_url else None
            key = row.get("documentId")
            group = groups.get(key)
            if group is None:
                group = groups[key] = {
                    "documentId": key,
                    "title": row.get("documentTitle") or "Document",
                    "url": url,
                    "previewUrl": document_preview_url,
                    "items": [],
                }
                ordered.append(group)
            if len(group["items"]) >= group_limit:
                continue
            snippet = row.get("snippet") or row.get("content") or ""
            group["items"].append({
                "id": row.get("id"),
                "type": row.get("kind") or "content",
                "title": row.get("heading") or row.get("documentTitle"),
                "titleParts": search_result_parts(row.get("heading") or row.get("documentTitle"), query),
                "snippet": snippet.replace("\x01", "").replace("\x02", ""),
                "snippetParts": search_result_parts(snippet, query, marked=True),
                "url": url,
                "previewUrl": document_preview_url,
                "anchor": row.get("anchor"),
                "score": row.get("score"),
            })
        return ordered

    async def search_filestore(request):
        user = ctx.get_username(request)
        filestore_id = int(request.match_info["id"])
        if not g_db.get_filestore(filestore_id, user=user):
            raise web.HTTPNotFound(text="File Store does not exist")
        query = request.query.get("q", "")
        take = min(max(int(request.query.get("take", 30)), 1), 100)
        skip = min(max(int(request.query.get("skip", 0)), 0), 1000)
        try:
            ranking = json.loads(request.query.get("ranking", "{}"))
        except (TypeError, ValueError):
            ranking = None
        rows = g_db.search_sections(
            filestore_id, query, user=user, take=take + 1, ranking=ranking, skip=skip)
        has_more = len(rows) > take
        rows = rows[:take]
        return web.json_response({
            "query": query,
            "hasMore": has_more,
            "nextSkip": skip + len(rows),
            "groups": grouped_search_results(
                rows, query, preview_url=lambda row:
                    f"{request_base_url(request)}/ext/gemini/filestores/{filestore_id}"
                    f"/search-documents/{row.get('documentId')}",
            ),
        })

    ctx.add_get("filestores/{id}/search", search_filestore)

    def markdown_document_payload(document):
        url = str(document.get("url") or "")
        filename = (document.get("filename") or document.get("sourceKey")
                    or document.get("displayName") or "document.md")
        if (not url.startswith("/~cache/")
                or not (re.search(r"\.md$", filename, re.I) or re.search(r"\.md(?:#|$)", url, re.I))):
            raise web.HTTPNotFound(text="Markdown preview is unavailable")
        path = ctx.get_cache_path(url[len("/~cache/"):].split("#", 1)[0])
        if not os.path.exists(path):
            raise web.HTTPNotFound(text="Cached document content is missing")
        with open(path, "rb") as handle:
            text, frontmatter, skip = ingest.extract(handle.read(), filename, {"minWords": 0})
        if skip:
            raise web.HTTPNotFound(text="Markdown preview is unavailable")
        title = ((frontmatter or {}).get("title") or document.get("displayName")
                 or document.get("sourceKey") or "Document")
        title = re.sub(r"\.(?:md|mdx|markdown)$", "", str(title), flags=re.I)
        return {"title": title, "markdown": text or ""}

    async def search_document(request):
        denied = signed_in_error(request)
        if denied:
            return denied
        user = ctx.get_username(request)
        filestore_id = int(request.match_info["id"])
        document = g_db.get_document(int(request.match_info["documentId"]), user=user)
        if not document or int(document.get("filestoreId") or 0) != filestore_id:
            raise web.HTTPNotFound(text="Document does not exist")
        return web.json_response(markdown_document_payload(document))

    ctx.add_get("filestores/{id}/search-documents/{documentId}", search_document)

    async def search_index_status(request):
        user = ctx.get_username(request)
        filestore_id = int(request.match_info["id"])
        if not g_db.get_filestore(filestore_id, user=user):
            raise web.HTTPNotFound(text="File Store does not exist")
        return web.json_response({**g_db.search_stats(filestore_id, user=user),
                                  "worker": g_search_worker.status()})

    ctx.add_get("filestores/{id}/search-index", search_index_status)

    async def rebuild_search_index(request):
        denied = auth_error(request)
        if denied:
            return denied
        user = ctx.get_username(request)
        filestore_id = int(request.match_info["id"])
        documents = list(g_db.query_documents_all({"filestoreId": filestore_id}, user=user))
        for document in documents:
            await g_db.update_document_async(document["id"], {
                "searchHash": search.desired_hash(document),
                "searchIndexedHash": None,
                "searchError": None,
                "searchRetries": None,
            }, user=user)
        g_search_worker.start()
        return web.json_response({"queued": len(documents), "worker": g_search_worker.status()})

    ctx.add_post("filestores/{id}/search-index/rebuild", rebuild_search_index)

    def public_search_or_error(request):
        public_id = request.match_info.get("publicId") or request.query.get("g") or ""
        widget = g_db.get_public_search_widget(public_id)
        store = g_db.get_filestore(widget["filestoreId"], user=widget.get("user")) if widget else None
        if not widget or not store or store.get("visibility") != "public":
            raise web.HTTPNotFound(text="Search is unavailable")
        return widget, store

    async def public_search_script(request):
        headers = {"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"}
        try:
            widget, _ = public_search_or_error(request)
            with open(search_widget_path, encoding="utf-8") as handle:
                source = handle.read()
            try:
                markdown = bundled_markdown_source()
            except Exception as error:
                ctx.err("Failed embedding the bundled Marked renderer for Search", error)
                markdown = 'console.warn("Gemini Search Markdown renderer is unavailable; using plain text.");return null;'
            config = search.public_config(widget, request_base_url(request))
            script = (f"(()=>{{const CONFIG={json.dumps(config, separators=(',', ':'))};"
                      f"const SCRIPT=document.currentScript;const MARKDOWN=(()=>{{\n{markdown}\n}})();"
                      f"const mount=()=>{{\n{source}\n}};"
                      "if(document.readyState!=='loading')mount();"
                      "else addEventListener('DOMContentLoaded',mount,{once:true});})();")
        except web.HTTPNotFound:
            script = 'console.error("Gemini Search widget failed to load: Search is unavailable (404)");'
        except Exception as error:
            ctx.err("Failed generating Gemini Search widget script", error)
            script = 'console.error("Gemini Search widget failed to load. Check the server logs for details.");'
        return web.Response(text=script, content_type="application/javascript", headers=headers)

    ctx.add_get("public/searches/widget.js", public_search_script)

    async def public_search_results(request):
        widget, store = public_search_or_error(request)
        config = search.normalize_config(widget.get("config"))
        origin = request.headers.get("Origin")
        allowed = assistants.origin_allowed(origin, config["hosting"]["allowedOrigins"])
        headers = {"Vary": "Origin"}
        if allowed:
            headers["Access-Control-Allow-Origin"] = origin or "*"
        if not allowed:
            return web.json_response(ctx.create_error_response(
                "This website is not allowed to use this Search widget", "OriginNotAllowed"),
                status=403, headers=headers)
        if not search_limiter.allow(
            (widget["id"], request.remote or "unknown"), config["hosting"]["requestsPerMinute"]
        ):
            return web.json_response(ctx.create_error_response(
                "Too many searches. Please wait a moment and try again.", "RateLimited"),
                status=429, headers={**headers, "Retry-After": "60"})
        query = str(request.query.get("q") or "").strip()[:200]
        skip = min(max(int(request.query.get("skip", 0)), 0), 1000)
        if len(query) < config["behavior"]["minChars"]:
            return web.json_response({
                "query": query, "groups": [], "hasMore": False, "nextSkip": 0,
            }, headers=headers)
        started = time.perf_counter()
        take = config["behavior"]["maxResults"]
        rows = g_db.search_sections(
            store["id"], query, user=widget.get("user"), scope=config["scope"],
            take=take + 1, ranking=config["ranking"], skip=skip,
        )
        has_more = len(rows) > take
        rows = rows[:take]
        groups = grouped_search_results(
            rows, query, config["behavior"]["groupLimit"],
            preview_url=lambda row:
                f"{request_base_url(request)}/ext/gemini/public/searches/{widget['publicId']}"
                f"/documents/{row.get('documentId')}",
        )
        search_event_id = None
        if not skip and not search_analytics_excluded(
                config, request, request.headers.get("Referer")):
            try:
                g_db.clear_search_analytics(
                    widget["id"], user=widget.get("user"), before=datetime.now() - timedelta(
                        days=config["analytics"]["retentionDays"]))
                search_event_id = await g_db.record_search_query_async(
                    widget["id"], query, origin=origin, page_url=request.headers.get("Referer"),
                    user_agent=request.headers.get("User-Agent"), result_count=len(rows),
                    document_count=len(groups), duration_ms=round((time.perf_counter() - started) * 1000),
                )
            except Exception as error:
                ctx.err("Failed recording Search analytics", error)
        return web.json_response({
            "query": query,
            "groups": groups,
            "hasMore": has_more,
            "nextSkip": skip + len(rows),
            "searchEventId": search_event_id,
        }, headers=headers)

    ctx.add_get("public/searches/{publicId}/results", public_search_results)

    async def public_search_click(request):
        widget, store = public_search_or_error(request)
        config = search.normalize_config(widget.get("config"))
        origin = request.headers.get("Origin")
        allowed = assistants.origin_allowed(origin, config["hosting"]["allowedOrigins"])
        headers = {"Vary": "Origin"}
        if allowed:
            headers["Access-Control-Allow-Origin"] = origin or "*"
        if not allowed:
            return web.json_response(ctx.create_error_response(
                "This website is not allowed to use this Search widget", "OriginNotAllowed"),
                status=403, headers=headers)
        if search_analytics_excluded(config, request, request.headers.get("Referer")):
            return web.Response(status=204, headers=headers)
        click_limit = max(int(config["hosting"]["requestsPerMinute"]) * 4, 120)
        if not search_click_limiter.allow((widget["id"], request.remote or "unknown"), click_limit):
            return web.json_response(ctx.create_error_response(
                "Too many Search interactions. Please wait a moment and try again.", "RateLimited"),
                status=429, headers={**headers, "Retry-After": "60"})
        try:
            body = json.loads(await request.text())
            search_event_id = int(body.get("searchEventId") or 0)
            document_id = int(body.get("documentId") or 0)
            section_id = int(body.get("sectionId") or 0) or None
            position = int(body.get("position") or 0)
        except (TypeError, ValueError, json.JSONDecodeError):
            return web.json_response(ctx.create_error_response("Invalid click event", "ValidationError"),
                                     status=400, headers=headers)
        if search_event_id <= 0 or document_id <= 0 or position <= 0:
            return web.json_response(ctx.create_error_response("Invalid click event", "ValidationError"),
                                     status=400, headers=headers)
        try:
            await g_db.record_search_click_async(
                widget["id"], store["id"], search_event_id, document_id,
                section_id=section_id, position=position,
                document_title=str(body.get("documentTitle") or "")[:500],
                source_url=str(body.get("sourceUrl") or "")[:2000],
                result_type=str(body.get("resultType") or "")[:50],
                user=widget.get("user"),
            )
        except Exception as error:
            ctx.err("Failed recording Search result click", error)
        return web.Response(status=204, headers=headers)

    ctx.add_post("public/searches/{publicId}/clicks", public_search_click)

    async def public_search_page_view(request):
        widget, _ = public_search_or_error(request)
        config = search.normalize_config(widget.get("config"))
        origin = request.headers.get("Origin")
        allowed = assistants.origin_allowed(origin, config["hosting"]["allowedOrigins"])
        headers = {"Vary": "Origin"}
        if allowed:
            headers["Access-Control-Allow-Origin"] = origin or "*"
        if not allowed:
            return web.json_response(ctx.create_error_response(
                "This website is not allowed to use this Search widget", "OriginNotAllowed"),
                status=403, headers=headers)
        if not config["analytics"]["enabled"]:
            return web.Response(status=204, headers=headers)
        if (config["analytics"]["respectDoNotTrack"] and request.headers.get("DNT") == "1"):
            return web.Response(status=204, headers=headers)
        if search_analytics_excluded(config, request):
            return web.Response(status=204, headers=headers)
        page_view_limit = max(int(config["hosting"]["requestsPerMinute"]) * 10, 600)
        if not search_page_view_limiter.allow(
            (widget["id"], request.remote or "unknown"), page_view_limit
        ):
            return web.json_response(ctx.create_error_response(
                "Too many Analytics events. Please wait a moment and try again.", "RateLimited"),
                status=429, headers={**headers, "Retry-After": "60"})
        try:
            body = json.loads(await request.text())
            if not isinstance(body, dict):
                raise ValueError("Page view must be an object")
        except (TypeError, ValueError, json.JSONDecodeError):
            return web.json_response(ctx.create_error_response("Invalid page view", "ValidationError"),
                                     status=400, headers=headers)
        page_url = str(body.get("pageUrl") or request.headers.get("Referer") or "").strip()[:2000]
        try:
            parsed = urlsplit(page_url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ValueError("Invalid page URL")
            page_path = str(body.get("pagePath") or parsed.path or "/")[:2000]
        except ValueError:
            return web.json_response(ctx.create_error_response("Invalid page URL", "ValidationError"),
                                     status=400, headers=headers)
        if search.is_excluded_path(parsed.path, config["analytics"]["excludedPaths"]):
            return web.Response(status=204, headers=headers)
        try:
            g_db.clear_search_analytics(
                widget["id"], user=widget.get("user"), before=datetime.now() - timedelta(
                    days=config["analytics"]["retentionDays"]))
            await g_db.record_search_page_view_async(widget["id"], {
                **body,
                "origin": origin,
                "pageUrl": page_url,
                "pagePath": page_path,
                # The POST Referer is the page currently hosting the widget, not the
                # previous page that led the visitor there. Only document.referrer
                # from the collector represents referral traffic.
                "referrer": str(body.get("referrer") or "")[:2000],
                "userAgent": request.headers.get("User-Agent"),
            })
        except Exception as error:
            ctx.err("Failed recording Search page view", error)
        return web.Response(status=204, headers=headers)

    ctx.add_post("public/searches/{publicId}/pageviews", public_search_page_view)

    async def public_search_document(request):
        widget, store = public_search_or_error(request)
        config = search.normalize_config(widget.get("config"))
        origin = request.headers.get("Origin")
        allowed = assistants.origin_allowed(origin, config["hosting"]["allowedOrigins"])
        headers = {"Vary": "Origin"}
        if allowed:
            headers["Access-Control-Allow-Origin"] = origin or "*"
        if not allowed:
            return web.json_response(ctx.create_error_response(
                "This website is not allowed to use this Search widget", "OriginNotAllowed"),
                status=403, headers=headers)
        document = g_db.get_document(int(request.match_info["documentId"]), user=widget.get("user"))
        if not document or int(document.get("filestoreId") or 0) != int(store["id"]):
            raise web.HTTPNotFound(text="Document does not exist")
        return web.json_response(markdown_document_payload(document), headers=headers)

    ctx.add_get("public/searches/{publicId}/documents/{documentId}", public_search_document)

    # --- Published assistants ----------------------------------------------------------

    assistant_limiter = assistants.MinuteLimiter()
    widget_path = os.path.join(os.path.dirname(__file__), "ui", "assistant-widget.js")
    def bundled_markdown_source():
        with open(marked_path, encoding="utf-8") as f:
            source = f.read()
        source = re.sub(r"(?m)^//# sourceMappingURL=.*$", "", source)
        export = re.search(r"\bexport\{([^{}]*)\};", source)
        if not export:
            raise ValueError("Bundled Marked module has no export declaration")
        binding = re.search(r"(?:^|,)\s*([A-Za-z_$][\w$]*)\s+as\s+marked(?=\s*(?:,|$))", export.group(1))
        if not binding:
            raise ValueError("Bundled Marked module does not export marked")
        return source[:export.start()] + f"return {binding.group(1)};" + source[export.end():]

    def request_base_url(request):
        return f"{request.scheme}://{request.host}"

    def assistant_dto(row, request):
        if not row:
            return None
        dto = dict(row)
        dto["config"] = assistants.normalize_config(dto.get("config"))
        dto["published"] = bool(dto.get("publishedAt") and dto.get("enabled"))
        src = f"{request_base_url(request)}/ext/gemini/public/assistants/widget.js?g={dto['publicId']}"
        dto["scriptUrl"] = src
        dto["embedCode"] = f'<script src="{src}" async></script>'
        return dto

    async def list_assistants(request):
        denied = signed_in_error(request)
        if denied:
            return denied
        user = ctx.get_username(request)
        filestore_id = int(request.match_info["id"])
        if not g_db.get_filestore(filestore_id, user=user):
            raise web.HTTPNotFound(text="File Store does not exist")
        return web.json_response([assistant_dto(x, request) for x in g_db.query_assistants(
            filestore_id, user=user, include_archived=True)])

    ctx.add_get("filestores/{id}/assistants", list_assistants)

    async def create_assistant(request):
        denied = auth_error(request)
        if denied:
            return denied
        user = ctx.get_username(request)
        filestore_id = int(request.match_info["id"])
        if not g_db.get_filestore(filestore_id, user=user):
            raise web.HTTPNotFound(text="File Store does not exist")
        body = await request.json()
        name = str(body.get("name") or "").strip()[:200]
        if not name:
            return web.json_response(ctx.create_error_response("Name is required", "ValidationError"), status=400)
        if g_db.assistant_name_exists(filestore_id, name, user=user):
            return web.json_response(ctx.create_error_response(
                f"An Assistant named '{name}' already exists", "AlreadyExists"), status=409)
        try:
            config = assistants.validate_config(body.get("config"))
        except ValueError as e:
            return web.json_response(ctx.create_error_response(str(e), "ValidationError"), status=400)
        publish = bool(body.get("published"))
        assistant_id = await g_db.create_assistant_async({
            "filestoreId": filestore_id,
            "name": name,
            "publicId": assistants.new_public_id(),
            "enabled": 1,
            "publishedAt": datetime.now() if publish else None,
            "config": config,
        }, user=user)
        if publish:
            await g_db.update_filestore_async(filestore_id, {"visibility": "public"}, user=user)
        return web.json_response(assistant_dto(g_db.get_assistant(assistant_id, user=user), request))

    ctx.add_post("filestores/{id}/assistants", create_assistant)

    async def get_assistant(request):
        denied = signed_in_error(request)
        if denied:
            return denied
        row = g_db.get_assistant(int(request.match_info["id"]), user=ctx.get_username(request))
        if not row:
            raise web.HTTPNotFound(text="Assistant does not exist")
        return web.json_response(assistant_dto(row, request))

    ctx.add_get("assistants/{id}", get_assistant)

    async def assistant_diagnostics(request):
        denied = signed_in_error(request)
        if denied:
            return denied
        user = ctx.get_username(request)
        row = g_db.get_assistant(int(request.match_info["id"]), user=user)
        if not row:
            raise web.HTTPNotFound(text="Assistant does not exist")
        store = g_db.get_filestore(row["filestoreId"], user=row.get("user"))
        documents = list(g_db.query_documents_all({"filestoreId":row["filestoreId"]}, user=row.get("user")))
        config = assistants.normalize_config(row.get("config"))
        published = bool(row.get("enabled") and row.get("publishedAt"))
        active = sum(1 for x in documents if x.get("state") == "STATE_ACTIVE")
        failed = sum(1 for x in documents if x.get("state") == "STATE_FAILED" or x.get("error"))
        origins = config["hosting"]["allowedOrigins"]
        checks = [
            {"name":"Published", "status":"pass" if published else "fail", "message":"The public Assistant deployment is enabled." if published else "Publish this Assistant before embedding it."},
            {"name":"Public File Store", "status":"pass" if store and store.get("visibility") == "public" else "fail", "message":"Anonymous retrieval is enabled." if store and store.get("visibility") == "public" else "The File Store must be public."},
            {"name":"Gemini knowledge", "status":"warn" if active and failed else "pass" if active else "fail", "message":f"{active} active and {failed} failed documents."},
            {"name":"Model", "status":"pass", "message":assistants.resolve_model(config, os.getenv("GEMINI_ASSISTANT_MODEL", "gemini-flash-latest"))},
            {"name":"Allowed origins", "status":"pass" if origins else "warn", "message":f"Restricted to {len(origins)} configured origin(s)." if origins else "All origins are currently allowed."},
            {"name":"Widget endpoint", "status":"pass" if published else "fail", "message":f"{request_base_url(request)}/ext/gemini/public/assistants/widget.js?g={row['publicId']}"},
        ]
        return web.json_response({"ready": all(x["status"] != "fail" for x in checks), "checks": checks})

    ctx.add_get("assistants/{id}/diagnostics", assistant_diagnostics)

    async def update_assistant(request):
        denied = auth_error(request)
        if denied:
            return denied
        user = ctx.get_username(request)
        assistant_id = int(request.match_info["id"])
        current = g_db.get_assistant(assistant_id, user=user)
        if not current:
            raise web.HTTPNotFound(text="Assistant does not exist")
        if current.get("enabled") == 0:
            return web.json_response(ctx.create_error_response(
                "Restore this Assistant before editing or publishing it", "AssistantArchived"), status=409)
        body = await request.json()
        name = str(body.get("name", current.get("name")) or "").strip()[:200]
        if not name:
            return web.json_response(ctx.create_error_response("Name is required", "ValidationError"), status=400)
        if g_db.assistant_name_exists(current["filestoreId"], name, user=user, exclude_id=assistant_id):
            return web.json_response(ctx.create_error_response(
                f"An Assistant named '{name}' already exists", "AlreadyExists"), status=409)
        try:
            config = assistants.validate_config(body.get("config", current.get("config")))
        except ValueError as e:
            return web.json_response(ctx.create_error_response(str(e), "ValidationError"), status=400)
        published = bool(body.get("published", current.get("publishedAt") is not None))
        public_id = assistants.new_public_id() if body.get("regeneratePublicId") else current["publicId"]
        await g_db.update_assistant_async(assistant_id, {
            "name": name,
            "publicId": public_id,
            # Lifecycle transitions have dedicated routes. An ordinary save must never silently
            # restore or archive an Assistant as a side effect.
            "enabled": current.get("enabled", 1),
            "publishedAt": current.get("publishedAt") or datetime.now() if published else None,
            "config": config,
        }, user=user)
        if published:
            await g_db.update_filestore_async(current["filestoreId"], {"visibility": "public"}, user=user)
        return web.json_response(assistant_dto(g_db.get_assistant(assistant_id, user=user), request))

    ctx.add_put("assistants/{id}", update_assistant)

    async def assistant_delete_summary(request):
        denied = auth_error(request)
        if denied:
            return denied
        summary = g_db.assistant_delete_summary(
            int(request.match_info["id"]), user=ctx.get_username(request))
        if not summary:
            raise web.HTTPNotFound(text="Assistant does not exist")
        return web.json_response(summary)

    ctx.add_get("assistants/{id}/delete-summary", assistant_delete_summary)

    async def archive_assistant(request):
        denied = auth_error(request)
        if denied:
            return denied
        found = await g_db.archive_assistant_async(int(request.match_info["id"]), user=ctx.get_username(request))
        if not found:
            raise web.HTTPNotFound(text="Assistant does not exist")
        return web.json_response({"archived": True, "conversationsRetained": True})

    ctx.add_delete("assistants/{id}", archive_assistant)

    async def restore_assistant(request):
        denied = auth_error(request)
        if denied:
            return denied
        assistant_id = int(request.match_info["id"])
        user = ctx.get_username(request)
        try:
            restored = await g_db.restore_assistant_async(assistant_id, user=user)
        except ValueError as e:
            return web.json_response(ctx.create_error_response(
                str(e), "AlreadyExists"), status=409)
        if not restored:
            raise web.HTTPNotFound(text="Assistant does not exist")
        return web.json_response(assistant_dto(restored, request))

    ctx.add_post("assistants/{id}/restore", restore_assistant)

    async def delete_assistant(request):
        denied = auth_error(request)
        if denied:
            return denied
        assistant_id = int(request.match_info["id"])
        user = ctx.get_username(request)
        summary = g_db.assistant_delete_summary(assistant_id, user=user)
        if not summary:
            raise web.HTTPNotFound(text="Assistant does not exist")
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            body = {}
        confirmation = body.get("confirm") if isinstance(body, dict) else None
        if confirmation != summary["name"]:
            return web.json_response(ctx.create_error_response(
                f'Type "{summary["name"]}" to confirm permanent deletion',
                "ConfirmationRequired"), status=400)
        try:
            deleted = await g_db.delete_assistant_async(
                assistant_id, user=user, confirmation=confirmation)
        except ValueError as e:
            return web.json_response(ctx.create_error_response(
                str(e), "ConfirmationRequired"), status=400)
        if not deleted:
            raise web.HTTPNotFound(text="Assistant does not exist")
        return web.json_response({"deleted": deleted})

    ctx.add_delete("assistants/{id}/permanent", delete_assistant)

    async def assistant_conversations(request):
        denied = signed_in_error(request)
        if denied:
            return denied
        assistant_id = int(request.match_info["id"])
        rows = g_db.query_assistant_conversations(
            assistant_id, user=ctx.get_username(request), take=request.query.get("take", 100))
        return web.json_response(rows)

    ctx.add_get("assistants/{id}/conversations", assistant_conversations)

    async def assistant_conversation(request):
        denied = signed_in_error(request)
        if denied:
            return denied
        assistant_id = int(request.match_info["id"])
        row = g_db.get_assistant_conversation(
            int(request.match_info["conversationId"]), assistant_id=assistant_id,
            user=ctx.get_username(request))
        if not row:
            raise web.HTTPNotFound(text="Conversation does not exist")
        row["messages"] = g_db.query_assistant_messages(row["id"])
        return web.json_response(row)

    ctx.add_get("assistants/{id}/conversations/{conversationId}", assistant_conversation)

    def cors_headers(request, allowed):
        origin = request.headers.get("Origin")
        headers = {"Vary": "Origin"}
        if allowed:
            headers["Access-Control-Allow-Origin"] = origin or "*"
        return headers

    def public_assistant_or_error(request):
        public_id = request.match_info.get("publicId") or request.query.get("g") or ""
        row = g_db.get_public_assistant(public_id)
        store = g_db.get_filestore(row["filestoreId"], user=row.get("user")) if row else None
        if not row or not store or store.get("visibility") != "public":
            raise web.HTTPNotFound(text="Assistant is unavailable")
        return row

    async def public_assistant_script(request):
        headers = {"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"}
        public_id = request.query.get("g") or ""
        row = g_db.get_public_assistant(public_id)
        store = g_db.get_filestore(row["filestoreId"], user=row.get("user")) if row else None
        if not row or not store or store.get("visibility") != "public":
            message = "Gemini Assistant widget failed to load: Assistant is unavailable (404)"
            return web.Response(text=f"console.error({json.dumps(message)});",
                                content_type="application/javascript", headers=headers)
        try:
            with open(widget_path, encoding="utf-8") as f:
                widget = f.read()
            try:
                markdown = bundled_markdown_source()
            except Exception as error:
                ctx.err("Failed embedding the bundled Marked renderer", error)
                markdown = ('console.warn("Gemini Assistant Markdown renderer is unavailable; '
                            'using plain text.");return null;')
            config = assistants.public_config(row, request_base_url(request))
            source = (f"(()=>{{const CONFIG={json.dumps(config, separators=(',', ':'))};"
                      f"const SCRIPT=document.currentScript;const MARKDOWN=(()=>{{\n{markdown}\n}})();"
                      f"const mount=()=>{{\n{widget}\n}};"
                      "if(document.readyState!=='loading')mount();"
                      "else addEventListener('DOMContentLoaded',mount,{once:true});})();")
        except Exception as error:
            ctx.err("Failed generating Gemini Assistant widget script", error)
            source = 'console.error("Gemini Assistant widget failed to load. Check the server logs for details.");'
        return web.Response(text=source, content_type="application/javascript", headers=headers)

    ctx.add_get("public/assistants/widget.js", public_assistant_script)

    def assistant_generation(row, store, messages):
        config = assistants.normalize_config(row.get("config"))
        behavior = config["behavior"]
        system = assistants.system_instruction(behavior)
        file_search = {"file_search_store_names": [store["name"]], "top_k": 10}
        expression = assistants.metadata_filter(config["scope"])
        if expression:
            file_search["metadata_filter"] = expression
        contents = [{
            "role": "model" if m["role"] == "assistant" else "user",
            "parts": [{"text": m.get("content") or ""}],
        } for m in messages[-20:] if m.get("role") in ("user", "assistant")]
        return behavior, {
            "model": assistants.resolve_model(
                config, os.getenv("GEMINI_ASSISTANT_MODEL", "gemini-flash-latest")),
            "contents": contents,
            "config": {"system_instruction": system, "tools": [{"file_search": file_search}]},
        }

    def result_citations(result, enabled=True):
        citations, seen = [], set()
        if enabled:
            for candidate in getattr(result, "candidates", None) or []:
                grounding = getattr(candidate, "grounding_metadata", None)
                for chunk in getattr(grounding, "grounding_chunks", None) or []:
                    context = getattr(chunk, "retrieved_context", None)
                    if not context:
                        continue
                    title = getattr(context, "title", None) or "Source"
                    url = getattr(context, "uri", None)
                    key = (title, url)
                    if key not in seen:
                        seen.add(key)
                        citations.append({"title": title, "url": url})
        return citations

    def resolve_citation_urls(citations, store, user):
        """Replace Gemini retrieval URIs with the imported document's public source URL."""
        if not citations:
            return citations
        documents = g_db.query_documents_all({
            "filestoreId": store["id"], "fields": "displayName,sourceKey,sourceUrl",
        }, user=user)
        source_urls = {}
        for doc in documents:
            source_url = doc.get("sourceUrl")
            if not source_url:
                continue
            for value in (doc.get("displayName"), doc.get("sourceKey")):
                if not value:
                    continue
                key = str(value).strip().lower()
                source_urls.setdefault(key, source_url)
                source_urls.setdefault(posixpath.basename(key), source_url)
        for citation in citations:
            title = str(citation.get("title") or "").strip().lower()
            source_url = source_urls.get(title) or source_urls.get(posixpath.basename(title))
            if source_url:
                citation["url"] = source_url
            elif not str(citation.get("url") or "").startswith(("http://", "https://")):
                citation["url"] = None
        return citations

    def assistant_answer(row, store, messages):
        behavior, request = assistant_generation(row, store, messages)
        result = g_client.models.generate_content(**request)
        text = (getattr(result, "text", None) or "").strip()
        strict = behavior["grounded"] and behavior["strictGrounding"]
        citations = resolve_citation_urls(
            result_citations(result, behavior["citations"] or strict), store, row.get("user"))
        return assistants.enforce_grounding(text, citations, behavior)

    async def public_assistant_chat(request):
        row = public_assistant_or_error(request)
        config = assistants.normalize_config(row.get("config"))
        allowed_origins = config["hosting"]["allowedOrigins"]
        origin = request.headers.get("Origin")
        allowed = assistants.origin_allowed(origin, allowed_origins)
        headers = cors_headers(request, allowed)
        if not allowed:
            return web.json_response(ctx.create_error_response(
                "This website is not allowed to use this Assistant", "OriginNotAllowed"),
                status=403, headers=headers)
        limit = config["hosting"]["requestsPerMinute"]
        remote = request.remote or "unknown"
        if not assistant_limiter.allow((row["id"], remote), limit):
            return web.json_response(ctx.create_error_response(
                "Too many requests. Please wait a moment and try again.", "RateLimited"),
                status=429, headers={**headers, "Retry-After": "60"})
        try:
            body = json.loads(await request.text())
        except Exception:
            return web.json_response(ctx.create_error_response("Invalid request", "ValidationError"),
                                     status=400, headers=headers)
        message = str(body.get("message") or "").strip()[:8000]
        session_id = str(body.get("sessionId") or "").strip()[:100]
        if not message or not re.fullmatch(r"[A-Za-z0-9._~-]{12,100}", session_id):
            return web.json_response(ctx.create_error_response(
                "A message and valid sessionId are required", "ValidationError"), status=400, headers=headers)
        store = g_db.get_filestore(row["filestoreId"], user=row.get("user"))
        if not store or not store.get("name"):
            return web.json_response(ctx.create_error_response("Assistant knowledge base is unavailable"),
                                     status=503, headers=headers)
        conversation = g_db.find_assistant_conversation(row["id"], session_id)
        if not conversation:
            conversation_id = await g_db.create_assistant_conversation_async(
                row, session_id, origin, str(body.get("pageUrl") or "")[:2000],
                request.headers.get("User-Agent", "")[:1000])
            conversation = g_db.get_assistant_conversation(conversation_id)
        await g_db.add_assistant_message_async(conversation, "user", message)
        history = g_db.query_assistant_messages(conversation["id"])
        if body.get("stream"):
            response = web.StreamResponse(status=200, headers=headers)
            response.content_type = "application/x-ndjson"
            await response.prepare(request)
            loop = asyncio.get_running_loop()
            queue = asyncio.Queue()

            def produce():
                try:
                    behavior, generation = assistant_generation(row, store, history)
                    strict = behavior["grounded"] and behavior["strictGrounding"]
                    for chunk in g_client.models.generate_content_stream(**generation):
                        try:
                            delta = getattr(chunk, "text", None) or ""
                        except Exception:
                            delta = ""
                        loop.call_soon_threadsafe(queue.put_nowait, ("chunk", delta, result_citations(
                            chunk, behavior["citations"] or strict)))
                    loop.call_soon_threadsafe(queue.put_nowait, ("done", behavior, None))
                except Exception as error:
                    loop.call_soon_threadsafe(queue.put_nowait, ("error", error, None))

            producer = loop.run_in_executor(None, produce)
            chunks, citations, citation_keys, failure, connected = [], [], set(), None, True
            while True:
                kind, value, found = await queue.get()
                if kind == "chunk":
                    if value:
                        chunks.append(value)
                        if connected and not (config["behavior"]["grounded"] and
                                              config["behavior"]["strictGrounding"]):
                            try:
                                await response.write((json.dumps({"delta": value}) + "\n").encode())
                            except ConnectionResetError:
                                connected = False
                    for citation in found or []:
                        key = (citation.get("title"), citation.get("url"))
                        if key not in citation_keys:
                            citation_keys.add(key)
                            citations.append(citation)
                elif kind == "error":
                    failure = value
                    break
                else:
                    behavior = value
                    break
            await producer
            if failure:
                ctx.err(f"Assistant {row['id']} streaming chat failed", failure)
                fallback = config["behavior"]["fallback"]
                await g_db.add_assistant_message_async(
                    conversation, "assistant", fallback, error=ctx.error_message(failure))
                if connected:
                    await response.write((json.dumps({"error": "The Assistant could not answer right now."}) + "\n").encode())
            else:
                answer = "".join(chunks).strip()
                citations = resolve_citation_urls(citations, store, row.get("user"))
                answer, citations = assistants.enforce_grounding(answer, citations, behavior)
                await g_db.add_assistant_message_async(conversation, "assistant", answer, citations=citations)
                if connected:
                    if (behavior["grounded"] and behavior["strictGrounding"]) or not chunks:
                        await response.write((json.dumps({"delta": answer}) + "\n").encode())
                    await response.write((json.dumps({"done": True, "citations": citations,
                                                      "conversationId": conversation["id"]}) + "\n").encode())
            if connected:
                await response.write_eof()
            return response
        try:
            answer, citations = await asyncio.get_running_loop().run_in_executor(
                None, assistant_answer, row, store, history)
            await g_db.add_assistant_message_async(conversation, "assistant", answer, citations=citations)
            return web.json_response({
                "conversationId": conversation["id"], "message": answer, "citations": citations,
            }, headers=headers)
        except Exception as e:
            ctx.err(f"Assistant {row['id']} chat failed", e)
            await g_db.add_assistant_message_async(
                conversation, "assistant", config["behavior"]["fallback"], error=ctx.error_message(e))
            return web.json_response(ctx.create_error_response(
                "The Assistant could not answer right now. Please try again.", "AssistantError"),
                status=500, headers=headers)

    ctx.add_post("public/assistants/{publicId}/chat", public_assistant_chat)

    # --- filter capabilities ------------------------------------------------------------

    async def get_capabilities(request):
        """
        What `metadata_filter` actually supports here.

        Google documents one filter form (`field="value"`) and delegates the rest to AIP-160, so
        whether `:` on a stringList, numeric comparison, OR and NOT work is a property of the
        deployment rather than something to assume. Probing it once and caching the answer lets
        the filter builder degrade instead of silently returning nothing.
        """
        return web.json_response(load_capabilities())

    ctx.add_get("capabilities", get_capabilities)

    async def probe_capabilities(request):
        denied = auth_error(request)
        if denied:
            return denied
        result = await asyncio.get_running_loop().run_in_executor(None, run_capability_probe)
        save_capabilities(result)
        return web.json_response(result)

    ctx.add_post("capabilities/probe", probe_capabilities)

    # Start the upload worker to check for pending uploads
    try:
        g_worker.start()
    except Exception as e:
        ctx.err("Failed to start UploadWorker", e)
    try:
        g_search_worker.start()
    except Exception as e:
        ctx.err("Failed to start SearchWorker", e)


__install__ = install
