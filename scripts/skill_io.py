"""Storage helpers for user-supplied skills.

User-added skills live in a separate pair of files so they survive system
index upgrades (the prebuilt index is overwritten by install.sh; user data
must not be).

    cache/user_skill_embeddings.npy        (N, 1024) float16
    cache/user_skill_metadata.jsonl        N lines, one JSON record each

Both files are written atomically: the .npy via temp-file + os.replace, and
the .jsonl via O_APPEND (atomic per-line for writes ≤ PIPE_BUF).
"""

from __future__ import annotations

import fcntl
import io
import json
import os
import time
from pathlib import Path
from typing import Iterator, Optional

import numpy as np


def _read_jsonl(path: Path) -> Iterator[dict]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue  # skip corrupt rows so a partial write doesn't brick


def load_user_index(
    emb_path: Path, meta_path: Path, embed_dim: int = 1024
) -> tuple[np.ndarray, list[dict]]:
    """Load the user index. Empty arrays if files don't exist yet."""
    if emb_path.exists():
        emb = np.load(emb_path)
    else:
        emb = np.zeros((0, embed_dim), dtype=np.float16)

    meta = list(_read_jsonl(meta_path))

    if emb.shape[0] != len(meta):
        raise RuntimeError(
            f"user index out of sync: {emb_path.name} has {emb.shape[0]} "
            f"rows but {meta_path.name} has {len(meta)} records"
        )
    return emb, meta


def append_user_skill(
    emb_path: Path,
    meta_path: Path,
    vec: np.ndarray,
    record: dict,
    embed_dim: int = 1024,
) -> int:
    """Atomically append a single skill to the user index. Returns row index
    (0-based, local to the user pool — NOT the global row).

    vec is expected to be a 1-D float16 array of length embed_dim, already
    L2-normalized by the caller.
    """
    if vec.shape != (embed_dim,):
        raise ValueError(
            f"embedding shape {vec.shape} != expected ({embed_dim},)"
        )
    if vec.dtype != np.float16:
        vec = vec.astype(np.float16)

    emb_path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing (or empty) embeddings; vstack with new row; atomic save.
    if emb_path.exists():
        existing = np.load(emb_path)
        if existing.shape[1] != embed_dim:
            raise RuntimeError(
                f"existing user index dim {existing.shape[1]} != {embed_dim}"
            )
        new_arr = np.vstack([existing, vec[None, :]])
    else:
        new_arr = vec[None, :].copy()

    tmp = emb_path.with_suffix(".npy.tmp")
    # Use an explicit file handle so np.save doesn't silently append ".npy"
    # to the temp filename (it would, because ".tmp" isn't a recognised
    # numpy extension), which would make the subsequent os.replace fail.
    with open(tmp, "wb") as f:
        np.save(f, new_arr)
    os.replace(tmp, emb_path)

    # Append one JSON line. POSIX guarantees atomicity for single-write
    # appends ≤ PIPE_BUF (typically 4 KB on macOS / Linux).
    line = json.dumps(record, ensure_ascii=False) + "\n"
    encoded = line.encode("utf-8")
    if len(encoded) > 4000:
        lock_path = meta_path.with_suffix(".jsonl.lock")
        with open(lock_path, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                existing_text = (
                    meta_path.read_text(encoding="utf-8") if meta_path.exists() else ""
                )
                tmp_meta = meta_path.with_suffix(".jsonl.tmp")
                tmp_meta.write_text(existing_text + line, encoding="utf-8")
                os.replace(tmp_meta, meta_path)
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)
    else:
        with meta_path.open("ab") as f:
            f.write(encoded)

    return new_arr.shape[0] - 1


def remove_user_skill_by_name(
    emb_path: Path, meta_path: Path, name: str, embed_dim: int = 1024
) -> bool:
    """Remove a user skill by name. Returns True if removed, False if not
    found. The .npy is rewritten without the removed row.
    """
    lock_path = meta_path.with_suffix(".jsonl.lock")
    with open(lock_path, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            emb, meta = load_user_index(emb_path, meta_path, embed_dim=embed_dim)
            keep_idx = [i for i, m in enumerate(meta) if m.get("name") != name]
            if len(keep_idx) == len(meta):
                return False

            if keep_idx:
                new_emb = emb[keep_idx]
                new_meta = [meta[i] for i in keep_idx]
            else:
                new_emb = np.zeros((0, embed_dim), dtype=np.float16)
                new_meta = []

            tmp = emb_path.with_suffix(".npy.tmp")
            with open(tmp, "wb") as f:
                np.save(f, new_emb)
            os.replace(tmp, emb_path)

            tmp_meta = meta_path.with_suffix(".jsonl.tmp")
            buf = io.StringIO()
            for m in new_meta:
                buf.write(json.dumps(m, ensure_ascii=False) + "\n")
            tmp_meta.write_text(buf.getvalue(), encoding="utf-8")
            os.replace(tmp_meta, meta_path)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)
    return True


def make_user_skill_record(
    name: str, description: str, body: str, source_url: str = ""
) -> dict:
    """Build a metadata record matching the schema of skill_metadata.jsonl."""
    return {
        "id": f"user-{name}-{int(time.time())}",
        "name": name,
        "namespace": "user",
        "description": description,
        "repo": "",
        "source_url": source_url,
        "body": body,
    }
