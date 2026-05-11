---
name: remove
description: Remove a user-added skill from the superskillret index by name. Use when the user no longer wants a previously-registered skill to be retrievable, or wants to replace it with an updated version (remove + re-add). Does NOT affect the 16,783 system skills.
disable-model-invocation: true
argument-hint: <skill-name>
---

## Remove a user-added superskillret skill

Argument expected: the skill `name` (the kebab-case frontmatter name, not the file path).

```!
PLUGIN_ROOT="$(dirname "${CLAUDE_SKILL_DIR}")"
PY="$PLUGIN_ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then PY="$(command -v python3)"; fi

if [ -z "$ARGUMENTS" ]; then
  echo "Usage: /superskillret:remove <name>"
  echo ""
  echo "Use /superskillret:list to see registered names."
  exit 0
fi

PLUGIN_ROOT="$PLUGIN_ROOT" "$PY" - "$ARGUMENTS" <<'PYEOF'
import os
import sys
sys.path.insert(0, os.path.join(os.environ["PLUGIN_ROOT"], "scripts"))

name = sys.argv[1].strip()

try:
    from daemon_client import daemon_request
except ImportError as e:
    print(f"FAIL: cannot import daemon_client ({e}); is the plugin venv set up?")
    sys.exit(1)

try:
    reply = daemon_request({"op": "remove_user_skill", "name": name})
except Exception as e:
    print(f"FAIL: {e}")
    sys.exit(1)

if reply.get("ok"):
    print(f"removed '{name}' (was at user-pool row {reply.get('removed_row')})")
else:
    print(f"FAIL: {reply.get('error', reply)}")
    sys.exit(1)
PYEOF
```

Confirm to the user whether the removal succeeded and remind them to use `/superskillret:list` to verify.
