---
name: list
description: Show all user-added skills currently in the superskillret index. Use to inspect what has been added via /superskillret:add. Does not show the 16,783 system skills — those are too many to list.
disable-model-invocation: true
---

## List user-added superskillret skills

```!
# Robust PLUGIN_ROOT resolution:
#   1. ${CLAUDE_PLUGIN_ROOT} (set by hooks; sometimes also slash commands)
#   2. dirname ${CLAUDE_SKILL_DIR} (set by slash commands in recent CC versions)
#   3. fail with a clear message
if [ -n "${CLAUDE_PLUGIN_ROOT}" ]; then
  PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"
elif [ -n "${CLAUDE_SKILL_DIR}" ]; then
  PLUGIN_ROOT="$(dirname "${CLAUDE_SKILL_DIR}")"
else
  echo "FAIL: cannot resolve plugin root — neither CLAUDE_PLUGIN_ROOT nor CLAUDE_SKILL_DIR is set"
  echo "      please report at https://github.com/hongchal/superskillret/issues"
  exit 1
fi

if [ ! -d "$PLUGIN_ROOT/scripts" ]; then
  echo "FAIL: $PLUGIN_ROOT/scripts not found — PLUGIN_ROOT resolved to a wrong directory"
  exit 1
fi

PY="$PLUGIN_ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then PY="$(command -v python3)"; fi

"$PY" "$PLUGIN_ROOT/scripts/list_skills_cli.py"
```

Summarize the count and any notable entries (e.g., particularly large bodies, generic descriptions) for the user.
