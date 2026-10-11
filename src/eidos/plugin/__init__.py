"""Eidos Agent Plugin ecosystem integration."""

from eidos.plugin.installer import (
    InstallResult,
    PluginInstallTarget,
    UninstallResult,
    install_plugin,
    uninstall_plugin,
)
from eidos.plugin.validator import PluginValidationReport, validate_plugin_package

__all__ = [
    "InstallResult",
    "PluginInstallTarget",
    "PluginValidationReport",
    "UninstallResult",
    "install_plugin",
    "uninstall_plugin",
    "validate_plugin_package",
]
