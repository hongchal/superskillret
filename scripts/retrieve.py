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
  SUPERSKILLRET_SCOPE       default "all". Restrict retrieval to a pool:
                              - "all"    : system + user (default)
                              - "user"   : only skills added via /superskillret:add
                              - "system" : only the prebuilt 16k+ system pool
                            Useful for project-specific curation: set
                            SUPERSKILLRET_SCOPE=user in a project's
                            .claude/settings.json env block to retrieve only
                            from your own curated skill set.
  SUPERSKILLRET_PYTHON      python used to spawn daemon (default current)
  SUPERSKILLRET_SPAWN_WAIT  seconds to wait for lazy daemon boot (default 90)
  SUPERSKILLRET_DISABLE     if "1", hook returns empty context
"""

# PEP 604 `X | Y` for type hints requires Python 3.10+. The plugin venv
# is whatever `python3 -m venv` produces (frequently 3.9 on macOS), so we
# defer all annotation evaluation to string form. This must follow the
# module docstring per PEP 236.
from __future__ import annotations

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
# Pool scope: "all" | "user" | "system". Anything else falls back to "all".
_RAW_SCOPE = (os.environ.get("SUPERSKILLRET_SCOPE", "all") or "all").lower()
SCOPE = _RAW_SCOPE if _RAW_SCOPE in ("all", "user", "system") else "all"
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


def ping() -> dict | None:
    """Return the daemon's health dict if reachable, else None.

    The daemon's ping response since v0.2.8 contains
    {ok, n_skills, system_count, user_count, embed_dim, backend}.
    """
    try:
        s = connect(timeout=1.0)
        s.sendall(b'{"op": "ping"}\n')
        reply = s.recv(2048)
        s.close()
        info = json.loads(reply.decode("utf-8"))
        return info if info.get("ok") else None
    except Exception:
        return None


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


def wait_for_daemon(timeout: float) -> dict | None:
    """Poll ping() until the daemon is up. Returns health dict on success,
    None on timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        info = ping()
        if info is not None:
            return info
        time.sleep(0.3)
    return None


def query_daemon(prompt: str, top_k: int, min_score: float,
                  session_id: str = "", scope: str = "all") -> dict:
    req = json.dumps({
        "prompt": prompt,
        "top_k": top_k,
        "min_score": min_score,
        "session_id": session_id,
        "scope": scope,
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


def _daemon_health_line(health: dict | None, latency_s: float | None = None,
                         scope: str = "all") -> str:
    """One human-readable line summarising daemon state for the user."""
    if health is None:
        return "_daemon: not reachable_"
    n_total = health.get("n_skills", 0)
    n_sys = health.get("system_count", 0)
    n_user = health.get("user_count", 0)
    backend = health.get("backend", "?")
    lat = f", {latency_s*1000:.0f} ms" if latency_s is not None else ""
    scope_part = f", scope={scope}" if scope and scope != "all" else ""
    return (
        f"_daemon: ready ({n_total} skills = {n_sys} system + {n_user} user, "
        f"backend={backend}{scope_part}{lat})_"
    )


def format_context(hits: list, health: dict | None = None,
                    latency_s: float | None = None,
                    scope: str = "all") -> str:
    if not hits:
        return ""
    summary = ", ".join(
        f"`{h.get('name','?')}` ({h.get('score',0):.2f})" for h in hits
    )
    names_csv = ", ".join(f"`{h.get('name','?')}`" for h in hits)
    health_line = _daemon_health_line(health, latency_s, scope=scope)
    lines = [
        f"**superskillret retrieved top-{len(hits)}:** {summary}",
        f"_{health_line.strip('_')}_",
        "",
        "Begin your response with two one-line notices: first the retrieved skills, then the daemon health line below.",
        f"> _superskillret retrieved: {summary}_",
        f"> {health_line}",
        "",
        "Use the skills below as authoritative reference material for the user's request when relevant.",
        "",
        "**After your answer, append exactly one final line declaring which of the retrieved skills you actually used to shape your response. This is required even when the answer feels unrelated — write `none` in that case.** Format:",
        f"> _superskillret used: <comma-separated names from {{{names_csv}}}, or `none`>_",
        "",
        "Definition: a skill counts as \"used\" if its body materially influenced this turn — directives followed, facts cited, or its structure adopted. Skills whose body you only acknowledged without applying do not count.",
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
    """True if bootstrap.sh has kicked off install.sh and it's still running.

    Returns False if INSTALL_DONE marker is already present (install.sh
    completed and the lock should have been cleared, but a stale lock can
    linger if install.sh crashed or PID re-use makes the alive-check
    misleading).
    """
    if INSTALL_DONE.exists():
        return False
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


def _read_install_log_tail(max_lines: int = 4) -> str:
    """Read the last few non-empty `[superskillret] ...` lines from the
    install log so the wait notice can show concrete progress."""
    try:
        with open(INSTALL_LOG, "r", encoding="utf-8", errors="replace") as f:
            # Read the whole file (it's tiny — a few KB at most) and pick
            # the last `max_lines` meaningful steps. Ignore progress-bar
            # CR-only updates and library deprecation warnings.
            lines = []
            for raw in f:
                line = raw.rstrip()
                if not line:
                    continue
                if "NotOpenSSLWarning" in line or "warnings.warn" in line:
                    continue
                # tqdm progress bars use \r so they end up on one giant
                # line — keep only the final segment.
                if "\r" in line:
                    line = line.split("\r")[-1].rstrip()
                lines.append(line)
            return "\n".join(lines[-max_lines:]) if lines else ""
    except FileNotFoundError:
        return ""


def _install_wait_message() -> str:
    log_tail = _read_install_log_tail()
    progress = (
        f"> \n"
        f"> Latest progress (from `{INSTALL_LOG}`):\n"
        f"> ```\n"
        + "".join(f"> {line}\n" for line in log_tail.split("\n"))
        + f"> ```\n"
    ) if log_tail else ""
    return (
        "> **superskillret: first-time setup is still running in the background.**\n"
        "> \n"
        "> It is downloading the ONNX INT8 encoder (~598 MB) and the prebuilt skill\n"
        "> index (~194 MB) from Hugging Face. This typically takes **1–2 minutes**\n"
        "> on a reasonable connection, longer on slow networks or first-time pip\n"
        "> installs. Please wait for it to finish and then retry your prompt — skill\n"
        "> retrieval will activate automatically on the next send.\n"
        + progress +
        "> \n"
        f"> Follow live: `tail -f {INSTALL_LOG}`\n"
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

    # Daemon ping returns the health dict on success, None otherwise.
    health = ping()
    if health is None:
        spawn_daemon()
        # Quick wait for visibility — if the daemon is doing a true cold
        # start (ONNX encoder load + index load = ~15-30s), we surface a
        # cold-start notice instead of silently blocking the CC hook.
        quick = float(os.environ.get("SUPERSKILLRET_QUICK_WAIT", "5"))
        health = wait_for_daemon(quick)
        if health is None:
            sys.stderr.write(
                f"superskillret: daemon cold-start in progress (>{quick:.0f}s); "
                "skipping retrieval this turn — should be ready next prompt\n"
            )
            emit(
                "> _superskillret: daemon cold-start in progress "
                f"(ONNX encoder + index loading, ~15–30 s). Retrieval will be "
                f"available on the next prompt. Follow `tail -f /tmp/superskillret.log` "
                f"for progress._\n"
            )
            return

    try:
        result = query_daemon(prompt, TOP_K, MIN_SCORE,
                              session_id=session_id, scope=SCOPE)
    except Exception as e:
        sys.stderr.write(f"superskillret: query failed: {e}\n")
        emit("> _superskillret: query failed. See stderr for details._\n")
        return

    if result.get("error"):
        sys.stderr.write(f"superskillret: daemon error: {result['error']}\n")
        emit("> _superskillret: daemon error. See stderr for details._\n")
        return

    hits = result.get("hits", [])
    skipped_seen = int(result.get("skipped_seen", 0))
    latency = result.get("latency_s", 0)
    pool_empty = bool(result.get("pool_empty"))
    context = format_context(hits, health=health, latency_s=latency, scope=SCOPE)

    # When there are zero hits, the framing returns empty; still surface a
    # daemon-status notice so the user can see retrieval was attempted.
    if not context:
        if pool_empty and SCOPE == "user":
            empty_reason = (
                "_superskillret: scope=user but no user-added skills yet — "
                "register some with `/superskillret:add <path>` or unset "
                "SUPERSKILLRET_SCOPE to fall back to the system pool_"
            )
        elif SCOPE != "all":
            empty_reason = (
                f"_superskillret: no skills in scope={SCOPE} above "
                f"MIN_SCORE={MIN_SCORE:.2f} matched_"
            )
        else:
            empty_reason = (
                f"_superskillret: no skills above MIN_SCORE={MIN_SCORE:.2f} matched_"
            )
        context = (
            f"> {_daemon_health_line(health, latency, scope=SCOPE)}\n"
            f"> {empty_reason}\n"
        )

    if hits:
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
