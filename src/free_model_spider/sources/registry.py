from typing import TypeVar

import httpx

from free_model_spider.sources.base import Source

T = TypeVar("T", bound=type[Source])

REGISTRY: dict[str, type[Source]] = {}


def register(cls: T) -> T:
    REGISTRY[cls.name] = cls
    return cls


def create_source(name: str, client: httpx.Client) -> Source:
    return REGISTRY[name](client)


def available_sources() -> list[str]:
    return sorted(REGISTRY)
