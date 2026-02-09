# Customizing User and Agent Avatars

Personalize your chat experience by adding custom avatars for yourself and AI agents.

## Overview

Avatars are displayed in the chat interface to visually distinguish between user messages and AI responses. You can customize these avatars by placing image files in your user directory (`~/.llms/`).

---

## Supported Formats

| Format | Extension | Notes |
|--------|-----------|-------|
| PNG    | `.png`    | Recommended for photos |
| SVG    | `.svg`    | Recommended for icons, scales perfectly |

---

## User Avatar

Your user avatar appears next to your messages in the chat.

### File Resolution Order

The system checks for avatar files in this order (first match wins):

| Priority | Path | Description |
|----------|------|-------------|
| 1 | `~/.llms/users/{username}/avatar.{mode}.png` | User-specific, mode-specific PNG |
| 2 | `~/.llms/users/{username}/avatar.{mode}.svg` | User-specific, mode-specific SVG |
| 3 | `~/.llms/users/{username}/avatar.png` | User-specific PNG |
| 4 | `~/.llms/users/{username}/avatar.svg` | User-specific SVG |
| 5 | `~/.llms/avatar.{mode}.png` | Global, mode-specific PNG |
| 6 | `~/.llms/avatar.{mode}.svg` | Global, mode-specific SVG |
| 7 | `~/.llms/avatar.png` | Global PNG |
| 8 | `~/.llms/avatar.svg` | Global SVG |

> [!NOTE]
> `{mode}` is either `light` or `dark` depending on the current UI theme.
> `{username}` is your authenticated username (if using authentication).

### Quick Setup

For a simple setup, just add one of these files:
- `~/.llms/avatar.png` - Works for all modes
- `~/.llms/avatar.svg` - Works for all modes

For theme-specific avatars:
- `~/.llms/avatar.light.png` - Light mode only
- `~/.llms/avatar.dark.png` - Dark mode only

---

## Agent Avatar

Agent avatars appear next to AI responses. You can customize the default agent avatar or create role-specific avatars.

### File Resolution Order

For a given agent role (e.g., `assistant`):

| Priority | Path | Description |
|----------|------|-------------|
| 1 | `~/.llms/{role}.{mode}.png` | Role-specific, mode-specific PNG |
| 2 | `~/.llms/{role}.{mode}.svg` | Role-specific, mode-specific SVG |
| 3 | `~/.llms/{role}.png` | Role-specific PNG |
| 4 | `~/.llms/{role}.svg` | Role-specific SVG |
| 5 | `~/.llms/agent.{mode}.png` | Default agent, mode-specific PNG |
| 6 | `~/.llms/agent.{mode}.svg` | Default agent, mode-specific SVG |
| 7 | `~/.llms/agent.png` | Default agent PNG |
| 8 | `~/.llms/agent.svg` | Default agent SVG |

> [!TIP]
> The `{role}` corresponds to the agent's role identifier (e.g., `assistant`, `coder`, `researcher`).

### Quick Setup

For a simple setup, just add one of these files:
- `~/.llms/agent.png` - Default for all agents
- `~/.llms/agent.svg` - Default for all agents

For role-specific avatars:
- `~/.llms/assistant.png` - Custom avatar for `assistant` role
- `~/.llms/coder.png` - Custom avatar for `coder` role

---

## Default Avatars

If no custom avatars are found, the system displays built-in default avatars:

- **User**: A person silhouette icon with a blue background
- **Agent**: An "AI" text icon with a gray background

Both defaults automatically adapt to light/dark mode.

---

## Examples

### Example 1: Simple Global Avatar

Place a single avatar file for all contexts:

```
~/.llms/
└── avatar.png          # Your user avatar (all modes)
```

### Example 2: Theme-Aware Avatars

Provide different avatars for light and dark modes:

```
~/.llms/
├── avatar.light.png    # User avatar for light mode
├── avatar.dark.png     # User avatar for dark mode
├── agent.light.svg     # Agent avatar for light mode
└── agent.dark.svg      # Agent avatar for dark mode
```

### Example 3: Role-Specific Agent Avatars

Customize avatars for different agent roles:

```
~/.llms/
├── avatar.png          # Your user avatar
├── assistant.png       # Avatar for assistant role
├── coder.png           # Avatar for coder role
└── agent.png           # Fallback for other roles
```
