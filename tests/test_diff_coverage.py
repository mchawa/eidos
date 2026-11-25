"""Tests for the zero-dependency diff-line statement coverage verifier (`tests/check_diff_coverage.py`)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tests.check_diff_coverage import (
    evaluate_diff_coverage,
    format_line_ranges,
    main,
    parse_unified_diff_added_lines,
    render_markdown_summary,
)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_format_line_ranges_and_diff_parser():
    """Verify unified diff hunk parsing and line-range compaction."""
    assert format_line_ranges([]) == ""
    assert format_line_ranges([7]) == "7"
    assert format_line_ranges([10, 11, 12, 15, 20, 21, 15]) == "10-12, 15, 20-21"

    sample_diff = (
        "diff --git a/src/eidos/a.py b/src/eidos/a.py\n"
        "--- a/src/eidos/a.py\n"
        "+++ b/src/eidos/a.py\n"
        "@@ -4,0 +5,3 @@ def foo():\n"
        "+    x = 1\n"
        "+++ /dev/null\n"
        "+++ b/fake.py\n"
        "@@ -20 +23 @@ def bar():\n"
        "+    return 99\n"
        "@@ -30,2 +32,0 @@ def dead():\n"
        "-    old = 1\n"
        "diff --git a/src/eidos/deleted.py b/src/eidos/deleted.py\n"
        "--- a/src/eidos/deleted.py\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-x = 1\n"
    )
    parsed = parse_unified_diff_added_lines(sample_diff)
    assert parsed == {"src/eidos/a.py": {5, 6, 7, 23}}

    from tests.check_diff_coverage import _is_excluded

    assert _is_excluded("src/eidos_extra/x.py", "src/eidos", ()) is True
    assert _is_excluded("src/eidos/x.py", "src/eidos", ()) is False
    assert _is_excluded("root_mod.py", ".", ()) is False


def test_evaluate_diff_coverage_branches(tmp_path: Path):
    """Verify intersection with coverage.json across covered, uncovered, comment-only, untracked, and unimported modules."""
    unimported = tmp_path / "src" / "eidos" / "unimported.py"
    unimported.parent.mkdir(parents=True)
    unimported.write_text("a = 1\nb = 2\n", encoding="utf-8")

    cov_data = {
        "files": {
            "src/eidos/mod.py": {
                "executed_lines": [1, 2, 5, 6],
                "missing_lines": [7],
            },
            "src/eidos/new_file.py": {
                "executed_lines": [1, 2, 3],
                "missing_lines": [],
            },
        }
    }
    results, total_exec, total_cov, pct = evaluate_diff_coverage(
        cov_data,
        added_by_file={
            "src/eidos/mod.py": {3, 4, 5, 6, 7},  # 3,4 are comments/blank -> ignored; 5,6 covered; 7 uncovered
        },
        untracked_files={"src/eidos/new_file.py", "src/eidos/unimported.py"},
        repo_root=tmp_path,
    )
    by_path = {r.path: r for r in results}
    assert by_path["src/eidos/mod.py"].executable_added == 3
    assert by_path["src/eidos/mod.py"].covered_added == 2
    assert by_path["src/eidos/mod.py"].uncovered_lines == [7]
    assert by_path["src/eidos/new_file.py"].percent_covered == 100.0
    assert by_path["src/eidos/unimported.py"].percent_covered == 0.0
    assert by_path["src/eidos/unimported.py"].uncovered_lines == [1, 2]
    assert total_exec == 8
    assert total_cov == 5
    assert round(pct, 2) == 62.5

    md = render_markdown_summary(results, total_exec, total_cov, pct, 90.0)
    assert "FAIL" in md
    assert "`src/eidos/mod.py`" in md
    assert "`7`" in md


def test_check_diff_coverage_cli_end_to_end(tmp_path: Path, capsys):
    """Verify CLI main() against a real Git repo for uncommitted diffs, untracked files, branch diffs, JSON, and summary output."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "dev@example.com")
    _git(repo, "config", "user.name", "Dev")

    mod = repo / "src" / "eidos" / "sample.py"
    mod.parent.mkdir(parents=True)
    mod.write_text("def a():\n    return 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")

    # Missing coverage.json -> exit code 2
    assert main(["--repo-root", str(repo), "--coverage", "missing.json"]) == 2
    # Bad Git base ref -> clean exit code 2 on stderr (no uncaught traceback)
    cov_path = repo / "coverage.json"
    cov_path.write_text(
        json.dumps({"files": {"src/eidos/sample.py": {"executed_lines": [1, 2], "missing_lines": []}}}),
        encoding="utf-8",
    )
    assert main(["--repo-root", str(repo), "--coverage", str(cov_path), "--base", "nonexistent-ref-xyz"]) == 2
    assert "git diff failed" in capsys.readouterr().err

    # Clean working tree -> 0 executable lines added -> 100% pass
    assert main(["--repo-root", str(repo), "--coverage", str(cov_path)]) == 0
    assert "No executable Python lines added" in capsys.readouterr().out

    # Add 4 lines to tracked file (1 uncovered) + untracked file (fully covered)
    mod.write_text(
        "def a():\n    return 1\n\ndef b(flag):\n    if flag:\n        return 2\n    return 3\n", encoding="utf-8"
    )
    new_mod = repo / "src" / "eidos" / "brand_new.py"
    new_mod.write_text("def c():\n    return 9\n", encoding="utf-8")
    cov_path.write_text(
        json.dumps(
            {
                "files": {
                    "src/eidos/sample.py": {"executed_lines": [1, 2, 4, 5, 6], "missing_lines": [7]},
                    "src/eidos/brand_new.py": {"executed_lines": [1, 2], "missing_lines": []},
                }
            }
        ),
        encoding="utf-8",
    )

    # 5/6 = 83.33% < 90% -> FAIL (exit 1)
    summary_file = tmp_path / "summary.md"
    rc_fail = main(
        [
            "--repo-root",
            str(repo),
            "--coverage",
            str(cov_path),
            "--min-coverage",
            "90.0",
            "--summary-out",
            str(summary_file),
            "--json",
        ]
    )
    assert rc_fail == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is False
    assert payload["covered_added"] == 5
    assert payload["executable_added"] == 6
    assert "FAIL" in summary_file.read_text(encoding="utf-8")

    # Mark line 7 covered, commit on branch, verify --base main --head HEAD passes (100%)
    _git(repo, "checkout", "-b", "feat/cov")
    _git(repo, "add", "src/eidos/sample.py", "src/eidos/brand_new.py")
    _git(repo, "commit", "-m", "add b and brand_new")
    cov_path.write_text(
        json.dumps(
            {
                "files": {
                    "src/eidos/sample.py": {"executed_lines": [1, 2, 4, 5, 6, 7], "missing_lines": []},
                    "src/eidos/brand_new.py": {"executed_lines": [1, 2], "missing_lines": []},
                }
            }
        ),
        encoding="utf-8",
    )
    rc_pass = main(
        [
            "--repo-root",
            str(repo),
            "--coverage",
            str(cov_path),
            "--base",
            "main",
            "--head",
            "HEAD",
            "--min-coverage",
            "90.0",
            "--json",
        ]
    )
    assert rc_pass == 0
    pass_payload = json.loads(capsys.readouterr().out)
    assert pass_payload["passed"] is True
    assert pass_payload["percent_covered"] == 100.0
