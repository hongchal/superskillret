# superskillret

Embedding-based **skill retrieval plugin for Claude Code**. On every user prompt, it picks the top-K most relevant skills from a pool of ~16,800 public skills and injects them as context — so Claude gets the right "how to" reference without you having to preload every skill in the system prompt.

Uses [`ThakiCloud/SkillRet-Embedding-0.6B`](https://huggingface.co/ThakiCloud/SkillRet-Embedding-0.6B) (fine-tuned from Qwen3-Embedding-0.6B) and the [`ThakiCloud/SKILLRET`](https://huggingface.co/datasets/ThakiCloud/SKILLRET) skill corpus.

## What it does

1. `UserPromptSubmit` hook fires on every user prompt.
2. Hook talks to a local retrieval daemon over a Unix socket.
3. Daemon encodes the prompt, finds top-K skills by cosine similarity, returns full `SKILL.md` bodies.
4. Hook emits a Claude Code `additionalContext` JSON so Claude sees the retrieved skills before answering.

The daemon is lazy-started on the first request and then stays warm. Model + index are loaded **once** per process.

## Install

```
/plugin marketplace add lotusroot-kim/superskillret
/plugin install superskillret@lotusroot-kim
```

Then, from the plugin directory (Claude Code tells you where it cloned the plugin — usually under `~/.claude/plugins/cache/`), run the one-time setup:

```bash
bash scripts/install.sh
```

This will:
- create `.venv/` (CPU torch wheel by default)
- install `sentence-transformers`, `datasets`, `numpy`, `huggingface_hub`
- download the embedding model (~1.2 GB) via Hugging Face cache
- **fetch the prebuilt index** (~194 MB) from [`youngryankim/superskillret-index`](https://huggingface.co/datasets/youngryankim/superskillret-index) — takes ~5 s on a decent connection
- fall back to building the index locally only if the prebuilt dataset is unreachable (offline, deleted). Local build time: **~1 min on GPU**, **~30–60 min on CPU** — and requires the 300 MB skill pool download.

Set `FORCE=1` to rebuild/refetch everything from scratch.
Set `SUPERSKILLRET_INDEX_REPO=<user>/<repo>` to point at a different prebuilt-index dataset.
Set `SUPERSKILLRET_SKIP_PREBUILT=1` to force a local build.

### GPU users

`install.sh` installs the CPU wheel. If you have CUDA, swap torch afterwards:

```bash
.venv/bin/pip install --force-reinstall torch --index-url https://download.pytorch.org/whl/cu121
```

Daemon auto-detects GPU (`SUPERSKILLRET_DEVICE=auto`). Override with `SUPERSKILLRET_DEVICE=cpu` or `cuda`.

## Register with Claude Code

### Recommended: marketplace install

```
/plugin marketplace add lotusroot-kim/superskillret
/plugin install superskillret@lotusroot-kim
```

Then, in the plugin's working directory (wherever Claude Code cloned it — typically `~/.claude/plugins/cache/superskillret@lotusroot-kim/`), run the first-time setup once:

```bash
bash scripts/install.sh
```

After that, every Claude Code session will lazy-start the retrieval daemon on the first prompt.

### Alternative: direct hook in settings.json (no marketplace)

Clone the repo somewhere and add to your `~/.claude/settings.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "/absolute/path/to/superskillret/.venv/bin/python /absolute/path/to/superskillret/scripts/retrieve.py",
            "timeout": 120000
          }
        ]
      }
    ]
  }
}
```

This path skips the marketplace and slash commands, but the hook runs the same.

## Usage

Nothing. Just talk to Claude. After the first prompt (~10s warm-up on CPU, ~3s on GPU), every prompt has top-K retrieved skills silently injected.

Inspect / control the daemon via slash commands (available only with the marketplace install):

| Command | What it does |
|---|---|
| `/superskillret:status` | show daemon pid, socket state, recent log |
| `/superskillret:stop` | kill the daemon; it will lazy-start on next prompt |

## Configuration

Environment variables (set in shell, hook command, or `settings.json` `env`):

| Variable | Default | Meaning |
|---|---|---|
| `SUPERSKILLRET_TOP_K` | `3` | how many skills to return |
| `SUPERSKILLRET_MIN_SCORE` | `0.25` | drop hits below this cosine score |
| `SUPERSKILLRET_DEVICE` | `auto` | `cpu`, `cuda`, or `auto` |
| `SUPERSKILLRET_SOCKET` | `/tmp/superskillret.sock` | daemon socket |
| `SUPERSKILLRET_PIDFILE` | `/tmp/superskillret.pid` | daemon pid file |
| `SUPERSKILLRET_LOG` | `/tmp/superskillret.log` | daemon log file |
| `SUPERSKILLRET_MODEL` | `ThakiCloud/SkillRet-Embedding-0.6B` | embedding model |
| `SUPERSKILLRET_SPAWN_WAIT` | `90` | seconds the hook waits for daemon boot |
| `SUPERSKILLRET_DISABLE` | unset | set to `1` to turn the hook into a no-op |

## Expected latency

| Call | CPU | GPU |
|---|---|---|
| First prompt (cold, daemon boot + model load) | ~20–40 s | ~10–15 s |
| Warm prompt | ~1–3 s | ~0.3–0.6 s |

Most of the warm cost is the hook's own Python startup; the daemon-side work is ~0.1s.

## Files

```
superskillret/
├── .claude-plugin/plugin.json     # plugin manifest
├── hooks/hooks.json               # UserPromptSubmit hook registration
├── commands/
│   ├── status.md                  # /superskillret:status
│   └── stop.md                    # /superskillret:stop
├── scripts/
│   ├── install.sh                 # one-shot setup (venv, data, index)
│   ├── build_index.py             # (re)build the embedding index
│   ├── daemon.py                  # long-running retrieval server
│   ├── retrieve.py                # UserPromptSubmit hook (thin client)
│   └── smoke_test.py              # sanity test for retrieval quality
├── skill_pool/skills.jsonl        # 16,783 skills (built by install.sh)
└── cache/
    ├── skill_embeddings.npy       # normalized embeddings, float16
    └── skill_metadata.jsonl       # one JSON record per embedding
```

## How retrieval works

1. Each skill's `(name | description)` is encoded at build time with the SKILLRET model.
2. Vectors stored as normalized float16 → `.npy`.
3. At query time the daemon encodes `"Instruct: ... Query: <prompt>"` (the query-side prompt SKILLRET was trained with) and takes top-K by inner product.
4. Hits below `MIN_SCORE` are dropped so irrelevant prompts don't get noise injected.

Model reported eval: NDCG@15 = 0.7887, Recall@10 = 0.8542.

## Troubleshooting

**Hook times out on first prompt.** The daemon is downloading the model from Hugging Face. Watch `/tmp/superskillret.log`; increase `SUPERSKILLRET_SPAWN_WAIT`.

**Nothing is being injected.** Run `/superskillret-status`. If the socket is missing and pinging fails, run `.venv/bin/python scripts/daemon.py` in a terminal to see the error.

**Retrieval is picking wrong skills.** Raise `SUPERSKILLRET_MIN_SCORE` (e.g. `0.4`) so weak matches are dropped, or lower `TOP_K` to 1.

**Want to use a custom skill pool.** Replace `skill_pool/skills.jsonl` (one JSON per line with at least `name`, `description`, `body`), then `python scripts/build_index.py`.

## Status & roadmap

MVP is complete and verified end-to-end (GPU and CPU). Retrieval works, daemon + socket client round-trip works, slash commands work. What remains is productionization.

### Known limitations today

- **CPU warm latency is 7–9 s.** Most of it is the PyTorch forward pass for a 0.6B-parameter model. Usable, but not great for chatty sessions.
- **Daemon holds 1.5 GB VRAM (GPU) or 2.4 GB RAM (CPU) forever** once started. There is no idle timeout; the process only exits when you run `/superskillret-stop` or kill it.
- **Prebuilt index lives at [`youngryankim/superskillret-index`](https://huggingface.co/datasets/youngryankim/superskillret-index).** Users get the fast (~5 s) install path when the dataset is accessible. If the dataset is offline or private without a token, `install.sh` falls back to rebuilding locally (1 min on GPU, 30–60 min on CPU).
- **Not published as a Claude Code marketplace plugin.** No `/plugin install superskillret@...` path exists — only the manual `settings.json` hook wiring described above.
- **Not tested against a live Claude Code session end-to-end.** The hook and daemon were verified by feeding synthetic `UserPromptSubmit` payloads; the real CLI hookup was not exercised.
- **No GPU auto-detection in install.sh.** `install.sh` always installs the CPU torch wheel; GPU users must manually reinstall torch with CUDA.
- **Quality is untested beyond 8 hand-picked queries.** There is no regression eval against the SKILLRET benchmark splits to confirm it still delivers the 0.79 NDCG@15 / 0.85 Recall@10 numbers from the model card.

### Roadmap for the next session

Each item below is intended to be actionable in a fresh Claude Code session without prior context. Pick one, not all.

**1. Cut CPU latency from ~8 s to ~2 s** (biggest UX win)
- Export SKILLRET model to ONNX: `optimum-cli export onnx --model ThakiCloud/SkillRet-Embedding-0.6B ./onnx_model`
- Quantize to INT8: use `optimum.onnxruntime` `ORTQuantizer` with dynamic quantization.
- Swap `SentenceTransformer(...)` call in `daemon.py` for `optimum.onnxruntime.ORTModelForFeatureExtraction` + a small mean-pooling wrapper.
- Compare NDCG@15 on a SKILLRET eval subset before vs. after to confirm ≤1% quality drop.
- Expected result: model size 1.2 GB → ~400 MB, CPU inference 3–5× faster.

**2. Refresh the prebuilt index** (already published, already public — see `youngryankim/superskillret-index`)
- Index is version-stamped via `cache/VERSION`.
- `install.sh` already tries the prebuilt index first and falls back to `build_index.py` on failure.
- When the corpus or encoder changes: bump `cache/VERSION`, rerun `python scripts/build_index.py`, then `HF_TOKEN=... python scripts/publish_index.py --repo youngryankim/superskillret-index` (drop `--private` since the dataset is public).

**3. Add idle-timeout to the daemon**
- In `scripts/daemon.py`, track `last_request_at` on every `handle()`.
- Add a watchdog thread that calls `os.kill(os.getpid(), SIGTERM)` if no request arrives for N seconds (env `SUPERSKILLRET_IDLE_TIMEOUT`, default e.g. 1800 s).
- The socket client already knows how to lazy-spawn, so a reaped daemon will just restart on the next prompt.

**4. Real Claude Code end-to-end test**
- Start Claude Code with this plugin registered (either drop into `~/.claude/plugins/` or add to a marketplace you control).
- Send a real user prompt like "help me write a Dockerfile for a Node app".
- Verify via `/superskillret-status` that the daemon booted and that `additionalContext` arrived (check `~/.claude/logs/` or add a temporary `logging` line in `retrieve.py`).
- Document any shape mismatch between the real UserPromptSubmit payload and what `retrieve.py` expects (it currently reads `payload["prompt"]`).

**5. Publish to a Claude Code marketplace**
- Create `your-marketplace/` repo with a top-level `marketplace.json` pointing at this plugin directory.
- Verify `/plugin marketplace add <repo>` and `/plugin install superskillret@<marketplace>` work.
- Write an update flow (version bump + `git tag`).

**6. GPU auto-detect in `install.sh`**
- Probe `nvidia-smi` or `torch.cuda.is_available()` before the `pip install torch` line; if GPU is present, use `--index-url https://download.pytorch.org/whl/cu121` (or the latest stable CUDA wheel).
- Keep an env override (`SUPERSKILLRET_FORCE_CPU=1`) for users who want to pin to CPU.

**7. Regression eval against the benchmark**
- Load `ThakiCloud/SKILLRET` `queries` + `qrels` test splits.
- Run each query through the running daemon (or directly against `SentenceTransformer`), compute NDCG@15 and Recall@10.
- Commit the eval script at `scripts/eval.py`. Use it as a gate before any change that swaps the model or the embedding encoder.

### Files of interest for future work

| File | Purpose | Most likely to change for |
|---|---|---|
| `scripts/daemon.py` | socket server holding model+index | idle timeout, ONNX swap, better concurrency |
| `scripts/retrieve.py` | thin socket client, hook entry point | payload-schema changes in real CC hook |
| `scripts/build_index.py` | encodes skill pool → `.npy` + metadata | ONNX encoder swap, quantized embeddings |
| `scripts/install.sh` | venv + deps + data + index (HF prebuilt first) | GPU auto-detect, public-dataset switch |
| `scripts/publish_index.py` | upload cache/ to HF dataset repo | rerun after corpus or encoder changes |
| `hooks/hooks.json` | registers `UserPromptSubmit` command | marketplace packaging, timeout tuning |
| `commands/*.md` | `/superskillret-status`, `/superskillret-stop` | add `/superskillret-restart`, `/superskillret-rebuild` |

### Pointers you'll want in the next session

- Embedding model: [`ThakiCloud/SkillRet-Embedding-0.6B`](https://huggingface.co/ThakiCloud/SkillRet-Embedding-0.6B) — SentenceTransformers wrapper, query prompt is `"Instruct: Given a skill search query, retrieve relevant skills that match the query\nQuery: "`, max seq 8192, dim 1024.
- Skill corpus: [`ThakiCloud/SKILLRET`](https://huggingface.co/datasets/ThakiCloud/SKILLRET) — `skills` config has `train` (10,123) + `test` (6,660) splits. Also `queries` + `qrels` configs for eval.
- Claude Code hook schema: `UserPromptSubmit` receives JSON on stdin and expects JSON on stdout with `{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "..."}}`.
- Daemon protocol: line-delimited JSON over Unix socket. Ops supported: `ping`, `shutdown`, and plain `{"prompt": ..., "top_k": ..., "min_score": ...}`.
- Working env on this machine: `/home/ubuntu/anaconda3/envs/swift_gkd_bw/bin/python3` has a GPU-enabled PyTorch install useful for quick daemon tests outside the plugin venv.

## License

MIT.
