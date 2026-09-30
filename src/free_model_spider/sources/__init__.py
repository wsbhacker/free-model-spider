from free_model_spider.sources import openrouter  # noqa: F401  触发注册
from free_model_spider.sources.registry import available_sources, create_source, register

__all__ = ["available_sources", "create_source", "register"]
