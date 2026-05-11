"""Smoke tests for skill_io. Uses a temp dir so it doesn't touch any real
index files.

Run from repo root:
    python tests/test_skill_io.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from skill_io import (  # noqa: E402
    append_user_skill,
    load_user_index,
    make_user_skill_record,
    remove_user_skill_by_name,
)


DIM = 1024


def _vec(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(DIM).astype(np.float32)
    v /= np.linalg.norm(v) + 1e-12
    return v.astype(np.float16)


class SkillIOTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="superskillret-io-"))
        self.emb_path = self.tmp / "user_skill_embeddings.npy"
        self.meta_path = self.tmp / "user_skill_metadata.jsonl"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_load_empty(self):
        emb, meta = load_user_index(self.emb_path, self.meta_path, embed_dim=DIM)
        self.assertEqual(emb.shape, (0, DIM))
        self.assertEqual(meta, [])

    def test_append_then_load(self):
        rec = make_user_skill_record(
            name="alpha", description="first skill", body="body of alpha"
        )
        idx = append_user_skill(
            self.emb_path, self.meta_path, _vec(1), rec, embed_dim=DIM
        )
        self.assertEqual(idx, 0)

        emb, meta = load_user_index(self.emb_path, self.meta_path, embed_dim=DIM)
        self.assertEqual(emb.shape, (1, DIM))
        self.assertEqual(meta[0]["name"], "alpha")
        self.assertEqual(meta[0]["body"], "body of alpha")

    def test_append_multiple_and_remove(self):
        for name in ("a", "b", "c"):
            append_user_skill(
                self.emb_path, self.meta_path,
                _vec(hash(name) & 0xFFFF),
                make_user_skill_record(name=name, description=f"d-{name}", body=f"b-{name}"),
                embed_dim=DIM,
            )
        emb, meta = load_user_index(self.emb_path, self.meta_path, embed_dim=DIM)
        self.assertEqual(emb.shape, (3, DIM))
        names = [m["name"] for m in meta]
        self.assertEqual(names, ["a", "b", "c"])

        removed = remove_user_skill_by_name(
            self.emb_path, self.meta_path, "b", embed_dim=DIM
        )
        self.assertTrue(removed)

        emb2, meta2 = load_user_index(self.emb_path, self.meta_path, embed_dim=DIM)
        self.assertEqual(emb2.shape, (2, DIM))
        self.assertEqual([m["name"] for m in meta2], ["a", "c"])

    def test_remove_missing(self):
        append_user_skill(
            self.emb_path, self.meta_path, _vec(1),
            make_user_skill_record(name="alpha", description="d", body="b"),
            embed_dim=DIM,
        )
        removed = remove_user_skill_by_name(
            self.emb_path, self.meta_path, "nonexistent", embed_dim=DIM
        )
        self.assertFalse(removed)
        # untouched
        _, meta = load_user_index(self.emb_path, self.meta_path, embed_dim=DIM)
        self.assertEqual(len(meta), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
