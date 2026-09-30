from abc import ABC, abstractmethod

from pydantic import BaseModel


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str = ""


class SearchProvider(ABC):
    name: str

    @abstractmethod
    def search(self, query: str, count: int = 5) -> list[SearchResult]: ...
