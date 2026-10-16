"""Core domain models for Eidos specifications."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _DictCompatibleModel(BaseModel):
    """Base Pydantic model with backwards-compatible dict-style key lookup."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    def __getitem__(self, key: str) -> Any:
        if key in self.__class__.model_fields:
            return getattr(self, key)
        for field_name, field_info in self.__class__.model_fields.items():
            if field_info.alias == key:
                return getattr(self, field_name)
        if self.__pydantic_extra__ and key in self.__pydantic_extra__:
            return self.__pydantic_extra__[key]
        if hasattr(self, key):
            val = getattr(self, key)
            if not callable(val) and val is not None:
                return val
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        if key in self.__class__.model_fields and getattr(self, key, None) is not None:
            return True
        for field_name, field_info in self.__class__.model_fields.items():
            if field_info.alias == key and getattr(self, field_name, None) is not None:
                return True
        return bool(self.__pydantic_extra__ and key in self.__pydantic_extra__)


class RejectedAlternativeModel(_DictCompatibleModel):
    """Typed architectural rejected alternative."""

    option: str
    reason_rejected: str


class DecisionModel(_DictCompatibleModel):
    """Typed architectural decision model (DEC-*)."""

    id: str
    title: str
    decision: str
    rationale: str
    rejected_alternatives: list[RejectedAlternativeModel] = Field(default_factory=list)
    graduated_from: str | None = None


class InvariantModel(_DictCompatibleModel):
    """Typed capability/policy/container/component invariant model (INV-*)."""

    id: str
    rule: str
    test_binding: str | None = None


class InlineComponentModel(_DictCompatibleModel):
    """Typed inline or reference C4 Level 3 component entry inside a container spec (CMP-*)."""

    id: str
    name: str | None = None
    ref: str | None = None
    status: str | None = None
    domain_binding: str | None = None
    responsibility: str | None = None
    source_path: list[str] | str | None = None
    decisions: list[DecisionModel] = Field(default_factory=list)
    invariants: list[InvariantModel] = Field(default_factory=list)

    def source_paths(self) -> list[str]:
        if isinstance(self.source_path, str) and self.source_path.strip():
            return [self.source_path.strip()]
        if isinstance(self.source_path, list):
            return [str(p).strip() for p in self.source_path if str(p).strip()]
        return []


class CapabilityTechnicalModel(_DictCompatibleModel):
    """Typed capability technical binding section."""

    containers: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    invariants: list[InvariantModel] = Field(default_factory=list)


class DeltaIntentModel(_DictCompatibleModel):
    """Typed SpecDelta intent metadata."""

    kind: str | None = None
    motivation: str | None = None
    bug_classification: str | None = None
    severity: str | None = None


class DeltaMutationModel(_DictCompatibleModel):
    """Typed SpecDelta mutation entry."""

    action: str
    details: str | None = None
    expect: dict[str, Any] = Field(default_factory=dict)


class FrontmatterModel(_DictCompatibleModel):
    """Typed Pydantic frontmatter domain model for living specifications (CMP-DOMAIN-MODELS)."""

    id: str | None = None
    type: str | None = None
    title: str | None = None
    name: str | None = None
    status: str | None = None
    version: str | None = None
    domain: str | None = None
    container: str | None = None
    owning_container: str | None = None
    protocol: str | None = None
    specification_file: str | None = None
    rule: str | None = None
    schema_: str | None = Field(default=None, alias="schema")
    package_path: list[str] | str | None = None
    source_path: list[str] | str | None = None
    components: list[InlineComponentModel] = Field(default_factory=list)
    technical: CapabilityTechnicalModel | None = None
    intent: DeltaIntentModel | None = None
    mutations: list[DeltaMutationModel] = Field(default_factory=list)
    decisions: list[DecisionModel] = Field(default_factory=list)
    invariants: list[InvariantModel] = Field(default_factory=list)

    def source_paths(self) -> list[str]:
        """Return normalized list of top-level source_path strings."""
        if isinstance(self.source_path, str) and self.source_path.strip():
            return [self.source_path.strip()]
        if isinstance(self.source_path, list):
            return [str(p).strip() for p in self.source_path if str(p).strip()]
        return []

    def package_paths(self) -> list[str]:
        """Return normalized list of top-level package_path strings."""
        if isinstance(self.package_path, str) and self.package_path.strip():
            return [self.package_path.strip()]
        if isinstance(self.package_path, list):
            return [str(p).strip() for p in self.package_path if str(p).strip()]
        return []

    def all_invariants(self) -> list[InvariantModel]:
        """Return all invariants declared at top level, under technical.invariants, or on inline components."""
        collected: list[InvariantModel] = list(self.invariants)
        if self.technical and self.technical.invariants:
            collected.extend(self.technical.invariants)
        for comp in self.components:
            if comp.invariants:
                collected.extend(comp.invariants)
        return collected

    def all_decisions(self) -> list[DecisionModel]:
        """Return all architectural decisions declared at top level or on inline components."""
        collected: list[DecisionModel] = list(self.decisions)
        for comp in self.components:
            if comp.decisions:
                collected.extend(comp.decisions)
        return collected


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
            raw = dict(self.frontmatter or {})
            for scalar_key in ("id", "type", "title", "name", "status", "version", "schema"):
                val = raw.get(scalar_key)
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    raw[scalar_key] = str(val)
            self._model = FrontmatterModel.model_validate(raw)
        return self._model

    @property
    def model(self) -> FrontmatterModel:
        """Convenience property returning the typed Pydantic FrontmatterModel."""
        return self.as_model()


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
