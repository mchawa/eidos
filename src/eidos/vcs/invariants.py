"""Static invariant test binding verification and orphan invariant detection (CMP-DRIFT-DETECTOR).

Extracts invariants declared in capabilities, policies, and C4 components,
and verifies bidirectional static test bindings across polyglot languages.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import orjson

from eidos.core.index.ast_indexer import (
    _has_function_level_fixture,
    extract_test_file_cache_payload,
)
from eidos.core.index.storage import (
    get_cached_spec_document,
    load_spec_validation_cache,
    load_test_binding_cache,
    save_test_binding_cache,
)
from eidos.core.workspace.project import STANDARD_EXCLUDED_DIRS, get_tracked_files, is_polyglot_test_file


@dataclass
class InvariantTrace:
    """Traceability record linking a living spec invariant to automated tests."""

    invariant_id: str
    rule: str
    spec_path: str
    spec_id: str
    test_binding: str | None
    has_test_coverage: bool
    matching_tests: list[str] = field(default_factory=list)


@dataclass
class _CachedTestFile:
    """Hydrated test file metadata from test_binding_cache."""

    file_path: str
    mtime_ns: int
    sha256: str
    functions: dict[str, list[str]] = field(default_factory=dict)
    file_tokens: set[str] = field(default_factory=set)


def _build_cached_test_file_entry(
    rel: str,
    tcontent: str,
    mtime_ns: int,
    sha256: str,
    ast_idx: Any,
) -> tuple[_CachedTestFile, str, str]:
    """Extract test functions and file-level tokens in a single AST pass for caching."""
    functions, file_tokens_list, functions_json, file_tokens_json = extract_test_file_cache_payload(
        rel, tcontent, ast_idx
    )
    entry = _CachedTestFile(
        file_path=rel,
        mtime_ns=mtime_ns,
        sha256=sha256,
        functions=functions,
        file_tokens=set(file_tokens_list),
    )
    return entry, functions_json, file_tokens_json


def _load_or_update_test_binding_cache(
    repo_root: Path,
    eidos_dir: Path | None = None,
) -> dict[str, _CachedTestFile]:
    """Stat polyglot test files and load or incrementally update test_binding_cache in eidos/.cache/index.db."""
    root = repo_root.resolve()
    if not root.exists():
        return {}

    if eidos_dir is None:
        effective_eidos = (root / "eidos").resolve()
    elif not eidos_dir.is_absolute():
        effective_eidos = (root / eidos_dir).resolve()
    else:
        effective_eidos = eidos_dir.resolve()
    db_path = effective_eidos / ".cache" / "index.db"

    cached_rows: dict[str, sqlite3.Row] = {}
    if effective_eidos.exists():
        cached_rows = load_test_binding_cache(db_path)

    discovered: list[tuple[str, Path, int]] = []
    used_git_tracked = False
    if (root / ".git").exists():
        tracked_rels = get_tracked_files(root)
        if tracked_rels:
            used_git_tracked = True
            for rel in tracked_rels:
                rel_norm = rel.replace("\\", "/")
                rel_parts = Path(rel_norm).parts
                if any(d in STANDARD_EXCLUDED_DIRS or d.startswith(".") for d in rel_parts[:-1]):
                    continue
                file_path = root / rel_norm
                if is_polyglot_test_file(Path(rel_norm), repo_root=root):
                    try:
                        mtime_ns = file_path.stat().st_mtime_ns
                    except OSError:
                        continue
                    discovered.append((rel_norm, file_path, mtime_ns))

    if not used_git_tracked:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in STANDARD_EXCLUDED_DIRS and not d.startswith(".")]
            for fn in filenames:
                file_path = Path(dirpath) / fn
                rel = str(file_path.relative_to(root)).replace("\\", "/")
                if is_polyglot_test_file(Path(rel), repo_root=root):
                    try:
                        mtime_ns = file_path.stat().st_mtime_ns
                    except OSError:
                        continue
                    discovered.append((rel, file_path, mtime_ns))

    result: dict[str, _CachedTestFile] = {}
    seen_rels: set[str] = set()
    to_upsert: list[tuple[str, int, str, str, str]] = []
    ast_idx: Any = None

    for rel, file_path, mtime_ns in sorted(discovered, key=lambda item: item[0]):
        seen_rels.add(rel)
        row = cached_rows.get(rel)
        if row is not None and row["mtime_ns"] == mtime_ns:
            with contextlib.suppress(ValueError, TypeError, KeyError):
                result[rel] = _CachedTestFile(
                    file_path=rel,
                    mtime_ns=mtime_ns,
                    sha256=row["sha256"],
                    functions=orjson.loads(row["functions_json"]),
                    file_tokens=set(orjson.loads(row["file_tokens_json"])),
                )
                continue

        try:
            raw_bytes = file_path.read_bytes()
        except OSError:
            continue
        sha256 = hashlib.sha256(raw_bytes).hexdigest()

        if row is not None and row["sha256"] == sha256:
            with contextlib.suppress(ValueError, TypeError, KeyError):
                result[rel] = _CachedTestFile(
                    file_path=rel,
                    mtime_ns=mtime_ns,
                    sha256=sha256,
                    functions=orjson.loads(row["functions_json"]),
                    file_tokens=set(orjson.loads(row["file_tokens_json"])),
                )
                to_upsert.append(
                    (
                        rel,
                        mtime_ns,
                        sha256,
                        row["functions_json"],
                        row["file_tokens_json"],
                    )
                )
                continue

        if ast_idx is None:
            from eidos.core.index.ast_indexer import get_ast_indexer

            ast_idx = get_ast_indexer()

        tcontent = raw_bytes.decode("utf-8", errors="replace")
        entry, functions_json, file_tokens_json = _build_cached_test_file_entry(
            rel, tcontent, mtime_ns, sha256, ast_idx
        )
        result[rel] = entry
        to_upsert.append(
            (
                rel,
                mtime_ns,
                sha256,
                functions_json,
                file_tokens_json,
            )
        )

    deleted_keys = [k for k in cached_rows if k not in seen_rels]
    if effective_eidos.exists() and (to_upsert or deleted_keys):
        save_test_binding_cache(db_path, to_upsert, deleted_keys)

    return result


def _verify_single_invariant(
    iid: str,
    tb: str | None,
    repo_root: Path,
    all_tests_content: dict[str, Any],
    inv_to_test_funcs: dict[str, list[str]],
    ast_idx: Any = None,
) -> list[str]:
    """Strictly verify static invariant coverage against test_binding (file::func or file) or test suite."""
    uses_cached_entries = (
        any(isinstance(v, _CachedTestFile) for v in all_tests_content.values()) or not all_tests_content
    )
    pattern: re.Pattern[str] | None = None

    if tb:
        parts = [p.strip() for p in tb.split("::") if p.strip()]
        if not parts:
            return []
        tb_file = parts[0].replace("\\", "/")
        tb_func = "::".join(parts[1:]) if len(parts) > 1 else None
        tb_path = repo_root / tb_file
        if not tb_path.exists():
            return []

        entry = all_tests_content.get(tb_file)
        if entry is None:
            try:
                raw_bytes = tb_path.read_bytes()
                tcontent = raw_bytes.decode("utf-8", errors="replace")
            except OSError:
                return []
            if uses_cached_entries:
                if ast_idx is None:
                    from eidos.core.index.ast_indexer import get_ast_indexer

                    ast_idx = get_ast_indexer()
                st_mtime_ns = tb_path.stat().st_mtime_ns if tb_path.exists() else 0
                sha256 = hashlib.sha256(raw_bytes).hexdigest()
                entry, _, _ = _build_cached_test_file_entry(tb_file, tcontent, st_mtime_ns, sha256, ast_idx)
                all_tests_content[tb_file] = entry
            else:
                all_tests_content[tb_file] = tcontent
                entry = tcontent

        if isinstance(entry, _CachedTestFile):
            if tb_func:
                candidate_token_lists: list[list[str]] = []
                if tb_func in entry.functions:
                    candidate_token_lists.append(entry.functions[tb_func])
                elif "::" not in tb_func:
                    suffix = f"::{parts[-1]}"
                    candidate_token_lists.extend(
                        toks for k, toks in entry.functions.items() if k == parts[-1] or k.endswith(suffix)
                    )
                if not candidate_token_lists:
                    return []
                if not any(iid in toks for toks in candidate_token_lists):
                    return []
                return list(dict.fromkeys([f"{tb_file}::{tb_func}", *inv_to_test_funcs.get(iid, [])]))
            if iid in entry.file_tokens:
                return list(dict.fromkeys([tb_file, *inv_to_test_funcs.get(iid, [])]))
            return []

        file_content = str(entry)
        pattern = re.compile(rf"\b{re.escape(iid)}\b")
        if tb_func:
            if ast_idx is None:
                from eidos.core.index.ast_indexer import get_ast_indexer

                ast_idx = get_ast_indexer()
            funcs = ast_idx.list_test_functions(tb_file, file_content)
            candidate_bodies: list[str] = []
            if tb_func in funcs:
                candidate_bodies.append(funcs[tb_func])
            elif "::" not in tb_func:
                suffix = f"::{parts[-1]}"
                candidate_bodies.extend(body for k, body in funcs.items() if k == parts[-1] or k.endswith(suffix))
            if not candidate_bodies:
                return []
            if not any(pattern.search(body) for body in candidate_bodies):
                return []
            return list(dict.fromkeys([f"{tb_file}::{tb_func}", *inv_to_test_funcs.get(iid, [])]))
        if pattern.search(file_content):
            return list(dict.fromkeys([tb_file, *inv_to_test_funcs.get(iid, [])]))
        return []

    matching = list(inv_to_test_funcs.get(iid, []))
    if not matching:
        for tpath, entry in all_tests_content.items():
            if isinstance(entry, _CachedTestFile):
                if iid in entry.file_tokens:
                    matching.append(tpath)
            else:
                if pattern is None:
                    pattern = re.compile(rf"\b{re.escape(iid)}\b")
                if pattern.search(str(entry)):
                    matching.append(tpath)
    return matching


def detect_orphan_invariants(
    repo_root: Path,
    known_invariant_ids: set[str],
    all_tests_content: dict[str, str] | None = None,
    eidos_dir: Path | None = None,
    *,
    cached_tests: dict[str, _CachedTestFile] | None = None,
) -> list[tuple[str, str]]:
    """Find INV-* tokens referenced in tests that do not exist in any living specification."""
    from eidos.core.index.ast_indexer import INVARIANT_PATTERN, get_ast_indexer

    orphans: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    if all_tests_content is None:
        if cached_tests is None:
            cached_tests = _load_or_update_test_binding_cache(repo_root, eidos_dir=eidos_dir)
        for tpath, entry in sorted(cached_tests.items()):
            qualified_invs_by_suffix: dict[str, set[str]] = {}
            for fname, fn_tokens in entry.functions.items():
                if "::" in fname:
                    sfx = fname.rsplit("::", 1)[-1]
                    invs = [t for t in fn_tokens if INVARIANT_PATTERN.fullmatch(t)]
                    qualified_invs_by_suffix.setdefault(sfx, set()).update(invs)
            for fname, fn_tokens in entry.functions.items():
                accounted = qualified_invs_by_suffix.get(fname, set()) if "::" not in fname else set()
                invs = [t for t in fn_tokens if INVARIANT_PATTERN.fullmatch(t)]
                for inv_id in invs:
                    if inv_id in accounted:
                        continue
                    if inv_id not in known_invariant_ids:
                        key = (inv_id, f"{tpath}::{fname}")
                        if key not in seen:
                            seen.add(key)
                            orphans.append(key)
        return orphans

    ast_idx = get_ast_indexer()

    for tpath, tcontent in sorted(all_tests_content.items()):
        has_fixture = "eidos:fixture" in tcontent
        funcs = ast_idx.list_test_functions(tpath, tcontent)
        func_invs: dict[str, list[str]] = {}
        for fname, fslice in funcs.items():
            if has_fixture and _has_function_level_fixture(fslice, INVARIANT_PATTERN):
                func_invs[fname] = []
                continue
            invs_in_fn: list[str] = []
            for line in fslice.splitlines():
                if has_fixture and "eidos:fixture" in line:
                    continue
                for inv_id in INVARIANT_PATTERN.findall(line):
                    if inv_id not in invs_in_fn:
                        invs_in_fn.append(inv_id)
            func_invs[fname] = invs_in_fn
        qualified_invs_by_suffix = {}
        for fname, inv_list in func_invs.items():
            if "::" in fname:
                sfx = fname.rsplit("::", 1)[-1]
                qualified_invs_by_suffix.setdefault(sfx, set()).update(inv_list)
        for fname, inv_list in func_invs.items():
            accounted = qualified_invs_by_suffix.get(fname, set()) if "::" not in fname else set()
            for inv_id in inv_list:
                if inv_id in accounted:
                    continue
                if inv_id not in known_invariant_ids:
                    key = (inv_id, f"{tpath}::{fname}")
                    if key not in seen:
                        seen.add(key)
                        orphans.append(key)

    return orphans


def extract_invariants_and_verify(
    eidos_dir: Path,
    repo_root: Path,
    *,
    cached_tests: dict[str, _CachedTestFile] | None = None,
) -> list[InvariantTrace]:
    """Extract all invariants declared in capabilities, policies, containers, and C4 components, and strictly verify static test bindings."""
    invariants: list[InvariantTrace] = []

    if cached_tests is None:
        cached_tests = _load_or_update_test_binding_cache(repo_root, eidos_dir=eidos_dir)

    inv_to_test_funcs: dict[str, list[str]] = {}
    for entry in cached_tests.values():
        for fname, fn_tokens in entry.functions.items():
            ident = f"{entry.file_path}::{fname}"
            for ref in fn_tokens:
                inv_to_test_funcs.setdefault(ref, []).append(ident)

    def _spec_rel(path: Path) -> str:
        return str(path.relative_to(repo_root) if path.is_relative_to(repo_root) else path)

    def _record(inv: Any, spec_path: Path, spec_id: str) -> None:
        iid = getattr(inv, "id", None) if not isinstance(inv, dict) else inv.get("id")
        if not iid:
            return
        tb = (
            (inv.effective_test_binding() or None)
            if hasattr(inv, "effective_test_binding")
            else inv.get("test_binding")
        )
        rule = (getattr(inv, "rule", None) or "") if not isinstance(inv, dict) else inv.get("rule", "")
        matching = _verify_single_invariant(iid, tb, repo_root, cached_tests, inv_to_test_funcs)
        invariants.append(
            InvariantTrace(
                invariant_id=iid,
                rule=rule,
                spec_path=_spec_rel(spec_path),
                spec_id=spec_id,
                test_binding=tb,
                has_test_coverage=bool(matching),
                matching_tests=matching,
            )
        )

    resolved_repo_root = repo_root.resolve()
    resolved_eidos_dir = eidos_dir.resolve()
    db_path = resolved_eidos_dir / ".cache" / "index.db"
    cached_rows = load_spec_validation_cache(db_path) if db_path.exists() else {}

    scan_roots: list[tuple[str, tuple[str, ...]]] = [
        ("domains", ("product/capability", "product/domain")),
        ("policies", ("product/policy",)),
        ("architecture", ("tech/component", "tech/container")),
    ]
    for sub_dir, doc_types in scan_roots:
        root_dir = eidos_dir / sub_dir
        if not root_dir.exists():
            continue
        for spec_path in sorted(root_dir.rglob("*.adoc")):
            if spec_path.name.lower() == "readme.adoc" or spec_path.name.startswith("_") or spec_path.is_symlink():
                continue
            res_spec = spec_path.resolve()
            rel_key: str | None = None
            for base in (resolved_repo_root, resolved_eidos_dir):
                try:
                    cand = str(res_spec.relative_to(base)).replace("\\", "/")
                    if cand in cached_rows:
                        rel_key = cand
                        break
                except ValueError:
                    continue
            try:
                doc, _, _ = get_cached_spec_document(
                    spec_path,
                    cached_row=cached_rows.get(rel_key) if rel_key else None,
                )
            except OSError:
                continue
            if doc is None:
                continue
            doc_type = doc.doc_type
            doc_id = doc.doc_id
            if doc_type not in doc_types:
                continue
            model = doc.model
            inv_list = [*model.invariants, *model.technical.invariants]
            seen_doc_invs: set[str] = set()
            for inv in inv_list:
                iid = getattr(inv, "id", None) if not isinstance(inv, dict) else inv.get("id")
                if iid and iid in seen_doc_invs:
                    continue
                if iid:
                    seen_doc_invs.add(iid)
                _record(inv, spec_path, doc_id)

    return invariants


def get_living_specs_catalog(eidos_dir: Path, repo_root: Path | None = None) -> dict[str, list[dict]]:
    """Generate a clean, token-efficient catalog of living capabilities, components, and policies for agents."""
    catalog: dict[str, list[dict]] = {
        "capabilities": [],
        "components": [],
        "contracts": [],
        "policies": [],
    }

    effective_root = (repo_root or Path.cwd()).resolve()
    resolved_eidos = (effective_root / eidos_dir).resolve() if not eidos_dir.is_absolute() else eidos_dir.resolve()
    if not resolved_eidos.exists():
        return catalog

    try:
        eidos_prefix = str(resolved_eidos.relative_to(effective_root)).replace("\\", "/").strip("/") or "eidos"
    except ValueError:
        eidos_prefix = resolved_eidos.name or "eidos"

    from eidos.core.index.graph_index import get_architectural_index

    idx = get_architectural_index(repo_root=effective_root, eidos_dir=resolved_eidos)
    entities = idx.get_entities()

    for ent in entities:
        dtype = ent.get("type", "")
        eid = ent.get("id", "")
        title = ent.get("title", "")
        file_p = Path(ent.get("file_path", ""))
        if file_p.is_relative_to(resolved_eidos):
            rel = str(file_p.relative_to(resolved_eidos)).replace("\\", "/")
        elif file_p.is_relative_to(eidos_dir):
            rel = str(file_p.relative_to(eidos_dir)).replace("\\", "/")
        else:
            rel = str(file_p).replace("\\", "/")
        spec_path = f"{eidos_prefix}/{rel}" if not rel.startswith(f"{eidos_prefix}/") else rel

        if dtype == "product/capability":
            catalog["capabilities"].append(
                {
                    "id": eid,
                    "title": title,
                    "domain": ent.get("domain", ""),
                    "jtbd": "",
                    "path": spec_path,
                }
            )
        elif dtype in ("tech/container", "tech/component"):
            catalog["components"].append(
                {
                    "id": eid,
                    "title": ent.get("title", title),
                    "source_path": ent.get("source_path", ""),
                    "runtime": ent.get("runtime", ""),
                    "path": spec_path,
                }
            )
        elif dtype == "tech/contract":
            catalog["contracts"].append(
                {
                    "id": eid,
                    "title": title,
                    "protocol": ent.get("protocol", ""),
                    "container": ent.get("container", ""),
                    "path": spec_path,
                }
            )
        elif dtype == "product/policy":
            catalog["policies"].append(
                {
                    "id": eid,
                    "title": title,
                    "category": ent.get("category", ""),
                    "severity": ent.get("severity", ""),
                    "path": spec_path,
                }
            )

    return catalog


def recommend_invariants_for_unbound_tests(
    index_or_repo_root: Any,
    unbound_tests: list[str | dict[str, Any]] | None = None,
    *,
    eidos_dir: Path | None = None,
    top_k: int = 3,
    use_semantic: bool = True,
) -> dict[str, list[dict[str, Any]]]:
    """Recommend candidate ``INV-*`` bindings for unbound test functions using de-compounded BM25 + optional Hybrid RRF."""
    from eidos.core.index.ast_indexer import INVARIANT_PATTERN
    from eidos.core.index.graph_index import ArchitecturalIndex, get_architectural_index
    from eidos.core.index.query import split_identifier

    if isinstance(index_or_repo_root, ArchitecturalIndex):
        idx = index_or_repo_root
        repo_root = idx.root
        resolved_eidos = idx.eidos_dir
    else:
        repo_root = Path(index_or_repo_root).resolve()
        resolved_eidos = (
            (repo_root / eidos_dir).resolve()
            if eidos_dir is not None and not eidos_dir.is_absolute()
            else (eidos_dir.resolve() if eidos_dir is not None else (repo_root / "eidos").resolve())
        )
        idx = get_architectural_index(repo_root=repo_root, eidos_dir=resolved_eidos)

    test_targets: list[tuple[str, str, str, str]] = []
    if unbound_tests is None:
        from eidos.core.index.ast_indexer import get_ast_indexer

        idx._ensure_synced()
        conn = idx._get_connection()
        ast_idx = get_ast_indexer()
        rows = conn.execute("SELECT file_path, functions_json FROM test_binding_cache ORDER BY file_path;").fetchall()
        if rows:
            for r in rows:
                tpath = str(r[0] or "")
                if not tpath:
                    continue
                try:
                    funcs_map = orjson.loads(r[1] or "{}")
                except (TypeError, ValueError):
                    funcs_map = {}
                abs_test = repo_root / tpath
                raw_funcs: dict[str, str] = {}
                if abs_test.is_file():
                    try:
                        raw_funcs = ast_idx.list_test_functions(
                            tpath, abs_test.read_text(encoding="utf-8", errors="replace")
                        )
                    except OSError:
                        raw_funcs = {}
                candidate_names = sorted(set(raw_funcs.keys()) | set(funcs_map.keys()))
                for fname in candidate_names:
                    if "::" in fname:
                        continue
                    fn_tokens = funcs_map.get(fname, [])
                    if any(INVARIANT_PATTERN.fullmatch(t) for t in fn_tokens):
                        continue
                    test_targets.append((f"{tpath}::{fname}", tpath, fname, ""))
        else:
            cached_tests = _load_or_update_test_binding_cache(repo_root, eidos_dir=resolved_eidos)
            for tpath, entry in sorted(cached_tests.items()):
                abs_test = repo_root / tpath
                raw_funcs = {}
                if abs_test.is_file():
                    try:
                        raw_funcs = ast_idx.list_test_functions(
                            tpath, abs_test.read_text(encoding="utf-8", errors="replace")
                        )
                    except OSError:
                        raw_funcs = {}
                candidate_names = sorted(set(raw_funcs.keys()) | set(entry.functions.keys()))
                for fname in candidate_names:
                    if "::" in fname:
                        continue
                    fn_tokens = entry.functions.get(fname, [])
                    if any(INVARIANT_PATTERN.fullmatch(t) for t in fn_tokens):
                        continue
                    test_targets.append((f"{tpath}::{fname}", tpath, fname, ""))
    else:
        for item in unbound_tests:
            if isinstance(item, str):
                ref = item.strip()
                if "::" in ref:
                    tpath, fname = ref.split("::", 1)
                else:
                    tpath, fname = "", ref
                test_targets.append((ref, tpath, fname, ""))
            elif isinstance(item, dict):
                ref = str(item.get("test_ref") or item.get("id") or item.get("function") or "").strip()
                tpath = str(item.get("file_path") or (ref.split("::", 1)[0] if "::" in ref else "")).strip()
                fname = str(item.get("function") or (ref.split("::", 1)[-1] if "::" in ref else ref)).strip()
                extra = str(item.get("docstring") or item.get("description") or item.get("query") or "").strip()
                if not ref:
                    ref = f"{tpath}::{fname}" if tpath else fname
                test_targets.append((ref, tpath, fname, extra))

    recommendations: dict[str, list[dict[str, Any]]] = {}
    noise_tokens = {"test", "tests", "verify", "check", "assert", "should", "must", "py", "ts", "js"}
    for ref, tpath, fname, extra in test_targets:
        stem = Path(tpath).stem if tpath else ""
        sub_tokens = [
            tok
            for tok in [*split_identifier(fname), *split_identifier(stem), *split_identifier(extra)]
            if tok and tok not in noise_tokens and len(tok) > 1
        ]
        query_text = " ".join(dict.fromkeys([*sub_tokens, *(extra.split() if extra else [])]))
        if not query_text:
            recommendations[ref] = []
            continue

        hits = idx.query_fts(
            query_text,
            limit=max(top_k * 4, 12),
            include_types=("invariant",),
            use_semantic=use_semantic,
        )
        scored: list[tuple[float, dict[str, Any]]] = []
        for rank_idx, hit in enumerate(hits):
            inv_data = hit.get("data") if isinstance(hit.get("data"), dict) else {}
            tb_str = str(inv_data.get("test_binding") or "")
            tb_file = tb_str.split("::", 1)[0] if tb_str else ""
            rrf_val = hit.get("rrf_score")
            base_score = float(rrf_val) if rrf_val is not None else (1.0 / (20.0 + rank_idx + 1))
            if tpath and tb_file and tb_file == tpath:
                base_score += 0.02
            scored.append(
                (
                    base_score,
                    {
                        "id": hit["id"],
                        "rule": hit["title"],
                        "source_id": inv_data.get("source_id", ""),
                        "test_binding": tb_str,
                        "score": round(base_score, 6),
                        "match_source": hit.get("match_source", "lexical"),
                    },
                )
            )
        scored.sort(key=lambda item: (-item[0], item[1]["id"]))
        recommendations[ref] = [entry for _, entry in scored[:top_k]]

    return recommendations
