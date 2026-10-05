# Sharing options

The core UI owns the top-bar Share icon and its tabbed top panel. Extensions register options through
`ctx.setShareOptions()`. It supports one provider, several providers, or none. Without registrations,
the Share icon is not registered. Removing the last option also closes the panel.

```javascript
ctx.setShareOptions({
    my_share: { name: 'My host', order: 50, component: MySharePanel },
})
```

Each key is a stable option ID. A definition needs a Vue `component`; `name` defaults to a humanized
ID, and `order` defaults to 100. Lower orders appear first and determine the default selected tab.
Equal orders retain registration order. Optional `props` are passed to the component, and
`isVisible(ctx)` can filter it for the current context. Re-registering an ID updates its definition;
`ctx.setShareOptions({ my_share: null })` unregisters it. Extension scopes forward the same API.
Inactive tab components unmount. Tabs support arrow keys, Home and End.

- `share_static` registers **Folder** at order 10. It exports projects locally, without an account.
  Defaults are enabled, `directory: './p'`, `basePath: '/p/'`, `baseUrl: ''`.
  Global overrides live in `user/default/share_static/config.json` under the host's data root.
  See [static sharing](../llms/extensions/share_static/README.md).
- `share_llmspy` registers **ai.llmspy.org** at order 100. It shares projects, threads and media on
  the public host and supplies the publisher account used by Jev sharing. Per-user grants live in
  `user/<username>/share_llmspy/config.json`; legacy `publish/config.json` grants migrate on save.
  See [llmspy sharing](../llms/extensions/share_llmspy/README.md).

Enable or disable them independently through `disable_extensions`. For no sharing:

```json
{"disable_extensions": ["share_static", "share_llmspy"]}
```

The old `publish` extension and top-level `staticPublish` section have been replaced. Move static
settings into the extension's global file and update any old `publish` disable entry to the desired
new extension IDs. The `publish` field on projects remains their relative build/output path.

C# AI.Chat uses the same shared UI and API contracts. Configure independent extensions through
`ChatFeature.ShareStatic` and `ChatFeature.ShareLlmspy`; a typed `ShareStatic.StaticPublish` override
wins over the global JSON file. Use `DisableExtensions` with the same IDs.
