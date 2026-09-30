# llms.py Desktop

This directory contains the complete, additive desktop distribution. The regular `llms-py` package does not import it, depend on it, or need to know that it exists.

The desktop app is a small Tauri 2 shell that starts a private, frozen Python sidecar and displays the existing llms.py web UI in the operating system WebView. The backend listens only on `127.0.0.1:18000`; a random per-process token protects every route except the readiness check.

## End-user requirements

People installing a release do **not** need Python, Rust, Node.js, Docker, or a separate llms.py installation. Python 3.11 and the normal package files are frozen into the application with PyInstaller.

The operating system WebView is used instead of bundling Chromium:

- macOS uses the WebKit already included with macOS 11 or newer.
- Linux needs WebKitGTK 4.1. The `.deb` declares its system dependencies; AppImage compatibility depends on the target distribution.
- Windows can be added later using WebView2. The supervisor, sidecar filename handling, build scripts, and CI layout are already Windows-aware.

Provider API keys and llms.py data remain in the existing browser-backed storage. Optional extension tools such as `git`, `uv`, `ffmpeg`, `typst`, `dotnet`, and `bun` are detected from the GUI application `PATH`; they are not bundled. Optional SDK-backed extensions such as FastMCP, the Google GenAI SDK, and DDGS are not part of the base runtime. Extensions that install or launch arbitrary Python packages may still require an external Python/uv environment.

## Isolation boundary

`python/entrypoint.py` is the only Python program launched by the desktop bundle. It installs `python/desktop_runtime.py` as the process-local replacement for `aiohttp.web.run_app`, then calls the unchanged `llms.main.main()`.

That runner seam receives the fully configured aiohttp application while keeping desktop behavior out of `llms/main.py`. It owns loopback binding, authentication, readiness, capabilities, graceful shutdown, and signal handling. There is intentionally no desktop flag, import, or optional dependency in the published package.

The PyInstaller spec copies the current `llms/` tree into a private onedir runtime. This is generated during every native build, so no duplicate source file needs to be maintained or committed.

## Build prerequisites

- Python 3.11 or newer
- Rust stable and Cargo
- Tauri CLI v2: `cargo install tauri-cli --version '^2' --locked`
- Platform build dependencies from the [Tauri prerequisites guide](https://v2.tauri.app/start/prerequisites/)

Create an isolated build environment from the repository root:

```sh
uv venv --python 3.11 desktop/.venv
uv pip install --python desktop/.venv/bin/python -r desktop/requirements-build.txt
```

Build and smoke-test only the frozen Python sidecar:

```sh
desktop/.venv/bin/python desktop/scripts/build-sidecar.py
desktop/.venv/bin/python desktop/scripts/verify-sidecar.py
```

Build a native bundle:

```sh
desktop/.venv/bin/python desktop/scripts/build-desktop.py
```

On macOS, `--bundles app` is useful for a fast local build and `--bundles app,dmg` creates release formats. On Linux use `--bundles deb,appimage`.

For development, build the sidecar once, then run `cargo tauri dev` from `desktop/`. The loading page remains visible until the backend emits a valid readiness event.

On Linux, the desktop window omits the native title bar and menu so the window manager controls its chrome. On systems with the NVIDIA kernel module loaded, the app defaults to `WEBKIT_DISABLE_DMABUF_RENDERER=1` to avoid WebKitGTK's Wayland protocol error. An explicitly supplied value takes precedence. If a driver still renders a blank window, try `WEBKIT_DISABLE_COMPOSITING_MODE=1 cargo tauri dev`; see [Tauri's Linux graphics troubleshooting](https://v2.tauri.app/develop/debug/linux-graphics/).

## Tests

```sh
python -m unittest discover -s desktop/tests -p 'test_*.py'
cargo test --manifest-path desktop/src-tauri/Cargo.toml
cargo clippy --manifest-path desktop/src-tauri/Cargo.toml --all-targets -- -D warnings
```

`verify-sidecar.py` is the integration test: it starts the frozen binary with a temporary home and random port, verifies the health endpoint, authenticates the WebView bootstrap, loads the existing UI, verifies that Python is frozen, and requests graceful shutdown.

## Releases and signing

`desktop-ci.yml` builds native artifacts on macOS and Linux in GitHub-hosted runners. `desktop-release.yml` runs for `desktop-v*` tags and publishes platform bundles through the Tauri GitHub Action.

Production macOS releases should configure these repository secrets:

- `APPLE_CERTIFICATE` and `APPLE_CERTIFICATE_PASSWORD`
- `APPLE_SIGNING_IDENTITY`
- `APPLE_ID`, `APPLE_PASSWORD`, and `APPLE_TEAM_ID` for notarization

The release job uses the protected `desktop-release` GitHub environment so approval and secrets can be managed separately from normal Python publishing. Linux bundles do not require these Apple secrets.

In-app updates are checked from the native application menu and use Tauri's signed updater artifacts. Generate the updater key pair once with `cargo tauri signer generate -w /secure/location/llms-desktop.key`, back up the private key, then configure:

- `TAURI_UPDATER_PUBLIC_KEY` with the generated public key
- `TAURI_SIGNING_PRIVATE_KEY` with the private key or its file contents
- `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` with its password

The updater private key must never be committed. Release CI generates an ignored Tauri configuration overlay from the public key, signs the updater archives, and publishes `latest.json` beside the release artifacts. A local signed release build can use `desktop/scripts/build-desktop.py --release-updater` with the same environment variables.

## Versioning

The desktop release uses the llms-py version. `python publish.py --bump` updates the Python version, `desktop/src-tauri/Cargo.toml`, the desktop package entry in `desktop/src-tauri/Cargo.lock`, and `desktop/src-tauri/tauri.conf.json` together. For manual version changes, keep these files aligned; `scripts/check-version.py` enforces that invariant in local and CI builds.

## Adding Windows

Add a native `windows-latest` release matrix entry, build the PyInstaller sidecar on that runner, enable the Tauri NSIS target, and configure a Windows signing certificate. Do not cross-compile the Python sidecar: each architecture must be built on its target operating system.
