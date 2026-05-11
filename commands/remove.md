---
name: remove
description: Remove a user-added skill from the superskillret index by name. Use when the user no longer wants a previously-registered skill to be retrievable, or wants to replace it with an updated version (remove + re-add). Does NOT affect the 16,783 system skills.
disable-model-invocation: true
argument-hint: <skill-name>
---

## Remove a user-added superskillret skill

Argument expected: the skill `name` (the kebab-case frontmatter name, not the file path).

```!
if [ -n "${CLAUDE_PLUGIN_ROOT}" ]; then
  PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"
elif [ -n "${CLAUDE_SKILL_DIR}" ]; then
  PLUGIN_ROOT="$(dirname "${CLAUDE_SKILL_DIR}")"
else
  echo "FAIL: cannot resolve plugin root — neither CLAUDE_PLUGIN_ROOT nor CLAUDE_SKILL_DIR is set"
  exit 1
fi

if [ ! -d "$PLUGIN_ROOT/scripts" ]; then
  echo "FAIL: $PLUGIN_ROOT/scripts not found"
  exit 1
fi

PY="$PLUGIN_ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then PY="$(command -v python3)"; fi

if [ -z "$ARGUMENTS" ]; then
  echo "Usage: /superskillret:remove <name>"
  echo ""
  echo "Use /superskillret:list to see registered names."
  exit 0
fi

eval "$PY \"$PLUGIN_ROOT/scripts/remove_skill_cli.py\" $ARGUMENTS"
```

Confirm to the user whether the removal succeeded and remind them to use `/superskillret:list` to verify.
