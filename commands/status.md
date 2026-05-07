---
name: status
description: Show the superskillret daemon status (pid, socket, recent log). Run this to see whether the retrieval daemon is alive and working.
disable-model-invocation: true
---

## superskillret daemon status

### Socket
```!
ls -la /tmp/superskillret.sock 2>/dev/null || echo "socket missing"
```

### Process
```!
if [ -f /tmp/superskillret.pid ]; then
  PID=$(cat /tmp/superskillret.pid)
  if ps -p "$PID" >/dev/null 2>&1; then
    ps -o pid,rss,etime,command -p "$PID"
  else
    echo "pid file is stale: $PID"
  fi
else
  echo "no pid file"
fi
```

### Ping
```!
python3 - <<'PY'
import socket
try:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(2)
    s.connect('/tmp/superskillret.sock')
    s.sendall(b'{"op":"ping"}\n')
    print('reply:', s.recv(1024).decode().strip())
except Exception as e:
    print('ping failed:', e)
PY
```

### Recent log
```!
tail -n 20 /tmp/superskillret.log 2>/dev/null || echo "no log"
```

Summarize the state of the daemon for me in one sentence.
