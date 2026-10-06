# Static Folder Sharing

`share_static` adds the **Folder** option to the core Share panel. It publishes project output
without a publisher account, API key or internet connection, independently of `share_llmspy`.
Disable it with `"disable_extensions": ["share_static"]` in your llms configuration. Disable both
sharing extensions to omit the Share icon entirely.

Open a project's chat or select a project, open **Share**, choose its build directory, and click
**Publish folder**. Leave the directory empty to export the project root. The saved `publish` path
or a detected `dist/` folder is pre-filled, and **Browse** selects another output folder.

The default export is `./p/<local-user>/<project-folder>/`. The anonymous local account uses
`default`. Relative destinations resolve against the working directory where llms started.

Configure the extension globally in `~/.llms/user/default/share_static/config.json` (or the equivalent
under your configured data root), then restart the host. With no file, these defaults apply:

```json
{
    "enabled": true,
    "directory": "./p",
    "basePath": "/p/",
    "baseUrl": ""
}
```

Set `enabled` to `false` to remove this sharing option. Settings belong to the host, not the signed-in
user: named-user account configuration and remote account actions cannot override them.

`directory` accepts absolute paths or paths relative to the startup working directory. `basePath`
is the static server's URL mount path. Set `baseUrl` to a public HTTP(S) URL including its mount,
for example `https://example.com/p` or `http://127.0.0.1:8080/p`. Its path determines the exported HTML
base path. A configured URL yields a project link opening in a new window. Empty or null uses
`basePath` and shows the URL path instead.

After publication the panel shows `Published 15m ago to ~/user/project`, followed by the link
or URL path. The timestamp's title contains the full date and time. **Update folder** replaces the
export and removes obsolete files. Copy/rewrite/metadata failures restore the previous export.
Source files remain unchanged; links/junctions, traversal, overlapping paths and unrelated existing
destinations are rejected. Hidden files and folders, whose names start with `.`, such as `.git`,
`.env` and `.vscode`, are never published, so a static server that doesn't hide them can't serve a
project's repository, secrets or tool settings. `staticPublication` metadata survives project edits and remains separate
from the remote publication link.

For example, run llms from `/srv/www` and serve that directory independently:

```sh
python -m http.server 8080 --directory /srv/www
```

Then open `/p/alice/my-project/` on that server. It must serve directory `index.html` files and
trailing-slash URLs. llms can stop once publishing finishes; the export contains no llms service.

The copied root `index.html` gets a missing `<base href="/p/alice/my-project/">`, and root-relative
HTML `src`/`href` attributes become relative. Existing base elements, scripts, comments and external
or protocol-relative links are preserved. Absolute paths inside CSS/JavaScript and SPA history
routing still need a build configured for the chosen mount path.
