"""Eidos core parsing, validation, graph modeling, and context slicing."""

from eidos.core.index.context import ArchitecturalContextSlice, get_context_slice
from eidos.core.spec.models import SpecDocument, ValidationReport
from eidos.core.spec.parser import parse_spec_file
from eidos.core.spec.validator import SpecValidator

__all__ = [
    "ArchitecturalContextSlice",
    "SpecDocument",
    "SpecValidator",
    "ValidationReport",
    "get_context_slice",
    "parse_spec_file",
]
