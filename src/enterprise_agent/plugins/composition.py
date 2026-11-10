"""Enterprise modules mounted as ordinary, replaceable plugins."""

import logging
from pathlib import Path

from pydantic import Field

from enterprise_agent.catalog.registry import Registry
from enterprise_agent.flow.checkpoints import Checkpoints
from enterprise_agent.flow.request_runs import Execution
from enterprise_agent.flow.turn_runs import Turns
from enterprise_agent.ledger.artifacts import Artifacts
from enterprise_agent.ledger.ledger import Stores
from enterprise_agent.ledger.summaries import compress
from enterprise_agent.model.client import ModelClient
from enterprise_agent.model.context import ContextManager
from enterprise_agent.model.transport import OpenAICompatibleTransport
from enterprise_agent.plugins.contract import Plugin, PluginConfig
from enterprise_agent.query.database import ReadonlyDatabase
from enterprise_agent.query.gateway import QueryGateway
from enterprise_agent.settings import WORK
from enterprise_agent.stages.answer.materialize import Responses
from enterprise_agent.stages.execute.agents.analysis import AnalysisAgent
from enterprise_agent.stages.execute.agents.capabilities import CAPABILITIES
from enterprise_agent.stages.execute.agents.dispatch import Specialists
from enterprise_agent.stages.execute.agents.presentation import PresentationAgent
from enterprise_agent.stages.execute.agents.query import QueryAgent
from enterprise_agent.stages.execute.agents.semantic import SemanticAgent
from enterprise_agent.stages.execute.agents.supervisor import Supervisor
from enterprise_agent.stages.execute.scheduler import Scheduler
from enterprise_agent.stages.execute.tools.semantic import semantic_tools
from enterprise_agent.stages.execute.tools.supervisor import supervisor_tools
from enterprise_agent.stages.prepare.plan import Tasks
from enterprise_agent.stages.prepare.preparation import Preparation
from enterprise_agent.stages.prepare.requests import Requests


class LedgerConfig(PluginConfig):
    directory: str | None = None


class DatabaseConfig(PluginConfig):
    dsn: str | None = None
    schema_name: str = Field(default="analysis", alias="schema")
    source_id: str | None = Field(default=None, min_length=1)


class LoggingConfig(PluginConfig):
    events: list[str] = Field(
        default_factory=lambda: ["plugin/loaded", "plugin/unloaded", "tool/error"]
    )


def ledger(ctx, config, stores=None):
    owned = stores is None
    stores = (
        stores
        if stores is not None
        else Stores(Path(config.directory) if config.directory else WORK)
    )
    ctx.provide("stores", stores)
    if owned:
        ctx.defer(stores.close)
    ctx.provide("artifacts", Artifacts(stores))
    ctx.provide("requests", Requests(stores))


def roles(ctx, config):
    ctx.provide("specialists", Specialists(events=ctx.runtime.events))


def semantic_registry(ctx, config):
    ctx.provide(
        "registry",
        Registry(
            ctx.require("database"),
            ctx.require("stores").directory,
            persistence=ctx.require("stores").persistence,
        ),
    )


def database(ctx, config):
    provider = ctx.provide(
        "database", ReadonlyDatabase(config.dsn, config.schema_name, config.source_id)
    )
    ctx.defer(provider.close)


def transport(ctx, config):
    ctx.provide("transport", OpenAICompatibleTransport())


def query_gateway(ctx, config):
    ctx.provide(
        "query_gateway",
        QueryGateway(
            ctx.require("registry"),
            events=ctx.runtime.events,
            database=ctx.require("database"),
        ),
    )


def tasks(ctx, config):
    ctx.provide(
        "tasks",
        Tasks(
            ctx.require("stores"), ctx.require("requests"), ctx.require("specialists")
        ),
    )


def model_loop(ctx, config):
    ctx.provide(
        "model", ModelClient(ctx.require("transport"), tool_executor=ctx.runtime.tools)
    )


def context(ctx, config):
    ctx.provide(
        "context",
        ContextManager(
            ctx.require("stores"),
            ctx.require("artifacts"),
            ctx.require("requests"),
            ctx.require("tasks"),
            ctx.require("registry"),
            ctx.require("specialists").capabilities,
        ),
    )


def supervisor(ctx, config):
    ctx.provide(
        "supervisor",
        Supervisor(
            ctx.require("model"),
            ctx.require("context"),
            ctx.require("stores"),
            ctx.require("artifacts"),
            tool_registry=ctx.require("enterprise_tools"),
        ),
    )


def enterprise_tools(ctx, config):
    registry, views = ctx.require("registry"), ctx.require("context")
    stores, artifacts = ctx.require("stores"), ctx.require("artifacts")
    ctx.tool(
        "Supervisor",
        "enterprise",
        lambda value, _: supervisor_tools(
            value["session_id"],
            views,
            stores,
            artifacts,
            value.get("drafts"),
            value.get("confirmed_refs"),
        ),
    )
    ctx.tool("SemanticAgent", "enterprise", lambda value, _: semantic_tools(registry))
    ctx.tool(
        "QueryAgent", "enterprise", lambda value, toolbox: toolbox.query_tools(value)
    )
    ctx.tool(
        "AnalysisAgent", "enterprise", lambda value, toolbox: toolbox.analysis_tools()
    )
    ctx.tool(
        "PresentationAgent",
        "enterprise",
        lambda value, toolbox: toolbox.presentation_tools(),
    )
    ctx.provide("enterprise_tools", ctx.runtime.tools)


def preparation(ctx, config):
    ctx.provide(
        "preparation",
        Preparation(
            ctx.require("stores"),
            ctx.require("requests"),
            ctx.require("tasks"),
            ctx.require("supervisor"),
        ),
    )


def responses(ctx, config):
    ctx.provide(
        "responses",
        Responses(
            ctx.require("stores"),
            ctx.require("artifacts"),
            ctx.require("requests"),
            ctx.require("tasks"),
        ),
    )


def workflow(ctx, config):
    app, stores = ctx.require("application"), ctx.require("stores")
    checkpoints = ctx.provide("checkpoints", Checkpoints(stores))
    ctx.defer(checkpoints.close)
    ctx.provide(
        "scheduler",
        Scheduler(stores, ctx.require("context"), ctx.require("specialists")),
    )
    ctx.provide("execution", Execution(app, ctx.require("checkpoints")))
    ctx.provide("turns", Turns(app, ctx.require("checkpoints")))


def summaries(ctx, config):
    stores, model, requests = (
        ctx.require("stores"),
        ctx.require("model"),
        ctx.require("requests"),
    )

    def published(value):
        compress(stores, model, value["session_id"], requests.validate_ref)

    ctx.on("response/published", published)


def telemetry(ctx, config):
    logger = logging.getLogger("enterprise_agent.plugins")
    for event in config.events:
        ctx.on(
            event,
            lambda value, name=event: logger.info(
                "%s %s",
                name,
                {
                    key: value[key]
                    for key in ("id", "version", "role", "name", "error_type")
                    if key in value
                },
            ),
        )


def agent_plugin(plugin_id, role, implementation, requires):
    def apply(ctx, config):
        if role == "SemanticAgent":
            agent = implementation(
                ctx.require("model"),
                ctx.require("registry"),
                tool_registry=ctx.require("enterprise_tools"),
            )
        else:
            agent = implementation(
                ctx.require("model"),
                ctx.require("registry"),
                ctx.require("artifacts"),
                ctx.require("query_gateway"),
                tool_registry=ctx.require("enterprise_tools"),
            )
        ctx.agent(role, agent, CAPABILITIES[role])

    return Plugin(plugin_id, apply, requires=requires)


BUILTINS = (
    Plugin(
        "ledger.postgres",
        ledger,
        provides=("stores", "artifacts", "requests"),
        config_schema=LedgerConfig,
    ),
    Plugin(
        "database.readonly",
        database,
        provides=("database",),
        config_schema=DatabaseConfig,
    ),
    Plugin(
        "semantic.yaml",
        semantic_registry,
        requires=("database", "stores"),
        provides=("registry",),
    ),
    Plugin(
        "llm.openai",
        transport,
        provides=("transport",),
    ),
    Plugin("agent.loop", model_loop, requires=("transport",), provides=("model",)),
    Plugin("agents", roles, provides=("specialists",)),
    Plugin(
        "tasks",
        tasks,
        requires=("stores", "requests", "specialists"),
        provides=("tasks",),
    ),
    Plugin(
        "context",
        context,
        requires=(
            "stores",
            "artifacts",
            "requests",
            "tasks",
            "registry",
            "specialists",
        ),
        provides=("context",),
    ),
    Plugin(
        "query.gateway",
        query_gateway,
        requires=("registry", "database"),
        provides=("query_gateway",),
    ),
    Plugin(
        "tools.enterprise",
        enterprise_tools,
        requires=("registry", "context", "stores", "artifacts"),
        provides=("enterprise_tools",),
    ),
    Plugin(
        "supervisor",
        supervisor,
        requires=("model", "context", "stores", "artifacts", "enterprise_tools"),
        provides=("supervisor",),
    ),
    Plugin(
        "preparation",
        preparation,
        requires=("stores", "requests", "tasks", "supervisor"),
        provides=("preparation",),
    ),
    Plugin(
        "responses",
        responses,
        requires=("stores", "artifacts", "requests", "tasks"),
        provides=("responses",),
    ),
    agent_plugin(
        "agent.semantic",
        "SemanticAgent",
        SemanticAgent,
        ("model", "registry", "specialists", "enterprise_tools"),
    ),
    agent_plugin(
        "agent.query",
        "QueryAgent",
        QueryAgent,
        (
            "model",
            "registry",
            "artifacts",
            "query_gateway",
            "specialists",
            "enterprise_tools",
        ),
    ),
    agent_plugin(
        "agent.analysis",
        "AnalysisAgent",
        AnalysisAgent,
        (
            "model",
            "registry",
            "artifacts",
            "query_gateway",
            "specialists",
            "enterprise_tools",
        ),
    ),
    agent_plugin(
        "agent.presentation",
        "PresentationAgent",
        PresentationAgent,
        (
            "model",
            "registry",
            "artifacts",
            "query_gateway",
            "specialists",
            "enterprise_tools",
        ),
    ),
    Plugin(
        "workflow.langgraph",
        workflow,
        requires=(
            "application",
            "stores",
            "requests",
            "tasks",
            "context",
            "specialists",
            "supervisor",
            "preparation",
            "responses",
        ),
        provides=("checkpoints", "scheduler", "execution", "turns"),
    ),
    Plugin("memory.summary", summaries, requires=("stores", "model", "requests")),
    Plugin("telemetry.logging", telemetry, config_schema=LoggingConfig),
)


def builtin_plugins(overrides):
    """Constructor-injected objects retain caller ownership across plugin reloads."""
    plugins = list(BUILTINS)
    for index, plugin in enumerate(plugins):
        if (
            plugin.plugin_id == "ledger.postgres"
            and overrides.get("stores") is not None
        ):
            plugins[index] = Plugin(
                plugin.plugin_id,
                lambda ctx, config: ledger(ctx, config, overrides["stores"]),
                provides=plugin.provides,
                config_schema=plugin.config_schema,
            )
        elif plugin.plugin_id in {"agent.loop", "semantic.yaml", "query.gateway"}:
            service = plugin.provides[0]
            if overrides.get(service) is not None:
                value = overrides[service]

                def provide(ctx, config, name=service, instance=value):
                    ctx.provide(name, instance)

                plugins[index] = Plugin(
                    plugin.plugin_id, provide, provides=plugin.provides
                )
    for role, value in (overrides.get("specialist_overrides") or {}).items():
        matches = {
            "SemanticAgent": "agent.semantic",
            "QueryAgent": "agent.query",
            "AnalysisAgent": "agent.analysis",
            "PresentationAgent": "agent.presentation",
        }
        if role not in matches:
            raise ValueError(f"未知专业 Agent：{role}")
        for index, plugin in enumerate(plugins):
            if plugin.plugin_id == matches[role]:

                def register(ctx, config, name=role, instance=value):
                    ctx.agent(name, instance, CAPABILITIES[name])

                plugins[index] = Plugin(
                    plugin.plugin_id, register, requires=("specialists",)
                )
    return plugins
