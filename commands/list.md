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

PLUGIN_ROOT="$PLUGIN_ROOT" "$PY" - <<'PYEOF'
import os
import sys
sys.path.insert(0, os.path.join(os.environ["PLUGIN_ROOT"], "scripts"))

try:
    from daemon_client import daemon_request
except ImportError as e:
    print(f"FAIL: cannot import daemon_client ({e}); is the plugin venv set up?")
    sys.exit(1)

try:
    reply = daemon_request({"op": "list_user_skills"})
except Exception as e:
    print(f"FAIL: {e}")
    sys.exit(1)

skills = reply.get("skills", [])
if not skills:
    print("no user-added skills yet — use /superskillret:add <path> to register one")
else:
    print(f"{len(skills)} user-added skill(s):")
    print()
    for s in skills:
        name = s.get("name", "?")
        desc = (s.get("description") or "").strip().replace("\n", " ")
        if len(desc) > 80:
            desc = desc[:77] + "..."
        body_kb = len((s.get("body") or "").encode("utf-8")) / 1024
        gr = s.get("global_row")
        print(f"  - {name}  [global row {gr}]")
        print(f"      {desc}")
        print(f"      (body: {body_kb:.1f} KB)")
PYEOF
```

Summarize the count and any notable entries (e.g., particularly large bodies, generic descriptions) for the user.
