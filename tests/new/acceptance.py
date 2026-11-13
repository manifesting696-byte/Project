"""Acceptance tests for request identity, revisions, task execution and provenance."""

import json
import tempfile
import unittest
from pathlib import Path

from enterprise_agent.flow.application import Application
from enterprise_agent.settings import ROOT
from tests.postgres_fixture import (
    TestStores as Stores,
)
from tests.postgres_fixture import (
    cleanup,
)


def main():
    import sys

    if "--live-only" not in sys.argv:
        suite = unittest.defaultTestLoader.discover(
            str(ROOT / "tests"), pattern="test_*.py", top_level_dir=str(ROOT)
        )
        outcome = unittest.TextTestRunner(verbosity=2).run(suite)
        if not outcome.wasSuccessful():
            raise SystemExit(1)
    if "--live" in sys.argv or "--live-only" in sys.argv:
        try:
            with (
                tempfile.TemporaryDirectory() as directory,
                Application(Stores(Path(directory))) as engine,
            ):
                result = engine.ask(
                    "live-smoke",
                    "查询 Products 表中的产品总数，只返回实际行数，不使用业务指标。",
                )
                data = [
                    item
                    for item in result["artifacts"]
                    if item["content_type"] == "table"
                ]
                evidence = {
                    "status": result["status"],
                    "requests": len(result["requests"]),
                    "tasks": len(result["tasks"]),
                    "data": [
                        {
                            "artifact_id": item["artifact_id"],
                            "rows": engine.artifacts.content(item["artifact_id"]),
                        }
                        for item in data
                    ],
                    "blocks": result["blocks"],
                }
                events = engine.stores.list(
                    "session_events", "session_id", result["session_id"]
                )
                diagnostics = {
                    "evidence": evidence,
                    "requests": result["requests"],
                    "revisions": result["revisions"],
                    "events": events,
                }
                diagnostic_path = (
                    ROOT
                    / "runtime"
                    / "diagnostics"
                    / "request-model-live-verification.json"
                )
                diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
                diagnostic_path.write_text(
                    json.dumps(diagnostics, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                print(json.dumps(evidence, ensure_ascii=False))
                if result["status"] != "completed" or not data:
                    raise SystemExit("真实模型尚未完成查询")
                expected = next(
                    item["rows"]
                    for name, item in engine.registry.tables.items()
                    if name.lower() == "products"
                )
                if not any(
                    len(item["rows"]) == 1 and expected in item["rows"][0].values()
                    for item in evidence["data"]
                ):
                    raise SystemExit("真实模型返回值与业务快照的产品数不一致")
        finally:
            cleanup()
