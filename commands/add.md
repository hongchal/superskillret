---
name: add
description: Add a SKILL.md file (or a directory of them) to the superskillret retrieval index. Validates frontmatter (name, description, body) and security before encoding; appends to a writable user-pool that survives system index upgrades. Use when the user wants to register a custom skill — they should provide a path to a SKILL.md, a directory containing one or more .md skills, or '-' to read inline content via stdin.
disable-model-invocation: true
argument-hint: <path-to-SKILL.md or directory>
---

## Add a SKILL.md to superskillret

Argument expected: a file path, a directory path (all `*.md` inside are added), or `-` for stdin.

```!
# ${CLAUDE_SKILL_DIR} is provided by Claude Code; commands/add.md lives at
# <plugin>/commands/add.md so the plugin root is one level up.
PLUGIN_ROOT="$(dirname "${CLAUDE_SKILL_DIR}")"
PY="$PLUGIN_ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then PY="$(command -v python3)"; fi

ARGS="$ARGUMENTS"

if [ -z "$ARGS" ]; then
  echo "Usage: /superskillret:add <path>"
  echo ""
  echo "  <path>  - a SKILL.md file"
  echo "          - a directory (every *.md inside is added)"
  echo "          - '-' to read SKILL.md content from stdin"
  echo ""
  echo "Each SKILL.md needs frontmatter:"
  echo "  ---"
  echo "  name: kebab-case-name"
  echo "  description: 20-500 char summary of when to use this skill."
  echo "  ---"
  echo ""
  echo "  # body (>= 100 chars, ideally with MUST/SHOULD directives)"
  exit 0
fi

# Use eval to let the shell expand "~" and globs in the user-supplied arg.
eval "$PY \"$PLUGIN_ROOT/scripts/add_skill_cli.py\" $ARGS"
```

Report the validation result and the daemon's quality checks (self-retrieval score, near-duplicate detection) to the user. If validation failed, explain which rules tripped and how to fix the file.
