---
name: stop
description: Stop the superskillret daemon to free memory. It will lazy-start again on the next user prompt.
disable-model-invocation: true
---

## Stopping superskillret daemon

```!
if [ -f /tmp/superskillret.pid ]; then
  PID=$(cat /tmp/superskillret.pid)
  if ps -p "$PID" >/dev/null 2>&1; then
    echo "stopping daemon pid=$PID"
    kill "$PID"
    sleep 1
    if ps -p "$PID" >/dev/null 2>&1; then
      echo "still alive, sending SIGKILL"
      kill -9 "$PID"
    fi
  else
    echo "pid $PID is not running"
  fi
  rm -f /tmp/superskillret.pid
fi
rm -f /tmp/superskillret.sock
echo "daemon stopped."
```

Confirm the daemon was stopped successfully.
