"""Core domain models for Eidos specifications."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RejectedAlternativeModel(BaseModel):
    """Typed architectural rejected alternative."""

    model_config = ConfigDict(extra="allow")
    option: str
    reason_rejected: str


class DecisionModel(BaseModel):
    """Typed architectural decision model (DEC-*)."""

    model_config = ConfigDict(extra="allow")
    id: str
    title: str
    decision: str
    rationale: str
    rejected_alternatives: list[RejectedAlternativeModel] = Field(default_factory=list)
    graduated_from: str | None = None


class InvariantModel(BaseModel):
    """Typed capability/policy invariant model (INV-*)."""

    model_config = ConfigDict(extra="allow")
    id: str
    rule: str
    test_binding: str | None = None


class FrontmatterModel(BaseModel):
    """Typed Pydantic frontmatter domain model for living specifications (CMP-DOMAIN-MODELS)."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str | None = None
    type: str | None = None
    title: str | None = None
    name: str | None = None
    status: str | None = None
    schema_: str | None = Field(default=None, alias="schema")
    package_path: list[str] | str | None = None
    source_path: list[str] | str | None = None
    decisions: list[DecisionModel] = Field(default_factory=list)
    invariants: list[InvariantModel] = Field(default_factory=list)

    def __getitem__(self, key: str) -> Any:
        if hasattr(self, key):
            val = getattr(self, key)
            if val is not None or key in self.model_fields:
                return val
        if self.__pydantic_extra__ and key in self.__pydantic_extra__:
            return self.__pydantic_extra__[key]
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default


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
    _model: FrontmatterModel | None = None

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def as_model(self) -> FrontmatterModel:
        """Return the frontmatter parsed into a typed Pydantic FrontmatterModel."""
        if self._model is None:
            self._model = FrontmatterModel.model_validate(self.frontmatter or {})
        return self._model


@dataclass
class ValidationReport:
    """Consolidated validation report for all specifications in a repository."""

    documents: list[SpecDocument] = field(default_factory=list)
    schema_errors: dict[str, list[str]] = field(default_factory=dict)
    broken_references: list[str] = field(default_factory=list)
    entity_index: dict[str, SpecDocument] = field(default_factory=dict)

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
        return self.invalid_docs == 0 and len(self.broken_references) == 0 and len(self.schema_errors) == 0
