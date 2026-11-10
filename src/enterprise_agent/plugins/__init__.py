"""Scoped plugins, service dependencies, and configuration-driven composition."""

from enterprise_agent.plugins.contract import Plugin, PluginConfig, PluginSpec
from enterprise_agent.plugins.runtime import PluginContext, PluginError, Runtime

__all__ = [
    "Plugin",
    "PluginConfig",
    "PluginContext",
    "PluginError",
    "PluginSpec",
    "Runtime",
]
