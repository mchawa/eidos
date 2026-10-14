"""Eidos: Git-Native, Agent-Agnostic Development AI Ecosystem."""

try:
    from eidos._version import __version__
except ImportError:
    try:
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as _get_version

        __version__ = _get_version("eidos-ai")
    except PackageNotFoundError:
        __version__ = "0.1.0"

__all__ = ["__version__"]
