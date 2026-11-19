"""Lossless two-tier SpecDelta and Exploration archive partitioning (CMP-DELTA-ENGINE)."""

import re
from pathlib import Path
from typing import Any

from eidos.core.index.storage import get_cached_spec_document
from eidos.core.lifecycle.catalog import _render_spec, _single_line
from eidos.core.spec.parser import _parse_spec_content
from eidos.core.spec.validator import SpecValidator


def _extract_exploration_section(body: str, heading_pattern: str, inline_label_pattern: str) -> str:
    """Extract full multi-line AsciiDoc section content (preserving paragraphs, bullets, and '===' subsections)."""
    if not body:
        return ""
    sec_m = re.search(
        rf"^==\s+[^\r\n]*(?:{heading_pattern})[^\r\n]*\r?\n(.*?)(?=^==\s+|\Z)",
        body,
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    if sec_m:
        extracted = sec_m.group(1).strip()
        if extracted:
            return extracted
    inline_m = re.search(
        rf"(?:\*{inline_label_pattern}:\*|\*\s*\*{inline_label_pattern}\*:)[ \t]*(.+?)(?=\r?\n\s*\r?\n|\r?\n\*\s*\*|\r?\n==\s+|\Z)",
        body,
        re.DOTALL | re.IGNORECASE,
    )
    if inline_m:
        return inline_m.group(1).strip()
    return ""


def _sequence_sort_key(doc_id: str, prefix: str) -> tuple[int, int, str]:
    """Return a deterministic (year, seq, doc_id) sort key for DLT-YYYY-NNN or EXP-YYYY-NNN IDs."""
    cleaned = str(doc_id or "").strip().upper()
    m = re.match(rf"^{re.escape(prefix.upper())}-(\d{{4}})-(\d+)(?:-[A-Z0-9_-]+)?$", cleaned)
    if m:
        return int(m.group(1)), int(m.group(2)), cleaned
    return 0, 0, cleaned


def archive_explorations(
    eidos_dir: Path,
    keep_recent: int = 15,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Move and slim concluded Explorations (adopted/discarded) beyond the last keep_recent items into eidos/explorations/archive/."""
    if keep_recent < 0:
        raise ValueError("keep_recent must be >= 0")

    exp_dir = eidos_dir / "explorations"
    archive_dir = exp_dir / "archive"
    if not exp_dir.exists():
        return {
            "archived_count": 0,
            "retained_terminal_count": 0,
            "active_retained_count": 0,
            "bytes_before": 0,
            "bytes_after": 0,
            "bytes_saved": 0,
            "archived_ids": [],
            "retained_ids": [],
        }

    terminal_items: list[tuple[tuple[int, int, str], Path, Any]] = []
    active_retained_count = 0

    for p in sorted(exp_dir.glob("*.adoc")):
        if not p.is_file() or p.is_symlink():
            continue
        doc, _, _ = get_cached_spec_document(p)
        if doc is None or doc.doc_type != "product/exploration":
            continue
        status = str((doc.frontmatter or {}).get("status", "proposed")).strip().lower()
        doc_id = str(doc.doc_id or p.stem.upper())
        if status in ("adopted", "discarded"):
            terminal_items.append((_sequence_sort_key(doc_id, "EXP"), p, doc))
        else:
            active_retained_count += 1

    terminal_items.sort(key=lambda item: item[0])
    if keep_recent == 0:
        to_archive = terminal_items
        retained = []
    elif len(terminal_items) > keep_recent:
        to_archive = terminal_items[:-keep_recent]
        retained = terminal_items[-keep_recent:]
    else:
        to_archive = []
        retained = terminal_items

    bytes_before = 0
    bytes_after = 0
    archived_ids: list[str] = []
    validator = SpecValidator()

    for _, p, doc in to_archive:
        fm = dict(doc.frontmatter or {})
        doc_id = str(doc.doc_id or p.stem.upper())
        title = str(fm.get("title") or doc.title or doc_id)
        orig_size = p.stat().st_size

        raw_body = doc.body or ""
        hypothesis = _extract_exploration_section(raw_body, r"Hypothesis", r"Hypothesis")
        findings = _extract_exploration_section(
            raw_body,
            r"Findings|Outcome|Learnings|Results|Conclusion",
            r"(?:Findings|Outcome|Learnings)",
        )

        slim_fm: dict[str, Any] = {
            "type": "product/exploration",
            "id": doc_id,
            "title": title,
            "status": str(fm.get("status", "adopted")).strip().lower(),
        }
        if isinstance(fm.get("decision"), dict) and fm["decision"]:
            dec_clean = {k: v for k, v in fm["decision"].items() if k in ("outcome", "resulting_deltas")}
            if dec_clean:
                slim_fm["decision"] = dec_clean

        body_lines = [f"= Exploration: {_single_line(title)}", ""]
        if hypothesis:
            body_lines.extend(["== Core Hypothesis", hypothesis, ""])
        if findings:
            body_lines.extend(["== Findings & Learnings", findings, ""])
        slim_body = "\n".join(body_lines).rstrip() + "\n"
        slim_content = _render_spec(slim_fm, slim_body)
        slim_size = len(slim_content.encode("utf-8"))

        dest_p = archive_dir / p.name
        if not dry_run:
            archive_dir.mkdir(parents=True, exist_ok=True)
            slim_doc = _parse_spec_content(dest_p, slim_content)
            errors = validator.validate_document(slim_doc)
            if errors:
                raise ValueError(f"Archived exploration '{doc_id}' failed schema validation: {'; '.join(errors)}")
            dest_p.write_text(slim_content, encoding="utf-8")
            p.unlink()

        bytes_before += orig_size
        bytes_after += slim_size
        archived_ids.append(doc_id)

    return {
        "archived_count": len(archived_ids),
        "retained_terminal_count": len(retained),
        "active_retained_count": active_retained_count,
        "bytes_before": bytes_before,
        "bytes_after": bytes_after,
        "bytes_saved": max(0, bytes_before - bytes_after),
        "archived_ids": archived_ids,
        "retained_ids": [str(it[2].doc_id or it[1].stem.upper()) for it in retained],
    }


def archive_deltas(
    eidos_dir: Path,
    keep_recent: int = 15,
    include_explorations: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Move and slim reconciled SpecDeltas (applied/rejected) beyond the last keep_recent items into eidos/deltas/archive/."""
    if keep_recent < 0:
        raise ValueError("keep_recent must be >= 0")

    deltas_dir = eidos_dir / "deltas"
    archive_dir = deltas_dir / "archive"
    terminal_items: list[tuple[tuple[int, int, str], Path, Any]] = []
    active_retained_count = 0

    if deltas_dir.exists():
        for p in sorted(deltas_dir.glob("*.adoc")):
            if not p.is_file() or p.is_symlink():
                continue
            doc, _, _ = get_cached_spec_document(p)
            if doc is None or doc.doc_type != "delta/mutation":
                continue
            status = str((doc.frontmatter or {}).get("status", "proposed")).strip().lower()
            doc_id = str(doc.doc_id or p.stem.upper())
            if status in ("applied", "rejected"):
                terminal_items.append((_sequence_sort_key(doc_id, "DLT"), p, doc))
            else:
                active_retained_count += 1

    terminal_items.sort(key=lambda item: item[0])
    if keep_recent == 0:
        to_archive = terminal_items
        retained = []
    elif len(terminal_items) > keep_recent:
        to_archive = terminal_items[:-keep_recent]
        retained = terminal_items[-keep_recent:]
    else:
        to_archive = []
        retained = terminal_items

    bytes_before = 0
    bytes_after = 0
    archived_ids: list[str] = []

    if to_archive:
        validator = SpecValidator()

        for _, p, doc in to_archive:
            fm = dict(doc.frontmatter or {})
            doc_id = str(doc.doc_id or p.stem.upper())
            title = str(fm.get("title") or doc.title or doc_id)
            orig_size = p.stat().st_size

            slim_muts: list[dict[str, Any]] = []
            for m in fm.get("mutations") or []:
                if not isinstance(m, dict):
                    continue
                m_copy = {k: v for k, v in m.items() if k not in ("expect", "details")}
                slim_muts.append(m_copy)

            slim_fm: dict[str, Any] = {
                "type": "delta/mutation",
                "id": doc_id,
                "title": title,
                "status": str(fm.get("status", "applied")).strip().lower(),
                "intent": fm.get("intent") or {"kind": "feature", "motivation": title},
            }
            if fm.get("graduated_from"):
                slim_fm["graduated_from"] = fm["graduated_from"]
            if fm.get("tombstone"):
                slim_fm["tombstone"] = fm["tombstone"]
            if fm.get("decisions"):
                slim_fm["decisions"] = fm["decisions"]
            if isinstance(fm.get("vcs"), dict) and fm["vcs"]:
                slim_fm["vcs"] = dict(fm["vcs"])

            slim_fm["mutations"] = slim_muts
            slim_body = f"= SpecDelta: {_single_line(title)}\n"
            slim_content = _render_spec(slim_fm, slim_body)
            slim_size = len(slim_content.encode("utf-8"))

            dest_p = archive_dir / p.name
            if not dry_run:
                archive_dir.mkdir(parents=True, exist_ok=True)
                slim_doc = _parse_spec_content(dest_p, slim_content)
                errors = validator.validate_document(slim_doc)
                if errors:
                    raise ValueError(f"Archived SpecDelta '{doc_id}' failed schema validation: {'; '.join(errors)}")
                dest_p.write_text(slim_content, encoding="utf-8")
                p.unlink()

            bytes_before += orig_size
            bytes_after += slim_size
            archived_ids.append(doc_id)

    exp_report = (
        archive_explorations(eidos_dir, keep_recent=keep_recent, dry_run=dry_run)
        if include_explorations
        else {
            "archived_count": 0,
            "retained_terminal_count": 0,
            "active_retained_count": 0,
            "bytes_before": 0,
            "bytes_after": 0,
            "bytes_saved": 0,
            "archived_ids": [],
            "retained_ids": [],
        }
    )

    total_before = bytes_before + exp_report["bytes_before"]
    total_after = bytes_after + exp_report["bytes_after"]
    return {
        "keep_recent": keep_recent,
        "dry_run": dry_run,
        "deltas": {
            "archived_count": len(archived_ids),
            "retained_terminal_count": len(retained),
            "active_retained_count": active_retained_count,
            "bytes_before": bytes_before,
            "bytes_after": bytes_after,
            "bytes_saved": max(0, bytes_before - bytes_after),
            "archived_ids": archived_ids,
            "retained_ids": [str(it[2].doc_id or it[1].stem.upper()) for it in retained],
        },
        "explorations": exp_report,
        "total_bytes_saved": max(0, total_before - total_after),
    }
