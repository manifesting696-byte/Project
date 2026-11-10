"""Dependency-ordered plugins with scoped effects and rollback on replacement."""

from collections.abc import Mapping
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from importlib import import_module, metadata
from threading import RLock

from .contracts import Plugin, PluginSpec
from .events import Events
from .tools import Tools


class PluginError(ValueError):
    pass


class PluginContext:
    def __init__(self, runtime, instance_id, plugin):
        self.runtime, self.id, self.plugin = runtime, instance_id, plugin
        self._effects, self.services = [], set()
        self._disposed = False

    def require(self, name):
        if name not in self.plugin.requires and name not in self.plugin.provides:
            raise PluginError(f"{self.id} 未声明服务依赖：{name}")
        return self.runtime.get(name)

    def provide(self, name, value):
        if self._disposed or name not in self.plugin.provides:
            raise PluginError(f"{self.id} 未声明提供服务：{name}")
        if name in self.runtime._services:
            raise PluginError(f"服务重复注册：{name}")
        self.runtime._services[name] = value
        self.services.add(name)
        self.defer(lambda: self.runtime._services.pop(name, None))
        return value

    def defer(self, cleanup):
        if self._disposed or not callable(cleanup):
            raise PluginError("插件作用域已释放或清理函数无效")
        self._effects.append(cleanup)
        return cleanup

    def on(self, event, handler, priority=0):
        if self._disposed:
            raise PluginError("插件作用域已释放")
        return self.defer(self.runtime.events.on(event, handler, priority))

    def tool(self, role, name, factory):
        if self._disposed:
            raise PluginError("插件作用域已释放")
        return self.defer(self.runtime.tools.register(role, name, factory))

    def agent(self, name, implementation, description):
        if self._disposed:
            raise PluginError("插件作用域已释放")
        registry = self.require("specialists")
        return self.defer(registry.register(name, implementation, description))

    def dispose(self):
        self._disposed = True
        errors = []
        for cleanup in reversed(self._effects):
            try:
                cleanup()
            except Exception as exc:
                errors.append(exc)
        self._effects.clear()
        if errors:
            raise ExceptionGroup(f"{self.id} 资源清理失败", errors)


@dataclass
class Loaded:
    spec: PluginSpec
    plugin: Plugin
    config: object
    dependencies: set[str]
    context: PluginContext | None = None


class Service:
    """Existing application attributes always resolve the current provider."""

    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        return instance.runtime.get(self.name)


class Runtime:
    def __init__(self, catalog=(), host=None):
        self._lock = RLock()
        self.events = Events()
        self.tools = Tools(self.events)
        self._services = {**(host or {}), "events": self.events, "tools": self.tools}
        self._host = set(self._services)
        self._catalog = {}
        for plugin in catalog:
            self.register(plugin)
        self._active = {}
        self._order = []
        self._operations = 0
        self._changing = False
        self._closed = False
        self._broken = False

    def register(self, plugin):
        if not isinstance(plugin, Plugin) or not plugin.plugin_id:
            raise PluginError("插件导出必须是有效的 Plugin")
        with self._lock:
            if plugin.plugin_id in self._catalog:
                raise PluginError(f"插件定义重复：{plugin.plugin_id}")
            self._catalog[plugin.plugin_id] = plugin

    def discover(self):
        entries = metadata.entry_points(group="enterprise_agent.plugins")
        return sorted(entry.name for entry in entries)

    def _definition(self, source):
        if source in self._catalog:
            return self._catalog[source]
        try:
            if ":" in source:
                module, export = source.split(":", 1)
                plugin = getattr(import_module(module), export)
            else:
                entries = [
                    entry
                    for entry in metadata.entry_points(group="enterprise_agent.plugins")
                    if entry.name == source
                ]
                if len(entries) != 1:
                    raise PluginError(f"插件不存在或发现名称冲突：{source}")
                plugin = entries[0].load()
        except (ImportError, AttributeError) as exc:
            raise PluginError(f"插件导入失败：{source}") from exc
        if not isinstance(plugin, Plugin):
            raise PluginError(f"插件导出类型错误：{source}")
        return plugin

    def get(self, name):
        with self._lock:
            if self._closed or self._broken:
                raise PluginError("插件运行时已关闭或恢复失败")
            if name not in self._services:
                raise PluginError(f"服务尚未启用：{name}")
            return self._services[name]

    @contextmanager
    def operation(self):
        with self._lock:
            if self._closed or self._broken or self._changing:
                raise PluginError("插件运行时当前不可执行")
            self._operations += 1
        try:
            yield
        finally:
            with self._lock:
                self._operations -= 1

    @contextmanager
    def maintenance(self):
        with self._lock:
            if self._closed or self._broken or self._changing or self._operations:
                raise PluginError("有工作正在执行或运行时不可用，请稍后调整目录")
            yield

    def _resolve(self, specs):
        resolved, providers = {}, {name: None for name in self._host}
        for spec in specs:
            if not spec.enabled:
                continue
            if spec.id in resolved:
                raise PluginError(f"插件实例 ID 重复：{spec.id}")
            plugin = self._definition(spec.plugin or spec.id)
            config = plugin.config_schema.model_validate(deepcopy(spec.config))
            for service in plugin.provides:
                if service in providers:
                    raise PluginError(f"服务提供者冲突：{service}")
                providers[service] = spec.id
            resolved[spec.id] = Loaded(
                spec.model_copy(deep=True), plugin, config, set()
            )
        for key, loaded in resolved.items():
            for service in loaded.plugin.requires:
                if service not in providers:
                    raise PluginError(f"{key} 缺少依赖服务：{service}")
                provider = providers[service]
                if provider is not None:
                    loaded.dependencies.add(provider)
        order, visiting = [], set()

        def visit(key):
            if key in visiting:
                raise PluginError(f"插件循环依赖：{key}")
            if key in order:
                return
            visiting.add(key)
            for dependency in sorted(resolved[key].dependencies):
                visit(dependency)
            visiting.remove(key)
            order.append(key)

        for key in resolved:
            visit(key)
        return resolved, order

    def _start(self, key, loaded):
        context = PluginContext(self, key, loaded.plugin)
        loaded.context = context
        try:
            cleanup = loaded.plugin.apply(context, loaded.config)
            if cleanup is not None:
                context.defer(cleanup)
            if context.services != set(loaded.plugin.provides):
                raise PluginError(f"{key} 没有注册全部已声明服务")
        except BaseException:
            context.dispose()
            raise
        self._active[key] = loaded
        self.events.notify(
            "plugin/loaded", {"id": key, "version": loaded.plugin.version}
        )

    def _stop(self, key):
        loaded = self._active.pop(key)
        try:
            loaded.context.dispose()
        finally:
            self.events.notify("plugin/unloaded", {"id": key})

    def configure(self, specs, force=()):
        with self._lock:
            if self._closed or self._broken or self._changing:
                raise PluginError("插件运行时当前不可修改")
            if self._operations:
                raise PluginError("有工作正在执行，请在执行结束后调整插件")
            desired, order = self._resolve(specs)
            old, old_order = dict(self._active), list(self._order)
            changed = set(force) | (old.keys() ^ desired.keys())
            changed.update(
                key
                for key in old.keys() & desired.keys()
                if old[key].spec != desired[key].spec
                or old[key].plugin != desired[key].plugin
            )
            while True:
                consumers = {
                    key
                    for key, item in [*old.items(), *desired.items()]
                    if item.dependencies & changed
                }
                if consumers <= changed:
                    break
                changed.update(consumers)
            self._changing = True
            started = []
            try:
                for key in reversed(old_order):
                    if key in changed:
                        self._stop(key)
                for key in order:
                    if key in changed:
                        self._start(key, desired[key])
                        started.append(key)
                self._order = order
            except BaseException as exc:
                recovery_errors = []
                for key in reversed(started):
                    try:
                        self._stop(key)
                    except Exception as cleanup_error:
                        recovery_errors.append(cleanup_error)
                for key in old_order:
                    if key not in self._active:
                        try:
                            self._start(key, old[key])
                        except Exception as recovery_error:
                            recovery_errors.append(recovery_error)
                self._order = old_order
                self._broken = bool(recovery_errors)
                if recovery_errors:
                    raise PluginError(
                        "插件切换失败，原配置恢复也失败，需要重启应用"
                    ) from ExceptionGroup("恢复错误", recovery_errors)
                if isinstance(exc, Exception):
                    raise PluginError(f"插件切换失败，已恢复原配置：{exc}") from exc
                raise
            finally:
                self._changing = False
        return self.inventory()

    def load(self, spec):
        spec = PluginSpec.model_validate(spec) if isinstance(spec, Mapping) else spec
        with self._lock:
            specs = {key: item.spec for key, item in self._active.items()}
            if spec.id in specs:
                raise PluginError(f"插件已加载，请使用 reload：{spec.id}")
            specs[spec.id] = spec
            return self.configure(list(specs.values()))

    def unload(self, instance_id, cascade=False):
        with self._lock:
            if instance_id not in self._active:
                raise PluginError(f"插件尚未加载：{instance_id}")
            removed = {instance_id}
            while True:
                consumers = {
                    key
                    for key, item in self._active.items()
                    if item.dependencies & removed
                }
                if consumers <= removed:
                    break
                removed.update(consumers)
            if len(removed) > 1 and not cascade:
                raise PluginError(f"插件仍被依赖：{sorted(removed - {instance_id})}")
            return self.configure(
                [item.spec for key, item in self._active.items() if key not in removed]
            )

    def reload(self, instance_id, config=None):
        with self._lock:
            if instance_id not in self._active:
                raise PluginError(f"插件尚未加载：{instance_id}")
            specs = []
            for key, item in self._active.items():
                spec = item.spec.model_copy(deep=True)
                if key == instance_id and config is not None:
                    spec.config = deepcopy(config)
                specs.append(spec)
            return self.configure(specs, force={instance_id})

    def inventory(self):
        with self._lock:
            return {
                "plugins": [
                    {
                        "id": key,
                        "plugin": item.plugin.plugin_id,
                        "version": item.plugin.version,
                        "requires": list(item.plugin.requires),
                        "provides": list(item.plugin.provides),
                        "dependencies": sorted(item.dependencies),
                        "status": "loaded",
                    }
                    for key in self._order
                    if (item := self._active.get(key))
                ],
                "services": sorted(self._services),
                "listeners": self.events.counts(),
                "tool_groups": self.tools.inventory(),
                "discovered": self.discover(),
                "active_operations": self._operations,
                "closed": self._closed,
                "healthy": not self._broken,
            }

    def close(self):
        with self._lock:
            if self._closed:
                return
            if self._operations or self._changing:
                raise PluginError("有工作正在执行，无法关闭插件运行时")
            self._changing = True
            errors = []
            try:
                for key in reversed(self._order):
                    if key not in self._active:
                        continue
                    try:
                        self._stop(key)
                    except Exception as exc:
                        errors.append(exc)
            finally:
                self._order.clear()
                self._closed = True
                self._changing = False
            if errors:
                raise ExceptionGroup("插件关闭失败", errors)
