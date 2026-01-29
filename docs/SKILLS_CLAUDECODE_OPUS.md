# Skills

Skills are modular packages that extend Claude's capabilities with specialized knowledge, workflows, and tools. Think of them as "onboarding guides" for specific domains—they transform Claude from a general-purpose assistant into a specialized agent equipped with procedural knowledge for particular tasks.

## What Skills Provide

- **Specialized Workflows** - Multi-step procedures for specific domains (e.g., creating plans, building frontends, processing documents)
- **Tool Integrations** - Instructions for working with specific file formats like PDFs, DOCX, XLSX, or PPTX
- **Domain Expertise** - Company-specific knowledge, schemas, business logic, and best practices
- **Bundled Resources** - Scripts, reference materials, and templates for complex or repetitive tasks

## Using Skills

### Skill Selector Panel

Click the **Skills icon** in the top toolbar to open the Skill Selector panel. This panel lets you control which skills are available during your conversations.

**Global Controls:**

| Button | Description |
|--------|-------------|
| **All Skills** | Include all available skills (green indicator) |
| **No Skills** | Exclude all skills (fuchsia indicator) |
| Custom selection | Include only specific skills you choose (blue indicator) |

**Working with Skill Groups:**

Skills are organized into groups based on their source location:

- `~/.llms/.agents` - Your personal skills (editable)
- `~/.claude/skills` - User-level global skills
- `.claude/skills` - Project-level skills

Each group shows a count of active skills (e.g., "3/5") and provides quick actions:
- **all** - Enable all skills in the group
- **none** - Disable all skills in the group

Click individual skill names to toggle them on/off. Hover over a skill to see its description.

### Skills Management Page

Access the full skills management interface by clicking the **Skills icon** in the left sidebar. This page provides comprehensive skill management:

**Left Sidebar:**
- Search skills by name or description
- Browse skills organized by group
- Expand skills to see their file structure
- Lock icon indicates read-only skills

**Center Panel:**
- View skill details (name, description, group, file count, location)
- Browse and edit skill files
- View file contents with syntax highlighting

### Creating Skills

1. Click **Create Skill** in the Skills Management page header
2. Enter a skill name (lowercase letters, numbers, and hyphens only, max 40 characters)
3. The new skill is created in your personal skills folder (`~/.llms/.agents`)
4. A template `SKILL.md` file is generated automatically

### Editing Skills

Only skills in your home directory (`~/.llms/.agents`) can be edited. Read-only skills show a lock icon.

**To edit a file:**
1. Select a skill and click on a file to view it
2. Click **Edit** to enter edit mode
3. Make your changes in the text editor
4. Click **Save** to save changes or **Cancel** to discard

**To add a file:**
1. Expand the skill in the sidebar
2. Click **+ file** in the skill's file tree header
3. Enter the relative file path (e.g., `scripts/helper.py`)

**To delete a file:**
1. Hover over a file in the tree
2. Click the **×** button that appears
3. Confirm deletion in the dialog

Note: The `SKILL.md` file cannot be deleted directly—delete the entire skill instead.

### Deleting Skills

1. Expand the skill in the sidebar
2. Click **delete** in the skill's header
3. Confirm deletion in the dialog

## Skill Structure

Each skill consists of:

```
skill-name/
├── SKILL.md           # Required - Main instructions and metadata
├── scripts/           # Optional - Executable code (Python, Bash, etc.)
├── references/        # Optional - Documentation and reference material
└── assets/            # Optional - Templates, images, fonts, boilerplate
```

### SKILL.md Format

The `SKILL.md` file contains:

**Frontmatter (YAML):**
```yaml
---
name: my-skill
description: What this skill does and when to use it
---
```

**Body (Markdown):**
Instructions and guidance for using the skill.

## Common Use Cases

### Document Processing
Skills like `docx`, `pdf`, `xlsx`, and `pptx` provide specialized capabilities for working with office documents—creating, editing, extracting data, and preserving formatting.

### Frontend Development
The `frontend-design` skill helps create distinctive, production-grade web interfaces with high design quality.

### Planning & Architecture
The `create-plan` skill generates concise, actionable plans for coding tasks with clear scope and action items.

### Creating New Skills
The `skill-creator` skill guides you through building effective skills with proper structure and best practices.

### Internal Communications
Skills for writing status reports, leadership updates, FAQs, and other internal documents in company-preferred formats.

### Testing & Validation
The `webapp-testing` skill enables interaction with local web applications using Playwright for frontend verification and debugging.

## How Claude Uses Skills

When skills are enabled:

1. Claude sees the name and description of all available skills
2. When a task matches a skill's description, Claude reads the skill's full instructions
3. Claude follows the skill's guidance, using any bundled scripts, references, or assets as needed
4. Skills can reference additional files that Claude reads only when necessary

This progressive loading ensures skills provide specialized capabilities without overwhelming the conversation context.

## Tips

- **Enable relevant skills** - Only include skills that match your current task to keep conversations focused
- **Check the indicator** - The top toolbar icon shows your current skill status (green=all, blue=custom, fuchsia=none)
- **Use skill groups** - Quickly enable/disable related skills together using group controls
- **Create project skills** - Build skills specific to your project's workflows, schemas, and conventions
- **Start with examples** - When creating new skills, look at existing skills like `create-plan` for structure guidance
