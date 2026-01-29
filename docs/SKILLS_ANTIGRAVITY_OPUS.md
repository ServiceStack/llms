# Skills

Extend your AI agent with specialized knowledge, workflows, and reusable tools through modular skill packages.

## What Are Skills?

Skills are packages of domain-specific instructions, scripts, and resources that enhance your AI agent's capabilities. They allow you to:

- **Encode expertise** — Capture specialized knowledge, company workflows, or best practices that persist across sessions
- **Save context** — Only load what's needed, keeping your conversation focused
- **Reuse tools** — Bundle executable scripts that work consistently every time
- **Share knowledge** — Package and distribute skills across teams

## UI Overview

### Skill Selector (Top Bar)

Click the **Skills icon** in the top toolbar to access the Skill Selector dropdown:

![Skill Selector](/docs/images/skills-selector.png)

| Control | Description |
|---------|-------------|
| **All Skills** | Include all available skills in conversations (green indicator) |
| **No Skills** | Exclude all skills from conversations |
| **Individual Skills** | Click skill names to toggle them on/off (blue = custom selection) |
| **Group Controls** | Use `all` / `none` buttons to toggle entire skill groups |

The Skills icon color indicates the current state:
- 🟢 **Green** — All skills included
- 🔵 **Blue** — Custom selection active  
- ⚪ **No color** — No skills included

> [!NOTE]
> The `skill` tool must be enabled for skills to work. If disabled, you'll see a warning in the Skill Selector.

### Skills Management Page

Access the full management interface by clicking the **Skills icon** in the left sidebar, or navigate to `/skills`.

**Features:**
- **Browse skills** organized by location (global vs project-specific)
- **Search** to quickly find skills by name or description
- **Create new skills** in your global user directory
- **View and edit** skill files and documentation
- **Add files** to existing skills (scripts, references, assets)
- **Delete** skills or individual files

> [!IMPORTANT]
> Only skills in your user directory (`~/.llms/.agents`) are editable. Project and built-in skills are read-only (indicated by a lock icon).

## Skill Locations

Skills are discovered from multiple locations, with later locations taking precedence:

| Location | Scope | Editable |
|----------|-------|----------|
| Built-in | Extension default skills | ❌ |
| `~/.claude/skills` | Global Claude skills | ❌ |
| `.claude/skills` | Project Claude skills | ❌ |
| `~/.llms/.agent/skills` | Global user skills | ✅ |
| `.agent/skills` | Project skills | ❌ |

## Creating a Skill

### From the UI

1. Open the Skills page (`/skills` or left sidebar icon)
2. Click **Create Skill**
3. Enter a name (lowercase letters, numbers, and hyphens only)
4. A new skill folder is created with a template `SKILL.md`
5. Click **Edit** to customize the skill instructions

### Skill Structure

Each skill is a folder containing:

```
my-skill/
├── SKILL.md          # Required: Instructions and documentation
├── scripts/          # Optional: Executable code (Python, Bash, etc.)
├── references/       # Optional: Additional documentation
└── assets/           # Optional: Templates, images, fonts
```

### SKILL.md Format

```yaml
---
name: my-skill
description: Brief description of what this skill does and when to use it.
---

# My Skill

Detailed instructions for the AI agent...
```

> [!TIP]
> The `description` is key — it tells the AI when to use this skill. Be specific about triggers and use cases.

## Built-in Skills

| Skill | Description |
|-------|-------------|
| `create-plan` | Creates structured, actionable plans for coding tasks |
| `skill-creator` | Comprehensive guide for creating new skills |

## Best Practices

### Writing Effective Skills

1. **Clear descriptions** — Include what the skill does AND when to use it
2. **Concise instructions** — Keep SKILL.md under 500 lines
3. **Use references** — Move detailed docs to `references/` folder
4. **Bundle scripts** — Executable code in `scripts/` runs without using context
5. **Templates in assets** — Store reusable templates and resources in `assets/`

### Organizing Skills

- **Simple skills**: Single SKILL.md with all instructions
- **Domain skills**: SKILL.md overview + multiple reference files organized by topic
- **Tool skills**: SKILL.md + scripts for specific operations

## Use Cases

### Personal Workflow Automation
Create skills for repetitive tasks like code review checklists, deployment procedures, or documentation templates.

### Team Knowledge Sharing
Package team conventions, coding standards, and best practices as skills that persist across sessions.

### Project-Specific Context
Add `.agent/skills/` to your project with skills containing architecture decisions, API conventions, or test patterns.

### Domain Expertise
Capture specialized knowledge (database optimization, security practices, framework patterns) for consistent application.

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| Click skill group header | Expand/collapse the group |
| Click skill name | Expand/collapse skill files |
| Click file | View file contents |

## Troubleshooting

### Skills not appearing
- Verify the `skill` tool is enabled in the Tools selector
- Check that `SKILL.md` exists in the skill folder
- Ensure proper YAML frontmatter with `name` and `description`

### Can't edit a skill
- Only skills in `~/.llms/.agent/skills` are editable
- Look for the lock icon on read-only skill groups

### Skill not triggering
- Make the `description` more specific about when to use the skill
- Ensure the skill is enabled in the Skill Selector
