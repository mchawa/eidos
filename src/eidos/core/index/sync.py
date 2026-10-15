"""Incremental sync engine and code ownership resolution (CMP-INCREMENTAL-SYNC-ENGINE).

Provides SHA-256 and mtime dirty checking, spec-order-independent source ownership
attribution, and incremental sync passes.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from eidos.core.workspace.project import match_path_pattern

_CODE_EXTS = frozenset({".py", ".ts", ".tsx", ".go", ".rs", ".js", ".jsx", ".java", ".kt", ".kts"})

_EXCLUDED_CODE_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        "dist",
        "build",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".eidos",
        "htmlcov",
        "site",
    }
)

_NON_AST_SYMBOL_KINDS = frozenset(
    {
        "specification_file",
        "contract_path",
        "interface_method",
        "interface_signature",
        "component_ref",
        "test_binding",
        "package_path",
        "owned_path",
        "module_name",
        "source_path",
        "component_id",
        "interface_command",
        "inferred_path",
    }
)


def _normalize_rel_path(path: str) -> str:
    norm = path.strip().replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    return norm.rstrip("/")


@dataclass
class IndexSyncStats:
    """Statistics from an incremental index sync pass."""

    total_specs_scanned: int = 0
    indexed_new: int = 0
    updated_modified: int = 0
    pruned_deleted: int = 0
    duration_ms: float = 0.0
    was_clean: bool = True
    duplicate_id_collisions: list[str] = field(default_factory=list)


class _CodeOwnershipResolver:
    """Deterministic, spec-order-independent attribution of source files to owning containers/components.

    Built once per sync from the persisted specification state, so every file resolves identically no matter
    which spec was processed first. Precedence (DLT-2026-034):

    1. exact component ``source_path`` declared by any container (lowest container id wins; every other declaring
       entity is kept as a secondary owner, see ``owners()``);
    2. highest-scoring ``owned_path`` pattern (``match_path_pattern``);
    3. longest ``package_path`` prefix (path-boundary aware) across all entries of all containers.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._component_sources: dict[str, tuple[str, str]] = {}
        self._declared_by: dict[str, set[str]] = {}
        self._declared_components: dict[tuple[str, str], str] = {}
        for row in conn.execute("SELECT id, data_json FROM entities WHERE type = 'tech/container' ORDER BY id;"):
            try:
                fm = json.loads(row["data_json"])
            except (TypeError, ValueError):
                continue
            for comp in fm.get("components", []) or fm.get("c4_internal_components", []) or []:
                if not isinstance(comp, dict):
                    continue
                raw = comp.get("source_path")
                paths = raw if isinstance(raw, list) else ([raw] if raw else [])
                for sp in paths:
                    norm = _normalize_rel_path(str(sp))
                    if not norm or any(ch in norm for ch in "*?["):
                        continue
                    self._component_sources.setdefault(norm, (row["id"], str(comp.get("id") or "")))
                    self._declared_by.setdefault(norm, set()).add(row["id"])
                    self._declared_components.setdefault((norm, row["id"]), str(comp.get("id") or ""))

        self._owned: list[tuple[str, str]] = [
            (r["symbol"], r["entity_id"])
            for r in conn.execute(
                "SELECT symbol, entity_id FROM symbol_mappings WHERE kind = 'owned_path' ORDER BY entity_id, symbol;"
            )
        ]
        self._packages: list[tuple[str, str]] = []
        for r in conn.execute(
            "SELECT symbol, entity_id FROM symbol_mappings WHERE kind = 'package_path' ORDER BY entity_id, symbol;"
        ):
            norm = _normalize_rel_path(r["symbol"])
            if not norm or any(ch in norm for ch in "*?["):
                continue
            self._packages.append((norm, r["entity_id"]))
            if "." in norm.rsplit("/", 1)[-1]:  # literal file declared as package_path
                self._declared_by.setdefault(norm, set()).add(r["entity_id"])

    def package_dirs(self) -> set[str]:
        """All literal ``package_path`` entries (callers filter for existing directories)."""
        return {pp for pp, _ in self._packages}

    def declared_owners(self, rel_path: str) -> set[str]:
        """Entities that explicitly declare ``rel_path`` as a source/package file."""
        return self._declared_by.get(_normalize_rel_path(rel_path), set())

    def declared_files(self) -> set[str]:
        """Every literal source file explicitly declared by any entity."""
        return set(self._declared_by)

    def owners(self, rel_path: str) -> list[tuple[str, str]]:
        """``(entity_id, component_id)`` owners of a file: the resolved primary owner first, then every other entity
        that explicitly declares the file (sorted by id). Empty when the file is unowned."""
        primary, component_id = self.resolve(rel_path)
        if not primary:
            return []
        rel = _normalize_rel_path(rel_path)
        result = [(primary, component_id)]
        for entity_id in sorted(self._declared_by.get(rel, set()) - {primary}):
            result.append((entity_id, self._declared_components.get((rel, entity_id), "")))
        return result

    def resolve(self, rel_path: str) -> tuple[str | None, str]:
        """Return ``(owning entity id or None, component id or '')`` for a repo-relative source path."""
        rel = _normalize_rel_path(rel_path)
        hit = self._component_sources.get(rel)
        if hit:
            return hit

        best_score = 0
        owner: str | None = None
        for pattern, entity_id in self._owned:
            score = match_path_pattern(rel, pattern)
            if score > best_score:
                best_score, owner = score, entity_id
        if owner:
            return owner, ""

        best_len = -1
        for pkg, entity_id in self._packages:
            if (rel == pkg or rel.startswith(pkg + "/")) and len(pkg) > best_len:
                best_len, owner = len(pkg), entity_id
        return owner, ""
