#!/usr/bin/env bash
# superskillret: first-time setup
#
# - Creates a local venv in .venv/
# - Installs torch (CPU wheel) + sentence-transformers + datasets + numpy
# - Downloads the SKILLRET skill pool (16,783 skills) as skill_pool/skills.jsonl
# - Downloads the SKILLRET embedding model (~1.2GB) via Hugging Face cache
# - Fetches the prebuilt embedding index from Hugging Face Hub; falls back to
#   building it locally if the prebuilt index is unavailable.
#
# Re-running is safe: steps are skipped when their outputs already exist.
# Set FORCE=1 to rebuild from scratch.
# Set SUPERSKILLRET_INDEX_REPO to override the prebuilt-index dataset repo.
# Set SUPERSKILLRET_SKIP_PREBUILT=1 to always build the index locally.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VENV="$ROOT/.venv"
PY="$VENV/bin/python"
PIP="$VENV/bin/pip"

INDEX_REPO="${SUPERSKILLRET_INDEX_REPO:-youngryankim/superskillret-index}"

log() { printf '[superskillret] %s\n' "$*"; }

# 1. Python
if ! command -v python3 >/dev/null 2>&1; then
  log "ERROR: python3 not found. Install Python 3.10+ first."
  exit 1
fi
PYVER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
log "using system python3 $PYVER"

# 2. venv
if [ ! -d "$VENV" ] || [ "${FORCE:-0}" = "1" ]; then
  log "creating venv at $VENV"
  rm -rf "$VENV"
  python3 -m venv "$VENV"
else
  log "venv exists, reusing ($VENV)"
fi

# 3. deps
log "upgrading pip"
"$PIP" install --quiet --upgrade pip

# Install CPU-only torch by default. GPU users can reinstall torch with CUDA
# afterwards; sentence-transformers will pick up whichever torch is installed.
if ! "$PY" -c "import torch" 2>/dev/null; then
  log "installing torch (CPU wheel)"
  "$PIP" install --quiet torch --index-url https://download.pytorch.org/whl/cpu
else
  log "torch already installed"
fi

log "installing sentence-transformers, datasets, numpy, huggingface_hub"
"$PIP" install --quiet "sentence-transformers>=3.0" "datasets>=3.0" "numpy>=1.26" "huggingface_hub>=0.24"

# 4. embedding index — try prebuilt first, fall back to local build
EMB="$ROOT/cache/skill_embeddings.npy"
META="$ROOT/cache/skill_metadata.jsonl"
VER="$ROOT/cache/VERSION"
mkdir -p "$ROOT/cache"

need_index=0
if [ ! -s "$EMB" ] || [ ! -s "$META" ] || [ "${FORCE:-0}" = "1" ]; then
  need_index=1
fi

if [ "$need_index" = "1" ] && [ "${SUPERSKILLRET_SKIP_PREBUILT:-0}" != "1" ]; then
  log "trying to fetch prebuilt index from dataset: $INDEX_REPO"
  if ROOT="$ROOT" REPO="$INDEX_REPO" "$PY" - <<'PYEOF'
import os, sys
from pathlib import Path
from huggingface_hub import snapshot_download
from huggingface_hub.errors import HfHubHTTPError, RepositoryNotFoundError, GatedRepoError

root = Path(os.environ["ROOT"])
repo = os.environ["REPO"]
try:
    snapshot_download(
        repo_id=repo,
        repo_type="dataset",
        local_dir=str(root / "cache"),
        allow_patterns=["skill_embeddings.npy", "skill_metadata.jsonl", "VERSION", "README.md"],
    )
    print("[superskillret] prebuilt index downloaded")
except (HfHubHTTPError, RepositoryNotFoundError, GatedRepoError) as e:
    print(f"[superskillret] prebuilt fetch failed: {e}", file=sys.stderr)
    sys.exit(2)
except Exception as e:
    print(f"[superskillret] prebuilt fetch failed: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(2)
PYEOF
  then
    need_index=0
    if [ -f "$VER" ]; then
      log "prebuilt index version: $(cat "$VER")"
    fi
  else
    log "prebuilt index not available (private repo? no token? offline?) — will build locally"
  fi
fi

# 5. skill pool — needed either to build index locally, OR so custom workflows
# (scripts/build_index.py, scripts/publish_index.py) can re-run. If we already
# have the prebuilt index and the pool is missing, skip the 300MB download.
POOL="$ROOT/skill_pool/skills.jsonl"
if [ "$need_index" = "1" ] && { [ ! -s "$POOL" ] || [ "${FORCE:-0}" = "1" ]; }; then
  log "downloading SKILLRET skill pool (~300MB, required for local index build)"
  mkdir -p "$ROOT/skill_pool"
  ROOT="$ROOT" "$PY" - <<'PYEOF'
from datasets import load_dataset
import json, os
ds = load_dataset("ThakiCloud/SKILLRET", "skills", split="train+test")
out = os.path.join(os.environ["ROOT"], "skill_pool", "skills.jsonl")
with open(out, "w", encoding="utf-8") as f:
    for row in ds:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
print(f"wrote {len(ds)} skills")
PYEOF
elif [ -s "$POOL" ]; then
  log "skill pool already present ($(wc -l <"$POOL") skills)"
else
  log "skill pool not fetched (prebuilt index is enough for runtime retrieval)"
fi

# 6. build index locally if we still need it
if [ "$need_index" = "1" ]; then
  log "building embedding index (slow on CPU; ~30-60 min)"
  "$PY" "$ROOT/scripts/build_index.py" --batch-size 32
fi

log "install complete."
log "  plugin python:  $PY"
log "  socket path:    ${SUPERSKILLRET_SOCKET:-/tmp/superskillret.sock}"
log "  index:          $EMB ($(du -h "$EMB" 2>/dev/null | cut -f1))"
log "  metadata:       $META ($(du -h "$META" 2>/dev/null | cut -f1))"
log ""
log "Claude Code hook is pre-wired in hooks/hooks.json using:"
log "  \${CLAUDE_PLUGIN_ROOT}/.venv/bin/python \${CLAUDE_PLUGIN_ROOT}/scripts/retrieve.py"
