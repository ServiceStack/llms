"""Opt-in real-component browser check; upstream decisions are mocked in the fixture."""

import argparse
import functools
import http.server
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=1100)
    parser.add_argument("--dark", action="store_true")
    parser.add_argument("--screenshot")
    parser.add_argument(
        "--view",
        choices=(
            "studio",
            "ai",
            "picker",
            "providers",
            "gemini",
            "images",
            "import",
            "editor",
            "notices",
            "examples",
            "example-name",
            "tags",
        ),
        default="studio",
    )
    parser.add_argument(
        "--fixture",
        choices=(
            "jev-studio",
            "jev-sharing",
            "jev-public-gallery",
            "jev-public-recipe",
            "jev-navigation",
            "model-picker",
        ),
        default="jev-studio",
    )
    parser.add_argument(
        "--publisher-root", default=str(Path(__file__).resolve().parents[2] / "ubixar.com/MyApp/wwwroot")
    )
    parser.add_argument("--disable-publish", action="store_true")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    chromium = shutil.which("chromium") or shutil.which("google-chrome")
    if not chromium:
        raise SystemExit("Chromium is required.")

    class Handler(http.server.SimpleHTTPRequestHandler):
        def translate_path(self, path):
            from urllib.parse import urlsplit

            route = urlsplit(path).path
            parts = Path(route).parts
            if args.disable_publish and route.startswith("/ext/share_llmspy/"):
                return str(repo / "tests/fixtures/disabled-publisher-not-found")
            if len(parts) >= 4 and parts[1] == "ext" and parts[2] in ("jev", "share_llmspy") and ".." not in parts:
                return str(repo / "llms/extensions" / parts[2] / "ui" / Path(*parts[3:]))
            if route.startswith("/ui/") and ".." not in parts:
                return str(repo / "llms/ui" / Path(*parts[2:]))

            assets = {
                "/publisher/recipes.mjs": "llms/recipes.mjs",
                "/publisher/recipe.mjs": "llms/recipe.mjs",
                "/publisher/recipeFormat.mjs": "llms/recipeFormat.mjs",
                "/publisher/PublicationPreview.mjs": "llms/PublicationPreview.mjs",
                "/publisher/RecipeStar.mjs": "llms/RecipeStar.mjs",
                "/publisher/RecordedAnswers.mjs": "llms/RecordedAnswers.mjs",
                "/publisher/decisionTags.mjs": "llms/decisionTags.mjs",
                "/publisher/recipes.css": "llms/recipes.css",
                "/publisher/register.html": "embed/register.html",
            }
            asset = assets.get(urlsplit(path).path)
            return str(Path(args.publisher_root) / asset) if asset else super().translate_path(path)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(repo)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix="jev-browser-") as profile:
            command = [
                chromium,
                "--headless",
                "--no-sandbox",
                "--disable-gpu",
                "--force-prefers-reduced-motion",
                f"--user-data-dir={profile}",
                f"--window-size={args.width},{args.height}",
                "--virtual-time-budget=60000",
                "--dump-dom",
            ]
            if args.screenshot:
                command.append("--screenshot=" + args.screenshot)
            query = "?" + "&".join(
                (["dark=1"] if args.dark else [])
                + (["disabled=1"] if args.disable_publish else [])
                + ([args.view + "=1"] if args.view != "studio" else [])
            )
            command.append(f"http://127.0.0.1:{server.server_port}/tests/fixtures/{args.fixture}.html" + query)
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        start = result.stdout.find('<pre id="result">')
        outcome = (
            result.stdout[start : result.stdout.find("</pre>", start) + 6] if start >= 0 else result.stderr[-2000:]
        )
        print(outcome)
        if not outcome.startswith('<pre id="result">PASS:'):
            Path("/tmp/jev-browser-failure.html").write_text(result.stdout)
        return 0 if outcome.startswith('<pre id="result">PASS:') else 1
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
