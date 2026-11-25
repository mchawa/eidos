#!/usr/bin/env python3
"""Zero-dependency diff-line statement coverage verifier for local development and CI.

Intersects newly added or modified Python lines under ``src/eidos/`` (from ``git diff -U0``
and untracked working-tree files) against ``coverage.json`` produced by ``pytest --cov``.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

_HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_DEFAULT_EXCLUDES = ("src/eidos/_version.py", "src/eidos/portal/frontend/")


@dataclass
class FileDiffCoverage:
    """Diff-line coverage statistics for a single modified Python source module."""

    path: str
    executable_added: int
    covered_added: int
    uncovered_lines: list[int]

    @property
    def percent_covered(self) -> float:
        if self.executable_added == 0:
            return 100.0
        return (self.covered_added / self.executable_added) * 100.0


def format_line_ranges(lines: list[int]) -> str:
    """Compact sorted 1-based line numbers into human-readable ranges (e.g. '10-12, 15')."""
    if not lines:
        return ""
    sorted_lines = sorted(set(lines))
    ranges: list[str] = []
    start = prev = sorted_lines[0]
    for ln in sorted_lines[1:]:
        if ln == prev + 1:
            prev = ln
        else:
            ranges.append(f"{start}-{prev}" if start != prev else str(start))
            start = prev = ln
    ranges.append(f"{start}-{prev}" if start != prev else str(start))
    return ", ".join(ranges)


def parse_unified_diff_added_lines(diff_text: str) -> dict[str, set[int]]:
    """Parse ``git diff -U0`` unified output into ``{repo_rel_path: set_of_added_1based_lines}``."""
    added_by_file: dict[str, set[int]] = {}
    current_file: str | None = None
    in_header = False
    for raw_line in diff_text.splitlines():
        if raw_line.startswith("diff --git "):
            current_file = None
            in_header = True
            continue
        if in_header and raw_line.startswith("--- "):
            continue
        if in_header and raw_line.startswith("+++ "):
            target = raw_line[4:].strip()
            if target == "/dev/null":
                current_file = None
            else:
                if target.startswith("b/"):
                    target = target[2:]
                current_file = target.replace("\\", "/")
            continue
        if raw_line.startswith("@@ "):
            in_header = False
            if current_file is None:
                continue
            m = _HUNK_HEADER_RE.match(raw_line)
            if not m:
                continue
            start = int(m.group(1))
            count = int(m.group(2)) if m.group(2) is not None else 1
            if count <= 0:
                continue
            added_by_file.setdefault(current_file, set()).update(range(start, start + count))
    return added_by_file


def _normalize_prefix(prefix: str) -> str:
    clean = prefix.replace("\\", "/").strip()
    if not clean or clean in (".", "./"):
        return ""
    return clean.rstrip("/") + "/"


def _is_excluded(rel_path: str, prefix: str, excludes: tuple[str, ...]) -> bool:
    norm = rel_path.replace("\\", "/")
    norm_prefix = _normalize_prefix(prefix)
    if norm_prefix and not norm.startswith(norm_prefix):
        return True
    if not norm.endswith(".py"):
        return True
    return any(norm == exc or (exc.endswith("/") and norm.startswith(exc)) for exc in excludes)


def collect_diff_added_lines(
    repo_root: pathlib.Path,
    *,
    base_ref: str | None = None,
    head_ref: str = "HEAD",
    prefix: str = "src/eidos/",
    excludes: tuple[str, ...] = _DEFAULT_EXCLUDES,
) -> tuple[dict[str, set[int]], set[str]]:
    """Collect added/modified lines per Python file plus any untracked working-tree Python files."""
    pathspec = _normalize_prefix(prefix).rstrip("/") or "."
    if base_ref:
        cmd = ["git", "-c", "core.quotepath=false", "diff", "-U0", f"{base_ref}...{head_ref}", "--", pathspec]
    else:
        cmd = ["git", "-c", "core.quotepath=false", "diff", "-U0", "HEAD", "--", pathspec]

    proc = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True, encoding="utf-8", check=False)
    if proc.returncode != 0 and base_ref:
        # Fallback to 2-dot diff if base_ref has no merge-base relation with head_ref.
        cmd_2dot = ["git", "-c", "core.quotepath=false", "diff", "-U0", f"{base_ref}..{head_ref}", "--", pathspec]
        proc = subprocess.run(cmd_2dot, cwd=repo_root, capture_output=True, text=True, encoding="utf-8", check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"git diff failed: {proc.stderr.strip() or proc.stdout.strip()}")

    raw_added = parse_unified_diff_added_lines(proc.stdout)
    added_by_file = {
        rel: lines for rel, lines in raw_added.items() if lines and not _is_excluded(rel, prefix, excludes)
    }

    untracked_files: set[str] = set()
    if not base_ref:
        untracked_proc = subprocess.run(
            ["git", "-c", "core.quotepath=false", "ls-files", "--others", "--exclude-standard", "--", pathspec],
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        if untracked_proc.returncode == 0:
            for line in untracked_proc.stdout.splitlines():
                rel = line.strip().replace("\\", "/")
                if rel and not _is_excluded(rel, prefix, excludes) and (repo_root / rel).is_file():
                    untracked_files.add(rel)

    return added_by_file, untracked_files


def _non_trivial_code_lines(file_path: pathlib.Path, candidate_lines: set[int] | None = None) -> list[int]:
    """Return 1-based non-blank, non-comment line numbers for files missing from coverage.json."""
    lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    selected: list[int] = []
    for idx, text in enumerate(lines, start=1):
        if candidate_lines is not None and idx not in candidate_lines:
            continue
        stripped = text.strip()
        if stripped and not stripped.startswith("#"):
            selected.append(idx)
    return selected


def evaluate_diff_coverage(
    cov_data: dict[str, Any],
    added_by_file: dict[str, set[int]],
    *,
    untracked_files: set[str] = frozenset(),
    repo_root: pathlib.Path | None = None,
) -> tuple[list[FileDiffCoverage], int, int, float]:
    """Intersect added lines with ``coverage.json`` data and return per-file and aggregate metrics."""
    files_cov = cov_data.get("files", {})
    all_targets = sorted(set(added_by_file.keys()) | set(untracked_files))
    results: list[FileDiffCoverage] = []
    total_exec = 0
    total_cov = 0

    for rel in all_targets:
        entry = files_cov.get(rel) or files_cov.get(rel.replace("/", "\\"))
        if entry is None:
            # Module was never imported/executed at all during pytest -> flag non-blank/comment lines as uncovered.
            disk_file = (repo_root / rel) if repo_root is not None else None
            if disk_file is not None and disk_file.is_file():
                cand = None if rel in untracked_files else added_by_file.get(rel, set())
                uncovered = _non_trivial_code_lines(disk_file, cand)
            else:
                uncovered = sorted(added_by_file.get(rel, set()))
            if not uncovered:
                continue
            results.append(
                FileDiffCoverage(path=rel, executable_added=len(uncovered), covered_added=0, uncovered_lines=uncovered)
            )
            total_exec += len(uncovered)
            continue

        executed = set(entry.get("executed_lines", []))
        missing = set(entry.get("missing_lines", []))
        executable_all = executed | missing

        target_lines = executable_all if rel in untracked_files else added_by_file.get(rel, set()) & executable_all
        if not target_lines:
            continue

        covered = target_lines & executed
        uncovered = sorted(target_lines & missing)
        results.append(
            FileDiffCoverage(
                path=rel,
                executable_added=len(target_lines),
                covered_added=len(covered),
                uncovered_lines=uncovered,
            )
        )
        total_exec += len(target_lines)
        total_cov += len(covered)

    total_pct = (total_cov / total_exec * 100.0) if total_exec > 0 else 100.0
    return results, total_exec, total_cov, total_pct


def render_markdown_summary(
    results: list[FileDiffCoverage],
    total_exec: int,
    total_cov: int,
    total_pct: float,
    min_coverage: float,
) -> str:
    """Render a GitHub-Flavored Markdown summary table of diff-line statement coverage."""
    status = "PASS" if total_pct >= min_coverage else "FAIL"
    lines = [
        f"### Diff-Line Statement Coverage ({status}: `{total_pct:.2f}%`, `{total_cov}/{total_exec}` new executable lines; threshold `>= {min_coverage:.1f}%`)",
        "",
    ]
    if not results:
        lines.append("No executable Python lines added or modified in scope.")
        return "\n".join(lines) + "\n"

    lines.append("| Module | Covered / Added | Diff Coverage | Uncovered Lines |")
    lines.append("| :--- | ---: | ---: | :--- |")
    for item in results:
        unc_str = f"`{format_line_ranges(item.uncovered_lines)}`" if item.uncovered_lines else "—"
        lines.append(
            f"| `{item.path}` | `{item.covered_added}/{item.executable_added}` | {item.percent_covered:.2f}% | {unc_str} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify diff-line statement coverage against coverage.json.")
    parser.add_argument("--coverage", default="coverage.json", help="Path to coverage.py JSON report.")
    parser.add_argument(
        "--base", default=None, help="Base Git ref for branch diff (omit for uncommitted diff vs HEAD)."
    )
    parser.add_argument("--head", default="HEAD", help="Head Git ref when --base is set (default: HEAD).")
    parser.add_argument(
        "--min-coverage", type=float, default=90.0, help="Minimum diff-line coverage %% (default: 90.0)."
    )
    parser.add_argument("--prefix", default="src/eidos/", help="Source path prefix to check (default: src/eidos/).")
    parser.add_argument(
        "--summary-out",
        default=None,
        help="Optional file path to append Markdown summary (e.g. $GITHUB_STEP_SUMMARY).",
    )
    parser.add_argument("--json", action="store_true", dest="json_output", help="Emit machine-readable JSON output.")
    parser.add_argument("--repo-root", default=".", help="Repository root directory (default: .).")
    args = parser.parse_args(argv)

    repo_root = pathlib.Path(args.repo_root).resolve()
    cov_path = pathlib.Path(args.coverage)
    if not cov_path.is_absolute():
        cov_path = (repo_root / cov_path).resolve()
    if not cov_path.is_file():
        print(
            f"Error: coverage report not found at '{cov_path}'. Run pytest with --cov-report=json:coverage.json first.",
            file=sys.stderr,
        )
        return 2

    cov_data = json.loads(cov_path.read_text(encoding="utf-8"))
    try:
        added_by_file, untracked = collect_diff_added_lines(
            repo_root,
            base_ref=args.base,
            head_ref=args.head,
            prefix=args.prefix,
        )
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    results, total_exec, total_cov, total_pct = evaluate_diff_coverage(
        cov_data,
        added_by_file,
        untracked_files=untracked,
        repo_root=repo_root,
    )
    passed = total_pct >= args.min_coverage

    md = render_markdown_summary(results, total_exec, total_cov, total_pct, args.min_coverage)
    if args.summary_out:
        with pathlib.Path(args.summary_out).open("a", encoding="utf-8") as fh:
            fh.write("\n" + md)

    if args.json_output:
        payload = {
            "passed": passed,
            "min_coverage": args.min_coverage,
            "executable_added": total_exec,
            "covered_added": total_cov,
            "percent_covered": round(total_pct, 2),
            "files": [
                {
                    "path": r.path,
                    "executable_added": r.executable_added,
                    "covered_added": r.covered_added,
                    "percent_covered": round(r.percent_covered, 2),
                    "uncovered_lines": r.uncovered_lines,
                }
                for r in results
            ],
        }
        print(json.dumps(payload, indent=2))
    else:
        stream = sys.stdout if passed else sys.stderr
        print(md, file=stream, end="")

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
