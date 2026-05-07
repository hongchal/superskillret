#!/usr/bin/env bash
# superskillret bootstrap — runs on every Claude Code SessionStart.
#
# Fast path (venv + cache already present): exit immediately, no output.
# First-time path: spawn install.sh in the background and exit. The
# detached install writes progress to $LOG and a lock file. retrieve.py
# sees the lock on the next user prompt and tells the user to wait.
#
# We must return in milliseconds and must not block the UI, so any real
# work is double-fork / disowned into a separate session.

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="${SUPERSKILLRET_INSTALL_LOG:-/tmp/superskillret-install.log}"
LOCK="${SUPERSKILLRET_INSTALL_LOCK:-/tmp/superskillret-install.lock}"
DONE_MARKER="$ROOT/.installed"

# Already fully installed? Nothing to do.
if [ -f "$DONE_MARKER" ] && [ -x "$ROOT/.venv/bin/python" ]; then
  exit 0
fi

# Install already in flight? Let it finish.
if [ -f "$LOCK" ]; then
  LOCK_PID=$(cat "$LOCK" 2>/dev/null)
  if [ -n "$LOCK_PID" ] && kill -0 "$LOCK_PID" 2>/dev/null; then
    exit 0
  fi
  # stale lock, clean up
  rm -f "$LOCK"
fi

# Fire and forget. Double fork + setsid so the install outlives this hook
# and this shell exits cleanly in milliseconds.
{
  (
    echo "$$" > "$LOCK"
    {
      echo "[$(date -Is)] superskillret: first-time setup starting"
      bash "$ROOT/scripts/install.sh"
      STATUS=$?
      if [ "$STATUS" -eq 0 ]; then
        touch "$DONE_MARKER"
        echo "[$(date -Is)] superskillret: setup complete"
      else
        echo "[$(date -Is)] superskillret: setup failed (exit $STATUS)"
      fi
      rm -f "$LOCK"
    } >> "$LOG" 2>&1
  ) &
} < /dev/null > /dev/null 2>&1

disown 2>/dev/null || true
exit 0
