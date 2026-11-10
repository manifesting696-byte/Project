"""Role-scoped tool contributions and the trusted tool execution pipeline."""

from copy import deepcopy


class Tools:
    def __init__(self, events):
        self.events = events
        self._groups = {}

    def register(self, role, name, factory):
        key = (role, name)
        if key in self._groups:
            raise ValueError(f"工具组重复注册：{role}/{name}")
        self._groups[key] = factory

        def dispose():
            self._groups.pop(key, None)

        return dispose

    def build(self, role, context, toolbox=None, base=None):
        tools = dict(base or {})
        for (target, group), factory in tuple(self._groups.items()):
            if target != role:
                continue
            additions = factory(context, toolbox)
            duplicates = tools.keys() & additions.keys()
            if duplicates:
                raise ValueError(f"工具名冲突：{role}/{group} {sorted(duplicates)}")
            tools.update(additions)
        return tools

    def execute(self, role, name, arguments, tools, scope=None):
        if name not in tools:
            raise ValueError("工具未授权")
        if not isinstance(arguments, dict):
            raise ValueError("工具参数必须是对象")
        call = {
            "role": role,
            "name": name,
            "arguments": deepcopy(arguments),
            "scope": dict(scope or {}),
        }
        try:
            # Hooks inspect a copy. Authorization and executed arguments stay authoritative.
            self.events.emit("tool/before_execute", deepcopy(call))
            result = tools[name].call(**arguments)
            self.events.emit(
                "tool/after_execute", {**deepcopy(call), "result": deepcopy(result)}
            )
            return result
        except Exception as exc:
            self.events.notify("tool/error", {**call, "error_type": type(exc).__name__})
            if isinstance(exc, (ValueError, TypeError, KeyError)):
                raise
            raise ValueError(f"工具执行失败：{exc}") from exc

    def inventory(self):
        return [{"role": role, "group": name} for role, name in self._groups]
