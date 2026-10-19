"""Git-native drift detection and spec verification package."""

from eidos.vcs.drift import (
    ChangedFilesClassification,
    DriftCheckResult,
    GitResolutionError,
    InvariantTrace,
    classify_changed_files,
    detect_drift,
    get_living_specs_catalog,
    map_code_to_components,
)

__all__ = [
    "ChangedFilesClassification",
    "DriftCheckResult",
    "GitResolutionError",
    "InvariantTrace",
    "classify_changed_files",
    "detect_drift",
    "get_living_specs_catalog",
    "map_code_to_components",
]
