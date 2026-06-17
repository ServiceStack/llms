# Agent Profiles & Customization

The **Agents Extension** allows you to configure specialized AI agent profiles within your environment. Each agent is defined as a profile that can have its own system prompt, default LLM model, UI theme, allowed tools, allowed skills, default avatar, and custom footer action buttons.

---

## Agent Profile Storage Directories

Agents are parsed from directory profiles. The application searches for agent profiles in the following order:

1. **Built-in Agent Profiles**: 
   [llms/extensions/agents/profiles](https://github.com/ServiceStack/llms/tree/main/llms/extensions/agents/profiles) -
   Includes default profiles like [chat](https://github.com/ServiceStack/llms/tree/main/llms/extensions/agents/profiles/chat) 
   and [planner](https://github.com/ServiceStack/llms/tree/main/llms/extensions/agents/profiles/planner)

2. **Global Custom Profiles**:
   `{user_data_path}/profiles/` (e.g. `~/.llms/profiles/`)

3. **User-Specific Custom Profiles**:
   `{user_data_path}/user/{username}/profiles/` (e.g. `~/.llms/user/admin/profiles/assistant`)

> [!NOTE]
> Custom agent profiles override built-in profiles of the same name. This allows you to customize default behaviors by redefining standard profiles in your user-specific directory.

---

## Profile Folder Structure

An agent profile is a subdirectory containing the following configuration and prompt files:

```directory
my-agent/
├── config.json          # Required: Defines agent preferences, tools, and actions
├── SYSTEM.md            # Required (or SYSTEM.template): Defines the system prompt
├── SYSTEM.template      # Optional: Text template for composite system prompts
└── avatar.png           # Optional: Agent's profile image (.png .webp .jpg .jpeg .svg)
```

---

## Agent Configuration (`config.json`)

The `config.json` file controls the default preferences and restrictions of the agent.

### Example Configuration

```json
{
  "theme": "dark",
  "model": "Kimi K2.6",
  "onlyTools": [
    "get_current_time",
    "calc",
    "run_bash",
    "skill"
  ],
  "onlySkills": [],
  "actions": {
    "Execute Plan": {
      "profile": "coder"
    }
  }
}
```

### Configuration Schema

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | `string` | No | A custom display name for the agent (defaults to the folder name capitalized). |
| `enabled` | `boolean` | No | Defaults to `true`. Set to `false` to disable loading of the agent. |
| `theme` | `string` | No | The UI color scheme/theme to load when this agent is active (e.g. `nord`, `dark`, `light_slate`). |
| `model` | `string` | No | The default LLM model to select when starting conversations with this agent. |
| `onlyTools` | `array` | No | Restricts the tools the agent is permitted to use to the listed tool names. Set to `[]` to exclude all tools. If omitted (or `null`), all tools are allowed. |
| `onlySkills` | `array` | No | Restricts the skills the agent is permitted to use to the listed skill names. Set to `[]` to exclude all skills. If omitted (or `null`), all skills are allowed. |
| `injectPrompt` | `boolean` | No | Defaults to `true`. If set to `false`, the agent's system prompt will not be automatically prepended to the chat request. |
| `actions` | `object` | No | Maps action names to buttons that appear in the chat footer under the assistant's responses (see [Footer Actions](#footer-actions) below). |

---

## Defining System Prompts

Agents support two methods for defining system prompts: simple prompts and composite templates.

### 1. Simple System Prompt (`SYSTEM.md`)
Create a `SYSTEM.md` file in the agent profile directory. The contents of this file are loaded directly as the system prompt.

### 2. Composite Prompt (`SYSTEM.template`)
For complex agents that benefit from dividing prompt logic across multiple files, use a `SYSTEM.template` file. 

When a template is used, the system compiles the prompt using Python string formatting:
* **Markdown Variables**: Any `.md` file placed inside the profile directory is read, and its content is mapped to a variable named after the file (without the `.md` extension). For example, if you place `CODER.md` in the directory, you can reference it as `{CODER}` in `SYSTEM.template`.
* **Dynamic Memory (`{MEMORY_LATEST}`)**: If a `memory/` subdirectory exists, the system scans for `.md` files, sorts them in reverse order (newest first, e.g. using ISO-date filenames like `2026-06-13.md`), and reads the latest file. Its content is injected as the `{MEMORY_LATEST}` variable. If no memory files exist, `{MEMORY_LATEST}` defaults to an empty string.

#### Example `SYSTEM.template`
```markdown
You are a helpful software engineering assistant.

{CODER}

Here is the latest shared context from memory:
{MEMORY_LATEST}
```

---

## Footer Actions

You can configure context-aware helper buttons to appear at the bottom of the chat view when the last message is from the assistant.

### 1. Send Message Action
Prompts the user to send a preset message instantly to the active agent.

```json
"actions": {
  "Proceed": {
    "message": "Proceed with the execution"
  }
}
```

### 2. Switch Profile and Execute Action
Transitions the conversation to a different agent. When clicked:
* The selected agent switches to the designated profile (e.g., `coder`).
* The system starts a new thread titled `Execute Plan {parent_thread_title}` using the target agent's system prompt.
* A user message with the action's key (e.g., `Execute Plan`) is automatically posted to trigger the new agent.

```json
"actions": {
  "Execute Plan": {
    "profile": "coder"
  }
}
```

---

## Agent Avatars

To customize your agent's visual avatar, place an image file named `avatar` in the agent's folder. 
The system automatically checks for the following extensions in order:
* `avatar.png`
* `avatar.webp`
* `avatar.jpg`
* `avatar.jpeg`
* `avatar.svg`

If no avatar image is found in the profile directory, the application falls back to the default SVG agent avatar.
