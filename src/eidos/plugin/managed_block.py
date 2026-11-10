"""Marker-delimited Eidos managed blocks inside shared, user-owned instruction files (AGENTS.md, CLAUDE.md).

Eidos owns only the text between its own named markers; every byte outside them belongs to the user::

    <!-- eidos:begin agents-md sha256:3f9a1c0b7e2d targets=aider:.aider/skills,claude:.claude/skills · managed ... -->
    ...Eidos-owned content...
    <!-- eidos:end agents-md -->

* ``sha256`` is the first 12 hex digits of the SHA-256 of the LF-normalized inner content, so hand edits inside the
  block are detectable while ``git autocrlf`` conversions are not mistaken for edits.
* Blocks are spliced into the original text, so line endings and bytes outside the markers are preserved exactly.
* ``targets=`` accumulates sorted ``<target>:<skills-dir>`` pairs so several harness installs share one block.
* ``<!-- eidos:<name> off -->`` anywhere in the file opts that block out entirely.
* Malformed markers never trigger a guess: the file is left untouched and ``malformed`` is reported.
"""

from __future__ import annotations

import contextlib
import enum
import hashlib
import os
import re
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

_UTF8_BOM = b"\xef\xbb\xbf"
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_TARGET_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SKILLS_DIR_RE = re.compile(r"^[^\s,:<>]+$")
_FENCE_OPEN_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})[^\r\n]*$")
_MARKER_NOTE = "· managed by `eidos plugin`; edits inside this block are overwritten"


class BlockStatus(enum.StrEnum):
    CREATED = "created"
    APPENDED = "appended"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    MODIFIED = "modified"
    OPTED_OUT = "opted_out"
    MALFORMED = "malformed"
    SKIPPED = "skipped"

    @property
    def written(self) -> bool:
        """Return True when the file content reflects the latest rendered block."""
        return self in (BlockStatus.CREATED, BlockStatus.APPENDED, BlockStatus.UPDATED, BlockStatus.UNCHANGED)


@dataclass
class BlockResult:
    status: BlockStatus
    path: Path
    targets: dict[str, str] = field(default_factory=dict)
    detail: str = ""


@dataclass
class ParsedBlock:
    """A well-formed managed block located inside LF-normalized file text."""

    start: int
    end: int
    inner: str
    sha: str | None
    targets: dict[str, str]


def is_valid_skills_dir(skills_dir: str) -> bool:
    """Return True if a repository-relative skills directory can be recorded in a ``targets=`` marker."""
    return bool(skills_dir) and bool(_SKILLS_DIR_RE.match(skills_dir)) and not skills_dir.startswith("/")


def content_hash(inner: str) -> str:
    """Return the 12-hex-digit SHA-256 of LF-normalized block inner content."""
    normalized = inner.replace("\r\n", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def _begin_re(name: str) -> re.Pattern[str]:
    return re.compile(
        r"^[ \t]*<!--[ \t]*eidos:begin[ \t]+" + re.escape(name) + r"(?=[ \t]|-->)(?P<attrs>[^\r\n]*?)-->[ \t]*(?=\r?$)",
        re.M,
    )


def _end_re(name: str) -> re.Pattern[str]:
    return re.compile(r"^[ \t]*<!--[ \t]*eidos:end[ \t]+" + re.escape(name) + r"[ \t]*-->[ \t]*(?=\r?$)", re.M)


def _opt_out_re(name: str) -> re.Pattern[str]:
    return re.compile(r"^[ \t]*<!--[ \t]*eidos:" + re.escape(name) + r"[ \t]+off[ \t]*-->[ \t]*(?=\r?$)", re.M)


def _mask_fenced_code_blocks(text: str) -> str:
    """Replace fenced code blocks (``` or ~~~) with spaces while preserving character offsets and line breaks."""
    out: list[str] = []
    fence_char = ""
    fence_len = 0
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        ending = line[len(stripped) :]
        if not fence_char:
            m = _FENCE_OPEN_RE.match(stripped)
            if m and ("`" not in stripped[m.end(1) :] if m.group(1)[0] == "`" else True):
                fence_char = m.group(1)[0]
                fence_len = len(m.group(1))
                out.append(" " * len(stripped) + ending)
                continue
            out.append(line)
        else:
            if re.match(rf"^[ \t]{{0,3}}{re.escape(fence_char)}{{{fence_len},}}[ \t]*$", stripped):
                fence_char = ""
                fence_len = 0
            out.append(" " * len(stripped) + ending)
    return "".join(out)


def _line_no(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _parse_targets(attrs: str) -> dict[str, str]:
    m = re.search(r"(?:^|\s)targets=(\S+)", attrs)
    if not m:
        return {}
    targets: dict[str, str] = {}
    for pair in m.group(1).split(","):
        target, sep, skills_dir = pair.partition(":")
        if sep and _TARGET_RE.match(target) and is_valid_skills_dir(skills_dir):
            targets[target] = skills_dir
    return targets


def find_managed_block(text: str, name: str) -> ParsedBlock | None:
    """Locate a well-formed block in file text (LF or CRLF), ignoring fenced code blocks; raise ValueError on malformed markers."""
    searchable = _mask_fenced_code_blocks(text)
    begins = list(_begin_re(name).finditer(searchable))
    ends = list(_end_re(name).finditer(searchable))
    if not begins and not ends:
        return None
    if len(begins) != 1 or len(ends) != 1:
        lines = sorted(_line_no(text, m.start()) for m in begins + ends)
        raise ValueError(
            f"expected exactly one begin and one end marker for '{name}', found {len(begins)} begin and "
            f"{len(ends)} end marker(s) at line(s) {lines}"
        )
    begin, end = begins[0], ends[0]
    if end.start() < begin.end():
        raise ValueError(
            f"end marker for '{name}' (line {_line_no(text, end.start())}) precedes its begin marker "
            f"(line {_line_no(text, begin.start())})"
        )
    inner_start = begin.end()
    if text.startswith("\r\n", inner_start):
        inner_start += 2
    elif text.startswith("\n", inner_start):
        inner_start += 1
    attrs = begin.group("attrs")
    sha_m = re.search(r"(?:^|\s)sha256:([0-9a-f]{12})(?=\s|$)", attrs)
    return ParsedBlock(
        start=begin.start(),
        end=end.end(),
        inner=text[inner_start : end.start()],
        sha=sha_m.group(1) if sha_m else None,
        targets=_parse_targets(attrs),
    )


def read_managed_block(path: Path, name: str) -> ParsedBlock | None:
    """Read and parse a managed block from a file (None if absent; ValueError if malformed)."""
    if not path.is_file():
        return None
    raw = path.read_bytes()
    if raw.startswith(_UTF8_BOM):
        raw = raw[len(_UTF8_BOM) :]
    return find_managed_block(raw.decode("utf-8"), name)


def render_managed_block(name: str, inner: str, targets: Mapping[str, str] | None = None) -> str:
    """Render a complete managed block (begin marker, inner content, end marker) without a trailing newline."""
    if not inner.endswith("\n"):
        inner += "\n"
    attrs = f"sha256:{content_hash(inner)}"
    if targets:
        attrs += " targets=" + ",".join(f"{t}:{d}" for t, d in sorted(targets.items()))
    return f"<!-- eidos:begin {name} {attrs} {_MARKER_NOTE} -->\n{inner}<!-- eidos:end {name} -->"


def _refuse_symlink(path: Path, root: Path | None) -> None:
    if path.is_symlink():
        raise FileExistsError(f"Destination path '{path}' is a symlink; refusing to write through symlink.")
    if root is None:
        return
    stop = {root, root.resolve()}
    for parent in path.parents:
        if parent in stop:
            break
        if parent.is_symlink():
            raise FileExistsError(f"Parent directory '{parent}' of '{path}' is a symlink; refusing to write.")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        if path.exists():
            os.chmod(tmp, path.stat().st_mode & 0o7777)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def upsert_managed_block(
    path: Path,
    name: str,
    content: str | Callable[[Mapping[str, str]], str],
    *,
    target: str | None = None,
    skills_dir: str | None = None,
    keep_target: Callable[[str, str], bool] | None = None,
    force: bool = False,
    root: Path | None = None,
) -> BlockResult:
    """Create or refresh the named managed block in ``path`` without touching any byte outside its markers.

    ``content`` is either static inner text or a renderer receiving the merged, sorted ``targets`` mapping.
    When ``target`` and ``skills_dir`` are given, the pair is merged into the block's ``targets=`` list;
    existing entries may be filtered through ``keep_target(target, skills_dir)``.
    ``force`` permits overwriting inner content that was edited by hand (hash mismatch).
    """
    if not _NAME_RE.match(name):
        raise ValueError(f"Invalid managed block name: {name!r}")
    if target is not None:
        if not _TARGET_RE.match(target):
            raise ValueError(f"Invalid managed block target: {target!r}")
        if skills_dir is None or not is_valid_skills_dir(skills_dir):
            raise ValueError(f"Invalid skills directory for target {target!r}: {skills_dir!r}")
    _refuse_symlink(path, root)

    raw = path.read_bytes() if path.exists() else b""
    has_bom = raw.startswith(_UTF8_BOM)
    if has_bom:
        raw = raw[len(_UTF8_BOM) :]
    text = raw.decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"

    if _opt_out_re(name).search(_mask_fenced_code_blocks(text)):
        return BlockResult(BlockStatus.OPTED_OUT, path, detail=f"opt-out marker <!-- eidos:{name} off --> present")

    try:
        block = find_managed_block(text, name)
    except ValueError as exc:
        return BlockResult(BlockStatus.MALFORMED, path, detail=str(exc))

    targets: dict[str, str] = (
        {t: d for t, d in block.targets.items() if keep_target is None or keep_target(t, d)} if block else {}
    )
    if target is not None and skills_dir is not None:
        targets[target] = skills_dir
    targets = dict(sorted(targets.items()))
    inner = content(targets) if callable(content) else content
    rendered = render_managed_block(name, inner.replace("\r\n", "\n"), targets).replace("\n", newline)

    if block is None:
        if text.strip():
            # Keep every original byte as an untouched prefix; separate the block by one blank line.
            prefix = text if text.endswith("\n") else text + newline
            new_text = prefix + newline + rendered + newline
            status = BlockStatus.APPENDED
        else:
            new_text = rendered + newline
            status = BlockStatus.CREATED
    else:
        hand_edited = block.sha is None or block.sha != content_hash(block.inner)
        if hand_edited and not force:
            return BlockResult(
                BlockStatus.MODIFIED,
                path,
                targets=dict(block.targets),
                detail="block content was edited by hand; re-run 'eidos plugin install <target> --force' to overwrite",
            )
        if text[block.start : block.end] == rendered:
            return BlockResult(BlockStatus.UNCHANGED, path, targets=targets)
        new_text = text[: block.start] + rendered + text[block.end :]
        status = BlockStatus.UPDATED

    _atomic_write(path, (_UTF8_BOM if has_bom else b"") + new_text.encode("utf-8"))
    return BlockResult(status, path, targets=targets)
