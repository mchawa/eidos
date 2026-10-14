"""Shared CLI primitives: the Rich console and help-panel names used by every command module (CMP-CLI-APP)."""

from rich.console import Console

PANEL_SETUP = "Setup & Workspace"
PANEL_GOVERNANCE = "Governance & Verification"
PANEL_SPECS = "Living Catalog & Mutations"
PANEL_KNOWLEDGE = "Knowledge Graph & AI Context"
PANEL_PORTAL = "Living Web Portal"
PANEL_PLUGINS = "Agent Ecosystem & Plugins"

console = Console()
