"""Build embedding index for the SKILLRET skill pool.

Reads skill_pool/skills.jsonl, encodes each skill's (name + description + body),
and saves:
  - cache/skill_embeddings.npy     (N, 1024) float16
  - cache/skill_metadata.jsonl     one record per line, aligned with embeddings

The full-context scheme (v2+) concatenates body so retrieval can match
keywords that appear only inside the skill body, not just its
name/description summary. Must stay in sync with daemon.RetrievalServer
._encode_skill so incremental adds embed into the same vector space.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import torch
from sentence_transformers import SentenceTransformer

ROOT = Path(__file__).resolve().parent.parent
POOL_PATH = ROOT / "skill_pool" / "skills.jsonl"
EMB_PATH = ROOT / "cache" / "skill_embeddings.npy"
META_PATH = ROOT / "cache" / "skill_metadata.jsonl"

MODEL_NAME = "ThakiCloud/SkillRet-Embedding-0.6B"


def skill_text(rec: dict) -> str:
    name = rec.get("name", "")
    desc = rec.get("description", "")
    body = rec.get("body", "")
    return f"{name} | {desc} | {body}".strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = ap.parse_args()

    if not POOL_PATH.exists():
        print(f"ERROR: {POOL_PATH} not found. Run the dataset download step first.", file=sys.stderr)
        sys.exit(1)

    records = []
    with POOL_PATH.open(encoding="utf-8") as f:
        for line in f:
            records.append(json.loads(line))
    print(f"Loaded {len(records)} skills from {POOL_PATH}")

    texts = [skill_text(r) for r in records]

    print(f"Loading model on {args.device} ...")
    t0 = time.time()
    model = SentenceTransformer(MODEL_NAME, trust_remote_code=False, device=args.device)
    print(f"Model loaded in {time.time()-t0:.1f}s")

    print(f"Encoding {len(texts)} skills (batch={args.batch_size}) ...")
    t0 = time.time()
    embeddings = model.encode(
        texts,
        batch_size=args.batch_size,
        normalize_embeddings=True,
        show_progress_bar=True,
        convert_to_numpy=True,
    )
    print(f"Encoded in {time.time()-t0:.1f}s | shape={embeddings.shape} dtype={embeddings.dtype}")

    embeddings_fp16 = embeddings.astype(np.float16)
    os.makedirs(EMB_PATH.parent, exist_ok=True)
    np.save(EMB_PATH, embeddings_fp16)
    print(f"Saved embeddings to {EMB_PATH} ({EMB_PATH.stat().st_size/1e6:.1f} MB)")

    with META_PATH.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps({
                "id": r.get("id"),
                "name": r.get("name"),
                "namespace": r.get("namespace"),
                "description": r.get("description"),
                "repo": r.get("repo"),
                "source_url": r.get("source_url"),
                "body": r.get("body"),
            }, ensure_ascii=False) + "\n")
    print(f"Saved metadata to {META_PATH} ({META_PATH.stat().st_size/1e6:.1f} MB)")


if __name__ == "__main__":
    main()
