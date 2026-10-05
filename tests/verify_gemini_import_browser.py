"""Opt-in browser verification of the real saved import component with mocked APIs."""

import functools
import http.server
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading


def main():
    root = pathlib.Path(__file__).resolve().parents[1]
    fixture = pathlib.Path(sys.argv[1]).name if len(sys.argv) > 1 else "gemini-saved-import.html"
    chromium = shutil.which("chromium") or shutil.which("google-chrome")
    if not chromium:
        raise SystemExit("Chromium is required")

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix="gemini-import-browser-") as profile:
            result = subprocess.run([
                chromium, "--headless", "--no-sandbox", "--disable-gpu", f"--user-data-dir={profile}",
                "--window-size=1280,1100", "--virtual-time-budget=10000", "--dump-dom",
                f"--screenshot=/tmp/{pathlib.Path(fixture).stem}.png",
                f"http://127.0.0.1:{server.server_port}/tests/fixtures/{fixture}",
            ], capture_output=True, text=True, timeout=30)
        start = result.stdout.find('<pre id="result">')
        outcome = result.stdout[start:result.stdout.find("</pre>", start) + 6] if start >= 0 else result.stderr[-2000:]
        print(outcome)
        if not outcome.startswith('<pre id="result">PASS:'):
            pathlib.Path("/tmp/gemini-saved-import-failure.html").write_text(result.stdout)
            return 1
        return 0
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
