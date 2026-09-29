from abc import ABC, abstractmethod
from typing import Optional

from models import FetchResult


class SourceError(Exception):
    pass


class QuotaExceeded(SourceError):
    def __init__(self, message: str, retry_after: float):
        super().__init__(message)
        self.retry_after = retry_after


class Source(ABC):
    id: str
    name: str
    quota_group: Optional[str] = None

    @abstractmethod
    def requests_per_cycle(self, keywords: list) -> int:
        ...

    @abstractmethod
    async def fetch(self, keywords: list) -> FetchResult:
        ...
