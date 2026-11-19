"""W3C DTCG design system tokens.manifest and acyclic extends-chain validation (CMP-SPEC-VALIDATOR)."""

from pathlib import Path
from typing import Any

import orjson

from eidos.core.spec.models import SpecDocument, ValidationReport
from eidos.core.workspace.project import resolve_spec_and_repo_roots

VALID_DTCG_TYPES = frozenset(
    {
        "color",
        "dimension",
        "fontFamily",
        "fontWeight",
        "duration",
        "cubicBezier",
        "number",
        "strokeStyle",
        "border",
        "transition",
        "shadow",
        "gradient",
        "typography",
    }
)


def validate_design_systems(report: ValidationReport, root_dir: Path) -> None:
    """Validate design/system W3C DTCG tokens.manifest files and acyclic extends chains."""
    spec_root, repo_root = resolve_spec_and_repo_roots(root_dir)
    inactive_statuses = {
        "planned",
        "proposed",
        "draft",
        "deprecated",
        "retired",
        "discarded",
        "rejected",
    }

    ds_docs_by_path: dict[Path, str] = {
        d.file_path.resolve(): d.doc_id
        for d in report.documents
        if d.doc_type == "design/system" and isinstance(d.doc_id, str) and d.doc_id
    }

    extends_map: dict[str, tuple[str, SpecDocument]] = {}
    for doc in report.documents:
        if doc.doc_type != "design/system":
            continue
        fm = doc.frontmatter or {}
        parent_ref = str(fm.get("extends") or "").strip()

        if parent_ref and isinstance(doc.doc_id, str) and doc.doc_id:
            parent_id = parent_ref
            if parent_ref.endswith(".adoc"):
                for base in (doc.file_path.parent, spec_root, root_dir, repo_root):
                    cand = (base / parent_ref).resolve()
                    if cand in ds_docs_by_path:
                        parent_id = ds_docs_by_path[cand]
                        break
            extends_map[doc.doc_id] = (parent_id, doc)

        status = str(fm.get("status", "active")).strip().lower()
        if status in inactive_statuses:
            continue

        tokens_cfg = fm.get("tokens")
        if not isinstance(tokens_cfg, dict):
            continue
        manifest_rel = str(tokens_cfg.get("manifest") or "").strip()
        spec_fmt = str(tokens_cfg.get("spec_format") or "w3c_design_tokens").strip().lower()
        if not manifest_rel or manifest_rel.upper() in ("N/A", "TBD", "NONE"):
            continue

        resolved_repo = repo_root.resolve()
        raw_norm = manifest_rel.replace("\\", "/")
        if Path(raw_norm).is_absolute() or ".." in Path(raw_norm).parts:
            report.broken_references.append(
                f"Broken tokens.manifest in {doc.file_path.name}: "
                f"Design System '{doc.doc_id}' references '{manifest_rel}' outside repository root"
            )
            continue

        manifest_path: Path | None = None
        escaped_repo = False
        for base in (doc.file_path.parent, spec_root, root_dir, repo_root):
            cand = base / manifest_rel
            if cand.exists() or cand.is_symlink():
                try:
                    resolved_cand = cand.resolve()
                except OSError:
                    continue
                if not resolved_cand.is_relative_to(resolved_repo):
                    escaped_repo = True
                    break
                if resolved_cand.is_file():
                    manifest_path = resolved_cand
                    break

        if escaped_repo:
            report.broken_references.append(
                f"Broken tokens.manifest in {doc.file_path.name}: "
                f"Design System '{doc.doc_id}' references '{manifest_rel}' which resolves outside repository root"
            )
            continue

        if manifest_path is None:
            report.broken_references.append(
                f"Broken tokens.manifest in {doc.file_path.name}: "
                f"Design System '{doc.doc_id}' references '{manifest_rel}' which does not exist on disk"
            )
            continue

        if spec_fmt == "w3c_design_tokens":
            try:
                token_data = orjson.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                report.broken_references.append(
                    f"Invalid W3C DTCG tokens.manifest in {doc.file_path.name}: "
                    f"'{manifest_rel}' is not valid JSON ({exc})"
                )
                continue

            if not isinstance(token_data, dict):
                report.broken_references.append(
                    f"Invalid W3C DTCG tokens.manifest in {doc.file_path.name}: "
                    f"'{manifest_rel}' root must be a JSON object"
                )
                continue

            token_count = 0

            def _walk_tokens(
                node: dict[str, Any],
                path_prefix: str,
                doc_name: str = doc.file_path.name,
                m_rel: str = manifest_rel,
            ) -> None:
                nonlocal token_count
                if "$value" in node:
                    token_count += 1
                    tok_type = node.get("$type")
                    if tok_type is not None and (not isinstance(tok_type, str) or tok_type not in VALID_DTCG_TYPES):
                        report.broken_references.append(
                            f"Invalid W3C DTCG token $type in {doc_name} ({m_rel}): "
                            f"token '{path_prefix}' has unsupported $type '{tok_type}'"
                        )
                    return

                child_dicts = {k: v for k, v in node.items() if not str(k).startswith("$") and isinstance(v, dict)}
                non_meta_scalars = [
                    k for k, v in node.items() if not str(k).startswith("$") and not isinstance(v, dict)
                ]
                if path_prefix and (non_meta_scalars or not child_dicts):
                    report.broken_references.append(
                        f"Invalid W3C DTCG token in {doc_name} ({m_rel}): "
                        f"token '{path_prefix}' is missing required '$value' property"
                    )
                    if not child_dicts:
                        return
                elif not path_prefix and non_meta_scalars:
                    for bad_key in non_meta_scalars:
                        report.broken_references.append(
                            f"Invalid W3C DTCG token in {doc_name} ({m_rel}): "
                            f"token '{bad_key}' is missing required '$value' property"
                        )

                for k, v in child_dicts.items():
                    sub = f"{path_prefix}.{k}" if path_prefix else str(k)
                    _walk_tokens(v, sub, doc_name, m_rel)

            _walk_tokens(token_data, "")
            if token_count == 0:
                report.broken_references.append(
                    f"Invalid W3C DTCG tokens.manifest in {doc.file_path.name} ({manifest_rel}): "
                    f"no tokens with '$value' found"
                )

    # Detect circular inheritance.extends chains across DS-* specifications
    reported_cycles: set[frozenset[str]] = set()
    for start_id, (first_parent, start_doc) in extends_map.items():
        chain = [start_id]
        curr: str | None = first_parent
        while curr:
            if curr in chain:
                cycle_start = chain.index(curr)
                cycle_key = frozenset(chain[cycle_start:])
                if cycle_key not in reported_cycles:
                    reported_cycles.add(cycle_key)
                    cycle_str = " -> ".join([*chain, curr])
                    report.broken_references.append(
                        f"Circular design system inheritance in {start_doc.file_path.name}: {cycle_str}"
                    )
                break
            chain.append(curr)
            nxt = extends_map.get(curr)
            curr = nxt[0] if nxt else None
