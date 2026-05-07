"""Long-running retrieval daemon.

Listens on a Unix domain socket. Holds the embedding model + index in memory
so each incoming request completes in ~0.1-1s instead of reloading from scratch.

Protocol (line-delimited JSON over Unix socket):
  Request : {"prompt": "...", "top_k": 3, "min_score": 0.25}
  Response: {"hits": [{"name": ..., "score": ..., "body": ..., ...}, ...],
             "latency_s": 0.12}

Environment variables:
  SUPERSKILLRET_SOCKET    default /tmp/superskillret.sock
  SUPERSKILLRET_DEVICE    default auto (cuda if available, else cpu)
  SUPERSKILLRET_MODEL     default ThakiCloud/SkillRet-Embedding-0.6B
  SUPERSKILLRET_PIDFILE   default /tmp/superskillret.pid
  SUPERSKILLRET_LOG       default /tmp/superskillret.log
"""

import json
import logging
import os
import signal
import socket
import sys
import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
EMB_PATH = ROOT / "cache" / "skill_embeddings.npy"
META_PATH = ROOT / "cache" / "skill_metadata.jsonl"

SOCKET_PATH = os.environ.get("SUPERSKILLRET_SOCKET", "/tmp/superskillret.sock")
PID_PATH = os.environ.get("SUPERSKILLRET_PIDFILE", "/tmp/superskillret.pid")
LOG_PATH = os.environ.get("SUPERSKILLRET_LOG", "/tmp/superskillret.log")
MODEL_NAME = os.environ.get("SUPERSKILLRET_MODEL", "ThakiCloud/SkillRet-Embedding-0.6B")
DEVICE_ENV = os.environ.get("SUPERSKILLRET_DEVICE", "auto")

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


def load_model(device: str):
    from sentence_transformers import SentenceTransformer

    logging.info("loading model %s on %s", MODEL_NAME, device)
    t0 = time.time()
    model = SentenceTransformer(MODEL_NAME, trust_remote_code=True, device=device)
    logging.info("model loaded in %.1fs", time.time() - t0)
    return model


def load_index():
    logging.info("loading index from %s", EMB_PATH)
    embeddings = np.load(EMB_PATH).astype(np.float32)
    metadata = []
    with META_PATH.open(encoding="utf-8") as f:
        for line in f:
            metadata.append(json.loads(line))
    logging.info("loaded %d embeddings, %d metadata records", len(embeddings), len(metadata))
    return embeddings, metadata


class RetrievalServer:
    def __init__(self):
        self.device = pick_device()
        self.model = load_model(self.device)
        self.embeddings, self.metadata = load_index()
        self._lock = threading.Lock()

    def search(self, query: str, top_k: int, min_score: float):
        t0 = time.time()
        with self._lock:
            q_emb = self.model.encode(
                [QUERY_PROMPT + query],
                normalize_embeddings=True,
                convert_to_numpy=True,
            )[0].astype(np.float32)
        sims = self.embeddings @ q_emb
        top_idx = np.argpartition(-sims, min(top_k, len(sims) - 1))[:top_k]
        top_idx = top_idx[np.argsort(-sims[top_idx])]
        hits = []
        for idx in top_idx:
            score = float(sims[idx])
            if score < min_score:
                continue
            rec = self.metadata[int(idx)]
            hits.append({**rec, "score": score})
        return {"hits": hits, "latency_s": time.time() - t0}

    def handle(self, conn: socket.socket):
        try:
            conn.settimeout(30)
            data = b""
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk
                if b"\n" in data:
                    break
            if not data.strip():
                return
            req = json.loads(data.decode("utf-8"))
            if req.get("op") == "ping":
                conn.sendall(b'{"ok": true}\n')
                return
            if req.get("op") == "shutdown":
                conn.sendall(b'{"ok": true}\n')
                logging.info("shutdown requested via socket")
                os.kill(os.getpid(), signal.SIGTERM)
                return
            prompt = req.get("prompt", "")
            top_k = int(req.get("top_k", 3))
            min_score = float(req.get("min_score", 0.25))
            if not prompt.strip():
                conn.sendall(json.dumps({"hits": [], "latency_s": 0.0}).encode() + b"\n")
                return
            result = self.search(prompt, top_k, min_score)
            conn.sendall(json.dumps(result, ensure_ascii=False).encode() + b"\n")
        except Exception as e:
            logging.exception("request failed")
            try:
                conn.sendall(json.dumps({"error": str(e)}).encode() + b"\n")
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


def cleanup(*_):
    try:
        os.unlink(SOCKET_PATH)
    except FileNotFoundError:
        pass
    try:
        os.unlink(PID_PATH)
    except FileNotFoundError:
        pass
    logging.info("daemon exiting")
    sys.exit(0)


def main():
    setup_logging()
    logging.info("daemon starting pid=%d", os.getpid())

    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

    server = RetrievalServer()
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
