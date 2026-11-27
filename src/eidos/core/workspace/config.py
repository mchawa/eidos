"""Optional project-scoped repository configuration loader and validator (CMP-REPO-CONFIG)."""

from __future__ import annotations

import functools
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import jsonschema

from eidos.core.spec.parser import YamlError, safe_load_yaml
from eidos.core.workspace.assets import get_config_schema_path

CONFIG_FILENAME = "eidos.yaml"
CONFIG_SCHEMA_VERSION = 1

CONCRETE_PLUGIN_TARGETS: tuple[str, ...] = (
    "antigravity",
    "claude",
    "cursor",
    "aider",
    "cline",
    "kilocode",
    "open-code",
    "codex",
    "copilot",
)

CHECK_SEVERITIES: tuple[str, ...] = ("error", "warn", "off")
CONTEXT_TIERS: tuple[str, ...] = ("anchor", "guardrails", "deep")
SEMANTIC_VARIANTS: tuple[str, ...] = ("quantized", "fp32", "fp16")

_BOOL_TRUE = frozenset({"1", "true", "yes", "on"})
_BOOL_FALSE = frozenset({"0", "false", "no", "off"})

CONFIG_ENV_VARS: tuple[dict[str, str], ...] = (
    {
        "env_var": "EIDOS_DRIFT_BASE_REF",
        "config_key": "drift.base_ref",
        "type": "string",
        "description": "Override drift.base_ref ahead of the default base branch fallback chain.",
    },
    {
        "env_var": "EIDOS_DRIFT_STRICT",
        "config_key": "drift.strict",
        "type": "boolean",
        "description": "Override drift.strict (1/true/yes/on or 0/false/no/off).",
    },
    {
        "env_var": "EIDOS_DRIFT_WARN_ONLY",
        "config_key": "drift.warn_only",
        "type": "boolean",
        "description": "Override drift.warn_only (1/true/yes/on or 0/false/no/off).",
    },
    {
        "env_var": "EIDOS_DRIFT_FAIL_ON_WARNINGS",
        "config_key": "drift.fail_on_warnings",
        "type": "boolean",
        "description": "Override drift.fail_on_warnings (1/true/yes/on or 0/false/no/off).",
    },
    {
        "env_var": "EIDOS_CONTEXT_TIER",
        "config_key": "context.default_tier",
        "type": "enum(anchor | guardrails | deep)",
        "description": "Override context.default_tier for context slicing.",
    },
    {
        "env_var": "EIDOS_SEMANTIC_ENABLED",
        "config_key": "context.semantic.enabled",
        "type": "boolean",
        "description": "Override context.semantic.enabled (1/true/yes/on or 0/false/no/off).",
    },
    {
        "env_var": "EIDOS_SEMANTIC_VARIANT",
        "config_key": "context.semantic.variant",
        "type": "enum(quantized | fp32 | fp16)",
        "description": "Override context.semantic.variant for local ONNX model setup.",
    },
    {
        "env_var": "EIDOS_SEMANTIC_MIRROR_URL",
        "config_key": "context.semantic.model_mirror_url",
        "type": "http(s) url",
        "description": "Override context.semantic.model_mirror_url (strictly http:// or https://).",
    },
    {
        "env_var": "EIDOS_HOOKS_ENABLED",
        "config_key": "plugin.hooks.enabled",
        "type": "boolean",
        "description": "Override plugin.hooks.enabled runtime kill-switch (1/true/yes/on or 0/false/no/off).",
    },
    {
        "env_var": "EIDOS_NO_UPDATE_CHECK",
        "config_key": "plugin.updates_check",
        "type": "boolean",
        "description": "When truthy, disables PyPI CLI update checks regardless of eidos/eidos.yaml or EIDOS_FORCE_UPDATE_CHECK.",
    },
    {
        "env_var": "EIDOS_FORCE_UPDATE_CHECK",
        "config_key": "plugin.updates_check",
        "type": "boolean",
        "description": "When truthy (and EIDOS_NO_UPDATE_CHECK is unset), re-enables PyPI CLI update checks over plugin.updates_check: false.",
    },
)

_ENV_VAR_NAMES: frozenset[str] = frozenset(item["env_var"] for item in CONFIG_ENV_VARS)

STARTER_CONFIG_YAML = """# Optional project-scoped Eidos engine and workflow configuration.
# Living specifications under eidos/*.adoc remain the single source of architectural truth.
# Precedence: CLI flags > EIDOS_* env vars > eidos/eidos.yaml > built-in defaults.
# Run `eidos config schema` to inspect all supported keys, constraints, and EIDOS_* env vars.
version: 1

# workspace:
#   exclude:                       # completely ignored from indexing, source ownership, tests, and drift
#     - "generated/**"
#   tests:
#     include: []                  # extra globs classified as automated tests
#     exclude: []                  # reclassified from test -> production source (use workspace.exclude to ignore helper scripts)
#   subprojects_exclude:
#     - "examples/**"

# drift:
#   base_ref: null
#   strict: false
#   warn_only: false
#   fail_on_warnings: false
#   ignore_paths: []
#   checks:
#     mermaid: error               # error | warn | off
#     import_boundaries: error     # error | warn | off
#     public_surface: error        # error | warn | off
#     contract_parity: error       # error | warn | off
#     deprecated_references: warn  # error | warn | off

# context:
#   default_tier: guardrails       # anchor | guardrails | deep
#   budgets:
#     anchor: 300
#     guardrails: 1000
#     deep: 2500
#   repo_map_max_tokens: 1500
#   semantic:
#     enabled: true
#     variant: quantized           # quantized | fp32 | fp16
#     model_mirror_url: null       # optional https:// or http:// mirror base URL

# memory:
#   commit_drift_threshold: 5
#   tier_caps:
#     anchor: [2, 0]
#     guardrails: [3, 2]
#     deep: [5, 3]
#   default_expiry_days: null

# lifecycle:
#   archive:
#     keep_recent: 15
#     include_explorations: true

# plugin:
#   targets: []                    # e.g. [claude, cursor]
#   launcher: eidos                # eidos | uvx
#   updates_check: true
#   hooks:
#     install: false
#     enabled: true
#     strict: null                 # inherits drift.strict when null
#     fail_on_warnings: null       # inherits drift.fail_on_warnings when null
#     baseline_ttl_seconds: 86400

# portal:
#   serve:
#     host: "127.0.0.1"
#     port: 3000
#     open_browser: false
#   build:
#     out_dir: "site"
#     single_file: false
"""


class ConfigValidationError(ValueError):
    """Raised when eidos/eidos.yaml or an EIDOS_* config environment variable fails validation."""

    def __init__(self, errors: list[str] | tuple[str, ...], config_path: Path | None = None) -> None:
        self.errors: tuple[str, ...] = tuple(errors)
        self.config_path: Path | None = config_path
        prefix = (
            f"Invalid configuration in '{config_path}'" if config_path is not None else "Invalid Eidos configuration"
        )
        detail = "; ".join(self.errors) if self.errors else "unknown validation error"
        super().__init__(f"{prefix}: {detail}")


@dataclass(frozen=True)
class WorkspaceTestsConfig:
    """Test file classification glob overrides."""

    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"include": list(self.include), "exclude": list(self.exclude)}


@dataclass(frozen=True)
class WorkspaceConfig:
    """Repository file discovery, exclusion, test classification, and sub-project boundaries."""

    exclude: tuple[str, ...] = ()
    tests: WorkspaceTestsConfig = field(default_factory=WorkspaceTestsConfig)
    subprojects_exclude: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "exclude": list(self.exclude),
            "tests": self.tests.to_dict(),
            "subprojects_exclude": list(self.subprojects_exclude),
        }


@dataclass(frozen=True)
class DriftChecksConfig:
    """Per-check enforcement severities ('error', 'warn', or 'off')."""

    mermaid: str = "error"
    import_boundaries: str = "error"
    public_surface: str = "error"
    contract_parity: str = "error"
    deprecated_references: str = "warn"

    def to_dict(self) -> dict[str, Any]:
        return {
            "mermaid": self.mermaid,
            "import_boundaries": self.import_boundaries,
            "public_surface": self.public_surface,
            "contract_parity": self.contract_parity,
            "deprecated_references": self.deprecated_references,
        }


@dataclass(frozen=True)
class DriftConfig:
    """Drift detection and specification validation default configuration."""

    base_ref: str | None = None
    strict: bool = False
    warn_only: bool = False
    fail_on_warnings: bool = False
    ignore_paths: tuple[str, ...] = ()
    checks: DriftChecksConfig = field(default_factory=DriftChecksConfig)

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_ref": self.base_ref,
            "strict": self.strict,
            "warn_only": self.warn_only,
            "fail_on_warnings": self.fail_on_warnings,
            "ignore_paths": list(self.ignore_paths),
            "checks": self.checks.to_dict(),
        }


@dataclass(frozen=True)
class ContextBudgetsConfig:
    """Per-tier token budget ceilings for context slicing."""

    anchor: int = 300
    guardrails: int = 1000
    deep: int = 2500

    def as_dict(self) -> dict[str, int]:
        return {"anchor": self.anchor, "guardrails": self.guardrails, "deep": self.deep}

    def to_dict(self) -> dict[str, Any]:
        return self.as_dict()


@dataclass(frozen=True)
class SemanticConfig:
    """Optional local ONNX semantic indexing and retrieval configuration."""

    enabled: bool = True
    variant: str = "quantized"
    model_mirror_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "variant": self.variant,
            "model_mirror_url": self.model_mirror_url,
        }


@dataclass(frozen=True)
class ContextConfig:
    """Context slicing tier, token budgets, repo-map limits, and semantic settings."""

    default_tier: str = "guardrails"
    budgets: ContextBudgetsConfig = field(default_factory=ContextBudgetsConfig)
    repo_map_max_tokens: int = 1500
    semantic: SemanticConfig = field(default_factory=SemanticConfig)

    def to_dict(self) -> dict[str, Any]:
        return {
            "default_tier": self.default_tier,
            "budgets": self.budgets.to_dict(),
            "repo_map_max_tokens": self.repo_map_max_tokens,
            "semantic": self.semantic.to_dict(),
        }


@dataclass(frozen=True)
class MemoryTierCapsConfig:
    """Per-tier (max_anchored, max_global) team memory attachment caps."""

    anchor: tuple[int, int] = (2, 0)
    guardrails: tuple[int, int] = (3, 2)
    deep: tuple[int, int] = (5, 3)

    def as_dict(self) -> dict[str, tuple[int, int]]:
        return {"anchor": self.anchor, "guardrails": self.guardrails, "deep": self.deep}

    def to_dict(self) -> dict[str, Any]:
        return {
            "anchor": list(self.anchor),
            "guardrails": list(self.guardrails),
            "deep": list(self.deep),
        }


@dataclass(frozen=True)
class MemoryConfig:
    """Team operational memory freshness threshold, tier caps, and default TTL."""

    commit_drift_threshold: int = 5
    tier_caps: MemoryTierCapsConfig = field(default_factory=MemoryTierCapsConfig)
    default_expiry_days: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "commit_drift_threshold": self.commit_drift_threshold,
            "tier_caps": self.tier_caps.to_dict(),
            "default_expiry_days": self.default_expiry_days,
        }


@dataclass(frozen=True)
class LifecycleArchiveConfig:
    """SpecDelta and Exploration archive retention defaults."""

    keep_recent: int = 15
    include_explorations: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "keep_recent": self.keep_recent,
            "include_explorations": self.include_explorations,
        }


@dataclass(frozen=True)
class LifecycleConfig:
    """Lifecycle archival configuration."""

    archive: LifecycleArchiveConfig = field(default_factory=LifecycleArchiveConfig)

    def to_dict(self) -> dict[str, Any]:
        return {"archive": self.archive.to_dict()}


@dataclass(frozen=True)
class PluginHooksConfig:
    """Agent Stop hook installation default and runtime enforcement settings."""

    install: bool = False
    enabled: bool = True
    strict: bool | None = None
    fail_on_warnings: bool | None = None
    baseline_ttl_seconds: float = 86400.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "install": self.install,
            "enabled": self.enabled,
            "strict": self.strict,
            "fail_on_warnings": self.fail_on_warnings,
            "baseline_ttl_seconds": self.baseline_ttl_seconds,
        }


@dataclass(frozen=True)
class PluginConfig:
    """Agent plugin targets, launcher, update checks, and hook configuration."""

    targets: tuple[str, ...] = ()
    launcher: str = "eidos"
    updates_check: bool = True
    hooks: PluginHooksConfig = field(default_factory=PluginHooksConfig)

    def to_dict(self) -> dict[str, Any]:
        return {
            "targets": list(self.targets),
            "launcher": self.launcher,
            "updates_check": self.updates_check,
            "hooks": self.hooks.to_dict(),
        }


@dataclass(frozen=True)
class PortalServeConfig:
    """Living Web Portal local preview server defaults."""

    host: str = "127.0.0.1"
    port: int = 3000
    open_browser: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "open_browser": self.open_browser,
        }


@dataclass(frozen=True)
class PortalBuildConfig:
    """Living Web Portal static site build defaults."""

    out_dir: str = "site"
    single_file: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "out_dir": self.out_dir,
            "single_file": self.single_file,
        }


@dataclass(frozen=True)
class PortalConfig:
    """Living Web Portal serve and build configuration."""

    serve: PortalServeConfig = field(default_factory=PortalServeConfig)
    build: PortalBuildConfig = field(default_factory=PortalBuildConfig)

    def to_dict(self) -> dict[str, Any]:
        return {
            "serve": self.serve.to_dict(),
            "build": self.build.to_dict(),
        }


@dataclass(frozen=True)
class EidosConfig:
    """Typed, immutable repository configuration resolved from defaults, eidos/eidos.yaml, and EIDOS_* env vars."""

    version: int = CONFIG_SCHEMA_VERSION
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    drift: DriftConfig = field(default_factory=DriftConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    lifecycle: LifecycleConfig = field(default_factory=LifecycleConfig)
    plugin: PluginConfig = field(default_factory=PluginConfig)
    portal: PortalConfig = field(default_factory=PortalConfig)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "workspace": self.workspace.to_dict(),
            "drift": self.drift.to_dict(),
            "context": self.context.to_dict(),
            "memory": self.memory.to_dict(),
            "lifecycle": self.lifecycle.to_dict(),
            "plugin": self.plugin.to_dict(),
            "portal": self.portal.to_dict(),
        }

    def effective_hook_strict(self) -> bool:
        """Resolve effective Stop hook strict mode (plugin.hooks.strict if set, else drift.strict)."""
        return self.plugin.hooks.strict if self.plugin.hooks.strict is not None else self.drift.strict

    def effective_hook_fail_on_warnings(self) -> bool:
        """Resolve effective Stop hook fail_on_warnings (plugin.hooks.fail_on_warnings if set, else drift.fail_on_warnings)."""
        return (
            self.plugin.hooks.fail_on_warnings
            if self.plugin.hooks.fail_on_warnings is not None
            else self.drift.fail_on_warnings
        )


DEFAULT_CONFIG = EidosConfig()

_ALL_LEAF_KEYS: tuple[str, ...] = (
    "version",
    "workspace.exclude",
    "workspace.tests.include",
    "workspace.tests.exclude",
    "workspace.subprojects_exclude",
    "drift.base_ref",
    "drift.strict",
    "drift.warn_only",
    "drift.fail_on_warnings",
    "drift.ignore_paths",
    "drift.checks.mermaid",
    "drift.checks.import_boundaries",
    "drift.checks.public_surface",
    "drift.checks.contract_parity",
    "drift.checks.deprecated_references",
    "context.default_tier",
    "context.budgets.anchor",
    "context.budgets.guardrails",
    "context.budgets.deep",
    "context.repo_map_max_tokens",
    "context.semantic.enabled",
    "context.semantic.variant",
    "context.semantic.model_mirror_url",
    "memory.commit_drift_threshold",
    "memory.tier_caps.anchor",
    "memory.tier_caps.guardrails",
    "memory.tier_caps.deep",
    "memory.default_expiry_days",
    "lifecycle.archive.keep_recent",
    "lifecycle.archive.include_explorations",
    "plugin.targets",
    "plugin.launcher",
    "plugin.updates_check",
    "plugin.hooks.install",
    "plugin.hooks.enabled",
    "plugin.hooks.strict",
    "plugin.hooks.fail_on_warnings",
    "plugin.hooks.baseline_ttl_seconds",
    "portal.serve.host",
    "portal.serve.port",
    "portal.serve.open_browser",
    "portal.build.out_dir",
    "portal.build.single_file",
)


@functools.lru_cache(maxsize=1)
def _load_config_schema() -> dict[str, Any]:
    """Load the bundled closed Draft 2020-12 configuration schema from disk."""
    schema_path = get_config_schema_path()
    data = safe_load_yaml(schema_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"Bundled configuration schema at '{schema_path}' is not a YAML mapping.")
    return data


@functools.lru_cache(maxsize=1)
def _get_config_validator() -> jsonschema.Draft202012Validator:
    """Return a cached Draft 2020-12 jsonschema validator for eidos/eidos.yaml."""
    schema = _load_config_schema()
    return jsonschema.Draft202012Validator(
        schema,
        format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
    )


def _resolve_config_candidate_and_root(
    eidos_dir: Path | None = None,
    *,
    repo_root: Path | None = None,
) -> tuple[Path, Path]:
    """Resolve the canonical (config_candidate_path, inferred_repo_root) strictly inside <eidos_dir>."""
    if eidos_dir is not None:
        ed = Path(eidos_dir)
        resolved_ed = ed.resolve()
        inferred_root = Path(repo_root).resolve() if repo_root is not None else resolved_ed.parent
        return ed / CONFIG_FILENAME, inferred_root
    if repo_root is not None:
        root = Path(repo_root).resolve()
        return root / "eidos" / CONFIG_FILENAME, root
    cwd = Path.cwd().resolve()
    return cwd / "eidos" / CONFIG_FILENAME, cwd


def find_config_file(
    eidos_dir: Path | None = None,
    *,
    repo_root: Path | None = None,
) -> Path | None:
    """Locate the optional repository configuration file strictly at ``<eidos_dir>/eidos.yaml``."""
    candidate, _ = _resolve_config_candidate_and_root(eidos_dir=eidos_dir, repo_root=repo_root)
    if candidate.is_symlink():
        return candidate
    if candidate.is_file():
        return candidate.resolve()
    return None


def _normalize_portal_out_dir(raw_out_dir: str) -> str:
    """Normalize a validated relative portal.build.out_dir path into canonical POSIX segments."""
    norm = raw_out_dir.strip().replace("\\", "/")
    parts = [seg for seg in norm.split("/") if seg and seg != "."]
    return "/".join(parts)


def _brace_expansion_exceeds_limit(pattern: str, *, limit: int = 256) -> bool:
    """Return True if expanding '{a,b}' brace groups in ``pattern`` would exceed ``limit`` patterns."""
    if "{" not in pattern or "}" not in pattern:
        return False

    def _first_group(s: str) -> tuple[int, int, list[str]] | None:
        n = len(s)
        idx = 0
        while idx < n:
            if s[idx] == "{":
                depth = 1
                j = idx + 1
                parts: list[str] = []
                seg_start = j
                while j < n and depth > 0:
                    ch = s[j]
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            parts.append(s[seg_start:j])
                            break
                    elif ch == "," and depth == 1:
                        parts.append(s[seg_start:j])
                        seg_start = j + 1
                    j += 1
                if depth == 0 and len(parts) >= 2:
                    return idx, j, parts
            idx += 1
        return None

    count = 0
    stack: list[str] = [pattern]
    while stack:
        cur = stack.pop()
        group = _first_group(cur)
        if group is None:
            count += 1
            if count > limit:
                return True
            continue
        open_idx, close_idx, options = group
        prefix = cur[:open_idx]
        suffix = cur[close_idx + 1 :]
        for opt in options:
            stack.append(f"{prefix}{opt}{suffix}")
    return False


def _find_non_finite_floats(obj: Any, prefix: str = "") -> list[str]:
    """Return validation error messages for any non-finite float (.inf, -.inf, .nan) in parsed YAML."""
    errs: list[str] = []
    if isinstance(obj, float) and not math.isfinite(obj):
        loc = f"Property '{prefix}'" if prefix else "Root configuration"
        errs.append(f"{loc}: non-finite floating-point value ({obj}) is not allowed.")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            child = f"{prefix}.{k}" if prefix else str(k)
            errs.extend(_find_non_finite_floats(v, child))
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            child = f"{prefix}[{idx}]" if prefix else f"[{idx}]"
            errs.extend(_find_non_finite_floats(item, child))
    return errs


def _validate_mirror_url(raw_url: str, *, field_label: str = "context.semantic.model_mirror_url") -> str | None:
    """Validate an HTTP(S) mirror URL, rejecting file://, non-HTTP schemes, control chars, invalid ports, and credentials."""
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw_url):
        return f"Property '{field_label}': URL must not contain control characters or newlines."
    cleaned = raw_url.strip()
    if not cleaned:
        return f"Property '{field_label}': must be a non-empty http:// or https:// URL."
    if any(ch.isspace() for ch in cleaned):
        return f"Property '{field_label}': URL '{raw_url}' must not contain whitespace."
    if any(ch in cleaned for ch in ("?", "#", ";", "\\")):
        return (
            f"Property '{field_label}': URL '{raw_url}' must be a clean base URL "
            "without query string, fragment, path parameters, or backslashes."
        )
    try:
        parsed = urlparse(cleaned)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        return f"Property '{field_label}': malformed URL '{raw_url}' ({exc})."
    scheme = (parsed.scheme or "").lower()
    if "@" in (parsed.netloc or "") or parsed.username is not None or parsed.password is not None:
        return f"Property '{field_label}': must not embed credentials in URLs committed to eidos/eidos.yaml."
    if (parsed.netloc or "").endswith(":"):
        return f"Property '{field_label}': empty port specifier in URL '{raw_url}'."
    if scheme not in ("http", "https") or not hostname:
        return (
            f"Property '{field_label}': must use an 'https://' or 'http://' URL scheme with a valid host "
            f"(got '{raw_url}'; file:// and other schemes are forbidden)."
        )
    if port is not None and not (1 <= port <= 65535):
        return f"Property '{field_label}': invalid port '{port}' in URL '{raw_url}'."
    return None


def _validate_portal_out_dir(raw_out_dir: str, *, repo_root: Path | None = None) -> str | None:
    """Validate that portal.build.out_dir stays strictly contained within repo_root (no '.', '..', abs, or symlinks)."""
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw_out_dir):
        return "Property 'portal.build.out_dir': path must not contain control characters or null bytes."
    cleaned = raw_out_dir.strip()
    if not cleaned:
        return "Property 'portal.build.out_dir': must be a non-empty repository-relative directory path."
    norm = cleaned.replace("\\", "/")
    if Path(cleaned).is_absolute() or norm.startswith("/") or (len(norm) >= 2 and norm[1] == ":"):
        return (
            f"Property 'portal.build.out_dir': absolute path '{raw_out_dir}' is forbidden; "
            "specify a relative directory strictly inside the repository root."
        )
    raw_parts = [seg for seg in norm.split("/") if seg]
    if any(seg == ".." for seg in raw_parts):
        return (
            f"Property 'portal.build.out_dir': parent traversal '..' in '{raw_out_dir}' is forbidden; "
            "output directory must remain inside the repository root."
        )
    effective_parts = [seg for seg in raw_parts if seg != "."]
    if not effective_parts:
        return (
            f"Property 'portal.build.out_dir': '{raw_out_dir}' resolves to the repository root itself; "
            "specify a dedicated subdirectory (e.g. 'site' or 'docs/portal')."
        )
    if repo_root is not None:
        import stat as _stat

        try:
            root_res = repo_root.resolve()
            cur = root_res
            for seg in effective_parts:
                cur = cur / seg
                try:
                    st = os.lstat(cur)
                except (FileNotFoundError, NotADirectoryError):
                    continue
                if _stat.S_ISLNK(st.st_mode):
                    return (
                        f"Property 'portal.build.out_dir': '{raw_out_dir}' traverses a symbolic link ('{seg}'), "
                        "which is forbidden to prevent repository escape."
                    )
            resolved_target = cur.resolve()
        except (OSError, ValueError) as exc:
            return f"Property 'portal.build.out_dir': invalid path '{raw_out_dir}' ({exc})."
        if resolved_target == root_res or not resolved_target.is_relative_to(root_res):
            return f"Property 'portal.build.out_dir': '{raw_out_dir}' escapes the repository root '{root_res}'."
    return None


def validate_config_data(data: Any, *, repo_root: Path | None = None) -> list[str]:
    """Validate parsed eidos/eidos.yaml data against Draft 2020-12 schema and security containment rules."""
    if data is None:
        return []
    if not isinstance(data, dict):
        return ["Root configuration must be a YAML mapping (key/value object)."]

    errors: list[str] = []
    raw_version = data.get("version")
    unsupported_future_version = (
        isinstance(raw_version, (int, float))
        and not isinstance(raw_version, bool)
        and math.isfinite(raw_version)
        and raw_version > CONFIG_SCHEMA_VERSION
    )
    if unsupported_future_version:
        errors.append(
            f"Property 'version': unsupported eidos.yaml schema version {raw_version} "
            f"(this Eidos CLI supports version {CONFIG_SCHEMA_VERSION}). "
            "Upgrade Eidos ('uv tool upgrade eidos-ai' or 'pip install -U eidos-ai') to use newer configuration versions."
        )

    raw_mirror_url: str | None = None
    ctx_sec = data.get("context")
    if isinstance(ctx_sec, dict):
        sem_sec = ctx_sec.get("semantic")
        if isinstance(sem_sec, dict) and isinstance(sem_sec.get("model_mirror_url"), str):
            raw_mirror_url = sem_sec["model_mirror_url"]
    mirror_url_err = _validate_mirror_url(raw_mirror_url) if raw_mirror_url is not None else None

    validator = _get_config_validator()
    for err in sorted(validator.iter_errors(data), key=lambda e: (list(e.absolute_path), e.message)):
        path_parts = [str(p) for p in err.absolute_path]
        loc = ".".join(path_parts)
        if unsupported_future_version and loc == "version" and err.validator == "const":
            continue
        if mirror_url_err is not None and loc == "context.semantic.model_mirror_url":
            continue
        prefix = f"Property '{loc}'" if loc else "Root configuration"
        msg = err.message
        if err.validator == "additionalProperties":
            msg = (
                f"{msg}. Run 'eidos config schema' to inspect supported keys, "
                "or upgrade Eidos ('uv tool upgrade eidos-ai') if this config targets a newer release."
            )
        full_msg = f"{prefix}: {msg}"
        if full_msg not in errors:
            errors.append(full_msg)

    if mirror_url_err is not None and mirror_url_err not in errors:
        errors.append(mirror_url_err)

    portal_sec = data.get("portal")
    if isinstance(portal_sec, dict):
        build_sec = portal_sec.get("build")
        if isinstance(build_sec, dict) and isinstance(build_sec.get("out_dir"), str):
            out_err = _validate_portal_out_dir(build_sec["out_dir"], repo_root=repo_root)
            if out_err is not None and out_err not in errors:
                errors.append(out_err)

    for nf_err in _find_non_finite_floats(data):
        if nf_err not in errors:
            errors.append(nf_err)

    glob_fields: list[tuple[str, Any]] = []
    ws_sec = data.get("workspace")
    if isinstance(ws_sec, dict):
        glob_fields.append(("workspace.exclude", ws_sec.get("exclude")))
        glob_fields.append(("workspace.subprojects_exclude", ws_sec.get("subprojects_exclude")))
        ws_tests = ws_sec.get("tests")
        if isinstance(ws_tests, dict):
            glob_fields.append(("workspace.tests.include", ws_tests.get("include")))
            glob_fields.append(("workspace.tests.exclude", ws_tests.get("exclude")))
    dr_sec = data.get("drift")
    if isinstance(dr_sec, dict):
        glob_fields.append(("drift.ignore_paths", dr_sec.get("ignore_paths")))
    for field_name, raw_list in glob_fields:
        if isinstance(raw_list, list):
            for pat in raw_list:
                if isinstance(pat, str) and _brace_expansion_exceeds_limit(pat, limit=256):
                    msg = (
                        f"Property '{field_name}': glob pattern '{pat}' exceeds the maximum "
                        "brace expansion limit (256)."
                    )
                    if msg not in errors:
                        errors.append(msg)

    return errors


def validate_config_file(config_path: Path, *, repo_root: Path | None = None) -> list[str]:
    """Validate an eidos/eidos.yaml file on disk, failing closed on syntax, schema, URL, or containment violations."""
    p = Path(config_path)
    if p.is_symlink():
        if not p.exists():
            return [f"Configuration file '{p}' is a broken symbolic link."]
        return [
            f"Configuration file '{p}' is a symbolic link; "
            "eidos/eidos.yaml must be a regular file inside the repository."
        ]
    if not p.is_file():
        return [f"Configuration file '{p}' does not exist or is not a regular file."]
    try:
        raw_text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"Could not read configuration file '{p}': {exc}"]
    try:
        parsed = safe_load_yaml(raw_text)
    except YamlError as exc:
        return [f"Invalid YAML syntax: {exc}"]

    effective_root = repo_root if repo_root is not None else p.parent.resolve().parent
    return validate_config_data(parsed, repo_root=effective_root)


def _parse_env_bool(env_var: str, raw: str) -> tuple[bool | None, str | None]:
    low = raw.strip().lower()
    if low in _BOOL_TRUE:
        return True, None
    if low in _BOOL_FALSE:
        return False, None
    return (
        None,
        f"Environment variable '{env_var}' has invalid boolean value '{raw}' "
        "(expected one of: 1, true, yes, on, 0, false, no, off).",
    )


def _deep_merge_into(
    base: dict[str, Any],
    override: dict[str, Any],
    sources: dict[str, str],
    *,
    prefix: str = "",
) -> None:
    """Overlay validated YAML mapping values onto defaults dict and mark leaf provenance as 'file'."""
    for key, val in override.items():
        dotted = f"{prefix}.{key}" if prefix else key
        if isinstance(val, dict) and isinstance(base.get(key), dict):
            _deep_merge_into(base[key], val, sources, prefix=dotted)
        else:
            base[key] = val
            if dotted in sources:
                sources[dotted] = "file"


def _apply_env_overrides(
    merged: dict[str, Any],
    sources: dict[str, str],
    env: Mapping[str, str],
) -> list[str]:
    """Apply supported EIDOS_* environment variable overrides deterministically, collecting validation errors."""
    errors: list[str] = []

    def _raw(name: str) -> str | None:
        val = env.get(name)
        if val is None or not val.strip():
            return None
        return val.strip()

    raw_base_ref = _raw("EIDOS_DRIFT_BASE_REF")
    if raw_base_ref is not None:
        merged["drift"]["base_ref"] = raw_base_ref
        sources["drift.base_ref"] = "env"

    for env_name, section, key in (
        ("EIDOS_DRIFT_STRICT", "drift", "strict"),
        ("EIDOS_DRIFT_WARN_ONLY", "drift", "warn_only"),
        ("EIDOS_DRIFT_FAIL_ON_WARNINGS", "drift", "fail_on_warnings"),
    ):
        raw_val = _raw(env_name)
        if raw_val is not None:
            parsed_b, b_err = _parse_env_bool(env_name, raw_val)
            if b_err is not None:
                errors.append(b_err)
            else:
                merged[section][key] = parsed_b
                sources[f"{section}.{key}"] = "env"

    raw_tier = _raw("EIDOS_CONTEXT_TIER")
    if raw_tier is not None:
        norm_tier = raw_tier.lower()
        if norm_tier not in CONTEXT_TIERS:
            errors.append(
                f"Environment variable 'EIDOS_CONTEXT_TIER' has invalid value '{raw_tier}' "
                f"(expected one of: {', '.join(CONTEXT_TIERS)})."
            )
        else:
            merged["context"]["default_tier"] = norm_tier
            sources["context.default_tier"] = "env"

    raw_sem_en = _raw("EIDOS_SEMANTIC_ENABLED")
    if raw_sem_en is not None:
        parsed_b, b_err = _parse_env_bool("EIDOS_SEMANTIC_ENABLED", raw_sem_en)
        if b_err is not None:
            errors.append(b_err)
        else:
            merged["context"]["semantic"]["enabled"] = parsed_b
            sources["context.semantic.enabled"] = "env"

    raw_sem_var = _raw("EIDOS_SEMANTIC_VARIANT")
    if raw_sem_var is not None:
        norm_var = raw_sem_var.lower()
        if norm_var not in SEMANTIC_VARIANTS:
            errors.append(
                f"Environment variable 'EIDOS_SEMANTIC_VARIANT' has invalid value '{raw_sem_var}' "
                f"(expected one of: {', '.join(SEMANTIC_VARIANTS)})."
            )
        else:
            merged["context"]["semantic"]["variant"] = norm_var
            sources["context.semantic.variant"] = "env"

    raw_mirror = _raw("EIDOS_SEMANTIC_MIRROR_URL")
    if raw_mirror is not None:
        url_err = _validate_mirror_url(raw_mirror, field_label="EIDOS_SEMANTIC_MIRROR_URL")
        if url_err is not None:
            errors.append(url_err)
        else:
            merged["context"]["semantic"]["model_mirror_url"] = raw_mirror
            sources["context.semantic.model_mirror_url"] = "env"

    raw_hooks_en = _raw("EIDOS_HOOKS_ENABLED")
    if raw_hooks_en is not None:
        parsed_b, b_err = _parse_env_bool("EIDOS_HOOKS_ENABLED", raw_hooks_en)
        if b_err is not None:
            errors.append(b_err)
        else:
            merged["plugin"]["hooks"]["enabled"] = parsed_b
            sources["plugin.hooks.enabled"] = "env"

    no_update_val: bool | None = None
    raw_no_upd = _raw("EIDOS_NO_UPDATE_CHECK")
    if raw_no_upd is not None:
        parsed_b, b_err = _parse_env_bool("EIDOS_NO_UPDATE_CHECK", raw_no_upd)
        if b_err is not None:
            errors.append(b_err)
        else:
            no_update_val = parsed_b
            if parsed_b:
                merged["plugin"]["updates_check"] = False
                sources["plugin.updates_check"] = "env"

    raw_force_upd = _raw("EIDOS_FORCE_UPDATE_CHECK")
    if raw_force_upd is not None:
        parsed_b, b_err = _parse_env_bool("EIDOS_FORCE_UPDATE_CHECK", raw_force_upd)
        if b_err is not None:
            errors.append(b_err)
        elif parsed_b and not no_update_val:
            merged["plugin"]["updates_check"] = True
            sources["plugin.updates_check"] = "env"

    return errors


def _build_config_from_dict(merged: dict[str, Any]) -> EidosConfig:
    """Materialize an immutable EidosConfig dataclass tree from a validated merged mapping."""
    ws = merged["workspace"]
    ws_tests = ws["tests"]
    dr = merged["drift"]
    dr_checks = dr["checks"]
    ctx = merged["context"]
    ctx_budgets = ctx["budgets"]
    ctx_sem = ctx["semantic"]
    mem = merged["memory"]
    mem_caps = mem["tier_caps"]
    lc_arch = merged["lifecycle"]["archive"]
    pl = merged["plugin"]
    pl_hooks = pl["hooks"]
    pt_serve = merged["portal"]["serve"]
    pt_build = merged["portal"]["build"]

    def _pair(raw_pair: list[int] | tuple[int, int]) -> tuple[int, int]:
        return (int(raw_pair[0]), int(raw_pair[1]))

    return EidosConfig(
        version=int(merged["version"]),
        workspace=WorkspaceConfig(
            exclude=tuple(str(x).strip() for x in ws["exclude"]),
            tests=WorkspaceTestsConfig(
                include=tuple(str(x).strip() for x in ws_tests["include"]),
                exclude=tuple(str(x).strip() for x in ws_tests["exclude"]),
            ),
            subprojects_exclude=tuple(str(x).strip() for x in ws["subprojects_exclude"]),
        ),
        drift=DriftConfig(
            base_ref=str(dr["base_ref"]).strip() if isinstance(dr["base_ref"], str) else None,
            strict=bool(dr["strict"]),
            warn_only=bool(dr["warn_only"]),
            fail_on_warnings=bool(dr["fail_on_warnings"]),
            ignore_paths=tuple(str(x).strip() for x in dr["ignore_paths"]),
            checks=DriftChecksConfig(
                mermaid=str(dr_checks["mermaid"]),
                import_boundaries=str(dr_checks["import_boundaries"]),
                public_surface=str(dr_checks["public_surface"]),
                contract_parity=str(dr_checks["contract_parity"]),
                deprecated_references=str(dr_checks["deprecated_references"]),
            ),
        ),
        context=ContextConfig(
            default_tier=str(ctx["default_tier"]),
            budgets=ContextBudgetsConfig(
                anchor=int(ctx_budgets["anchor"]),
                guardrails=int(ctx_budgets["guardrails"]),
                deep=int(ctx_budgets["deep"]),
            ),
            repo_map_max_tokens=int(ctx["repo_map_max_tokens"]),
            semantic=SemanticConfig(
                enabled=bool(ctx_sem["enabled"]),
                variant=str(ctx_sem["variant"]),
                model_mirror_url=(
                    str(ctx_sem["model_mirror_url"]).strip() if isinstance(ctx_sem["model_mirror_url"], str) else None
                ),
            ),
        ),
        memory=MemoryConfig(
            commit_drift_threshold=int(mem["commit_drift_threshold"]),
            tier_caps=MemoryTierCapsConfig(
                anchor=_pair(mem_caps["anchor"]),
                guardrails=_pair(mem_caps["guardrails"]),
                deep=_pair(mem_caps["deep"]),
            ),
            default_expiry_days=int(mem["default_expiry_days"]) if mem["default_expiry_days"] is not None else None,
        ),
        lifecycle=LifecycleConfig(
            archive=LifecycleArchiveConfig(
                keep_recent=int(lc_arch["keep_recent"]),
                include_explorations=bool(lc_arch["include_explorations"]),
            ),
        ),
        plugin=PluginConfig(
            targets=tuple(str(x) for x in pl["targets"]),
            launcher=str(pl["launcher"]),
            updates_check=bool(pl["updates_check"]),
            hooks=PluginHooksConfig(
                install=bool(pl_hooks["install"]),
                enabled=bool(pl_hooks["enabled"]),
                strict=bool(pl_hooks["strict"]) if pl_hooks["strict"] is not None else None,
                fail_on_warnings=(
                    bool(pl_hooks["fail_on_warnings"]) if pl_hooks["fail_on_warnings"] is not None else None
                ),
                baseline_ttl_seconds=float(pl_hooks["baseline_ttl_seconds"]),
            ),
        ),
        portal=PortalConfig(
            serve=PortalServeConfig(
                host=str(pt_serve["host"]).strip(),
                port=int(pt_serve["port"]),
                open_browser=bool(pt_serve["open_browser"]),
            ),
            build=PortalBuildConfig(
                out_dir=_normalize_portal_out_dir(str(pt_build["out_dir"])) or "site",
                single_file=bool(pt_build["single_file"]),
            ),
        ),
    )


def parse_config_yaml_text(
    raw_text: str,
    *,
    repo_root: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[EidosConfig, list[str], dict[str, str]]:
    """Parse, validate, and resolve eidos.yaml text + EIDOS_* env overrides without raising."""
    eff_env = os.environ if env is None else env
    merged = DEFAULT_CONFIG.to_dict()
    sources: dict[str, str] = dict.fromkeys(_ALL_LEAF_KEYS, "default")
    errors: list[str] = []

    try:
        parsed = safe_load_yaml(raw_text)
    except YamlError as exc:
        errors.append(f"Invalid YAML syntax: {exc}")
        parsed = None

    if not errors:
        file_errs = validate_config_data(parsed, repo_root=repo_root)
        if file_errs:
            errors.extend(file_errs)
        elif isinstance(parsed, dict):
            _deep_merge_into(merged, parsed, sources)

    env_errs = _apply_env_overrides(merged, sources, eff_env)
    errors.extend(env_errs)
    return _build_config_from_dict(merged), errors, sources


def _resolve_repo_config_internal(
    eidos_dir: Path | None = None,
    *,
    repo_root: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[EidosConfig, Path | None, dict[str, str], list[str]]:
    """Core non-raising config resolver returning (config, config_path, sources, errors)."""
    eff_env = os.environ if env is None else env
    candidate, inferred_root = _resolve_config_candidate_and_root(eidos_dir=eidos_dir, repo_root=repo_root)
    config_path: Path | None = None
    non_regular_entry = False
    if candidate.is_symlink():
        config_path = candidate
    elif candidate.is_file():
        config_path = candidate.resolve()
    elif candidate.exists():
        config_path = candidate.resolve()
        non_regular_entry = True

    # Zero-config fast path: no file and no relevant EIDOS_* env vars set
    if config_path is None and not any(eff_env.get(k, "").strip() for k in _ENV_VAR_NAMES):
        return DEFAULT_CONFIG, None, dict.fromkeys(_ALL_LEAF_KEYS, "default"), []

    merged = DEFAULT_CONFIG.to_dict()
    sources: dict[str, str] = dict.fromkeys(_ALL_LEAF_KEYS, "default")
    errors: list[str] = []

    if config_path is not None:
        if config_path.is_symlink():
            if not config_path.exists():
                errors.append(f"Configuration file '{config_path}' is a broken symbolic link.")
            else:
                errors.append(
                    f"Configuration file '{config_path}' is a symbolic link; "
                    "eidos/eidos.yaml must be a regular file inside the repository."
                )
        elif non_regular_entry or not config_path.is_file():
            errors.append(f"Configuration file '{config_path}' does not exist or is not a regular file.")
        else:
            try:
                raw_text = config_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                errors.append(f"Could not read configuration file '{config_path}': {exc}")
            else:
                try:
                    parsed = safe_load_yaml(raw_text)
                except YamlError as exc:
                    errors.append(f"Invalid YAML syntax: {exc}")
                else:
                    file_errs = validate_config_data(parsed, repo_root=inferred_root)
                    if file_errs:
                        errors.extend(file_errs)
                    elif isinstance(parsed, dict):
                        _deep_merge_into(merged, parsed, sources)

    env_errs = _apply_env_overrides(merged, sources, eff_env)
    errors.extend(env_errs)
    return _build_config_from_dict(merged), config_path, sources, errors


def load_repo_config_with_errors(
    eidos_dir: Path | None = None,
    *,
    repo_root: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[EidosConfig, list[str]]:
    """Load optional <eidos_dir>/eidos.yaml + EIDOS_* env vars without raising, returning (config, errors)."""
    cfg, _path, _sources, errors = _resolve_repo_config_internal(
        eidos_dir=eidos_dir,
        repo_root=repo_root,
        env=env,
    )
    return cfg, errors


def load_repo_config_with_sources(
    eidos_dir: Path | None = None,
    *,
    repo_root: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[EidosConfig, Path | None, dict[str, str]]:
    """Load optional <eidos_dir>/eidos.yaml + EIDOS_* env vars with per-key provenance, raising on errors."""
    cfg, config_path, sources, errors = _resolve_repo_config_internal(
        eidos_dir=eidos_dir,
        repo_root=repo_root,
        env=env,
    )
    if errors:
        raise ConfigValidationError(errors, config_path=config_path)
    return cfg, config_path, sources


def load_repo_config(
    eidos_dir: Path | None = None,
    *,
    repo_root: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> EidosConfig:
    """Load and validate optional <eidos_dir>/eidos.yaml + EIDOS_* env overrides, failing closed on errors."""
    cfg, _config_path, _sources = load_repo_config_with_sources(
        eidos_dir=eidos_dir,
        repo_root=repo_root,
        env=env,
    )
    return cfg


def init_config_file(eidos_dir: Path, *, force: bool = False) -> Path:
    """Scaffold a commented, schema-validated starter <eidos_dir>/eidos.yaml file."""
    import contextlib
    import errno
    import tempfile

    target_dir = Path(eidos_dir).resolve()
    if not target_dir.is_dir():
        raise FileNotFoundError(f"Specifications directory '{target_dir}' does not exist. Run 'eidos init' first.")
    target_file = target_dir / CONFIG_FILENAME
    if target_file.is_dir() and not target_file.is_symlink():
        raise IsADirectoryError(
            f"Configuration path '{target_file}' is a directory; remove it before running 'eidos config init'."
        )
    if not force:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(str(target_file), flags, 0o644)
        except FileExistsError as exc:
            raise FileExistsError(
                f"Configuration file already exists at '{target_file}'. Pass --force to overwrite."
            ) from exc
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.EEXIST):
                raise FileExistsError(
                    f"Configuration file already exists at '{target_file}'. Pass --force to overwrite."
                ) from exc
            raise
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(STARTER_CONFIG_YAML)
    else:
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=target_dir,
                prefix=".eidos-config-",
                suffix=".tmp",
                delete=False,
            ) as tmp_f:
                tmp_path = Path(tmp_f.name)
                tmp_f.write(STARTER_CONFIG_YAML)
            os.replace(tmp_path, target_file)
            tmp_path = None
        finally:
            if tmp_path is not None:
                with contextlib.suppress(OSError):
                    tmp_path.unlink()
    errs = validate_config_file(target_file, repo_root=target_dir.parent)
    if errs:  # pragma: no cover - defensive self-check on bundled starter template
        raise ConfigValidationError(errs, config_path=target_file)
    return target_file


def _resolve_schema_node(node: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    """Resolve a local '#/$defs/...' reference while keeping sibling metadata (default, description)."""
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        ref_name = ref.split("/")[-1]
        base = defs.get(ref_name)
        if isinstance(base, dict):
            return {**base, **{k: v for k, v in node.items() if k != "$ref"}}
    return node


def _summarize_schema_type(node: dict[str, Any], defs: dict[str, Any]) -> str:
    resolved = _resolve_schema_node(node, defs)
    if "const" in resolved:
        return f"integer = {resolved['const']}"
    if "enum" in resolved:
        return f"enum({' | '.join(str(v) for v in resolved['enum'])})"
    if "anyOf" in resolved:
        parts = [_summarize_schema_type(branch, defs) for branch in resolved["anyOf"] if isinstance(branch, dict)]
        return " | ".join(parts)
    typ = resolved.get("type")
    if isinstance(typ, list):
        return " | ".join(str(t) for t in typ)
    if typ == "array":
        if "prefixItems" in resolved:
            return "[integer >= 0, integer >= 0]"
        items = resolved.get("items")
        if isinstance(items, dict):
            return f"array<{_summarize_schema_type(items, defs)}>"
        return "array"
    if typ == "integer":
        if resolved.get("minimum") == 1 and "maximum" not in resolved:
            return "integer (>= 1)"
        if "minimum" in resolved and "maximum" in resolved:
            return f"integer ({resolved['minimum']}..{resolved['maximum']})"
        if "minimum" in resolved:
            return f"integer (>= {resolved['minimum']})"
    if typ == "number" and "exclusiveMinimum" in resolved:
        return f"number (> {resolved['exclusiveMinimum']})"
    return str(typ or "any")


def _flatten_schema_properties(
    props: dict[str, Any],
    defs: dict[str, Any],
    env_by_key: dict[str, list[str]],
    *,
    prefix: str = "",
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, raw_node in props.items():
        if not isinstance(raw_node, dict):
            continue
        node = _resolve_schema_node(raw_node, defs)
        dotted = f"{prefix}.{key}" if prefix else key
        sub_props = node.get("properties")
        if node.get("type") == "object" and isinstance(sub_props, dict):
            rows.extend(_flatten_schema_properties(sub_props, defs, env_by_key, prefix=dotted))
            continue
        row: dict[str, Any] = {
            "key": dotted,
            "type": _summarize_schema_type(node, defs),
            "default": node.get("default"),
            "description": str(node.get("description") or "").strip(),
        }
        if "enum" in node:
            row["enum"] = list(node["enum"])
        if dotted in env_by_key:
            row["env_vars"] = list(env_by_key[dotted])
        rows.append(row)
    return rows


def get_config_schema_info() -> dict[str, Any]:
    """Return offline schema metadata, section/key constraints, defaults, and EIDOS_* env var mappings."""
    schema = _load_config_schema()
    defs = schema.get("$defs") if isinstance(schema.get("$defs"), dict) else {}
    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}

    env_by_key: dict[str, list[str]] = {}
    for item in CONFIG_ENV_VARS:
        env_by_key.setdefault(item["config_key"], []).append(item["env_var"])

    sections: list[dict[str, Any]] = []
    all_keys: list[dict[str, Any]] = []
    for sec_name, sec_node in props.items():
        if not isinstance(sec_node, dict):
            continue
        resolved = _resolve_schema_node(sec_node, defs)
        if resolved.get("type") == "object" and isinstance(resolved.get("properties"), dict):
            sec_keys = _flatten_schema_properties(resolved["properties"], defs, env_by_key, prefix=sec_name)
            sections.append(
                {
                    "name": sec_name,
                    "description": str(resolved.get("description") or "").strip(),
                    "keys": sec_keys,
                }
            )
            all_keys.extend(sec_keys)
        else:
            top_item = {
                "key": sec_name,
                "type": _summarize_schema_type(resolved, defs),
                "default": resolved.get("default"),
                "description": str(resolved.get("description") or "").strip(),
            }
            all_keys.append(top_item)

    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "filename": CONFIG_FILENAME,
        "canonical_path": f"eidos/{CONFIG_FILENAME}",
        "schema_path": "assets/schemas/config/eidos-config.v1.schema.yaml",
        "additional_properties": False,
        "precedence": ["CLI flags", "EIDOS_* env vars", "eidos/eidos.yaml", "built-in defaults"],
        "sections": sections,
        "keys": all_keys,
        "env_vars": [dict(item) for item in CONFIG_ENV_VARS],
        "starter_yaml": STARTER_CONFIG_YAML,
        "schema": schema,
    }
