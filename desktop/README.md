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

Without Apple credentials, release CI signs the macOS bundle ad hoc (`signingIdentity: "-"`).
This is not Developer ID signing or notarization, so downloaded apps still need explicit approval
in **System Settings → Privacy & Security → Open Anyway**. To distribute apps that pass Gatekeeper
without that override, configure the Apple signing and notarization secrets below.

If macOS reports an installed app as damaged, inspect it with:

```bash
codesign --verify --deep --strict --verbose=2 /Applications/llms.app
spctl --assess --type execute --verbose=4 /Applications/llms.app
```

For an unnotarized build you downloaded from this repository and trust, removing quarantine from
that app alone can allow local testing:

```bash
xattr -dr com.apple.quarantine /Applications/llms.app
```

This removes the download quarantine check; it does not repair an invalid code signature or
notarize the app. Do not disable Gatekeeper globally.

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

### Local Developer ID signing and notarization

Install the **Developer ID Application** certificate and its matching private key in your
login keychain. `security find-identity -v -p codesigning` must list the identity. If Keychain
Access marks a G2-issued certificate as untrusted, install **Developer ID – G2** from
[Apple's certificate authority page](https://www.apple.com/certificateauthority/) using the
normal system trust defaults.

From the repository root, build a signed app and DMG with the installed identity:

```sh
export APPLE_SIGNING_IDENTITY='Developer ID Application: ServiceStack, Inc. (N546HR88H9)'
export APPLE_TEAM_ID='N546HR88H9'
desktop/.venv/bin/python desktop/scripts/build-desktop.py --bundles app,dmg
```

PyInstaller signs the frozen Python executable and its native libraries with that same
identity, including Hardened Runtime and secure timestamps. Tauri then signs the shell
and bundle. The sidecar build seals collected frameworks, and the macOS configuration
copies the resource directory with symbolic links preserved so those seals remain valid.
Signing only the outer app leaves the Python resources ad hoc signed and
is insufficient for notarization. In release CI, set `APPLE_SIGNING_IDENTITY` along with
the certificate secrets; the workflow imports the certificate before building Python.
The CI sidecar build uses `--keep-build` to preserve the generated release overlay.

To notarize, create an **app-specific password** at
[Apple Account](https://account.apple.com/) under **Sign-In and Security → App-Specific
Passwords**. Then run:

```sh
desktop/.venv/bin/python desktop/scripts/build-desktop.py --bundles app,dmg --notarize
```

The script prompts for your Apple Account email and app-specific password locally; password
entry is hidden and it is passed to Tauri in the process environment without saving it to
source files. Use the generated app-specific password, not your normal Apple Account password.
Tauri submits the signed app to Apple and staples its notarization ticket before packaging
the DMG. The first notarization can take longer while Apple evaluates the new developer.
Find the bundles under `desktop/src-tauri/target/release/bundle/` (or the equivalent target
directory if `CARGO_TARGET_DIR` is configured).

Verify the resulting app before distributing it:

```sh
codesign --verify --deep --strict --verbose=2 desktop/src-tauri/target/release/bundle/macos/llms.app
xcrun stapler validate desktop/src-tauri/target/release/bundle/macos/llms.app
spctl --assess --type execute --verbose=4 desktop/src-tauri/target/release/bundle/macos/llms.app
```

For GitHub releases, export the signing identity (certificate and private key together) as a
password-protected `.p12` from Keychain Access. Base64-encode it with
`openssl base64 -A -in /secure/path/certificate.p12 -out /secure/path/certificate-base64.txt`.
Store that file's contents as `APPLE_CERTIFICATE`, the export password as
`APPLE_CERTIFICATE_PASSWORD`, the identity above as `APPLE_SIGNING_IDENTITY`, and the
app-specific password as `APPLE_PASSWORD`. `APPLE_ID` is the Apple Account email and
`APPLE_TEAM_ID` is `N546HR88H9`. Add these to the repository's `desktop-release` environment
secrets. Keep exported private keys and password files outside the repository.

To upload the certificate and authentication secrets with GitHub CLI without putting
passwords in shell history, run this in your local terminal:

```sh
desktop/.venv/bin/python desktop/scripts/configure-apple-secrets.py --certificate /secure/path/certificate.p12
```

It prompts for the Apple Account email and both passwords, with password entry hidden,
then uploads `APPLE_CERTIFICATE`, `APPLE_CERTIFICATE_PASSWORD`, `APPLE_ID`, and
`APPLE_PASSWORD` to the `ServiceStack/llms` repository's `desktop-release` environment.
The `.p12` must contain both the certificate and its matching private key. Set
`APPLE_SIGNING_IDENTITY` and `APPLE_TEAM_ID` separately as listed above.

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
