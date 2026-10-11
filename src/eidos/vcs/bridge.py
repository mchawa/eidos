"""Normalized VCS interface bridging GitHub and GitLab."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class VCSIssue:
    id: str
    title: str
    body: str
    labels: List[str]
    url: str


@dataclass
class VCSPullRequest:
    id: str
    title: str
    source_branch: str
    target_branch: str
    url: str


class VCSBridge(ABC):
    """Abstract interface defining required parity across GitHub and GitLab."""

    @abstractmethod
    def fetch_issue(self, issue_id: str) -> VCSIssue:
        pass

    @abstractmethod
    def post_comment(self, item_id: str, comment: str) -> None:
        pass

    @abstractmethod
    def create_pull_request(self, title: str, body: str, source_branch: str, target_branch: str) -> VCSPullRequest:
        pass
