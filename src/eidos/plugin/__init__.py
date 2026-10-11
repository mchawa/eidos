"""Eidos Agent Plugin ecosystem integration."""

from eidos.plugin.validator import validate_plugin_package, PluginValidationReport
from eidos.plugin.installer import install_plugin, PluginInstallTarget

__all__ = [
    "validate_plugin_package",
    "PluginValidationReport",
    "install_plugin",
    "PluginInstallTarget",
]

