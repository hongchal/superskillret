"""Shared client helpers for talking to the long-running retrieval daemon.

Used by `scripts/add_skill_cli.py` and by the inline Python in
`commands/list.md` and `commands/remove.md`. Both paths need to lazy-spawn
the daemon when the user invokes a slash command without first sending a
regular prompt (slash commands don't fire the UserPromptSubmit hook that
retrieve.py uses to lazy-spawn).

Exposes:
    daemon_request(req: dict, timeout: float = 30.0) -> dict
        Send a single JSON request, lazy-spawning the daemon if needed.
        Returns the parsed JSON reply. Raises RuntimeError on hard failures
        (daemon couldn't be spawned, didn't come up in time, etc.).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path


_THIS = Path(__file__).resolve()
PLUGIN_ROOT = _THIS.parent.parent

SOCKET_PATH = os.environ.get("SUPERSKILLRET_SOCKET", "/tmp/superskillret.sock")
DAEMON_LOG = os.environ.get("SUPERSKILLRET_LOG", "/tmp/superskillret.log")
DAEMON_SCRIPT = PLUGIN_ROOT / "scripts" / "daemon.py"
VENV_PYTHON = PLUGIN_ROOT / ".venv" / "bin" / "python"
DAEMON_SPAWN_WAIT = float(os.environ.get("SUPERSKILLRET_SPAWN_WAIT", "60"))


def ping_daemon(timeout: float = 1.0) -> bool:
    """True iff the daemon is listening on SOCKET_PATH and responds to ping."""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(SOCKET_PATH)
        s.sendall(b'{"op": "ping"}\n')
        reply = s.recv(1024)
        s.close()
        return b'"ok"' in reply
    except Exception:
        return False


def spawn_daemon() -> None:
    """Fire-and-forget background spawn of the retrieval daemon.

    Uses the plugin venv python when available; otherwise falls back to the
    interpreter running this module. Output goes to the daemon log so the
    parent process exits cleanly.
    """
    py = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
    log = open(DAEMON_LOG, "a")
    subprocess.Popen(
        [py, str(DAEMON_SCRIPT)],
        stdout=log,
        stderr=log,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        env={**os.environ},
    )


def ensure_daemon(spawn_wait: float = DAEMON_SPAWN_WAIT) -> None:
    """Block until the daemon is reachable, spawning it if necessary.

    Raises RuntimeError when the daemon script or venv is missing, or when
    the daemon was spawned but didn't bind its socket within spawn_wait.
    """
    if ping_daemon():
        return
    if not DAEMON_SCRIPT.exists():
        raise RuntimeError(
            f"daemon script not found at {DAEMON_SCRIPT}; "
            "is this the plugin install dir?"
        )
    if not VENV_PYTHON.exists():
        raise RuntimeError(
            f"venv python not found at {VENV_PYTHON}; "
            "run scripts/install.sh first or send any user prompt to Claude "
            "Code so the SessionStart bootstrap can set it up"
        )
    spawn_daemon()
    deadline = time.time() + spawn_wait
    while time.time() < deadline:
        if ping_daemon():
            return
        time.sleep(0.3)
    raise RuntimeError(
        f"daemon spawned but never came up within {spawn_wait:.0f}s — "
        f"check {DAEMON_LOG} for tracebacks"
    )


def daemon_request(req: dict, timeout: float = 30.0) -> dict:
    """Send one JSON request and return the parsed reply.

    Lazy-spawns the daemon if not running. Raises RuntimeError on connect
    or protocol failures.
    """
    ensure_daemon()
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(SOCKET_PATH)
    except (FileNotFoundError, ConnectionRefusedError) as e:
        raise RuntimeError(
            f"daemon connect failed at {SOCKET_PATH} even after spawn: {e}"
        )
    try:
        s.sendall((json.dumps(req) + "\n").encode("utf-8"))
        buf = b""
        while True:
            chunk = s.recv(1 << 16)
            if not chunk:
                break
            buf += chunk
            if b"\n" in buf:
                break
        return json.loads(buf.decode("utf-8"))
    finally:
        s.close()
