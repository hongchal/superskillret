"""UserPromptSubmit hook: thin socket client.

On each hook invocation:
  1. Connect to the long-running daemon over a Unix socket.
  2. If the socket is absent or dead, spawn the daemon in the background and
     wait briefly. First-call latency is high; subsequent calls are fast.
  3. Ask daemon for top-K skills, format as additionalContext, and print
     the Claude Code hook JSON.

This file intentionally does NOT import torch / sentence-transformers, so the
warm path is dominated by socket round-trip + python startup.

Environment variables:
  SUPERSKILLRET_SOCKET      default /tmp/superskillret.sock
  SUPERSKILLRET_TOP_K       default 3   (how many skills to inject)
  SUPERSKILLRET_MIN_SCORE   default 0.30 (drop hits below this cosine score)
  SUPERSKILLRET_PYTHON      python used to spawn daemon (default current)
  SUPERSKILLRET_SPAWN_WAIT  seconds to wait for lazy daemon boot (default 90)
  SUPERSKILLRET_DISABLE     if "1", hook returns empty context
"""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DAEMON_SCRIPT = ROOT / "scripts" / "daemon.py"
INSTALL_SCRIPT = ROOT / "scripts" / "install.sh"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
INSTALL_LOCK = Path(os.environ.get("SUPERSKILLRET_INSTALL_LOCK", "/tmp/superskillret-install.lock"))
INSTALL_DONE = ROOT / ".installed"
INSTALL_LOG = os.environ.get("SUPERSKILLRET_INSTALL_LOG", "/tmp/superskillret-install.log")

SOCKET_PATH = os.environ.get("SUPERSKILLRET_SOCKET", "/tmp/superskillret.sock")
TOP_K = int(os.environ.get("SUPERSKILLRET_TOP_K", "3"))
MIN_SCORE = float(os.environ.get("SUPERSKILLRET_MIN_SCORE", "0.30"))
# Prefer the plugin's own venv python (has torch, onnxruntime, etc).
# Only fall back to whatever python is running this hook if the venv isn't set up yet.
PYTHON = os.environ.get("SUPERSKILLRET_PYTHON") or (
    str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
)
SPAWN_WAIT = float(os.environ.get("SUPERSKILLRET_SPAWN_WAIT", "180"))
DISABLED = os.environ.get("SUPERSKILLRET_DISABLE") == "1"


def connect(timeout: float = 2.0):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect(SOCKET_PATH)
    return sock


def ping() -> bool:
    try:
        s = connect(timeout=1.0)
        s.sendall(b'{"op": "ping"}\n')
        reply = s.recv(1024)
        s.close()
        return b'"ok"' in reply
    except Exception:
        return False


def spawn_daemon():
    log = open(os.environ.get("SUPERSKILLRET_LOG", "/tmp/superskillret.log"), "a")
    subprocess.Popen(
        [PYTHON, str(DAEMON_SCRIPT)],
        stdout=log,
        stderr=log,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        env={**os.environ},
    )


def wait_for_daemon(timeout: float):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ping():
            return True
        time.sleep(0.3)
    return False


def query_daemon(prompt: str, top_k: int, min_score: float, session_id: str = "") -> dict:
    req = json.dumps({
        "prompt": prompt,
        "top_k": top_k,
        "min_score": min_score,
        "session_id": session_id,
    }) + "\n"
    s = connect(timeout=60.0)
    try:
        s.sendall(req.encode("utf-8"))
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


def format_context(hits: list) -> str:
    if not hits:
        return ""
    summary = ", ".join(
        f"`{h.get('name','?')}` ({h.get('score',0):.2f})" for h in hits
    )
    lines = [
        f"**superskillret retrieved top-{len(hits)}:** {summary}",
        "",
        "Before answering, begin your response with a one-line notice:",
        f"> _superskillret: using {summary}_",
        "",
        "Then use the skills below as authoritative reference material for the user's request.",
        "",
        "---",
        "",
        "# Relevant skills retrieved by superskillret",
        "",
    ]
    for i, h in enumerate(hits, 1):
        score = h.get("score", 0.0)
        lines.append(f"## {i}. {h.get('name','?')}  (score={score:.3f})")
        src = h.get("source_url") or h.get("repo") or h.get("namespace") or ""
        if src:
            lines.append(f"_source: {src}_")
        lines.append("")
        lines.append(h.get("body") or h.get("description") or "")
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def emit(context: str):
    json.dump({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    }, sys.stdout)


def _is_installing() -> bool:
    """True if bootstrap.sh has kicked off install.sh and it's still running."""
    if not INSTALL_LOCK.exists():
        return False
    try:
        pid = int(INSTALL_LOCK.read_text().strip())
    except Exception:
        return False
    try:
        os.kill(pid, 0)  # signal 0 == probe
        return True
    except (OSError, ProcessLookupError):
        return False


def _trigger_install() -> bool:
    """Lazy-fork install.sh from the hook when bootstrap.sh hasn't run yet.

    Happens after `/plugin install` / `/plugin update` without a fresh CC
    session — SessionStart wasn't fired, so the user would otherwise be
    stuck on the "not yet installed" notice forever. We record the spawned
    PID into INSTALL_LOCK so subsequent prompts route to the "still
    installing" wait message instead of double-forking.
    """
    if not INSTALL_SCRIPT.exists():
        return False
    try:
        log = open(INSTALL_LOG, "a")
        proc = subprocess.Popen(
            ["bash", str(INSTALL_SCRIPT)],
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env={**os.environ},
        )
        try:
            INSTALL_LOCK.write_text(str(proc.pid))
        except Exception:
            pass
        sys.stderr.write(
            f"superskillret: lazy-spawned install.sh (pid {proc.pid}) — "
            f"tail {INSTALL_LOG} for progress\n"
        )
        return True
    except Exception as e:
        sys.stderr.write(f"superskillret: lazy-spawn failed: {e}\n")
        return False


def _install_wait_message() -> str:
    return (
        "> **superskillret: first-time setup is still running in the background.**\n"
        "> \n"
        "> It is downloading the ONNX INT8 encoder (~598 MB) and the prebuilt skill\n"
        "> index (~194 MB) from Hugging Face. This typically takes **1–2 minutes**\n"
        "> on a reasonable connection, longer on slow networks or first-time pip\n"
        "> installs. Please wait for it to finish and then retry your prompt — skill\n"
        "> retrieval will activate automatically on the next send.\n"
        "> \n"
        f"> Follow progress: `tail -f {INSTALL_LOG}`\n"
        "> Check status any time with `/superskillret:status`.\n"
    )


def _install_missing_message() -> str:
    return (
        "> **superskillret: not yet installed.**\n"
        "> \n"
        "> The plugin's Python environment and model files are missing. Run the\n"
        f"> one-time setup once:\n"
        "> \n"
        f"> ```bash\n"
        f"> bash {ROOT}/scripts/install.sh\n"
        f"> ```\n"
        "> \n"
        "> This downloads the ONNX INT8 encoder (~598 MB) plus the prebuilt skill\n"
        "> index (~194 MB). After it completes, skill retrieval activates on your\n"
        "> next user prompt — no config change needed.\n"
    )


def main():
    if DISABLED:
        emit("")
        return

    try:
        payload = json.load(sys.stdin)
    except Exception:
        emit("")
        return

    prompt = payload.get("prompt") or payload.get("user_prompt") or ""
    session_id = payload.get("session_id") or ""
    if not prompt.strip():
        emit("")
        return

    # First-run: background install kicked off by bootstrap.sh is still running.
    # Tell the user to wait rather than trying to spawn a daemon that would fail.
    if _is_installing():
        emit(_install_wait_message())
        return

    # No venv yet — bootstrap.sh may not have run (e.g. /plugin install or
    # /plugin update without a fresh CC session, so SessionStart never
    # fired). Lazy-fork install.sh ourselves so the user doesn't have to
    # restart Claude Code. Re-check _is_installing() afterward so we still
    # show the wait notice instead of trying to spawn the daemon yet.
    if not VENV_PYTHON.exists() and not INSTALL_DONE.exists():
        if not _is_installing():
            _trigger_install()
        emit(_install_wait_message())
        return

    if not ping():
        spawn_daemon()
        if not wait_for_daemon(SPAWN_WAIT):
            sys.stderr.write("superskillret: daemon failed to start in time\n")
            emit("")
            return

    try:
        result = query_daemon(prompt, TOP_K, MIN_SCORE, session_id=session_id)
    except Exception as e:
        sys.stderr.write(f"superskillret: query failed: {e}\n")
        emit("")
        return

    if result.get("error"):
        sys.stderr.write(f"superskillret: daemon error: {result['error']}\n")
        emit("")
        return

    hits = result.get("hits", [])
    skipped_seen = int(result.get("skipped_seen", 0))
    context = format_context(hits)
    latency = result.get("latency_s", 0)
    if context:
        dedup_note = f", {skipped_seen} skipped (already seen this session)" if skipped_seen else ""
        context += f"\n<!-- superskillret: {len(hits)} hit(s){dedup_note}, {latency:.2f}s -->\n"

    if hits:
        summary = " | ".join(f"{h.get('name','?')} ({h.get('score',0):.2f})" for h in hits)
        dedup_note = f" ({skipped_seen} dedup-skipped)" if skipped_seen else ""
        sys.stderr.write(
            f"superskillret: retrieved top-{len(hits)} in {latency:.2f}s{dedup_note} → {summary}\n"
        )
    else:
        sys.stderr.write(
            f"superskillret: no hits above min_score={MIN_SCORE}\n"
        )
    emit(context)


if __name__ == "__main__":
    main()
