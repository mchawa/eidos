"""Git-native drift detection and spec reconciliation package."""

from eidos.vcs.drift import (
    ChangedFilesClassification,
    DriftCheckResult,
    GitResolutionError,
    InvariantTrace,
    ReconcileResult,
    ReconcileSuggestion,
    check_pr_drift,
    classify_changed_files,
    detect_drift,
    get_living_specs_catalog,
    map_code_to_components,
    reconcile_specs,
)

__all__ = [
    "ChangedFilesClassification",
    "DriftCheckResult",
    "GitResolutionError",
    "InvariantTrace",
    "ReconcileResult",
    "ReconcileSuggestion",
    "check_pr_drift",
    "classify_changed_files",
    "detect_drift",
    "get_living_specs_catalog",
    "map_code_to_components",
    "reconcile_specs",
]
