"""Shared HTTP dependencies: the Application singleton and static asset root."""

from functools import lru_cache
from pathlib import Path

from enterprise_agent.flow.application import Application

STATIC = Path(__file__).resolve().parent / "static"


@lru_cache(maxsize=1)
def application() -> Application:
    """One Application per process; the graph ledger and pools live inside it."""
    return Application()


def close_application() -> None:
    """Close the cached Application; tests may replace the factory with a stub."""
    factory = application
    if not hasattr(factory, "cache_info") or not factory.cache_info().currsize:
        return
    try:
        factory().close()
    finally:
        factory.cache_clear()
