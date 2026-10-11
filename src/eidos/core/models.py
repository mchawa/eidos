"""Core domain models for Eidos specifications."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SpecDocument:
    """Represents a parsed AsciiDoc document with its YAML frontmatter metadata."""

    file_path: Path
    frontmatter: dict[str, Any]
    body: str
    doc_type: str
    doc_id: str
    schema_ref: str | None = None
    title: str | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0


@dataclass
class ValidationReport:
    """Consolidated validation report for all specifications in a repository."""

    documents: list[SpecDocument] = field(default_factory=list)
    schema_errors: dict[str, list[str]] = field(default_factory=dict)
    broken_references: list[str] = field(default_factory=list)

    @property
    def total_docs(self) -> int:
        return len(self.documents)

    @property
    def valid_docs(self) -> int:
        return len([d for d in self.documents if d.is_valid])

    @property
    def invalid_docs(self) -> int:
        return self.total_docs - self.valid_docs

    @property
    def is_clean(self) -> bool:
        return self.invalid_docs == 0 and len(self.broken_references) == 0
