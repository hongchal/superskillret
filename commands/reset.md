---
name: reset
description: Forget the per-session "already seen" skill memory so the next prompt can re-retrieve any skill from scratch. Use after switching topics or when a skill you want keeps getting suppressed.
disable-model-invocation: true
---

## Reset superskillret session memory

```!
python3 - <<'PY'
import socket, json
try:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(2)
    s.connect('/tmp/superskillret.sock')
    s.sendall(b'{"op": "reset_session"}\n')
    print("daemon reply:", s.recv(1024).decode().strip())
except Exception as e:
    print("reset failed:", e)
PY
```

Confirm all tracked sessions were cleared. The retriever will rank from the full top-K on the next prompt.
