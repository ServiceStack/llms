# llms.py Desktop

This directory contains the complete, additive desktop distribution. The regular `llms-py` package does not import it, depend on it, or need to know that it exists.

The desktop app is a small Tauri 2 shell that starts a private, frozen Python sidecar and displays the existing llms.py web UI in the operating system WebView. The backend listens only on `127.0.0.1:18000`; a random per-process token protects every route except the readiness check.

## End-user requirements

People installing a release do **not** need Python, Rust, Node.js, Docker, or a separate llms.py installation. Python 3.11 and the normal package files are frozen into the application with PyInstaller.

The operating system WebView is used instead of bundling Chromium:

- macOS uses the WebKit already included with macOS 11 or newer.
- Linux needs WebKitGTK 4.1. The `.deb` declares its system dependencies; AppImage compatibility depends on the target distribution.
- Windows uses WebView2. The NSIS installer installs the WebView2 runtime if it is missing.

Provider API keys and llms.py data remain in the existing browser-backed storage. Optional extension tools such as `git`, `uv`, `ffmpeg`, `typst`, `dotnet`, and `bun` are detected from the GUI application `PATH`; they are not bundled. Optional SDK-backed extensions such as FastMCP, the Google GenAI SDK, and DDGS are not part of the base runtime. Extensions that install or launch arbitrary Python packages may still require an external Python/uv environment.

## Isolation boundary

`python/entrypoint.py` is the only Python program launched by the desktop bundle. It installs `python/desktop_runtime.py` as the process-local replacement for `aiohttp.web.run_app`, then calls the unchanged `llms.main.main()`.

That runner seam receives the fully configured aiohttp application while keeping desktop behavior out of `llms/main.py`. It owns loopback binding, authentication, readiness, capabilities, graceful shutdown, and signal handling. There is intentionally no desktop flag, import, or optional dependency in the published package.

On quit, backend cleanup runs on a worker thread while the desktop event loop stays responsive.
The desktop server gives active requests one second to drain before cancelling long-lived streams
and running extension cleanup. The shell retains its eight-second graceful-exit budget and terminates
the backend if it still fails to exit; repeated quit requests do not start duplicate cleanup workers.
On macOS, menu Quit and ⌘Q request graceful shutdown through Tauri. Native termination paths
such as Dock Quit also reap the backend before the shell exits, preventing an occupied port on relaunch.
The application bundle is named `llms.app`.

The desktop loading screen inlines the UI's loading sprite, synchronized from `llms/index.html`
by the Tauri build. The UI's `localStorage['color-scheme']` remains authoritative and is mirrored to
`preferences.json` in Tauri's application config directory through an authenticated desktop-only
endpoint. The shell reads that cache before creating the window so its background and loader match
the saved theme; absent or invalid preferences fall back to the OS theme.

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

`desktop-ci.yml` builds native artifacts on macOS, Linux, and Windows in GitHub-hosted runners. Publishing a normal `v<version>` GitHub release automatically runs `desktop-release.yml` and attaches installers to that existing release. Each runner freezes its own Python runtime; no cross-compilation is needed.

| Platform | Architecture | Release files |
| --- | --- | --- |
| Linux | x86_64 | `.deb`, `.AppImage` |
| macOS | Apple Silicon (arm64), Intel (x86_64) | `.dmg`, `.app` archive |
| Windows | x86_64 | NSIS setup `.exe` |

The existing `desktop-v<version>` tag flow also builds all platforms and creates a separate draft desktop release. Publishing that draft does not build the installers a second time. The release tag must match the package and desktop versions.

Production macOS releases should configure these repository secrets:

- `APPLE_CERTIFICATE` and `APPLE_CERTIFICATE_PASSWORD`
- `APPLE_SIGNING_IDENTITY`
- `APPLE_ID`, `APPLE_PASSWORD`, and `APPLE_TEAM_ID` for notarization

The release job uses the `desktop-release` GitHub environment so approval and secrets can be managed separately from normal Python publishing. If that environment requires approval, the installer jobs wait for it. Linux and Windows bundles do not require Apple secrets. macOS signing and notarization are optional for generating installers; configure the Apple secrets for production distribution. Unset signing secrets are omitted from the bundler environment so macOS builds do not try to import an empty certificate. Windows Authenticode signing can be configured through Tauri's Windows signing options.

In-app updates are checked from the native application menu and use Tauri's signed updater artifacts. Generate the updater key pair once with `cargo tauri signer generate -w /secure/location/llms-desktop.key`, back up the private key, then configure:

- `TAURI_UPDATER_PUBLIC_KEY` with the generated public key
- `TAURI_SIGNING_PRIVATE_KEY` with the private key or its file contents
- `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` with its password

The updater private key must never be committed. When both updater keys are configured, release CI generates an ignored Tauri configuration overlay, signs the updater archives, and publishes `latest.json` beside the release artifacts. With neither key configured, it still publishes installers, with in-app updates disabled. A partially configured key pair fails explicitly. Updater signatures are separate from Apple and Windows code signing. A local signed release build can use `desktop/scripts/build-desktop.py --release-updater` with the same environment variables.

## Versioning

The desktop release uses the llms-py version. `python publish.py --bump` updates the Python version, `desktop/src-tauri/Cargo.toml`, the desktop package entry in `desktop/src-tauri/Cargo.lock`, and `desktop/src-tauri/tauri.conf.json` together. For manual version changes, keep these files aligned; `scripts/check-version.py` enforces that invariant in local and CI builds.

## Windows builds

Build on Windows with Python 3.11, Rust, and the Tauri CLI installed, then run `python desktop/scripts/build-desktop.py --bundles nsis` from the repository root. Do not cross-compile the Python sidecar: each architecture must be built on its target operating system.
