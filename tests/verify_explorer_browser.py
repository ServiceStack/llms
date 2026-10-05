"""Opt-in browser integration check: python tests/verify_explorer_browser.py."""
import functools
import argparse
import http.server
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixture', default='explorer-navigation.html', choices=['explorer-navigation.html', 'project-creation.html', 'project-organization.html', 'git-operations.html', 'project-publishing.html'])
    parser.add_argument('--screenshot')
    parser.add_argument('--dark', action='store_true')
    parser.add_argument('--width', type=int, default=1280)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    chromium = shutil.which('chromium') or shutil.which('google-chrome')
    if not chromium:
        raise SystemExit('Chromium is required for this browser check')

    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith('/fixture'):
                content = (repo / 'tests/fixtures' / args.fixture).read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.end_headers()
                self.wfile.write(content)
            else:
                if self.path.startswith('/ui/'):
                    self.path = '/llms' + self.path
                elif self.path.startswith('/ext/'):
                    parts = self.path.split('/', 3)
                    if len(parts) == 4 and parts[2] in ('app', 'share_static', 'share_llmspy'):
                        self.path = '/llms/extensions/' + parts[2] + '/ui/' + parts[3]
                super().do_GET()

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(repo)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='llms-explorer-browser-') as profile:
            command = [
                chromium, '--headless', '--no-sandbox', '--disable-gpu', f'--user-data-dir={profile}',
                f'--window-size={args.width},900', '--virtual-time-budget=15000', '--dump-dom',
            ]
            if args.screenshot:
                command.append('--screenshot=' + args.screenshot)
            command.append(f'http://127.0.0.1:{server.server_port}/fixture' + ('?dark=1' if args.dark else ''))
            result = subprocess.run(command, capture_output=True, text=True, timeout=25)
        start = result.stdout.find('<pre id="result">')
        outcome = result.stdout[start:result.stdout.find('</pre>', start) + 6] if start >= 0 else result.stderr[-2000:]
        print(outcome)
        return 0 if outcome.startswith('<pre id="result">PASS:') else 1
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    raise SystemExit(main())
