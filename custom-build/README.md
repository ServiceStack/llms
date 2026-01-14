# Custom llms .py Build

## Lean Core + Extensibility

To reduce bloat, **llms .py** was intentionally built around a lean core with support for extensibility. Most major features are encapsulated in **extension folders** that can be layered on to create a custom build with only the features you need.

### Benefits of This Approach

- **Minimal Footprint** - Deploy only what you need, reducing dependencies and attack surface
- **Faster Startup** - Fewer modules to load means quicker initialization times
- **Easier Maintenance** - Isolated extensions are simpler to update, debug, and replace
- **Flexible Deployment** - Create purpose-built distributions for different use cases (e.g., CLI-only, API server, full UI)
- **Cleaner Codebase** - Separation of concerns keeps the core focused and extensions self-contained

## Creating a Custom Build

This directory demonstrates how to create a custom build of the llms .py CLI/Server with a curated set of extensions. This allows you to deploy a lightweight version of the application with only the specific functionality you need.

## Custom Builder

Use `./build.sh` inside the [custom-build](https://github.com/ServiceStack/llms/tree/main/custom-build) directory to create a custom build of llms .py with only the extensions you want, where running `./build.sh` without any arguments:

```bash
cd custom-build

./build.sh
```

Generates a minimal custom build inside a new `llms` directory containing just the [providers extension](https://llmspy.org/docs/extensions/built-in#providers-extension) and necessary configuration files:

```
llms/
├── .llms/            # .llms/ home for this build
├── extensions/       # Extensions directory
│   └── providers/    # Providers extension
├── llms.json         # llms.json config
├── providers.json    # models.dev providers.json config
├── main.py           # llms.py implementation
├── llms.sh           # llms.sh startup script
```

This minimal build doesn't contain any UI or tools, but does contain the [providers extension](https://llmspy.org/docs/extensions/built-in#providers-extension) with support for all 24 supported providers and their 530+ models in both [CLI mode](https://llmspy.org/docs/features/cli) and its Open AI Compatible `/v1/chat/completions` endpoint.

### Startup Script

Once built you would use the `llms.sh` startup script for all commands instead of the `llms` command, e.g:

```bash
./llms.sh "What's the Capital of France?"
```

### Minimal UI Build

Set the `UI` environment variable to `1` for the minimal UI build which includes the [app](https://llmspy.org/docs/extensions/built-in#app-extension) and [tools](https://llmspy.org/docs/extensions/tools) extensions.

```bash
UI=1 ./build.sh
```

### Custom Extensions

For a custom build set the `EXTENSIONS` environment variable to a comma separated list of extensions to include in the build. E.g. this includes the same as as the Minimal UI build:

```bash
EXTENSIONS="providers,app,tools" ./build.sh
```

> Including any extension that requires a UI will automatically include it in the build.

### Add external extensions

With [full CLI support](https://llmspy.org/docs/features/cli), your custom build can use the [extension management commands](https://llmspy.org/docs/extensions) to manage installed extensions, e.g., you can add external extensions to your custom build by using the `llms.sh --add` command.

Preview available extensions:

```bash
./llms.sh --add
```

Add a specific extension:

```bash
./llms.sh --add xmas
```

## Built-in Extensions

You can copy any of these built-in extensions from the source [llms/extensions](https://github.com/ServiceStack/llms/tree/main/llms/extensions) directory - see [Built-in Extensions](http://llmspy.org/docs/extensions/built-in) for the list of built-in extensions you might want to include in your custom build.
