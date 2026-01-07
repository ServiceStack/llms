# System Prompts Extension

This extension configures AI requests with a library of **over 200+** awesome curated system prompts that can be selected from the UI.

## Custom System Prompts

You can also maintain your own library of system prompts which can be maintained for all anonymous users at:
`~/.llms/user/default/system-prompts.json`

Or for signed in users at:
`~/.llms/user/<github-user>/system-prompts.json`

The JSON file should contain an array of Prompt objects, e.g:

```json
[
    {
        "name": "Helpful Assistant",
        "prompt": "You are a helpful assistant."
    }
]
```
