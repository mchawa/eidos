"""VCS integration bridge and drift governance package."""

from eidos.vcs.bridge import VCSBridge, VCSIssue, VCSPullRequest
from eidos.vcs.drift import (
    ChangedFilesClassification,
    DriftCheckResult,
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
    "InvariantTrace",
    "ReconcileResult",
    "ReconcileSuggestion",
    "VCSBridge",
    "VCSIssue",
    "VCSPullRequest",
    "check_pr_drift",
    "classify_changed_files",
    "detect_drift",
    "get_living_specs_catalog",
    "map_code_to_components",
    "reconcile_specs",
]
