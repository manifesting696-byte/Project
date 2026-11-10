"""Scoped plugins, service dependencies, and configuration-driven composition."""

from .contracts import Plugin, PluginConfig, PluginSpec
from .runtime import PluginContext, PluginError, Runtime

__all__ = [
    "Plugin",
    "PluginConfig",
    "PluginContext",
    "PluginError",
    "PluginSpec",
    "Runtime",
]
