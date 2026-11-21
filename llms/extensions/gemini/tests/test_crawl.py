"""Tests for staged crawl workspaces and import.json inheritance."""

import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import types
import unittest
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
PKG = "gemini_crawl_tests"
pkg = types.ModuleType(PKG)
pkg.__path__ = [str(ROOT)]
sys.modules[PKG] = pkg


def load(name):
    spec = importlib.util.spec_from_file_location(f"{PKG}.{name}", ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ingest = load("ingest")
crawl = load("crawl")


class CrawlTests(unittest.TestCase):
    def test_site_name_includes_port_with_dash(self):
        self.assertEqual(crawl.site_name("http://localhost:5000/docs"), "localhost-5000")
        self.assertEqual(crawl.site_name("https://Docs.Example.org/a"), "docs.example.org")

    def test_query_strings_get_stable_distinct_folders(self):
        a = crawl.page_relative_path("https://example.org/search?q=one")
        b = crawl.page_relative_path("https://example.org/search?q=two")
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("search--"))
        self.assertTrue(a.endswith(".md"))

    def test_page_paths_use_clean_markdown_files_and_directory_indexes(self):
        self.assertEqual(crawl.page_relative_path("https://example.org/"), "index.md")
        self.assertEqual(crawl.page_relative_path("https://example.org/docs/"), "docs/index.md")
        self.assertEqual(crawl.page_relative_path("https://example.org/docs/templates/next-rsc"),
                         "docs/templates/next-rsc.md")
        self.assertEqual(crawl.page_relative_path("https://example.org/about.html"), "about.md")

    def test_canonical_url_applies_scope_paths_and_query_policy(self):
        options = {"include": ["/docs/**"], "exclude": ["/docs/archive/**"], "query": {
            "mode": "allow", "allow": ["version", "lang"], "exclude": ["utm_*"],
        }}
        start = "https://example.org/docs/"
        self.assertEqual(
            crawl.canonical_url("guide?utm_source=x&lang=en&version=v2", start, start, options),
            "https://example.org/docs/guide?lang=en&version=v2",
        )
        self.assertIsNone(crawl.canonical_url("/docs/archive/old", start, start, options))
        self.assertIsNone(crawl.canonical_url("/docs-private/page", start, start, options))
        self.assertIsNone(crawl.canonical_url("https://other.org/docs/page", start, start, options))

    def test_canonical_url_resolves_against_page_but_keeps_start_scope(self):
        for start_path in ("/docs/intro.html", "/docs/", "/docs"):
            start = "https://example.org" + start_path
            base = "https://example.org/docs/nested/page.html"
            with self.subTest(start=start):
                self.assertEqual(crawl.canonical_url("../setup.html", base, start, {}),
                                 "https://example.org/docs/setup.html")
                self.assertIsNone(crawl.canonical_url("/docs-private/page", base, start, {}))
                self.assertIsNone(crawl.canonical_url("/outside", base, start, {}))
                self.assertIsNone(crawl.canonical_url("https://other.org/docs/page", base, start, {}))
        self.assertEqual(crawl.crawl_scope("https://example.org/intro.html"), "")
        self.assertEqual(crawl.crawl_scope("https://example.org/v1.0/docs/"), "/v1.0/docs")

    def test_allowed_host_does_not_replace_start_origin(self):
        start = "https://example.org/docs/"
        base = "https://allowed.org/docs/a"
        options = {"allowedHosts": ["allowed.org"]}
        self.assertEqual(crawl.canonical_url("https://example.org/docs/b", base, start, options),
                         "https://example.org/docs/b")
        self.assertIsNone(crawl.canonical_url("https://other.org/docs/b", base, start, options))

    def test_ordered_rules_support_follow_only_and_query_matching(self):
        options = {"rules": [
            {"match": "/sitemap/**", "action": "followOnly"},
            {"queryString": True, "action": "exclude"},
        ]}
        self.assertEqual(crawl.crawl_action("https://x/sitemap/a", options), "followOnly")
        self.assertEqual(crawl.crawl_action("https://x/page?q=1", options), "exclude")
        self.assertEqual(crawl.crawl_action("https://x/page", options), "save")

    def test_crawl_rule_schema_and_server_validation_share_the_contract(self):
        valid = [{"match": "/archive/**", "action": "exclude"},
                 {"queryString": True, "action": "followOnly"}]
        self.assertIs(crawl.validate_crawl_rules(valid), valid)
        self.assertEqual(crawl.CRAWL_RULE_SCHEMA["type"], "array")
        self.assertEqual(len(crawl.CRAWL_RULE_SCHEMA["items"]["oneOf"]), 2)
        with self.assertRaisesRegex(ValueError, "needs a path glob"):
            crawl.validate_crawl_rules([{"action": "exclude"}])

    def test_parser_extracts_canonical_robots_and_nofollow_links(self):
        parser = crawl.PageParser()
        parser.feed('''<html><head><link rel="canonical" href="/real"><meta name="robots" content="noindex,nofollow"></head>
            <body><a href="/one">One</a><a href="/two" rel="nofollow">Two</a></body></html>''')
        self.assertEqual(parser.canonical, "/real")
        self.assertEqual(parser.robots, {"noindex", "nofollow"})
        self.assertEqual(parser.links, [("/one", False), ("/two", True)])

    def test_directory_manifest_overwrites_root_metadata(self):
        with tempfile.TemporaryDirectory() as root:
            pathlib.Path(root, "import.json").write_text(json.dumps({"metadata": {
                "defaults": {"product": "Docs", "status": "draft", "tags": ["root"]},
                "rules": [{"match": "**/*.md", "set": {"locale": "en"}}],
            }}))
            pathlib.Path(root, "guides").mkdir()
            pathlib.Path(root, "guides", "import.json").write_text(json.dumps({"metadata": {
                "defaults": {"status": "published", "tags": ["guides"]},
            }}))
            cfg = crawl.effective_manifest(root, "guides/auth/page.md")
            self.assertEqual(cfg["metadata"]["defaults"], {
                "product": "Docs", "status": "published", "tags": ["guides"],
            })
            self.assertEqual(len(cfg["metadata"]["rules"]), 1)

    def test_saving_metadata_preserves_crawl_and_transforms(self):
        with tempfile.TemporaryDirectory() as root:
            crawl.write_json(os.path.join(root, "import.json"), {
                "crawl": {"url": "https://example.org"},
                "transforms": [{"pattern": "old", "replacement": "new"}],
            })
            crawl.save_metadata(root, {"defaults": {"product": "Docs"}, "rules": []})
            cfg = crawl.read_json(os.path.join(root, "import.json"))
            self.assertEqual(cfg["crawl"]["url"], "https://example.org")
            self.assertEqual(len(cfg["transforms"]), 1)

    def test_transforms_apply_only_to_matching_markdown(self):
        with tempfile.TemporaryDirectory() as root:
            pathlib.Path(root, "page.md").write_text("keep OLD", encoding="utf-8")
            pathlib.Path(root, "other.txt").write_text("OLD", encoding="utf-8")
            changed = crawl.apply_transforms(root, [{
                "match": "**/*.md", "pattern": "old", "replacement": "new", "flags": "gi",
            }])
            self.assertEqual(changed, 1)
            self.assertEqual(pathlib.Path(root, "page.md").read_text(), "keep new")
            self.assertEqual(pathlib.Path(root, "other.txt").read_text(), "OLD")

    def test_transform_schema_and_server_validation_share_the_contract(self):
        valid = [{"match": "**/*.md", "pattern": "old$", "replacement": "new", "flags": "gim"}]
        self.assertIs(crawl.validate_transforms(valid), valid)
        self.assertEqual(crawl.TRANSFORM_SCHEMA["type"], "array")
        with self.assertRaisesRegex(ValueError, "unsupported flags"):
            crawl.validate_transforms([{"pattern": "old", "flags": "x"}])
        with self.assertRaisesRegex(ValueError, "is invalid"):
            crawl.validate_transforms([{"pattern": "[", "flags": "g"}])
        with self.assertRaisesRegex(ValueError, "replacement is invalid.*invalid group reference 1"):
            crawl.validate_transforms([{"pattern": "text", "replacement": "\\1", "flags": "g"}])

    def test_crawled_page_browser_only_lists_and_reads_generated_pages(self):
        with tempfile.TemporaryDirectory() as root:
            pathlib.Path(root, "guide").mkdir()
            pathlib.Path(root, "guide", "index.md").write_text("# Guide", encoding="utf-8")
            pathlib.Path(root, "import.json").write_text(json.dumps({
                "crawl": {"generated": ["guide/index.md"]},
            }), encoding="utf-8")
            pathlib.Path(root, "notes.md").write_text("private", encoding="utf-8")
            self.assertEqual(crawl.list_crawled_pages(root), ["guide/index.md"])
            self.assertEqual(crawl.read_crawled_page(root, "guide/index.md"), "# Guide")
            with self.assertRaisesRegex(ValueError, "required"):
                crawl.read_crawled_page(root, "notes.txt")
            with self.assertRaisesRegex(ValueError, "not found"):
                crawl.read_crawled_page(root, "notes.md")
            with self.assertRaisesRegex(ValueError, "not found"):
                crawl.read_crawled_page(root, "../index.md")

    def test_zip_directory_manifest_and_frontmatter_override_defaults(self):
        prose = " ".join(["documentation"] * 30)
        with tempfile.TemporaryDirectory() as root:
            archive = os.path.join(root, "docs.zip")
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("import.json", json.dumps({"metadata": {"defaults": {
                    "product": "Docs", "status": "draft", "tags": ["root"],
                }}}))
                zf.writestr("guides/import.json", json.dumps({"metadata": {"defaults": {
                    "status": "published", "tags": ["guides"],
                }}}))
                zf.writestr("guides/auth/page.md", f"---\ntags: [page]\n---\n{prose}")
            source = ingest.ZipSource(None, {"path": archive})
            plan = ingest.build_plan({"rules": {}, "category": {}}, source, {})
            self.assertEqual(len(plan.add), 1)
            doc = plan.add[0]
            self.assertEqual(doc["product"], "Docs")
            self.assertEqual(doc["status"], "published")
            self.assertEqual(doc["tags"], ["page"])


class CrawlTraversalTests(unittest.IsolatedAsyncioTestCase):
    async def test_page_failures_are_reported_without_aborting_or_removing_previous_pages(self):
        from unittest.mock import patch
        import asyncio
        for failure in (302, 404, 410, 429, 503, "network", "timeout", "read"):
            class Response:
                headers = {"Content-Type": "text/html"}
                def __init__(self, url):
                    self.url = url
                    self.status = failure if url.endswith("/bad") and isinstance(failure, int) else 200
                async def __aenter__(self):
                    if self.url.endswith("/bad"):
                        if failure == "network": raise OSError("Connection failed")
                        if failure == "timeout": raise asyncio.TimeoutError()
                    return self
                async def __aexit__(self, *args): pass
                async def text(self, **kwargs):
                    if self.url.endswith("/bad") and failure == "read": raise OSError("Read failed")
                    if self.url.endswith("/docs/"):
                        return '<p>Start</p><a href="bad">Bad</a><a href="good">Good</a>'
                    return '<p>Good page</p>'

            class Session:
                async def __aenter__(self): return self
                async def __aexit__(self, *args): pass
                def get(self, url, **kwargs): return Response(url)

            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as root:
                pathlib.Path(root, "old.md").write_text("Original content")
                crawl.write_json(os.path.join(root, "import.json"), {"crawl": {"generated": ["old.md"]}})
                with patch("aiohttp.ClientSession", return_value=Session()):
                    result = await crawl.crawl_site(None, None, {
                        "url": "https://example.org/docs/", "respectRobots": False,
                    }, target_path=root)
                self.assertEqual(result["pages"], 2)
                self.assertEqual(result["requests"], 3)
                self.assertEqual(result["failed"], 1)
                self.assertEqual(result["stopReason"], "complete")
                error = result["errors"][0]
                self.assertEqual(error["url"], "https://example.org/docs/bad")
                self.assertEqual(error["statusCode"], failure if isinstance(failure, int) else 200 if failure == "read" else None)
                self.assertTrue(error["message"])
                self.assertEqual(result["retainedPages"], 1)
                self.assertIn("old.md", result["config"]["crawl"]["generated"])
                self.assertEqual(pathlib.Path(root, "old.md").read_text(), "Original content")
                failure = None
                with patch("aiohttp.ClientSession", return_value=Session()):
                    complete = await crawl.crawl_site(None, None, {
                        "url": "https://example.org/docs/", "respectRobots": False,
                    }, target_path=root)
                self.assertEqual(complete["failed"], 0)
                self.assertEqual(complete["retainedPages"], 0)
                self.assertFalse(pathlib.Path(root, "old.md").exists())

    async def test_failed_start_is_a_result_and_cancellation_still_propagates(self):
        from unittest.mock import patch
        import asyncio
        for failure in (503, "cancel"):
            class Response:
                status = 503
                headers = {"Content-Type": "text/html"}
                async def __aenter__(self):
                    if failure == "cancel": raise asyncio.CancelledError()
                    return self
                async def __aexit__(self, *args): pass

            class Session:
                async def __aenter__(self): return self
                async def __aexit__(self, *args): pass
                def get(self, url, **kwargs): return Response()

            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as root:
                pathlib.Path(root, "old.md").write_text("Original content")
                crawl.write_json(os.path.join(root, "import.json"), {"crawl": {"generated": ["old.md"]}})
                with patch("aiohttp.ClientSession", return_value=Session()):
                    task = crawl.crawl_site(None, None, {
                        "url": "https://example.org/docs/", "respectRobots": False,
                    }, target_path=root)
                    if failure == "cancel":
                        with self.assertRaises(asyncio.CancelledError): await task
                    else:
                        result = await task
                        self.assertEqual(result["pages"], 0)
                        self.assertEqual(result["failed"], 1)
                        self.assertEqual(result["retainedPages"], 1)
                self.assertEqual(pathlib.Path(root, "old.md").read_text(), "Original content")

    async def test_crawl_reports_limits_only_when_urls_remain_queued(self):
        from unittest.mock import patch
        cases = [(1, 10, False, "maxPages", 1, 3),
                 (2, 2, True, "maxRequests", 1, 2),
                 (4, 4, False, "complete", 4, 0),
                 (10, 10, False, "complete", 4, 0),
                 (1, 1, False, "maxPages", 1, 3)]
        for max_pages, max_requests, noindex, reason, saved, remaining in cases:
            class Response:
                status = 200
                headers = {"Content-Type": "text/html"}
                def __init__(self, url): self.url = url
                async def __aenter__(self): return self
                async def __aexit__(self, *args): pass
                async def text(self, **kwargs):
                    if self.url.endswith("/docs/"):
                        return '<p>Start</p><a href="a">A</a><a href="b">B</a><a href="c">C</a>'
                    return ('<meta name="robots" content="noindex">' if noindex else '') + '<p>' + self.url + '</p>'

            class Session:
                async def __aenter__(self): return self
                async def __aexit__(self, *args): pass
                def get(self, url, **kwargs): return Response(url)

            with self.subTest(reason=reason, max_pages=max_pages, max_requests=max_requests):
                with tempfile.TemporaryDirectory() as root, patch("aiohttp.ClientSession", return_value=Session()):
                    result = await crawl.crawl_site(None, None, {
                        "url": "https://example.org/docs/", "respectRobots": False,
                        "maxPages": max_pages, "maxRequests": max_requests,
                    }, target_path=root)
                self.assertEqual(result["pages"], saved)
                self.assertEqual(result["stopReason"], reason)
                self.assertEqual(result["remaining"], remaining)
                self.assertEqual(result["maxPages"], max_pages)
                self.assertEqual(result["maxRequests"], max_requests)

    async def test_file_and_directory_starts_follow_siblings_and_keep_scope(self):
        from unittest.mock import patch
        for start_path in ("/docs/intro.html", "/docs/", "/docs"):
            start = "https://example.org" + start_path
            pages = {
                start: '<p>Start</p><a href="/docs/setup.html">Setup</a>',
                "https://example.org/docs/setup.html": '<link rel="canonical" href="canonical.html"><p>Setup</p>'
                    '<a href="nested/page">Nested</a><a href="/outside">Outside</a>'
                    '<a href="/docs-private/leak">Leak</a><a href="https://other.org/docs/page">Other</a>',
                "https://example.org/docs/nested/page": '<p>Nested</p><a href="/docs/finish.html">Finish</a>',
                "https://example.org/docs/finish.html": '<p>Finish</p>',
            }
            requested = []

            class Response:
                status = 200
                headers = {"Content-Type": "text/html"}
                def __init__(self, url): self.url = url
                async def __aenter__(self): return self
                async def __aexit__(self, *args): pass
                async def text(self, **kwargs): return pages[self.url]

            class Session:
                async def __aenter__(self): return self
                async def __aexit__(self, *args): pass
                def get(self, url, **kwargs):
                    requested.append(url)
                    return Response(url)

            with self.subTest(start=start), tempfile.TemporaryDirectory() as root:
                with patch("aiohttp.ClientSession", return_value=Session()):
                    result = await crawl.crawl_site(None, None, {
                        "url": start, "respectRobots": False,
                    }, target_path=root)
                self.assertCountEqual(requested, pages)
                self.assertEqual(result["pages"], 4)
                self.assertEqual(result["stopReason"], "complete")
                self.assertEqual(result["remaining"], 0)
                canonical = pathlib.Path(root, "docs/canonical.md").read_text()
                self.assertIn('https://example.org/docs/canonical.html', canonical)


if __name__ == "__main__":
    unittest.main(verbosity=2)
