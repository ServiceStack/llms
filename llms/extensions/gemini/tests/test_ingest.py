"""
Tests for the gemini ingest pipeline.

The load-bearing one is `test_reimport_is_free`: re-importing an unchanged source must perform no
writes and spend nothing. That's what makes a scheduled sync viable, and it's the guarantee most
likely to regress silently.

    python3 -m pytest gemini/tests/test_ingest.py
    python3 gemini/tests/test_ingest.py          # no pytest required
"""

import importlib.util
import os
import pathlib
import shutil
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))


def _stub_llms_db():
    """
    `db.py` imports `llms.db`, which is only importable when running inside the llms package.
    Stub the names it needs so these tests also run standalone against a checkout of the
    extension alone; the real module wins whenever it's available.
    """
    try:
        import llms.db  # noqa: F401
        return
    except ImportError:
        pass
    import sys
    import types as _types

    pkg = _types.ModuleType("llms")
    dbmod = _types.ModuleType("llms.db")
    dbmod.DbManager = object
    for fn in ("order_by", "select_columns", "valid_columns"):
        setattr(dbmod, fn, lambda *a, **k: "")
    dbmod.to_dto = lambda ctx, row, cols: row
    pkg.db = dbmod
    sys.modules.setdefault("llms", pkg)
    sys.modules.setdefault("llms.db", dbmod)


def _load(name):
    if name == "db":
        _stub_llms_db()
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, "..", f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ig = _load("ingest")
gdb = _load("db")


class Ctx:
    def log(self, *a):
        pass

    def dbg(self, *a):
        pass

    def err(self, *a):
        pass


SOURCE = {
    "category": {"root": "docs"},
    "volatile": [r"(?m)^Generated: build [0-9a-f]+$"],
    "extractorVer": "1",
    "extract": {"selector": "main"},
    "rules": {
        "defaults": {"product": "ServiceStack", "locale": "en"},
        "rules": [
            {"match": "**/reference/**", "set": {"docType": "reference"}},
            {"match": "docs/guides/**", "set": {"docType": "guide", "versions": ["v8"]}},
            {"match": "**/internal/**", "skip": True},
        ],
    },
}

FILES = {
    "docs/guides/auth/jwt.md": (
        "---\ndocType: guide\ntags: [security, auth]\nstatus: published\n---\n"
        "# JWT Authentication\n\nConfigure JWT auth by registering the JwtAuthProvider in your "
        "AppHost. Tokens are signed with HS256 by default and expire after 14 days unless you "
        "override the expiry.\n"
    ),
    "docs/guides/perf/caching.md": (
        "# Caching\n\nUse the caching provider to store computed responses. Redis and in-memory "
        "providers are both supported and can be swapped without changing calling code at all.\n"
        "Generated: build a91f3c2\n"
    ),
    "docs/reference/api.md": (
        "# API Reference\n\nEvery service method accepts a request DTO and returns a response DTO. "
        "The routing table maps HTTP verbs onto those types, and content negotiation is handled "
        "automatically for you.\n"
    ),
    "docs/internal/runbook.md": (
        "# On-call Runbook\n\nProduction database credentials are stored in the vault. Escalate to "
        "the platform team if the primary replica fails over more than twice in one hour.\n"
    ),
    "docs/index.html": (
        "<html><head><style>body{color:red}</style></head><body>"
        "<nav>Home | Guides | API</nav><header>Skip to content</header>"
        "<main><h1>Documentation</h1><p>Welcome to the documentation for our platform. Start with "
        "the guides, then consult the reference when you need specifics about a service method.</p>"
        "<p>Was this page helpful?</p></main><footer>Copyright &copy; 2026</footer></body></html>"
    ),
    "docs/tiny.md": "nav only\n",
    "src/main.py": "print('not docs')\n",
}

INDEXED = [
    "docs/guides/auth/jwt.md",
    "docs/guides/perf/caching.md",
    "docs/index.html",
    "docs/reference/api.md",
]


class IngestTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="gemini-ingest-")
        for rel, body in FILES.items():
            path = pathlib.Path(self.root, rel)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        os.makedirs(os.path.join(self.root, "docs", ".git"), exist_ok=True)
        pathlib.Path(self.root, "docs", ".git", "config").write_text("x")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def source(self):
        return ig.FolderSource(Ctx(), {"path": self.root})

    def plan(self, existing=None, source_row=None):
        return ig.build_plan(source_row or SOURCE, self.source(), existing or {})

    def index(self, plan):
        """The state the index would be in after applying `plan`."""
        return {
            d["sourceKey"]: {
                "id": i + 1,
                "sourceKey": d["sourceKey"],
                "contentHash": d["contentHash"],
                "metadataHash": d["metadataHash"],
                "extractorVer": d["extractorVer"],
            }
            for i, d in enumerate(plan.add)
        }

    def write(self, rel, body):
        pathlib.Path(self.root, rel).write_text(body, encoding="utf-8")


class TestDiscovery(IngestTestCase):
    def test_first_run_adds_expected_documents(self):
        plan = self.plan()
        self.assertEqual(sorted(d["sourceKey"] for d in plan.add), INDEXED)
        self.assertEqual(plan.counts()["removed"], 0)

    def test_skip_rule_excludes_internal(self):
        keys = [d["sourceKey"] for d in self.plan().add]
        self.assertNotIn("docs/internal/runbook.md", keys)

    def test_outside_root_is_not_part_of_the_source(self):
        reasons = {s["sourceKey"]: s["reason"] for s in self.plan().skipped}
        self.assertIn("outside root", reasons.get("src/main.py", ""))

    def test_default_excludes_apply(self):
        plan = self.plan()
        seen = [d["sourceKey"] for d in plan.add] + [s["sourceKey"] for s in plan.skipped]
        self.assertFalse([k for k in seen if "/.git/" in k])

    def test_near_empty_document_is_skipped_not_indexed(self):
        reasons = {s["sourceKey"]: s["reason"] for s in self.plan().skipped}
        self.assertIn("under", reasons.get("docs/tiny.md", ""))


class TestReimport(IngestTestCase):
    def test_reimport_is_free(self):
        """The guarantee: nothing changed upstream -> no writes, no embedding spend."""
        existing = self.index(self.plan())
        counts = self.plan(existing).counts()
        self.assertEqual(counts["embeds"], 0)
        self.assertEqual(counts["unchanged"], len(INDEXED))
        self.assertEqual(counts["added"], 0)
        self.assertEqual(counts["changed"], 0)
        self.assertEqual(counts["removed"], 0)

    def test_volatile_only_change_is_not_a_change(self):
        existing = self.index(self.plan())
        self.write(
            "docs/guides/perf/caching.md",
            FILES["docs/guides/perf/caching.md"].replace("a91f3c2", "77bde01"),
        )
        self.assertEqual(self.plan(existing).counts()["embeds"], 0)

    def test_content_change_replaces_rather_than_duplicates(self):
        existing = self.index(self.plan())
        self.write(
            "docs/guides/perf/caching.md",
            FILES["docs/guides/perf/caching.md"] + "\nRedis clustering is supported from v8.\n",
        )
        plan = self.plan(existing)
        self.assertEqual(plan.counts()["changed"], 1)
        self.assertEqual(plan.counts()["added"], 0)
        changed = plan.change[0]
        self.assertEqual(changed["sourceKey"], "docs/guides/perf/caching.md")
        # Keeping the row id is what makes this a replace: the old document is not left behind
        # to keep answering questions from stale content.
        self.assertEqual(changed["id"], existing["docs/guides/perf/caching.md"]["id"])

    def test_rule_change_is_metadata_only(self):
        existing = self.index(self.plan())
        source_row = {**SOURCE, "rules": {**SOURCE["rules"], "defaults": {"locale": "ja"}}}
        counts = self.plan(existing, source_row).counts()
        self.assertEqual(counts["changed"], 0)
        self.assertEqual(counts["metadataOnly"], len(INDEXED))

    def test_extractor_version_bump_reindexes_everything(self):
        existing = self.index(self.plan())
        counts = self.plan(existing, {**SOURCE, "extractorVer": "2"}).counts()
        self.assertEqual(counts["changed"], len(INDEXED))


class TestDeleteRails(IngestTestCase):
    def test_removal_is_detected(self):
        existing = self.index(self.plan())
        os.remove(os.path.join(self.root, "docs/reference/api.md"))
        self.assertEqual(self.plan(existing).counts()["removed"], 1)

    def test_small_corpus_does_not_trip_the_ratio_rail(self):
        """One legitimate deletion from four documents is 25% and must not need confirming."""
        existing = self.index(self.plan())
        os.remove(os.path.join(self.root, "docs/reference/api.md"))
        plan = self.plan(existing)
        self.assertIsNone(ig.check_delete_rails(plan, len(existing)))

    def test_mass_deletion_is_refused(self):
        plan = ig.Plan()
        plan.removed = [{"sourceKey": f"k{i}"} for i in range(150)]
        self.assertIsNotNone(ig.check_delete_rails(plan, 200))

    def test_proportional_deletion_refused_on_a_real_corpus(self):
        plan = ig.Plan()
        plan.removed = [{"sourceKey": f"k{i}"} for i in range(30)]
        self.assertIsNotNone(ig.check_delete_rails(plan, 100))


class TestCategory(unittest.TestCase):
    def test_derivation(self):
        cases = [
            ("docs/guides/auth/jwt.md", {"root": "docs"}, "guides/auth"),
            ("docs/index.md", {"root": "docs"}, ""),
            ("guides/auth/jwt.md", {}, "guides/auth"),
            ("docs\\guides\\perf\\cache.md", {"root": "docs"}, "guides/perf"),
            ("docs/a/b/c/d/e.md", {"root": "docs", "max_depth": 2}, "a/b"),
            ("src/README.md", {"root": "docs"}, None),
            ("docs/Getting Started/x.md", {"root": "docs"}, "Getting Started"),
        ]
        for key, kwargs, want in cases:
            with self.subTest(key=key):
                self.assertEqual(ig.derive_category(key, **kwargs), want)

    def test_ancestors_enable_subtree_filtering(self):
        self.assertEqual(ig.category_ancestors("guides/auth"), ["guides", "guides/auth"])
        self.assertEqual(ig.category_ancestors(""), [])
        self.assertIn("guides", ig.category_ancestors("guides/auth/deep"))

    def test_max_depth_limits_files_relative_to_the_category_root(self):
        self.assertTrue(ig.within_max_depth("index.md", max_depth=0))
        self.assertFalse(ig.within_max_depth("guides/index.md", max_depth=0))
        self.assertTrue(ig.within_max_depth("guides/index.md", max_depth=1))
        self.assertFalse(ig.within_max_depth("guides/auth/index.md", max_depth=1))
        self.assertTrue(ig.within_max_depth("docs/index.md", root="docs", max_depth=0))
        self.assertFalse(ig.within_max_depth("docs/guides/index.md", root="docs", max_depth=0))

    def test_max_depth_excludes_deeper_files_from_preview_counts(self):
        body = "# Documentation\n" + "word " * 60
        source = _FakeSource([
            ("index.md", body),
            ("guides/index.md", body),
            ("guides/auth/index.md", body),
        ])
        direct = ig.build_plan({"category": {"maxDepth": 0}}, source, {})
        self.assertEqual([d["sourceKey"] for d in direct.add], ["index.md"])
        self.assertEqual(direct.counts()["discovered"], 1)

        one_level = ig.build_plan({"category": {"maxDepth": 1}}, source, {})
        self.assertEqual([d["sourceKey"] for d in one_level.add], ["index.md", "guides/index.md"])
        self.assertEqual(one_level.counts()["discovered"], 2)


class TestGlobs(unittest.TestCase):
    def test_double_star_spans_directories(self):
        self.assertTrue(ig.glob_match("docs/guides/auth/jwt.md", "docs/**/*.md"))
        self.assertTrue(ig.glob_match("a.md", "**/*.md"))
        self.assertTrue(ig.glob_match("docs/internal/x.md", "**/internal/**"))

    def test_single_star_does_not_span_directories(self):
        self.assertFalse(ig.glob_match("docs/a/b.md", "docs/*.md"))
        self.assertFalse(ig.glob_match("docs/guides/x.txt", "docs/**/*.md"))


class TestExtraction(unittest.TestCase):
    def test_html_is_scoped_and_stripped(self):
        text, _, skip = ig.extract(FILES["docs/index.html"].encode(), "index.html", {"selector": "main"})
        self.assertIsNone(skip)
        self.assertIn("Welcome to the documentation", text)
        for noise in ("Home | Guides", "Copyright", "color:red", "Was this page helpful"):
            self.assertNotIn(noise, text)
        self.assertTrue(text.startswith("# Documentation"))

    def test_razor_is_stripped_then_converted_to_markdown(self):
        razor = b"""@page \"/add-servicestack-reference\"\n@model DocsPage\n<div>\n<h1>Visible docs</h1>\n@if (Model.Internal)\n{\n  <p>Hidden Razor content</p>\n}\n<p>This public documentation has enough useful words for readers.</p>\n</div>"""
        text, front, skip = ig.extract(razor, "index.cshtml", {"minWords": 0})
        self.assertIsNone(skip)
        self.assertEqual(front["route"], "/add-servicestack-reference")
        self.assertIn("# Visible docs", text)
        self.assertIn("public documentation", text)
        self.assertNotIn("@page", text)
        self.assertNotIn("Hidden Razor content", text)

    def test_frontmatter_is_parsed_and_removed_from_body(self):
        meta, body = ig.parse_frontmatter(FILES["docs/guides/auth/jwt.md"])
        self.assertEqual(meta["docType"], "guide")
        self.assertEqual(meta["tags"], ["security", "auth"])
        self.assertFalse(body.lstrip().startswith("---"))

    def test_unsupported_type_is_reported_not_raised(self):
        _, _, skip = ig.extract(b"%PDF-1.4", "manual.pdf")
        self.assertIn("unsupported", skip)


class TestHashing(unittest.TestCase):
    def test_normalisation_is_deterministic(self):
        self.assertEqual(ig.content_hash("Hello  \r\nworld\n\n\n\nagain\n"), ig.content_hash("Hello\nworld\n\nagain"))

    def test_metadata_hash_ignores_ordering(self):
        self.assertEqual(ig.metadata_hash({"tags": ["b", "a"], "x": None}), ig.metadata_hash({"tags": ["a", "b"]}))


class TestMetadataRules(unittest.TestCase):
    def test_precedence(self):
        meta, _ = ig.derive_metadata(
            "docs/guides/auth/jwt.md",
            SOURCE["rules"],
            frontmatter={"docType": "guide", "tags": ["security"], "status": "published"},
        )
        self.assertEqual(meta["product"], "ServiceStack")
        self.assertEqual(meta["versions"], ["v8"])
        self.assertEqual(meta["tags"], ["security"])

    def test_override_wins(self):
        meta, _ = ig.derive_metadata("docs/reference/api.md", SOURCE["rules"], override={"status": "draft"})
        self.assertEqual(meta["docType"], "reference")
        self.assertEqual(meta["status"], "draft")

    def test_skip_rule_returns_none(self):
        meta, _ = ig.derive_metadata("docs/internal/runbook.md", SOURCE["rules"])
        self.assertIsNone(meta)


class TestCustomMetadataWireFormat(unittest.TestCase):
    """
    The payload the worker sends must match Gemini's REST wire format.

    `string_list_value` has two shapes: requests need `{"values": [...]}`, while responses come
    back unwrapped through `custom_metadata_dto()`. Emitting the bare list looked correct in
    isolation and is rejected by the API, so it is pinned here.
    """

    def setUp(self):
        self.db = _load("db")
        self.client = _load("client")

    def doc(self):
        return {
            "id": 4, "hash": "abc", "category": "guides/auth",
            "categoryPath": '["guides","guides/auth"]', "docType": "guide",
            "sourceUpdatedAt": 1755648000, "versions": '["v7","v8"]', "tags": '["security"]',
        }

    def test_payload_uses_rest_field_names(self):
        payload = self.client._wire({
            "display_name": "x", "custom_metadata": self.db.to_custom_metadata(self.doc())
        })
        self.assertEqual(payload["displayName"], "x")
        versions = next(x for x in payload["customMetadata"] if x["key"] == "versions")
        self.assertEqual(versions["stringListValue"], {"values": ["v7", "v8"]})

    def test_sent_and_returned_shapes_compare_equal(self):
        doc = self.doc()
        sent = self.db.to_custom_metadata(doc)
        returned = [
            {"key": c["key"], **({"string_list_value": c["string_list_value"]["values"]}
                                 if "string_list_value" in c
                                 else {k: v for k, v in c.items() if k != "key"})}
            for c in sent
        ]
        self.assertFalse(self.db.metadata_differs({**doc, "customMetadata": returned}))
        self.assertTrue(self.db.metadata_differs({**doc, "versions": '["v9"]', "customMetadata": returned}))



class TestPushedMetadataKeys(unittest.TestCase):
    """
    Gemini indexes a camelCase custom_metadata key without complaint and then never matches it in
    a `metadata_filter`. Probed against the live API: one document carrying the same value under
    `docType`, `doctype` and `doc_type` is found by the latter two and not by the first; likewise
    `sortkey` vs `sortKey` for numeric comparison. Everything else in AIP-160 works.

    So key casing is a correctness property, not a style choice, and its failure mode is silent.
    """

    def test_every_pushed_key_is_lowercase(self):
        offenders = [key for _, (key, _) in gdb.PUSHED_METADATA.items() if key != key.lower()]
        self.assertEqual(offenders, [], f"camelCase keys are unfilterable in Gemini: {offenders}")

    def test_local_columns_may_stay_camel_case(self):
        """The mapping is what lets the codebase keep its own convention on the local side."""
        self.assertIn("docType", gdb.PUSHED_METADATA)
        self.assertEqual(gdb.PUSHED_METADATA["docType"][0], "doc_type")
        self.assertEqual(gdb.PUSHED_METADATA["sourceUpdatedAt"][0], "updated_at")

    def test_list_values_are_wrapped_for_the_sdk(self):
        """A bare list is rejected by the SDK's StringList model before the request is sent."""
        cm = {c["key"]: c for c in gdb.to_custom_metadata({"versions": ["v7", "v8"]})}
        self.assertEqual(cm["versions"]["string_list_value"], {"values": ["v7", "v8"]})


class TestArchiveExpansion(unittest.TestCase):
    """
    A .zip is a transport, not a document.

    Uploading one indexes what's inside it — indexing the archive itself would produce a single
    useless blob. These cover the rules that make that predictable: the wrapper directory every
    "Download ZIP" produces is stripped, junk never reaches the store, and an unsupported type is
    passed through for Gemini rather than dropped.

    The logic under test lives in `expand_zip` inside the extension's `install()` closure, so it's
    loaded out of the source here rather than imported.
    """

    PROSE = (
        "Configure the provider by registering it in your application host, then set the expiry "
        "and signing options that suit the deployment you are running in production today.\n"
    )

    @classmethod
    def setUpClass(cls):
        import io
        import posixpath
        import textwrap
        import zipfile

        with open(os.path.join(_HERE, "..", "__init__.py"), encoding="utf-8") as f:
            src = f.read()
        body = src[src.index("    def expand_zip(content, base_category):"):
                   src.index("    async def upload_to_filestore(request):")]
        ns = {"zipfile": zipfile, "io": io, "ingest": ig, "posixpath": posixpath,
              "ctx": type("C", (), {"dbg": lambda s, *a: None})()}
        exec(textwrap.dedent(body), ns)
        cls.expand = staticmethod(ns["expand_zip"])

    def build(self, entries):
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for name, content in entries.items():
                z.writestr(name, content)
        return buf.getvalue()

    def github_zip(self):
        return self.build({
            "docs-main/guides/auth/jwt.md": "# JWT\n\n" + self.PROSE,
            "docs-main/index.html":
                "<html><body><nav>Home | Guides</nav><main><h1>Docs</h1><p>"
                + self.PROSE + "</p></main><footer>Copyright 2026</footer></body></html>",
            "docs-main/tiny.md": "nav\n",
            "docs-main/manual.pdf": b"%PDF-1.4 binary",
            "__MACOSX/._junk": "x",
            "docs-main/.git/config": "x",
        })

    def test_wrapper_directory_is_stripped(self):
        keys = {e["key"] for e in self.expand(self.github_zip(), None)}
        self.assertIn("guides/auth/jwt.md", keys)
        self.assertFalse([k for k in keys if "docs-main" in k])

    def test_junk_never_reaches_the_store(self):
        """Also guards the strip: a stray __MACOSX/ made the archive look like it had two roots."""
        keys = {e["key"] for e in self.expand(self.github_zip(), None)}
        self.assertFalse([k for k in keys if "__MACOSX" in k or ".git" in k])

    def test_folder_structure_becomes_the_category(self):
        cats = {e["key"]: e["category"] for e in self.expand(self.github_zip(), None)}
        self.assertEqual(cats["guides/auth/jwt.md"], "guides/auth")
        self.assertIsNone(cats["index.html"])

    def test_base_category_prefixes_the_archives_own_structure(self):
        cats = {e["category"] for e in self.expand(self.github_zip(), "imported")}
        self.assertIn("imported/guides/auth", cats)
        self.assertIn("imported", cats)

    def test_near_empty_skipped_but_unsupported_type_passed_through(self):
        keys = {e["key"] for e in self.expand(self.github_zip(), None)}
        self.assertNotIn("tiny.md", keys)
        self.assertIn("manual.pdf", keys)

    def test_html_is_extracted_to_markdown(self):
        entry = next(e for e in self.expand(self.github_zip(), None) if e["key"] == "index.html")
        self.assertEqual(entry["displayName"], "index.md")
        self.assertIn(b"Configure", entry["content"])
        self.assertNotIn(b"Guides", entry["content"])

    def test_flat_archive_keeps_its_top_level_folder(self):
        zipped = self.build({"a.md": "# A\n\n" + self.PROSE, "guides/b.md": "# B\n\n" + self.PROSE})
        self.assertEqual({e["key"] for e in self.expand(zipped, None)}, {"a.md", "guides/b.md"})

    def test_single_root_archive_strips_exactly_one_level(self):
        zipped = self.build({"only/deep/c.md": "# C\n\n" + self.PROSE})
        self.assertEqual({e["key"] for e in self.expand(zipped, None)}, {"deep/c.md"})


class TestTrustedRoots(unittest.TestCase):
    """
    The rail that decides which folders a non-admin may import from. The interesting cases are
    the ones that look allowed: a sibling that shares a name prefix, and a symlink that lives
    inside a trusted root but points out of it.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "docs")
        os.makedirs(os.path.join(self.root, "guides"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_root_itself_is_allowed(self):
        self.assertTrue(ig.within_roots(self.root, [self.root]))

    def test_descendant_is_allowed(self):
        self.assertTrue(ig.within_roots(os.path.join(self.root, "guides"), [self.root]))

    def test_outside_is_rejected(self):
        other = os.path.join(self.tmp, "secrets")
        os.makedirs(other)
        self.assertFalse(ig.within_roots(other, [self.root]))

    def test_name_prefix_sibling_is_rejected(self):
        # '/tmp/x/docs-private' shares a string prefix with '/tmp/x/docs' but is not inside it.
        sibling = self.root + "-private"
        os.makedirs(sibling)
        self.assertFalse(ig.within_roots(sibling, [self.root]))

    def test_parent_is_rejected(self):
        self.assertFalse(ig.within_roots(self.tmp, [self.root]))

    def test_traversal_is_normalised_then_rejected(self):
        self.assertFalse(ig.within_roots(os.path.join(self.root, "..", "elsewhere"), [self.root]))

    def test_traversal_that_lands_back_inside_is_allowed(self):
        self.assertTrue(ig.within_roots(os.path.join(self.root, "guides", "..", "guides"), [self.root]))

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symlinks")
    def test_symlink_escaping_the_root_is_rejected(self):
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        link = os.path.join(self.root, "escape")
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        # The link *is* inside the root; what it resolves to is not.
        self.assertFalse(ig.within_roots(link, [self.root]))

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symlinks")
    def test_symlink_staying_inside_the_root_is_allowed(self):
        link = os.path.join(self.root, "alias")
        try:
            os.symlink(os.path.join(self.root, "guides"), link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        self.assertTrue(ig.within_roots(link, [self.root]))

    def test_any_of_several_roots_matches(self):
        second = os.path.join(self.tmp, "wiki")
        os.makedirs(second)
        self.assertTrue(ig.within_roots(second, [self.root, second]))

    def test_no_roots_matches_nothing(self):
        # The caller is expected to treat "no roots" as a hard failure, not as unrestricted.
        self.assertFalse(ig.within_roots(self.root, []))



class TestGeminiNumericRoundTrip(unittest.TestCase):
    """
    Gemini stores numeric_value as a float32 and serialises it with 8 significant digits, so a
    value we send is not the value we get back. Every pair below was captured from the live API.

    This is the bug that made the pending count unclearable: metadata_differs() compared the
    local epoch-seconds against Gemini's lossy echo, found them unequal, and reported the
    document as pending again the instant a re-index finished.
    """

    LIVE_SAMPLES = [
        (1730696874, 1730696800),
        (1726881179, 1726881200),
        (1688880329, 1688880400),
        (1766722738, 1766722700),
        (1692412589, 1692412500),
        (1720345042, 1720345100),
        (1786627362, 1786627300),
        (1787129369, 1787129300),
    ]

    def test_predicts_what_the_api_returned(self):
        for sent, returned in self.LIVE_SAMPLES:
            self.assertEqual(gdb.gemini_numeric(sent), float(returned), f"sent {sent}")

    def test_normalising_both_sides_converges(self):
        # The property that matters: whatever we send, our value and Gemini's echo of it
        # normalise to the same thing - so a document stops being pending once pushed.
        for sent, returned in self.LIVE_SAMPLES:
            self.assertEqual(gdb.gemini_numeric(sent), gdb.gemini_numeric(returned))

    def test_small_values_are_exact(self):
        # Document ids stay exact until 2**24, which is what keeps `id` usable as a filter key.
        for v in [0, 1, 65, 12345, 16777216]:
            self.assertEqual(gdb.gemini_numeric(v), float(v))

    def test_non_numeric_is_none_not_an_exception(self):
        self.assertIsNone(gdb.gemini_numeric("not a number"))
        self.assertIsNone(gdb.gemini_numeric(None))

    def test_document_with_a_timestamp_is_not_permanently_pending(self):
        # The end-to-end shape of the bug, at the level metadata_differs() works on.
        doc = {"id": 1, "hash": "abc", "sourceUpdatedAt": 1730696874}
        pushed = gdb.to_custom_metadata(doc)
        # What Gemini gives back for that push.
        echoed = []
        for item in pushed:
            if "numeric_value" in item:
                echoed.append({"key": item["key"], "numeric_value": gdb.gemini_numeric(item["numeric_value"])})
            else:
                echoed.append(dict(item))
        self.assertTrue(gdb.metadata_differs({**doc, "customMetadata": None}))
        self.assertFalse(gdb.metadata_differs({**doc, "customMetadata": echoed}),
                         "a freshly pushed document must not still read as pending")

    def test_a_real_edit_is_still_detected(self):
        # The tolerance must not swallow genuine changes.
        doc = {"id": 1, "hash": "abc", "docType": "guide"}
        pushed = gdb.to_custom_metadata(doc)
        self.assertTrue(gdb.metadata_differs({**doc, "docType": "faq", "customMetadata": pushed}))


class SourceUrlTemplateTests(unittest.TestCase):
    """
    `sourceUrl` is per-document, but the only ways to set one are a source default and a path
    rule - both single strings. Without expansion, "set the source URL" on a 1,500 document import
    means putting one identical link on all 1,500.
    """

    def expand(self, template, key, category=None, title=None, root=None):
        return ig.expand_template(template, ig.template_values(key, category, title, root))

    def test_the_case_this_exists_for(self):
        self.assertEqual(
            self.expand("https://docs.servicestack.net/{category}/{name}", "guides/auth.md", "guides"),
            "https://docs.servicestack.net/guides/auth",
        )

    def test_every_placeholder(self):
        v = ig.template_values("docs/guides/auth.md", "guides", "Auth", "docs", "/auth")
        self.assertEqual(v["fullpath"], "docs/guides/auth.md")
        self.assertEqual(v["path"], "guides/auth.md")
        self.assertEqual(v["pathnoext"], "guides/auth")
        self.assertEqual(v["dir"], "docs/guides")
        self.assertEqual(v["filename"], "auth.md")
        self.assertEqual(v["name"], "auth")
        self.assertEqual(v["ext"], "md")
        self.assertEqual(v["category"], "guides")
        self.assertEqual(v["title"], "Auth")
        self.assertEqual(v["route"], "/auth")

    def test_razor_route_builds_the_source_url(self):
        values = ig.template_values("Pages/Docs.cshtml", route="/add-servicestack-reference")
        self.assertEqual(ig.expand_template("https://docs.example{route}", values),
                         "https://docs.example/add-servicestack-reference")
        self.assertEqual(ig.expand_template("https://docs.example/{route}", values),
                         "https://docs.example/add-servicestack-reference")
        self.assertEqual(ig.extract_razor_route('\ufeff@page "/pdf"\n<h1>PDF</h1>'), "/pdf")
        self.assertIsNone(ig.extract_razor_route('@page "/products/{id}"'))
        self.assertIsNone(ig.extract_razor_route('@page "/products/{id:int?}"'))

        warnings = []
        self.assertIsNone(ig.expand_template(
            "https://docs.example{route}", ig.template_values("guide.md"), warnings.append))
        self.assertIn("{route}", warnings[0])

    def test_path_without_a_category_root_is_the_full_path(self):
        self.assertEqual(ig.template_values("guides/auth.md")["path"], "guides/auth.md")

    def test_path_template_strips_the_category_root(self):
        self.assertEqual(
            self.expand("https://docs.acme.com/{path}", "docs/guides/auth.md", root="docs"),
            "https://docs.acme.com/guides/auth.md",
        )

    def test_path_no_ext_strips_the_root_and_extension(self):
        self.assertEqual(
            self.expand("https://docs.acme.com/{pathNoExt}", "docs/guides/auth.md", root="docs"),
            "https://docs.acme.com/guides/auth",
        )

    def test_placeholders_are_case_insensitive(self):
        self.assertEqual(self.expand("x/{pathNoExt}", "a/b.md"), "x/a/b")

    def test_a_file_with_no_extension(self):
        v = ig.template_values("LICENSE")
        self.assertEqual((v["name"], v["ext"], v["pathnoext"]), ("LICENSE", "", "LICENSE"))

    def test_an_empty_placeholder_does_not_leave_a_double_slash(self):
        # A document in no category, which is the common case at a source root.
        self.assertEqual(
            self.expand("https://docs.acme.com/{category}/{name}", "intro.md", ""),
            "https://docs.acme.com/intro",
        )

    def test_the_scheme_survives_slash_collapsing(self):
        self.assertTrue(self.expand("https://x/{category}/{name}", "a.md", "c").startswith("https://"))

    def test_an_unknown_placeholder_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown Source URL variable"):
            self.expand("https://x/{nmae}", "a.md")

    def test_regex_placeholder_uses_its_first_capture(self):
        self.assertEqual(
            self.expand(r"https://x/{name:/^\d{4}-\d{2}-\d{2}_(.+)$/}",
                        "2026-09-04_servicestack-pdf.md"),
            "https://x/servicestack-pdf",
        )

    def test_regex_placeholder_without_a_capture_uses_the_match(self):
        self.assertEqual(
            self.expand(r"https://x/{name:/servicestack/}", "2026-09-04_servicestack-pdf.md"),
            "https://x/servicestack",
        )

    def test_regex_placeholder_non_match_omits_source_url(self):
        warnings = []
        value = ig.expand_template(
            r"https://x/{name:/^release-(.+)$/}",
            ig.template_values("2026-09-04_servicestack-pdf.md"),
            warnings.append,
        )
        self.assertIsNone(value)
        self.assertIn("omitting Source URL", warnings[0])

    def test_regex_placeholder_rejects_an_invalid_pattern(self):
        with self.assertRaisesRegex(ValueError, "Invalid regex"):
            ig.validate_template(r"https://x/{name:/([/}")

    def test_a_plain_url_is_untouched(self):
        self.assertEqual(self.expand("https://x/y", "a.md"), "https://x/y")

    def test_build_plan_expands_per_document(self):
        """The point: one template on the source, a different URL on every document."""
        source_row = {
            "category": {"root": "docs"},
            "rules": {"defaults": {"sourceUrl": "https://docs.acme.com/{category}/{name}"}},
        }
        source = _FakeSource([
            ("docs/guides/auth.md", "# Auth\n" + "word " * 60),
            ("docs/reference/api.md", "# Api\n" + "word " * 60),
        ])
        plan = ig.build_plan(source_row, source, {})
        urls = {d["sourceKey"]: d["sourceUrl"] for d in plan.add}
        self.assertEqual(urls, {
            "docs/guides/auth.md": "https://docs.acme.com/guides/auth",
            "docs/reference/api.md": "https://docs.acme.com/reference/api",
        })

    def test_build_plan_omits_source_url_only_for_non_matching_document(self):
        source_row = {
            "rules": {"defaults": {
                "sourceUrl": r"https://servicestack.net/posts/{name:/^[^_]+_(.+)$/}"
            }},
        }
        source = _FakeSource([
            ("2026-09-04_servicestack-pdf.md", "# Post\n" + "word " * 60),
            ("authors.md", "# Authors\n" + "word " * 60),
        ])
        warnings = []
        plan = ig.build_plan(source_row, source, {}, on_warning=warnings.append)
        docs = {d["sourceKey"]: d for d in plan.add}
        self.assertEqual(docs["2026-09-04_servicestack-pdf.md"]["sourceUrl"],
                         "https://servicestack.net/posts/servicestack-pdf")
        self.assertNotIn("sourceUrl", docs["authors.md"])
        self.assertIn("authors.md", warnings[0])

    def test_build_plan_can_require_a_resolved_source_url(self):
        source_row = {
            "config": {"requireSourceUrl": True},
            "rules": {"defaults": {"sourceUrl": "https://docs.example{route}"}},
        }
        source = _FakeSource([
            ("Routed.cshtml", '@page "/routed"\n<h1>Routed</h1>\n' + "word " * 60),
            ("Partial.cshtml", '<h1>Partial</h1>\n' + "word " * 60),
        ])
        plan = ig.build_plan(source_row, source, {})
        self.assertEqual([d["sourceKey"] for d in plan.add], ["Routed.cshtml"])
        self.assertEqual(plan.add[0]["sourceUrl"], "https://docs.example/routed")
        self.assertEqual(plan.skipped, [{"sourceKey": "Partial.cshtml", "reason": "Source URL is required"}])

    def test_build_plan_passes_the_category_root_to_path(self):
        source_row = {
            "category": {"root": "docs"},
            "rules": {"defaults": {"sourceUrl": "https://docs.acme.com/{path}"}},
        }
        source = _FakeSource([("docs/guides/auth.md", "# Auth\n" + "word " * 60)])
        plan = ig.build_plan(source_row, source, {})
        self.assertEqual(plan.add[0]["sourceUrl"], "https://docs.acme.com/guides/auth.md")

    def test_a_path_rule_can_template_too(self):
        source_row = {
            "category": {"root": "docs"},
            "rules": {"rules": [{"match": "**/reference/**", "set": {"sourceUrl": "https://api.acme.com/{name}"}}]},
        }
        source = _FakeSource([("docs/reference/api.md", "# Api\n" + "word " * 60)])
        plan = ig.build_plan(source_row, source, {})
        self.assertEqual(plan.add[0]["sourceUrl"], "https://api.acme.com/api")

    def test_changing_the_template_is_a_metadata_change(self):
        """Otherwise backfilling URLs onto an existing corpus would be a no-op re-run."""
        source = _FakeSource([("docs/guides/auth.md", "# Auth\n" + "word " * 60)])
        first = ig.build_plan(
            {"category": {"root": "docs"}, "rules": {"defaults": {"sourceUrl": "https://a.com/{name}"}}},
            source, {})
        prior = {first.add[0]["sourceKey"]: {**first.add[0], "id": 1}}
        again = ig.build_plan(
            {"category": {"root": "docs"}, "rules": {"defaults": {"sourceUrl": "https://b.com/{name}"}}},
            source, prior)
        self.assertEqual(len(again.metadata_only), 1)
        self.assertEqual(again.metadata_only[0]["sourceUrl"], "https://b.com/auth")


class _FakeSource:
    """Discovery and fetch over an in-memory list, which is all build_plan asks of a source."""

    def __init__(self, files):
        self.files = files

    def discover(self):
        return [ig.Item(key=k, size=len(t)) for k, t in self.files]

    def fetch(self, item):
        return dict(self.files)[item.key].encode()


if __name__ == "__main__":
    unittest.main(verbosity=2)
