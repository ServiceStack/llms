### Reset Configuration

You can reset the default configuration files used by `llms.py`. This is useful if your local configuration files (`~/.llms/llms.json`, `~/.llms/providers.json`, `~/.llms/providers-extra.json`) become corrupted or if you want to start fresh with the factory defaults.

#### `--reset`

View the available reset options:

```bash
llms --reset
```
Output:
```text
Available resets:
  config - Reset ~/.llms/llms.json to default
  providers - Reset ~/.llms/providers.json and ~/.llms/providers-extra.json to default
  all - Reset all configuration
```

#### `--reset config`

Resets your `~/.llms/llms.json` file back to the original default structure without affecting your providers configuration.

```bash
llms --reset config
```

#### `--reset providers`

Resets the `~/.llms/providers.json` and `~/.llms/providers-extra.json` files by downloading the latest defaults from the official repository, and automatically fetches updates. 

```bash
llms --reset providers
```

#### `--reset all`

Performs both configuration and providers resets simultaneously.

```bash
llms --reset all
```
