"""Static invariant test binding verification and orphan invariant detection (CMP-DRIFT-DETECTOR).

Extracts invariants declared in capabilities, policies, and C4 components,
and verifies bidirectional static test bindings across polyglot languages.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eidos.core.index.storage import (
    get_cached_spec_document,
    load_spec_validation_cache,
    load_test_binding_cache,
    save_test_binding_cache,
)
from eidos.vcs.diff import POLYGLOT_EXCLUDED_DIRS, is_polyglot_test_file


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
    has_fixture: bool
    functions: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    bindings: list[dict[str, Any]] = field(default_factory=list)
    file_tokens: set[str] = field(default_factory=set)


_INV_OR_POL_TOKEN_RE = re.compile(r"\b(?:INV|POL)-[A-Za-z0-9_-]+\b")


def _has_function_level_fixture(fslice: str, inv_pattern: re.Pattern[str]) -> bool:
    """Return True if eidos:fixture appears in the function header, docstring, or leading standalone comment."""
    if "eidos:fixture" not in fslice:
        return False
    lines = fslice.splitlines()
    if not lines:
        return False

    idx = 0
    seen_fn_decl = False
    decorator_depth = 0
    sig_paren_depth = 0
    # 1. Consume decorator and signature lines (up to ':' in Python or '{' in brace languages)
    while idx < len(lines):
        line = lines[idx]
        if "eidos:fixture" in line:
            return True
        idx += 1
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#["):
            comment_free = stripped.split("//", 1)[0].rstrip()
        else:
            comment_free = stripped.split("#", 1)[0].split("//", 1)[0].rstrip()
        if not comment_free:
            continue
        if not seen_fn_decl:
            if decorator_depth > 0 or stripped.startswith("@") or stripped.startswith("#["):
                decorator_depth += sum(1 for c in comment_free if c in "([{") - sum(
                    1 for c in comment_free if c in ")]}"
                )
                if decorator_depth < 0:
                    decorator_depth = 0
                continue
            seen_fn_decl = True
        sig_paren_depth += comment_free.count("(") - comment_free.count(")")
        if sig_paren_depth < 0:
            sig_paren_depth = 0
        if sig_paren_depth == 0 and (comment_free.endswith(":") or comment_free.endswith("{")):
            break

    # Skip blank lines after signature
    while idx < len(lines) and not lines[idx].strip():
        idx += 1

    # 2. Consume optional Python docstring
    if idx < len(lines):
        stripped = lines[idx].lstrip()
        for quote in ('"""', "'''", 'r"""', "r'''"):
            if stripped.startswith(quote):
                raw_quote = quote[-3:]
                if "eidos:fixture" in lines[idx]:
                    return True
                rest_after_open = stripped[len(quote) :]
                idx += 1
                if raw_quote not in rest_after_open:
                    while idx < len(lines):
                        if "eidos:fixture" in lines[idx]:
                            return True
                        if raw_quote in lines[idx]:
                            idx += 1
                            break
                        idx += 1
                break

    # 3. Consume leading standalone comment lines before the first statement
    while idx < len(lines):
        stripped = lines[idx].strip()
        if not stripped:
            idx += 1
            continue
        if stripped.startswith("/*"):
            while idx < len(lines):
                cur_line = lines[idx]
                if "eidos:fixture" in cur_line and not inv_pattern.search(cur_line):
                    return True
                idx += 1
                if "*/" in cur_line:
                    break
            continue
        if stripped.startswith("#") or stripped.startswith("//"):
            if "eidos:fixture" in stripped and not inv_pattern.search(stripped):
                return True
            idx += 1
            continue
        break

    return False


def _build_cached_test_file_entry(
    rel: str,
    tcontent: str,
    mtime_ns: int,
    sha256: str,
    ast_idx: Any,
) -> tuple[_CachedTestFile, str, str, str]:
    """Extract test functions, fixture-filtered INV tokens, AST bindings, and file-level tokens for caching."""
    from eidos.core.index.ast_indexer import INVARIANT_PATTERN

    has_fixture = "eidos:fixture" in tcontent
    raw_funcs = ast_idx.list_test_functions(rel, tcontent)
    functions: dict[str, dict[str, list[str]]] = {}
    for fname, fslice in raw_funcs.items():
        fn_tokens = list(dict.fromkeys(_INV_OR_POL_TOKEN_RE.findall(fslice)))
        fixture_invs: list[str] = []
        if has_fixture and not _has_function_level_fixture(fslice, INVARIANT_PATTERN):
            for line in fslice.splitlines():
                if "eidos:fixture" in line:
                    continue
                for inv_id in INVARIANT_PATTERN.findall(line):
                    if inv_id not in fixture_invs:
                        fixture_invs.append(inv_id)
        functions[fname] = {
            "tokens": fn_tokens,
            "fixture_invs": fixture_invs,
        }

    raw_bindings = ast_idx.extract_test_bindings(rel, tcontent)
    bindings = [
        {
            "identifier": b.identifier,
            "invariants": list(b.invariants),
            "policies": list(b.policies),
        }
        for b in raw_bindings
    ]
    file_tokens_list = list(dict.fromkeys(_INV_OR_POL_TOKEN_RE.findall(tcontent)))
    entry = _CachedTestFile(
        file_path=rel,
        mtime_ns=mtime_ns,
        sha256=sha256,
        has_fixture=has_fixture,
        functions=functions,
        bindings=bindings,
        file_tokens=set(file_tokens_list),
    )
    return (
        entry,
        json.dumps(functions, sort_keys=True),
        json.dumps(bindings, sort_keys=True),
        json.dumps(file_tokens_list),
    )


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
        from eidos.vcs.diff import get_tracked_files

        tracked_rels = get_tracked_files(root)
        if tracked_rels:
            used_git_tracked = True
            for rel in tracked_rels:
                rel_norm = rel.replace("\\", "/")
                rel_parts = Path(rel_norm).parts
                if any(d in POLYGLOT_EXCLUDED_DIRS or d.startswith(".") for d in rel_parts[:-1]):
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
            dirnames[:] = [d for d in dirnames if d not in POLYGLOT_EXCLUDED_DIRS and not d.startswith(".")]
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
    to_upsert: list[tuple[str, int, str, int, str, str, str]] = []
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
                    has_fixture=bool(row["has_fixture"]),
                    functions=json.loads(row["functions_json"]),
                    bindings=json.loads(row["bindings_json"]),
                    file_tokens=set(json.loads(row["file_tokens_json"])),
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
                    has_fixture=bool(row["has_fixture"]),
                    functions=json.loads(row["functions_json"]),
                    bindings=json.loads(row["bindings_json"]),
                    file_tokens=set(json.loads(row["file_tokens_json"])),
                )
                to_upsert.append(
                    (
                        rel,
                        mtime_ns,
                        sha256,
                        int(bool(row["has_fixture"])),
                        row["functions_json"],
                        row["bindings_json"],
                        row["file_tokens_json"],
                    )
                )
                continue

        if ast_idx is None:
            from eidos.core.index.ast_indexer import get_ast_indexer

            ast_idx = get_ast_indexer()

        tcontent = raw_bytes.decode("utf-8", errors="replace")
        entry, functions_json, bindings_json, file_tokens_json = _build_cached_test_file_entry(
            rel, tcontent, mtime_ns, sha256, ast_idx
        )
        result[rel] = entry
        to_upsert.append(
            (
                rel,
                mtime_ns,
                sha256,
                1 if entry.has_fixture else 0,
                functions_json,
                bindings_json,
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
    uses_cached_entries = any(isinstance(v, _CachedTestFile) for v in all_tests_content.values()) or not all_tests_content
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
                entry, _, _, _ = _build_cached_test_file_entry(tb_file, tcontent, st_mtime_ns, sha256, ast_idx)
                all_tests_content[tb_file] = entry
            else:
                all_tests_content[tb_file] = tcontent
                entry = tcontent

        if isinstance(entry, _CachedTestFile):
            if tb_func:
                candidate_metas: list[dict[str, list[str]]] = []
                if tb_func in entry.functions:
                    candidate_metas.append(entry.functions[tb_func])
                elif "::" not in tb_func:
                    suffix = f"::{parts[-1]}"
                    candidate_metas.extend(
                        meta for k, meta in entry.functions.items() if k == parts[-1] or k.endswith(suffix)
                    )
                if not candidate_metas:
                    return []
                if not any(iid in meta.get("tokens", []) for meta in candidate_metas):
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
                candidate_bodies.extend(
                    body for k, body in funcs.items() if k == parts[-1] or k.endswith(suffix)
                )
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
            if entry.has_fixture:
                qualified_invs_by_suffix: dict[str, set[str]] = {}
                for fname, func_meta in entry.functions.items():
                    if "::" in fname:
                        sfx = fname.rsplit("::", 1)[-1]
                        qualified_invs_by_suffix.setdefault(sfx, set()).update(func_meta.get("fixture_invs", []))
                for fname, func_meta in entry.functions.items():
                    accounted = qualified_invs_by_suffix.get(fname, set()) if "::" not in fname else set()
                    for inv_id in func_meta.get("fixture_invs", []):
                        if inv_id in accounted:
                            continue
                        if inv_id not in known_invariant_ids:
                            key = (inv_id, f"{tpath}::{fname}")
                            if key not in seen:
                                seen.add(key)
                                orphans.append(key)
                continue

            qualified_binding_invs: dict[str, set[str]] = {}
            for b in entry.bindings:
                if b["identifier"].count("::") >= 2:
                    sfx = b["identifier"].rsplit("::", 1)[-1]
                    qualified_binding_invs.setdefault(sfx, set()).update(b.get("invariants", []))
            for binding in entry.bindings:
                ident = binding["identifier"]
                accounted = (
                    qualified_binding_invs.get(ident.rsplit("::", 1)[-1], set())
                    if ident.count("::") == 1
                    else set()
                )
                for inv_id in binding.get("invariants", []):
                    if inv_id in accounted:
                        continue
                    if inv_id not in known_invariant_ids:
                        key = (inv_id, ident)
                        if key not in seen:
                            seen.add(key)
                            orphans.append(key)
        return orphans

    ast_idx = get_ast_indexer()

    for tpath, tcontent in sorted(all_tests_content.items()):
        if "eidos:fixture" in tcontent:
            funcs = ast_idx.list_test_functions(tpath, tcontent)
            func_fixture_invs: dict[str, list[str]] = {}
            for fname, fslice in funcs.items():
                if _has_function_level_fixture(fslice, INVARIANT_PATTERN):
                    func_fixture_invs[fname] = []
                    continue
                invs_in_fn: list[str] = []
                for line in fslice.splitlines():
                    if "eidos:fixture" in line:
                        continue
                    for inv_id in INVARIANT_PATTERN.findall(line):
                        if inv_id not in invs_in_fn:
                            invs_in_fn.append(inv_id)
                func_fixture_invs[fname] = invs_in_fn
            qualified_invs_by_suffix = {}
            for fname, inv_list in func_fixture_invs.items():
                if "::" in fname:
                    sfx = fname.rsplit("::", 1)[-1]
                    qualified_invs_by_suffix.setdefault(sfx, set()).update(inv_list)
            for fname, inv_list in func_fixture_invs.items():
                accounted = qualified_invs_by_suffix.get(fname, set()) if "::" not in fname else set()
                for inv_id in inv_list:
                    if inv_id in accounted:
                        continue
                    if inv_id not in known_invariant_ids:
                        key = (inv_id, f"{tpath}::{fname}")
                        if key not in seen:
                            seen.add(key)
                            orphans.append(key)
            continue

        extracted_bindings = ast_idx.extract_test_bindings(tpath, tcontent)
        qualified_binding_invs = {}
        for b in extracted_bindings:
            if b.identifier.count("::") >= 2:
                sfx = b.identifier.rsplit("::", 1)[-1]
                qualified_binding_invs.setdefault(sfx, set()).update(b.invariants)
        for binding in extracted_bindings:
            accounted = (
                qualified_binding_invs.get(binding.identifier.rsplit("::", 1)[-1], set())
                if binding.identifier.count("::") == 1
                else set()
            )
            for inv_id in binding.invariants:
                if inv_id in accounted:
                    continue
                if inv_id not in known_invariant_ids:
                    key = (inv_id, binding.identifier)
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
        for binding in entry.bindings:
            ident = binding["identifier"]
            for ref in list(binding.get("invariants", [])) + list(binding.get("policies", [])):
                inv_to_test_funcs.setdefault(ref, []).append(ident)

    def _spec_rel(path: Path) -> str:
        return str(path.relative_to(repo_root) if path.is_relative_to(repo_root) else path)

    def _record(inv: Any, spec_path: Path, spec_id: str) -> None:
        iid = getattr(inv, "id", None) if not isinstance(inv, dict) else inv.get("id")
        if not iid:
            return
        tb = (inv.effective_test_binding() or None) if hasattr(inv, "effective_test_binding") else inv.get("test_binding")
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
            if (
                spec_path.name.lower() == "readme.adoc"
                or spec_path.name.startswith("_")
                or spec_path.is_symlink()
            ):
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
            if doc_type == "tech/container":
                for comp in model.components:
                    for inv in comp.invariants:
                        _record(inv, spec_path, str(comp.id or doc_id))
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
    resolved_eidos = (
        (effective_root / eidos_dir).resolve()
        if not eidos_dir.is_absolute()
        else eidos_dir.resolve()
    )
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
            biz = ent.get("business", {})
            catalog["capabilities"].append(
                {
                    "id": eid,
                    "title": title,
                    "domain": ent.get("domain", ""),
                    "jtbd": biz.get("jtbd", "") if isinstance(biz, dict) else "",
                    "path": spec_path,
                }
            )
        elif dtype in ("tech/container", "tech/component"):
            catalog["components"].append(
                {
                    "id": eid,
                    "name": ent.get("name", title),
                    "package_path": ent.get("package_path", ""),
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
                    "owning_container": ent.get("owning_container") or ent.get("owning_component", ""),
                    "owning_component": ent.get("owning_container") or ent.get("owning_component", ""),
                    "path": spec_path,
                }
            )
        elif dtype == "product/policy":
            catalog["policies"].append(
                {
                    "id": eid,
                    "title": title,
                    "category": ent.get("category", ""),
                    "rule": ent.get("rule", ""),
                    "path": spec_path,
                }
            )

    return catalog
