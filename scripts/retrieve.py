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
  SUPERSKILLRET_TOP_K       default 3
  SUPERSKILLRET_MIN_SCORE   default 0.25
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

SOCKET_PATH = os.environ.get("SUPERSKILLRET_SOCKET", "/tmp/superskillret.sock")
TOP_K = int(os.environ.get("SUPERSKILLRET_TOP_K", "5"))
MIN_SCORE = float(os.environ.get("SUPERSKILLRET_MIN_SCORE", "0.25"))
PYTHON = os.environ.get("SUPERSKILLRET_PYTHON", sys.executable)
SPAWN_WAIT = float(os.environ.get("SUPERSKILLRET_SPAWN_WAIT", "90"))
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


def query_daemon(prompt: str, top_k: int, min_score: float) -> dict:
    req = json.dumps({"prompt": prompt, "top_k": top_k, "min_score": min_score}) + "\n"
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
    if not prompt.strip():
        emit("")
        return

    if not ping():
        spawn_daemon()
        if not wait_for_daemon(SPAWN_WAIT):
            sys.stderr.write("superskillret: daemon failed to start in time\n")
            emit("")
            return

    try:
        result = query_daemon(prompt, TOP_K, MIN_SCORE)
    except Exception as e:
        sys.stderr.write(f"superskillret: query failed: {e}\n")
        emit("")
        return

    if result.get("error"):
        sys.stderr.write(f"superskillret: daemon error: {result['error']}\n")
        emit("")
        return

    hits = result.get("hits", [])
    context = format_context(hits)
    latency = result.get("latency_s", 0)
    if context:
        context += f"\n<!-- superskillret: {len(hits)} hit(s), {latency:.2f}s -->\n"

    if hits:
        summary = " | ".join(f"{h.get('name','?')} ({h.get('score',0):.2f})" for h in hits)
        sys.stderr.write(
            f"superskillret: retrieved top-{len(hits)} in {latency:.2f}s → {summary}\n"
        )
    else:
        sys.stderr.write(
            f"superskillret: no hits above min_score={MIN_SCORE}\n"
        )
    emit(context)


if __name__ == "__main__":
    main()
