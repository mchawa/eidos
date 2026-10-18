"""Core domain models for Eidos specifications."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


def _coerce_opt_str(val: Any) -> str | None:
    if isinstance(val, (str, int, float)) and not isinstance(val, bool):
        stripped = str(val).strip()
        return stripped if stripped else None
    return None


def _coerce_req_str(val: Any) -> str:
    if isinstance(val, (str, int, float)) and not isinstance(val, bool):
        return str(val).strip()
    return ""


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

    option: str = ""
    reason_rejected: str = ""

    @model_validator(mode="before")
    @classmethod
    def _normalize_input(cls, data: Any) -> dict[str, Any]:
        if isinstance(data, str):
            return {"option": data.strip(), "reason_rejected": ""}
        if not isinstance(data, dict):
            return {}
        raw = dict(data)
        raw["option"] = _coerce_req_str(raw.get("option"))
        raw["reason_rejected"] = _coerce_req_str(raw.get("reason_rejected"))
        return raw


class DecisionModel(_DictCompatibleModel):
    """Typed architectural decision model (DEC-*)."""

    id: str = ""
    title: str = ""
    decision: str = ""
    rationale: str = ""
    rejected_alternatives: list[RejectedAlternativeModel] = Field(default_factory=list)
    graduated_from: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_input(cls, data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            return {}
        raw = dict(data)
        for req_k in ("id", "title", "decision", "rationale"):
            raw[req_k] = _coerce_req_str(raw.get(req_k))
        raw["graduated_from"] = _coerce_opt_str(raw.get("graduated_from"))
        alts = raw.get("rejected_alternatives")
        raw["rejected_alternatives"] = (
            [a for a in alts if isinstance(a, (dict, str, RejectedAlternativeModel))]
            if isinstance(alts, list)
            else []
        )
        return raw


class InvariantModel(_DictCompatibleModel):
    """Typed capability/policy/container/component invariant model (INV-*)."""

    id: str = ""
    rule: str = ""
    test_binding: str | None = None
    verification: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_input(cls, data: Any) -> dict[str, Any]:
        if isinstance(data, str):
            return {"id": data.strip(), "rule": ""}
        if not isinstance(data, dict):
            return {}
        raw = dict(data)
        raw["id"] = _coerce_req_str(raw.get("id"))
        raw["rule"] = _coerce_req_str(raw.get("rule"))
        raw["test_binding"] = _coerce_opt_str(raw.get("test_binding"))
        raw["verification"] = _coerce_opt_str(raw.get("verification"))
        return raw

    def effective_test_binding(self) -> str:
        """Return normalized test_binding or legacy verification string."""
        return str(self.test_binding or self.verification or "").strip()


class InlineComponentModel(_DictCompatibleModel):
    """Typed inline or reference C4 Level 3 component entry inside a container spec (CMP-*)."""

    id: str = ""
    name: str | None = None
    ref: str | None = None
    status: str | None = None
    domain_binding: str | None = None
    responsibility: str | None = None
    source_path: list[str] | str | None = None
    decisions: list[DecisionModel] = Field(default_factory=list)
    invariants: list[InvariantModel] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_input(cls, data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            return {}
        raw = dict(data)
        raw["id"] = _coerce_req_str(raw.get("id"))
        for opt_k in ("name", "ref", "status", "domain_binding", "responsibility"):
            raw[opt_k] = _coerce_opt_str(raw.get(opt_k))
        sp = raw.get("source_path")
        if isinstance(sp, list):
            raw["source_path"] = [str(p).strip() for p in sp if _coerce_opt_str(p)]
        elif _coerce_opt_str(sp) is not None:
            raw["source_path"] = _coerce_opt_str(sp)
        else:
            raw["source_path"] = None
        decs = raw.get("decisions")
        raw["decisions"] = (
            [d for d in decs if isinstance(d, (dict, DecisionModel))] if isinstance(decs, list) else []
        )
        invs = raw.get("invariants")
        raw["invariants"] = (
            [i for i in invs if isinstance(i, (dict, str, InvariantModel))] if isinstance(invs, list) else []
        )
        return raw

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

    @model_validator(mode="before")
    @classmethod
    def _normalize_input(cls, data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            return {}
        raw = dict(data)
        cnts = raw.get("containers")
        raw["containers"] = (
            [str(c).strip() for c in cnts if _coerce_opt_str(c)] if isinstance(cnts, list) else []
        )
        cmps = raw.get("components")
        raw["components"] = (
            [str(c).strip() for c in cmps if _coerce_opt_str(c)] if isinstance(cmps, list) else []
        )
        invs = raw.get("invariants")
        raw["invariants"] = (
            [i for i in invs if isinstance(i, (dict, str, InvariantModel))] if isinstance(invs, list) else []
        )
        return raw


class DeltaIntentModel(_DictCompatibleModel):
    """Typed SpecDelta intent metadata."""

    kind: str | None = None
    motivation: str | None = None
    bug_classification: str | None = None
    severity: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_input(cls, data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            return {}
        raw = dict(data)
        for opt_k in ("kind", "motivation", "bug_classification", "severity"):
            raw[opt_k] = _coerce_opt_str(raw.get(opt_k))
        return raw


class DeltaMutationModel(_DictCompatibleModel):
    """Typed composable SpecDelta mutation entry."""

    operation: str = ""
    target: str = ""
    parent: str | None = None
    title: str | None = None
    details: str | None = None
    expect: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _normalize_input(cls, data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            return {}
        raw = dict(data)
        raw["operation"] = _coerce_req_str(raw.get("operation"))
        raw["target"] = _coerce_req_str(raw.get("target"))
        for opt_k in ("parent", "title", "details"):
            raw[opt_k] = _coerce_opt_str(raw.get(opt_k))
        exp = raw.get("expect")
        raw["expect"] = dict(exp) if isinstance(exp, dict) else {}
        return raw


_FRONTMATTER_SCALAR_KEYS = (
    "id",
    "type",
    "title",
    "name",
    "status",
    "version",
    "domain",
    "domain_binding",
    "container",
    "container_type",
    "runtime",
    "owning_container",
    "owning_component",
    "protocol",
    "specification_file",
    "rule",
    "schema",
)


class FrontmatterModel(_DictCompatibleModel):
    """Typed Pydantic frontmatter domain model for living specifications (CMP-DOMAIN-MODELS)."""

    id: str | None = None
    type: str | None = None
    title: str | None = None
    name: str | None = None
    status: str | None = None
    version: str | None = None
    domain: str | None = None
    domain_binding: str | None = None
    container: str | None = None
    container_type: str | None = None
    runtime: str | None = None
    owning_container: str | None = None
    owning_component: str | None = None
    protocol: str | None = None
    specification_file: str | None = None
    rule: str | None = None
    schema_: str | None = Field(default=None, alias="schema")
    package_path: list[str] | str | None = None
    source_path: list[str] | str | None = None
    components: list[InlineComponentModel] = Field(default_factory=list)
    technical: CapabilityTechnicalModel = Field(default_factory=CapabilityTechnicalModel)
    intent: DeltaIntentModel | None = None
    mutations: list[DeltaMutationModel] = Field(default_factory=list)
    decisions: list[DecisionModel] = Field(default_factory=list)
    invariants: list[InvariantModel] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_input(cls, data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            return {}
        raw = dict(data)
        for scalar_key in _FRONTMATTER_SCALAR_KEYS:
            if scalar_key in raw:
                raw[scalar_key] = _coerce_opt_str(raw.get(scalar_key))
        for path_key in ("package_path", "source_path"):
            pval = raw.get(path_key)
            if isinstance(pval, list):
                raw[path_key] = [str(p).strip() for p in pval if _coerce_opt_str(p)]
            elif _coerce_opt_str(pval) is not None:
                raw[path_key] = _coerce_opt_str(pval)
            else:
                raw[path_key] = None
        comps_raw = raw.get("components")
        if not comps_raw and isinstance(raw.get("c4_internal_components"), list):
            comps_raw = raw.get("c4_internal_components")
        raw["components"] = (
            [c for c in comps_raw if isinstance(c, (dict, InlineComponentModel))]
            if isinstance(comps_raw, list)
            else []
        )
        tech_raw = raw.get("technical")
        raw["technical"] = tech_raw if isinstance(tech_raw, (dict, CapabilityTechnicalModel)) else {}
        intent_raw = raw.get("intent")
        raw["intent"] = intent_raw if isinstance(intent_raw, (dict, DeltaIntentModel)) else None
        muts_raw = raw.get("mutations")
        raw["mutations"] = (
            [m for m in muts_raw if isinstance(m, (dict, DeltaMutationModel))]
            if isinstance(muts_raw, list)
            else []
        )
        decs_raw = raw.get("decisions")
        raw["decisions"] = (
            [d for d in decs_raw if isinstance(d, (dict, DecisionModel))]
            if isinstance(decs_raw, list)
            else []
        )
        invs_raw = raw.get("invariants")
        raw["invariants"] = (
            [i for i in invs_raw if isinstance(i, (dict, str, InvariantModel))]
            if isinstance(invs_raw, list)
            else []
        )
        return raw

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

    def effective_package_paths(self) -> list[str]:
        """Return package_paths if declared, otherwise fallback to source_paths."""
        return self.package_paths() or self.source_paths()

    def effective_domain(self) -> str:
        """Return normalized domain or domain_binding string."""
        return str(self.domain or self.domain_binding or "").strip()

    def invariants_with_component(self) -> list[tuple[InvariantModel, str | None]]:
        """Return (InvariantModel, owning_inline_component_id_or_None) across top-level, technical, and inline components."""
        collected: list[tuple[InvariantModel, str | None]] = [(inv, None) for inv in self.invariants if inv.id]
        if self.technical and self.technical.invariants:
            collected.extend((inv, None) for inv in self.technical.invariants if inv.id)
        for comp in self.components:
            if comp.invariants:
                cid = comp.id or None
                collected.extend((inv, cid) for inv in comp.invariants if inv.id)
        return collected

    def all_invariants(self) -> list[InvariantModel]:
        """Return all invariants declared at top level, under technical.invariants, or on inline components."""
        return [inv for inv, _ in self.invariants_with_component()]

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
        """Return the frontmatter parsed into a typed Pydantic FrontmatterModel with fault-tolerant coercion."""
        if self._model is None:
            try:
                self._model = FrontmatterModel.model_validate(self.frontmatter or {})
            except ValidationError:
                self._model = FrontmatterModel.model_construct()
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
