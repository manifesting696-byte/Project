"""The small public contract consumed by built-in and installed plugins."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from .runtime import PluginContext


class PluginConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PluginSpec(PluginConfig):
    id: str = Field(min_length=1)
    plugin: str | None = None
    enabled: bool = True
    config: dict = Field(default_factory=dict)


@dataclass(frozen=True)
class Plugin:
    plugin_id: str
    apply: Callable[["PluginContext", BaseModel], Callable | None]
    requires: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()
    config_schema: type[BaseModel] = PluginConfig
    version: str = "1.0.0"
