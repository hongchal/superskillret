"""Long-running retrieval daemon.

Listens on a Unix domain socket. Holds the embedding model + index in memory
so each incoming request completes in ~0.1-1s instead of reloading from scratch.

Protocol (line-delimited JSON over Unix socket):
  Request : {"prompt": "...", "top_k": 3, "min_score": 0.25,
             "session_id": "<claude code session uuid>",
             "scope": "all" | "user" | "system"}
  Response: {"hits": [{"name": ..., "score": ..., "body": ..., ...}, ...],
             "latency_s": 0.12, "skipped_seen": 2}

  `scope` (optional, default "all") restricts retrieval to a pool:
    - "all"    : search the merged system + user index (default)
    - "user"   : search only user-added skills
    - "system" : search only system skills

  Ops: {"op": "ping"}           → {"ok": true}
       {"op": "shutdown"}       → {"ok": true}, daemon exits
       {"op": "reset_session",
        "session_id": "..."}    → {"ok": true, "had_session": bool}

  Per-session dedup: if session_id is given and SUPERSKILLRET_SEEN_TRACKING
  is on (default), skills already returned to that session are skipped in
  subsequent retrievals for the same session. LRU capped at MAX_SESSIONS.

Environment variables:
  SUPERSKILLRET_SOCKET    default /tmp/superskillret.sock
  SUPERSKILLRET_DEVICE    default cpu. Set to "cuda" to use GPU, or
                          "auto" to prefer GPU when available.
  SUPERSKILLRET_BACKEND   default "onnx" (ONNX INT8, ~0.07s CPU). Set to
                          "pytorch" for the sentence-transformers path.
  SUPERSKILLRET_ONNX_DIR  default <plugin>/onnx_model_int8. Directory
                          containing model.onnx + tokenizer files.
  SUPERSKILLRET_MODEL     default ThakiCloud/SkillRet-Embedding-0.6B
  SUPERSKILLRET_PIDFILE   default /tmp/superskillret.pid
  SUPERSKILLRET_LOG       default /tmp/superskillret.log
  SUPERSKILLRET_SEEN_TRACKING default "0" (as of v0.2.5). Set to "1" to
                          enable per-session dedup, which skips a skill on
                          re-retrieve within the same Claude Code session.
                          Off by default because a skill's active framing
                          ("authoritative reference material") expires when
                          its body is no longer re-injected, even though
                          the body lingers in conversation history.
  SUPERSKILLRET_MAX_SESSIONS  default 100. LRU cap on tracked sessions.
  SUPERSKILLRET_OVERFETCH default 4. When dedup is on, fetch top-K * this
                          many candidates before filtering.
"""

import json
import logging
import os
import secrets
import signal
import socket
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path

import numpy as np

# Make sibling scripts/ modules importable when daemon is launched directly.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from skill_io import (  # noqa: E402
    append_user_skill,
    load_user_index,
    make_user_skill_record,
    remove_user_skill_by_name,
)

ROOT = Path(__file__).resolve().parent.parent
EMB_PATH = ROOT / "cache" / "skill_embeddings.npy"
EMB_INT8_PATH = ROOT / "cache" / "skill_embeddings_int8.npy"
EMB_SCALE_PATH = ROOT / "cache" / "skill_embeddings_scale.npy"
META_PATH = ROOT / "cache" / "skill_metadata.jsonl"
USER_EMB_PATH = ROOT / "cache" / "user_skill_embeddings.npy"
USER_META_PATH = ROOT / "cache" / "user_skill_metadata.jsonl"


def _migrate_user_pool_from_sibling_versions() -> None:
    """One-shot: when /plugin update brings up a new version, its cache/
    starts empty and the user-added skills sitting in the previous
    version's cache would be silently lost. On first boot of a fresh
    install, scan sibling version dirs and copy the most-recently-modified
    user pool over. Idempotent: skips when our own user pool already
    exists."""
    if USER_META_PATH.exists() or USER_EMB_PATH.exists():
        return
    versions_root = ROOT.parent  # .../superskillret/
    if not versions_root.is_dir():
        return
    candidates = []
    for sibling in versions_root.iterdir():
        if not sibling.is_dir() or sibling.resolve() == ROOT.resolve():
            continue
        emb = sibling / "cache" / "user_skill_embeddings.npy"
        meta = sibling / "cache" / "user_skill_metadata.jsonl"
        if meta.exists():
            candidates.append((meta.stat().st_mtime, emb, meta))
    if not candidates:
        return
    candidates.sort(reverse=True)
    _, src_emb, src_meta = candidates[0]
    USER_META_PATH.parent.mkdir(parents=True, exist_ok=True)
    import shutil
    if src_meta.exists():
        shutil.copy2(src_meta, USER_META_PATH)
    if src_emb.exists():
        shutil.copy2(src_emb, USER_EMB_PATH)
    logging.info(
        "migrated user pool from %s (%d bytes meta) into %s",
        src_meta.parent.parent.name,
        src_meta.stat().st_size,
        USER_META_PATH.parent,
    )

SOCKET_PATH = os.environ.get("SUPERSKILLRET_SOCKET", "/tmp/superskillret.sock")
PID_PATH = os.environ.get("SUPERSKILLRET_PIDFILE", "/tmp/superskillret.pid")
LOG_PATH = os.environ.get("SUPERSKILLRET_LOG", "/tmp/superskillret.log")
MODEL_NAME = os.environ.get("SUPERSKILLRET_MODEL", "ThakiCloud/SkillRet-Embedding-0.6B")
DEVICE_ENV = os.environ.get("SUPERSKILLRET_DEVICE", "cpu")
BACKEND = os.environ.get("SUPERSKILLRET_BACKEND", "onnx").lower()  # "onnx" | "pytorch"
ONNX_DIR = Path(os.environ.get(
    "SUPERSKILLRET_ONNX_DIR",
    str(ROOT / "onnx_model_int8"),
))

# Per-session skill dedup: a skill index already returned to a given session_id
# is skipped in that session's future retrievals.
#
# Default OFF as of v0.2.5: previous design (default on) was confusing in
# practice because a SKILL.md's framing (e.g. MUST/SHOULD directives) only
# stays "current authoritative reference material" while the skill is being
# re-injected each turn. Dedup left the body in conversation history but
# expired its active framing, so the same query produced inconsistent
# behaviour across turns. Users who want token-cost reduction over
# consistent activation can opt in with SUPERSKILLRET_SEEN_TRACKING=1.
MAX_SESSIONS = int(os.environ.get("SUPERSKILLRET_MAX_SESSIONS", "100"))
SEEN_TRACKING = os.environ.get("SUPERSKILLRET_SEEN_TRACKING", "0") == "1"
# Over-fetch factor when dedup is on so that after filtering we still have
# enough candidates to return TOP_K hits.
OVERFETCH = int(os.environ.get("SUPERSKILLRET_OVERFETCH", "4"))

MAX_REQ_BYTES = 4 * 1024 * 1024  # 4 MiB per request
TOKEN_PATH = Path(os.environ.get(
    "SUPERSKILLRET_TOKEN_FILE",
    "/tmp/superskillret.token",
))
_PRIVILEGED_OPS = frozenset({"shutdown", "add_skill", "remove_user_skill"})


def _read_plugin_version() -> str:
    """Plugin's declared version, used by retrieve.py to detect drift after
    /plugin update. Falls back to "" if plugin.json is missing or malformed
    so the daemon never refuses to start over a metadata read."""
    try:
        import json as _json
        data = _json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
        return str(data.get("version") or "")
    except Exception:
        return ""


PLUGIN_VERSION = _read_plugin_version()

QUERY_PROMPT = (
    "Instruct: Given a skill search query, retrieve relevant skills that match the query\n"
    "Query: "
)


def setup_logging():
    logging.basicConfig(
        filename=LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def pick_device() -> str:
    if DEVICE_ENV != "auto":
        return DEVICE_ENV
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def _normalize(x: np.ndarray) -> np.ndarray:
    return x / (np.linalg.norm(x, axis=-1, keepdims=True) + 1e-12)


def _last_token_pool(token_embeddings: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
    seq_lengths = attention_mask.sum(axis=1) - 1
    batch = token_embeddings.shape[0]
    return token_embeddings[np.arange(batch), seq_lengths]


class PyTorchEncoder:
    def __init__(self, device: str):
        from sentence_transformers import SentenceTransformer

        logging.info("loading PyTorch model %s on %s", MODEL_NAME, device)
        t0 = time.time()
        self.model = SentenceTransformer(MODEL_NAME, trust_remote_code=False, device=device)
        logging.info("model loaded in %.1fs", time.time() - t0)

    def encode(self, text: str) -> np.ndarray:
        emb = self.model.encode(
            [text],
            normalize_embeddings=True,
            convert_to_numpy=True,
        )[0]
        return emb.astype(np.float32)


class ONNXEncoder:
    def __init__(self, onnx_dir: Path):
        from onnxruntime import InferenceSession, SessionOptions
        from transformers import AutoTokenizer

        onnx_path = onnx_dir / "model.onnx"
        if not onnx_path.exists():
            raise FileNotFoundError(
                f"ONNX model not found at {onnx_path}. Either point "
                f"SUPERSKILLRET_ONNX_DIR at the right directory or run "
                f"scripts/install.sh to download/build it."
            )
        logging.info("loading ONNX model from %s", onnx_dir)
        t0 = time.time()
        sess_opts = SessionOptions()
        sess_opts.intra_op_num_threads = max(1, (os.cpu_count() or 4) // 2)
        self.session = InferenceSession(
            str(onnx_path),
            sess_options=sess_opts,
            providers=["CPUExecutionProvider"],
        )
        self.tokenizer = AutoTokenizer.from_pretrained(str(onnx_dir), trust_remote_code=False)
        self._input_names = {i.name for i in self.session.get_inputs()}
        logging.info("ONNX model loaded in %.1fs", time.time() - t0)

    def encode(self, text: str) -> np.ndarray:
        enc = self.tokenizer(text, return_tensors="np", padding=True, truncation=True, max_length=8192)
        inputs = {k: v for k, v in enc.items() if k in self._input_names}
        outputs = self.session.run(None, inputs)
        token_embeds = outputs[0]
        attn = enc.get("attention_mask", np.ones(token_embeds.shape[:2], dtype=np.int64))
        pooled = _last_token_pool(token_embeds, attn)
        return _normalize(pooled)[0].astype(np.float32)


def make_encoder(device: str):
    if BACKEND == "onnx":
        try:
            return ONNXEncoder(ONNX_DIR)
        except Exception as e:
            logging.warning("ONNX encoder failed (%s); falling back to PyTorch", e)
            return PyTorchEncoder(device)
    return PyTorchEncoder(device)


def load_index():
    """Load the system index, then concatenate the user-pool index on top.

    Returns (embeddings, metadata, system_count) where system_count is the
    number of system rows. Row indices >= system_count belong to the
    user-pool (writable; survives system index upgrades).
    """
    if EMB_INT8_PATH.exists() and EMB_SCALE_PATH.exists():
        logging.info("loading INT8 index from %s", EMB_INT8_PATH)
        embeddings_int8 = np.load(EMB_INT8_PATH)
        scale = np.load(EMB_SCALE_PATH)
        embeddings = (embeddings_int8.astype(np.float32) / 127.0) * scale
    else:
        logging.info("loading index from %s", EMB_PATH)
        embeddings = np.load(EMB_PATH).astype(np.float32)
    metadata = []
    with META_PATH.open(encoding="utf-8") as f:
        for line in f:
            metadata.append(json.loads(line))
    system_count = len(metadata)
    logging.info(
        "loaded %d system embeddings, %d system metadata records",
        len(embeddings), system_count,
    )

    embed_dim = embeddings.shape[1] if embeddings.shape[0] else 1024
    try:
        user_emb, user_meta = load_user_index(
            USER_EMB_PATH, USER_META_PATH, embed_dim=embed_dim
        )
    except RuntimeError as e:
        logging.error("user index inconsistent (%s); skipping user pool", e)
        user_emb = np.zeros((0, embed_dim), dtype=np.float16)
        user_meta = []

    if user_emb.shape[0] > 0:
        embeddings = np.vstack([embeddings, user_emb.astype(np.float32)])
        metadata.extend(user_meta)
        logging.info(
            "appended %d user-pool skills (global rows %d..%d)",
            user_emb.shape[0], system_count, len(metadata) - 1,
        )

    return embeddings, metadata, system_count


class RetrievalServer:
    def __init__(self, auth_token: str = ""):
        self.device = pick_device()
        self.encoder = make_encoder(self.device)
        self.embeddings, self.metadata, self.system_count = load_index()
        self.embed_dim = (
            self.embeddings.shape[1] if self.embeddings.shape[0] else 1024
        )
        self._lock = threading.Lock()
        self._index_lock = threading.Lock()
        self._auth_token = auth_token
        self._seen: "OrderedDict[str, set[int]]" = OrderedDict()
        self._seen_lock = threading.Lock()

    def _get_seen(self, session_id: str) -> set:
        with self._seen_lock:
            if session_id in self._seen:
                self._seen.move_to_end(session_id)
                return self._seen[session_id]
            self._seen[session_id] = set()
            # evict oldest sessions past the cap
            while len(self._seen) > MAX_SESSIONS:
                evicted_sid, _ = self._seen.popitem(last=False)
                logging.info("seen-set: evicted oldest session %s", evicted_sid[:8])
            return self._seen[session_id]

    def search(self, query: str, top_k: int, min_score: float,
               session_id: str = "", scope: str = "all"):
        """Top-K retrieval. `scope` restricts the candidate pool:
            "all"    — system + user (default)
            "user"   — only rows >= self.system_count
            "system" — only rows <  self.system_count
        Out-of-scope rows have their similarity set to -inf so they are
        naturally dropped by the min_score filter downstream.
        """
        scope = (scope or "all").lower()
        if scope not in ("all", "user", "system"):
            scope = "all"

        t0 = time.time()
        with self._lock:
            q_emb = self.encoder.encode(QUERY_PROMPT + query)

        with self._index_lock:
            sims = self.embeddings @ q_emb

            # Pool restriction: poison out-of-scope rows so they cannot make
            # it through min_score even if argpartition surfaces them.
            if scope == "user":
                if len(sims) > self.system_count:
                    sims[: self.system_count] = -np.inf
                else:
                    # No user-pool rows exist yet — short-circuit.
                    return {
                        "hits": [], "latency_s": time.time() - t0,
                        "skipped_seen": 0, "scope": scope,
                        "pool_empty": True,
                    }
            elif scope == "system":
                if self.system_count > 0:
                    sims[self.system_count:] = -np.inf
                else:
                    return {
                        "hits": [], "latency_s": time.time() - t0,
                        "skipped_seen": 0, "scope": scope,
                        "pool_empty": True,
                    }

            use_dedup = SEEN_TRACKING and bool(session_id)
            fetch_k = min(top_k * OVERFETCH, len(sims)) if use_dedup else top_k

            top_idx = np.argpartition(-sims, min(fetch_k, len(sims) - 1))[:fetch_k]
            top_idx = top_idx[np.argsort(-sims[top_idx])]

            seen = self._get_seen(session_id) if use_dedup else None
            skipped = 0
            hits = []
            returned_indices = []
            for idx in top_idx:
                idx_int = int(idx)
                score = float(sims[idx_int])
                if score < min_score:
                    continue
                if seen is not None and idx_int in seen:
                    skipped += 1
                    continue
                hits.append({**self.metadata[idx_int], "score": score})
                returned_indices.append(idx_int)
                if len(hits) >= top_k:
                    break

        if seen is not None:
            seen.update(returned_indices)

        return {
            "hits": hits,
            "latency_s": time.time() - t0,
            "skipped_seen": skipped,
            "scope": scope,
        }

    # ------------------------------------------------------------------
    # User-pool skill management (add / list / remove)
    # ------------------------------------------------------------------

    def _find_existing_global_row(self, name: str):
        for i, m in enumerate(self.metadata):
            if m.get("name") == name:
                return i
        return None

    def _encode_skill(self, name: str, description: str, body: str) -> np.ndarray:
        """Encode a skill's (name | description | body) into a normalized
        float32 vector, using the same skill-side format as build_index.py.
        Body is included so the embedding captures keywords that appear only
        inside the skill content, not just its summary."""
        text = f"{name} | {description} | {body}".strip()
        with self._lock:
            vec = self.encoder.encode(text)
        return vec.astype(np.float32)

    def _encode_query(self, query: str) -> np.ndarray:
        with self._lock:
            return self.encoder.encode(QUERY_PROMPT + query).astype(np.float32)

    def _quality_check(self, skill_vec: np.ndarray, description: str,
                       exclude_global_row: int = -1) -> dict:
        """Compute self-retrieval score and nearest-existing similarity for
        a newly-encoded skill vector."""
        # Self-retrieval: encode `description` as a query and measure
        # similarity to the just-built skill vector. Both are L2-normalized
        # so inner product is the cosine score.
        try:
            query_vec = self._encode_query(description)
            self_score = float(skill_vec @ query_vec)
        except Exception as e:
            logging.warning("self-retrieval encode failed: %s", e)
            self_score = None

        # Nearest-existing: argmax over the current index.
        nearest_name = None
        nearest_score = None
        if self.embeddings.shape[0] > 0:
            sims = self.embeddings @ skill_vec
            if exclude_global_row >= 0 and exclude_global_row < sims.shape[0]:
                sims[exclude_global_row] = -np.inf
            top = int(np.argmax(sims))
            nearest_score = float(sims[top])
            nearest_name = self.metadata[top].get("name") if top < len(self.metadata) else None

        return {
            "self_retrieval_score": self_score,
            "nearest_existing_name": nearest_name,
            "nearest_existing_score": nearest_score,
        }

    def add_skill(self, payload: dict) -> dict:
        name = (payload.get("name") or "").strip()
        description = (payload.get("description") or "").strip()
        body = payload.get("body") or ""
        force = bool(payload.get("force"))
        source = payload.get("source") or ""

        if not name or not description or not body:
            return {"ok": False,
                    "error": "name, description, and body are required"}

        t_enc = time.time()
        skill_vec_fp32 = self._encode_skill(name, description, body)
        encode_ms = (time.time() - t_enc) * 1000.0

        with self._index_lock:
            existing_global = self._find_existing_global_row(name)

            if existing_global is not None and existing_global < self.system_count:
                return {"ok": False,
                        "reason": "system_collision",
                        "error": f"name '{name}' collides with a system skill"
                                 f" at row {existing_global}; pick a different name"}

            if existing_global is not None and not force:
                return {"ok": False,
                        "reason": "duplicate",
                        "existing_global_row": existing_global,
                        "error": f"name '{name}' already in user pool at row "
                                 f"{existing_global}; pass force=true to overwrite"}

            if existing_global is not None and force:
                user_local = existing_global - self.system_count
                remove_user_skill_by_name(
                    USER_EMB_PATH, USER_META_PATH, name,
                    embed_dim=self.embed_dim,
                )
                del self.metadata[existing_global]
                self.embeddings = np.delete(
                    self.embeddings, existing_global, axis=0
                )

            # Quality check before persisting (against pre-insert index).
            quality = self._quality_check(skill_vec_fp32, description)

            # Persist to user pool (atomic).
            skill_vec_fp16 = skill_vec_fp32.astype(np.float16)
            record = make_user_skill_record(
                name=name, description=description, body=body,
                source_url=source if source.startswith(("http://", "https://"))
                else "",
            )
            user_local_row = append_user_skill(
                USER_EMB_PATH, USER_META_PATH,
                skill_vec_fp16, record, embed_dim=self.embed_dim,
            )

            # Hot-reload in-memory index.
            self.embeddings = np.vstack(
                [self.embeddings, skill_vec_fp32[None, :]]
            )
            self.metadata.append(record)
            global_row = len(self.metadata) - 1

        return {
            "ok": True,
            "name": name,
            "user_row_index": user_local_row,
            "global_row_index": global_row,
            "system_count": self.system_count,
            "encode_ms": encode_ms,
            "quality": quality,
        }

    def list_user_skills(self) -> dict:
        with self._index_lock:
            user_skills = [
                {
                    "name": m.get("name"),
                    "description": m.get("description"),
                    "body": m.get("body"),
                    "global_row": i,
                    "user_row": i - self.system_count,
                }
                for i, m in enumerate(self.metadata)
                if i >= self.system_count
            ]
        return {"ok": True, "skills": user_skills, "count": len(user_skills)}

    def remove_user_skill(self, payload: dict) -> dict:
        name = (payload.get("name") or "").strip()
        if not name:
            return {"ok": False, "error": "name required"}

        with self._index_lock:
            existing_global = self._find_existing_global_row(name)
            if existing_global is None:
                return {"ok": False, "error": f"no skill named '{name}'"}
            if existing_global < self.system_count:
                return {"ok": False,
                        "error": f"'{name}' is a system skill at row "
                                 f"{existing_global}; refusing to remove "
                                 "(reinstall to refresh system index instead)"}

            user_local = existing_global - self.system_count
            ok = remove_user_skill_by_name(
                USER_EMB_PATH, USER_META_PATH, name,
                embed_dim=self.embed_dim,
            )
            if not ok:
                return {"ok": False,
                        "error": f"persist remove failed for '{name}'"}

            del self.metadata[existing_global]
            self.embeddings = np.delete(
                self.embeddings, existing_global, axis=0
            )

        return {"ok": True, "name": name, "removed_row": user_local}

    def handle(self, conn: socket.socket):
        try:
            conn.settimeout(30)
            data = b""
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk
                if len(data) > MAX_REQ_BYTES:
                    conn.sendall(json.dumps({"error": "payload_too_large"}).encode() + b"\n")
                    return
                if b"\n" in data:
                    break
            if not data.strip():
                return
            req = json.loads(data.decode("utf-8"))
            op = req.get("op", "")
            if self._auth_token and op in _PRIVILEGED_OPS:
                if req.get("auth") != self._auth_token:
                    conn.sendall(json.dumps({"error": "auth_required"}).encode() + b"\n")
                    return
            if req.get("op") == "ping":
                # Return some health info too so retrieve.py can surface
                # daemon readiness in the answer framing.
                reply = {
                    "ok": True,
                    "n_skills": len(self.metadata),
                    "system_count": self.system_count,
                    "user_count": max(0, len(self.metadata) - self.system_count),
                    "embed_dim": self.embed_dim,
                    "backend": "onnx" if isinstance(self.encoder, ONNXEncoder) else "pytorch",
                    "version": PLUGIN_VERSION,
                    "pid": os.getpid(),
                }
                conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))
                return
            if req.get("op") == "reset_session":
                sid = req.get("session_id", "")
                if sid:
                    with self._seen_lock:
                        removed = self._seen.pop(sid, None)
                    conn.sendall(json.dumps({"ok": True, "had_session": removed is not None}).encode() + b"\n")
                else:
                    # No session_id given → drop all sessions.
                    with self._seen_lock:
                        count = len(self._seen)
                        self._seen.clear()
                    conn.sendall(json.dumps({"ok": True, "cleared_sessions": count}).encode() + b"\n")
                return
            if req.get("op") == "shutdown":
                conn.sendall(b'{"ok": true}\n')
                logging.info("shutdown requested via socket")
                os.kill(os.getpid(), signal.SIGTERM)
                return
            if req.get("op") == "add_skill":
                reply = self.add_skill(req)
                conn.sendall(json.dumps(reply, ensure_ascii=False).encode() + b"\n")
                return
            if req.get("op") == "list_user_skills":
                reply = self.list_user_skills()
                conn.sendall(json.dumps(reply, ensure_ascii=False).encode() + b"\n")
                return
            if req.get("op") == "remove_user_skill":
                reply = self.remove_user_skill(req)
                conn.sendall(json.dumps(reply, ensure_ascii=False).encode() + b"\n")
                return
            prompt = req.get("prompt", "")
            top_k = int(req.get("top_k", 3))
            min_score = float(req.get("min_score", 0.25))
            session_id = req.get("session_id", "") or ""
            scope = (req.get("scope") or "all")
            if not prompt.strip():
                conn.sendall(json.dumps({"hits": [], "latency_s": 0.0, "skipped_seen": 0, "scope": scope}).encode() + b"\n")
                return
            result = self.search(prompt, top_k, min_score,
                                  session_id=session_id, scope=scope)
            conn.sendall(json.dumps(result, ensure_ascii=False).encode() + b"\n")
        except Exception:
            logging.exception("request failed")
            try:
                conn.sendall(json.dumps({"error": "internal_error"}).encode() + b"\n")
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except Exception:
                pass


def bind_socket() -> socket.socket:
    if os.path.exists(SOCKET_PATH):
        try:
            test = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            test.settimeout(0.5)
            test.connect(SOCKET_PATH)
            test.close()
            logging.error("socket %s is already in use by a live daemon", SOCKET_PATH)
            sys.exit(1)
        except (ConnectionRefusedError, FileNotFoundError, socket.timeout, OSError):
            try:
                os.unlink(SOCKET_PATH)
            except FileNotFoundError:
                pass
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(SOCKET_PATH)
    os.chmod(SOCKET_PATH, 0o600)
    sock.listen(16)
    return sock


def write_pidfile():
    with open(PID_PATH, "w") as f:
        f.write(str(os.getpid()))


def init_auth_token() -> str:
    token = os.environ.get("SUPERSKILLRET_SOCKET_SECRET") or secrets.token_hex(32)
    TOKEN_PATH.write_text(token, encoding="utf-8")
    os.chmod(str(TOKEN_PATH), 0o600)
    logging.info("socket auth file written to %s", TOKEN_PATH)
    return token


def cleanup(*_):
    """Drop only the files we own. Critical: a stale, non-owner daemon
    receiving SIGTERM (e.g. user kills an orphan PID by hand) must NOT
    unlink the live owner's socket/pidfile/token — doing so causes the
    next retrieve hook to spawn yet another daemon, leading to a zombie
    cascade. We assert ownership via the pidfile contents."""
    own_pid = str(os.getpid())
    is_owner = False
    try:
        is_owner = Path(PID_PATH).read_text().strip() == own_pid
    except (FileNotFoundError, OSError):
        is_owner = False

    if is_owner:
        for p in (SOCKET_PATH, PID_PATH, str(TOKEN_PATH)):
            try:
                os.unlink(p)
            except FileNotFoundError:
                pass
        logging.info("daemon exiting pid=%s (owner cleanup)", own_pid)
    else:
        logging.info(
            "daemon exiting pid=%s (non-owner; leaving socket/pidfile intact)",
            own_pid,
        )
    sys.exit(0)


def main():
    setup_logging()
    logging.info("daemon starting pid=%d version=%s", os.getpid(), PLUGIN_VERSION or "<unknown>")

    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

    _migrate_user_pool_from_sibling_versions()

    auth_token = init_auth_token()
    server = RetrievalServer(auth_token=auth_token)
    sock = bind_socket()
    write_pidfile()
    logging.info("daemon ready on %s", SOCKET_PATH)

    while True:
        try:
            conn, _ = sock.accept()
        except OSError:
            break
        t = threading.Thread(target=server.handle, args=(conn,), daemon=True)
        t.start()


if __name__ == "__main__":
    main()
