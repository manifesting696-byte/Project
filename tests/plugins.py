"""Plugin discovery, reversible effects, live replacement, and application integration."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from threading import Event, Thread
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from pydantic import BaseModel

from enterprise_agent.flow.application import Application
from enterprise_agent.model.client import STRING, ModelClient, tool
from enterprise_agent.model.transport import ChatResponse, ToolCall
from enterprise_agent.plugins import (
    Plugin,
    PluginConfig,
    PluginError,
    PluginSpec,
    Runtime,
)
from enterprise_agent.plugins.profiles import load_profile
from .postgres_fixture import TestStores as Stores, identity_scope, cleanup
from .fixtures import FixtureModel


class ProviderConfig(PluginConfig):
    value: int = 1
    fail: bool = False


class Plugins(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(cleanup)

    def runtime(self, plugins=(), host=None):
        runtime = Runtime(plugins, host)
        self.addCleanup(runtime.close)
        return runtime

    def application(self, profile="default", plugins=(), model=None):
        engine = Application(
            Stores(Path(self.directory.name)),
            model or FixtureModel(),
            profile=profile,
            plugins=plugins,
        )
        self.addCleanup(engine.close)
        return engine

    def test_dependency_order_and_reverse_disposal(self):
        history = []

        def provider(ctx, config):
            history.append("provider.start")
            ctx.provide("value", object())
            ctx.defer(lambda: history.append("provider.stop"))

        def consumer(ctx, config):
            ctx.provide("consumer", ctx.require("value"))
            history.append("consumer.start")
            ctx.defer(lambda: history.append("consumer.stop"))

        runtime = self.runtime(
            [
                Plugin("provider", provider, provides=("value",)),
                Plugin(
                    "consumer", consumer, requires=("value",), provides=("consumer",)
                ),
            ]
        )
        runtime.configure([PluginSpec(id="consumer"), PluginSpec(id="provider")])
        self.assertIs(runtime.get("value"), runtime.get("consumer"))
        with self.assertRaisesRegex(PluginError, "仍被依赖"):
            runtime.unload("provider")
        runtime.unload("provider", cascade=True)
        self.assertEqual(
            history,
            ["provider.start", "consumer.start", "consumer.stop", "provider.stop"],
        )

    def test_graph_validation_runs_before_initialization(self):
        started = []
        first = Plugin(
            "first", lambda ctx, config: started.append("first"), requires=("missing",)
        )
        runtime = self.runtime([first])
        with self.assertRaisesRegex(PluginError, "缺少依赖"):
            runtime.load(PluginSpec(id="first"))
        self.assertEqual(started, [])

        cyclic = self.runtime(
            [
                Plugin("a", lambda ctx, config: None, requires=("b",), provides=("a",)),
                Plugin("b", lambda ctx, config: None, requires=("a",), provides=("b",)),
            ]
        )
        with self.assertRaisesRegex(PluginError, "循环依赖"):
            cyclic.configure([PluginSpec(id="a"), PluginSpec(id="b")])

        conflict = self.runtime(
            [
                Plugin("a", lambda ctx, config: None, provides=("same",)),
                Plugin("b", lambda ctx, config: None, provides=("same",)),
            ]
        )
        with self.assertRaisesRegex(PluginError, "冲突"):
            conflict.configure([PluginSpec(id="a"), PluginSpec(id="b")])

    def test_failed_initialization_cleans_all_scoped_effects(self):
        closed, calls, scopes = [], [], []

        def broken(ctx, config):
            scopes.append(ctx)
            ctx.provide("value", object())
            ctx.on("example", lambda value: calls.append(value))
            ctx.tool(
                "Example",
                "broken",
                lambda context, toolbox: {"echo": tool("echo", lambda: 1)},
            )
            ctx.defer(lambda: closed.append(True))
            raise ValueError("初始化失败")

        runtime = self.runtime([Plugin("broken", broken, provides=("value",))])
        with self.assertRaisesRegex(PluginError, "已恢复原配置"):
            runtime.load(PluginSpec(id="broken"))
        self.assertEqual(closed, [True])
        self.assertNotIn("value", runtime.inventory()["services"])
        self.assertEqual(runtime.events.counts(), {})
        self.assertEqual(runtime.tools.inventory(), [])
        runtime.events.emit("example", "ignored")
        self.assertEqual(calls, [])
        with self.assertRaises(PluginError):
            scopes[0].on("leak", lambda value: None)
        self.assertEqual(runtime.events.counts(), {})

    def test_provider_reload_rebuilds_consumers_and_rolls_back_failure(self):
        history = []

        def provider(ctx, config):
            ctx.provide("value", {"number": config.value})
            ctx.defer(lambda: history.append(("dispose", config.value)))
            if config.fail:
                raise ValueError("拒绝新实现")

        def consumer(ctx, config):
            ctx.provide("consumer", ctx.require("value"))

        runtime = self.runtime(
            [
                Plugin(
                    "provider",
                    provider,
                    provides=("value",),
                    config_schema=ProviderConfig,
                ),
                Plugin(
                    "consumer", consumer, requires=("value",), provides=("consumer",)
                ),
            ]
        )
        runtime.configure([PluginSpec(id="consumer"), PluginSpec(id="provider")])
        original = runtime.get("consumer")
        runtime.reload("provider", {"value": 2})
        self.assertIsNot(original, runtime.get("consumer"))
        self.assertEqual(runtime.get("consumer"), {"number": 2})
        with self.assertRaisesRegex(PluginError, "已恢复原配置"):
            runtime.reload("provider", {"value": 3, "fail": True})
        self.assertEqual(runtime.get("consumer"), {"number": 2})
        self.assertIs(runtime.get("consumer"), runtime.get("value"))
        self.assertTrue(runtime.inventory()["healthy"])
        self.assertIn(("dispose", 3), history)

    def test_bad_config_keeps_the_existing_instance(self):
        def apply(ctx, config):
            ctx.provide("value", object())

        runtime = self.runtime(
            [
                Plugin(
                    "provider", apply, provides=("value",), config_schema=ProviderConfig
                )
            ]
        )
        runtime.load(PluginSpec(id="provider"))
        original = runtime.get("value")
        with self.assertRaises(ValueError):
            runtime.reload("provider", {"value": "bad"})
        self.assertIs(runtime.get("value"), original)

    def test_live_operation_rejects_plugin_changes(self):
        runtime = self.runtime([Plugin("empty", lambda ctx, config: None)])
        entered, release = Event(), Event()

        def run():
            with runtime.operation():
                entered.set()
                release.wait(timeout=10)

        worker = Thread(target=run)
        worker.start()
        try:
            self.assertTrue(entered.wait(timeout=10))
            with self.assertRaisesRegex(PluginError, "正在执行"):
                runtime.load(PluginSpec(id="empty"))
            with self.assertRaisesRegex(PluginError, "正在执行"):
                runtime.close()
        finally:
            release.set()
            worker.join(timeout=10)
        runtime.load(PluginSpec(id="empty"))

    def test_ordered_hooks_can_block_execution_and_are_removed_on_unload(self):
        history = []

        def apply(ctx, config):
            ctx.on(
                "tool/before_execute", lambda value: history.append("late"), priority=20
            )
            ctx.on(
                "tool/before_execute", lambda value: history.append("early"), priority=0
            )

        runtime = self.runtime([Plugin("policy", apply)])
        runtime.load(PluginSpec(id="policy"))
        tools = {"echo": tool("echo", lambda value: value, value=STRING)}
        self.assertEqual(
            runtime.tools.execute("Example", "echo", {"value": "ok"}, tools), "ok"
        )
        self.assertEqual(history, ["early", "late"])
        runtime.unload("policy")
        self.assertEqual(runtime.events.counts(), {})
        runtime.tools.execute("Example", "echo", {"value": "ok"}, tools)
        self.assertEqual(history, ["early", "late"])
        with self.assertRaisesRegex(ValueError, "未授权"):
            runtime.tools.execute("Example", "unknown", {}, tools)

    def test_discovery_from_module_and_installed_entry_point(self):
        plugin = Plugin("external", lambda ctx, config: None)
        module = ModuleType("example_agent_plugin")
        module.PLUGIN = plugin
        runtime = self.runtime()
        entry = SimpleNamespace(name="installed-example", load=lambda: plugin)
        with (
            patch.dict(sys.modules, {module.__name__: module}),
            patch(
                "enterprise_agent.plugins.runtime.metadata.entry_points",
                return_value=[entry],
            ),
        ):
            runtime.load(PluginSpec(id="module", plugin="example_agent_plugin:PLUGIN"))
            self.assertEqual(runtime.discover(), ["installed-example"])
            runtime.load(PluginSpec(id="installed", plugin="installed-example"))
        self.assertEqual(len(runtime.inventory()["plugins"]), 2)

    def test_unload_removes_tools_and_closes_resources(self):
        closed = []

        def apply(ctx, config):
            ctx.tool(
                "Example",
                "extra",
                lambda value, toolbox: {"echo": tool("echo", lambda: 1)},
            )
            ctx.defer(lambda: closed.append(True))

        runtime = self.runtime([Plugin("extra", apply)])
        runtime.load(PluginSpec(id="extra"))
        self.assertIn("echo", runtime.tools.build("Example", {}))
        runtime.unload("extra")
        self.assertEqual(runtime.tools.build("Example", {}), {})
        self.assertEqual(closed, [True])
        runtime.close()
        runtime.close()
        with self.assertRaises(PluginError):
            runtime.get("tools")

    def test_profiles_stack_bundles_and_replace_whole_config(self):
        path = Path(self.directory.name) / "harness.yaml"
        patch_path = Path(self.directory.name) / "patch.yaml"
        path.write_text(
            """default_profile: example
bundles:
  base:
    plugins:
      - id: provider
        plugin: example:PROVIDER
        config: {value: 1, fail: false}
  extension:
    bundles: [base]
    plugins:
      - id: extra
profiles:
  example:
    bundles: [extension]
    plugins:
      - id: provider
        config: {value: 2}
""",
            encoding="utf-8",
        )
        patch_path.write_text(
            "plugins:\n  - id: extra\n    enabled: false\n", encoding="utf-8"
        )
        name, specs = load_profile(path, patches=[patch_path])
        self.assertEqual(name, "example")
        self.assertEqual(specs[0].plugin, "example:PROVIDER")
        self.assertEqual(specs[0].config, {"value": 2})
        self.assertFalse(specs[1].enabled)
        with self.assertRaisesRegex(ValueError, "未知 Profile"):
            load_profile(path, "missing")
        path.write_text(
            """bundles:
  loop:
    bundles: [loop]
profiles:
  default:
    bundles: [loop]
""",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "循环依赖"):
            load_profile(path)

    def test_data_profile_and_presentation_hot_unload_keep_queries_working(self):
        engine = self.application()
        engine.runtime.unload("agent.presentation")
        self.assertNotIn("PresentationAgent", engine.context.capabilities)
        result = engine.ask(1, "列出前五个产品")
        self.assertEqual(result["status"], "completed", result["blocks"])
        engine.runtime.load(PluginSpec(id="agent.presentation"))
        self.assertIn("PresentationAgent", engine.context.capabilities)
        inventory = engine.reload_profile("data")
        self.assertEqual(inventory["profile"], "data")
        self.assertNotIn("PresentationAgent", engine.context.capabilities)
        self.assertEqual(engine.ask(1, "列出前五个产品")["status"], "completed")

    def test_tool_provider_cannot_unload_while_agents_depend_on_it(self):
        engine = self.application()
        with self.assertRaisesRegex(PluginError, "仍被依赖"):
            engine.runtime.unload("tools.enterprise")
        self.assertIn("QueryAgent", engine.specialists.agents)

    def test_close_attempts_every_cleanup_even_when_one_fails(self):
        closed = []

        def apply(ctx, config):
            ctx.on("example", lambda value: None)
            ctx.defer(lambda: closed.append(True))

            def fail():
                raise RuntimeError("关闭失败")

            ctx.defer(fail)

        runtime = self.runtime([Plugin("cleanup", apply)])
        runtime.load(PluginSpec(id="cleanup"))
        with self.assertRaises(ExceptionGroup):
            runtime.close()
        self.assertEqual(closed, [True])
        self.assertEqual(runtime.events.counts(), {})
        self.assertTrue(runtime.inventory()["closed"])

    def test_minimal_profile_answers_without_specialist_roles(self):
        engine = self.application("minimal")
        self.assertEqual(engine.specialists.agents, {})
        result = engine.ask(1, "什么是平均值？")
        self.assertEqual(result["status"], "completed", result["blocks"])
        self.assertFalse(result["tasks"])

    def test_profile_reload_keeps_paused_request_recoverable(self):
        engine = self.application()
        preview = engine.ask(1, "统计产品并生成图表和报告", preview=True)
        request = preview["requests"][0]
        stores = engine.stores
        engine.runtime.reload("agent.loop")
        self.assertIs(engine.stores, stores)
        result = engine.resume(1, request["request_id"])
        self.assertEqual(result["status"], "completed", result["blocks"])
        self.assertEqual(
            result["requests"][0]["current_revision_id"], request["current_revision_id"]
        )

    def test_custom_agent_registration_reaches_planning_and_execution(self):
        class CustomAgent:
            def execute(self, context, allowed, emit):
                return {
                    "status": "completed",
                    "summary": "custom",
                    "produced_outputs": {},
                }

        def apply(ctx, config):
            ctx.agent("CustomAgent", CustomAgent(), "自定义检查")

        engine = self.application(
            plugins=[Plugin("custom", apply, requires=("specialists",))]
        )
        engine.runtime.load(PluginSpec(id="custom"))
        self.assertIn("CustomAgent", engine.context.capabilities)
        self.assertEqual(
            engine.specialists.execute(
                {"task": {"assigned_agent": "CustomAgent", "task_id": "custom"}},
                set(),
                None,
            )["summary"],
            "custom",
        )
        engine.runtime.unload("custom")
        self.assertNotIn("CustomAgent", engine.context.capabilities)

    def test_sql_policy_is_live_and_unloads_cleanly(self):
        def deny(value):
            raise ValueError("SQL 已被插件拒绝")

        def apply(ctx, config):
            ctx.on("sql/before_execute", deny)

        engine = self.application(plugins=[Plugin("sql.policy", apply)])
        binding = {"status": "bound", "tables": ["Products"], "relations": []}
        engine.runtime.load(PluginSpec(id="sql.policy"))
        with self.assertRaisesRegex(ValueError, "插件拒绝"):
            engine.query_gateway.execute(
                "SELECT product_id FROM Products LIMIT 1", binding
            )
        engine.runtime.unload("sql.policy")
        _, rows, _ = engine.query_gateway.execute(
            "SELECT product_id FROM Products LIMIT 1", binding
        )
        self.assertEqual(len(rows), 1)

    def test_model_loop_executes_plugin_tools_and_honors_policies(self):
        class Answer(BaseModel):
            value: str

        class Transport:
            def complete(self, messages, tools):
                if messages[-1]["role"] != "tool":
                    return ChatResponse(
                        None, (ToolCall("call", "echo", '{"value":"hello"}'),)
                    )
                return ChatResponse(
                    json.dumps({"value": json.loads(messages[-1]["content"])["value"]})
                )

        def apply(ctx, config):
            ctx.tool(
                "Example",
                "extra",
                lambda value, toolbox: {
                    "echo": tool("echo", lambda value: {"value": value}, value=STRING),
                },
            )

        runtime = self.runtime([Plugin("extra", apply)])
        runtime.load(PluginSpec(id="extra"))
        calls = []
        dispose = runtime.events.on(
            "tool/before_execute", lambda value: calls.append(value["name"])
        )
        model = ModelClient(Transport(), tool_executor=runtime.tools)
        response = model.run(
            "Example", "echo", {}, Answer, runtime.tools.build("Example", {})
        )
        self.assertEqual(response.value, "hello")
        self.assertEqual(calls, ["echo"])
        dispose()
        runtime.unload("extra")
        self.assertEqual(runtime.tools.build("Example", {}), {})

    def test_admin_http_management_and_analyst_denial(self):
        from fastapi.testclient import TestClient
        from enterprise_agent.server import app as adapter
        from enterprise_agent.server import deps, identity

        engine = self.application()
        with (
            identity_scope(self.directory.name),
            patch.object(deps, "application", lambda: engine),
        ):
            identity.create_user("plugin-admin", "plugin-admin-password", "admin")
            identity.create_user("plugin-analyst", "plugin-analyst-password", "analyst")
            with TestClient(adapter.app) as client:
                tokens = {}
                for role in ("admin", "analyst"):
                    login = client.post(
                        "/auth/login",
                        json={
                            "username": f"plugin-{role}",
                            "password": f"plugin-{role}-password",
                        },
                    )
                    tokens[role] = {
                        "Authorization": "Bearer " + login.json()["access_token"]
                    }
                self.assertEqual(
                    client.get(
                        "/runtime/plugins", headers=tokens["analyst"]
                    ).status_code,
                    403,
                )
                inventory = client.get("/runtime/plugins", headers=tokens["admin"])
                self.assertEqual(inventory.status_code, 200)
                unloaded = client.post(
                    "/runtime/plugins/agent.presentation/unload",
                    json={},
                    headers=tokens["admin"],
                )
                self.assertEqual(unloaded.status_code, 200, unloaded.text)
                self.assertNotIn("PresentationAgent", engine.specialists.agents)
                loaded = client.post(
                    "/runtime/plugins/load",
                    json={"id": "agent.presentation"},
                    headers=tokens["admin"],
                )
                self.assertEqual(loaded.status_code, 200, loaded.text)
                invalid = client.post(
                    "/runtime/plugins/ledger.postgres/reload",
                    json={"config": {"bad": True}},
                    headers=tokens["admin"],
                )
                self.assertEqual(invalid.status_code, 400, invalid.text)
                restored = client.post(
                    "/runtime/profile/reload",
                    json={"profile": "data"},
                    headers=tokens["admin"],
                )
                self.assertEqual(restored.status_code, 200, restored.text)
