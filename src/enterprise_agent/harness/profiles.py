"""Profiles stack named bundles, then replace rows through ordered patches."""

import os
from pathlib import Path

import yaml
from pydantic import Field

from .contracts import PluginConfig, PluginSpec


class Composition(PluginConfig):
    bundles: list[str] = Field(default_factory=list)
    plugins: list[PluginSpec] = Field(default_factory=list)


class Configuration(PluginConfig):
    default_profile: str = "default"
    bundles: dict[str, Composition] = Field(default_factory=dict)
    profiles: dict[str, Composition]


def load_profile(path, profile=None, patches=()):
    configuration = Configuration.model_validate(
        yaml.safe_load(Path(path).read_text(encoding="utf-8-sig"))
    )
    name = profile or configuration.default_profile
    if name not in configuration.profiles:
        raise ValueError(f"未知 Profile：{name}")
    rows, visiting = {}, set()

    def apply(composition):
        for bundle in composition.bundles:
            if bundle in visiting:
                raise ValueError(f"Bundle 循环依赖：{bundle}")
            if bundle not in configuration.bundles:
                raise ValueError(f"Bundle 不存在：{bundle}")
            visiting.add(bundle)
            apply(configuration.bundles[bundle])
            visiting.remove(bundle)
        ids = [row.id for row in composition.plugins]
        if len(ids) != len(set(ids)):
            raise ValueError("同一配置层的插件 ID 重复")
        for row in composition.plugins:
            previous = rows.get(row.id)
            source = row.plugin or (previous.plugin if previous else None) or row.id
            rows[row.id] = row.model_copy(update={"plugin": source})

    apply(configuration.profiles[name])
    for patch in patches:
        apply(
            Composition.model_validate(
                yaml.safe_load(Path(patch).read_text(encoding="utf-8-sig"))
            )
        )
    return name, list(rows.values())


def environment_patches():
    return tuple(filter(None, os.getenv("AGENT_PLUGIN_PATCHES", "").split(os.pathsep)))
