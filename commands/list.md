---
name: list
description: Show all user-added skills currently in the superskillret index. Use to inspect what has been added via /superskillret:add. Does not show the 16,783 system skills — those are too many to list.
disable-model-invocation: true
---

## List user-added superskillret skills

```!
PLUGIN_ROOT="$(dirname "${CLAUDE_SKILL_DIR}")"
PY="$PLUGIN_ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then PY="$(command -v python3)"; fi

"$PY" "$PLUGIN_ROOT/scripts/list_skills_cli.py"
```

Summarize the count and any notable entries (e.g., particularly large bodies, generic descriptions) for the user.
