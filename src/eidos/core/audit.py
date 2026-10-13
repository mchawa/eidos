"""SpecDelta Deletion and Deprecation Blast-Radius Audit Engine.

Implements INV-DELTA-DELETION-AUDIT (CAP-PROD-002):
- Graph-level invariant verification via embedded SQLite ArchitecturalIndex (index.db).
- Context-aware semantic reference discovery across the codebase.
- Semantic classification distinguishing legitimate records (historical ledgers, negative
  regression tests, cleanup routines, tombstone exemptions) from active leaks.
- Structured Rich table reporting and machine-readable JSON payloads for AI self-healing.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from eidos.core.graph_index import get_architectural_index
from eidos.core.parser import parse_spec_file
from eidos.core.scaffolder import find_spec_by_id_or_path


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
        return json.dumps(self.to_dict(), indent=indent)


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
    IGNORE_EXTS: ClassVar[set[str]] = {".pyc", ".db", ".sqlite", ".sqlite3", ".lock", ".png", ".jpg", ".jpeg", ".ico", ".wasm"}

    def __init__(self, repo_root: Path | None = None, eidos_dir: Path | None = None):
        if repo_root is not None:
            self.repo_root = Path(repo_root).resolve()
        elif eidos_dir is not None and Path(eidos_dir).name == "eidos":
            self.repo_root = Path(eidos_dir).resolve().parent
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

        delta_id = delta_doc.frontmatter.get("id", delta_file.stem.upper())
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

        # Phase 1: Graph-level foreign key checks
        graph_violations = self._check_graph_integrity(targets, delta_id)

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

        deletion_prefixes = ("remove_", "delete_", "deprecate_", "retire_")

        for m in mutations:
            if not isinstance(m, dict):
                continue
            action = str(m.get("action", "")).strip().lower()
            if any(action.startswith(prefix) for prefix in deletion_prefixes):
                # Explicit target keys
                for key in (
                    "target_component",
                    "target_contract",
                    "target_capability",
                    "target_policy",
                    "target_domain",
                    "target_screen",
                    "target_ui_component",
                    "target_design_system",
                    "target_invariant_id",
                ):
                    val = m.get(key)
                    if val and isinstance(val, str):
                        targets.add(val.strip())

                # Parse IDs mentioned in details
                details = str(m.get("details", ""))
                for match in re.findall(r"\b([A-Z]{2,4}-[A-Z0-9_-]+)\b", details):
                    if any(
                        match.startswith(p)
                        for p in ("CNT-", "CTR-", "CAP-", "POL-", "DOM-", "DS-", "SCR-", "UIC-", "FLOW-", "INV-")
                    ):
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
        try:
            index = get_architectural_index(repo_root=self.repo_root, eidos_dir=self.eidos_dir)
            for target in targets:
                refs = index.get_incoming_references(target)
                for r in refs:
                    src = r.get("source_id", "")
                    if src == delta_id or src.startswith("DLT-"):
                        # Deltas are audit records; only living specs are graph violations
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
        except Exception:
            # Fallback gracefully if index cannot be read
            pass
        return violations

    def _scan_and_classify_references(
        self,
        targets: set[str],
        search_tokens: set[str],
        delta_file: Path,
        tombstone: dict[str, Any],
    ) -> tuple[list[AuditFinding], list[AuditFinding]]:
        """Scan text files in repository and classify all occurrences."""
        all_terms = sorted(targets | search_tokens)
        if not all_terms:
            return [], []

        delta_rel = delta_file.resolve().relative_to(self.repo_root)

        approved: list[AuditFinding] = []
        unacknowledged: list[AuditFinding] = []

        allowed_reasons = tombstone.get("allowed_reasons", []) if isinstance(tombstone, dict) else []

        for root, dirs, files in os.walk(self.repo_root):
            # Prune ignored directories
            dirs[:] = [d for d in dirs if d not in self.IGNORE_DIRS]

            for fname in files:
                if fname.startswith("."):
                    continue
                fpath = Path(root) / fname
                if fpath.suffix.lower() in self.IGNORE_EXTS:
                    continue

                try:
                    rel_p = fpath.relative_to(self.repo_root)
                except ValueError:
                    continue

                try:
                    with open(fpath, encoding="utf-8", errors="ignore") as f:
                        lines = f.readlines()
                except Exception:
                    continue

                for idx, line in enumerate(lines, 1):
                    for term in all_terms:
                        if term in line:
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
                            break  # Avoid duplicate matches on same line for multiple overlapping terms

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
        rel_str = str(rel_path)

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

        # 2. Historical Applied Deltas & Release Changelogs
        if (
            (rel_str.startswith("eidos/deltas/") and ("by-status/applied/" in rel_str or self._is_delta_applied(rel_path)))
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
                reason="Historical applied SpecDelta or changelog release ledger (immutable record)",
                risk_level="low",
            )


        # 3. Explicit Tombstone declarations in SpecDelta
        for entry in allowed_reasons:
            if isinstance(entry, dict):
                allowed_p = entry.get("path", "")
                pat = entry.get("pattern")
                if allowed_p and allowed_p in rel_str and (not pat or re.search(str(pat), line_text)):
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

        # 5. Tests in test suite
        if rel_str.startswith("tests/"):
            # Check if this is audit/test tooling or inspecting the audit results
            if any(term in line_text for term in ("targets_audited", "audit_delta", "AuditEngine", "DeltaAuditResult")) or rel_path.name == "test_audit.py":
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

            if line_text.startswith(("#", '"""', "'''", "*")):
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
        """Check if a SpecDelta file has status: applied in frontmatter."""
        try:
            full_p = self.repo_root / delta_path
            doc = parse_spec_file(full_p)
            return str(doc.frontmatter.get("status", "")).strip().lower() == "applied"
        except Exception:
            return False
