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

"$PY" - <<'PYEOF'
import json
import socket
import sys

SOCK = "/tmp/superskillret.sock"

def call(req):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(10.0)
    try:
        s.connect(SOCK)
    except Exception as e:
        print(f"daemon unreachable ({e}); send any user prompt first")
        sys.exit(1)
    s.sendall((json.dumps(req) + "\n").encode())
    buf = b""
    while True:
        chunk = s.recv(1 << 16)
        if not chunk: break
        buf += chunk
        if b"\n" in buf: break
    s.close()
    return json.loads(buf.decode())

reply = call({"op": "list_user_skills"})
skills = reply.get("skills", [])
if not skills:
    print("no user-added skills yet - use /superskillret:add <path> to register one")
else:
    print(f"{len(skills)} user-added skill(s):")
    print()
    for s in skills:
        name = s.get("name", "?")
        desc = (s.get("description") or "").strip().replace("\n", " ")
        if len(desc) > 80: desc = desc[:77] + "..."
        body_kb = len((s.get("body") or "").encode("utf-8")) / 1024
        print(f"  - {name}")
        print(f"      {desc}")
        print(f"      (body: {body_kb:.1f} KB)")
PYEOF
```

Summarize the count and any notable entries (e.g., particularly large bodies, generic descriptions) for the user.
