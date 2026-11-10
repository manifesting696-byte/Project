"""Turn orchestration over persistent Requests and their execution Task DAGs."""

from contextlib import contextmanager
from pathlib import Path
from threading import Lock

from enterprise_agent.plugins.composition import builtin_plugins
from enterprise_agent.plugins.profiles import environment_patches, load_profile
from enterprise_agent.plugins.runtime import Runtime, Service
from enterprise_agent.settings import ROOT, setting
from enterprise_agent.stages.answer.publication import (
    clarification_block,
    finish_operation,
    respond,
    snapshot,
    turn_status,
)
from enterprise_agent.stages.answer.retry import materialize_answer
from enterprise_agent.stages.execute.operations import _resume, process_operation
from enterprise_agent.stages.prepare.retry import apply_plan


class Application:
    process_operation = process_operation
    _resume = _resume
    apply_plan = apply_plan
    finish_operation = finish_operation
    respond = respond
    snapshot = snapshot
    clarification_block = clarification_block
    turn_status = staticmethod(turn_status)
    materialize_answer = materialize_answer

    stores = Service()
    model = Service()
    registry = Service()
    database = Service()
    artifacts = Service()
    requests = Service()
    tasks = Service()
    context = Service()
    supervisor = Service()
    query_gateway = Service()
    specialists = Service()
    preparation = Service()
    scheduler = Service()
    responses = Service()
    checkpoints = Service()
    execution = Service()
    turns = Service()

    def __init__(
        self,
        stores=None,
        model=None,
        registry=None,
        query_gateway=None,
        specialist_overrides=None,
        profile=None,
        plugin_config=None,
        plugin_patches=None,
        plugins=(),
    ):
        self._locks, self._guard = {}, Lock()
        self._plugin_path = Path(
            plugin_config
            or setting("AGENT_PLUGIN_CONFIG", str(ROOT / "config" / "harness.yaml"))
        )
        self._patches = (
            environment_patches() if plugin_patches is None else tuple(plugin_patches)
        )
        self.profile = profile or setting("AGENT_PROFILE")
        self.runtime = Runtime(
            definitions=builtin_plugins(
                {
                    "stores": stores,
                    "model": model,
                    "registry": registry,
                    "query_gateway": query_gateway,
                    "specialist_overrides": specialist_overrides,
                }
            )
            + list(plugins),
            host={"application": self},
        )
        try:
            self.reload_profile()
            if stores is None:
                self.recover_interrupted()
        except BaseException:
            self.runtime.close()
            raise

    def reload_profile(self, profile=None):
        selected, specs = load_profile(
            self._plugin_path, profile or self.profile, self._patches
        )
        result = self.runtime.configure(specs)
        self.profile = selected
        return {**result, "profile": selected}

    def close(self):
        self.runtime.close()

    def available_tables(self):
        with self.runtime.operation():
            return self.registry.available_tables()

    def change_tables(self, action, **options):
        if action not in ("enroll", "remove", "refresh"):
            raise ValueError("未知目录操作")
        with self.runtime.maintenance():
            self.runtime.events.emit(
                "catalog/before_change",
                {"action": action, "table": options.get("name")},
            )
            result = getattr(self.registry, action)(**options)
            self.runtime.events.notify(
                "catalog/changed",
                {
                    "action": action,
                    "table": options.get("name"),
                    "catalog_version": self.registry.catalog_version,
                },
            )
            return result

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @contextmanager
    def _lock(self, session_id):
        with self._guard:
            local_lock = self._locks.setdefault(session_id, Lock())
        with local_lock, self.stores.session_lock(session_id):
            yield

    def emitter(self, session_id, turn_id, request_id=None):
        def emit(kind, payload):
            record = self.stores.event(
                session_id, turn_id, kind, "Supervisor", payload, request_id=request_id
            )
            self.runtime.events.notify("session/event", record)
            return record

        return emit

    def ask(
        self,
        user_id,
        query,
        session_id=None,
        constraints=None,
        pause=False,
        preview=False,
    ):
        with self.runtime.operation():
            session = self.stores.session(user_id, session_id, query)
            session_id = session["session_id"]
            with self._lock(session_id):
                turn_id = self.stores.new_id("turn")
                self.stores.event(
                    session_id, turn_id, "user_message", "User", {"content": query}
                )
                return self.turns.run(
                    session_id, turn_id, query, constraints or {}, pause, preview
                )

    def recover_interrupted(self):
        with self.stores.db() as db:
            requests = [row[0] for row in db.execute("SELECT body FROM requests")]
        resumable = {
            item["request_id"] for item in requests if self.execution.active(item)
        }
        self.stores.recover_interrupted(resumable)

    def resume_turn(self, user_id, session_id, turn_id):
        with self.runtime.operation():
            self.stores.session(user_id, session_id)
            with self._lock(session_id):
                return self.turns.resume(session_id, turn_id)

    def run(self, request, turn_id, query, pause=False, output_names=None):
        with self.runtime.operation():
            return self.execution.start(request, turn_id, query, pause, output_names)

    def resume(self, user_id, request_id, continue_analysis=True, answer=None):
        with self.runtime.operation():
            return self._resume(user_id, request_id, continue_analysis, answer)
