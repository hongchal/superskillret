"""Publish the prebuilt embedding index to a Hugging Face dataset repo.

Usage:
    HF_TOKEN=hf_xxx python scripts/publish_index.py \
        --repo ThakiCloud/superskillret-index --private

Uploads:
    cache/skill_embeddings.npy
    cache/skill_metadata.jsonl
    cache/VERSION
    (plus an auto-generated README.md describing the index)
"""

import argparse
import os
import sys
from pathlib import Path

from huggingface_hub import HfApi, create_repo

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"


def dataset_readme(version: str, n_skills: int, dim: int) -> str:
    return f"""---
license: mit
tags:
  - embeddings
  - skill-retrieval
  - claude-code
---

# superskillret prebuilt index

Prebuilt embedding index for the [superskillret](../superskillret) Claude Code plugin.

- **Version:** {version}
- **Corpus:** [`ThakiCloud/SKILLRET`](https://huggingface.co/datasets/ThakiCloud/SKILLRET) (`train+test`)
- **Encoder:** [`ThakiCloud/SkillRet-Embedding-0.6B`](https://huggingface.co/ThakiCloud/SkillRet-Embedding-0.6B)
- **Skills indexed:** {n_skills}
- **Embedding dim:** {dim}
- **Normalized:** yes (inner product = cosine similarity)

## Files

| File | Description |
|---|---|
| `skill_embeddings.npy` | FP16 numpy array of shape `({n_skills}, {dim})` |
| `skill_metadata.jsonl` | one JSON record per row, aligned with embeddings (includes `name`, `description`, `body`, `source_url`) |
| `VERSION` | integer version tag; bumped when the corpus or encoder changes |

## Usage

```python
from huggingface_hub import snapshot_download
snapshot_download(repo_id="ThakiCloud/superskillret-index",
                  repo_type="dataset",
                  local_dir="cache/")
```

Downstream consumers should check `VERSION` against their cached copy before reusing local files.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="e.g. ThakiCloud/superskillret-index")
    ap.add_argument("--private", action="store_true")
    args = ap.parse_args()

    token = os.environ.get("HF_TOKEN")
    if not token:
        print("ERROR: HF_TOKEN env var is required.", file=sys.stderr)
        sys.exit(1)

    emb = CACHE / "skill_embeddings.npy"
    meta = CACHE / "skill_metadata.jsonl"
    version = (CACHE / "VERSION").read_text().strip()
    if not emb.exists() or not meta.exists():
        print(f"ERROR: missing {emb} or {meta}. Run build_index.py first.", file=sys.stderr)
        sys.exit(1)

    import numpy as np
    arr = np.load(emb, mmap_mode="r")
    n_skills, dim = arr.shape[0], arr.shape[1]

    api = HfApi(token=token)
    print(f"creating repo {args.repo} (private={args.private})")
    create_repo(args.repo, repo_type="dataset", private=args.private,
                token=token, exist_ok=True)

    readme = CACHE / "README.md"
    readme.write_text(dataset_readme(version, n_skills, dim))

    print(f"uploading cache/ -> {args.repo}")
    api.upload_folder(
        folder_path=str(CACHE),
        repo_id=args.repo,
        repo_type="dataset",
        allow_patterns=["skill_embeddings.npy", "skill_metadata.jsonl", "VERSION", "README.md"],
        commit_message=f"Publish index v{version} ({n_skills} skills, dim {dim})",
    )
    print(f"done -> https://huggingface.co/datasets/{args.repo}")


if __name__ == "__main__":
    main()
