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

ARGS="$ARGUMENTS"

if [ -z "$ARGS" ]; then
  echo "Usage: /superskillret:remove <name>"
  echo ""
  echo "Use /superskillret:list to see registered names."
  exit 0
fi

"$PY" - "$ARGS" <<'PYEOF'
import json, socket, sys
name = sys.argv[1].strip()
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(15.0)
try:
    s.connect("/tmp/superskillret.sock")
except Exception as e:
    print(f"daemon unreachable ({e})"); sys.exit(1)
s.sendall((json.dumps({"op": "remove_user_skill", "name": name}) + "\n").encode())
buf = b""
while True:
    c = s.recv(1 << 16)
    if not c: break
    buf += c
    if b"\n" in buf: break
s.close()
reply = json.loads(buf.decode())
if reply.get("ok"):
    print(f"removed '{name}' (was at user-pool row {reply.get('removed_row')})")
else:
    print(f"FAIL: {reply.get('error', reply)}")
    sys.exit(1)
PYEOF
```

Confirm to the user whether the removal succeeded and remind them to use `/superskillret:list` to verify.
