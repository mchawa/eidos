"""SpecDelta Deletion and Deprecation Blast-Radius Audit Engine.

Implements INV-DELTA-DELETION-AUDIT:
- Graph-level invariant verification via embedded SQLite ArchitecturalIndex (index.db).
- Context-aware semantic reference discovery across the codebase.
- Semantic classification distinguishing legitimate records (historical ledgers, negative
  regression tests, cleanup routines, tombstone exemptions) from active leaks.
- Structured Rich table reporting and machine-readable JSON payloads for AI self-healing.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import orjson

from eidos.core.index.graph_index import get_architectural_index
from eidos.core.index.sync import is_test_code_path
from eidos.core.lifecycle.catalog import find_spec_by_id_or_path
from eidos.core.spec.parser import parse_spec_file


@dataclass
class AuditFinding:
    """A textual reference match found in the codebase and its classification."""

    file_path: str
    line_number: int
    matched_term: str
    line_snippet: str
    classification: str
    is_approved: bool
    reason: str
    risk_level: str  # "low", "medium", "high", "critical"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GraphViolation:
    """A dangling incoming edge in the architectural knowledge graph."""

    source_id: str
    target_id: str
    rel_type: str
    source_file: str
    source_type: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DeltaAuditResult:
    """Result of an automated reverse-dependency blast-radius audit."""

    delta_id: str
    status: str  # "PASSED", "LEAKS_DETECTED", "NO_DELETIONS", "ERROR"
    is_clean: bool
    targets_audited: list[str] = field(default_factory=list)
    search_tokens: list[str] = field(default_factory=list)
    graph_violations: list[GraphViolation] = field(default_factory=list)
    approved_findings: list[AuditFinding] = field(default_factory=list)
    unacknowledged_findings: list[AuditFinding] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "delta_id": self.delta_id,
            "status": self.status,
            "is_clean": self.is_clean,
            "targets_audited": self.targets_audited,
            "search_tokens": self.search_tokens,
            "graph_violations": [v.to_dict() for v in self.graph_violations],
            "approved_findings": [f.to_dict() for f in self.approved_findings],
            "unacknowledged_findings": [f.to_dict() for f in self.unacknowledged_findings],
            "summary": self.summary,
            "error_message": self.error_message,
        }

    def to_json(self, indent: int = 2) -> str:
        option = orjson.OPT_INDENT_2 if indent else 0
        return orjson.dumps(self.to_dict(), option=option).decode("utf-8")


class AuditEngine:
    """Performs context-aware reverse blast-radius audits for SpecDeltas."""

    IGNORE_DIRS: ClassVar[set[str]] = {
        ".git",
        ".venv",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "node_modules",
        "dist",
        "build",
        "site",
        ".cache",
        ".eidos",
    }
    IGNORE_EXTS: ClassVar[set[str]] = {
        ".pyc",
        ".db",
        ".sqlite",
        ".sqlite3",
        ".lock",
        ".png",
        ".jpg",
        ".jpeg",
        ".ico",
        ".wasm",
        ".gz",
        ".tar",
        ".zip",
        ".tgz",
    }
    SKIP_EXTENSIONS: ClassVar[set[str]] = IGNORE_EXTS

    def __init__(self, repo_root: Path | None = None, eidos_dir: Path | None = None):
        if repo_root is not None:
            self.repo_root = Path(repo_root).resolve()
        elif eidos_dir is not None:
            edir_resolved = Path(eidos_dir).resolve()
            self.repo_root = edir_resolved.parent
        else:
            self.repo_root = Path.cwd().resolve()

        self.eidos_dir = self.repo_root / "eidos" if eidos_dir is None else Path(eidos_dir).resolve()

    def audit_delta(self, delta_id_or_path: str) -> DeltaAuditResult:
        """Run complete deletion audit on a SpecDelta."""
        delta_file = find_spec_by_id_or_path(self.eidos_dir / "deltas", delta_id_or_path)
        if not delta_file:
            delta_file = find_spec_by_id_or_path(self.eidos_dir, delta_id_or_path)

        if not delta_file or not delta_file.exists():
            return DeltaAuditResult(
                delta_id=delta_id_or_path,
                status="ERROR",
                is_clean=False,
                error_message=f"SpecDelta '{delta_id_or_path}' not found in '{self.eidos_dir}'.",
            )

        try:
            delta_doc = parse_spec_file(delta_file)
        except Exception as e:
            return DeltaAuditResult(
                delta_id=delta_id_or_path,
                status="ERROR",
                is_clean=False,
                error_message=f"Failed to parse SpecDelta '{delta_file}': {e}",
            )

        if delta_doc.errors or not delta_doc.frontmatter:
            return DeltaAuditResult(
                delta_id=delta_id_or_path,
                status="ERROR",
                is_clean=False,
                error_message=f"Failed to parse SpecDelta '{delta_file}': {'; '.join(delta_doc.errors) or 'Missing frontmatter'}",
            )

        if delta_doc.doc_type != "delta/mutation":
            return DeltaAuditResult(
                delta_id=delta_id_or_path,
                status="ERROR",
                is_clean=False,
                error_message=f"Document at '{delta_file}' is not a SpecDelta (type: {delta_doc.doc_type}).",
            )

        delta_id = delta_doc.frontmatter.get("id") or delta_file.stem.upper()
        targets, search_tokens, tombstone = self._extract_targets_and_tokens(delta_doc)

        if not targets and not search_tokens:
            return DeltaAuditResult(
                delta_id=delta_id,
                status="NO_DELETIONS",
                is_clean=True,
                targets_audited=[],
                search_tokens=[],
                summary={"approved": 0, "unacknowledged": 0, "graph_violations": 0},
            )

        # Phase 1: Graph-level foreign key checks (fail loudly if index cannot be read)
        try:
            graph_violations = self._check_graph_integrity(targets, delta_id)
        except Exception as exc:
            return DeltaAuditResult(
                delta_id=delta_id,
                status="ERROR",
                is_clean=False,
                targets_audited=sorted(targets),
                search_tokens=sorted(search_tokens),
                error_message=f"Graph integrity verification failed: {exc}",
            )

        # Phase 2: Semantic reference scan and classification
        approved, unacknowledged = self._scan_and_classify_references(
            targets=targets,
            search_tokens=search_tokens,
            delta_file=delta_file,
            tombstone=tombstone,
        )

        is_clean = len(graph_violations) == 0 and len(unacknowledged) == 0
        status = "PASSED" if is_clean else "LEAKS_DETECTED"

        summary = {
            "approved": len(approved),
            "unacknowledged": len(unacknowledged),
            "graph_violations": len(graph_violations),
            "historical_ledgers": sum(1 for f in approved if f.classification == "historical_ledger"),
            "negative_tests": sum(1 for f in approved if f.classification == "negative_test"),
            "cleanup_routines": sum(1 for f in approved if f.classification == "cleanup_routine"),
            "tombstone_exemptions": sum(1 for f in approved if f.classification == "tombstone_exempt"),
            "documentation_rationales": sum(1 for f in approved if f.classification == "documentation_rationale"),
        }

        return DeltaAuditResult(
            delta_id=delta_id,
            status=status,
            is_clean=is_clean,
            targets_audited=sorted(targets),
            search_tokens=sorted(search_tokens),
            graph_violations=graph_violations,
            approved_findings=approved,
            unacknowledged_findings=unacknowledged,
            summary=summary,
        )

    def _extract_targets_and_tokens(self, delta_doc: Any) -> tuple[set[str], set[str], dict[str, Any]]:
        """Extract deleted or deprecated target entity IDs and associated search tokens."""
        fm = delta_doc.frontmatter
        mutations = fm.get("mutations", [])
        targets: set[str] = set()
        search_tokens: set[str] = set()

        from eidos.core.lifecycle.delta_engine import _extract_mutation_target, normalize_delta_mutation

        _child_prefixes_by_parent: dict[str, tuple[str, ...]] = {
            "CNT-": ("CMP-", "INV-"),
            "CMP-": ("INV-",),
            "CAP-": ("INV-",),
            "POL-": ("INV-",),
            "DOM-": ("CAP-", "PER-", "INV-"),
            "DS-": ("UIC-", "SCR-", "FLOW-"),
            "CLT-": ("SCR-", "FLOW-", "UIC-"),
        }
        _replacement_clause_re = re.compile(
            r"(?:\b(?:into|by|with|to|from|in|under|via|for|favor\s+of|see|replaces?|replaced|"
            r"consolidated|merged|migrated|moved|superseded|succeeded|retained|kept|preserving)\b|->|=>)"
            r"[^.;:\n()]{0,40}$",
            re.IGNORECASE,
        )

        normalized_mutations = [normalize_delta_mutation(raw_m) for raw_m in mutations if isinstance(raw_m, dict)]
        non_removed_ids: set[str] = set()
        for m in normalized_mutations:
            op = str(m.get("operation", "")).strip().lower()
            t_id = _extract_mutation_target(m)
            p_id = str(m.get("parent") or "").strip()
            if p_id:
                non_removed_ids.add(p_id)
            if op not in ("remove", "deprecate") and t_id and t_id != "unspecified_target":
                non_removed_ids.add(t_id)

        for m in normalized_mutations:
            operation = str(m.get("operation", "")).strip().lower()
            if operation in ("remove", "deprecate"):
                primary_target = _extract_mutation_target(m)
                has_concrete_primary = bool(
                    primary_target
                    and primary_target != "unspecified_target"
                    and not primary_target.endswith(".adoc")
                    and "/" not in primary_target
                )
                if has_concrete_primary:
                    targets.add(primary_target)
                elif primary_target and primary_target != "unspecified_target":
                    clean_p = primary_target.replace("\\", "/").lstrip("./")
                    if clean_p.startswith("eidos/"):
                        clean_p = clean_p[len("eidos/") :]
                    search_tokens.add(clean_p)
                    candidate_files = [
                        self.eidos_dir / clean_p,
                        self.repo_root / primary_target,
                        self.eidos_dir / primary_target,
                    ]
                    resolved_id = ""
                    for cf in candidate_files:
                        if cf.exists() and cf.is_file():
                            try:
                                doc = parse_spec_file(cf)
                                doc_id = str(doc.frontmatter.get("id") or "").strip()
                                if doc_id:
                                    resolved_id = doc_id
                                    break
                            except Exception:
                                pass
                    if not resolved_id:
                        from eidos.core.lifecycle.delta_engine import resolve_entity_prefix_meta

                        clean_path_obj = Path(clean_p)
                        stem_upper = clean_path_obj.stem.upper()
                        parent_upper = clean_path_obj.parent.name.upper()
                        if resolve_entity_prefix_meta(stem_upper) is not None:
                            resolved_id = stem_upper
                        elif "architecture/components/" in clean_p:
                            resolved_id = stem_upper if stem_upper.startswith("CMP-") else f"CMP-{stem_upper}"
                        elif "architecture/containers/" in clean_p:
                            resolved_id = stem_upper if stem_upper.startswith("CNT-") else f"CNT-{stem_upper}"
                        elif "architecture/contracts/" in clean_p:
                            resolved_id = stem_upper if stem_upper.startswith("CTR-") else f"CTR-{stem_upper}"
                        elif "policies/" in clean_p:
                            resolved_id = stem_upper if stem_upper.startswith("POL-") else f"POL-{stem_upper}"
                        elif "personas/" in clean_p:
                            resolved_id = stem_upper if stem_upper.startswith("PER-") else f"PER-{stem_upper}"
                        elif "/capabilities/" in clean_p:
                            resolved_id = stem_upper if stem_upper.startswith("CAP-") else f"CAP-{stem_upper}"
                        elif "domains/" in clean_p:
                            dom_token = parent_upper if stem_upper == "DOMAIN" and parent_upper else stem_upper
                            resolved_id = dom_token if dom_token.startswith("DOM-") else f"DOM-{dom_token}"
                        elif "design/systems/" in clean_p and "/components/" in clean_p:
                            resolved_id = stem_upper if stem_upper.startswith("UIC-") else f"UIC-{stem_upper}"
                        elif "design/systems/" in clean_p:
                            ds_token = parent_upper if stem_upper == "SYSTEM" and parent_upper else stem_upper
                            resolved_id = ds_token if ds_token.startswith("DS-") else f"DS-{ds_token}"
                        elif "/screens/" in clean_p or clean_p.startswith("design/screens/"):
                            resolved_id = stem_upper if stem_upper.startswith("SCR-") else f"SCR-{stem_upper}"
                        elif "/flows/" in clean_p or clean_p.startswith("design/flows/"):
                            resolved_id = stem_upper if stem_upper.startswith("FLOW-") else f"FLOW-{stem_upper}"
                        elif "design/clients/" in clean_p:
                            clt_token = parent_upper if stem_upper == "CLIENT" and parent_upper else stem_upper
                            resolved_id = clt_token if clt_token.startswith("CLT-") else f"CLT-{clt_token}"
                        elif "explorations/" in clean_p:
                            resolved_id = stem_upper if stem_upper.startswith("EXP-") else f"EXP-{stem_upper}"
                    if resolved_id:
                        targets.add(resolved_id)
                        primary_target = resolved_id
                        has_concrete_primary = True

                parent_val = str(m.get("parent") or "").strip()
                parent_context_ids = {parent_val} if parent_val and parent_val != primary_target else set()
                allowed_detail_prefixes: tuple[str, ...] = (
                    "CNT-",
                    "CMP-",
                    "CTR-",
                    "CAP-",
                    "POL-",
                    "DOM-",
                    "PER-",
                    "DS-",
                    "SCR-",
                    "UIC-",
                    "CLT-",
                    "FLOW-",
                    "INV-",
                )
                if has_concrete_primary:
                    primary_prefix = primary_target.split("-", 1)[0] + "-"
                    allowed_detail_prefixes = _child_prefixes_by_parent.get(primary_prefix, ())

                details = str(m.get("details", ""))
                for m_obj in re.finditer(r"\b([A-Z]{2,4}-[A-Z0-9_-]+)\b", details):
                    match = m_obj.group(1)
                    if (
                        match == primary_target
                        or match in parent_context_ids
                        or match in non_removed_ids
                        or not allowed_detail_prefixes
                        or not any(match.startswith(p) for p in allowed_detail_prefixes)
                    ):
                        continue
                    preceding = details[max(0, m_obj.start() - 60) : m_obj.start()]
                    if _replacement_clause_re.search(preceding):
                        continue
                    targets.add(match)

        tombstone = fm.get("tombstone", {})
        if isinstance(tombstone, dict):
            extra_tokens = tombstone.get("search_tokens", [])
            if isinstance(extra_tokens, list):
                for token in extra_tokens:
                    if isinstance(token, str) and token.strip():
                        search_tokens.add(token.strip())

        return targets, search_tokens, tombstone if isinstance(tombstone, dict) else {}

    def _check_graph_integrity(self, targets: set[str], delta_id: str) -> list[GraphViolation]:
        """Check SQLite knowledge graph for any active incoming edges pointing to deleted targets."""
        violations: list[GraphViolation] = []
        index = get_architectural_index(repo_root=self.repo_root, eidos_dir=self.eidos_dir)
        index.sync()
        for target in targets:
            refs = index.get_incoming_references(target)
            for r in refs:
                src = r.get("source_id", "")
                if (
                    src == delta_id
                    or src.startswith(("DLT-", "EXP-"))
                    or r.get("type") == "product/exploration"
                    or src in targets
                ):
                    # Deltas, explorations, and co-removed targets are not living spec violations
                    continue
                violations.append(
                    GraphViolation(
                        source_id=src,
                        target_id=target,
                        rel_type=r.get("rel_type", "references"),
                        source_file=r.get("file_path", ""),
                        source_type=r.get("type", "unknown"),
                        message=f"Living spec '{src}' ({r.get('type')}) still has active '{r.get('rel_type')}' edge to removed entity '{target}'",
                    )
                )
        return violations

    @staticmethod
    def _compile_term_pattern(term: str) -> re.Pattern[str]:
        """Compile a word-bounded regex pattern for an entity ID or search token."""
        if re.match(r"^[A-Za-z0-9_-]+$", term):
            return re.compile(rf"(?<![A-Za-z0-9_-]){re.escape(term)}(?![A-Za-z0-9_-])")
        return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(term)}(?![A-Za-z0-9_])")

    def _scan_and_classify_references(
        self,
        targets: set[str],
        search_tokens: set[str],
        delta_file: Path,
        tombstone: dict[str, Any],
    ) -> tuple[list[AuditFinding], list[AuditFinding]]:
        """Scan text files in repository and classify all occurrences with word-bounded multi-term matching."""
        all_terms = sorted(targets | search_tokens)
        if not all_terms:
            return [], []

        compiled_terms = [(term, self._compile_term_pattern(term)) for term in all_terms]
        delta_resolved = delta_file.resolve()
        delta_rel = (
            delta_resolved.relative_to(self.repo_root)
            if delta_resolved.is_relative_to(self.repo_root)
            else Path(delta_file.name)
        )

        approved: list[AuditFinding] = []
        unacknowledged: list[AuditFinding] = []

        allowed_reasons = tombstone.get("allowed_reasons", []) if isinstance(tombstone, dict) else []

        candidate_files: list[tuple[Path, Path]] = []
        used_git_tracked = False
        if (self.repo_root / ".git").exists():
            from eidos.vcs.diff import get_tracked_files

            tracked_rels = get_tracked_files(self.repo_root)
            if tracked_rels:
                used_git_tracked = True
                for rel_str in tracked_rels:
                    rel_p = Path(rel_str)
                    if any(part in self.IGNORE_DIRS for part in rel_p.parts):
                        continue
                    if rel_p.name.startswith("."):
                        continue
                    fpath = self.repo_root / rel_p
                    suffixes_lower = {s.lower() for s in fpath.suffixes}
                    if fpath.suffix.lower() in self.IGNORE_EXTS or (suffixes_lower & self.IGNORE_EXTS):
                        continue
                    candidate_files.append((fpath, rel_p))

        if not used_git_tracked:
            for root, dirs, files in os.walk(self.repo_root):
                dirs[:] = [d for d in dirs if d not in self.IGNORE_DIRS]
                for fname in files:
                    if fname.startswith("."):
                        continue
                    fpath = Path(root) / fname
                    suffixes_lower = {s.lower() for s in fpath.suffixes}
                    if fpath.suffix.lower() in self.IGNORE_EXTS or (suffixes_lower & self.IGNORE_EXTS):
                        continue
                    try:
                        rel_p = fpath.relative_to(self.repo_root)
                    except ValueError:
                        continue
                    candidate_files.append((fpath, rel_p))

        for fpath, rel_p in candidate_files:
            try:
                with open(fpath, encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
            except OSError:
                continue

            for idx, line in enumerate(lines, 1):
                for term, term_re in compiled_terms:
                    if term_re.search(line):
                        finding = self._classify_occurrence(
                            rel_path=rel_p,
                            line_number=idx,
                            line_text=line.strip(),
                            matched_term=term,
                            delta_rel=delta_rel,
                            allowed_reasons=allowed_reasons,
                            prev_line=lines[idx - 2].strip() if idx > 1 else "",
                        )
                        if finding.is_approved:
                            approved.append(finding)
                        else:
                            unacknowledged.append(finding)

        return approved, unacknowledged

    def _classify_occurrence(
        self,
        rel_path: Path,
        line_number: int,
        line_text: str,
        matched_term: str,
        delta_rel: Path,
        allowed_reasons: list[dict[str, Any]],
        prev_line: str,
    ) -> AuditFinding:
        """Classify a single occurrence into approved (exempt) or unacknowledged (leak)."""
        rel_str = str(rel_path).replace("\\", "/")

        # 1. Current SpecDelta being audited
        if rel_path == delta_rel or rel_path.name == delta_rel.name:
            return AuditFinding(
                file_path=rel_str,
                line_number=line_number,
                matched_term=matched_term,
                line_snippet=line_text[:80],
                classification="self_delta",
                is_approved=True,
                reason="Current SpecDelta audit document",
                risk_level="low",
            )

        # 2. Historical Applied/Rejected Deltas, Explorations & Release Changelogs
        if (
            (rel_str.startswith("eidos/deltas/") and self._is_delta_applied(rel_path))
            or rel_str.startswith("eidos/explorations/")
            or rel_str in ("CHANGELOG.md", "docs/changelog.md")
            or rel_str.endswith("CHANGELOG.md")
            or rel_str.endswith("changelog.md")
        ):
            return AuditFinding(
                file_path=rel_str,
                line_number=line_number,
                matched_term=matched_term,
                line_snippet=line_text[:80],
                classification="historical_ledger",
                is_approved=True,
                reason="Historical applied/rejected SpecDelta, exploration spike, or changelog release ledger (immutable record)",
                risk_level="low",
            )

        # 3. Explicit Tombstone declarations in SpecDelta
        for entry in allowed_reasons:
            if isinstance(entry, dict):
                allowed_p = entry.get("path", "")
                pat = entry.get("pattern")
                if allowed_p and allowed_p in rel_str:
                    pat_str = str(pat) if pat else ""
                    if not pat_str:
                        pat_matches = True
                    else:
                        ids_in_pat = {
                            m.upper()
                            for m in re.findall(
                                r"\b(?:CNT|CMP|CTR|CAP|POL|DOM|PER|DS|SCR|UIC|CLT|FLOW|INV)-[A-Z0-9_-]+\b",
                                pat_str,
                                re.IGNORECASE,
                            )
                        }
                        term_is_id = bool(
                            re.match(
                                r"^(?:CNT|CMP|CTR|CAP|POL|DOM|PER|DS|SCR|UIC|CLT|FLOW|INV)-[A-Z0-9_-]+$",
                                matched_term,
                                re.IGNORECASE,
                            )
                        )
                        if ids_in_pat and term_is_id and matched_term.upper() not in ids_in_pat:
                            pat_matches = False
                        else:
                            try:
                                pat_matches = bool(
                                    re.search(pat_str, matched_term, re.IGNORECASE)
                                    or re.search(pat_str, line_text, re.IGNORECASE)
                                )
                            except re.error:
                                pat_matches = (
                                    pat_str.lower() in matched_term.lower() or pat_str.lower() in line_text.lower()
                                )
                    if pat_matches:
                        return AuditFinding(
                            file_path=rel_str,
                            line_number=line_number,
                            matched_term=matched_term,
                            line_snippet=line_text[:80],
                            classification="tombstone_exempt",
                            is_approved=True,
                            reason=entry.get("reason", "Declared tombstone policy exception"),
                            risk_level="low",
                        )

        # 4. Inline Source Code Exemption Annotations
        if any(ann in line_text or ann in prev_line for ann in ("# eidos:allow-retired", "# eidos:tombstone")):
            return AuditFinding(
                file_path=rel_str,
                line_number=line_number,
                matched_term=matched_term,
                line_snippet=line_text[:80],
                classification="tombstone_exempt",
                is_approved=True,
                reason="Inline source code exemption annotation",
                risk_level="low",
            )

        # 5. Tests in polyglot test suites
        if rel_str.startswith("tests/") or is_test_code_path(rel_str):
            # Check if this is audit/test tooling or inspecting the audit results
            if (
                any(term in line_text for term in ("targets_audited", "audit_delta", "AuditEngine", "DeltaAuditResult"))
                or rel_path.name == "test_audit.py"
            ):
                return AuditFinding(
                    file_path=rel_str,
                    line_number=line_number,
                    matched_term=matched_term,
                    line_snippet=line_text[:80],
                    classification="negative_test",
                    is_approved=True,
                    reason="Test verifying audit engine behavior",
                    risk_level="low",
                )

            is_negative_assertion = False
            negative_patterns = (
                r"\bassert\s+not\b",
                r"\bassert\s+.*==\s*0\b",
                r"\bassert\s+.*is\s+False\b",
                r"\bassert\s+.*not\s+in\b",
                r"\bpytest\.raises\b",
                r"\btoThrow\b",
                r"\.rejects\b",
                r"\bshould_panic\b",
                r"\bassertFalse\b",
                r"\bassertNil\b",
                r"\bassert\.False\b",
            )
            for pat in negative_patterns:
                if re.search(pat, line_text):
                    is_negative_assertion = True
                    break

            if is_negative_assertion:
                return AuditFinding(
                    file_path=rel_str,
                    line_number=line_number,
                    matched_term=matched_term,
                    line_snippet=line_text[:80],
                    classification="negative_test",
                    is_approved=True,
                    reason="Negative regression test verifying non-existence",
                    risk_level="low",
                )

            if line_text.startswith(("#", '"""', "'''", "*", "//", "/*")):
                return AuditFinding(
                    file_path=rel_str,
                    line_number=line_number,
                    matched_term=matched_term,
                    line_snippet=line_text[:80],
                    classification="documentation_rationale",
                    is_approved=True,
                    reason="Test docstring or comment mentioning retired entity",
                    risk_level="low",
                )

        # 6. Uninstaller / Cleanup Routines
        cleanup_indicators = (
            "def _remove_",
            "def remove_",
            "def cleanup_",
            "unlink()",
            "rmdir()",
            "removed_",
            "_remove_project_",
        )
        if any(ind in line_text for ind in cleanup_indicators):
            return AuditFinding(
                file_path=rel_str,
                line_number=line_number,
                matched_term=matched_term,
                line_snippet=line_text[:80],
                classification="cleanup_routine",
                is_approved=True,
                reason="Uninstallation or backward-compatible cleanup routine",
                risk_level="low",
            )

        # 7. Documentation Rationale / Educational Context
        if rel_str.startswith("docs/"):
            rationale_indicators = (
                "decommissioned",
                "deprecated",
                "retired",
                "sunset",
                "historically",
                "replaced by",
                "previously",
                "removed in",
                "optional",
            )
            line_lower = line_text.lower()
            if any(ind in line_lower for ind in rationale_indicators):
                return AuditFinding(
                    file_path=rel_str,
                    line_number=line_number,
                    matched_term=matched_term,
                    line_snippet=line_text[:80],
                    classification="documentation_rationale",
                    is_approved=True,
                    reason="Documentation explaining retirement or backward compatibility rationale",
                    risk_level="low",
                )

        # --- UNACKNOWLEDGED LEAKS ---

        # 8. Active Living Specification Leak (Highest Risk)
        if rel_str.startswith("eidos/"):
            return AuditFinding(
                file_path=rel_str,
                line_number=line_number,
                matched_term=matched_term,
                line_snippet=line_text[:80],
                classification="active_spec_leak",
                is_approved=False,
                reason=f"Living specification '{rel_str}' still references removed entity '{matched_term}'",
                risk_level="critical",
            )

        # 9. Active Code Leak in Engine
        if rel_str.startswith("src/"):
            return AuditFinding(
                file_path=rel_str,
                line_number=line_number,
                matched_term=matched_term,
                line_snippet=line_text[:80],
                classification="active_code_leak",
                is_approved=False,
                reason=f"Source code '{rel_str}' still references removed entity '{matched_term}'",
                risk_level="high",
            )

        # 10. Active Documentation Leak
        if rel_str.startswith("docs/") or rel_path.name.lower().startswith("readme"):
            return AuditFinding(
                file_path=rel_str,
                line_number=line_number,
                matched_term=matched_term,
                line_snippet=line_text[:80],
                classification="active_docs_leak",
                is_approved=False,
                reason=f"User documentation '{rel_str}' still references removed entity '{matched_term}'",
                risk_level="medium",
            )

        # 11. Unclassified Active Leak
        return AuditFinding(
            file_path=rel_str,
            line_number=line_number,
            matched_term=matched_term,
            line_snippet=line_text[:80],
            classification="unclassified_leak",
            is_approved=False,
            reason=f"Unacknowledged reference to '{matched_term}' in '{rel_str}'",
            risk_level="medium",
        )

    def _is_delta_applied(self, delta_path: Path) -> bool:
        """Check if a SpecDelta file has status: applied or rejected in frontmatter."""
        try:
            full_p = self.repo_root / delta_path
            doc = parse_spec_file(full_p)
            return str(doc.frontmatter.get("status", "")).strip().lower() in ("applied", "rejected")
        except Exception:
            return False
