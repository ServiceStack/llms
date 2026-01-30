# Skills

Skills extend Claude's capabilities with specialized knowledge, workflows, and tools. Think of them as modular packages that transform Claude from a general-purpose assistant into a domain expert for specific tasks.

## What Are Skills?

A skill is a self-contained package containing:

- **Instructions** - Step-by-step guidance for specific workflows
- **Scripts** - Executable code for complex or repetitive tasks
- **References** - Documentation loaded on demand when needed
- **Assets** - Templates, images, and files used in output

When a skill is active, Claude automatically reads its instructions before starting relevant tasks, ensuring consistent, high-quality results.

## When to Use Skills

Skills are valuable when you need Claude to:

- **Follow consistent workflows** - Code reviews, planning, documentation writing
- **Work with specific technologies** - Frameworks, APIs, or proprietary systems
- **Apply domain expertise** - Legal analysis, medical terminology, company policies
- **Produce standardized outputs** - Reports, designs, formatted documents
- **Use specialized tools** - Custom scripts, validation tools, generators

## Managing Skills

### The Skills Panel

Access the skills panel by clicking the **Skills** icon in the top toolbar. The panel displays all available skills organized by source.

![Skills Panel Overview](https://docs.servicestack.net/assets/img/llms/skills-panel-overview.png)

### Skill Groups

Skills are organized into groups based on their source location. Skills are discovered from these directories in order:

| Group | Location | Description | Editable |
|-------|----------|-------------|----------|
| Project (Agent) | `.agent/skills/` | Skills local to the current project | ✓ Yes |
| Project (Claude) | `.claude/skills/` | Claude-format skills in the current project | ✓ Yes |
| User (Agent) | `~/.llms/.agents/skills/` | Your personal skills collection | ✓ Yes |
| User (Claude) | `~/.claude/skills/` | Claude-format skills in your home directory | ✓ Yes |
| Built-in | Extension directory | Skills bundled with the extension | ✗ No |

**Project-level skills** (`.agent/` and `.claude/`) are specific to the workspace you're working in. They're ideal for project-specific workflows, coding standards, or team conventions.

**User-level skills** (`~/.llms/.agents/` and `~/.claude/`) are available across all projects. Use these for personal workflows and preferences.

Both `.agent` and `.claude` directory formats are supported for compatibility with different tooling conventions.

### Selecting Skills for a Conversation

The skills panel provides three modes for controlling which skills are active:

#### All Skills (Green)
All available skills are available for Claude to use. Claude decides which skills are relevant based on your request.

**Best for:** General conversations where you want Claude to have access to all capabilities.

#### No Skills (Purple)
No skills are loaded. Claude operates with base capabilities only.

**Best for:** Simple tasks or when you want to ensure skills don't influence the response.

#### Custom Selection (Blue)
Select specific skills to include. Click individual skills to toggle them on/off.

**Best for:** Focusing Claude on specific domains or troubleshooting skill interactions.

### Group Controls

Each skill group has **all** and **none** buttons for quick selection:

- **all** - Include every skill in the group
- **none** - Exclude every skill in the group

### Collapsing Groups

Click the chevron (▼) next to a group name to collapse or expand it. This helps manage screen space when you have many skills.

## Browsing and Installing Skills

The skills extension includes a community registry with thousands of pre-built skills you can browse and install.

### Accessing the Skill Browser

1. Navigate to the **Manage Skills** page (click the Skills icon in the left sidebar)
2. Click the **Browse** tab to open the skill browser
3. Use the search box to find skills by name or source repository

### Searching for Skills

The search feature queries the community registry, which indexes skills from popular GitHub repositories. Results show:

- **Skill name** - The identifier used for installation
- **Install count** - How many times the skill has been installed
- **Source repository** - The GitHub repo containing the skill

### Installing a Skill

1. Find a skill you want to install
2. Click the **Install** button next to the skill
3. Wait for the installation to complete (the skill is cloned from GitHub)
4. The skill appears in your personal collection (`~/.llms/.agents`)

**What happens during installation:**

1. The source repository is cloned from GitHub (shallow clone for speed)
2. The skill's SKILL.md and associated files are discovered
3. Files are copied to `~/.llms/.agents/skills/<skill-name>`
4. The skill is immediately available for use

Example:
```bash
# Search for React-related skills
curl "http://localhost:8000/ext/skills/search?q=react"

# Install a specific skill
curl -X POST "http://localhost:8000/ext/skills/install/vercel-react-best-practices"
```

## Creating and Editing Skills

### Creating a New Skill

1. Navigate to the **Manage Skills** page (click the Skills icon in the left sidebar)
2. Click the **Create Skill** button
3. Enter a skill name (lowercase letters, numbers, and hyphens only)
4. The skill is created with a template SKILL.md file

### Skill Structure

A skill consists of at least one file:

```
my-skill/
└── SKILL.md          # Required: Instructions and metadata
```

Skills can also include additional resources:

```
my-skill/
├── SKILL.md          # Required: Instructions and metadata
├── scripts/          # Optional: Executable code
│   └── helper.py
├── references/       # Optional: Documentation
│   └── guide.md
└── assets/           # Optional: Templates and files
    └── template.html
```

### Editing Skills

1. In the Manage Skills page, select a skill from your personal collection (`~/.llms/.agents`)
2. Click on a file in the file tree to view it
3. Click **Edit** to modify the file
4. Make your changes in the editor
5. Click **Save** to persist changes

**Note:** Only skills in `~/.llms/.agents` are editable. Built-in skills are read-only.

### Adding Files to a Skill

1. Select an editable skill
2. Click **+ file** next to the skill name
3. Enter the file path (e.g., `scripts/my-script.py`)
4. The file is created and opened for editing

### Deleting Skills and Files

- **Delete a skill:** Select the skill, then click the **delete** button next to the skill name
- **Delete a file:** Hover over the file in the tree, then click the **×** icon

**Note:** The `SKILL.md` file cannot be deleted as it's required for every skill.

## Skill Metadata (SKILL.md)

Every skill requires a `SKILL.md` file with YAML frontmatter:

```yaml
---
name: my-skill
description: Clear description of what this skill does and when to use it
---

# Skill Instructions

Write clear, actionable guidance here...
```

### Required Fields

| Field | Description |
|-------|-------------|
| `name` | Unique identifier for the skill (kebab-case) |
| `description` | What the skill does and when Claude should use it |

### Optional Fields

| Field | Description |
|-------|-------------|
| `license` | License information for the skill |
| `compatibility` | Version or system compatibility notes |
| `metadata` | Key-value pairs for client-specific properties |

## How Skills Work

When you send a message with skills enabled:

1. **Selection** - Claude reads the name and description of active skills
2. **Triggering** - Claude determines which skills are relevant to your request
3. **Loading** - For triggered skills, Claude loads the full SKILL.md instructions
4. **Execution** - Claude follows the skill's guidance to complete your task
5. **References** - Claude loads additional files from `references/` only when needed
6. **Scripts** - Claude may execute scripts from `scripts/` directly

This progressive loading ensures efficient use of context window while providing comprehensive guidance when needed.

## Example Use Cases

### Documentation Writing

**Skill:** `doc-coauthoring`

Guides you through creating structured documentation with a three-stage workflow: context gathering, refinement, and reader testing.

**Trigger:** "Help me write a design doc for..."

### Code Planning

**Skill:** `create-plan`

Creates actionable implementation plans with scope definition, checklists, and risk assessment.

**Trigger:** "Create a plan for adding authentication..."

### Creating New Skills

**Skill:** `skill-creator`

Guides you through creating effective skills with proper structure, bundled resources, and best practices.

**Trigger:** "I want to create a skill for..."

## Tips for Effective Skill Usage

1. **Start broad, then narrow** - Begin with "All Skills" and switch to custom selection if Claude uses irrelevant skills

2. **Check the skill description** - Hover over a skill name in the panel to see its full description

3. **Enable the skill tool** - The `skill` tool must be enabled for skills to work. A warning appears if it's disabled.

4. **Iterate on personal skills** - As you use your custom skills, refine them based on what works

5. **Use references for large content** - Keep SKILL.md focused; move detailed docs to `references/`

6. **Test scripts** - Always verify scripts work before relying on them

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Skills not appearing | Check that the `skill` tool is enabled in preferences |
| Claude not using a skill | Verify the skill is selected (not in "No Skills" mode) |
| Can't edit a skill | Only skills in `~/.llms/.agents` are editable; built-in skills are read-only |
| Skill not triggering | Review the description in SKILL.md - it determines when Claude uses the skill |
| Changes not saving | Ensure you click **Save** after editing; unsaved changes show an orange dot |

## Keyboard Shortcuts

| Action | Shortcut |
|--------|----------|
| Open Skills panel | Click Skills icon in top toolbar |
| Navigate to Manage Skills | Click Skills icon in left sidebar |
| Save file | Ctrl+S (when editing) |

## See Also

- [Skill Creator Guide](./ui/skills/skill-creator/SKILL.md) - Learn to build effective skills
- [Create Plan Skill](./ui/skills/create-plan/SKILL.md) - Example of a workflow skill
