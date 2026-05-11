"""CLI entry point for /superskillret:add.

Reads a SKILL.md path (or '-' for stdin), validates it locally, then asks the
daemon to encode + persist via the {"op": "add_skill", ...} protocol.

Exits 0 on success, 1 on validation/daemon error. Prints a human-readable
summary on stdout that the slash command surfaces to Claude.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from skill_validator import validate_skill_file, validate_skill_text


SOCKET_PATH = os.environ.get("SUPERSKILLRET_SOCKET", "/tmp/superskillret.sock")
DAEMON_LOG = os.environ.get("SUPERSKILLRET_LOG", "/tmp/superskillret.log")
DAEMON_SCRIPT = ROOT / "scripts" / "daemon.py"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
DAEMON_SPAWN_WAIT = float(os.environ.get("SUPERSKILLRET_SPAWN_WAIT", "60"))

# Convention: users keep their personal skills here so `/superskillret:add`
# with no argument can pick them all up automatically.
DEFAULT_SKILL_DIR = Path(
    os.environ.get("SUPERSKILLRET_USER_SKILLS_DIR",
                   str(Path.home() / ".superskillret" / "skills"))
).expanduser()


def _ping_daemon(timeout: float = 1.0) -> bool:
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


def _spawn_daemon() -> None:
    """Lazy-fork the retrieval daemon in the background.

    Mirrors retrieve.py's `spawn_daemon()` so slash commands work even when
    no UserPromptSubmit hook has fired yet (e.g. right after /reload-plugins
    or /plugin update). Uses the plugin venv python when available.
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


def _ensure_daemon(spawn_wait: float = DAEMON_SPAWN_WAIT) -> None:
    """Ensure the daemon is reachable, spawning it if not. Raises on failure."""
    if _ping_daemon():
        return
    if not DAEMON_SCRIPT.exists():
        raise RuntimeError(
            f"daemon script not found at {DAEMON_SCRIPT}; "
            "is this the plugin install dir?"
        )
    if not VENV_PYTHON.exists():
        raise RuntimeError(
            f"venv python not found at {VENV_PYTHON}; "
            "run scripts/install.sh first or send any user prompt to "
            "Claude Code so the SessionStart bootstrap can set it up"
        )
    _spawn_daemon()
    deadline = time.time() + spawn_wait
    while time.time() < deadline:
        if _ping_daemon():
            return
        time.sleep(0.3)
    raise RuntimeError(
        f"daemon spawned but never came up in {spawn_wait:.0f}s — "
        f"check {DAEMON_LOG} for tracebacks"
    )


def daemon_request(req: dict, timeout: float = 30.0) -> dict:
    _ensure_daemon()
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


def _process_one(path_or_stdin: str, force: bool, json_out: bool) -> int:
    """Validate + add a single SKILL.md (or stdin). Returns 0 on success."""
    if path_or_stdin == "-":
        text = sys.stdin.read()
        result = validate_skill_text(text, source="<stdin>")
        source_label = "<stdin>"
    else:
        p = Path(path_or_stdin).expanduser().resolve()
        result = validate_skill_file(p)
        source_label = str(p)

    if not result.valid:
        if json_out:
            print(json.dumps({"ok": False, "stage": "validation",
                              "path": source_label,
                              "errors": result.errors,
                              "warnings": result.warnings},
                             ensure_ascii=False))
        else:
            print(f"✗ {source_label}")
            for e in result.errors:
                print(f"    ERR: {e}")
            for w in result.warnings:
                print(f"    WARN: {w}")
        return 1

    try:
        reply = daemon_request({
            "op": "add_skill",
            "name": result.name,
            "description": result.description,
            "body": result.body,
            "force": force,
            "source": source_label,
        }, timeout=60.0)
    except Exception as e:
        if json_out:
            print(json.dumps({"ok": False, "stage": "daemon",
                              "path": source_label,
                              "error": str(e)}, ensure_ascii=False))
        else:
            print(f"✗ {source_label}")
            print(f"    daemon error: {e}")
        return 1

    if not reply.get("ok"):
        # Skip-with-notice when the skill is already in the user pool — this
        # is the common case for re-running batch adds and shouldn't count
        # as a hard failure. Caller distinguishes via the return code.
        if reply.get("reason") == "duplicate":
            if json_out:
                print(json.dumps({"ok": True, "skipped": True, "reason": "duplicate",
                                  "path": source_label, "name": result.name},
                                 ensure_ascii=False))
            else:
                print(f"○ Skipped '{result.name}' — already in user pool "
                      f"(row {reply.get('existing_global_row')})  ({source_label})")
            return 2  # 2 = skipped (not 0=added, not 1=error)
        if json_out:
            print(json.dumps({"ok": False, "stage": "daemon",
                              "path": source_label,
                              **reply}, ensure_ascii=False))
        else:
            print(f"✗ {source_label}")
            print(f"    daemon refused: {reply.get('error', reply)}")
        return 1

    if json_out:
        out = {"ok": True, "path": source_label, "name": result.name,
               "warnings": result.warnings, **reply}
        print(json.dumps(out, ensure_ascii=False))
        return 0

    print(f"✓ Added '{result.name}'  ({source_label})")
    print(f"    user-pool row: {reply.get('user_row_index')}")
    print(f"    global row:    {reply.get('global_row_index')}")
    print(f"    encode time:   {reply.get('encode_ms', 0):.0f} ms")

    quality = reply.get("quality") or {}
    near_name = quality.get("nearest_existing_name")
    near_sim = quality.get("nearest_existing_score")
    if near_name is not None and near_sim is not None:
        flag = " ← near-duplicate, consider editing instead" \
            if near_sim > 0.85 else ""
        print(f"    nearest existing: {near_name} ({near_sim:.3f}){flag}")
    self_score = quality.get("self_retrieval_score")
    if self_score is not None:
        flag = " ← description may be too generic" if self_score < 0.6 else ""
        print(f"    self-retrieval:   {self_score:.3f}{flag}")

    for w in result.warnings:
        print(f"    WARN: {w}")
    for w in reply.get("warnings", []):
        print(f"    WARN: {w}")

    return 0


def _resolve_arg(raw: str) -> tuple[Path | None, str]:
    """Map a user-supplied argument to a real filesystem path.

    Resolution order:
      1. If raw looks like a path (contains '/' or '~' or '.', or exists) →
         expand and use as-is.
      2. Otherwise treat as a bare skill name. Try
         DEFAULT_SKILL_DIR/<name>/SKILL.md, then DEFAULT_SKILL_DIR/<name>.md.

    Returns (resolved_path, mode) where mode is "file", "dir", or "missing".
    """
    p = Path(raw).expanduser()
    if "/" in raw or raw.startswith(("~", ".")) or p.exists():
        if not p.exists():
            return p, "missing"
        return p, ("dir" if p.is_dir() else "file")

    # Bare name → check well-known layout under DEFAULT_SKILL_DIR.
    candidate_dir = DEFAULT_SKILL_DIR / raw / "SKILL.md"
    if candidate_dir.exists():
        return candidate_dir, "file"
    candidate_flat = DEFAULT_SKILL_DIR / f"{raw}.md"
    if candidate_flat.exists():
        return candidate_flat, "file"
    return DEFAULT_SKILL_DIR / raw, "missing"


def _batch_add(md_files: list[Path], force: bool, json_out: bool) -> int:
    added = skipped = failed = 0
    if not json_out and md_files:
        print(f"Adding {len(md_files)} skill file(s)...")
        print()
    for md in md_files:
        rc = _process_one(str(md), force, json_out)
        if rc == 0:
            added += 1
        elif rc == 2:
            skipped += 1
        else:
            failed += 1
        if not json_out:
            print()
    if not json_out:
        parts = [f"{added} added"]
        if skipped:
            parts.append(f"{skipped} skipped (already in pool)")
        if failed:
            parts.append(f"{failed} failed")
        print(f"Batch done: {', '.join(parts)} of {len(md_files)} total")
    return 0 if failed == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Add a SKILL.md to superskillret.\n\n"
                    "Resolution:\n"
                    "  no arg              → scan {DEFAULT}/*.md (and */SKILL.md)\n"
                    "  bare name           → look up under {DEFAULT}\n"
                    "  path to file        → add that file\n"
                    "  path to directory   → batch-add all *.md inside\n"
                    "  '-'                 → read SKILL.md from stdin".replace(
                        "{DEFAULT}", str(DEFAULT_SKILL_DIR)
                    ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "path",
        nargs="?",
        default=None,
        help="path / name / '-' (omit to scan the default skill directory)",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Overwrite a user-pool entry with the same name instead of skipping",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of human-readable text",
    )
    args = ap.parse_args()

    # Case 1: no argument → scan the default user-skills directory.
    if not args.path:
        if not DEFAULT_SKILL_DIR.exists():
            msg = (f"default skill directory does not exist yet: "
                   f"{DEFAULT_SKILL_DIR}\n"
                   f"Create it and drop SKILL.md files in, or pass an explicit path.")
            print(json.dumps({"ok": False, "error": msg}) if args.json else f"✗ {msg}")
            return 1
        md_files: list[Path] = sorted(DEFAULT_SKILL_DIR.glob("*.md"))
        md_files += sorted(DEFAULT_SKILL_DIR.glob("*/SKILL.md"))
        if not md_files:
            msg = f"no *.md files under {DEFAULT_SKILL_DIR}"
            print(json.dumps({"ok": False, "error": msg}) if args.json else f"✗ {msg}")
            return 1
        return _batch_add(md_files, args.force, args.json)

    # Case 2: stdin.
    if args.path == "-":
        return _process_one("-", args.force, args.json)

    # Case 3: path or bare name.
    resolved, mode = _resolve_arg(args.path)

    if mode == "missing":
        msg = (f"could not find a SKILL.md for {args.path!r}\n"
               f"Looked at: {resolved}")
        print(json.dumps({"ok": False, "error": msg}) if args.json else f"✗ {msg}")
        return 1

    if mode == "file":
        return _process_one(str(resolved), args.force, args.json)

    # mode == "dir"
    md_files = sorted(resolved.glob("*.md"))
    md_files += sorted(resolved.glob("*/SKILL.md"))
    if not md_files:
        msg = f"no *.md files in directory: {resolved}"
        print(json.dumps({"ok": False, "error": msg}) if args.json else f"✗ {msg}")
        return 1
    return _batch_add(md_files, args.force, args.json)


if __name__ == "__main__":
    raise SystemExit(main())
