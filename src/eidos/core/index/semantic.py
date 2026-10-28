"""Opt-in local Harrier-OSS-v1-0.6B ONNX semantic embedding engine and Hybrid RRF fusion (CMP-SEMANTIC-EMBEDDING-ENGINE).

Graduated from EXP-2026-009 via DLT-2026-115-HYBRID-SEMANTIC-8B5F (DEC-INDEX-HYBRID-SEMANTIC-RRF):
- Uses onnxruntime + tokenizers + numpy when installed via ``eidos-ai[semantic]`` with 0.0ms overhead when absent.
- Enforces right-padding (``direction="right"``, ``pad_id=151643``, ``<|endoftext|>``) for in-graph ``sentence_embedding`` pooling.
- Formats queries with the canonical pre-trained ``sts_query`` instruction prefix (``Instruct: Retrieve semantically similar text\\nQuery: ``).
- Persists content-addressed 1,024-dim float32 vectors in ``spec_embeddings`` keyed by spec-only ``content_sha256``
  (isolated from post-sync AST ``code_symbols`` so source-code edits incur 0.0ms re-embedding overhead).
- Executes sub-millisecond SQLite BLOB + NumPy BLAS cosine similarity scans and gated Reciprocal Rank Fusion (RRF)
  with explicit entity ID pinning (+1.0), multi-term AND boost (+0.25), k0=20, w_lex=1.0, w_sem=0.50, and tau_min=0.50.
"""

from __future__ import annotations

import hashlib
import importlib.util
import math
import os
import sqlite3
import struct
import time
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import orjson

HARRIER_MODEL_ID = "onnx-community/harrier-oss-v1-0.6b-ONNX"
HARRIER_HF_BASE_URL = "https://huggingface.co/onnx-community/harrier-oss-v1-0.6b-ONNX/resolve/main"
HARRIER_EMBEDDING_DIM = 1024
HARRIER_PAD_TOKEN_ID = 151643
HARRIER_PAD_TOKEN = "<|endoftext|>"
HARRIER_MAX_LENGTH = 32768

# Pre-trained canonical MTEB sts_query instruction prefix (config_sentence_transformers.json)
HARRIER_STS_INSTRUCTION = "Instruct: Retrieve semantically similar text\nQuery: "
HARRIER_QUERY_INSTRUCTION = HARRIER_STS_INSTRUCTION

# Calibrated zero-regression Hybrid RRF hyperparameters (EXP-2026-009 Spike 3)
HYBRID_RRF_K0 = 20
HYBRID_RRF_W_LEX = 1.0
HYBRID_RRF_W_SEM = 0.50
HYBRID_RRF_TAU_MIN = 0.50
DEFAULT_RRF_K0 = HYBRID_RRF_K0
DEFAULT_RRF_W_LEX = HYBRID_RRF_W_LEX
DEFAULT_RRF_W_SEM = HYBRID_RRF_W_SEM
DEFAULT_SEMANTIC_MIN_COSINE = HYBRID_RRF_TAU_MIN

_VARIANT_FILES: dict[str, tuple[str, ...]] = {
    "quantized": ("onnx/model_quantized.onnx", "onnx/model_quantized.onnx_data"),
    "fp32": ("onnx/model.onnx", "onnx/model.onnx_data", "onnx/model.onnx_data_1"),
    "fp16": ("onnx/model_fp16.onnx", "onnx/model_fp16.onnx_data"),
}

_METADATA_FILES: tuple[str, ...] = (
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
)

# Process-local singleton cache keyed by (resolved_model_dir, onnx_rel_path)
_SESSION_CACHE: dict[tuple[str, str], SemanticEmbeddingEngine] = {}


def format_harrier_query(query: str) -> str:
    """Format a search or similarity query with Harrier's canonical ``sts_query`` instruction prefix."""
    clean = (query or "").strip()
    if clean.startswith("Instruct:"):
        return clean
    return f"{HARRIER_STS_INSTRUCTION}{clean}"


def format_spec_embedding_document(
    entity_id: str,
    entity_type: str,
    title: str,
    domain: str = "",
    jtbd_or_rule: str = "",
    body_text: str = "",
    spec_symbols: str = "",
) -> str:
    """Construct the deterministic unprefixed specification text used for content-addressed embedding."""
    return (
        f"[{entity_id}] ({entity_type}) {title or entity_id}\n"
        f"Domain: {domain or ''} | Symbols: {spec_symbols or ''}\n"
        f"Rule/JTBD: {jtbd_or_rule or ''}\n"
        f"{body_text or ''}"
    ).strip()


def compute_spec_embedding_sha256(
    entity_id: str,
    entity_type: str,
    title: str,
    domain: str = "",
    jtbd_or_rule: str = "",
    body_text: str = "",
    spec_symbols: str = "",
) -> str:
    """Compute a spec-only SHA-256 digest isolated from derived AST ``code_symbols``."""
    doc_text = format_spec_embedding_document(
        entity_id=entity_id,
        entity_type=entity_type,
        title=title,
        domain=domain,
        jtbd_or_rule=jtbd_or_rule,
        body_text=body_text,
        spec_symbols=spec_symbols,
    )
    return hashlib.sha256(doc_text.encode("utf-8")).hexdigest()


def pack_embedding_f32(values: Sequence[float] | Any) -> bytes:
    """Pack a sequence of floats into an L2-normalized little-endian float32 BLOB."""
    floats = [float(x) for x in values]
    norm = math.sqrt(math.fsum(x * x for x in floats))
    if norm > 1e-12:
        floats = [x / norm for x in floats]
    return struct.pack(f"<{len(floats)}f", *floats)


def unpack_embedding_f32(blob: bytes) -> tuple[float, ...]:
    """Unpack a little-endian float32 BLOB into a tuple of floats."""
    count = len(blob) // 4
    return struct.unpack(f"<{count}f", blob[: count * 4])


def resolve_semantic_model_dir(eidos_dir: Path) -> Path:
    """Return the canonical local cache directory for Harrier ONNX artifacts."""
    return (Path(eidos_dir).resolve() / ".cache" / "models" / "harrier-oss-v1-0.6b").resolve()


def _detect_installed_variant(model_dir: Path) -> str | None:
    """Return the preferred ONNX graph relative path present in ``model_dir``."""
    quant_path = model_dir / "onnx" / "model_quantized.onnx"
    quant_data = model_dir / "onnx" / "model_quantized.onnx_data"
    if quant_path.is_file() and quant_data.is_file():
        return "onnx/model_quantized.onnx"
    fp32_path = model_dir / "onnx" / "model.onnx"
    fp32_data = model_dir / "onnx" / "model.onnx_data"
    if fp32_path.is_file() and fp32_data.is_file():
        return "onnx/model.onnx"
    fp16_path = model_dir / "onnx" / "model_fp16.onnx"
    fp16_data = model_dir / "onnx" / "model_fp16.onnx_data"
    if fp16_path.is_file() and fp16_data.is_file():
        return "onnx/model_fp16.onnx"
    return None


def is_semantic_runtime_available() -> dict[str, bool]:
    """Check whether ``onnxruntime``, ``tokenizers``, and ``numpy`` are importable without importing them eagerly."""
    has_ort = importlib.util.find_spec("onnxruntime") is not None
    has_tok = importlib.util.find_spec("tokenizers") is not None
    has_np = importlib.util.find_spec("numpy") is not None
    return {
        "onnxruntime": has_ort,
        "tokenizers": has_tok,
        "numpy": has_np,
        "available": has_ort and has_tok and has_np,
    }


class SemanticEmbeddingEngine:
    """Zero-PyTorch ONNX Runtime wrapper for ``microsoft/harrier-oss-v1-0.6b`` (`CMP-SEMANTIC-EMBEDDING-ENGINE`)."""

    def __init__(self, model_dir: Path, onnx_rel_path: str | None = None) -> None:
        self.model_dir = Path(model_dir).resolve()
        self.onnx_rel_path = onnx_rel_path or _detect_installed_variant(self.model_dir) or "onnx/model_quantized.onnx"
        self._tokenizer: Any = None
        self._session: Any = None

    def _ensure_loaded(self) -> None:
        if self._tokenizer is not None and self._session is not None:
            return
        import onnxruntime as ort
        from tokenizers import Tokenizer

        tok_path = self.model_dir / "tokenizer.json"
        onnx_path = self.model_dir / self.onnx_rel_path
        if not tok_path.is_file() or not onnx_path.is_file():
            raise FileNotFoundError(
                f"Harrier ONNX artifacts not found under '{self.model_dir}'. Run 'eidos index semantic setup' first."
            )

        tokenizer = Tokenizer.from_file(str(tok_path))
        # CRITICAL (EXP-2026-009 Spike 1): /model/st/pool_0/lasttoken_gather gathers at ReduceSum(attention_mask) - 1,
        # which strictly requires right-padding so the trailing <|endoftext|> (151643) token sits at sum(mask) - 1.
        tokenizer.enable_padding(
            direction="right",
            pad_id=HARRIER_PAD_TOKEN_ID,
            pad_token=HARRIER_PAD_TOKEN,
        )
        tokenizer.enable_truncation(max_length=HARRIER_MAX_LENGTH)

        sess_opts = ort.SessionOptions()
        cpu_threads = min(8, max(1, (os.cpu_count() or 4) // 2))
        sess_opts.intra_op_num_threads = cpu_threads
        if self.onnx_rel_path.endswith("model.onnx"):
            sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        else:
            sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC

        self._session = ort.InferenceSession(
            str(onnx_path),
            sess_options=sess_opts,
            providers=["CPUExecutionProvider"],
        )
        self._tokenizer = tokenizer

    def encode_texts(self, texts: Sequence[str], is_query: bool = False) -> list[bytes]:
        """Encode texts into L2-normalized 1,024-dim float32 BLOBs using in-graph ``sentence_embedding``."""
        if not texts:
            return []
        self._ensure_loaded()
        import numpy as np

        formatted = [format_harrier_query(t) if is_query else t for t in texts]
        encodings = self._tokenizer.encode_batch(formatted)
        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)

        (sentence_embeddings,) = self._session.run(
            ["sentence_embedding"],
            {"input_ids": input_ids, "attention_mask": attention_mask},
        )
        vecs = np.asarray(sentence_embeddings, dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        normalized = np.ascontiguousarray(vecs / np.clip(norms, a_min=1e-12, a_max=None), dtype=np.float32)
        return [normalized[i].tobytes() for i in range(normalized.shape[0])]


def get_semantic_engine(eidos_dir: Path) -> SemanticEmbeddingEngine | None:
    """Return a cached ``SemanticEmbeddingEngine`` if runtime dependencies and model files are present."""
    runtime = is_semantic_runtime_available()
    if not runtime["available"]:
        return None
    model_dir = resolve_semantic_model_dir(eidos_dir)
    variant_rel = _detect_installed_variant(model_dir)
    if variant_rel is None or not (model_dir / "tokenizer.json").is_file():
        return None
    cache_key = (str(model_dir), variant_rel)
    engine = _SESSION_CACHE.get(cache_key)
    if engine is None:
        engine = SemanticEmbeddingEngine(model_dir=model_dir, onnx_rel_path=variant_rel)
        _SESSION_CACHE[cache_key] = engine
    return engine


_CUSTOM_ENCODE_FNS: dict[str, Callable[[list[str]], list[bytes]]] = {}


def encode_semantic_query(text: str, eidos_dir: Path | None = None) -> bytes | None:
    """Encode a search query into a normalized float32 BLOB using the configured semantic engine or custom encoder."""
    if not text or not text.strip() or eidos_dir is None:
        return None
    custom_fn = _CUSTOM_ENCODE_FNS.get(str(eidos_dir.resolve()))
    if custom_fn is not None:
        try:
            blobs = custom_fn([format_harrier_query(text)])
            return blobs[0] if blobs else None
        except Exception:
            return None
    engine = get_semantic_engine(eidos_dir)
    if engine is None:
        return None
    try:
        blobs = engine.encode_texts([text], is_query=True)
        return blobs[0] if blobs else None
    except Exception:
        return None


def get_semantic_index_status(conn: sqlite3.Connection, eidos_dir: Path) -> dict[str, Any]:
    """Return telemetry for the optional semantic embedding index (`eidos index semantic status`)."""
    runtime = is_semantic_runtime_available()
    model_dir = resolve_semantic_model_dir(eidos_dir)
    active_variant = _detect_installed_variant(model_dir)
    tokenizer_ready = (model_dir / "tokenizer.json").is_file()
    model_ready = bool(active_variant and tokenizer_ready)

    embedded_count = 0
    total_sliceable = 0
    matching_fresh = 0
    if conn is not None:
        try:
            row = conn.execute("SELECT COUNT(*) FROM spec_embeddings;").fetchone()
            embedded_count = int(row[0]) if row else 0
            spec_rows = _collect_embeddable_spec_records(conn)
            total_sliceable = len(spec_rows)
            existing_shas = {
                r[0]: r[1] for r in conn.execute("SELECT entity_id, content_sha256 FROM spec_embeddings;").fetchall()
            }
            for rec in spec_rows:
                if existing_shas.get(rec["entity_id"]) == rec["content_sha256"]:
                    matching_fresh += 1
        except sqlite3.Error:
            pass

    return {
        "model_id": HARRIER_MODEL_ID,
        "embedding_dim": HARRIER_EMBEDDING_DIM,
        "model_dir": str(model_dir),
        "model_ready": model_ready,
        "active_variant": active_variant,
        "runtime": runtime,
        "runtime_available": bool(runtime.get("available")),
        "embedded_entities": embedded_count,
        "total_embeddable_entities": total_sliceable,
        "total_sliceable_entities": total_sliceable,
        "fresh_entities": matching_fresh,
        "fresh_embeddings": matching_fresh,
        "stale_or_missing_entities": max(0, total_sliceable - matching_fresh),
        "stale_or_missing_embeddings": max(0, total_sliceable - matching_fresh),
        "hybrid_rrf": {
            "k0": HYBRID_RRF_K0,
            "w_lex": HYBRID_RRF_W_LEX,
            "w_sem": HYBRID_RRF_W_SEM,
            "tau_min": HYBRID_RRF_TAU_MIN,
        },
    }


class _nullcontext:
    def __init__(self, obj: Any) -> None:
        self.obj = obj

    def __enter__(self) -> Any:
        return self.obj

    def __exit__(self, *_: Any) -> None:
        return None


def setup_semantic_model(
    eidos_dir: Path,
    variant: str = "quantized",
    force: bool = False,
    base_url: str = HARRIER_HF_BASE_URL,
) -> dict[str, Any]:
    """Download Harrier-OSS-v1-0.6B ONNX artifacts into ``eidos/.cache/models/harrier-oss-v1-0.6b/``."""
    norm_variant = (variant or "quantized").strip().lower()
    if norm_variant not in _VARIANT_FILES:
        raise ValueError(
            f"Unsupported Harrier ONNX variant '{variant}'. Supported variants: {', '.join(sorted(_VARIANT_FILES))}."
        )
    model_dir = resolve_semantic_model_dir(eidos_dir)
    (model_dir / "onnx").mkdir(parents=True, exist_ok=True)

    rel_files = (*_METADATA_FILES, *_VARIANT_FILES[norm_variant])
    downloaded: list[str] = []
    skipped: list[str] = []

    for rel_path in rel_files:
        dest = model_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_file() and dest.stat().st_size > 0 and not force:
            skipped.append(rel_path)
            continue
        url = f"{base_url.rstrip('/')}/{rel_path}"
        tmp_dest = dest.with_suffix(dest.suffix + ".part")
        req = urllib.request.Request(url, headers={"User-Agent": "eidos-ai/semantic-setup"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(tmp_dest, "wb") as out_f:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                out_f.write(chunk)
        tmp_dest.replace(dest)
        downloaded.append(rel_path)

    return {
        "model_id": HARRIER_MODEL_ID,
        "variant": norm_variant,
        "model_dir": str(model_dir),
        "downloaded": downloaded,
        "skipped": skipped,
    }


def _collect_embeddable_spec_records(conn: sqlite3.Connection) -> list[dict[str, str]]:
    """Collect active specification and invariant records with their spec-only ``content_sha256``."""
    from eidos.core.index.query import compound_identifier_subtokens

    rows = conn.execute(
        """
        SELECT f.entity_id, e.type, e.title, e.domain, e.file_path, f.jtbd_or_rule, f.body_text
        FROM specs_fts f
        JOIN entities e ON e.id = f.entity_id
        WHERE NOT (e.type = 'delta/mutation' AND e.file_path LIKE '%deltas/archive/%')
        ORDER BY f.entity_id;
        """
    ).fetchall()
    records: list[dict[str, str]] = []
    for row in rows:
        entity_id = str(row[0] or "")
        entity_type = str(row[1] or "")
        title = str(row[2] or "")
        domain = str(row[3] or "")
        jtbd_or_rule = str(row[5] or "")
        body_text = str(row[6] or "")
        spec_symbols = compound_identifier_subtokens(f"{title} {jtbd_or_rule} {body_text}").strip()
        doc_text = format_spec_embedding_document(
            entity_id=entity_id,
            entity_type=entity_type,
            title=title,
            domain=domain,
            jtbd_or_rule=jtbd_or_rule,
            body_text=body_text,
            spec_symbols=spec_symbols,
        )
        content_sha = hashlib.sha256(doc_text.encode("utf-8")).hexdigest()
        records.append(
            {
                "entity_id": entity_id,
                "entity_type": entity_type,
                "doc_text": doc_text,
                "content_sha256": content_sha,
            }
        )
    return records


def sync_spec_embeddings(
    conn: sqlite3.Connection,
    eidos_dir: Path,
    force: bool = False,
    batch_size: int = 8,
    encode_fn: Callable[[list[str]], list[bytes]] | None = None,
    model_id: str = HARRIER_MODEL_ID,
) -> dict[str, Any]:
    """Synchronize ``spec_embeddings`` using spec-only ``content_sha256`` caching."""
    t0 = time.perf_counter()
    if encode_fn is None:
        engine = get_semantic_engine(eidos_dir)
        if engine is None:
            raise RuntimeError(
                "Harrier ONNX runtime or model artifacts are not available. "
                "Install 'eidos-ai[semantic]' and run 'eidos index semantic setup'."
            )
        encode_fn = lambda texts: engine.encode_texts(texts, is_query=False)  # noqa: E731
    else:
        _CUSTOM_ENCODE_FNS[str(eidos_dir.resolve())] = encode_fn

    records = _collect_embeddable_spec_records(conn)
    existing: dict[str, tuple[str, str]] = {}
    if not force:
        for row in conn.execute("SELECT entity_id, content_sha256, model_id FROM spec_embeddings;").fetchall():
            existing[str(row[0])] = (str(row[1]), str(row[2]))

    to_encode: list[dict[str, str]] = []
    reused = 0
    for rec in records:
        eid = rec["entity_id"]
        prev = existing.get(eid)
        if not force and prev is not None and prev[0] == rec["content_sha256"] and prev[1] == model_id:
            reused += 1
        else:
            to_encode.append(rec)

    upserted = 0
    step = max(1, int(batch_size))
    for start in range(0, len(to_encode), step):
        batch = to_encode[start : start + step]
        blobs = encode_fn([item["doc_text"] for item in batch])
        with conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO spec_embeddings (entity_id, content_sha256, model_id, embedding)
                VALUES (?, ?, ?, ?);
                """,
                [
                    (item["entity_id"], item["content_sha256"], model_id, blob)
                    for item, blob in zip(batch, blobs, strict=True)
                ],
            )
        upserted += len(batch)

    with conn:
        cur = conn.execute(
            """
            DELETE FROM spec_embeddings
            WHERE entity_id NOT IN (SELECT id FROM entities)
               OR entity_id NOT IN (SELECT entity_id FROM specs_fts);
            """
        )
        pruned = max(0, cur.rowcount or 0)

    duration_ms = (time.perf_counter() - t0) * 1000
    return {
        "model_id": model_id,
        "total_entities": len(records),
        "encoded_entities": upserted,
        "reused_entities": reused,
        "pruned_entities": pruned,
        "pruned_orphans": pruned,
        "duration_ms": round(duration_ms, 2),
    }


def query_semantic_embeddings(
    conn: sqlite3.Connection,
    query_embedding: bytes | Sequence[float],
    limit: int = 20,
    include_types: Sequence[str] | set[str] | None = None,
    exclude_types: Sequence[str] | set[str] = (),
    tau_min: float = HYBRID_RRF_TAU_MIN,
) -> list[dict[str, Any]]:
    """Scan ``spec_embeddings`` in SQLite via NumPy BLAS (or stdlib fallback) and return top cosine matches."""
    q_blob = query_embedding if isinstance(query_embedding, (bytes, bytearray)) else pack_embedding_f32(query_embedding)
    if not q_blob:
        return []

    type_clauses: list[str] = ["NOT (e.type = 'delta/mutation' AND e.file_path LIKE '%deltas/archive/%')"]
    type_params: list[str] = []
    if include_types is not None:
        inc = list(include_types)
        if not inc:
            return []
        type_clauses.append(f"e.type IN ({','.join('?' for _ in inc)})")
        type_params.extend(inc)
    exc = list(exclude_types or ())
    if exc:
        type_clauses.append(f"e.type NOT IN ({','.join('?' for _ in exc)})")
        type_params.extend(exc)
    where_sql = " AND ".join(type_clauses)

    try:
        rows = conn.execute(
            f"""
            SELECT e.id, e.type, e.title, e.domain, e.file_path, e.data_json, s.embedding
            FROM spec_embeddings s
            JOIN entities e ON e.id = s.entity_id
            WHERE {where_sql}
            ORDER BY e.id;
            """,
            tuple(type_params),
        ).fetchall()
    except sqlite3.OperationalError:
        return []

    if not rows:
        return []

    dim = len(q_blob) // 4
    valid_rows = [r for r in rows if len(r[6]) == len(q_blob)]
    if not valid_rows:
        return []

    has_np = importlib.util.find_spec("numpy") is not None
    scores: list[float]
    if has_np:
        import numpy as np

        q_vec = np.frombuffer(q_blob, dtype=np.float32)
        q_norm = float(np.linalg.norm(q_vec))
        if q_norm > 1e-12:
            q_vec = q_vec / q_norm
        matrix = np.frombuffer(b"".join(bytes(r[6]) for r in valid_rows), dtype=np.float32).reshape(
            len(valid_rows), dim
        )
        scores = [float(x) for x in (matrix @ q_vec)]
    else:
        q_tuple = unpack_embedding_f32(bytes(q_blob))
        q_norm = math.sqrt(math.fsum(x * x for x in q_tuple))
        if q_norm > 1e-12:
            q_tuple = tuple(x / q_norm for x in q_tuple)
        scores = []
        for r in valid_rows:
            d_tuple = unpack_embedding_f32(bytes(r[6]))
            scores.append(math.fsum(a * b for a, b in zip(q_tuple, d_tuple, strict=False)))

    scored_items: list[tuple[float, Any]] = [
        (score, row) for score, row in zip(scores, valid_rows, strict=True) if score >= tau_min
    ]
    scored_items.sort(key=lambda item: (-item[0], str(item[1][0])))

    results: list[dict[str, Any]] = []
    for sem_rank, (cos_score, row) in enumerate(scored_items[:limit], start=1):
        data = orjson.loads(row[5])
        raw_sp = data.get("source_path") or ""
        source_path = raw_sp[0] if isinstance(raw_sp, list) and raw_sp else (raw_sp if isinstance(raw_sp, str) else "")
        results.append(
            {
                "id": row[0],
                "type": row[1],
                "title": row[2],
                "domain": row[3],
                "source_path": source_path,
                "file_path": row[4],
                "rank": -float(cos_score),
                "semantic_score": round(float(cos_score), 6),
                "semantic_rank": sem_rank,
                "match_source": "semantic",
                "data": data,
            }
        )
    return results


def fuse_hybrid_rrf(
    lex_results: list[dict[str, Any]],
    sem_results: list[dict[str, Any]],
    *,
    explicit_ids: Sequence[str] = (),
    and_matched_ids: set[str] | Sequence[str] = (),
    limit: int = 10,
    k0: int = HYBRID_RRF_K0,
    w_lex: float = HYBRID_RRF_W_LEX,
    w_sem: float = HYBRID_RRF_W_SEM,
    tau_min: float = HYBRID_RRF_TAU_MIN,
) -> list[dict[str, Any]]:
    """Fuse FTS5 BM25 lexical results and Harrier semantic results via gated Reciprocal Rank Fusion (RRF)."""
    if not sem_results:
        return lex_results[:limit]

    explicit_upper = {str(eid).upper() for eid in explicit_ids if eid}
    and_set = set(and_matched_ids or ())

    by_id: dict[str, dict[str, Any]] = {}
    lex_rank_map: dict[str, int] = {}
    sem_rank_map: dict[str, int] = {}
    sem_score_map: dict[str, float] = {}

    for idx, item in enumerate(lex_results, start=1):
        eid = str(item["id"])
        by_id[eid] = dict(item)
        lex_rank_map[eid] = idx

    for idx, item in enumerate(sem_results, start=1):
        cos_val = float(item.get("semantic_score", -float(item.get("rank", 0.0))))
        if cos_val < tau_min:
            continue
        eid = str(item["id"])
        if eid not in by_id:
            by_id[eid] = dict(item)
        sem_rank_map[eid] = idx
        sem_score_map[eid] = cos_val

    if not sem_rank_map:
        return lex_results[:limit]

    fused: list[tuple[float, int, str, dict[str, Any]]] = []
    for eid, entry in by_id.items():
        score = 0.0
        l_rank = lex_rank_map.get(eid)
        s_rank = sem_rank_map.get(eid)
        if eid.upper() in explicit_upper or (l_rank is not None and float(entry.get("rank", 0.0)) <= -900.0):
            score += 1.0
        if eid in and_set:
            score += 0.25
        if l_rank is not None:
            score += w_lex / (k0 + l_rank)
        if s_rank is not None:
            score += w_sem / (k0 + s_rank)

        if l_rank is not None and s_rank is not None:
            entry["match_source"] = "hybrid"
            entry["bm25_rank"] = l_rank
            entry["lexical_rank"] = l_rank
            entry["semantic_rank"] = s_rank
            entry["semantic_score"] = round(sem_score_map[eid], 6)
        elif s_rank is not None:
            entry["match_source"] = "semantic"
            entry["semantic_rank"] = s_rank
            entry["semantic_score"] = round(sem_score_map[eid], 6)
        else:
            entry["match_source"] = "lexical"
            entry["bm25_rank"] = l_rank
            entry["lexical_rank"] = l_rank

        entry["rrf_score"] = round(score, 6)
        tie_rank = l_rank if l_rank is not None else (1000 + (s_rank or 999))
        fused.append((score, tie_rank, eid, entry))

    fused.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [item[3] for item in fused[:limit]]
