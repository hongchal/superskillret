"""Compare retrieval quality/latency across backends.

- PyTorch (sentence-transformers) on CPU
- ONNX FP32 on CPU
- ONNX INT8 on CPU

Reports per-query: latency, top-5 skill names (to check overlap).
"""

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch
from onnxruntime import InferenceSession, SessionOptions
from sentence_transformers import SentenceTransformer
from transformers import AutoTokenizer

ROOT = Path(__file__).resolve().parent.parent
EMB_PATH = ROOT / "cache" / "skill_embeddings.npy"
META_PATH = ROOT / "cache" / "skill_metadata.jsonl"

QUERIES = [
    "help me set up a CI/CD pipeline for my Python project",
    "write unit tests for my React component with mocks",
    "debug a memory leak in my Node.js server",
    "design a REST API with OpenAPI spec",
    "create a slack bot that sends daily standup reminders",
    "analyze a PDF document and extract tables",
    "brainstorm new feature ideas for my SaaS product",
    "refactor this legacy Java codebase safely",
    "build a python cli tool with click",
    "help me scrape a website with beautifulsoup",
]

QUERY_PROMPT = (
    "Instruct: Given a skill search query, retrieve relevant skills that match the query\n"
    "Query: "
)


def last_token_pool(token_embeddings: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
    """Pick the hidden state at the position of the last real (non-pad) token for each row."""
    seq_lengths = attention_mask.sum(axis=1) - 1  # last non-pad index
    batch = token_embeddings.shape[0]
    return token_embeddings[np.arange(batch), seq_lengths]


def normalize(x: np.ndarray) -> np.ndarray:
    return x / (np.linalg.norm(x, axis=-1, keepdims=True) + 1e-12)


def encode_onnx(session, tokenizer, text: str) -> np.ndarray:
    enc = tokenizer(text, return_tensors="np", padding=True, truncation=True, max_length=8192)
    inputs = {k: v for k, v in enc.items() if k in {i.name for i in session.get_inputs()}}
    outputs = session.run(None, inputs)
    token_embeds = outputs[0]
    attn = enc.get("attention_mask", np.ones(token_embeds.shape[:2], dtype=np.int64))
    pooled = last_token_pool(token_embeds, attn)
    return normalize(pooled)[0]


def topk(query_emb: np.ndarray, skill_embs: np.ndarray, k: int = 5):
    sims = skill_embs @ query_emb
    idx = np.argsort(-sims)[:k]
    return idx, sims[idx]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--onnx-dir", default="onnx_model")
    ap.add_argument("--onnx-int8-dir", default="onnx_model_int8")
    args = ap.parse_args()

    print("Loading skill index ...")
    skill_embs = np.load(EMB_PATH).astype(np.float32)
    metadata = []
    with META_PATH.open() as f:
        for line in f:
            metadata.append(json.loads(line))
    print(f"  {len(metadata)} skills, dim={skill_embs.shape[1]}")

    print("\nLoading PyTorch (sentence-transformers) on CPU ...")
    t0 = time.time()
    st_model = SentenceTransformer("ThakiCloud/SkillRet-Embedding-0.6B", trust_remote_code=True, device="cpu")
    print(f"  loaded in {time.time()-t0:.1f}s")

    for label, onnx_dir in [("ONNX FP32", args.onnx_dir), ("ONNX INT8", args.onnx_int8_dir)]:
        print(f"\nLoading {label} from {onnx_dir} ...")
        t0 = time.time()
        sess_opts = SessionOptions()
        sess_opts.intra_op_num_threads = 4
        session = InferenceSession(str(Path(onnx_dir) / "model.onnx"), sess_options=sess_opts, providers=["CPUExecutionProvider"])
        tokenizer = AutoTokenizer.from_pretrained(onnx_dir, trust_remote_code=True)
        print(f"  loaded in {time.time()-t0:.1f}s")
        # stash for later
        if label == "ONNX FP32":
            onnx_fp32 = (session, tokenizer)
        else:
            onnx_int8 = (session, tokenizer)

    # Warm-up
    _ = st_model.encode([QUERY_PROMPT + "warmup"], normalize_embeddings=True, convert_to_numpy=True)[0]
    _ = encode_onnx(*onnx_fp32, QUERY_PROMPT + "warmup")
    _ = encode_onnx(*onnx_int8, QUERY_PROMPT + "warmup")

    print(f"\n{'='*110}")
    print(f"Benchmarking {len(QUERIES)} queries (warm latency, top-5 retrieval)")
    print(f"{'='*110}\n")

    lat = {"PyTorch": [], "ONNX FP32": [], "ONNX INT8": []}
    overlap_fp = []
    overlap_int = []

    for q_idx, q in enumerate(QUERIES):
        print(f"[{q_idx+1}] {q}")
        full = QUERY_PROMPT + q

        t0 = time.time()
        pt_emb = st_model.encode([full], normalize_embeddings=True, convert_to_numpy=True)[0]
        lat["PyTorch"].append(time.time() - t0)

        t0 = time.time()
        fp32_emb = encode_onnx(*onnx_fp32, full)
        lat["ONNX FP32"].append(time.time() - t0)

        t0 = time.time()
        int8_emb = encode_onnx(*onnx_int8, full)
        lat["ONNX INT8"].append(time.time() - t0)

        pt_idx, pt_scores = topk(pt_emb, skill_embs, 5)
        fp_idx, fp_scores = topk(fp32_emb, skill_embs, 5)
        int_idx, int_scores = topk(int8_emb, skill_embs, 5)

        pt_names = [metadata[int(i)]["name"] for i in pt_idx]
        fp_names = [metadata[int(i)]["name"] for i in fp_idx]
        int_names = [metadata[int(i)]["name"] for i in int_idx]

        ov_fp = len(set(pt_idx) & set(fp_idx)) / 5
        ov_int = len(set(pt_idx) & set(int_idx)) / 5
        overlap_fp.append(ov_fp)
        overlap_int.append(ov_int)

        print(f"    PyTorch  ({lat['PyTorch'][-1]:.2f}s): {pt_names}")
        print(f"    FP32 ONNX({lat['ONNX FP32'][-1]:.2f}s): {fp_names}  [overlap={ov_fp:.0%}]")
        print(f"    INT8 ONNX({lat['ONNX INT8'][-1]:.2f}s): {int_names}  [overlap={ov_int:.0%}]")
        print()

    print(f"{'='*110}")
    print("Summary")
    print(f"{'='*110}")
    for k, vs in lat.items():
        avg = sum(vs) / len(vs)
        print(f"  {k:<12}: mean={avg:.2f}s  min={min(vs):.2f}s  max={max(vs):.2f}s")
    print(f"  top-5 overlap vs PyTorch:  ONNX FP32 = {sum(overlap_fp)/len(overlap_fp):.1%}  |  ONNX INT8 = {sum(overlap_int)/len(overlap_int):.1%}")


if __name__ == "__main__":
    main()
