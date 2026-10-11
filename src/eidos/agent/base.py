"""Base classes and protocols for pluggable coding agents."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class AgentExecutionContext:
    """Context passed to an agent runner containing only the necessary task slice."""
    workspace_dir: Path
    task_id: str
    task_prompt: str
    relevant_spec_files: List[Path]
    invariants_to_enforce: List[Dict[str, str]]
    timeout_seconds: int = 600


@dataclass
class AgentExecutionResult:
    """Outcome of an agent execution run."""
    success: bool
    diff: str
    modified_files: List[Path]
    logs: str
    error_message: Optional[str] = None


class BaseAgentAdapter(ABC):
    """Abstract interface that every agent driver (agy, claude, codex) must implement."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Driver name (e.g. 'agy', 'claude', 'codex')."""
        pass

    @abstractmethod
    def execute_task(self, context: AgentExecutionContext) -> AgentExecutionResult:
        """Dispatch task to the agent in its workspace sandbox."""
        pass

