"""Process entry point.

    uvicorn enterprise_agent.main:app --host 0.0.0.0 --port 8000 --workers 1

One worker on purpose: a turn holds the per-session advisory lock and the graph
scenes are checkpointed per thread, so parallel workers would only add contention.
"""

from enterprise_agent.server.app import app

__all__ = ["app"]
