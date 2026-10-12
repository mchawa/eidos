"""Embedded SQLite Architectural Knowledge Graph & Hybrid Context Retrieval Index.

Implements EXP-2026-002:
- Standard library `sqlite3` backed graph store (.eidos/cache/index.db).
- FTS5 virtual table with BM25 ranking for fuzzy and lexical matching.
- Recursive CTE queries for sub-millisecond k-hop blast radius and dependency traversal.
- SHA-256 and mtime dirty-checking for incremental <5ms zero-drift refreshes.
- Tiered context output (Anchor, Guardrails, Deep) for optimal token compression.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from eidos.core.context import ArchitecturalContextSlice
from eidos.core.parser import parse_spec_file


@dataclass
class IndexSyncStats:
    """Statistics from an incremental index sync pass."""

    total_specs_scanned: int = 0
    indexed_new: int = 0
    updated_modified: int = 0
    pruned_deleted: int = 0
    duration_ms: float = 0.0
    was_clean: bool = True


@dataclass
class TieredContextSlice(ArchitecturalContextSlice):
    """Token-budgeted architectural slice matching a query, extending ArchitecturalContextSlice."""

    tier: str = "guardrails"  # "anchor" | "guardrails" | "deep"
    query_latency_ms: float = 0.0
    token_estimate: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def to_markdown(self) -> str:
        """Render markdown formatted for the requested tier."""
        lines = [
            f"# 🧭 Eidos Architectural Context Slice ({self.tier.upper()} Tier)",
            "",
        ]

        query_parts = []
        if self.query.get("files"):
            query_parts.append(f"**Files:** `{', '.join(self.query['files'])}`")
        if self.query.get("component"):
            query_parts.append(f"**Component:** `{self.query['component']}`")
        if self.query.get("task"):
            query_parts.append(f'**Task:** *"{self.query["task"]}"*')
        if query_parts:
            lines.append(" | ".join(query_parts))
            lines.append("")

        # 1. Components
        lines.append(f"## 🧱 C4 Level 2/3 Components ({len(self.components)})")
        if not self.components:
            lines.append("*No components directly matched.*")
        for comp in self.components:
            cid = comp.get("id", "")
            name = comp.get("name", cid)
            ctype = comp.get("container_type", "service")
            runtime = comp.get("runtime", "N/A")
            pkg = comp.get("package_path", "")
            lines.append(f"### `{cid}` ({name})")
            lines.append(f"- **Container:** `{ctype}` | **Runtime:** `{runtime}` | **Package:** `{pkg}`")
            if comp.get("domain_binding"):
                lines.append(f"- **Domain:** `{comp['domain_binding']}`")

            if self.tier in ("guardrails", "deep"):
                deps = (
                    (comp.get("dependencies") or {}).get("internal_components", [])
                    if isinstance(comp.get("dependencies"), dict)
                    else []
                )
                if deps:
                    lines.append(f"- **Internal Dependencies:** {', '.join(f'`{d}`' for d in deps)}")
                contracts = comp.get("contracts") or {}
                prov = contracts.get("provides", []) if isinstance(contracts, dict) else []
                if prov:
                    lines.append(f"- **Provides Contracts:** {', '.join(f'`{p}`' for p in prov)}")
                req = (
                    (contracts.get("requires") or contracts.get("consumes") or [])
                    if isinstance(contracts, dict)
                    else []
                )
                if req:
                    lines.append(f"- **Requires Contracts:** {', '.join(f'`{r}`' for r in req)}")

            if self.tier == "deep":
                modules = comp.get("c4_internal_components", [])
                if modules:
                    lines.append("- **Internal Modules:**")
                    for m in modules:
                        lines.append(f"  - `{m.get('name', '')}`: {m.get('responsibility', '')}")
            lines.append("")

        # 2. Bound Contracts
        lines.append(f"## 📜 Bound Interface Contracts ({len(self.contracts)})")
        if not self.contracts:
            lines.append("*No bound contracts in slice.*")
        for ctr in self.contracts:
            cid = ctr.get("id", "")
            title = ctr.get("title", cid)
            protocol = ctr.get("protocol", "standard")
            owner = ctr.get("owning_component", "N/A")
            lines.append(f"### `{cid}`: {title}")
            lines.append(f"- **Protocol:** `{protocol}` | **Owner:** `{owner}`")
            lines.append("")

        # 3. Domain Capabilities (Guardrails & Deep)
        if self.tier in ("guardrails", "deep"):
            lines.append(f"## 🎯 Domain Capabilities ({len(self.capabilities)})")
            if not self.capabilities:
                lines.append("*No capabilities directly matched.*")
            for cap in self.capabilities:
                cid = cap.get("id", "")
                title = cap.get("title", cid)
                dom = cap.get("domain", "")
                lines.append(f"### `{cid}`: {title}")
                lines.append(f"- **Domain:** `{dom}` | **Status:** `{cap.get('status', 'active')}`")
                if self.tier == "deep" and cap.get("jtbd"):
                    lines.append(f'- **JTBD:** *"{cap["jtbd"]}"*')
                lines.append("")

        # 4. Invariants (Guardrails & Deep)
        if self.tier in ("guardrails", "deep"):
            lines.append(f"## 🔒 Active Invariants & Verification Guardrails ({len(self.invariants)})")
            if not self.invariants:
                lines.append("*No active invariants linked to this slice.*")
            for inv in self.invariants:
                iid = inv.get("id", "")
                rule = inv.get("rule", "")
                tb = inv.get("test_binding", "None")
                cov = "✓ Covered" if inv.get("has_test_coverage") else "⚠️ Uncovered"
                lines.append(f"- **`{iid}`** [{cov}]: {rule}")
                if tb and tb != "None":
                    lines.append(f"  - *Binding:* `{tb}`")
            lines.append("")

        # 5. Policies (Guardrails & Deep)
        if self.tier in ("guardrails", "deep"):
            lines.append(f"## 📋 Governing Policies ({len(self.policies)})")
            if not self.policies:
                lines.append("*No specific governing policies in slice.*")
            for pol in self.policies:
                pid = pol.get("id", "")
                title = pol.get("title", pid)
                rule = pol.get("rule", "")
                lines.append(f"- **`{pid}`** ({title})")
                if rule and self.tier == "deep":
                    lines.append(f"  - *Rule:* {rule}")
            lines.append("")

        lines.append(
            f"> Context Slice generated in {self.query_latency_ms:.2f}ms | Estimated Tokens: ~{self.token_estimate}"
        )
        return "\n".join(lines)


class ArchitecturalIndex:
    """Embedded SQLite Architectural Knowledge Graph & Hybrid Retrieval Index."""

    SCHEMA_VERSION = "1.0.0"

    def __init__(
        self,
        db_path: Path | None = None,
        repo_root: Path | None = None,
        eidos_dir: Path | None = None,
    ) -> None:
        self.root = (repo_root or Path.cwd()).resolve()
        self.eidos_dir = (eidos_dir or (self.root / "eidos")).resolve()

        if db_path is not None:
            self.db_path = Path(db_path).resolve()
        else:
            cache_dir = self.root / ".eidos" / "cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            self.db_path = cache_dir / "index.db"

        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path), timeout=10.0)
            self._conn.row_factory = sqlite3.Row
            # Enable SQLite WAL mode & performance PRAGMAs
            self._conn.execute("PRAGMA journal_mode = WAL;")
            self._conn.execute("PRAGMA synchronous = NORMAL;")
            self._conn.execute("PRAGMA foreign_keys = ON;")
            self._conn.execute("PRAGMA mmap_size = 268435456;")  # 256MB mmap
        return self._conn

    def _init_db(self) -> None:
        """Create tables, indexes, and FTS5 virtual tables."""
        conn = self._get_connection()
        with conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS indexed_files (
                    rel_path TEXT PRIMARY KEY,
                    mtime REAL NOT NULL,
                    sha256 TEXT NOT NULL,
                    doc_id TEXT,
                    doc_type TEXT,
                    last_indexed REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS entities (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT,
                    domain TEXT,
                    package_path TEXT,
                    runtime TEXT,
                    container_type TEXT,
                    file_path TEXT NOT NULL,
                    data_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);
                CREATE INDEX IF NOT EXISTS idx_entities_domain ON entities(domain);

                CREATE TABLE IF NOT EXISTS edges (
                    source_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    rel_type TEXT NOT NULL,
                    metadata_json TEXT,
                    PRIMARY KEY (source_id, target_id, rel_type)
                );
                CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
                CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
                CREATE INDEX IF NOT EXISTS idx_edges_rel ON edges(rel_type);

                CREATE TABLE IF NOT EXISTS symbol_mappings (
                    symbol TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    PRIMARY KEY (symbol, entity_id, kind)
                );
                CREATE INDEX IF NOT EXISTS idx_symbols_symbol ON symbol_mappings(symbol);
                CREATE INDEX IF NOT EXISTS idx_symbols_entity ON symbol_mappings(entity_id);

                -- FTS5 full-text search table for BM25 ranking
                CREATE VIRTUAL TABLE IF NOT EXISTS specs_fts USING fts5(
                    entity_id UNINDEXED,
                    title,
                    jtbd_or_rule,
                    body_text,
                    keywords,
                    symbols,
                    tokenize='porter unicode61'
                );
                """
            )

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def is_dirty(self) -> tuple[bool, list[Path], list[Path]]:
        """Perform sub-5ms dirty checking comparing filesystem mtime with indexed state.

        Returns:
            (is_dirty, modified_or_new_files, deleted_rel_paths)
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT rel_path, mtime, sha256 FROM indexed_files;")
        db_files = {row["rel_path"]: (row["mtime"], row["sha256"]) for row in cursor.fetchall()}

        disk_files: set[str] = set()
        to_update: list[Path] = []

        if self.eidos_dir.exists():
            for p in self.eidos_dir.rglob("*.adoc"):
                if "by-status" in p.parts or p.name.startswith("_") or p.name.lower() == "readme.adoc":
                    continue
                try:
                    rel_p = str(p.relative_to(self.root)).replace("\\", "/")
                except ValueError:
                    rel_p = str(p).replace("\\", "/")
                disk_files.add(rel_p)

                stat = p.stat()
                cached = db_files.get(rel_p)
                if not cached:
                    to_update.append(p)
                elif abs(stat.st_mtime - cached[0]) > 0.001:
                    # mtime changed, compute sha256 to confirm content modification
                    content = p.read_bytes()
                    h = hashlib.sha256(content).hexdigest()
                    if h != cached[1]:
                        to_update.append(p)

        deleted = [Path(rel) for rel in db_files.keys() - disk_files]
        is_dirty = len(to_update) > 0 or len(deleted) > 0
        return is_dirty, to_update, deleted

    def _ensure_synced(self) -> None:
        """Lazily ensure the index is synchronized (<2ms check when clean)."""
        dirty, _, _ = self.is_dirty()
        if dirty:
            self.sync()

    def sync(self, force: bool = False) -> IndexSyncStats:
        """Incrementally synchronize the index with specifications on disk."""
        t0 = time.perf_counter()
        stats = IndexSyncStats()

        if force:
            conn = self._get_connection()
            with conn:
                conn.execute("DELETE FROM indexed_files;")
                conn.execute("DELETE FROM entities;")
                conn.execute("DELETE FROM edges;")
                conn.execute("DELETE FROM symbol_mappings;")
                conn.execute("DELETE FROM specs_fts;")

        dirty, to_update, deleted = self.is_dirty()
        if not dirty and not force:
            stats.duration_ms = (time.perf_counter() - t0) * 1000
            stats.was_clean = True
            return stats

        stats.was_clean = False
        conn = self._get_connection()

        with conn:
            # Handle deletions
            for d in deleted:
                d_rel = str(d).replace("\\", "/")
                row = conn.execute("SELECT doc_id FROM indexed_files WHERE rel_path = ?", (d_rel,)).fetchone()
                if row and row["doc_id"]:
                    doc_id = row["doc_id"]
                    conn.execute("DELETE FROM entities WHERE id = ?", (doc_id,))
                    conn.execute("DELETE FROM edges WHERE source_id = ? OR target_id = ?", (doc_id, doc_id))
                    conn.execute("DELETE FROM symbol_mappings WHERE entity_id = ?", (doc_id,))
                    conn.execute("DELETE FROM specs_fts WHERE entity_id = ?", (doc_id,))
                conn.execute("DELETE FROM indexed_files WHERE rel_path = ?", (d_rel,))
                stats.pruned_deleted += 1

            # Handle updates & new additions
            stats.total_specs_scanned = len(to_update)
            for p in to_update:
                try:
                    rel_p = str(p.relative_to(self.root)).replace("\\", "/")
                except ValueError:
                    rel_p = str(p).replace("\\", "/")

                mtime = p.stat().st_mtime
                content = p.read_bytes()
                sha = hashlib.sha256(content).hexdigest()

                try:
                    doc = parse_spec_file(p)
                except Exception:
                    continue

                doc_id = doc.doc_id or p.stem
                fm = doc.frontmatter or {}
                doc_type = doc.doc_type or "unknown"

                # Check if it was an update or new
                existing = conn.execute("SELECT 1 FROM indexed_files WHERE rel_path = ?", (rel_p,)).fetchone()
                if existing:
                    stats.updated_modified += 1
                    conn.execute("DELETE FROM entities WHERE id = ?", (doc_id,))
                    conn.execute("DELETE FROM edges WHERE source_id = ? OR target_id = ?", (doc_id, doc_id))
                    conn.execute("DELETE FROM symbol_mappings WHERE entity_id = ?", (doc_id,))
                    conn.execute("DELETE FROM specs_fts WHERE entity_id = ?", (doc_id,))
                else:
                    stats.indexed_new += 1

                # 1. Insert entity
                title = doc.title or fm.get("title", doc_id)
                status = fm.get("status", "active")
                domain = fm.get("domain", fm.get("domain_binding", ""))
                package_path = fm.get("package_path", "")
                runtime = fm.get("runtime", "")
                container_type = fm.get("container_type", "")

                conn.execute(
                    """
                    INSERT OR REPLACE INTO entities
                    (id, type, title, status, domain, package_path, runtime, container_type, file_path, data_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        doc_id,
                        doc_type,
                        title,
                        status,
                        domain,
                        package_path,
                        runtime,
                        container_type,
                        rel_p,
                        json.dumps(fm),
                    ),
                )

                # 2. Extract and insert edges
                # Component domain binding
                if domain:
                    conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'realizes', NULL);", (doc_id, domain))
                    conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'domain_member', NULL);", (domain, doc_id))

                # Dependencies
                deps = fm.get("dependencies", {})
                if isinstance(deps, dict):
                    for dep in deps.get("internal_components", []):
                        conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'depends_on', NULL);", (doc_id, dep))

                # Contracts on components
                contracts = fm.get("contracts", {})
                if isinstance(contracts, dict):
                    for prov in contracts.get("provides", []):
                        conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'provides', NULL);", (doc_id, prov))
                    for req in contracts.get("requires") or contracts.get("consumes") or []:
                        conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'consumes', NULL);", (doc_id, req))

                # Contracts standalone specs
                if doc_type == "tech/contract":
                    owning = fm.get("owning_component")
                    if owning:
                        conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'provides', NULL);", (owning, doc_id))
                        conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'owned_by', NULL);", (doc_id, owning))
                    spec_file = fm.get("specification_file")
                    if spec_file:
                        conn.execute(
                            "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'specification_file');",
                            (spec_file, doc_id),
                        )
                    conn.execute(
                        "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'contract_path');", (rel_p, doc_id)
                    )

                # Capability bindings
                biz = fm.get("business", {})
                if isinstance(biz, dict):
                    for pol in biz.get("policies", []):
                        pol_id = Path(pol).stem.upper().split(".")[0]
                        conn.execute(
                            "INSERT OR IGNORE INTO edges VALUES (?, ?, 'governed_by', NULL);", (doc_id, pol_id)
                        )
                        conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'governs', NULL);", (pol_id, doc_id))

                tech = fm.get("technical", {})
                invariants = []
                if isinstance(tech, dict):
                    for comp_ref in tech.get("components", []):
                        conn.execute(
                            "INSERT OR IGNORE INTO edges VALUES (?, ?, 'implements', NULL);", (doc_id, comp_ref)
                        )
                        conn.execute(
                            "INSERT OR IGNORE INTO edges VALUES (?, ?, 'implemented_by', NULL);", (comp_ref, doc_id)
                        )
                    invariants = tech.get("invariants", []) or []

                # Policy invariants
                if doc_type == "product/policy":
                    invariants = fm.get("invariants", []) or []

                # Insert invariants as child entities & edges
                for inv in invariants:
                    iid = inv.get("id")
                    if iid:
                        rule = inv.get("rule", "")
                        test = inv.get("test_binding", "")
                        test_file = test.split("::")[0] if test else ""
                        test_exists = bool(test_file and (self.root / test_file).exists())
                        inv_data = {
                            "id": iid,
                            "rule": rule,
                            "test_binding": test,
                            "source_id": doc_id,
                            "capability_id": doc_id if doc_type == "product/capability" else None,
                            "policy_id": doc_id if doc_type == "product/policy" else None,
                            "has_test_coverage": test_exists,
                            "matching_tests": [test] if test else [],
                        }
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO entities
                            (id, type, title, status, domain, package_path, runtime, container_type, file_path, data_json)
                            VALUES (?, 'invariant', ?, 'active', ?, '', '', '', ?, ?);
                            """,
                            (iid, rule, domain, rel_p, json.dumps(inv_data)),
                        )
                        conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'asserts', NULL);", (doc_id, iid))
                        conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'bound_to', NULL);", (iid, doc_id))

                        if test:
                            conn.execute(
                                "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'test_binding');",
                                (test_file, iid),
                            )

                # 3. Symbol mappings
                if package_path:
                    conn.execute(
                        "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'package_path');", (package_path, doc_id)
                    )

                # Internal C4 modules
                for module in fm.get("c4_internal_components", []):
                    mname = module.get("name")
                    mpath = module.get("source_path")
                    if mname:
                        conn.execute(
                            "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'module_name');", (mname, doc_id)
                        )
                    if mpath:
                        conn.execute(
                            "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'source_path');", (mpath, doc_id)
                        )

                # Interface commands
                if isinstance(fm.get("contracts"), dict):
                    for iface in fm["contracts"].get("interfaces", []):
                        cmd = iface.split()[0]
                        conn.execute(
                            "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'interface_command');", (cmd, doc_id)
                        )

                # 4. Insert into FTS5
                jtbd_or_rule = biz.get("jtbd", fm.get("rule", ""))
                body_clean = re.sub(r"[{}\[\]=:#*`]", " ", doc.body or "")
                keywords = " ".join([doc_id, doc_type, domain, container_type, package_path])
                symbols = " ".join([m.get("name", "") for m in fm.get("c4_internal_components", [])])

                conn.execute(
                    """
                    INSERT INTO specs_fts (entity_id, title, jtbd_or_rule, body_text, keywords, symbols)
                    VALUES (?, ?, ?, ?, ?, ?);
                    """,
                    (doc_id, title, jtbd_or_rule, body_clean, keywords, symbols),
                )

                # Update indexed_files manifest
                conn.execute(
                    """
                    INSERT OR REPLACE INTO indexed_files (rel_path, mtime, sha256, doc_id, doc_type, last_indexed)
                    VALUES (?, ?, ?, ?, ?, ?);
                    """,
                    (rel_p, mtime, sha, doc_id, doc_type, time.time()),
                )

            # Resolve any cross-reference edges where target_id or source_id is a file path
            conn.execute(
                """
                UPDATE OR IGNORE edges
                SET target_id = (SELECT doc_id FROM indexed_files WHERE rel_path = edges.target_id)
                WHERE target_id IN (SELECT rel_path FROM indexed_files);
                """
            )
            conn.execute(
                """
                DELETE FROM edges
                WHERE target_id IN (SELECT rel_path FROM indexed_files);
                """
            )
            conn.execute(
                """
                UPDATE OR IGNORE edges
                SET source_id = (SELECT doc_id FROM indexed_files WHERE rel_path = edges.source_id)
                WHERE source_id IN (SELECT rel_path FROM indexed_files);
                """
            )
            conn.execute(
                """
                DELETE FROM edges
                WHERE source_id IN (SELECT rel_path FROM indexed_files);
                """
            )

        stats.duration_ms = (time.perf_counter() - t0) * 1000
        return stats

    def query_fts(self, text: str, limit: int = 10) -> list[dict[str, Any]]:
        """Execute BM25 ranked full-text query using FTS5."""
        if not text or not text.strip():
            return []

        self._ensure_synced()

        clean_terms = re.findall(r"\w+", text.lower())
        if not clean_terms:
            return []

        fts_query = " OR ".join(f'"{term}"*' for term in clean_terms)
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute(
                """
                SELECT
                    e.id, e.type, e.title, e.domain, e.package_path, e.data_json,
                    bm25(specs_fts) as rank
                FROM specs_fts f
                JOIN entities e ON e.id = f.entity_id
                WHERE specs_fts MATCH ?
                ORDER BY rank
                LIMIT ?;
                """,
                (fts_query, limit),
            )
            results = []
            for row in cursor.fetchall():
                results.append(
                    {
                        "id": row["id"],
                        "type": row["type"],
                        "title": row["title"],
                        "domain": row["domain"],
                        "package_path": row["package_path"],
                        "rank": row["rank"],
                        "data": json.loads(row["data_json"]),
                    }
                )
            return results
        except sqlite3.OperationalError:
            return []

    def get_k_hop_neighborhood(self, entity_id: str, max_depth: int = 2) -> list[dict[str, Any]]:
        """Traverse graph up to k hops in both directions using recursive CTE."""
        self._ensure_synced()
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            WITH RECURSIVE graph_walk(node_id, depth, path) AS (
                SELECT ?, 0, ?
                UNION
                SELECT
                    CASE WHEN e.source_id = gw.node_id THEN e.target_id ELSE e.source_id END,
                    gw.depth + 1,
                    gw.path || '->' || CASE WHEN e.source_id = gw.node_id THEN e.target_id ELSE e.source_id END
                FROM edges e
                JOIN graph_walk gw ON (e.source_id = gw.node_id OR e.target_id = gw.node_id)
                WHERE gw.depth < ?
                  AND gw.path NOT LIKE '%' || CASE WHEN e.source_id = gw.node_id THEN e.target_id ELSE e.source_id END || '%'
            )
            SELECT DISTINCT gw.node_id, gw.depth, ent.type, ent.title, ent.domain, ent.data_json
            FROM graph_walk gw
            JOIN entities ent ON ent.id = gw.node_id
            ORDER BY gw.depth ASC;
            """,
            (entity_id, entity_id, max_depth),
        )

        results = []
        for row in cursor.fetchall():
            results.append(
                {
                    "id": row["node_id"],
                    "depth": row["depth"],
                    "type": row["type"],
                    "title": row["title"],
                    "domain": row["domain"],
                    "data": json.loads(row["data_json"]),
                }
            )
        return results

    def resolve_symbol_or_path(self, query: str) -> list[dict[str, Any]]:
        """Find entity IDs matching source path or symbol."""
        self._ensure_synced()
        conn = self._get_connection()
        cursor = conn.cursor()

        clean = query.strip().replace("\\", "/")
        while clean.startswith("./"):
            clean = clean[2:]

        cursor.execute(
            """
            SELECT s.symbol, s.entity_id, s.kind, e.type, e.title, e.data_json
            FROM symbol_mappings s
            JOIN entities e ON e.id = s.entity_id
            WHERE s.symbol = ?
               OR (? LIKE s.symbol || '/%' AND s.kind = 'package_path')
               OR (? LIKE '%' || s.symbol AND s.kind IN ('source_path', 'specification_file'))
               OR (? LIKE s.symbol || '%' AND s.kind = 'test_binding')
            LIMIT 10;
            """,
            (clean, clean, clean, clean),
        )

        results = []
        for row in cursor.fetchall():
            results.append(
                {
                    "symbol": row["symbol"],
                    "entity_id": row["entity_id"],
                    "kind": row["kind"],
                    "type": row["type"],
                    "title": row["title"],
                    "data": json.loads(row["data_json"]),
                }
            )
        return results

    def get_index_status(self) -> dict[str, Any]:
        """Return comprehensive health and statistics of the architectural index."""
        t0 = time.perf_counter()
        dirty, to_update, to_delete = self.is_dirty()
        dirty_ms = (time.perf_counter() - t0) * 1000

        conn = self._get_connection()
        cursor = conn.cursor()

        total_entities = cursor.execute("SELECT COUNT(*) FROM entities;").fetchone()[0]
        total_edges = cursor.execute("SELECT COUNT(*) FROM edges;").fetchone()[0]
        total_symbols = cursor.execute("SELECT COUNT(*) FROM symbol_mappings;").fetchone()[0]
        total_files = cursor.execute("SELECT COUNT(*) FROM indexed_files;").fetchone()[0]

        type_counts = {}
        for row in cursor.execute("SELECT type, COUNT(*) FROM entities GROUP BY type;").fetchall():
            type_counts[row[0]] = row[1]

        db_size_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0

        return {
            "db_path": str(self.db_path),
            "schema_version": self.SCHEMA_VERSION,
            "total_entities": total_entities,
            "total_edges": total_edges,
            "total_symbols": total_symbols,
            "total_indexed_files": total_files,
            "db_size_bytes": db_size_bytes,
            "is_dirty": dirty,
            "pending_updates": len(to_update),
            "pending_deletions": len(to_delete),
            "dirty_check_latency_ms": round(dirty_ms, 2),
            "entity_counts_by_type": type_counts,
        }

    def get_tiered_context_slice(
        self,
        files: list[str] | str | None = None,
        component: str | None = None,
        task: str | None = None,
        tier: str = "guardrails",
    ) -> TieredContextSlice:
        """Extract a token-budgeted architectural context slice using hybrid indexed retrieval."""
        t0 = time.perf_counter()
        self._ensure_synced()

        matched_components: dict[str, dict[str, Any]] = {}
        matched_contracts: dict[str, dict[str, Any]] = {}
        matched_capabilities: dict[str, dict[str, Any]] = {}
        matched_invariants: dict[str, dict[str, Any]] = {}
        matched_policies: dict[str, dict[str, Any]] = {}
        matched_reasons: dict[str, list[str]] = {}

        conn = self._get_connection()
        cursor = conn.cursor()

        # 1. Resolve by Component parameter
        if component:
            c_clean = component.strip().upper()
            cursor.execute(
                """
                SELECT id, title, type, domain, data_json
                FROM entities
                WHERE type = 'tech/component' AND (id = ? OR id LIKE '%' || ? || '%');
                """,
                (c_clean, c_clean),
            )
            for row in cursor.fetchall():
                cid = row["id"]
                data = json.loads(row["data_json"])
                data["id"] = cid
                data["title"] = row["title"]
                matched_components[cid] = data
                matched_reasons.setdefault(cid, []).append(f"Explicit component parameter '{component}'")

        # 2. Resolve by File paths
        raw_files = [files] if isinstance(files, str) else (files or [])
        clean_files: list[str] = []
        for f in raw_files:
            if not f:
                continue
            for sub in str(f).split(","):
                sub_clean = sub.strip()
                if not sub_clean:
                    continue
                p = Path(sub_clean)
                if p.is_absolute():
                    try:
                        rel = str(p.resolve().relative_to(self.root)).replace("\\", "/")
                        clean_files.append(rel)
                    except ValueError:
                        clean_files.append(sub_clean.replace("\\", "/"))
                else:
                    c = sub_clean.replace("\\", "/")
                    while c.startswith("./"):
                        c = c[2:]
                    clean_files.append(c)

        for fpath in clean_files:
            matches = self.resolve_symbol_or_path(fpath)
            for m in matches:
                eid = m["entity_id"]
                etype = m["type"]
                data = m["data"]
                data["id"] = eid
                data["title"] = m["title"]

                if etype == "tech/component":
                    matched_components[eid] = data
                    matched_reasons.setdefault(eid, []).append(f"Source file match on '{fpath}'")
                elif etype == "invariant":
                    matched_invariants[eid] = data
                    matched_reasons.setdefault(eid, []).append(f"Test binding match on '{fpath}'")
                elif etype == "tech/contract":
                    matched_contracts[eid] = data
                    matched_reasons.setdefault(eid, []).append(f"Contract file match on '{fpath}'")

        # 3. Resolve by Task prompt (FTS5 lexical & semantic search)
        if task and task.strip():
            fts_matches = self.query_fts(task, limit=8)
            for m in fts_matches:
                eid = m["id"]
                etype = m["type"]
                data = m["data"]
                data["id"] = eid
                data["title"] = m["title"]

                reason = f"FTS lexical match on task (BM25 score: {m['rank']:.2f})"
                if etype == "tech/component":
                    matched_components[eid] = data
                    matched_reasons.setdefault(eid, []).append(reason)
                elif etype == "tech/contract":
                    matched_contracts[eid] = data
                    matched_reasons.setdefault(eid, []).append(reason)
                elif etype == "product/capability":
                    matched_capabilities[eid] = data
                    matched_reasons.setdefault(eid, []).append(reason)
                elif etype == "product/policy":
                    matched_policies[eid] = data
                    matched_reasons.setdefault(eid, []).append(reason)
                elif etype == "invariant":
                    matched_invariants[eid] = data
                    matched_reasons.setdefault(eid, []).append(reason)

        # 4. Fallback if no specific filter matched or empty query
        if not matched_components and not matched_capabilities and not clean_files and not component and not task:
            # Full catalog overview: select all components
            cursor.execute("SELECT id, title, type, domain, data_json FROM entities WHERE type = 'tech/component';")
            for row in cursor.fetchall():
                c_data = json.loads(row["data_json"])
                c_data["id"] = row["id"]
                c_data["title"] = row["title"]
                matched_components[row["id"]] = c_data
        elif not matched_components and not matched_capabilities:
            # Filters provided but nothing matched: fallback baseline anchors
            for fb in ["CMP-EIDOS-CLI", "CMP-EIDOS-CORE"]:
                row = cursor.execute(
                    "SELECT id, title, type, domain, data_json FROM entities WHERE id = ?;", (fb,)
                ).fetchone()
                if row:
                    c_data = json.loads(row["data_json"])
                    c_data["id"] = row["id"]
                    c_data["title"] = row["title"]
                    matched_components[row["id"]] = c_data
                    matched_reasons.setdefault(row["id"], []).append("Fallback baseline component anchor")
            if not matched_components:
                for row in cursor.execute(
                    "SELECT id, title, type, domain, data_json FROM entities WHERE type = 'tech/component' LIMIT 2;"
                ).fetchall():
                    c_data = json.loads(row["data_json"])
                    c_data["id"] = row["id"]
                    c_data["title"] = row["title"]
                    matched_components[row["id"]] = c_data
                    matched_reasons.setdefault(row["id"], []).append("Fallback baseline component anchor")

        # 5. Forward dependency expansion for primary matched components
        primary_cids = list(matched_components.keys())
        for cid in primary_cids:
            comp_data = matched_components.get(cid, {})
            deps = (comp_data.get("dependencies") or {}).get("internal_components", [])
            for dep_id in deps:
                if dep_id not in matched_components:
                    row = cursor.execute(
                        "SELECT id, title, type, domain, data_json FROM entities WHERE id = ?;", (dep_id,)
                    ).fetchone()
                    if row:
                        c_data = json.loads(row["data_json"])
                        c_data["id"] = row["id"]
                        c_data["title"] = row["title"]
                        matched_components[row["id"]] = c_data
                        matched_reasons.setdefault(row["id"], []).append(f"Internal dependency of '{cid}'")

        # 6. Include contracts owned by or provided/consumed by primary components
        for cid in primary_cids:
            cursor.execute(
                """
                SELECT id, title, type, domain, data_json FROM entities
                WHERE type = 'tech/contract' AND (
                    id IN (SELECT target_id FROM edges WHERE source_id = ? AND rel_type IN ('provides', 'consumes'))
                    OR id IN (SELECT source_id FROM edges WHERE target_id = ? AND rel_type = 'provides')
                );
                """,
                (cid, cid),
            )
            for row in cursor.fetchall():
                ctr_data = json.loads(row["data_json"])
                ctr_data["id"] = row["id"]
                ctr_data["title"] = row["title"]
                matched_contracts.setdefault(row["id"], ctr_data)

        # 7. Include domain capabilities for primary components' domains
        domains = {c.get("domain_binding") or c.get("domain") for c in [matched_components[cid] for cid in primary_cids]}
        domains.discard("")
        for dom in domains:
            cursor.execute(
                """
                SELECT id, title, type, domain, data_json FROM entities
                WHERE type = 'product/capability' AND domain = ?;
                """,
                (dom,),
            )
            for row in cursor.fetchall():
                cap_data = json.loads(row["data_json"])
                cap_data["id"] = row["id"]
                cap_data["title"] = row["title"]
                matched_capabilities.setdefault(row["id"], cap_data)

        # 8. Invariants & Policies expansion from capabilities
        for cap_id in list(matched_capabilities.keys()):
            # Invariants
            cursor.execute(
                """
                SELECT id, title, data_json FROM entities
                WHERE type = 'invariant' AND id IN (
                    SELECT target_id FROM edges WHERE source_id = ? AND rel_type = 'asserts'
                );
                """,
                (cap_id,),
            )
            for row in cursor.fetchall():
                inv_data = json.loads(row["data_json"])
                inv_data["id"] = row["id"]
                matched_invariants.setdefault(row["id"], inv_data)

            # Policies
            cursor.execute(
                """
                SELECT id, title, data_json FROM entities
                WHERE type = 'product/policy' AND id IN (
                    SELECT target_id FROM edges WHERE source_id = ? AND rel_type = 'governed_by'
                );
                """,
                (cap_id,),
            )
            for row in cursor.fetchall():
                pol_data = json.loads(row["data_json"])
                pol_data["id"] = row["id"]
                pol_data["title"] = row["title"]
                matched_policies.setdefault(row["id"], pol_data)

        # 9. Fallback governing policies if none matched
        if not matched_policies:
            for pid in ["POL-001-GIT-SOURCE-OF-TRUTH", "POL-002-INVARIANT-TEST-ENFORCEMENT"]:
                row = cursor.execute(
                    "SELECT id, title, type, domain, data_json FROM entities WHERE id = ?;", (pid,)
                ).fetchone()
                if row:
                    p_data = json.loads(row["data_json"])
                    p_data["id"] = row["id"]
                    p_data["title"] = row["title"]
                    matched_policies[row["id"]] = p_data
            if not matched_policies:
                for row in cursor.execute(
                    "SELECT id, title, type, domain, data_json FROM entities WHERE type = 'product/policy' LIMIT 2;"
                ).fetchall():
                    p_data = json.loads(row["data_json"])
                    p_data["id"] = row["id"]
                    p_data["title"] = row["title"]
                    matched_policies[row["id"]] = p_data

        # 10. Invariants from governing policies
        for pid in list(matched_policies.keys()):
            cursor.execute(
                """
                SELECT id, title, data_json FROM entities
                WHERE type = 'invariant' AND id IN (
                    SELECT target_id FROM edges WHERE source_id = ? AND rel_type = 'asserts'
                );
                """,
                (pid,),
            )
            for row in cursor.fetchall():
                inv_data = json.loads(row["data_json"])
                inv_data["id"] = row["id"]
                matched_invariants.setdefault(row["id"], inv_data)

        # Assemble and sort lists
        sorted_comps = sorted(matched_components.values(), key=lambda x: x.get("id", ""))
        sorted_ctrs = sorted(matched_contracts.values(), key=lambda x: x.get("id", ""))
        sorted_caps = sorted(matched_capabilities.values(), key=lambda x: x.get("id", ""))
        sorted_invs = sorted(matched_invariants.values(), key=lambda x: x.get("id", ""))
        sorted_pols = sorted(matched_policies.values(), key=lambda x: x.get("id", ""))

        slice_obj = TieredContextSlice(
            tier=tier,
            query={"files": clean_files, "component": component, "task": task},
            components=sorted_comps,
            contracts=sorted_ctrs,
            capabilities=sorted_caps,
            invariants=sorted_invs,
            policies=sorted_pols,
            matched_reasons=matched_reasons,
            query_latency_ms=0.0,
            token_estimate=0,
        )
        slice_obj.token_estimate = max(1, len(slice_obj.to_markdown()) // 4)
        slice_obj.query_latency_ms = (time.perf_counter() - t0) * 1000
        return slice_obj


# Global singleton instance for high-speed module reuse
_INDEX_INSTANCE: ArchitecturalIndex | None = None


def get_architectural_index(
    repo_root: Path | None = None,
    eidos_dir: Path | None = None,
    db_path: Path | None = None,
    force_new: bool = False,
) -> ArchitecturalIndex:
    """Get or create singleton ArchitecturalIndex instance."""
    global _INDEX_INSTANCE
    target_root = (repo_root or Path.cwd()).resolve()
    target_eidos = (eidos_dir or (target_root / "eidos")).resolve()
    if (
        force_new
        or _INDEX_INSTANCE is None
        or _INDEX_INSTANCE.root != target_root
        or _INDEX_INSTANCE.eidos_dir != target_eidos
    ):
        _INDEX_INSTANCE = ArchitecturalIndex(db_path=db_path, repo_root=target_root, eidos_dir=target_eidos)
    return _INDEX_INSTANCE
