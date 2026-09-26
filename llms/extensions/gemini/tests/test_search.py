"""Contract tests for local Search indexing and the self-contained published widget."""

import importlib.util
import os
import pathlib
import re
import tempfile
import threading
import time
import unittest


ROOT = pathlib.Path(__file__).resolve().parent.parent


def load(name):
    spec = importlib.util.spec_from_file_location(f"gemini_search_{name}_tests", ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


search = load("search")
try:
    import llms.db  # noqa: F401
    search_db = load("db")
except ImportError:
    search_db = None


class SearchTests(unittest.TestCase):
    def test_analytics_privacy_defaults_and_bot_detection(self):
        analytics = search.normalize_config()["analytics"]
        self.assertEqual(analytics["retentionDays"], 90)
        self.assertTrue(analytics["anonymizeIp"])
        self.assertTrue(analytics["respectDoNotTrack"])
        self.assertTrue(analytics["excludeBots"])
        self.assertFalse(analytics["requireConsent"])
        self.assertTrue(search.is_bot("Mozilla/5.0 compatible; Googlebot/2.1"))
        self.assertFalse(search.is_bot("Mozilla/5.0 Chrome/140 Safari/537.36"))
        self.assertIn("gptbot", analytics["deniedUserAgents"])
        self.assertEqual(analytics["deniedIpRanges"], [])
        self.assertEqual(analytics["excludedPaths"], [])

    def test_normalizes_and_applies_analytics_exclusions(self):
        analytics = search.normalize_config({"analytics": {
            "deniedUserAgents": [" GPTBot ", "gptbot", "Custom Monitor"],
            "deniedIpRanges": ["114.119.*", "203.0.113.9", "2001:db8:1234::/48", "invalid"],
            "excludedPaths": ["admin/*", "/health", "https://example.org/preview/*"],
        }})["analytics"]
        self.assertEqual(analytics["deniedUserAgents"], ["gptbot", "custom monitor"])
        self.assertEqual(analytics["deniedIpRanges"],
                         ["114.119.0.0/16", "203.0.113.9", "2001:db8:1234::/48"])
        self.assertEqual(analytics["excludedPaths"], ["/admin/*", "/health", "/preview/*"])
        self.assertTrue(search.is_denied_user_agent("Mozilla/5.0 GPTBot/1.2",
                                                    analytics["deniedUserAgents"]))
        self.assertFalse(search.is_denied_user_agent("Mozilla/5.0 Safari/605.1",
                                                     analytics["deniedUserAgents"]))
        self.assertTrue(search.is_denied_ip("114.119.42.8", analytics["deniedIpRanges"]))
        self.assertFalse(search.is_denied_ip("114.120.42.8", analytics["deniedIpRanges"]))
        self.assertTrue(search.is_denied_ip("203.0.113.9", analytics["deniedIpRanges"]))
        self.assertTrue(search.is_denied_ip("2001:db8:1234::99", analytics["deniedIpRanges"]))
        self.assertFalse(search.is_denied_ip("2001:db8:1235::99", analytics["deniedIpRanges"]))
        self.assertTrue(search.is_excluded_path("https://example.org/admin/users?active=1",
                                                analytics["excludedPaths"]))
        self.assertTrue(search.is_excluded_path("/health?full=true", analytics["excludedPaths"]))
        self.assertFalse(search.is_excluded_path("/docs/admin/start", analytics["excludedPaths"]))

    def test_portable_ranking_prefers_fields_freshness_and_document_types(self):
        now = int(time.time())
        rows = [
            {"id": 1, "documentId": 1, "documentTitle": "Search tuning", "heading": "Other", "content": "search tuning", "docType": "archive"},
            {"id": 2, "documentId": 2, "documentTitle": "Release", "heading": "Other", "content": "search tuning", "docType": "guide"},
        ]
        documents = {
            1: {"sourceUpdatedAt": now - 86400 * 365 * 5, "docType": "archive"},
            2: {"sourceUpdatedAt": now, "docType": "guide"},
        }
        ranked = search.rank_results(rows, "search tuning", documents)
        self.assertEqual(ranked[0]["documentId"], 2, "fresh pages should substantially outrank stale pages")
        ranked = search.rank_results(rows, "search tuning", documents, {
            "freshnessWeight": 0, "docTypeWeights": {"archive": 20}, "nativeWeight": 0,
        })
        self.assertEqual(ranked[0]["documentId"], 1, "docType boosts should be able to change the order")

    def test_search_intent_keys_group_related_wording(self):
        self.assertEqual(search.normalize_search_query("  Café   Configuration! "),
                         "cafe configuration")
        self.assertEqual(search.search_query_group_key("How to configure OrmLite"),
                         search.search_query_group_key("OrmLite configuration"))
        self.assertNotEqual(search.search_query_group_key("OrmLite configuration"),
                            search.search_query_group_key("Redis configuration"))

    def test_config_is_bounded_and_public_script_config_contains_the_endpoint(self):
        config = search.normalize_config({
            "identity": {"placeholder": " Find it "},
            "behavior": {"maxResults": 999, "minChars": 0},
            "ranking": {"titleWeight": 999, "freshnessHalfLifeDays": 0,
                        "docTypeWeights": {"guide": 7, "ignore": 0}},
            "appearance": {
                "accent": "#ff0000", "highlightColor": "#12abEF", "width": 9999,
                "fontFamily": "Inter;{}", "position": "top-left",
                "offset": {"top": -5, "right": 999},
            },
            "hosting": {"allowedOrigins": "https://docs.example\nhttps://help.example"},
            "analytics": {"enabled": True},
            "scope": {"category": "docs", "unknown": "hidden"},
        })
        self.assertEqual(config["identity"]["placeholder"], "Find it")
        self.assertEqual(config["behavior"]["maxResults"], 100)
        self.assertEqual(config["behavior"]["minChars"], 1)
        self.assertEqual(config["ranking"]["titleWeight"], 50)
        self.assertEqual(config["ranking"]["freshnessHalfLifeDays"], 1)
        self.assertEqual(config["ranking"]["docTypeWeights"], {"guide": 7.0})
        self.assertTrue(config["behavior"]["commandKShortcut"])
        self.assertTrue(config["behavior"]["slashShortcut"])
        self.assertTrue(config["analytics"]["enabled"])
        self.assertFalse(search.normalize_config()["analytics"]["enabled"])
        legacy = search.normalize_config({"behavior": {"keyboardShortcut": False}})
        self.assertFalse(legacy["behavior"]["commandKShortcut"])
        self.assertFalse(legacy["behavior"]["slashShortcut"])
        self.assertNotIn("accent", config["appearance"])
        self.assertEqual(config["appearance"]["highlightColor"], "#12abEF")
        self.assertEqual(search.normalize_config({"appearance": {"highlightColor": "blue"}})
                         ["appearance"]["highlightColor"], "")
        self.assertEqual(config["appearance"]["width"], 900)
        self.assertEqual(config["appearance"]["fontFamily"], "Inter")
        self.assertEqual(config["appearance"]["position"], "top-left")
        self.assertEqual(config["appearance"]["launcherStyle"], "flat")
        self.assertEqual(config["appearance"]["offset"], {"top": 0, "right": 400, "bottom": 20, "left": 20})
        invalid_position = search.normalize_config({"appearance": {"position": "center"}})
        self.assertEqual(invalid_position["appearance"]["position"], "bottom-right")
        self.assertEqual(config["scope"], {"category": "docs"})
        public = search.public_config({"publicId": "docs", "config": config}, "https://host.example")
        self.assertEqual(public["searchUrl"], "https://host.example/ext/gemini/public/searches/docs/results")
        self.assertEqual(public["clickUrl"], "https://host.example/ext/gemini/public/searches/docs/clicks")
        self.assertEqual(public["analyticsUrl"], "https://host.example/ext/gemini/public/searches/docs/pageviews")
        self.assertTrue(public["analyticsEnabled"])
        self.assertNotIn("deniedUserAgents", public["analytics"])
        self.assertNotIn("deniedIpRanges", public["analytics"])
        self.assertNotIn("excludedPaths", public["analytics"])
        self.assertNotIn("scope", public)
        self.assertNotIn("hosting", public)

    def test_launcher_style_defaults_to_flat_and_drives_the_widget_shadow(self):
        for style in ("raised", "flat", "inset"):
            self.assertEqual(search.normalize_config({"appearance": {"launcherStyle": style}})
                             ["appearance"]["launcherStyle"], style)
        self.assertEqual(search.normalize_config({"appearance": {"launcherStyle": "sunken"}})
                         ["appearance"]["launcherStyle"], "flat")
        source = (ROOT / "ui" / "search-widget.js").read_text(encoding="utf-8")
        self.assertIn("box-shadow:var(--launcher-shadow)", source)
        self.assertIn("box-shadow:var(--launcher-shadow-hover)", source)
        self.assertIn("transform:translateY(var(--launcher-lift))", source)
        self.assertIn("appearance.launcherStyle", source)
        self.assertNotIn("box-shadow:0 8px 24px rgba(15,23,42,.14)", source)

    def test_launcher_tooltip_is_opt_in_and_themed_in_the_shadow_root(self):
        self.assertEqual(search.normalize_config()["identity"]["tooltip"], "")
        self.assertEqual(search.normalize_config({"identity": {"tooltip": "  Search the docs  "}})
                         ["identity"]["tooltip"], "Search the docs")
        self.assertEqual(search.public_config(
            {"publicId": "p", "config": {"identity": {"tooltip": "Search the docs"}}},
            "https://host.example")["tooltip"], "Search the docs")
        source = (ROOT / "ui" / "search-widget.js").read_text(encoding="utf-8")
        self.assertIn("const tooltipText = String(CONFIG.tooltip || '').trim()", source)
        self.assertIn("launcher.setAttribute('aria-describedby', tooltip.id)", source)
        # never the browser's own title tooltip
        self.assertNotIn("launcher.title", source)
        # the wrapper is a containing block in both mounts so the tooltip tracks the launcher
        self.assertIn(".launcher-wrap{position:fixed", source)
        self.assertIn(":host([data-inline]) .launcher-wrap{position:relative", source)
        self.assertIn("launcherShadow.append(launcherWrap)", source)

    def test_hover_highlights_a_row_only_while_the_pointer_is_over_it(self):
        source = (ROOT / "ui" / "search-widget.js").read_text(encoding="utf-8")
        self.assertIn("function choose(index, hover = false)", source)
        self.assertIn("row.addEventListener('mouseenter', () => choose(index, true))", source)
        self.assertIn("if (hoverSelected && selected === index) choose(-1, true)", source)
        # a hover never scrolls the list; only the keyboard cursor does
        self.assertIn("if (!hover) shadow.querySelectorAll('.result')[selected]?.scrollIntoView", source)

    def test_inline_mount_keeps_the_dialogs_overlaying_from_the_document_body(self):
        self.assertEqual(search.normalize_config()["appearance"]["mount"], "")
        self.assertEqual(search.normalize_config({"appearance": {"mount": "  #search-slot  "}})
                         ["appearance"]["mount"], "#search-slot")
        self.assertEqual(search.normalize_config({"appearance": {"mount": "#nav</style><script>a{}"}})
                         ["appearance"]["mount"], "#nav/stylescripta")
        source = (ROOT / "ui" / "search-widget.js").read_text(encoding="utf-8")
        self.assertIn("'mount' in overrides ? overrides.mount : appearance.mount", source)
        self.assertIn("dataset.geminiSearchLauncher", source)
        self.assertIn(":host([data-inline]) .launcher-wrap{position:relative", source)
        # the dialogs always overlay from the body so an inline launcher cannot trap them
        self.assertIn("document.body.appendChild(host)", source)

    def test_sections_retain_heading_hierarchy_and_unique_anchors(self):
        rows = search.split_sections(
            "# Testing\n\nIntro text.\n\n## Integration tests\n\nFirst body.\n\n"
            "## Integration tests\n\nSecond body.",
            {"displayName": "Guide.md", "sourceUrl": "https://docs.example/guide"},
        )
        self.assertEqual(rows[0]["heading"], "Testing")
        self.assertEqual(rows[0]["kind"], "heading")
        bodies = [row for row in rows if row["content"]]
        self.assertEqual(bodies[1]["hierarchy"], ["Testing", "Integration tests"])
        self.assertEqual(bodies[1]["url"], "https://docs.example/guide#integration-tests")
        self.assertEqual(bodies[2]["url"], "https://docs.example/guide#integration-tests-1")

    def test_sections_strip_layout_html_code_fences_and_container_directives(self):
        rows = search.split_sections(
            '<div class="not-prose hide-title"><h1 class="title">\nAutoQuery '
            '<span>Home</span>\n</h1>'
            '<p>Build <strong>typed</strong> APIs &amp; clients.</p></div>\n\n'
            ':::{.shadow .rounded-md}\n![Banner](/banner.webp)\n:::\n\n'
            '```html\n<div class="sample">Example</div>\n```',
            {"displayName": "autoquery.md", "sourceUrl": "https://docs.example/autoquery"},
        )
        self.assertEqual(rows[0]["heading"], "AutoQuery Home")
        content = " ".join(row["content"] for row in rows)
        self.assertIn("Build typed APIs & clients.", content)
        self.assertNotIn("not-prose", content)
        self.assertNotIn(":::", content)
        self.assertNotIn('<div class="sample">Example</div>', content)

    def test_document_title_preserves_filename_underscores_and_prefers_frontmatter_title(self):
        document = {"displayName": "2025-10-15_ormlite-new-configuration.md"}
        rows = search.split_sections("Configuration details.", document)
        self.assertEqual(rows[0]["documentTitle"], "2025-10-15_ormlite-new-configuration")
        rows = search.split_sections("Configuration details.", document,
                                     document_title="New OrmLite Configuration")
        self.assertEqual(rows[0]["documentTitle"], "New OrmLite Configuration")

    def test_desired_hash_changes_when_search_scope_metadata_changes(self):
        document = {"contentHash": "same", "displayName": "Guide.md", "status": "draft"}
        before = search.desired_hash(document)
        document["status"] = "published"
        self.assertNotEqual(search.desired_hash(document), before)

    def test_widget_source_has_no_config_fetch_and_uses_shadow_dom(self):
        source = (ROOT / "ui" / "search-widget.js").read_text(encoding="utf-8")
        self.assertIn("attachShadow", source)
        self.assertIn("CONFIG.searchUrl", source)
        self.assertNotIn("/config", source)
        self.assertIn("document.body.appendChild(host)", source)
        self.assertIn("event.ctrlKey || event.metaKey", source)
        self.assertIn("event.key === '/'", source)
        self.assertIn("behavior.commandKShortcut", source)
        self.assertIn("behavior.slashShortcut", source)
        self.assertIn("const slashOnly = !commandKEnabled", source)
        self.assertIn(".launcher.slash-only{padding-right:14px}", source)
        self.assertIn(':host([data-position="top-left"])', source)
        self.assertIn("--assistant-offset", source)
        self.assertIn("new MutationObserver(syncLauncherPosition)", source)
        self.assertIn("localStorage.getItem('color-scheme')", source)
        self.assertIn("appearance.fontFamily", source)
        self.assertIn("appearance.offset?.[side]", source)
        self.assertNotIn("shortcutLabels.join", source)
        self.assertIn("localStorage.setItem(recentKey", source)
        self.assertIn(".remove-recent{flex:none;border:0;border-radius:7px;background:transparent;"
                      "color:color-mix(in srgb,var(--muted) 55%,transparent)", source)
        self.assertIn("renderRecent()", source)
        self.assertIn("previewUrl", source)
        self.assertIn("sanitizedMarkdown", source)
        self.assertIn("location.href = url.href", source)
        self.assertIn("event.stopPropagation()", source)
        self.assertIn("Back to search results", source)
        self.assertIn('<input type="text" autocomplete="off"', source)
        self.assertIn('<button class="esc" type="button"', source)
        self.assertIn("shadow.querySelector('.esc').addEventListener('click', close)", source)
        self.assertIn(".searchbar input:focus", source)
        self.assertIn("box-shadow:none", source)
        self.assertIn("font-weight:700", source)
        self.assertIn("text-decoration:var(--match-decoration)", source)
        self.assertIn("color:var(--highlight)", source)
        self.assertIn("appearance.highlightColor", source)
        self.assertIn('<article class="document-body" tabindex="-1">', source)
        self.assertIn("documentBody.focus({ preventScroll: true })", source)
        self.assertIn("&skip=", source)
        self.assertIn("results.addEventListener('scroll', maybeLoadMore", source)
        self.assertIn("Loading more…", source)
        self.assertIn("CONFIG.clickUrl", source)
        self.assertIn("CONFIG.analyticsEnabled === true", source)
        self.assertIn("CONFIG.analyticsUrl", source)
        self.assertIn("30 * 60 * 1000", source)
        self.assertIn("performance.getEntriesByType?.('navigation')", source)
        self.assertIn("utmCampaign:params.get('utm_campaign')", source)
        self.assertIn("searchEventId: activeSearchEventId", source)
        self.assertIn("keepalive: true", source)
        panel = (ROOT / "ui" / "searches.mjs").read_text(encoding="utf-8")
        self.assertIn("@keydown.esc.stop.prevent", panel)
        self.assertRegex(panel, r"setTimeout\(testSearch,\s*180\)")
        self.assertRegex(panel, r"window\.open\(url\.href,\s*'_blank'")
        self.assertIn('@click="openResult(item,group)"', panel)
        self.assertIn("hover:outline-2", panel)
        self.assertIn("focus-visible:outline-2", panel)
        self.assertIn("resultParts(item,'snippet')", panel)
        self.assertIn("resultParts(item,'title')", panel)
        self.assertIn("textDecoration: dark ? 'none' : 'underline'", panel)
        self.assertIn("Highlight color", panel)
        self.assertIn("resetHighlightColor", panel)
        self.assertIn("'#ffffff' : '#0ea5e9'", panel)
        self.assertIn("$fmt.markdown(documentPreview", panel)
        self.assertIn('@keydown.esc.stop.prevent="escapePreview"', panel)
        self.assertIn("if (handled) { event.preventDefault(); event.stopPropagation() }", panel)
        self.assertIn("previewInput.value?.focus()", panel)
        self.assertIn('ref="previewDocumentBody" tabindex="-1"', panel)
        self.assertIn("previewDocumentBody.value?.focus({ preventScroll: true })", panel)
        self.assertIn("window.open(url.href, '_blank', 'noopener,noreferrer')?.focus()", panel)
        self.assertIn('<input type="text" ref="previewInput"', panel)
        self.assertIn("focus:!border-0", panel)
        self.assertIn("focus:!ring-0", panel)
        self.assertIn('aria-label="Close search"', panel)
        self.assertIn("previewDialogStyle", panel)
        self.assertNotIn("appearance.accent", panel)
        self.assertIn("event.key === 'ArrowDown'", panel)
        self.assertIn("event.key === 'ArrowUp'", panel)
        self.assertIn("event.key === 'Enter'", panel)
        self.assertIn("scrollIntoView({ block: 'nearest' })", panel)
        self.assertIn("commandKShortcut", panel)
        self.assertIn("slashShortcut", panel)
        self.assertIn("paddingRight: slashOnly.value ? '14px' : '10px'", panel)
        self.assertIn("commandKEnabled.value ? (isMac ? '⌘K' : 'Ctrl K') : slashOnly.value ? '/' : ''", panel)
        self.assertIn("components: { CheckBox, DeleteSearchDialog }", panel)
        self.assertNotIn("View Searches", panel)
        self.assertIn(">New Search</button>", panel)
        self.assertIn(">Close Search</button>", panel)
        self.assertIn("const searchSections = computed", panel)
        self.assertIn("label: draft.value?.id ? 'Edit Search' : 'New Search'", panel)
        self.assertIn("label: 'Customer searches'", panel)
        self.assertIn("label: 'Website Analytics'", panel)
        self.assertIn("activeSection === 'searches'", panel)
        self.assertIn("activeSection === 'analytics'", panel)
        self.assertIn("Related searches by frequency", panel)
        self.assertIn("Latest searches", panel)
        self.assertIn("Popular documents", panel)
        self.assertIn("Search CTR", panel)
        self.assertIn("Capture Analytics", panel)
        self.assertIn("Disable Analytics", panel)
        self.assertIn("activeSection === 'analytics' && analyticsEnabled", panel)
        self.assertIn("lg:h-[32rem] lg:grid-cols-3", panel)
        self.assertIn('class="border-b p-3 last:border-b-0"', panel)
        self.assertIn("import { Chart, registerables } from 'chart.js'", panel)
        self.assertIn("export const colors = [", panel)
        self.assertIn("borderColor: colors[0].border", panel)
        self.assertIn("backgroundColor: colors[1].background", panel)
        self.assertEqual(panel.count("borderWidth: 1"), 2)
        self.assertIn("new Chart(trafficChart.value.getContext('2d')", panel)
        self.assertIn("Top pages", panel)
        self.assertIn("Top referrers", panel)
        self.assertIn("Recent visitors", panel)
        self.assertIn("IP address", panel)
        self.assertIn("const geographyGroups = computed", panel)
        self.assertIn("value.countries || []", panel)
        self.assertIn("searchAnalytics.traffic.recentPageViews", panel)
        self.assertIn('class="min-w-full border-separate border-spacing-0 text-left text-sm"', panel)
        self.assertIn('class="whitespace-nowrap border-b px-3 py-2 font-semibold" :class="$styles.chromeBorder"', panel)
        self.assertIn("{{item.ipAddress || ''}}", panel)
        self.assertNotIn("item.organization || '—'", panel)
        self.assertNotIn("|| 'Unresolved'", panel)
        self.assertIn("/analytics?groupTake=50&recentTake=100&period=", panel)
        self.assertIn("&visitorSkip=${visitorSkip}&visitorTake=${visitorPageSize}", panel)
        self.assertIn('@click="previousVisitors"', panel)
        self.assertIn('@click="nextVisitors"', panel)
        self.assertIn(':title="formatSearchDate(item.createdAt)">{{formatRelativeTime(item.createdAt)}}', panel)
        self.assertIn('class="grid grid-cols-2 gap-3 border-b p-5 sm:grid-cols-3 xl:grid-cols-6"', panel)
        self.assertNotIn('class="bg-white px-4 py-3 dark:bg-gray-950"', panel)
        self.assertEqual(panel.count("<CheckBox v-model="), 6)
        self.assertIn(">Deployment</h3>", panel)
        self.assertIn(">Unpublish</button>", panel)
        self.assertIn(">Regenerate ID</button>", panel)
        self.assertNotIn('<CheckBox v-model="draft.published"', panel)
        self.assertIn("localStorage.getItem('color-scheme')", panel)
        self.assertIn('v-model="draft.config.appearance.position"', panel)
        self.assertIn('v-model.number="draft.config.appearance.offset[side]"', panel)
        self.assertIn('id="search-font-family" type="text"', panel)
        self.assertIn("window.addEventListener('keydown', onKeydown, true)", panel)
        self.assertIn("Document scope", panel)
        self.assertIn("facetOptions(field)", panel)
        self.assertIn('<select v-model="draft.config.scope[field]"', panel)
        self.assertIn('@scroll.passive="onPreviewResultsScroll"', panel)
        self.assertIn("testSearch(true)", panel)
        self.assertIn("Results per page", panel)
        index = (ROOT / "ui" / "index.mjs").read_text(encoding="utf-8")
        self.assertIn('<SearchesPanel :storeId="storeId" :facets="facets"', index)
        for path in (ROOT / "ui").glob("*.mjs"):
            markup = path.read_text(encoding="utf-8")
            missing = re.findall(r"<input(?![^>]*\s:?type=)[^>]*>", markup, re.DOTALL)
            self.assertFalse(missing, f"{path.name} has input elements without a type: {missing}")
        assistant = (ROOT / "ui" / "assistant-widget.js").read_text(encoding="utf-8")
        self.assertIn("[data-gemini-search]", assistant)
        self.assertIn("event.shiftKey !== searchPresent", assistant)


class SearchWorkerTests(unittest.TestCase):
    class Context:
        def __init__(self):
            self.errors = []
        def err(self, *args):
            self.errors.append(args)

    class Queue:
        @staticmethod
        def join(): pass

    class InnerDb:
        def __init__(self):
            self.task_queue = SearchWorkerTests.Queue()
        @staticmethod
        def all(*_args, **_kwargs): return []

    class Db:
        def __init__(self, fail_first=False, block_first=False):
            self.db = SearchWorkerTests.InnerDb()
            self.fail_first = fail_first
            self.block_first = block_first
            self.reads = 0
            self.entered = threading.Event()
            self.release = threading.Event()
        def clone(self): return self
        def get_search_candidates(self, _take):
            self.reads += 1
            if self.block_first and self.reads == 1:
                self.entered.set()
                self.release.wait(2)
            if self.fail_first and self.reads == 1:
                raise RuntimeError("temporary database error")
            return []

    @staticmethod
    def wait_until(predicate, timeout=2):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(.01)
        return False

    def test_start_while_running_requests_another_queue_read(self):
        from llms.extensions.gemini.search_worker import SearchWorker
        db = self.Db(block_first=True)
        worker = SearchWorker(self.Context(), db)
        worker.start()
        self.assertTrue(db.entered.wait(1))
        worker.start()
        db.release.set()
        self.assertTrue(self.wait_until(lambda: not worker.running))
        self.assertGreaterEqual(db.reads, 2)

    def test_temporary_queue_read_failure_is_retried(self):
        from llms.extensions.gemini.search_worker import SearchWorker
        context, db = self.Context(), self.Db(fail_first=True)
        worker = SearchWorker(context, db)
        worker.retry_delay = .01
        worker.start()
        self.assertTrue(self.wait_until(lambda: not worker.running))
        self.assertGreaterEqual(db.reads, 2)
        self.assertTrue(any("retrying" in str(error) for error in context.errors))


@unittest.skipUnless(search_db, "llms.db is only available in the llms-py workspace")
class SearchPersistenceTests(unittest.TestCase):
    class Context:
        debug = False
        def dbg(self, *_): pass
        def log(self, *_): pass
        def err(self, *args): raise AssertionError(args)

    @staticmethod
    def insert(db, table, values):
        result = []
        db.db.insert(table, db.columns[table], values,
                     lambda lastrowid, _rowcount, error=None: result.append((lastrowid, error)))
        db.db.task_queue.join()
        if result[0][1]:
            raise result[0][1]
        return result[0][0]

    def test_fts_index_widget_lifecycle_and_document_delete_are_consistent(self):
        with tempfile.TemporaryDirectory() as root:
            db = search_db.GeminiDB(self.Context(), os.path.join(root, "gemini.sqlite"))
            self.addCleanup(db.db.close)
            store_id = self.insert(db, "filestore", db.prepare_filestore({
                "displayName": "Docs", "visibility": "public",
            }))
            document = {
                "filestoreId": store_id,
                "displayName": "Testing Guide.md",
                "sourceUrl": "https://docs.example/testing",
                "contentHash": "content-v1",
                "metadataHash": "meta-v1",
                "status": "published",
                "searchHash": "wanted",
            }
            document_id = self.insert(db, "document", db.prepare_document(document))
            document = db.get_document(document_id)
            sections = search.split_sections(
                "# Testing Guide\n\nHow to write unit and integration tests.\n\n"
                "## Integration example\n\nUse the test client to run this example.", document,
            )
            db.replace_search_sections(document, sections, "wanted")
            rows = db.search_sections(store_id, "integration", take=20)
            self.assertTrue(rows)
            self.assertTrue(any(row.get("score") != 0 for row in rows))
            self.assertTrue(any("integration" in (row.get("heading") or row.get("content") or "").lower()
                                for row in rows))
            first_page = db.search_sections(store_id, "integration", take=1)
            second_page = db.search_sections(store_id, "integration", take=1, skip=1)
            self.assertEqual(len(first_page), 1)
            self.assertEqual(len(second_page), 1)
            self.assertNotEqual(first_page[0]["id"], second_page[0]["id"])
            scoped = db.search_sections(store_id, "integration", scope={"status": "draft"})
            self.assertEqual(scoped, [])
            stats = db.search_stats(store_id)
            self.assertEqual(stats["documents"], 1)
            self.assertEqual(stats["indexed"], 1)
            self.assertGreater(stats["sections"], 0)
            self.assertEqual(stats["provider"], "sqlite-fts5")
            self.assertIsNone(db.get_document(document_id).get("searchStartedAt"))

            widget_id = self.insert(db, "search_widget", {
                "filestoreId": store_id, "name": "Docs Search", "publicId": "public-search",
                "enabled": 1, "publishedAt": "now", "config": {"identity": {"placeholder": "Find"}},
            })
            self.assertIsNotNone(db.get_public_search_widget("public-search"))
            db.db.update("search_widget", db.columns["search_widget"], {
                "id": widget_id, "enabled": 0, "publishedAt": None,
            })
            db.db.task_queue.join()
            self.assertIsNone(db.get_public_search_widget("public-search"))
            db.db.update("search_widget", db.columns["search_widget"], {
                "id": widget_id, "enabled": 1, "publishedAt": None,
            })
            db.db.task_queue.join()
            self.assertIsNone(db.get_search_widget(widget_id).get("publishedAt"))

            first_query_id = self.insert(db, "search_query", {
                "searchWidgetId": widget_id, "createdAt": search_db.datetime.now(),
                "query": "How to configure OrmLite", "normalizedQuery": "how to configure ormlite",
                "groupKey": search.search_query_group_key("How to configure OrmLite"),
                "origin": "https://docs.example", "pageUrl": "https://docs.example/ormlite",
                "resultCount": 4, "documentCount": 2, "durationMs": 12,
            })
            self.insert(db, "search_query", {
                "searchWidgetId": widget_id, "createdAt": search_db.datetime.now(),
                "query": "OrmLite configuration", "normalizedQuery": "ormlite configuration",
                "groupKey": search.search_query_group_key("OrmLite configuration"),
                "resultCount": 0, "documentCount": 0, "durationMs": 8,
            })
            first_section = db.db.one(
                "SELECT id FROM search_section WHERE documentId=:id ORDER BY id LIMIT 1",
                {"id": document_id})
            for position in (2, 4):
                self.insert(db, "search_click", {
                    "searchQueryId": first_query_id, "searchWidgetId": widget_id,
                    "documentId": document_id, "sectionId": first_section["id"],
                    "createdAt": search_db.datetime.now(), "position": position,
                    "documentTitle": "Testing Guide", "sourceUrl": "https://docs.example/testing",
                    "resultType": "content",
                })
            analytics = db.search_analytics(widget_id)
            self.assertEqual(analytics["total"], 2)
            self.assertEqual(analytics["uniqueQueries"], 2)
            self.assertEqual(analytics["relatedGroups"], 1)
            self.assertEqual(analytics["noResults"], 1)
            self.assertEqual(analytics["groups"][0]["count"], 2)
            self.assertEqual(analytics["groups"][0]["clickCount"], 2)
            self.assertEqual(analytics["groups"][0]["clickThroughRate"], 50.0)
            self.assertEqual(len(analytics["groups"][0]["variants"]), 2)
            self.assertEqual(analytics["totalClicks"], 2)
            self.assertEqual(analytics["clickedSearches"], 1)
            self.assertEqual(analytics["clickThroughRate"], 50.0)
            self.assertEqual(analytics["popularDocuments"][0]["documentId"], document_id)
            self.assertEqual(analytics["popularDocuments"][0]["averagePosition"], 3.0)
            self.assertEqual(db.query_search_widgets(store_id, include_archived=True)[0]["searchCount"], 2)

            for values in (
                {"clientId": "c1", "sessionId": "s1", "firstVisit": True,
                 "pageUrl": "https://docs.example/testing", "pagePath": "/testing",
                 "pageTitle": "Testing", "language": "en-AU", "timezone": "Australia/Perth",
                 "deviceType": "desktop", "platform": "Linux", "connectionType": "4g",
                 "loadMs": 120, "utmCampaign": "launch"},
                {"clientId": "c1", "sessionId": "s1",
                 "pageUrl": "https://docs.example/other", "pagePath": "/other",
                 "pageTitle": "Other", "language": "en-AU", "timezone": "Australia/Perth",
                 "deviceType": "desktop", "platform": "Linux", "loadMs": 80},
                {"clientId": "c2", "sessionId": "s2",
                 "pageUrl": "https://docs.example/testing", "pagePath": "/testing",
                 "pageTitle": "Testing", "referrer": "https://search.example/",
                 "language": "en-US", "timezone": "America/New_York",
                 "deviceType": "mobile", "platform": "Android", "loadMs": 100},
                {"clientId": "c1", "sessionId": "s1",
                 "pageUrl": "https://docs.example/testing", "pagePath": "/testing",
                 "pageTitle": "测试"},
            ):
                self.insert(db, "search_page_view", db.prepare_search_page_view(widget_id, values))
            traffic = db.search_traffic_analytics(widget_id, period="1d")
            paged_traffic = db.search_traffic_analytics(
                widget_id, period="1d", recent_skip=1, recent_take=1)
            self.assertEqual(traffic["pageViews"], 4)
            self.assertEqual(traffic["visitors"], 2)
            self.assertEqual(traffic["sessions"], 2)
            self.assertEqual(traffic["pagesPerSession"], 2.0)
            self.assertEqual(traffic["bounceRate"], 50.0)
            self.assertEqual(traffic["averageLoadMs"], 100)
            self.assertEqual(traffic["recentTotal"], 4)
            self.assertEqual(len(traffic["recentPageViews"]), 4)
            self.assertEqual(paged_traffic["recentSkip"], 1)
            self.assertEqual(paged_traffic["recentTake"], 1)
            self.assertEqual(len(paged_traffic["recentPageViews"]), 1)
            self.assertEqual(sum(x["pageViews"] for x in traffic["timeline"]), 4)
            self.assertEqual(traffic["topPages"][0]["path"], "/testing")
            self.assertEqual(traffic["topPages"][0]["title"], "Testing")
            self.assertEqual(traffic["topPages"][0]["views"], 3)
            self.assertEqual(db.query_search_widgets(store_id, include_archived=True)[0]["pageViewCount"], 4)

            db.delete_document(document_id)
            db.db.task_queue.join()
            self.assertEqual(db.db.scalar(
                "SELECT COUNT(*) FROM search_section WHERE documentId=?", (document_id,)), 0)
            self.assertEqual(db.db.scalar(
                "SELECT COUNT(*) FROM search_click WHERE documentId=?", (document_id,)), 2)
            cleared = db.clear_search_analytics(widget_id)
            self.assertEqual(cleared, {"clicks": 2, "pageViews": 4, "searches": 2})
            self.assertEqual(db.db.scalar(
                "SELECT COUNT(*) FROM search_query WHERE searchWidgetId=?", (widget_id,)), 0)
            db.delete_search_widget(widget_id, confirmation="Docs Search")
            self.assertEqual(db.db.scalar(
                "SELECT COUNT(*) FROM search_click WHERE searchWidgetId=?", (widget_id,)), 0)
            self.assertEqual(db.db.scalar(
                "SELECT COUNT(*) FROM search_page_view WHERE searchWidgetId=?", (widget_id,)), 0)


if __name__ == "__main__":
    unittest.main()
