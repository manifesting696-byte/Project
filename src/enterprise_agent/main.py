"""Run with uvicorn enterprise_agent.main:app or --factory enterprise_agent.main:create_app."""

from enterprise_agent.server.app import app, create_app

__all__ = ["app", "create_app"]
