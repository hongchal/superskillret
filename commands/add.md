---
name: add
description: Add a SKILL.md file to the superskillret retrieval index. Validates frontmatter (name, description, body) and security before encoding; appends to a writable user-pool that survives system index upgrades. Use when the user wants to register a custom skill — they should provide a path to a SKILL.md or pass '-' to pipe inline content.
disable-model-invocation: true
---

## Add a SKILL.md to superskillret

Argument expected: a path to a SKILL.md file (`~/my-skills/auth.md`), an absolute path, or `-` to read inline content.

```!
PLUGIN_ROOT="$(dirname "$(dirname "$0")")"
PY="$PLUGIN_ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then PY="$(command -v python3)"; fi

# $ARGUMENTS is the slash-command argument string Claude Code passes through.
# If empty, prompt the user.
if [ -z "$ARGUMENTS" ]; then
  echo "Usage: /superskillret:add <path-to-SKILL.md>"
  echo "       /superskillret:add - < SKILL.md       (stdin)"
  echo ""
  echo "The file must have frontmatter:"
  echo "  ---"
  echo "  name: kebab-case-name"
  echo "  description: 20-500 char summary of when to use this skill."
  echo "  ---"
  echo ""
  echo "  # body (≥ 100 chars, ideally with MUST/SHOULD directives)"
  exit 1
fi

"$PY" "$PLUGIN_ROOT/scripts/add_skill_cli.py" $ARGUMENTS
```

Report the validation result and the daemon's quality checks (self-retrieval score, near-duplicate detection) to the user. If validation failed, explain which rules tripped and how to fix the file.
