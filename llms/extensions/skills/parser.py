"""YAML frontmatter parsing for SKILL.md files."""

from pathlib import Path
from typing import Optional

from .errors import ParseError, ValidationError
from .models import SkillProperties


def load_yaml(content: str) -> dict:
    """Simple YAML parser for skill frontmatter.

    Supports:
    - Key-value pairs: key: "value"
    - Comments: # comment
    - Simple nesting (indentation-based)
    """
    result = {}
    stack = [result]
    indents = [-1]
    last_key = None

    for line in content.splitlines():
        # Skip empty lines or full comments
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())

        # Handle indent levels
        while indent <= indents[-1]:
            indents.pop()
            stack.pop()

        # If we have a nested block under last key
        if indent > indents[-1] and last_key and isinstance(stack[-1], dict) and stack[-1].get(last_key) is None:
            # This branch is tricky with the simple look-behind.
            # Better approach: check if line is a key-value or array item
            pass

        # Parse key: value
        if ":" in stripped:
            key, val = stripped.split(":", 1)
            key = key.strip()
            val = val.strip()

            # Handle quotes
            if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            elif val.lower() == "true":
                val = True
            elif val.lower() == "false":
                val = False
            elif val == "":
                val = None  # Could be start of nested object

            current_dict = stack[-1]

            if val is None:
                # Prepare for nested object
                new_dict = {}
                current_dict[key] = new_dict
                stack.append(new_dict)
                indents.append(indent)
            else:
                current_dict[key] = val

            last_key = key
        else:
            # Handle continuation lines or unknown format if needed,
            # but for our simple use case we might error or ignore.
            pass

    return result


def find_skill_md(skill_dir: Path) -> Optional[Path]:
    """Find the SKILL.md file in a skill directory.

    Prefers SKILL.md (uppercase) but accepts skill.md (lowercase).

    Args:
        skill_dir: Path to the skill directory

    Returns:
        Path to the SKILL.md file, or None if not found
    """
    for name in ("SKILL.md", "skill.md"):
        path = skill_dir / name
        if path.exists():
            return path
    return None


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from SKILL.md content.

    Args:
        content: Raw content of SKILL.md file

    Returns:
        Tuple of (metadata dict, markdown body)

    Raises:
        ParseError: If frontmatter is missing or invalid
    """
    if not content.startswith("---"):
        raise ParseError("SKILL.md must start with YAML frontmatter (---)")

    parts = content.split("---", 2)
    if len(parts) < 3:
        raise ParseError("SKILL.md frontmatter not properly closed with ---")

    frontmatter_str = parts[1]
    body = parts[2].strip()

    try:
        metadata = load_yaml(frontmatter_str)
    except Exception as e:
        raise ParseError(f"Invalid YAML in frontmatter: {e}") from e

    if not isinstance(metadata, dict):
        raise ParseError("SKILL.md frontmatter must be a YAML mapping")

    # Clean up metadata values if necessary (simple parser already handles basics)
    if "metadata" in metadata and isinstance(metadata["metadata"], dict):
        metadata["metadata"] = {str(k): str(v) for k, v in metadata["metadata"].items()}

    return metadata, body


def read_properties(skill_dir: Path) -> SkillProperties:
    """Read skill properties from SKILL.md frontmatter.

    This function parses the frontmatter and returns properties.
    It does NOT perform full validation. Use validate() for that.

    Args:
        skill_dir: Path to the skill directory

    Returns:
        SkillProperties with parsed metadata

    Raises:
        ParseError: If SKILL.md is missing or has invalid YAML
        ValidationError: If required fields (name, description) are missing
    """
    skill_dir = Path(skill_dir)
    skill_md = find_skill_md(skill_dir)

    if skill_md is None:
        raise ParseError(f"SKILL.md not found in {skill_dir}")

    content = skill_md.read_text()
    metadata, _ = parse_frontmatter(content)

    if "name" not in metadata:
        raise ValidationError("Missing required field in frontmatter: name")
    if "description" not in metadata:
        raise ValidationError("Missing required field in frontmatter: description")

    name = metadata["name"]
    description = metadata["description"]

    if not isinstance(name, str) or not name.strip():
        raise ValidationError("Field 'name' must be a non-empty string")
    if not isinstance(description, str) or not description.strip():
        raise ValidationError("Field 'description' must be a non-empty string")

    return SkillProperties(
        name=name.strip(),
        description=description.strip(),
        license=metadata.get("license"),
        compatibility=metadata.get("compatibility"),
        allowed_tools=metadata.get("allowed-tools"),
        metadata=metadata.get("metadata"),
    )
