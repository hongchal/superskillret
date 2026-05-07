"""Smoke test: run retrieve.py against a set of queries, print top-K summaries."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RETRIEVE = ROOT / "scripts" / "retrieve.py"
PYTHON = "/home/ubuntu/anaconda3/envs/swift_gkd_bw/bin/python3"

QUERIES = [
    "help me set up a CI/CD pipeline for my Python project with github actions",
    "I need to write unit tests for a React component with mocks",
    "debug a memory leak in my Node.js server",
    "design a REST API with OpenAPI spec",
    "create a slack bot that sends daily standup reminders",
    "analyze a PDF document and extract tables",
    "brainstorm new feature ideas for my SaaS product",
    "refactor this legacy Java codebase safely",
]


def run_query(q: str):
    t0 = time.time()
    proc = subprocess.run(
        [PYTHON, str(RETRIEVE)],
        input=json.dumps({"prompt": q}),
        capture_output=True,
        text=True,
        env={**os.environ, "CUDA_VISIBLE_DEVICES": "0", "SUPERSKILLRET_TOP_K": "3"},
    )
    latency = time.time() - t0
    if proc.returncode != 0:
        return {"error": proc.stderr, "latency": latency}
    try:
        data = json.loads(proc.stdout)
        ctx = data["hookSpecificOutput"]["additionalContext"]
    except Exception as e:
        return {"error": f"parse error: {e}", "raw": proc.stdout[:500], "latency": latency}

    hits = []
    for line in ctx.splitlines():
        if line.startswith("## "):
            hits.append(line[3:])
    return {"hits": hits, "latency": latency, "ctx_len": len(ctx)}


def main():
    print(f"Running {len(QUERIES)} queries through retrieve.py ...\n")
    results = []
    for i, q in enumerate(QUERIES, 1):
        print(f"[{i}/{len(QUERIES)}] {q}")
        r = run_query(q)
        results.append((q, r))
        if "error" in r:
            print(f"  ERROR: {r['error'][:200]}")
        else:
            print(f"  latency={r['latency']:.2f}s  ctx_len={r['ctx_len']}")
            for h in r["hits"]:
                print(f"    - {h}")
        print()

    latencies = [r["latency"] for _, r in results if "hits" in r]
    if latencies:
        print(f"Latency: min={min(latencies):.2f}s  max={max(latencies):.2f}s  "
              f"mean={sum(latencies)/len(latencies):.2f}s  (each starts cold)")


if __name__ == "__main__":
    main()
