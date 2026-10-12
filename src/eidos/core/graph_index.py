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
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

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
class TieredContextSlice:
    """Token-budgeted architectural slice matching a query."""

    tier: str  # "anchor" | "guardrails" | "deep"
    query: dict[str, Any] = field(default_factory=dict)
    components: list[dict[str, Any]] = field(default_factory=list)
    contracts: list[dict[str, Any]] = field(default_factory=list)
    capabilities: list[dict[str, Any]] = field(default_factory=list)
    invariants: list[dict[str, Any]] = field(default_factory=list)
    policies: list[dict[str, Any]] = field(default_factory=list)
    matched_reasons: dict[str, list[str]] = field(default_factory=dict)
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

        # 3. Domain Capabilities
        if self.tier in ("guardrails", "deep"):
            lines.append(f"## 🎯 Domain Capabilities ({len(self.capabilities)})")
            if not self.capabilities:
                lines.append("*No capabilities directly matched.*")
            for cap in self.capabilities:
                cid = cap.get("id", "")
                title = cap.get("title", cid)
                dom = cap.get("domain", "")
                lines.append(f"### `{cid}`: {title}")
                if dom:
                    lines.append(f"- **Domain:** `{dom}`")
                if cap.get("jtbd"):
                    lines.append(f'- **JTBD:** *"{cap["jtbd"]}"*')
                lines.append("")

        # 4. Invariants & Guardrails (Included in Guardrails and Deep)
        if self.tier in ("guardrails", "deep"):
            lines.append(f"## 🛡️ Active Invariants & Verification Guardrails ({len(self.invariants)})")
            if not self.invariants:
                lines.append("*No invariants bound to matched entities.*")
            for inv in self.invariants:
                iid = inv.get("id", "")
                rule = inv.get("rule", "")
                test = inv.get("test_binding", "None")
                status = inv.get("test_status", "UNKNOWN")
                status_icon = "✅" if status == "PASS" else ("❌" if status == "FAIL" else "⏳")
                lines.append(f"- {status_icon} **`{iid}`**: {rule}")
                lines.append(f"  - **Test Binding:** `{test}` ({status})")
            lines.append("")

        # 5. Governing Policies
        if self.tier in ("guardrails", "deep"):
            lines.append(f"## ⚖️ Governing System Policies ({len(self.policies)})")
            if not self.policies:
                lines.append("*No policies directly governing.*")
            for pol in self.policies:
                pid = pol.get("id", "")
                title = pol.get("title", pid)
                rule = pol.get("rule", "")
                lines.append(f"- **`{pid}`** ({title}): {rule}")
            lines.append("")

        lines.append(
            f"> Context Slice generated in {self.query_latency_ms:.2f}ms | Estimated Tokens: ~{self.token_estimate}"
        )
        return "\n".join(lines)


class ArchitecturalIndex:
    """Embedded SQLite-backed knowledge graph and hybrid search index."""

    SCHEMA_VERSION = 1

    def __init__(
        self,
        db_path: Path | None = None,
        repo_root: Path | None = None,
        eidos_dir: Path | None = None,
    ) -> None:
        self.root = (repo_root or Path.cwd()).resolve()
        self.eidos_dir = (eidos_dir or (self.root / "eidos")).resolve()

        if db_path:
            self.db_path = Path(db_path).resolve()
        else:
            cache_dir = self.root / ".eidos" / "cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            self.db_path = cache_dir / "index.db"

        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), timeout=10.0)
            self._conn.row_factory = sqlite3.Row
            # Enable WAL mode for high concurrency and memory caching
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
        return self._conn

    def _init_db(self) -> None:
        """Create tables, indexes, and FTS5 virtual table if they don't exist."""
        conn = self._get_connection()
        with conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
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
                    status TEXT NOT NULL DEFAULT 'active',
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

            # Handle additions & modifications
            for p in to_update:
                stats.total_specs_scanned += 1
                try:
                    rel_p = str(p.relative_to(self.root)).replace("\\", "/")
                except ValueError:
                    rel_p = str(p).replace("\\", "/")

                content_bytes = p.read_bytes()
                sha = hashlib.sha256(content_bytes).hexdigest()
                mtime = p.stat().st_mtime

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
                    # Clean previous associated records
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
                    conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, ?, NULL);", (doc_id, domain, "realizes"))

                # Dependencies
                deps = fm.get("dependencies", {})
                if isinstance(deps, dict):
                    for dep in deps.get("internal_components", []):
                        conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, ?, NULL);", (doc_id, dep, "depends_on"))

                # Contracts
                contracts = fm.get("contracts", {})
                if isinstance(contracts, dict):
                    for prov in contracts.get("provides", []):
                        conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, ?, NULL);", (doc_id, prov, "provides"))
                    for req in contracts.get("requires") or contracts.get("consumes") or []:
                        conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, ?, NULL);", (doc_id, req, "consumes"))

                # Capability business & technical bindings
                biz = fm.get("business", {})
                if isinstance(biz, dict):
                    for pol in biz.get("policies", []):
                        pol_id = Path(pol).stem.upper().split(".")[0]
                        conn.execute(
                            "INSERT OR IGNORE INTO edges VALUES (?, ?, ?, NULL);", (doc_id, pol_id, "governed_by")
                        )

                tech = fm.get("technical", {})
                invariants = []
                if isinstance(tech, dict):
                    for comp_ref in tech.get("components", []):
                        conn.execute(
                            "INSERT OR IGNORE INTO edges VALUES (?, ?, ?, NULL);", (doc_id, comp_ref, "implements")
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
                        inv_data = {"id": iid, "rule": rule, "test_binding": test, "parent": doc_id}
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO entities
                            (id, type, title, status, domain, package_path, runtime, container_type, file_path, data_json)
                            VALUES (?, 'invariant', ?, 'active', ?, '', '', '', ?, ?);
                            """,
                            (iid, rule, domain, rel_p, json.dumps(inv_data)),
                        )
                        conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, ?, NULL);", (doc_id, iid, "asserts"))

                        if test:
                            test_sym = test.split("::")[0]
                            conn.execute(
                                "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, ?);",
                                (test_sym, iid, "test_binding"),
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

        stats.duration_ms = (time.perf_counter() - t0) * 1000
        return stats

    def query_fts(self, text: str, limit: int = 10) -> list[dict[str, Any]]:
        """Execute BM25 ranked full-text query using FTS5."""
        if not text or not text.strip():
            return []

        # Sanitize query text for FTS5 syntax
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
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT s.symbol, s.entity_id, s.kind, e.type, e.title, e.data_json
            FROM symbol_mappings s
            JOIN entities e ON e.id = s.entity_id
            WHERE s.symbol = ? OR ? LIKE '%' || s.symbol || '%'
            LIMIT 15;
            """,
            (query, query),
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

    def get_tiered_context_slice(
        self,
        files: list[str] | str | None = None,
        component: str | None = None,
        task: str | None = None,
        tier: str = "guardrails",
    ) -> TieredContextSlice:
        """Extract a token-budgeted architectural context slice using hybrid indexed retrieval."""
        t0 = time.perf_counter()
        # Guarantee index is synchronized with sub-5ms check
        self.sync()

        matched_components: dict[str, dict[str, Any]] = {}
        matched_contracts: dict[str, dict[str, Any]] = {}
        matched_capabilities: dict[str, dict[str, Any]] = {}
        matched_invariants: dict[str, dict[str, Any]] = {}
        matched_policies: dict[str, dict[str, Any]] = {}
        matched_reasons: dict[str, list[str]] = {}

        # 1. Resolve by Component parameter
        if component:
            c_clean = component.strip().upper()
            conn = self._get_connection()
            cursor = conn.cursor()
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
                c = sub.strip().replace("\\", "/")
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

        # 3. Resolve by Task prompt (FTS5 lexical & semantic search)
        if task and task.strip():
            fts_matches = self.query_fts(task, limit=6)
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

        # 4. Graph expansion (k-hop neighbors for all matched components)
        for cid in list(matched_components.keys()):
            neighbors = self.get_k_hop_neighborhood(cid, max_depth=2)
            for n in neighbors:
                nid = n["id"]
                ntype = n["type"]
                ndata = n["data"]
                ndata["id"] = nid
                ndata["title"] = n["title"]

                if ntype == "tech/contract":
                    matched_contracts.setdefault(nid, ndata)
                elif ntype == "product/capability":
                    matched_capabilities.setdefault(nid, ndata)
                elif ntype == "product/policy":
                    matched_policies.setdefault(nid, ndata)
                elif ntype == "invariant":
                    matched_invariants.setdefault(nid, ndata)

        slice_obj = TieredContextSlice(
            tier=tier,
            query={"files": clean_files, "component": component, "task": task},
            components=list(matched_components.values()),
            contracts=list(matched_contracts.values()),
            capabilities=list(matched_capabilities.values()),
            invariants=list(matched_invariants.values()),
            policies=list(matched_policies.values()),
            matched_reasons=matched_reasons,
            query_latency_ms=0.0,
            token_estimate=0,
        )
        slice_obj.token_estimate = max(1, len(slice_obj.to_markdown()) // 4)
        slice_obj.query_latency_ms = (time.perf_counter() - t0) * 1000
        return slice_obj


# Global singleton instance for high-speed module reuse
_INDEX_INSTANCE: ArchitecturalIndex | None = None


def get_architectural_index(repo_root: Path | None = None, eidos_dir: Path | None = None) -> ArchitecturalIndex:
    global _INDEX_INSTANCE
    if _INDEX_INSTANCE is None:
        _INDEX_INSTANCE = ArchitecturalIndex(repo_root=repo_root, eidos_dir=eidos_dir)
    return _INDEX_INSTANCE
