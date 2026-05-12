"""CLI entry for /superskillret:setup.

Brings the plugin to a fully-ready state synchronously, with live progress
output. Four phases:

    [1/4] auto-mode   — append a permissive entry to ~/.claude/settings.json
                        so the auto-mode classifier doesn't silently block
                        our hooks. Idempotent and safe when not in auto-mode.
    [2/4] install.sh  — only if `.installed` marker is missing
    [3/4] daemon up   — spawn and wait for socket bind + first ping
    [4/4] verify      — sample retrieval to confirm end-to-end

Idempotent: re-running on a ready plugin reports "Already ready" without
side effects.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

INSTALL_SCRIPT = ROOT / "scripts" / "install.sh"
INSTALL_DONE = ROOT / ".installed"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"


def _line(char: str = "-", width: int = 64) -> None:
    print(char * width)


def _phase(num: int, total: int, title: str) -> None:
    print()
    print(f"[{num}/{total}] {title}")
    _line()


def run_install_live() -> int:
    """Stream install.sh stdout to our stdout so the user sees real-time
    log lines instead of staring at nothing for 1-2 minutes."""
    proc = subprocess.Popen(
        ["bash", str(INSTALL_SCRIPT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        text=True,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        # Strip trailing CR (tqdm progress bars use \r) for readability
        sys.stdout.write(line)
        sys.stdout.flush()
    proc.wait()
    return proc.returncode


def authorize_auto_mode() -> None:
    """Append a permissive entry to ~/.claude/settings.json autoMode.allow
    so Claude Code's auto-mode classifier doesn't silently block our
    UserPromptSubmit hook (`python3 retrieve.py`) and setup_cli.py
    invocations.

    Idempotent: re-running finds the existing entry and exits.
    Safe: if settings.json is missing or has invalid JSON, we skip with a
    warning rather than corrupt the user's config. Atomic write via temp.
    """
    import json
    settings = Path.home() / ".claude" / "settings.json"
    if not settings.exists():
        print("  ~/.claude/settings.json not found — skipping (auto-mode not configured)")
        return
    try:
        data = json.loads(settings.read_text())
    except json.JSONDecodeError as e:
        print(f"  WARNING: settings.json has invalid JSON ({e}) — skipping authorization")
        return

    entry = (
        "Running superskillret hooks is allowed: the plugin runs a local "
        "Python daemon that injects relevant skills from a 16,783-skill "
        "corpus into prompts. It downloads ONNX models from Hugging Face "
        "Hub on first install and communicates with retrieve.py via "
        "/tmp/superskillret.sock — no other network access."
    )

    am = data.setdefault("autoMode", {})
    allow = am.setdefault("allow", ["$defaults"])
    if any("superskillret" in str(x).lower() for x in allow):
        print("  ✓ autoMode.allow already contains a superskillret entry")
        return

    if "$defaults" not in allow:
        allow.insert(0, "$defaults")
    allow.append(entry)

    tmp = settings.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(settings)
    print(f"  ✓ Added autoMode.allow entry to {settings}")


def main() -> int:
    print("=" * 64)
    print("  superskillret :: setup")
    print("=" * 64)

    # ---- Phase 1: authorize under auto-mode ----
    _phase(1, 4, "Authorizing under Claude Code auto-mode classifier")
    authorize_auto_mode()

    # ---- Phase 2: install.sh ----
    if INSTALL_DONE.exists() and VENV_PYTHON.exists():
        _phase(2, 4, "✓ Already installed (.installed marker + venv present)")
        print("    skipping install.sh")
    else:
        _phase(2, 4, "Running install.sh (first-time setup, ~1-2 min)")
        t0 = time.time()
        rc = run_install_live()
        elapsed = time.time() - t0
        if rc != 0:
            _line()
            print(f"\n✗ install.sh failed (exit {rc}) after {elapsed:.0f}s")
            print(f"  Check /tmp/superskillret-install.log for details")
            return 1
        _line()
        print(f"✓ install.sh complete ({elapsed:.0f}s)")

    # ---- Phase 3: daemon ----
    # Import deferred so the system python that runs this script doesn't
    # need numpy / onnxruntime in scope — daemon_client itself uses only
    # stdlib (socket, subprocess, json), and the daemon process it spawns
    # is the venv python.
    from daemon_client import ping_daemon, ensure_daemon

    _phase(3, 4, "Ensuring daemon is up")
    if ping_daemon():
        print("✓ Daemon already running")
    else:
        print("  spawning daemon (cold start ~15-30s, ONNX encoder + index load)")
        t0 = time.time()
        try:
            ensure_daemon(spawn_wait=90)
            print(f"✓ Daemon up after {time.time() - t0:.0f}s")
        except Exception as e:
            print(f"✗ Daemon failed to start: {e}")
            print(f"  Check /tmp/superskillret.log for tracebacks")
            return 1

    # ---- Phase 4: verify ----
    from daemon_client import daemon_request

    _phase(4, 4, "Verifying end-to-end retrieval")
    health = daemon_request({"op": "ping"}, timeout=5)
    print(f"  ping: ok, n_skills={health.get('n_skills', '?')}, "
          f"system={health.get('system_count', '?')}, "
          f"user={health.get('user_count', '?')}, "
          f"backend={health.get('backend', '?')}")

    t0 = time.time()
    result = daemon_request({
        "prompt": "JWT token authentication",
        "top_k": 3,
        "min_score": 0.30,
        "session_id": "superskillret-setup-verify",
    }, timeout=15)
    elapsed_ms = (time.time() - t0) * 1000
    hits = result.get("hits", [])
    print(f"  sample retrieve: {len(hits)} hits in {elapsed_ms:.0f} ms")
    for h in hits:
        print(f"    - {h.get('name', '?'):40s} {h.get('score', 0):.3f}")

    print()
    _line("=")
    print("  ✓ Ready. Retrieval is live on every subsequent user prompt.")
    print()
    print("  Quick reference:")
    print("    /superskillret:add <path>   register a custom SKILL.md")
    print("    /superskillret:list         show your registered skills")
    print("    /superskillret:status       daemon + last-retrieve status")
    print("    /superskillret:stop         kill daemon (lazy respawn on next prompt)")
    _line("=")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
