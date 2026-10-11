"""Eidos Agent Plugin ecosystem integration."""

from eidos.plugin.validator import validate_plugin_package, PluginValidationReport
from eidos.plugin.installer import (
    install_plugin,
    uninstall_plugin,
    PluginInstallTarget,
    InstallResult,
    UninstallResult,
)

__all__ = [
    "validate_plugin_package",
    "PluginValidationReport",
    "install_plugin",
    "uninstall_plugin",
    "PluginInstallTarget",
    "InstallResult",
    "UninstallResult",
]
